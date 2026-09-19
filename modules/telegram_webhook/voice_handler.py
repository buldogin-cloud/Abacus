"""Обробка голосових повідомлень Telegram через Google Speech-to-Text."""

import os
import json
import logging
import tempfile
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_access_token() -> str | None:
    """Отримати свіжий Google access_token для Speech-to-Text API."""
    raw = os.environ.get('GOOGLE_TOKEN', '')
    if not raw:
        logger.warning("GOOGLE_TOKEN не встановлено")
        return None

    try:
        token_data = json.loads(raw)
        refresh_token = token_data.get('refresh_token')
        client_id = token_data.get('client_id')
        client_secret = token_data.get('client_secret')

        if not all([refresh_token, client_id, client_secret]):
            logger.error("GOOGLE_TOKEN неповний")
            return None

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
        if resp.status_code == 200:
            return resp.json().get('access_token')
        else:
            logger.error(f"Помилка refresh: {resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"Помилка отримання токена: {e}")
        return None


def transcribe_voice_message(file_id: str, bot_token: str) -> str | None:
    """
    Транскрибувати голосове повідомлення з Telegram через Google Speech-to-Text.
    
    Args:
        file_id: ID файлу голосового повідомлення в Telegram
        bot_token: Токен Telegram бота
        
    Returns:
        Розпізнаний текст або None якщо помилка
    """
    try:
        # Крок 1: Отримати інформацію про файл від Telegram
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
        
        # Крок 2: Завантажити файл .oga (Telegram voice format)
        file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        voice_resp = requests.get(file_url, timeout=30)
        
        if voice_resp.status_code != 200:
            logger.error(f"Завантаження файлу помилка: {voice_resp.status_code}")
            return None
        
        # Крок 3: Зберегти тимчасово
        with tempfile.NamedTemporaryFile(suffix='.oga', delete=False) as tmp_file:
            tmp_file.write(voice_resp.content)
            tmp_path = tmp_file.name
        
        try:
            # Крок 4: Конвертувати .oga → .wav (якщо потрібно) або надіслати як є
            # Google Speech-to-Text підтримує OGG_OPUS
            audio_content = voice_resp.content
            
            # Крок 5: Отримати Google access token
            access_token = _get_access_token()
            if not access_token:
                logger.error("Не вдалося отримати Google token")
                return None
            
            # Крок 6: Викликати Google Speech-to-Text REST API
            import base64
            audio_base64 = base64.b64encode(audio_content).decode('utf-8')
            
            speech_url = 'https://speech.googleapis.com/v1/speech:recognize'
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            }
            
            payload = {
                'config': {
                    'encoding': 'OGG_OPUS',
                    'sampleRateHertz': 48000,  # Telegram voice стандарт
                    'languageCode': 'uk-UA',  # Українська
                    'alternativeLanguageCodes': ['ru-RU', 'en-US'],  # Альтернативи
                    'enableAutomaticPunctuation': True,
                },
                'audio': {
                    'content': audio_base64
                }
            }
            
            speech_resp = requests.post(speech_url, headers=headers, json=payload, timeout=60)
            
            if speech_resp.status_code != 200:
                logger.error(f"Google Speech API помилка: {speech_resp.status_code} {speech_resp.text[:200]}")
                return None
            
            result = speech_resp.json()
            
            # Крок 7: Витягти текст з відповіді
            if 'results' in result and len(result['results']) > 0:
                transcript = result['results'][0]['alternatives'][0]['transcript']
                logger.info(f"✅ Транскрипція успішна: {transcript[:50]}...")
                return transcript
            else:
                logger.warning("Розпізнавання не дало результатів")
                return None
                
        finally:
            # Видалити тимчасовий файл
            try:
                os.unlink(tmp_path)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Помилка транскрипції: {e}", exc_info=True)
        return None
