"""Обробник повідомлень через Abacus LLM з реальними даними з Google Sheets (REST API)."""

import os
import sys
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, date
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Корінь проєкту
_ROOT = Path(__file__).resolve().parent.parent.parent

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '11oqxqRm7cAH6jpKf9T7CYtIY0j01LAV259F7NGkd5so')
SHEET_NAME = 'Реєстр'

SYSTEM_PROMPT_TEMPLATE = """Ти — особистий асистент-секретар Андрія.
Андрій — лікар, завідувач ВАІТ (відділення анестезіології та інтенсивної терапії) у ВОКЛ (Волинська обласна клінічна лікарня).

Відповідай ВИКЛЮЧНО українською мовою. Будь лаконічним та конкретним.
При відповіді на запитання про завдання — використовуй ТІЛЬКИ наведені нижче дані.

=== АКТУАЛЬНІ ДАНІ (оновлено {timestamp}) ===

{context}

=== КІНЕЦЬ ДАНИХ ==="""


# --------------------------------------------------------------------------- #
# Google OAuth: отримання свіжого access_token через refresh_token             #
# --------------------------------------------------------------------------- #

def _get_access_token() -> str | None:
    """Отримати свіжий access_token через refresh_token з GOOGLE_TOKEN env var.

    Не використовує google-auth бібліотеки — тільки прямий HTTP запит.
    """
    raw = os.environ.get('GOOGLE_TOKEN', '')
    if not raw:
        logger.warning("GOOGLE_TOKEN не встановлено в env")
        return None

    try:
        token_data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"GOOGLE_TOKEN містить невалідний JSON: {e}")
        return None

    refresh_token = token_data.get('refresh_token')
    client_id = token_data.get('client_id')
    client_secret = token_data.get('client_secret')

    if not refresh_token or not client_id or not client_secret:
        logger.error("GOOGLE_TOKEN не містить refresh_token / client_id / client_secret")
        return None

    try:
        resp = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"Помилка refresh token: {resp.status_code} {resp.text[:200]}")
            return None
        return resp.json().get('access_token')
    except Exception as e:
        logger.error(f"Виключення при refresh token: {e}")
        return None


# --------------------------------------------------------------------------- #
# Завантаження завдань із Google Sheets                                        #
# --------------------------------------------------------------------------- #

def _is_done(status: str) -> bool:
    s = (status or '').lower().strip()
    if not s:
        return False
    if 'частково' in s:
        return False
    return any(k in s for k in ('виконано', 'закрито', 'скасовано', 'завершено'))


def _extract_date(text: str) -> date | None:
    import re
    if not text:
        return None
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', text)
    if m:
        try:
            return datetime.strptime(m.group(0), '%d.%m.%Y').date()
        except ValueError:
            pass
    return None


def _load_tasks_context() -> str:
    """Завантажити завдання через Google Sheets REST API (без google-auth бібліотек)."""
    access_token = _get_access_token()
    if not access_token:
        return '[Google токен недоступний — перевірте GOOGLE_TOKEN env var на Render]'

    url = (
        f'https://sheets.googleapis.com/v4/spreadsheets/'
        f'{SPREADSHEET_ID}/values/{quote(SHEET_NAME)}'
    )
    headers = {'Authorization': f'Bearer {access_token}'}

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 401:
            return '[Google: помилка авторизації (401) — оновіть GOOGLE_TOKEN на Render]'
        if resp.status_code == 403:
            return '[Google: немає доступу до таблиці (403) — перевірте дозволи]'
        resp.raise_for_status()
    except Exception as e:
        return f'[Помилка з\'єднання з Google Sheets: {e}]'

    data = resp.json()
    values = data.get('values', [])

    if not values or len(values) < 2:
        return 'Таблиця завдань порожня або недоступна.'

    header_row = values[0]
    rows = values[1:]

    today = date.today()
    overdue = []
    open_tasks = []

    for row in rows:
        padded = row + [''] * (len(header_row) - len(row))
        task = dict(zip(header_row, padded))

        title = task.get('Назва', '').strip()
        if not title:
            continue

        status = task.get('Статус', '')
        if _is_done(status):
            continue

        deadline = task.get('Строк', '')
        due_date = _extract_date(deadline)
        is_overdue = ('прострочено' in status.lower()) or (due_date is not None and due_date < today)

        if is_overdue:
            overdue.append(task)
        else:
            open_tasks.append(task)

    lines = []

    if overdue:
        lines.append(f'⚠️ ПРОСТРОЧЕНІ ЗАВДАННЯ ({len(overdue)}):')
        for t in overdue:
            pri = t.get('Пріоритет', '')
            name = t.get('Назва', '?')
            deadline_str = t.get('Строк', 'не вказано')
            assignee = t.get('Виконавець / підрозділ', '')
            status_t = t.get('Статус', '')
            lines.append(f'  • [{pri}] {name} — строк: {deadline_str} | {assignee} | {status_t}')

    if open_tasks:
        lines.append(f'\n📋 ВІДКРИТІ ЗАВДАННЯ ({len(open_tasks)}):')
        for t in open_tasks[:15]:
            pri = t.get('Пріоритет', '')
            name = t.get('Назва', '?')
            deadline_str = t.get('Строк', 'не вказано')
            status_t = t.get('Статус', '')
            lines.append(f'  • [{pri}] {name} — {status_t} (строк: {deadline_str})')

    if not lines:
        return 'Відкритих та прострочених завдань не знайдено — всі завдання завершені.'

    return '\n'.join(lines)


