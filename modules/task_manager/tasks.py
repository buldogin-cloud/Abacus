"""Модуль управління завданнями (Task Manager).

Клас :class:`TaskManager` веде реєстр завдань у Google Sheets. Реальна
структура таблиці «Секретар — реєстр завдань», аркуш ``Реєстр``,
перший рядок — заголовки:

    Пріоритет | № документа | Дата | Назва |
    Завдання / очікуваний результат | Моя роль |
    Виконавець / підрозділ | Строк | Статус | Остання відповідь / доказ

Статуси в реєстрі — описові (наприклад «Постійне», «Частково виконано»,
«Прострочено — ...», «У процесі»). Тому «завершеність» визначається за
ключовими словами (див. :func:`_is_done`), а не за фіксованим набором.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Дозволяємо запуск як окремого скрипта та як частини пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from integrations.sheets_client import SheetsClient  # noqa: E402

# --- Назви колонок у реальній таблиці ---
COL_PRIORITY = "Пріоритет"
COL_DOC = "№ документа"
COL_DATE = "Дата"
COL_TITLE = "Назва"
COL_TASK = "Завдання / очікуваний результат"
COL_ROLE = "Моя роль"
COL_ASSIGNEE = "Виконавець / підрозділ"
COL_DEADLINE = "Строк"
COL_STATUS = "Статус"
COL_PROOF = "Остання відповідь / доказ"

# Назва аркуша та діапазон за замовчуванням (10 колонок A:J).
DEFAULT_SHEET = "Реєстр"
DEFAULT_RANGE_COLS = "A1:J1000"

# Індекс колонки «Статус» для оновлення (I = 9-та колонка).
STATUS_COLUMN_LETTER = "I"


def _is_done(status: str) -> bool:
    """Визначає, чи завдання ПОВНІСТЮ завершене (за ключовими словами).

    «Частково виконано» вважається відкритим завданням.
    """
    s = (status or "").lower().strip()
    if not s:
        return False
    if "частково" in s:
        return False
    return any(k in s for k in ("виконано", "закрито", "скасовано", "завершено"))


def _extract_date(text: str) -> date | None:
    """Витягує першу дату (DD.MM.YYYY або YYYY-MM-DD) з довільного тексту."""
    if not text:
        return None
    # Формат DD.MM.YYYY
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%d.%m.%Y").date()
        except ValueError:
            pass
    # Формат YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def _normalize(record: dict[str, str]) -> dict[str, str]:
    """Перетворює «сирий» рядок таблиці у зручний словник для брифінгу."""
    return {
        # Ключі, які очікує форматувальник брифінгу.
        "Завдання": record.get(COL_TITLE, "").strip(),
        "Деталі": record.get(COL_TASK, "").strip(),
        "Термін": record.get(COL_DEADLINE, "").strip(),
        "Відповідальний": record.get(COL_ASSIGNEE, "").strip(),
        "Статус": record.get(COL_STATUS, "").strip(),
        "Пріоритет": record.get(COL_PRIORITY, "").strip(),
        "Документ": record.get(COL_DOC, "").strip(),
        "Доказ": record.get(COL_PROOF, "").strip(),
        "_row": record.get("_row"),
    }


class TaskManager:
    """Управління реєстром завдань у Google Sheets."""

    def __init__(self, spreadsheet_id: str, sheet_name: str = DEFAULT_SHEET) -> None:
        """Ініціалізує менеджер завдань.

        :param spreadsheet_id: ID таблиці Google Sheets.
        :param sheet_name: назва аркуша із завданнями (за замовчуванням «Реєстр»).
        """
        self.client = SheetsClient(spreadsheet_id)
        self.sheet_name = sheet_name
        self.range = f"{sheet_name}!{DEFAULT_RANGE_COLS}"

    def _is_overdue(self, deadline: str, status: str) -> bool:
        """Перевіряє, чи прострочене завдання.

        Прострочене, якщо: (а) статус містить «прострочено», або
        (б) з поля «Строк» вдалося витягти дату, і вона вже минула —
        і завдання не завершене.
        """
        if _is_done(status):
            return False
        if "прострочено" in (status or "").lower():
            return True
        due = _extract_date(deadline)
        if due is not None:
            return due < date.today()
        return False

    def get_open_tasks(self) -> list[dict[str, str]]:
        """Повертає всі відкриті (незавершені) завдання, нормалізовані."""
        records = self.client.read_records(self.range)
        return [
            _normalize(r)
            for r in records
            if r.get(COL_TITLE, "").strip() and not _is_done(r.get(COL_STATUS, ""))
        ]

    def get_overdue_tasks(self) -> list[dict[str, str]]:
        """Повертає прострочені завдання (термін минув / статус «Прострочено»)."""
        records = self.client.read_records(self.range)
        result = []
        for r in records:
            if not r.get(COL_TITLE, "").strip():
                continue
            if self._is_overdue(r.get(COL_DEADLINE, ""), r.get(COL_STATUS, "")):
                result.append(_normalize(r))
        return result

    def get_all_tasks(self) -> list[dict[str, str]]:
        """Повертає всі завдання (нормалізовані), для повного огляду реєстру."""
        records = self.client.read_records(self.range)
        return [_normalize(r) for r in records if r.get(COL_TITLE, "").strip()]

    def update_task_status(self, row: int, new_status: str) -> dict[str, Any]:
        """Оновлює статус завдання за номером рядка в таблиці.

        :param row: номер рядка (значення ``_row`` з нормалізованого запису).
        :param new_status: новий текст статусу.
        """
        cell = f"{self.sheet_name}!{STATUS_COLUMN_LETTER}{row}"
        return self.client.update_cell(cell, new_status)


if __name__ == "__main__":
    # Проста ручна перевірка (потрібен реальний spreadsheet_id як аргумент).
    if len(sys.argv) > 1:
        tm = TaskManager(sys.argv[1])
        print("Відкриті завдання:")
        for t in tm.get_open_tasks():
            print(
                f"  • [{t.get('Пріоритет')}] {t.get('Завдання')} "
                f"— {t.get('Статус')} (строк: {t.get('Термін') or '—'})"
            )
        print("\nПрострочені:")
        for t in tm.get_overdue_tasks():
            print(f"  ⚠️ {t.get('Завдання')} — {t.get('Статус')}")
