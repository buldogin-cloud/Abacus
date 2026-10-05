"""
handoff.py — Формування та збереження SECRETARY_HANDOFF (Abacus, шар 2).

Відповідальність:
    - Дедуплікація записів (source_id / message_id / thread_id).
    - Зв'язування з реєстром завдань Secretary (read-only).
    - Класифікація кандидата: new_task / update_existing / info_only / needs_review.
    - Запис SECRETARY_HANDOFF у Google Drive → handoffs/secretary/<date>.jsonl
    - НЕ змінює реєстр завдань.
    - НЕ надсилає handoff у Telegram (Telegram — лише для користувача).
    - НЕ приймає управлінських рішень.

Формат файлу на Drive:
    Command Center / handoffs / secretary / handoff_YYYY-MM-DD.jsonl
    Кожен рядок — один JSON-об'єкт (SECRETARY_HANDOFF).

ChatGPT Secretary читає ці файли та:
    - вирішує, чи це нова задача;
    - оновлює реєстр завдань;
    - формує управлінський брифінг.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from modules.daily_briefing.collector import CollectedData, SourceResult


# ────────────────────────────────────────────────────────────
# Константи
# ────────────────────────────────────────────────────────────

# ID папки Command Center у Drive
COMMAND_CENTER_FOLDER_ID = "1eh47d2AtLZwfuYdthzVfJ-5pz_x2Qgf5"  # оригінальна тека (недоступна через drive.file)
HANDOFFS_FOLDER_ID = "1tmeJKjy_P-T38AKXHv9LtdjjqOFf4lgl"   # пряме посилання на handoffs
SECRETARY_FOLDER_ID = "1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj"   # пряме посилання на secretary

# ID реєстру завдань Secretary (ТІЛЬКИ ЧИТАННЯ для Abacus)
TASKS_REGISTRY_SHEET_ID = "11oqxqRm7cAH6jpKf9T7CYtIY0j01LAV259F7NGkd5so"

# Мінімальний рівень впевненості для передачі Secretary
MIN_CONFIDENCE_FOR_HANDOFF = "low"

# Пріоритетні категорії (передаються раніше)
PRIORITY_CATEGORIES = {
    "наказ_МОЗ_або_керівництва",
    "розпорядження",
    "доручення_керівництва",
    "дедлайн",
    "моз",
    "нсзу",
    "анестезіологія",
    "ваіт",
    "медичне_обладнання",
    "несправне_обладнання",
    "закупівлі",
    "клінічний_протокол",
    "нормативні_вимоги",
}


# ────────────────────────────────────────────────────────────
# Дедуплікатор
# ────────────────────────────────────────────────────────────

def _norm_doc_key(record: dict) -> str | None:
    """Канонічний ключ нормативного документа: norm:№<номер>|<дата> (Req 4).

    Дозволяє відсікати повтори того самого наказу/постанови незалежно від URL
    чи назви файлу.
    """
    order_no = (record.get("order_no") or "").strip()
    doc_date = (record.get("doc_date") or record.get("published_at") or "").strip()
    if order_no:
        return f"norm:{order_no}|{doc_date}"
    return None


# Українські стоп-слова, що НЕ є значущими для зіставлення (Req 3).
_STOPWORDS = {
    "про", "щодо", "для", "від", "при", "над", "під", "або", "та", "і", "й",
    "the", "and", "for", "наказ", "лист", "питання", "зміни", "деяких",
    "затвердження", "внесення", "україни", "моз", "наказу", "справа", "re", "fwd",
}


def _significant_tokens(text: str) -> set[str]:
    """Значущі слова (≥4 символів, без стоп-слів) для зіставлення."""
    import re
    words = re.findall(r"\w+", (text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


class Deduplicator:
    """
    Перевірка дублів перед записом SECRETARY_HANDOFF.

    Перевіряє (Req 4):
        1. source_id;
        2. message_id / thread_id (для Gmail);
        3. канонічний URL (для Researcher);
        4. номер+дата нормативного документа (norm:№|дата).

    Зіставлення з реєстром завдань (Req 3) — СТРОГЕ: лише за достатнім збігом
    значущих слів. Випадкові збіги за одним словом заборонені.
    """

    # Мінімальна кількість спільних значущих слів для надійного зв'язку з задачею.
    MIN_SHARED_TOKENS = 2

    def __init__(self, existing_handoff_ids: set[str], registry_tasks: list[dict]):
        self.existing_handoff_ids = existing_handoff_ids
        self.registry_tasks = registry_tasks

    def is_duplicate(self, record: dict) -> bool:
        """Чи є цей запис дублем вже переданого handoff?"""
        candidates = [
            record.get("source_id"),
            record.get("message_id"),
            record.get("thread_id"),
            record.get("url"),
            _norm_doc_key(record),
        ]
        return any(c and c in self.existing_handoff_ids for c in candidates)

    def find_registry_match(self, record: dict) -> tuple[dict | None, bool]:
        """
        Спробувати знайти пов'язану задачу в реєстрі Secretary (СТРОГО).

        Повертає (task, confident):
            task      — кандидат або None;
            confident — True лише якщо зв'язок надійний (≥ MIN_SHARED_TOKENS
                        спільних значущих слів). Якщо False — Secretary має
                        вирішувати сам (needs_review), related_task_candidate
                        НЕ заповнюється.
        """
        text = record.get("subject") or record.get("title") or ""
        rec_tokens = _significant_tokens(text)
        if not rec_tokens:
            return None, False

        best_task = None
        best_overlap = 0
        for task in self.registry_tasks:
            task_name = task.get("task_name") or ""
            task_tokens = _significant_tokens(task_name)
            if not task_tokens:
                continue
            overlap = len(rec_tokens & task_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_task = task

        if best_task and best_overlap >= self.MIN_SHARED_TOKENS:
            return best_task, True
        # Є слабкий збіг (1 слово) — повертаємо задачу-підказку, але confident=False.
        if best_task and best_overlap >= 1:
            return best_task, False
        return None, False


# ────────────────────────────────────────────────────────────
# Класифікатор handoff
# ────────────────────────────────────────────────────────────

def classify_handoff(
    record: dict,
    registry_match: dict | None,
    confident: bool = False,
) -> str:
    """
    Класифікувати запис для Secretary (Req 3).

    Дозволені значення (і лише вони):
        "new_task"        — нова потенційна задача (пріоритетний лист Gmail)
        "update_existing" — можливе оновлення існуючої задачі (НАДІЙНИЙ збіг)
        "info_only"       — інформація (не потребує дії)
        "needs_review"    — неоднозначний випадок, рішення за Secretary

    Жорсткі правила з інструкції:
        - Загальні новини органів влади (researcher: moz/nszu/kmu) НІКОЛИ
          не перетворюються автоматично на new_task. Максимум — needs_review
          (для нормативних документів з номером) або info_only (загальна новина).
          Рішення «це задача» приймає лише Secretary.
        - update_existing — лише коли зв'язок із задачею НАДІЙНИЙ (confident=True).
          Слабкий збіг за одним словом → needs_review, без прив'язки до задачі.
    """
    category = record.get("category_guess", "")
    source = record.get("source", "")

    # Надійний збіг із реєстром → кандидат на оновлення.
    if registry_match and confident:
        return "update_existing"

    # Слабкий (ненадійний) збіг → хай вирішує Secretary, не нав'язуємо задачу.
    if registry_match and not confident:
        return "needs_review"

    # Researcher / органи влади: НЕ автоконвертуємо новини у задачі.
    if source in ("moz_ukraine", "nszu", "kmu_cabinet"):
        # Нормативний документ з номером — потребує перегляду (радар + рішення
        # Secretary), але це не автоматична нова задача.
        if record.get("order_no") or record.get("document_type") == "наказ":
            return "needs_review"
        return "info_only"

    # Gmail: пріоритетна категорія → потенційна нова задача (лист адресований
    # Андрію напряму, на відміну від загальних новин).
    if source == "gmail":
        if category in PRIORITY_CATEGORIES:
            return "new_task"
        return "needs_review"

    # Все інше (calendar тощо) — інформація.
    return "info_only"


def confidence_level(record: dict, is_duplicate: bool) -> str:
    """Рівень впевненості в релевантності."""
    if is_duplicate:
        return "duplicate"
    category = record.get("category_guess", "")
    if category in PRIORITY_CATEGORIES:
        return "high"
    if category != "загальне":
        return "medium"
    return "low"


# ────────────────────────────────────────────────────────────
# Формувач SECRETARY_HANDOFF
# ────────────────────────────────────────────────────────────

def build_handoff(
    record: dict,
    classification: str,
    confidence: str,
    registry_match: dict | None,
    run_id: str,
    confident: bool = False,
) -> dict:
    """Сформувати структуру SECRETARY_HANDOFF для одного запису.

    Req 3: related_task_candidate заповнюється ЛИШЕ коли зв'язок надійний
    (confident=True). За слабкого збігу поле лишається None — Secretary
    зіставляє сам.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source = record.get("source", "unknown")
    # Прив'язку до задачі показуємо лише за надійного збігу.
    link_task = registry_match if (registry_match and confident) else None

    return {
        # Мета
        "handoff_id": str(uuid.uuid4()),
        "run_id": run_id,
        "produced_by": "Abacus",
        "produced_at": now,

        # Основні поля (з інструкції)
        "detected_at": record.get("detected_at", now),
        "source": source,
        "source_id": record.get("source_id", ""),
        "source_url": record.get("url") or record.get("source_url", ""),
        "category": record.get("category_guess") or record.get("document_type", ""),
        "title": record.get("subject") or record.get("title", ""),
        "factual_summary": record.get("snippet") or record.get("factual_summary", ""),
        "explicit_deadline": record.get("explicit_deadline"),
        "people_mentioned": record.get("people_mentioned", []),
        "attachments": {
            "has_attachments": record.get("has_attachments", False),
            "types": record.get("attachment_types", []),
        },
        "related_task_candidate": {
            "task_name": link_task.get("task_name"),
            "task_id": link_task.get("source_id"),
            "status": link_task.get("status"),
        } if link_task else None,
        "confidence": confidence,
        "reason_for_handoff": _reason(record, classification),
        "suggested_check": _suggested_check(classification, link_task),
        "source_verified": record.get("source_verified", False),
        "processing_status": "pending_secretary",

        # Класифікація (Abacus)
        "classification": classification,  # new_task | update_existing | info_only | needs_review
        "is_priority": record.get("category_guess", "") in PRIORITY_CATEGORIES,

        # Технічні поля для дедуплікації (Req 4)
        "_dedup_keys": {
            "source_id": record.get("source_id", ""),
            "message_id": record.get("message_id", ""),
            "thread_id": record.get("thread_id", ""),
            "url": record.get("url", ""),
            "norm_doc_key": _norm_doc_key(record) or "",
        },

        # Примітка: Secretary є єдиним агентом, що може змінювати реєстр
        "_readonly_note": "Abacus не змінює реєстр. Лише Secretary може оновити статус/дедлайн/відповідального.",
    }


