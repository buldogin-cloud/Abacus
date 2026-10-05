"""Інтеграція з Google API.

Функція :func:`get_credentials` управляє авторизацією для сервісів Google.
Використовується модулями, які потребують доступу до Drive, Calendar, Tasks.

ВАЖЛИВО: Gmail більше не використовує OAuth (перейшов на IMAP/SMTP з App Password).
"""

import json
import os
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Шляхи до файлів
REPO_ROOT = Path(__file__).parent.parent
CREDENTIALS_FILE = REPO_ROOT / "credentials.json"
TOKEN_FILE = REPO_ROOT / "token.json"

# Області доступу (scopes) для Google сервісів (БЕЗ Gmail - він тепер використовує IMAP).
# ВАЖЛИВО: цей перелік має точно відповідати scopes в authorize.py.
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.file",  # Лишень файли, створені застосунком
    "https://www.googleapis.com/auth/spreadsheets",
]


def _load_token_from_env(scopes: Iterable[str]) -> Credentials | None:
    """Завантажує токен із змінної оточення GOOGLE_TOKEN (GitHub Actions)."""
    token_json = os.getenv("GOOGLE_TOKEN")
    if not token_json:
        return None
    try:
        info = json.loads(token_json)
        return Credentials.from_authorized_user_info(info, list(scopes))
    except Exception as e:
        print(f"Помилка завантаження токена з GOOGLE_TOKEN: {e}")
        return None


def get_credentials(scopes: Iterable[str] | None = None) -> Credentials:
    """Отримує валідні Google API credentails.

    Порядок пошуку:
    1. Змінна оточення GOOGLE_TOKEN (GitHub Actions)
    2. Локальний файл token.json
    3. Інтерактивна авторизація (для розробки)

    :param scopes: Список областей доступу. За замовчуванням — DEFAULT_SCOPES.
    :return: Об'єкт Credentials для використання з Google API.
    """
    scopes = list(scopes or DEFAULT_SCOPES)

    # 1. Спроба завантажити з環境
    creds = _load_token_from_env(scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        return creds

    # 2. Спроба завантажити з локального файлу
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, scopes)
            if creds.valid:
                return creds
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                return creds
        except Exception as e:
            print(f"Помилка завантаження token.json: {e}")

    # 3. Інтерактивна авторизація (розробка)
    if not CREDENTIALS_FILE.exists():
        raise RuntimeError(
            f"Файл {CREDENTIALS_FILE} не знайдено. "
            "Завантажте OAuth credentials.json з Google Cloud Console."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes)
    creds = flow.run_local_server(port=8765)

    # Зберігаємо для наступного разу
    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())

    return creds
