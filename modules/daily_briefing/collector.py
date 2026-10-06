"""
collector.py — Фоновий збирач сирих даних (Abacus, шар 1).

Відповідальність:
    - Технічний збір даних із Gmail, Calendar, Tasks, Researcher.
    - Нормалізація у стандартні структури.
    - Дедуплікація (через source_id / URL).
    - НЕ інтерпретує зміст.
    - НЕ приймає управлінських рішень.
    - НЕ змінює статуси завдань, календар, реєстр секретаря.

Повертає:
    CollectedData — словник із сирими нормалізованими записами
    кожного джерела та мета-інформацією про перевірку.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))


# ────────────────────────────────────────────────────────────
# Структури даних
# ────────────────────────────────────────────────────────────

@dataclass
class SourceResult:
    """Результат перевірки одного джерела."""
    source: str
    status: str            # success | partial | failed | source_unavailable
    checked_at: str        # ISO UTC
    window_start: str      # ISO UTC — з якого часу перевіряли
    window_end: str        # ISO UTC — до якого часу перевіряли
    records: list[dict]    # нормалізовані записи
    error: str | None = None
    # Технічне підтвердження, що з'єднання з джерелом реально встановлено.
    # Req 1: нуль знайдених записів НЕ є доказом справної роботи — має бути
    # окреме підтвердження підключення (IMAP login / API ping).
    connection_verified: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class CollectedData:
    """Контейнер результатів усіх джерел за один прогін."""
    run_id: str
    run_started_at: str    # ISO UTC
    sources: dict[str, SourceResult] = field(default_factory=dict)

    def overall_status(self) -> str:
        """Загальний статус прогону на основі статусів джерел."""
        statuses = [s.status for s in self.sources.values()]
        if all(s == "success" for s in statuses):
            return "success"
        if all(s in ("failed", "source_unavailable") for s in statuses):
            return "failed"
        return "partial"


# ────────────────────────────────────────────────────────────
# Допоміжні утиліти
# ────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")


# ────────────────────────────────────────────────────────────
# Gmail Collector
# ────────────────────────────────────────────────────────────

def collect_gmail(cfg: dict[str, Any], window_start: str) -> SourceResult:
    """
    Технічний збір листів Gmail без управлінської інтерпретації.

    Збирає лише:
        message_id, thread_id, sender, recipients, subject,
        timestamp, has_attachments, attachment_types,
        snippet (перші 200 символів), labels, category_guess.

    НЕ визначає:
        - чи є лист новим завданням;
        - управлінське значення листа;
        - статус завдань.
    """
    checked_at = _utc_now_iso()
    days = cfg.get("lookback_days", 1)
    limit = cfg.get("max_messages", 20)

    try:
        from integrations.gmail_client import GmailClient
        gmail = GmailClient()
    except Exception as exc:
        return SourceResult(
            source="gmail",
            status="source_unavailable",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error=str(exc),
        )

    # Req 1: ЯВНО підтверджуємо підключення (IMAP login + SELECT INBOX).
    # Без цього «0 листів» може насправді означати збій логіну, а не порожню скриньку.
    try:
        gmail.verify_connection()
        connection_verified = True
    except Exception as exc:
        return SourceResult(
            source="gmail",
            status="failed",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error=f"IMAP-підключення не підтверджено: {exc}",
            connection_verified=False,
        )

    records = []
    seen_ids: set[str] = set()

    try:
        unread = gmail.get_unread_messages(days=days, max_results=limit)
        important = gmail.get_important_messages(days=days, max_results=limit)
    except Exception as exc:
        return SourceResult(
            source="gmail",
            status="failed",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error=str(exc),
            connection_verified=connection_verified,
        )

    for msg in unread + important:
        msg_id = msg.get("id") or msg.get("message_id") or msg.get("subject", "")
        if msg_id in seen_ids:
            continue
        seen_ids.add(msg_id)

        # Визначення категорії — лише технічна класифікація за ключовими словами
        subject_lower = (msg.get("subject") or "").lower()
        category_guess = _guess_email_category(subject_lower)

        record = {
            "source": "gmail",
            "source_id": msg_id,
            "message_id": msg.get("id", ""),
            "thread_id": msg.get("threadId", ""),
            "sender": msg.get("from", ""),
            "recipients": msg.get("to", ""),
            "subject": msg.get("subject", ""),
            "timestamp": msg.get("date", ""),
            "detected_at": checked_at,
            "labels": msg.get("labels", []),
            "has_attachments": bool(msg.get("attachments")),
            "attachment_types": [a.get("type", "") for a in msg.get("attachments", [])],
            "snippet": (msg.get("snippet") or "")[:200],
            # Req 2: factual_summary не має бути порожнім. Для листа —
            # це тема + перший фрагмент тексту (фактичний зміст, без інтерпретації).
            "factual_summary": (
                f"{msg.get('subject', '').strip()} — {(msg.get('snippet') or '').strip()}"
            ).strip(" —") or (msg.get("subject", "").strip()),
            "category_guess": category_guess,
            "is_unread": msg in unread,
            "is_important": msg in important,
            # Поля для Secretary
            "people_mentioned": [],   # Secretary заповнить
            "explicit_deadline": None,  # Secretary визначить
            "related_task_candidate": None,  # Secretary зіставить
        }
        records.append(record)

    return SourceResult(
        source="gmail",
        status="success",
        checked_at=checked_at,
        window_start=window_start,
        window_end=checked_at,
        records=records,
        connection_verified=connection_verified,
    )


def _guess_email_category(subject_lower: str) -> str:
    """
    Технічна класифікація листа за темою (без управлінської інтерпретації).
    Повертає одну з категорій для початкової маршрутизації.
    """
    priority_keywords = {
        "наказ": "наказ_МОЗ_або_керівництва",
        "розпорядження": "розпорядження",
        "протокол": "протокол",
        "нсзу": "нсзу",
        "мoz": "моз",
        "моз": "моз",
        "доручення": "доручення_керівництва",
        "термін": "дедлайн",
        "дедлайн": "дедлайн",
        "закупівл": "закупівлі",
        "тендер": "закупівлі",
        "прозорро": "закупівлі",
        "обладнан": "медичне_обладнання",
        "несправн": "несправне_обладнання",
        "ваіт": "ваіт",
        "реанімац": "реанімація",
        "анестезіолог": "анестезіологія",
        "клінічний протокол": "клінічний_протокол",
    }
    for kw, category in priority_keywords.items():
        if kw in subject_lower:
            return category
    return "загальне"


# ────────────────────────────────────────────────────────────
# Calendar Collector
# ────────────────────────────────────────────────────────────

# Ключові слова особистих подій (Req 4): такі події НЕ передаються в реєстр
# завдань, а лише фіксуються як info_only.
_PERSONAL_EVENT_KEYWORDS = (
    "день народження", "birthday", "др ", "лікар", "стоматолог", "медогляд",
    "відпустка", "vacation", "сімей", "родин", "особист", "вечеря", "dinner",
    "обід", "lunch", "кава", "coffee", "зустріч з друз", "party", "вечірка",
    "спортзал", "басейн", "тренуванн", "фітнес", "перукар", "manicure",
    "манікюр", "кіно", "театр", "concert", "концерт", "весілля", "wedding",
    "хрестини", "ювілей", "свято", "holiday", "appointment",
)

# Слова-маркери конкретної робочої дії/нормативного документа в події.
_ACTION_EVENT_KEYWORDS = (
    "наказ", "розпорядження", "постанов", "протокол", "дедлайн", "термін",
    "здати", "подати", "звіт", "нарада", "засідання", "комісі", "тендер",
    "закупівл", "доручення", "№",
)


def _classify_calendar_event(title: str, description: str) -> tuple[bool, bool]:
    """Визначити характер події календаря (Req 4).

    Повертає (is_personal, has_action):
        is_personal — подія особистого характеру (не для реєстру завдань);
        has_action  — у події є ознака конкретної робочої дії / документа.
    """
    text = f"{title} {description}".lower()
    is_personal = any(kw in text for kw in _PERSONAL_EVENT_KEYWORDS)
    has_action = any(kw in text for kw in _ACTION_EVENT_KEYWORDS)
    return is_personal, has_action


def collect_calendar(cfg: dict[str, Any], window_start: str) -> SourceResult:
    """
    Технічний збір подій Google Calendar.

    Abacus лише читає — НЕ створює, НЕ переносить, НЕ видаляє події.
    """
    checked_at = _utc_now_iso()
    upcoming_days = cfg.get("upcoming_days", 2)

    try:
        from integrations.calendar_client import CalendarClient
        calendar = CalendarClient(calendar_id=cfg.get("calendar_id", "primary"))
    except Exception as exc:
        return SourceResult(
            source="calendar",
            status="source_unavailable",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error=str(exc),
        )

    records = []
    seen_ids: set[str] = set()

    try:
        today = calendar.get_today_events()
        upcoming = calendar.get_upcoming_events(days=upcoming_days)
    except Exception as exc:
        return SourceResult(
            source="calendar",
            status="failed",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error=str(exc),
        )

    for event in today + upcoming:
        event_id = event.get("id", "")
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        title = event.get("summary", "").strip()
        start = event.get("start", "")
        location = event.get("location", "").strip()
        description = (event.get("description") or "")[:300]
        # Req 4: характер події (особиста / з робочою дією).
        is_personal, has_action = _classify_calendar_event(title, description)
        # Req 2: непорожній factual_summary для кожної події.
        fs_parts = [p for p in [title, f"початок: {start}" if start else "", location] if p]
        record = {
            "source": "calendar",
            "source_id": event_id,
            "detected_at": checked_at,
            "title": title,
            "factual_summary": " | ".join(fs_parts) if fs_parts else title,
            "start": start,
            "end": event.get("end", ""),
            "all_day": event.get("all_day", False),
            "location": location,
            "description": description,
            "is_today": event in today,
            "is_personal": is_personal,
            "has_action": has_action,
        }
        records.append(record)

    return SourceResult(
        source="calendar",
        status="success",
        checked_at=checked_at,
        window_start=window_start,
        window_end=checked_at,
        records=records,
        connection_verified=True,
    )


# ────────────────────────────────────────────────────────────
# Tasks Collector (read-only)
# ────────────────────────────────────────────────────────────

def collect_tasks(cfg: dict[str, Any], window_start: str) -> SourceResult:
    """
    Читання реєстру завдань Secretary (Google Sheet) — ТІЛЬКИ ДЛЯ ЧИТАННЯ.

    Abacus:
        - читає для технічної перевірки та зв'язування source_id;
        - НЕ змінює статус, дедлайн, відповідального, пріоритет.
    """
    checked_at = _utc_now_iso()
    spreadsheet_id = cfg.get("spreadsheet_id", "")
    sheet_name = cfg.get("sheet_name", "Реєстр")

    if not spreadsheet_id or spreadsheet_id == "ВАШ_SPREADSHEET_ID":
        return SourceResult(
            source="tasks",
            status="source_unavailable",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error="spreadsheet_id не налаштовано",
        )

    try:
        from modules.task_manager.tasks import TaskManager
        tm = TaskManager(spreadsheet_id, sheet_name=sheet_name)
        open_tasks = tm.get_open_tasks()
        overdue = tm.get_overdue_tasks()
    except Exception as exc:
        return SourceResult(
            source="tasks",
            status="failed",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error=str(exc),
        )

    overdue_rows = {t.get("_row") for t in overdue}
    records = []

    for task in open_tasks:
        record = {
            "source": "tasks_registry",
            "source_id": f"task_row_{task.get('_row', 'unknown')}",
            "detected_at": checked_at,
            "task_name": task.get("Завдання", ""),
            "status": task.get("Статус", ""),
            "deadline": task.get("Термін", ""),
            "responsible": task.get("Відповідальний", ""),
            "priority": task.get("Пріоритет", ""),
            "is_overdue": task.get("_row") in overdue_rows,
            "row_index": task.get("_row"),
            # Примітка: Secretary є єдиним, хто змінює ці поля
            "_readonly": True,
        }
        records.append(record)

    return SourceResult(
        source="tasks",
        status="success",
        checked_at=checked_at,
        window_start=window_start,
        window_end=checked_at,
        records=records,
        connection_verified=True,
    )


# ────────────────────────────────────────────────────────────
# Researcher Collector
# ────────────────────────────────────────────────────────────

def collect_researcher(cfg: dict[str, Any], window_start: str) -> SourceResult:
    """
    Фоновий технічний збір нових матеріалів з МОЗ / НСЗУ та інших джерел.

    Abacus знаходить і структурує → ChatGPT аналізує та робить висновки.
    НЕ генерує управлінських або клінічних висновків.
    """
    checked_at = _utc_now_iso()

    if not cfg.get("enabled", False):
        return SourceResult(
            source="researcher",
            status="success",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
        )

    try:
        from modules.researcher.researcher import Researcher
        researcher = Researcher(keywords=cfg.get("keywords", []))
        limit = cfg.get("limit_per_source", 5)
        # Новий інтерфейс: collect() повертає записи + статуси джерел.
        collected = researcher.collect(limit_per_source=limit)
        source_status = collected["source_status"]
        blocked_sources = collected["blocked_sources"]
        errored_sources = collected["errored_sources"]
        all_items = collected["records"]
    except Exception as exc:
        return SourceResult(
            source="researcher",
            status="failed",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            records=[],
            error=str(exc),
        )

    # Зіставлення людської назви джерела → канонічний код для Secretary.
    def _primary_source(human_name: str) -> str:
        low = human_name.lower()
        if "кму" in low:
            return "kmu_cabinet"
        if "нсзу" in low:
            return "nszu"
        return "moz_ukraine"

    records = []
    for item in all_items:
        human_src = item.get("source", "")
        title = item.get("title", "")
        order_no = item.get("order_no", "")
        doc_date = item.get("date", "") or item.get("published", "")
        # Номер+дата наказу (з aaukr) додаємо у заголовок для наочності.
        display_title = title
        if order_no:
            display_title = f"№{order_no} від {doc_date} — {title}"
        # Req 2: source_verified=true дозволено ЛИШЕ після перевірки офіційного
        # документа/сторінки відповідного органу. google_news та пошукові
        # сторінки — НЕ першоджерела. Дзеркало aaukr — теж не офіційне джерело.
        # Тому на етапі фонового збору ЗАВЖДИ false; підтвердження робить
        # окремий крок обробки нормативних документів (handoff → radar).
        records.append({
            "source": _primary_source(human_src),
            "source_id": item.get("url", "") or (f"norm:{order_no}|{doc_date}" if order_no else title),
            "detected_at": checked_at,
            "title": display_title,
            # Req 2: непорожній factual_summary (фактичний заголовок документа).
            "factual_summary": display_title,
            "url": item.get("url", ""),
            "published_at": doc_date,
            "order_no": order_no,
            "doc_date": doc_date,
            "document_type": "наказ" if order_no else "новина",
            "change_status": "NEW",  # буде уточнено дедуплікатором
            "primary_source": _primary_source(human_src),
            "source_channel": item.get("via", ""),  # google_news / aaukr / rss
            "source_label": human_src,
            "source_verified": False,  # Req 2: ніколи true без перевірки офіц. джерела
            "potential_impact_areas": _guess_impact_areas(title),
        })

    # Чесний статус: якщо частина джерел заблокована/впала, це partial,
    # навіть коли резервні канали дали записи. Так Secretary бачить діру.
    if blocked_sources or errored_sources:
        status = "partial"
    else:
        status = "success"

    error_note = None
    if blocked_sources:
        error_note = "Заблоковано (Cloudflare): " + ", ".join(blocked_sources)

    # connection_verified: хоча б одне джерело реально відповіло (ok/empty).
    any_reachable = any(st in ("ok", "empty") for st in source_status.values())

    result = SourceResult(
        source="researcher",
        status=status,
        checked_at=checked_at,
        window_start=window_start,
        window_end=checked_at,
        records=records,
        error=error_note,
        connection_verified=any_reachable,
        meta={"source_status": source_status},
    )
    # Додаємо статуси джерел як метадані (для логів/дайджесту Secretary).
    result.source_status = source_status  # type: ignore[attr-defined]
    return result


def _guess_impact_areas(title: str) -> list[str]:
    """Технічне визначення сфер впливу документа за ключовими словами."""
    title_lower = title.lower()
    areas = []
    mapping = {
        "анестезіол": "анестезіологія",
        "інтенсивна терапія": "інтенсивна_терапія",
        "реанімац": "реанімація",
        "ваіт": "ваіт",
        "діаліз": "центр_діалізу",
        "закупівл": "закупівлі",
        "обладнан": "медичне_обладнання",
        "протокол": "клінічні_протоколи",
        "стандарт": "нормативні_вимоги",
        "фінансуван": "фінансування",
        "тариф": "тарифи_нсзу",
        "трансплантац": "трансплантація",
    }
    for kw, area in mapping.items():
        if kw in title_lower:
            areas.append(area)
    return areas or ["загальне"]


# ────────────────────────────────────────────────────────────
# Головна точка входу
# ────────────────────────────────────────────────────────────

def collect_all(config: dict[str, Any], window_start: str | None = None) -> CollectedData:
    """
    Запустити збір усіх джерел. Повертає CollectedData.

    Args:
        config: повний config.yaml
        window_start: ISO UTC — з якого часу збирати (за замовчуванням — 24 год тому)
    """
    if window_start is None:
        from datetime import timedelta
        window_start = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat().replace("+00:00", "Z")

    run = CollectedData(
        run_id=_run_id(),
        run_started_at=_utc_now_iso(),
    )

    print(f"[→] Collector: запуск {run.run_id}, вікно з {window_start}")

    # Gmail
    print("[→] Збір Gmail...")
    run.sources["gmail"] = collect_gmail(config.get("gmail", {}), window_start)
    _log_source(run.sources["gmail"])

    # Calendar
    print("[→] Збір Calendar...")
    run.sources["calendar"] = collect_calendar(config.get("calendar", {}), window_start)
    _log_source(run.sources["calendar"])

    # Tasks (read-only)
    print("[→] Читання реєстру завдань...")
    run.sources["tasks"] = collect_tasks(config.get("tasks", {}), window_start)
    _log_source(run.sources["tasks"])

    # Researcher
    print("[→] Фоновий моніторинг джерел...")
    run.sources["researcher"] = collect_researcher(config.get("researcher", {}), window_start)
    _log_source(run.sources["researcher"])

    print(f"[✓] Collector завершено. Загальний статус: {run.overall_status()}")
    return run


def _log_source(result: SourceResult) -> None:
    """Вивести стислий лог результату джерела."""
    icon = {"success": "✓", "partial": "⚠", "failed": "✗", "source_unavailable": "—"}.get(
        result.status, "?"
    )
    count = len(result.records)
    err = f" | ПОМИЛКА: {result.error}" if result.error else ""
    print(f"  [{icon}] {result.source}: {result.status}, записів: {count}{err}")