# --------------------------------------------------------------------------- #
# Останній брифінг                                                             #
# --------------------------------------------------------------------------- #

def _load_last_briefing() -> str:
    try:
        briefings_dir = _ROOT / 'briefings'
        files = sorted(briefings_dir.glob('briefing_*.md'), reverse=True)
        if not files:
            return '[Брифінгів ще немає]'
        content = files[0].read_text(encoding='utf-8')
        return content[:2000] + ('...' if len(content) > 2000 else '')
    except Exception as e:
        return f'[Брифінг недоступний: {e}]'


# --------------------------------------------------------------------------- #
# Головна функція                                                               #
# --------------------------------------------------------------------------- #

def get_agent_response(message_text: str, user_id: int, chat_id: int) -> str:
    if not message_text or not message_text.strip():
        return 'Не розібрав повідомлення, спробуйте ще раз.'

    api_key = os.environ.get('ABACUS_API_KEY', '')
    if not api_key:
        return _fallback_response(message_text)

    try:
        logger.info('Завантаження завдань з Google Sheets REST API...')
        tasks_context = _load_tasks_context()
        logger.info(f'Завдання: {tasks_context[:80]}')

        logger.info('Завантаження останнього брифінгу...')
        briefing_context = _load_last_briefing()

        full_context = (
            '--- ЗАВДАННЯ З РЕЄСТРУ ---\n'
            f'{tasks_context}\n\n'
            '--- ОСТАННІЙ БРИФІНГ ---\n'
            f'{briefing_context}'
        )

        system_message = SYSTEM_PROMPT_TEMPLATE.format(
            timestamp=datetime.now().strftime('%d.%m.%Y %H:%M'),
            context=full_context,
        )

        import abacusai
        client = abacusai.ApiClient(api_key=api_key)
        response = client.evaluate_prompt(
            system_message=system_message,
            messages=[{'role': 'user', 'content': message_text}],
            llm_name='CLAUDE_V3_5_SONNET',
            max_tokens=800,
            temperature=0.5,
        )
        return response.content or _fallback_response(message_text)

    except Exception as e:
        logger.error(f'LLM помилка: {e}', exc_info=True)
        return _fallback_response(message_text)


def _fallback_response(text: str) -> str:
    t = text.lower().strip()
    if any(w in t for w in ['привіт', 'hello', 'добрий']):
        return '👋 Привіт, Андрію!'
    elif 'статус' in t:
        return '✅ Система працює!\n⏰ Брифінг: щодня о 06:00\n📧 Email: підключено'
    elif 'брифінг' in t:
        return '📋 Брифінг формується щодня о 06:00 і надходить на andrewbelin6@gmail.com'
    elif 'дяку' in t:
        return 'Будь ласка! 😊'
    else:
        return '✅ Отримав ваше повідомлення. Команди: /start /help /status /briefing'
