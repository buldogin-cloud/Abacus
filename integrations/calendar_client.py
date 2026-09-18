"""Інтеграція з Google Calendar API.

Клас :class:`CalendarClient` надає методи для отримання подій на сьогодні
та на найближчі дні. Використовується модулем щоденного брифінгу.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from googleapiclient.discovery import build

from .google_auth import get_credentials


class CalendarClient:
    """Клієнт для роботи з подіями Google Calendar."""

    def __init__(self, calendar_id: str = "primary") -> None:
        """Ініціалізує сервіс Calendar.

        :param calendar_id: ідентифікатор календаря ("primary" — основний).
        """
        self.calendar_id = calendar_id
        creds = get_credentials()
        self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # ------------------------------------------------------------------ #
    # Внутрішні допоміжні методи
    # ------------------------------------------------------------------ #
    def _fetch_events(self, time_min: datetime, time_max: datetime) -> list[dict[str, Any]]:
        """Повертає події у заданому проміжку часу, впорядковані за початком."""
        response = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return [self._format_event(e) for e in response.get("items", [])]

    @staticmethod
    def _format_event(event: dict[str, Any]) -> dict[str, Any]:
        """Форматує подію у зручну структуру."""
        start = event.get("start", {})
        end = event.get("end", {})
        return {
            "id": event.get("id"),
            "summary": event.get("summary", "(без назви)"),
            "location": event.get("location", ""),
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "all_day": "date" in start,
            "attendees": [a.get("email") for a in event.get("attendees", [])],
        }

    # ------------------------------------------------------------------ #
    # Публічний інтерфейс
    # ------------------------------------------------------------------ #
    def get_today_events(self) -> list[dict[str, Any]]:
        """Повертає всі події на сьогодні."""
        now = datetime.now(timezone.utc).astimezone()
        start_of_day = datetime.combine(now.date(), time.min).astimezone()
        end_of_day = datetime.combine(now.date(), time.max).astimezone()
        return self._fetch_events(start_of_day, end_of_day)

    def get_upcoming_events(self, days: int = 2) -> list[dict[str, Any]]:
        """Повертає події на найближчі ``days`` днів (враховуючи сьогодні).

        :param days: кількість днів наперед (2 = сьогодні + завтра).
        """
        now = datetime.now(timezone.utc).astimezone()
        start_of_day = datetime.combine(now.date(), time.min).astimezone()
        end = datetime.combine((now + timedelta(days=days - 1)).date(), time.max).astimezone()
        return self._fetch_events(start_of_day, end)


if __name__ == "__main__":
    # Проста ручна перевірка роботи клієнта.
    client = CalendarClient()
    print("Події на сьогодні:")
    for e in client.get_today_events():
        print(f"  • {e['start']} — {e['summary']}")