def _reason(record: dict, classification: str) -> str:
    reasons = {
        "new_task": "потенційна нова задача — потребує управлінського рішення Secretary",
        "update_existing": "можливе оновлення існуючої задачі — потребує зіставлення Secretary",
        "info_only": "інформаційний запис без ознак нової задачі",
        "needs_review": "неоднозначний лист — Secretary має визначити значення",
    }
    return reasons.get(classification, "невідомо")


def _suggested_check(classification: str, registry_match: dict | None) -> str:
    if classification == "update_existing" and registry_match:
        return f"Перевірити задачу '{registry_match.get('task_name', '')}' у реєстрі"
    if classification == "new_task":
        return "Оцінити, чи потрібно додати нову задачу до реєстру"
    if classification == "needs_review":
        return "Прочитати лист повністю та визначити управлінську дію"
    return "—"


# ────────────────────────────────────────────────────────────
# Drive — читання існуючих handoff ID
# ────────────────────────────────────────────────────────────

def _get_or_create_handoffs_folder(drive) -> str:
    """Повернути ID теки handoffs/secretary/ для запису SECRETARY_HANDOFF.

    Використовуємо прямий ID теки secretary (drive.file scope). Раніше код
    шукав підтеки всередині COMMAND_CENTER_FOLDER_ID, яка недоступна через
    drive.file (404) — тому пряме посилання надійніше.
    """
    return SECRETARY_FOLDER_ID


