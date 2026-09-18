"""Інтеграція з Google Sheets API.

Клас :class:`SheetsClient` надає низькорівневі методи роботи з таблицями:
читання діапазонів, додавання рядків, оновлення клітинок. Використовується
модулем управління завданнями (task_manager).
"""

from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build

from .google_auth import get_credentials


class SheetsClient:
    """Клієнт для роботи з Google Sheets."""

    def __init__(self, spreadsheet_id: str) -> None:
        """Ініціалізує сервіс Sheets.

        :param spreadsheet_id: ідентифікатор таблиці (з URL Google Sheets).
        """
        self.spreadsheet_id = spreadsheet_id
        creds = get_credentials()
        self.service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def read_range(self, range_name: str) -> list[list[str]]:
        """Читає значення діапазону (напр. ``"Завдання!A1:F100"``)."""
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )
        return result.get("values", [])

    def read_records(self, range_name: str) -> list[dict[str, str]]:
        """Читає діапазон як список словників (перший рядок — заголовки)."""
        rows = self.read_range(range_name)
        if not rows:
            return []
        headers = rows[0]
        records = []
        for i, row in enumerate(rows[1:], start=2):
            # Доповнюємо рядок до довжини заголовків.
            padded = row + [""] * (len(headers) - len(row))
            record = dict(zip(headers, padded))
            record["_row"] = i  # номер рядка в таблиці (для оновлень)
            records.append(record)
        return records

    def append_row(self, range_name: str, values: list[Any]) -> dict[str, Any]:
        """Додає новий рядок у кінець діапазону."""
        return (
            self.service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [values]},
            )
            .execute()
        )

    def update_cell(self, range_name: str, value: Any) -> dict[str, Any]:
        """Оновлює одну клітинку або діапазон (напр. ``"Завдання!E5"``)."""
        return (
            self.service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": [[value]]},
            )
            .execute()
        )


if __name__ == "__main__":
    # Проста ручна перевірка (потрібен реальний spreadsheet_id).
    import sys

    if len(sys.argv) > 1:
        client = SheetsClient(sys.argv[1])
        print(client.read_range("A1:F10"))
