"""Модуль щоденного брифінгу (Daily Briefing).

Основний скрипт Command Center: збирає дані з Gmail (непрочитані та важливі
листи за 24 год), Google Calendar (події на сьогодні та завтра), Google
Sheets (поточні та прострочені завдання) і, за потреби, дайджест Дослідника.
Результат — структурований щоденний брифінг у markdown-форматі.

Запуск:
    python modules/daily_briefing/briefing.py [--config config.yaml]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# Дозволяємо запуск як окремого скрипта (додаємо корінь проекту у sys.path).
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from integrations.calendar_client import CalendarClient  # noqa: E402
from integrations.gmail_client import GmailClient  # noqa: E402
from integrations.drive_client import DriveClient  # noqa: E402
from modules.researcher.researcher import Researcher  # noqa: E402
from modules.task_manager.tasks import TaskManager  # noqa: E402
from modules.daily_briefing.checkpoints_util import CheckpointsReader  # noqa: E402


def load_config(path: str | None) -> dict[str, Any]:
    """Завантажує конфігурацію з YAML-файлу.

    Якщо файл не задано або відсутній — використовує config.yaml у корені,
    а за його відсутності — приклад config.example.yaml.
    """
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(_ROOT / "config.yaml")
    candidates.append(_ROOT / "modules" / "daily_briefing" / "config.example.yaml")

    for candidate in candidates:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                print(f"[i] Використано конфігурацію: {candidate}")
                return yaml.safe_load(f) or {}
    return {}


# ---------------------------------------------------------------------- #
# Секції брифінгу
# ---------------------------------------------------------------------- #
def _format_emails_section(gmail: GmailClient, cfg: dict[str, Any]) -> str:
    """Формує секцію листів (непрочитані + важливі)."""
    days = cfg.get("lookback_days", 1)
    limit = cfg.get("max_messages", 15)
    lines = ["## 📧 Пошта (за 24 години)", ""]

    try:
        unread = gmail.get_unread_messages(days=days, max_results=limit)
        important = gmail.get_important_messages(days=days, max_results=limit)
    except Exception as exc:  # noqa: BLE001 — не зриваємо брифінг через одне джерело
        return "## 📧 Пошта (за 24 години)\n\n_Помилка доступу до Gmail: " f"{exc}_\n"

    lines.append(f"**Непрочитані:** {len(unread)} · **Важливі:** {len(important)}")
    lines.append("")

    if unread:
        lines.append("### Непрочитані листи")
        for m in unread:
            lines.append(f"- **{m['subject']}** — _{m['from']}_")
            if m.get("snippet"):
                lines.append(f"  > {m['snippet'][:140]}")
    else:
        lines.append("_Немає непрочитаних листів._")
    lines.append("")

    if important:
        lines.append("### Важливі листи")
        for m in important:
            lines.append(f"- **{m['subject']}** — _{m['from']}_")
    lines.append("")
    return "\n".join(lines)


def _format_calendar_section(calendar: CalendarClient, cfg: dict[str, Any]) -> str:
    """Формує секцію подій календаря (сьогодні + найближчі дні)."""
    upcoming_days = cfg.get("upcoming_days", 2)
    lines = ["## 📅 Календар", ""]

    try:
        today = calendar.get_today_events()
        upcoming = calendar.get_upcoming_events(days=upcoming_days)
    except Exception as exc:  # noqa: BLE001
        return f"## 📅 Календар\n\n_Помилка доступу до Google Calendar: {exc}_\n"

    lines.append("### Сьогодні")
    if today:
        for e in today:
            when = "весь день" if e["all_day"] else e["start"][11:16]
            loc = f" @ {e['location']}" if e["location"] else ""
            lines.append(f"- **{when}** — {e['summary']}{loc}")
    else:
        lines.append("_Подій на сьогодні немає._")
    lines.append("")

    # Події завтра й далі (виключаємо сьогоднішні за ID).
    today_ids = {e["id"] for e in today}
    future = [e for e in upcoming if e["id"] not in today_ids]
    if future:
        lines.append("### Найближчі дні")
        for e in future:
            date_part = e["start"][:10]
            when = "весь день" if e["all_day"] else e["start"][11:16]
            lines.append(f"- **{date_part} {when}** — {e['summary']}")
        lines.append("")
    return "\n".join(lines)


def _format_tasks_section(cfg: dict[str, Any]) -> str:
    """Формує секцію завдань (відкриті + прострочені)."""
    spreadsheet_id = cfg.get("spreadsheet_id", "")
    sheet_name = cfg.get("sheet_name", "Реєстр")
    lines = ["## ✅ Завдання", ""]

    if not spreadsheet_id or spreadsheet_id == "ВАШ_SPREADSHEET_ID":
        lines.append("_Не налаштовано spreadsheet_id у config.yaml._")
        lines.append("")
        return "\n".join(lines)

    try:
        tm = TaskManager(spreadsheet_id, sheet_name=sheet_name)
        open_tasks = tm.get_open_tasks()
        overdue = tm.get_overdue_tasks()
    except Exception as exc:  # noqa: BLE001
        return f"## ✅ Завдання\n\n_Помилка доступу до Google Sheets: {exc}_\n"

    # Рядки прострочених показуємо окремо; щоб не дублювати їх у списку
    # відкритих, формуємо множину номерів рядків прострочених завдань.
    overdue_rows = {t.get("_row") for t in overdue}

    if overdue:
        lines.append(f"### ⚠️ Прострочені ({len(overdue)})")
        for t in overdue:
            prio = t.get("Пріоритет")
            prio_str = f"{prio} · " if prio else ""
            lines.append(
                f"- {prio_str}**{t.get('Завдання')}** — строк: {t.get('Термін') or '—'} "
                f"({t.get('Відповідальний') or 'без виконавця'})"
            )
        lines.append("")

    # Решта відкритих завдань (без уже показаних прострочених).
    other_open = [t for t in open_tasks if t.get("_row") not in overdue_rows]
    lines.append(f"### Відкриті завдання ({len(open_tasks)})")
    if other_open:
        for t in other_open:
            prio = t.get("Пріоритет")
            prio_str = f"{prio} · " if prio else ""
            status = t.get("Статус") or "—"
            lines.append(
                f"- {prio_str}**{t.get('Завдання')}** "
                f"— _{status}_ (строк: {t.get('Термін') or '—'})"
            )
    elif not overdue:
        lines.append("_Відкритих завдань немає._")
    lines.append("")
    return "\n".join(lines)


def _format_researcher_section(cfg: dict[str, Any]) -> str:
    """Формує секцію дайджесту Дослідника."""
    if not cfg.get("enabled", False):
        return ""
    try:
        researcher = Researcher(keywords=cfg.get("keywords", []))
        moz = researcher.search_moz_updates(limit=cfg.get("limit_per_source", 5))
        nszu = researcher.search_nszu_updates(limit=cfg.get("limit_per_source", 5))
    except Exception as exc:  # noqa: BLE001
        return f"## 📰 Дослідник\n\n_Помилка моніторингу джерел: {exc}_\n"

    lines = ["## 📰 Дослідник (МОЗ / НСЗУ)", ""]
    lines.append("### МОЗ України")
    lines += [f"- [{it['title']}]({it['url']})" for it in moz] or ["_Немає нових записів._"]
    lines.append("")
    lines.append("### НСЗУ")
    lines += [f"- [{it['title']}]({it['url']})" for it in nszu] or ["_Немає нових записів._"]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# Головна логіка
# ---------------------------------------------------------------------- #
def build_briefing(config: dict[str, Any]) -> str:
    """Збирає всі секції та повертає повний текст брифінгу (markdown)."""
    now = datetime.now()
    header = [
        f"# 🗂️ Щоденний брифінг Command Center",
        f"**Дата:** {now:%A, %d.%m.%Y} · **Час формування:** {now:%H:%M}",
        "",
        "---",
        "",
    ]

    gmail = None
    calendar = None
    # Ініціалізуємо клієнтів окремо, щоб частковий збій не зривав увесь брифінг.
    try:
        gmail = GmailClient()
    except Exception as exc:  # noqa: BLE001
        print(f"[!] Не вдалося ініціалізувати Gmail: {exc}")
    try:
        calendar = CalendarClient(
            calendar_id=config.get("calendar", {}).get("calendar_id", "primary")
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[!] Не вдалося ініціалізувати Calendar: {exc}")

    sections = []
    if gmail is not None:
        sections.append(_format_emails_section(gmail, config.get("gmail", {})))
    if calendar is not None:
        sections.append(_format_calendar_section(calendar, config.get("calendar", {})))
    sections.append(_format_tasks_section(config.get("tasks", {})))
    sections.append(_format_researcher_section(config.get("researcher", {})))

    footer = [
        "---",
        "",
        "> *«Системи не замінюють людей, вони дають людям можливість бути ефективнішими.»*",
    ]

    parts = header + [s for s in sections if s] + footer
    return "\n".join(parts)


def save_briefing(text: str, output_dir: str) -> Path:
    """Зберігає брифінг у файл ``briefing_YYYY-MM-DD.md`` та повертає шлях."""
    out_dir = _ROOT / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"briefing_{datetime.now():%Y-%m-%d}.md"
    out_path.write_text(text, encoding="utf-8")
    return out_path


def main() -> None:
    """Точка входу: парсинг аргументів, збір даних, збереження, розсилка."""
    parser = argparse.ArgumentParser(description="Щоденний брифінг Command Center")
    parser.add_argument("--config", help="Шлях до config.yaml", default=None)
    parser.add_argument("--stdout", action="store_true", help="Вивести у консоль")
    parser.add_argument("--no-drive", action="store_true", help="Не зберігати в Google Drive")
    args = parser.parse_args()

    # КРОК 1: Завантажити checkpoints з Google Drive (delta-only mode)
    print("[→] Завантаження checkpoints з Google Drive...")
    checkpoints_reader = CheckpointsReader()
    checkpoints = checkpoints_reader.load_from_drive()
    
    if checkpoints:
        print(f"[✓] Checkpoints завантажено (gmail: {checkpoints.get('gmail_last_success', 'N/A')})")
    else:
        print("[!] Checkpoints не завантажені, використовуємо defaults")

    # КРОК 2: Генерувати брифінг
    config = load_config(args.config)
    briefing = build_briefing(config)

    # КРОК 3: Зберегти локально
    output_dir = config.get("general", {}).get("output_dir", "briefings")
    path = save_briefing(briefing, output_dir)
    print(f"[✓] Брифінг збережено локально: {path}")

    # КРОК 4: Зберегти у Google Drive (daily/)
    if not args.no_drive:
        try:
            print("[→] Збереження брифінгу в Google Drive...")
            drive = DriveClient()
            daily_folder_id = '1a0RJCRUXOBHf_75mm7Pu2cBIeKSXG3hN'  # ID папки daily/
            filename = f"briefing_{datetime.now():%Y-%m-%d}.md"
            
            file_id = drive.write_file(briefing, filename, daily_folder_id)
            
            if file_id:
                print(f"[✓] Брифінг збережено в Drive: daily/{filename}")
            else:
                print("[!] Не вдалося зберегти в Drive")
        except Exception as exc:
            print(f"[!] Помилка збереження в Drive: {exc}")

    if args.stdout:
        print("\n" + briefing)

    # КРОК 5: Необовʼязкова розсилка брифінгу на пошту
    delivery = config.get("delivery", {})
    if delivery.get("send_email") and delivery.get("email_to"):
        try:
            GmailClient().send_message(
                to=delivery["email_to"],
                subject=f"Щоденний брифінг — {datetime.now():%d.%m.%Y}",
                body=briefing,
            )
            print(f"[✓] Брифінг надіслано на {delivery['email_to']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Не вдалося надіслати брифінг: {exc}")
    
    print("\n[✓] Усі операції завершено")


if __name__ == "__main__":
    main()
