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

    def read_range(
        self,
        range_name: str,
        *,
        value_render_option: str = "FORMATTED_VALUE",
    ) -> list[list[Any]]:
        """Читає значення діапазону (напр. ``"Завдання!A1:F100"``)."""
        result = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueRenderOption=value_render_option,
            )
            .execute()
        )
        return result.get("values", [])

    def batch_update_values(
        self,
        data: list[dict[str, Any]],
        *,
        value_input_option: str = "RAW",
    ) -> dict[str, Any]:
        """Apply narrow, explicitly ranged value updates in one API request.

        ``RAW`` is the safe default for Hub history: source text cannot become
        an unintended formula.  Callers must perform a readback before moving
        any checkpoint.
        """
        if not data:
            return {"totalUpdatedCells": 0}
        return (
            self.service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "valueInputOption": value_input_option,
                    "data": data,
                    "includeValuesInResponse": False,
                },
            )
            .execute()
        )

    def read_user_entered_values(self, range_name: str) -> list[dict[str, Any]]:
        """Read only formulas/literals physically entered in the guarded grid.

        Calculated and spilled values are intentionally excluded so a normal
        source refresh cannot look like a frontend-formula mutation.
        """
        result = (
            self.service.spreadsheets()
            .get(
                spreadsheetId=self.spreadsheet_id,
                ranges=[range_name],
                includeGridData=True,
                fields=(
                    "sheets(properties(sheetId,title),"
                    "data(startRow,startColumn,rowData(values(userEnteredValue))))"
                ),
            )
            .execute()
        )
        return result.get("sheets", [])

    def get_sheet_ids(self) -> dict[str, int]:
        """Return exact title -> numeric sheet ID without reading cell data."""
        result = (
            self.service.spreadsheets()
            .get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(sheetId,title)",
            )
            .execute()
        )
        return {
            item["properties"]["title"]: int(item["properties"]["sheetId"])
            for item in result.get("sheets", [])
        }

    def atomic_batch_update(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply validated spreadsheet requests atomically.

        Google Sheets validates all ``spreadsheets.batchUpdate`` requests before
        applying them.  The Hub runner still performs an exact readback because
        a network failure can make the client uncertain whether the batch was
        committed.
        """
        if not requests:
            return {"replies": []}
        return (
            self.service.spreadsheets()
            .batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests, "includeSpreadsheetInResponse": False},
            )
            .execute()
        )

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
