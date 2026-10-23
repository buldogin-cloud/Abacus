"""Read-only Gmail collection via IMAP; SMTP sends remain explicit calls only.

Collection never changes Seen flags. Failures are not represented as empty mailboxes.
App passwords can be revoked; missing/invalid credentials require user action.
"""
import os
import re
import smtplib
import imaplib
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.mime.text import MIMEText
from typing import Any


class GmailClient:
    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587
    EMAIL = "andrewbelin6@gmail.com"

    def __init__(self, user_id: str = "me") -> None:
        self.user_id = user_id
        self.email = self.EMAIL
        self.imap = None
        self.uidvalidity = None
        self.last_collection = {}
        self.app_password = self._get_app_password()
        if not self.app_password:
            raise RuntimeError("Missing GMAIL_APP_PASSWORD; configure it through the approved secret store, never chat or logs")

    def _get_app_password(self) -> str | None:
        pwd = os.getenv("GMAIL_APP_PASSWORD")
        if pwd:
            return pwd.strip()
        fpath = "/home/ubuntu/gmail_app_password.txt"
        if os.path.exists(fpath):
            with open(fpath) as f:
                return f.read().strip()
        return None

    def _connect_imap(self):
        if self.imap is None:
            connection = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT, timeout=30)
            try:
                connection.login(self.email, self.app_password)
            except Exception:
                try:
                    connection.logout()
                except Exception:
                    pass
                raise RuntimeError("Gmail IMAP login failed; check account access securely") from None
            self.imap = connection

    def _disconnect_imap(self):
        connection = getattr(self, "imap", None)
        self.imap = None
        if connection:
            try:
                connection.logout()
            except Exception:
                pass

    def verify_connection(self) -> bool:
        self._connect_imap()
        status, _ = self.imap.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("IMAP read-only INBOX selection failed")
        _, values = self.imap.response("UIDVALIDITY")
        value = values[0] if values else None
        self.uidvalidity = value.decode() if isinstance(value, bytes) else value
        return True

    def _list_message_ids(self, query: str, max_results: int = 25) -> list[str]:
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        self.verify_connection()
        status, values = self.imap.uid("search", None, query)
        if status != "OK" or not isinstance(values, list) or not values or values[0] is None:
            raise RuntimeError("IMAP UID search failed; mailbox completeness is unknown")
        ids = values[0].split()
        self.last_collection = {
            "scope": "INBOX only; archived, spam and trash are not included",
            "query": query, "matched": len(ids), "limit": max_results,
            "truncated": len(ids) > max_results,
        }
        # Oldest first, with explicit incomplete status if capped; checkpoints must not advance.
        return [i.decode() if isinstance(i, bytes) else str(i) for i in ids[:max_results]]

    def _get_message_summary(self, uid: str) -> dict[str, Any]:
        self._connect_imap()
        caps = {c.decode().upper() if isinstance(c, bytes) else str(c).upper()
                for c in getattr(self.imap, "capabilities", ())}
        gmail = "X-GM-EXT-1" in caps
        fields = "UID FLAGS INTERNALDATE"
        if gmail:
            fields += " X-GM-MSGID X-GM-THRID X-GM-LABELS"
        fields += " BODY.PEEK[]"
        status, data = self.imap.uid("fetch", uid, "(" + fields + ")")
        chunks = [item for item in (data or []) if isinstance(item, tuple) and len(item) == 2]
        if status != "OK" or not chunks or not isinstance(chunks[0][1], bytes):
            raise RuntimeError("IMAP UID fetch failed; collection is incomplete")
        header, raw = chunks[0]
        # Servers may place metadata after the body literal as separate bytes.
        metadata = [header] + [item for item in data if isinstance(item, bytes)]
        header = " ".join(item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
                          for item in metadata)
        msg = message_from_bytes(raw, policy=policy.default)
        def gm_id(field):
            match = re.search(r"\b" + field + r"\s+(\d+)", header)
            return format(int(match.group(1)), "x") if match else ""
        message_id = gm_id("X-GM-MSGID")
        thread_id = gm_id("X-GM-THRID")
        if not message_id and not self.uidvalidity:
            raise RuntimeError("No stable Gmail ID or IMAP UIDVALIDITY; refusing unstable message identity")
        source_id = message_id or f"imap:{self.email}:INBOX:{self.uidvalidity}:{uid}"
        flags_match = re.search(r"FLAGS\s+\(([^)]*)\)", header)
        flags = flags_match.group(1).split() if flags_match else []
        labels_match = re.search(r"X-GM-LABELS\s+\(([^)]*)\)", header)
        labels = labels_match.group(1).split() if labels_match else []
        internaldate_match = re.search(r'INTERNALDATE\s+"([^"]+)"', header)
        internaldate = internaldate_match.group(1) if internaldate_match else ""
        body_parts, attachments = [], []
        for part in msg.walk():
            if part.is_multipart():
                continue
            if part.get_content_disposition() == "attachment" or part.get_filename():
                payload = part.get_payload(decode=True) or b""
                attachments.append({"filename": part.get_filename() or "", "type": part.get_content_type(),
                                    "size_bytes": len(payload), "analysis_status": "not_analyzed"})
            elif part.get_content_type() == "text/plain":
                body_parts.append(part.get_content())
        body = "\n".join(body_parts)
        body_format = "text/plain"
        if not body_parts:
            html = msg.get_body(preferencelist=("html",))
            if html is not None:
                body = html.get_content()
                body_format = "text/html"
        return {"id": source_id, "message_id": message_id, "threadId": thread_id,
                "id_kind": "gmail" if message_id else "imap_uid",
                "url": f"https://mail.google.com/mail/#all/{message_id}" if message_id else "",
                "from": str(msg.get("From", "")), "to": str(msg.get("To", "")),
                "subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", "")),
                "rfc_message_id": str(msg.get("Message-ID", "")),
                "internaldate": internaldate,
                "snippet": body[:200], "body": body, "body_format": body_format,
                "body_complete": True, "attachments": attachments, "labels": labels,
                "is_unread": "\\Seen" not in flags, "is_important": "\\Flagged" in flags,
                "is_draft": "\\Draft" in flags or "\\Drafts" in labels,
                "is_sent": "\\Sent" in labels}

    def _messages(self, query, max_results):
        return [self._get_message_summary(uid) for uid in self._list_message_ids(query, max_results)]

    def get_recent_messages(self, window_start: str, max_results: int = 25):
        start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("window_start must include a timezone")
        # IMAP SINCE uses server date granularity. Overlap one day to avoid timezone gaps.
        cutoff = (start.astimezone(timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")
        return self._messages(f'SINCE {cutoff} NOT DELETED NOT DRAFT', max_results)

    def get_unread_messages(self, days: int = 1, max_results: int = 25):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        return self._messages(f'UNSEEN SINCE {cutoff}', max_results)

    def get_important_messages(self, days: int = 1, max_results: int = 25):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        return self._messages(f'FLAGGED SINCE {cutoff}', max_results)

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
        self._disconnect_imap()
