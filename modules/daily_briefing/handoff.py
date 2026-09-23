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
COMMAND_CENTER_FOLDER_ID = "1eh47d2AtLZwfuYdthzVfJ-5pz_x2Qgf5"

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

class Deduplicator:
    """
    Перевірка дублів перед записом SECRETARY_HANDOFF.

    Перевіряє:
        1. source_id;
        2. message_id / thread_id (для Gmail);
        3. URL (для Researcher);
        4. Реєстр завдань Secretary (за ключовими словами).
    """

    def __init__(self, existing_handoff_ids: set[str], registry_tasks: list[dict]):
        self.existing_handoff_ids = existing_handoff_ids
        self.registry_tasks = registry_tasks
        # Індекс реєстру за ключовими словами назви (перші 40 символів)
        self._registry_index = {
            t.get("task_name", "")[:40].lower()
            for t in registry_tasks
            if t.get("task_name")
        }

    def is_duplicate(self, record: dict) -> bool:
        """Чи є цей запис дублем вже переданого handoff?"""
        # За source_id
        if record.get("source_id") in self.existing_handoff_ids:
            return True
        # За message_id
        if record.get("message_id") and record["message_id"] in self.existing_handoff_ids:
            return True
        # За thread_id (якщо вже передавали будь-що з цього треду)
        if record.get("thread_id") and record["thread_id"] in self.existing_handoff_ids:
            return True
        # За URL (для researcher)
        if record.get("url") and record["url"] in self.existing_handoff_ids:
            return True
        return False

    def find_registry_match(self, record: dict) -> dict | None:
        """
        Спробувати знайти потенційно пов'язану задачу в реєстрі Secretary.
        Повертає першу задачу-кандидат або None.
        """
        subject = (
            record.get("subject") or record.get("title") or ""
        ).lower()

        # Прямий пошук ключових слів теми в назвах задач реєстру
        for task in self.registry_tasks:
            task_name = (task.get("task_name") or "").lower()
            if not task_name:
                continue
            # Якщо хоча б 3+ символьне слово з теми є в назві задачі
            words = [w for w in subject.split() if len(w) >= 3]
            if any(w in task_name for w in words):
                return task
        return None


# ────────────────────────────────────────────────────────────
# Класифікатор handoff
# ────────────────────────────────────────────────────────────

