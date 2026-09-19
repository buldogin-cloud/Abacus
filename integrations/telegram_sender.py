"""
Telegram sender для надсилання брифінгів та сповіщень.
Використовує BOT_TOKEN з secrets або env змінних.
"""

import json
import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# Файл де зберігаємо відомі chat_id
CHAT_IDS_FILE = os.path.join(os.path.dirname(__file__), '..', 'chat_ids.json')


def _get_bot_token() -> Optional[str]:
    """Отримати BOT_TOKEN з env або secrets файлу."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if token:
        return token
    try:
        with open(os.path.expanduser('~/.config/abacusai_auth_secrets.json')) as f:
            secrets = json.load(f)
        for key in ['telegram', 'TELEGRAM', 'TELEGRAMBOT']:
            if key in secrets:
                t = secrets[key].get('secrets', {}).get('bot_token', {}).get('value')
                if t:
                    return t
    except Exception:
        pass
    return None


def _load_chat_ids() -> dict:
    """Завантажити відомі chat_id."""
    try:
        if os.path.exists(CHAT_IDS_FILE):
            with open(CHAT_IDS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_chat_id(username: str, chat_id: int):
    """Зберегти chat_id у файл."""
    ids = _load_chat_ids()
    ids[username] = chat_id
    ids['_last'] = chat_id  # завжди зберігаємо останній
    with open(CHAT_IDS_FILE, 'w') as f:
        json.dump(ids, f, indent=2)


def get_owner_chat_id() -> Optional[int]:
    """
    Отримати chat_id власника.
    Порядок: 1) env 2) chat_ids.json 3) getUpdates (якщо немає webhook)
    """
    # 1. З env (для GitHub Actions)
    env_id = os.environ.get('TELEGRAM_CHAT_ID')
    if env_id:
        return int(env_id)

    # 2. З файлу
    ids = _load_chat_ids()
    if '_last' in ids:
        return ids['_last']

    return None


def send_briefing(text: str, chat_id: Optional[int] = None) -> bool:
    """
    Надіслати брифінг у Telegram.

    Telegram має ліміт 4096 символів — довгі брифінги ріжемо на частини.

    Args:
        text: Текст брифінгу (Markdown)
        chat_id: ID чату. Якщо None — береться з get_owner_chat_id()

    Returns:
        True якщо успішно, False якщо помилка
    """
    token = _get_bot_token()
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN не знайдено")
        return False

    if chat_id is None:
        chat_id = get_owner_chat_id()

    if not chat_id:
        logger.error("❌ TELEGRAM_CHAT_ID невідомий. Напишіть /start боту @AbacusSECbot")
        return False

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    MAX_LEN = 4000  # запас до ліміту 4096

    # Ріжемо на частини якщо потрібно
    parts = _split_message(text, MAX_LEN)
    success = True

    for i, part in enumerate(parts, 1):
        suffix = f"\n\n_Частина {i}/{len(parts)}_" if len(parts) > 1 else ""
        payload = {
            'chat_id': chat_id,
            'text': part + suffix,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        }
        try:
            r = requests.post(api_url, json=payload, timeout=15)
            if r.status_code == 200:
                logger.info(f"✅ Telegram: частина {i}/{len(parts)} надіслана")
            else:
                # Fallback: без Markdown (якщо є спецсимволи що ламають парсинг)
                payload['parse_mode'] = ''
                r2 = requests.post(api_url, json=payload, timeout=15)
                if r2.status_code == 200:
                    logger.info(f"✅ Telegram: частина {i}/{len(parts)} надіслана (plain)")
                else:
                    logger.error(f"❌ Telegram API: {r2.status_code} {r2.text[:200]}")
                    success = False
        except Exception as e:
            logger.error(f"❌ Помилка Telegram: {e}")
            success = False

    return success


def _split_message(text: str, max_len: int) -> list:
    """Розбити довгий текст по межах рядків."""
    if len(text) <= max_len:
        return [text]

    parts = []
    while len(text) > max_len:
        # Шукаємо найближчий перенос рядка перед межею
        cut = text.rfind('\n', 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip('\n')
    if text:
        parts.append(text)
    return parts


def send_notification(message: str, chat_id: Optional[int] = None) -> bool:
    """Надіслати коротке сповіщення (не брифінг)."""
    return send_briefing(message, chat_id)
