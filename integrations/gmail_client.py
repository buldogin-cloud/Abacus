"""Інтеграція з Gmail API.

Клас :class:`GmailClient` надає зручні методи для отримання непрочитаних
та важливих листів, а також для надсилання повідомлень. Використовується
модулем щоденного брифінгу.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build

from .google_auth import get_credentials


class GmailClient:
    """Клієнт для роботи з поштовою скринькою через Gmail API."""

    def __init__(self, user_id: str = "me") -> None:
        """Ініціалізує сервіс Gmail.

        :param user_id: ідентифікатор користувача Gmail ("me" — поточний).
        """
        self.user_id = user_id
        creds = get_credentials()
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    # ------------------------------------------------------------------ #
    # Внутрішні допоміжні методи
    # ------------------------------------------------------------------ #
    def _list_message_ids(self, query: str, max_results: int = 25) -> list[str]:
        """Повертає ID листів за пошуковим запитом Gmail."""
        response = (
            self.service.users()
            .messages()
            .list(userId=self.user_id, q=query, maxResults=max_results)
            .execute()
        )
        return [m["id"] for m in response.get("messages", [])]

    def _get_message_summary(self, message_id: str) -> dict[str, Any]:
        """Повертає стислу інформацію про лист: відправник, тема, дата, фрагмент."""
        msg = (
            self.service.users()
            .messages()
            .get(
                userId=self.user_id,
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        return {
            "id": message_id,
            "from": headers.get("From", "(невідомо)"),
            "subject": headers.get("Subject", "(без теми)"),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        }

    # ------------------------------------------------------------------ #
    # Публічний інтерфейс
    # ------------------------------------------------------------------ #
    def get_unread_messages(self, days: int = 1, max_results: int = 25) -> list[dict[str, Any]]:
        """Повертає непрочитані листи за останні ``days`` днів.

        :param days: скільки останніх днів охопити.
        :param max_results: максимальна кількість листів.
        """
        after = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
        query = f"is:unread after:{after}"
        ids = self._list_message_ids(query, max_results=max_results)
        return [self._get_message_summary(mid) for mid in ids]

    def get_important_messages(self, days: int = 1, max_results: int = 25) -> list[dict[str, Any]]:
        """Повертає важливі листи (позначені Gmail як important) за ``days`` днів."""
        after = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
        query = f"is:important after:{after}"
        ids = self._list_message_ids(query, max_results=max_results)
        return [self._get_message_summary(mid) for mid in ids]

    def send_message(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Надсилає текстовий лист.

        :param to: адреса отримувача.
        :param subject: тема листа.
        :param body: текст листа (plain text).
        :return: відповідь Gmail API про надісланий лист.
        """
        message = MIMEText(body, _charset="utf-8")
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        return (
            self.service.users()
            .messages()
            .send(userId=self.user_id, body={"raw": raw})
            .execute()
        )


if __name__ == "__main__":
    # Проста ручна перевірка роботи клієнта.
    client = GmailClient()
    print("Непрочитані листи за 24 години:")
    for m in client.get_unread_messages(days=1):
        print(f"  • {m['from']} — {m['subject']}")