def classify_handoff(record: dict, registry_match: dict | None) -> str:
    """
    Класифікувати запис для Secretary.

    Повертає:
        "new_task"        — нова потенційна задача (немає в реєстрі, потребує дії)
        "update_existing" — можливе оновлення існуючої задачі
        "info_only"       — інформація (не потребує дії)
        "needs_review"    — неоднозначний випадок, потребує рішення Secretary
    """
    category = record.get("category_guess", "")
    source = record.get("source", "")

    # Якщо є відповідність у реєстрі — кандидат на оновлення
    if registry_match:
        return "update_existing"

    # Пріоритетна категорія + Gmail → швидше за все нова задача
    if source == "gmail" and category in PRIORITY_CATEGORIES:
        return "new_task"

    # Наказ / розпорядження з МОЗ/НСЗУ → нова задача
    if source in ("moz_ukraine", "nszu"):
        if any(kw in (record.get("title") or "").lower()
               for kw in ["наказ", "розпорядження", "затверджено", "зміни"]):
            return "new_task"
        return "info_only"

    # Загальні листи без ознак пріоритету → перегляд
    if source == "gmail":
        return "needs_review"

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
) -> dict:
    """Сформувати структуру SECRETARY_HANDOFF для одного запису."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source = record.get("source", "unknown")

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
            "task_name": registry_match.get("task_name") if registry_match else None,
            "task_id": registry_match.get("source_id") if registry_match else None,
            "status": registry_match.get("status") if registry_match else None,
        } if registry_match else None,
        "confidence": confidence,
        "reason_for_handoff": _reason(record, classification),
        "suggested_check": _suggested_check(classification, registry_match),
        "source_verified": record.get("source_verified", False),
        "processing_status": "pending_secretary",

        # Класифікація (Abacus)
        "classification": classification,  # new_task | update_existing | info_only | needs_review
        "is_priority": record.get("category_guess", "") in PRIORITY_CATEGORIES,

        # Технічні поля для дедуплікації
        "_dedup_keys": {
            "source_id": record.get("source_id", ""),
            "message_id": record.get("message_id", ""),
            "thread_id": record.get("thread_id", ""),
            "url": record.get("url", ""),
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
    """Отримати або створити папку handoffs/secretary/ у Command Center."""
    # Шукаємо папку handoffs
    handoffs_folder = drive.find_file(
        "handoffs",
        COMMAND_CENTER_FOLDER_ID,
        mime_type="application/vnd.google-apps.folder",
    )
    if not handoffs_folder:
        handoffs_folder = drive.service.files().create(body={
            "name": "handoffs",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [COMMAND_CENTER_FOLDER_ID],
        }, fields="id").execute()

    # Шукаємо підпапку secretary
    secretary_folder = drive.find_file(
        "secretary",
        handoffs_folder["id"],
        mime_type="application/vnd.google-apps.folder",
    )
    if not secretary_folder:
        secretary_folder = drive.service.files().create(body={
            "name": "secretary",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [handoffs_folder["id"]],
        }, fields="id").execute()

    return secretary_folder["id"]


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
        "storage_status": "unknown",
        "handoff_file": today_filename,
        "handoff_folder_id": None,
        "priority_handoffs": 0,
        "error": None,
    }

    # Ініціалізація Drive
    try:
        from integrations.drive_client import DriveClient
        drive = DriveClient()
    except Exception as exc:
        result["storage_status"] = "failed"
        result["error"] = str(exc)
        return result

    # Отримати/створити папку handoffs/secretary/
    try:
        folder_id = _get_or_create_handoffs_folder(drive)
        result["handoff_folder_id"] = folder_id
    except Exception as exc:
        result["storage_status"] = "failed"
        result["error"] = f"Не вдалося отримати папку handoffs: {exc}"
        return result

    # Завантажити вже збережені source_id (дедуплікація)
    existing_ids = _load_existing_handoff_ids(drive, folder_id, today_filename)
    print(f"  [i] Вже передано сьогодні: {len(existing_ids)} записів (дедуплікація)")

    # Прочитати реєстр завдань Secretary (read-only)
    print("  [→] Читання реєстру завдань для зв'язування...")
    registry_tasks = _load_registry_tasks()
    print(f"  [i] Завдань у реєстрі: {len(registry_tasks)}")

    # Ініціалізація дедуплікатора
    dedup = Deduplicator(existing_ids, registry_tasks)

    # Збір усіх записів з усіх джерел
    all_records: list[dict] = []
    for source_result in collected.sources.values():
        all_records.extend(source_result.records)

    print(f"  [i] Всього записів до обробки: {len(all_records)}")

    # Формування handoffs
    new_handoffs: list[dict] = []
    for record in all_records:
        # Дедуплікація
        if dedup.is_duplicate(record):
            result["handoffs_skipped_duplicate"] += 1
            continue

        # Зв'язування з реєстром
        registry_match = dedup.find_registry_match(record)

        # Класифікація
        classification = classify_handoff(record, registry_match)
        confidence = confidence_level(record, False)

        # Формуємо handoff
        handoff = build_handoff(
            record=record,
            classification=classification,
            confidence=confidence,
            registry_match=registry_match,
            run_id=collected.run_id,
        )

        new_handoffs.append(handoff)

        # Додаємо ключі до множини (щоб не дублювати в межах цього прогону)
        for v in handoff["_dedup_keys"].values():
            if v:
                existing_ids.add(v)

        if handoff["is_priority"]:
            result["priority_handoffs"] += 1

    print(f"  [i] Нових handoffs: {len(new_handoffs)} | Дублів пропущено: {result['handoffs_skipped_duplicate']}")

    if not new_handoffs:
        result["storage_status"] = "success"
        result["handoffs_written"] = 0
        return result

    # Сортування: спочатку пріоритетні
    new_handoffs.sort(key=lambda h: (0 if h["is_priority"] else 1, h["produced_at"]))

    # Запис на Drive (JSONL — дозапис до існуючого файлу)
    try:
        existing_file = drive.find_file(today_filename, folder_id)

        if existing_file:
            existing_content = drive.read_file(existing_file["id"]) or ""
        else:
            existing_content = ""

        new_lines = "\n".join(json.dumps(h, ensure_ascii=False) for h in new_handoffs) + "\n"
        full_content = existing_content + new_lines

        file_id = drive.write_file(
            content=full_content,
            filename=today_filename,
            parent_id=folder_id,
            mime_type="text/plain",
        )

        if file_id:
            result["handoffs_written"] = len(new_handoffs)
            result["storage_status"] = "success"
            print(f"  [✓] Записано {len(new_handoffs)} handoffs → Drive: handoffs/secretary/{today_filename}")
        else:
            result["storage_status"] = "failed"
            result["error"] = "drive.write_file повернув None"

    except Exception as exc:
        result["storage_status"] = "failed"
        result["error"] = str(exc)

    return result
