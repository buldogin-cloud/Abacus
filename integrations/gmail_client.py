"""Інтеграція з Gmail через IMAP/SMTP.

Клас GmailClient надає методи для отримання непрочитаних та важливих листів
(через IMAP), а також для надсилання повідомлень (через SMTP).

Використовує App Password замість OAuth — токен ніколи не помирає.
"""

import os
import smtplib
from datetime import datetime, timedelta
from email import message_from_bytes, message_from_string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any
import imaplib


class GmailClient:
    """Клієнт для роботи з Gmail через IMAP/SMTP (без OAuth)."""

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587
    EMAIL = "andrewbelin6@gmail.com"

    def __init__(self, user_id: str = "me") -> None:
        """Ініціалізує IMAP/SMTP клієнт для Gmail.

        :param user_id: ігнорується (для сумісності з API)
        """
        self.user_id = user_id
        self.email = self.EMAIL
        self.app_password = self._get_app_password()
        if not self.app_password:
            raise RuntimeError(
                "App Password не знайдено. Встанови GMAIL_APP_PASSWORD у змінну оточення "
                "або збереги у /home/ubuntu/gmail_app_password.txt"
            )
        self.imap = None

    def _get_app_password(self) -> str | None:
        """Отримує Google App Password з oточення або файлу."""
        pwd = os.getenv("GMAIL_APP_PASSWORD")
        if pwd:
            return pwd
        fpath = "/home/ubuntu/gmail_app_password.txt"
        if os.path.exists(fpath):
            with open(fpath) as f:
                return f.read().strip()
        return None

    def _connect_imap(self):
        """Підключається до IMAP (lazy connection)."""
        if self.imap is None:
            self.imap = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
            self.imap.login(self.email, self.app_password)

    def _disconnect_imap(self):
        """Відключається від IMAP."""
        if self.imap:
            try:
                self.imap.close()
            except:
                pass
            self.imap = None

    def _list_message_ids(self, query: str, max_results: int = 25) -> list[str]:
        """Повертає UID листів за пошуком IMAP."""
        try:
            self._connect_imap()
            self.imap.select("INBOX")
            # IMAP SEARCH за стандартними критеріями
            status, uids = self.imap.search(None, query)
            if status == "OK" and uids[0]:
                uid_list = uids[0].split()[-max_results:]  # останні (найновіші)
                return [u.decode() if isinstance(u, bytes) else u for u in uid_list]
            return []
        except Exception as e:
            print(f"Помилка IMAP search: {e}")
            self._disconnect_imap()
            return []

    def _get_message_summary(self, uid: str) -> dict[str, Any]:
        """Повертає стислу інформацію про лист."""
        try:
            self._connect_imap()
            status, data = self.imap.fetch(uid, "(RFC822)")
            if status != "OK":
                return {}
            
            msg = message_from_bytes(data[0][1])
            from_addr = msg.get("From", "Unknown")
            subject = msg.get("Subject", "(no subject)")
            date_str = msg.get("Date", "Unknown")
            
            # Витягнути перший текстовий фрагмент
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            
            snippet = body[:200] if body else "(empty)"
            
            return {
                "id": uid,
                "from": from_addr,
                "subject": subject,
                "date": date_str,
                "snippet": snippet,
            }
        except Exception as e:
            print(f"Помилка при читанні листа {uid}: {e}")
            return {}

    # ------------------------------------------------------------------ #
    # Публічні методи
    # ------------------------------------------------------------------ #
    def verify_connection(self) -> bool:
        """Явно підтверджує робоче IMAP-підключення (Req 1).

        Виконує реальний LOGIN + SELECT INBOX. Якщо App Password невірний або
        сервер недоступний — піднімає виняток. Саме це підтвердження (а не
        «0 листів») є доказом справної роботи Gmail.
        """
        self._connect_imap()
        status, _ = self.imap.select("INBOX")
        if status != "OK":
            raise RuntimeError(f"IMAP SELECT INBOX повернув статус {status}")
        return True

    def get_unread_messages(self, days: int = 1, max_results: int = 25) -> list[dict[str, Any]]:
        """Повертає непрочитані листи за останні N днів."""
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            # IMAP критерій: UNSEEN (непрочитані) та SINCE (після дати)
            query = f'UNSEEN SINCE {cutoff}'
            uids = self._list_message_ids(query, max_results)
            
            result = []
            for uid in uids:
                msg_data = self._get_message_summary(uid)
                if msg_data:
                    result.append(msg_data)
            return result
        except Exception as e:
            print(f"Помилка get_unread_messages: {e}")
            return []

    def get_important_messages(self, days: int = 1, max_results: int = 25) -> list[dict[str, Any]]:
        """Повертає важливі листи за останні N днів."""
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            # IMAP критерій: FLAGGED (важливі)
            query = f'FLAGGED SINCE {cutoff}'
            uids = self._list_message_ids(query, max_results)
            
            result = []
            for uid in uids:
                msg_data = self._get_message_summary(uid)
                if msg_data:
                    result.append(msg_data)
            return result
        except Exception as e:
            print(f"Помилка get_important_messages: {e}")
            return []

    def send_message(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Надсилає лист через SMTP."""
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = self.email
            msg["To"] = to
            
            with smtplib.SMTP(self.SMTP_HOST, self.SMTP_PORT) as server:
                server.starttls()
                server.login(self.email, self.app_password)
                server.send_message(msg)
            
            return {"id": f"sent-{datetime.now().isoformat()}", "labelIds": ["SENT"]}
        except Exception as e:
            print(f"Помилка при надсиланні: {e}")
            raise

    def __del__(self):
        """Закриває IMAP при видаленні об'єкта."""
        self._disconnect_imap()
