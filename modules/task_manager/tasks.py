"""Модуль управління завданнями (Task Manager).

Клас :class:`TaskManager` веде реєстр завдань у Google Sheets. Очікувана
структура таблиці (аркуш ``Завдання``), перший рядок — заголовки:

    ID | Завдання | Відповідальний | Термін | Статус | Примітки

Статуси: ``Нове``, ``В роботі``, ``Виконано``, ``Скасовано``.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Дозволяємо запуск як окремого скрипта та як частини пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from integrations.sheets_client import SheetsClient  # noqa: E402

# Статуси, що вважаються «відкритими» (незавершеними).
OPEN_STATUSES = {"Нове", "В роботі", ""}
DONE_STATUSES = {"Виконано", "Скасовано"}

# Назва аркуша та діапазон за замовчуванням.
DEFAULT_SHEET = "Завдання"
DEFAULT_RANGE = f"{DEFAULT_SHEET}!A1:F1000"


class TaskManager:
    """Управління реєстром завдань у Google Sheets."""

    def __init__(self, spreadsheet_id: str, sheet_name: str = DEFAULT_SHEET) -> None:
        """Ініціалізує менеджер завдань.

        :param spreadsheet_id: ID таблиці Google Sheets.
        :param sheet_name: назва аркуша із завданнями.
        """
        self.client = SheetsClient(spreadsheet_id)
        self.sheet_name = sheet_name
        self.range = f"{sheet_name}!A1:F1000"

    @staticmethod
    def _is_overdue(deadline: str, status: str) -> bool:
        """Перевіряє, чи прострочене завдання (термін минув, ще не завершене)."""
        if status in DONE_STATUSES or not deadline:
            return False
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                due = datetime.strptime(deadline.strip(), fmt).date()
                return due < date.today()
            except ValueError:
                continue
        return False

    def get_open_tasks(self) -> list[dict[str, str]]:
        """Повертає всі відкриті (незавершені) завдання."""
        records = self.client.read_records(self.range)
        return [r for r in records if r.get("Статус", "") not in DONE_STATUSES]

    def get_overdue_tasks(self) -> list[dict[str, str]]:
        """Повертає прострочені завдання (термін минув, не завершені)."""
        records = self.client.read_records(self.range)
        return [
            r
            for r in records
            if self._is_overdue(r.get("Термін", ""), r.get("Статус", ""))
        ]

    def add_task(
        self,
        title: str,
        assignee: str = "",
        deadline: str = "",
        notes: str = "",
        status: str = "Нове",
    ) -> dict[str, Any]:
        """Додає нове завдання у реєстр.

        :param title: назва/опис завдання.
        :param assignee: відповідальний.
        :param deadline: термін виконання (YYYY-MM-DD або DD.MM.YYYY).
        :param notes: примітки.
        :param status: початковий статус.
        :return: результат додавання рядка + згенерований ID.
        """
        task_id = uuid.uuid4().hex[:8]
        row = [task_id, title, assignee, deadline, status, notes]
        result = self.client.append_row(self.range, row)
        result["task_id"] = task_id
        return result

    def update_task_status(self, task_id: str, new_status: str) -> dict[str, Any]:
        """Оновлює статус завдання за його ID.

        :param task_id: ідентифікатор завдання (колонка ID).
        :param new_status: новий статус.
        :raises ValueError: якщо завдання з таким ID не знайдено.
        """
        records = self.client.read_records(self.range)
        for r in records:
            if r.get("ID") == task_id:
                # Статус — 5-та колонка (E).
                cell = f"{self.sheet_name}!E{r['_row']}"
                return self.client.update_cell(cell, new_status)
        raise ValueError(f"Завдання з ID '{task_id}' не знайдено.")


if __name__ == "__main__":
    # Проста ручна перевірка (потрібен реальний spreadsheet_id як аргумент).
    if len(sys.argv) > 1:
        tm = TaskManager(sys.argv[1])
        print("Відкриті завдання:")
        for t in tm.get_open_tasks():
            print(f"  • [{t.get('Статус')}] {t.get('Завдання')} (до {t.get('Термін')})")
