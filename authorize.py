"""
Простий скрипт авторизації Google OAuth
Не потребує браузера — просто скопіюйте посилання вручну
"""
import os
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'  # дозволяє зміну порядку scopes
from google_auth_oauthlib.flow import InstalledAppFlow
import json

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets',
]

# Шлях до credentials.json
creds_file = os.path.join(os.path.dirname(__file__), 'credentials.json')

print("=" * 60)
print("  Command Center — Авторизація Google")
print("=" * 60)
print()

flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'

auth_url, _ = flow.authorization_url(
    access_type='offline',
    include_granted_scopes='true',
    prompt='consent'
)

print("📋 Крок 1: Відкрийте це посилання у браузері:")
print()
print(auth_url)
print()
print("📋 Крок 2: Виберіть акаунт andrewbelin6@gmail.com")
print("         ⚠️  ВАЖЛИВО: на екрані дозволів ПОСТАВТЕ ВСІ ГАЛОЧКИ")
print("            (Gmail, Календар, Таблиці, Диск) або натисніть")
print("            'Вибрати все' / 'Select all', інакше частина")
print("            функцій НЕ працюватиме!")
print("         → Натисніть 'Продовжити' / 'Continue'")
print("         → Скопіюйте код який з'явиться")
print()

code = input("📋 Крок 3: Вставте код сюди і натисніть Enter: ").strip()

flow.fetch_token(code=code)
creds = flow.credentials

token_data = {
    'token': creds.token,
    'refresh_token': creds.refresh_token,
    'token_uri': creds.token_uri,
    'client_id': creds.client_id,
    'client_secret': creds.client_secret,
    'scopes': list(creds.scopes),
}

token_path = os.path.join(os.path.dirname(__file__), 'token.json')
with open(token_path, 'w') as f:
    json.dump(token_data, f, indent=2)

print()
print("=" * 60)
print("✅ Авторизація успішна!")
print(f"✅ Файл token.json збережено: {token_path}")
print("=" * 60)
print()
print("📤 Наступний крок:")
print("   Завантажте файл token.json і відправте його в чат Abacus AI")
