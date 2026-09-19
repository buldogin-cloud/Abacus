"""Обробка голосових повідомлень Telegram через OpenAI Whisper."""

import os
import io
import logging
import requests

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')


def transcribe_voice_message(file_id: str, bot_token: str) -> str | None:
    """
    Транскрибувати голосове повідомлення з Telegram через OpenAI Whisper.

    Args:
        file_id: ID файлу голосового повідомлення в Telegram
        bot_token: Токен Telegram бота

    Returns:
        Розпізнаний текст або None якщо помилка
    """
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY не встановлено")
        return None

    try:
        # Крок 1: Отримати шлях до файлу від Telegram
        file_info_url = f"https://api.telegram.org/bot{bot_token}/getFile"
        file_resp = requests.get(file_info_url, params={'file_id': file_id}, timeout=10)

        if file_resp.status_code != 200:
            logger.error(f"Telegram getFile помилка: {file_resp.status_code}")
            return None

        file_data = file_resp.json()
        if not file_data.get('ok'):
            logger.error(f"Telegram API: {file_data.get('description')}")
            return None

        file_path = file_data['result']['file_path']

        # Крок 2: Завантажити голосовий файл (.oga / opus)
        file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        voice_resp = requests.get(file_url, timeout=30)

        if voice_resp.status_code != 200:
            logger.error(f"Завантаження файлу помилка: {voice_resp.status_code}")
            return None

        audio_bytes = voice_resp.content
        logger.info(f"🎵 Завантажено {len(audio_bytes)} байт аудіо")

        # Крок 3: Надіслати до OpenAI Whisper
        # Whisper приймає формати: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg
        # Telegram voice = .oga (OGG Opus) — Whisper це розуміє
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = 'voice.ogg'

        whisper_url = 'https://api.openai.com/v1/audio/transcriptions'
        headers = {
            'Authorization': f'Bearer {OPENAI_API_KEY}',
        }
        files = {
            'file': ('voice.ogg', audio_file, 'audio/ogg'),
        }
        data = {
            'model': 'whisper-1',
            'language': 'uk',  # Українська (покращує точність)
        }

        whisper_resp = requests.post(
            whisper_url,
            headers=headers,
            files=files,
            data=data,
            timeout=90,
        )

        if whisper_resp.status_code != 200:
            logger.error(f"Whisper помилка: {whisper_resp.status_code} {whisper_resp.text[:200]}")
            return None

        result = whisper_resp.json()
        transcript = result.get('text', '').strip()

        if transcript:
            logger.info(f"✅ Транскрипція успішна: {transcript[:60]}")
            return transcript
        else:
            logger.warning("Whisper повернув порожній текст")
            return None

    except Exception as e:
        logger.error(f"Помилка транскрипції: {e}", exc_info=True)
        return None
