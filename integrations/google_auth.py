"""Спільна автентифікація Google API (OAuth 2.0).

Модуль надає єдину функцію отримання облікових даних (credentials) для
всіх Google-сервісів: Gmail, Calendar, Sheets. Токени зберігаються у
``token.json`` та автоматично оновлюються.

Порядок пошуку облікових даних:
1. Змінні середовища ``GOOGLE_CREDENTIALS`` / ``GOOGLE_TOKEN`` (для CI).
2. Локальні файли ``credentials.json`` / ``token.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Області доступу (scopes) для всіх сервісів Command Center.
# ВАЖЛИВО: цей перелік має точно відповідати scopes в authorize.py,
# інакше при оновленні токена частина дозволів «губиться» (token.json
# перезаписується лише з переліченими тут scopes).
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Шляхи за замовчуванням (корінь проекту).
_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = os.environ.get("CREDENTIALS_FILE", str(_ROOT / "credentials.json"))
TOKEN_FILE = os.environ.get("TOKEN_FILE", str(_ROOT / "token.json"))


def _load_token_from_env(scopes: Iterable[str]) -> Credentials | None:
    """Завантажує токен зі змінної середовища ``GOOGLE_TOKEN`` (для CI)."""
    raw = os.environ.get("GOOGLE_TOKEN")
    if not raw:
        return None
    info = json.loads(raw)
    return Credentials.from_authorized_user_info(info, list(scopes))


def _write_credentials_from_env() -> None:
    """Записує ``credentials.json`` зі змінної середовища, якщо потрібно (CI)."""
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if raw and not Path(CREDENTIALS_FILE).exists():
        Path(CREDENTIALS_FILE).write_text(raw, encoding="utf-8")


def get_credentials(scopes: Iterable[str] | None = None) -> Credentials:
    """Повертає дійсні облікові дані Google API.

    Якщо збережений токен відсутній або недійсний — ініціює OAuth-потік
    (відкриває браузер у локальному середовищі). У CI очікує наявність
    змінних середовища ``GOOGLE_TOKEN`` / ``GOOGLE_CREDENTIALS``.
    """
    scopes = list(scopes or DEFAULT_SCOPES)
    creds: Credentials | None = None

    # 1. Спроба зчитати токен зі змінної середовища (CI).
    creds = _load_token_from_env(scopes)

    # 2. Спроба зчитати локальний token.json.
    if creds is None and Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, scopes)

    # 3. Оновлення або інтерактивна авторизація.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            _write_credentials_from_env()
            if not Path(CREDENTIALS_FILE).exists():
                raise FileNotFoundError(
                    "Не знайдено credentials.json. Створіть OAuth Client ID у "
                    "Google Cloud Console та збережіть файл у корені проекту, "
                    "або задайте змінну середовища GOOGLE_CREDENTIALS."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes)
            creds = flow.run_local_server(port=0)

        # Зберігаємо оновлений токен для наступних запусків.
        Path(TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")

    return creds