def _load_existing_handoff_ids(drive, folder_id: str, today_filename: str) -> set[str]:
    """
    Завантажити source_id вже збережених handoffs за сьогодні
    (для дедуплікації в межах одного дня).
    """
    existing_ids: set[str] = set()
    existing_file = drive.find_file(today_filename, folder_id)
    if not existing_file:
        return existing_ids
    try:
        content = drive.read_file(existing_file["id"])
        for line in (content or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                keys = obj.get("_dedup_keys", {})
                for v in keys.values():
                    if v:
                        existing_ids.add(v)
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    return existing_ids


def _load_recent_handoff_ids(drive, folder_id: str, days: int = 14) -> set[str]:
    """Зібрати dedup-ключі з handoff-файлів за останні `days` днів (Req 4).

    Повторний запуск у інший день не повинен створювати дублі того самого
    листа/документа. Тому перевіряємо не лише сьогоднішній файл, а вікно
    попередніх днів.
    """
    from datetime import timedelta

    ids: set[str] = set()
    today = datetime.now()
    for d in range(days):
        day = today - timedelta(days=d)
        fname = f"handoff_{day.strftime('%Y-%m-%d')}.jsonl"
        ids |= _load_existing_handoff_ids(drive, folder_id, fname)
    return ids


# ────────────────────────────────────────────────────────────
# Валідація запису перед передачею (Req 2)
# ────────────────────────────────────────────────────────────

# Джерела, які НЕ формують SECRETARY_HANDOFF (Req 6): реєстр завдань Abacus
# читає лише для зв'язування, він не є «новим матеріалом» для Secretary.
_NON_HANDOFF_SOURCES = {"tasks", "tasks_registry"}


def validate_record(record: dict) -> tuple[bool, str]:
    """Перевірити, що запис придатний до передачі Secretary (Req 2).

    Повертає (ok, reason). Запис НЕ передається, якщо порожні обов'язкові поля:
    title, factual_summary, source, source_id. «Нуль валідних полів ⇒ не
    передаємо» — замість того, щоб слати Secretary сміття з порожнім змістом.
    """
    title = (record.get("subject") or record.get("title") or "").strip()
    factual = (record.get("factual_summary") or "").strip()
    source = (record.get("source") or "").strip()
    source_id = (record.get("source_id") or "").strip()

    if not title:
        return False, "порожній title"
    if not factual:
        return False, "порожній factual_summary"
    if not source:
        return False, "порожній source"
    if not source_id:
        return False, "порожній source_id"
    return True, ""


# ────────────────────────────────────────────────────────────
# Registry reader (read-only)
# ────────────────────────────────────────────────────────────

def _load_registry_tasks() -> list[dict]:
    """
    Читання реєстру завдань Secretary (read-only).
    Повертає спрощений список задач для дедуплікації/зв'язування.
    """
    try:
        from modules.task_manager.tasks import TaskManager
        tm = TaskManager(TASKS_REGISTRY_SHEET_ID, sheet_name="Реєстр")
        open_tasks = tm.get_open_tasks()
        # Перетворюємо у стандартний формат
        return [
            {
                "task_name": t.get("Завдання", ""),
                "status": t.get("Статус", ""),
                "deadline": t.get("Термін", ""),
                "source_id": f"task_row_{t.get('_row', 'unknown')}",
            }
            for t in open_tasks
        ]
    except Exception as exc:
        print(f"  [⚠] Не вдалося прочитати реєстр завдань: {exc}")
        return []


# ────────────────────────────────────────────────────────────
# Головна функція
# ────────────────────────────────────────────────────────────

def process_handoffs(collected: CollectedData) -> dict[str, Any]:
    """
    Обробити зібрані дані: дедуплікувати → класифікувати → зберегти на Drive.

    Args:
        collected: результат collector.collect_all()

    Returns:
        Словник зі статистикою:
            {
                "handoffs_written": int,
                "handoffs_skipped_duplicate": int,
                "storage_status": "success" | "partial" | "failed",
                "handoff_file": "handoff_YYYY-MM-DD.jsonl",
                "handoff_folder_id": str,
                "priority_handoffs": int,
                "error": str | None,
            }
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_filename = f"handoff_{today_str}.jsonl"
    result: dict[str, Any] = {
        "handoffs_written": 0,
        "handoffs_skipped_duplicate": 0,
        "invalid_dropped": 0,           # Req 2: відкинуті неповні записи
        "skipped_non_handoff": 0,       # Req 6: реєстр завдань не передається
        "storage_status": "unknown",
        "handoff_file": today_filename,
        "handoff_folder_id": None,
        "priority_handoffs": 0,
        "classification_counts": {      # Req 8
            "new_task": 0,
            "update_existing": 0,
            "info_only": 0,
            "needs_review": 0,
        },
        "readback_confirmed": 0,        # Req 6/8: підтверджено читанням після запису
        "readback_ok": False,
        "errors": [],                   # Req 8: перелік проблем
        "error": None,
    }

    # Ініціалізація Drive
    try:
        from integrations.drive_client import DriveClient
        drive = DriveClient()
    except Exception as exc:
        result["storage_status"] = "failed"
        result["error"] = str(exc)
        result["errors"].append(f"drive_init: {exc}")
        return result

    # Отримати папку handoffs/secretary/
    try:
        folder_id = _get_or_create_handoffs_folder(drive)
        result["handoff_folder_id"] = folder_id
    except Exception as exc:
        result["storage_status"] = "failed"
        result["error"] = f"Не вдалося отримати папку handoffs: {exc}"
        result["errors"].append(f"folder: {exc}")
        return result

    # Req 4: дедуплікація не лише за сьогодні, а за вікно останніх днів,
    # щоб повторний запуск іншого дня не створював дублі.
    existing_ids = _load_recent_handoff_ids(drive, folder_id, days=14)
    print(f"  [i] Відомих dedup-ключів за 14 днів: {len(existing_ids)}")

    # Прочитати реєстр завдань Secretary (read-only)
    print("  [→] Читання реєстру завдань для зв'язування...")
    registry_tasks = _load_registry_tasks()
    print(f"  [i] Завдань у реєстрі: {len(registry_tasks)}")

    # Ініціалізація дедуплікатора
    dedup = Deduplicator(existing_ids, registry_tasks)

    # Збір записів з джерел — КРІМ реєстру завдань (Req 6).
    all_records: list[dict] = []
    for source_result in collected.sources.values():
        if source_result.source in _NON_HANDOFF_SOURCES:
            result["skipped_non_handoff"] += len(source_result.records)
            continue
        all_records.extend(source_result.records)

    print(f"  [i] Записів до обробки (без реєстру): {len(all_records)}")

    # Формування handoffs
    new_handoffs: list[dict] = []
    for record in all_records:
        # Req 2: валідація — не передаємо записи з порожнім title/factual_summary.
        ok, reason = validate_record(record)
        if not ok:
            result["invalid_dropped"] += 1
            result["errors"].append(
                f"invalid[{record.get('source','?')}/{record.get('source_id','?')}]: {reason}"
            )
            continue

        # Дедуплікація
        if dedup.is_duplicate(record):
            result["handoffs_skipped_duplicate"] += 1
            continue

        # Зв'язування з реєстром (строге): (task, confident)
        registry_match, confident = dedup.find_registry_match(record)

        # Класифікація (Req 3)
        classification = classify_handoff(record, registry_match, confident)
        confidence = confidence_level(record, False)

        # Формуємо handoff
        handoff = build_handoff(
            record=record,
            classification=classification,
            confidence=confidence,
            registry_match=registry_match,
            run_id=collected.run_id,
            confident=confident,
        )

        new_handoffs.append(handoff)
        # Лічильник класифікацій (Req 8)
        if classification in result["classification_counts"]:
            result["classification_counts"][classification] += 1

        # Додаємо ключі до множини (щоб не дублювати в межах цього прогону)
        for v in handoff["_dedup_keys"].values():
            if v:
                existing_ids.add(v)

        if handoff["is_priority"]:
            result["priority_handoffs"] += 1

    print(
        f"  [i] Нових handoffs: {len(new_handoffs)} | "
        f"дублів: {result['handoffs_skipped_duplicate']} | "
        f"відкинуто неповних: {result['invalid_dropped']}"
    )

    if not new_handoffs:
        # Немає що писати — але це валідний успішний прогін (нічого нового).
        result["storage_status"] = "success"
        result["handoffs_written"] = 0
        result["readback_ok"] = True
        return result

    # Сортування: спочатку пріоритетні
    new_handoffs.sort(key=lambda h: (0 if h["is_priority"] else 1, h["produced_at"]))

    # Запис на Drive (JSONL — дозапис до існуючого файлу)
    written_ids = {h["handoff_id"] for h in new_handoffs}
    try:
        existing_file = drive.find_file(today_filename, folder_id)
        existing_content = drive.read_file(existing_file["id"]) if existing_file else ""
        existing_content = existing_content or ""

        new_lines = "\n".join(json.dumps(h, ensure_ascii=False) for h in new_handoffs) + "\n"
        full_content = existing_content + new_lines

        file_id = drive.write_file(
            content=full_content,
            filename=today_filename,
            parent_id=folder_id,
            mime_type="text/plain",
        )

        if not file_id:
            result["storage_status"] = "failed"
            result["error"] = "drive.write_file повернув None"
            result["errors"].append("write_file: None")
            return result

        # Req 6: READ-BACK — перечитуємо файл і підтверджуємо, що всі записані
        # handoff_id реально присутні. Лише тоді вважаємо запис доставленим.
        readback_file = drive.find_file(today_filename, folder_id)
        readback_content = drive.read_file(readback_file["id"]) if readback_file else ""
        found_ids: set[str] = set()
        for line in (readback_content or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                hid = obj.get("handoff_id")
                if hid:
                    found_ids.add(hid)
            except json.JSONDecodeError:
                pass

        confirmed = written_ids & found_ids
        result["readback_confirmed"] = len(confirmed)

        if written_ids.issubset(found_ids):
            result["handoffs_written"] = len(new_handoffs)
            result["storage_status"] = "success"
            result["readback_ok"] = True
            print(
                f"  [✓] Записано і підтверджено читанням {len(new_handoffs)} "
                f"handoffs → secretary/{today_filename}"
            )
        else:
            # Запис пройшов, але read-back не підтвердив усі записи — чесно partial.
            missing = written_ids - found_ids
            result["handoffs_written"] = len(confirmed)
            result["storage_status"] = "partial"
            result["readback_ok"] = False
            result["error"] = (
                f"read-back підтвердив {len(confirmed)}/{len(written_ids)}; "
                f"не знайдено {len(missing)}"
            )
            result["errors"].append(result["error"])
            print(f"  [⚠] Read-back неповний: {len(confirmed)}/{len(written_ids)}")

    except Exception as exc:
        result["storage_status"] = "failed"
        result["error"] = str(exc)
        result["errors"].append(f"write/readback: {exc}")

    return result
