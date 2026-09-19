"""Flask сервер для Telegram webhook."""

import os
import sys
import json
import logging
import requests
from flask import Flask, request, jsonify

# Додаємо батьківську директорію в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from modules.telegram_webhook.agent_handler import get_agent_response

# Налаштування Flask
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get('PORT', 8443))

# Отримуємо токен — перевіряємо обидва варіанти назви
BOT_TOKEN = (
    os.environ.get('TELEGRAM_BOT_TOKEN') or
    os.environ.get('BOT_TOKEN') or
    ''
)
CHAT_ID = int(os.environ.get('DEFAULT_CHAT_ID', '5456389264'))
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(chat_id: int, text: str) -> bool:
    """Надіслати повідомлення в Telegram."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не встановлено!")
        return False
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
        data = resp.json()
        if not data.get('ok'):
            logger.error(f"Telegram API помилка: {data.get('description')}")
            return False
        logger.info(f"✅ Повідомлення надіслано в чат {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Помилка надсилання: {e}")
        return False


@app.route('/webhook', methods=['POST'])
def webhook():
    """Обробник webhook від Telegram."""
    try:
        update = request.get_json(force=True)
        logger.info(f"📥 Update ID: {update.get('update_id')}")

        message = update.get('message', {})
        if not message:
            return jsonify({"ok": True})

        chat_id = message['chat']['id']
        user_name = message.get('from', {}).get('first_name', '')
        
        # Перевіряємо чи це голосове повідомлення
        if 'voice' in message:
            voice = message['voice']
            file_id = voice['file_id']
            duration = voice.get('duration', 0)
            logger.info(f"🎤 Голосове від {user_name}: {duration}с")
            
            # Транскрибуємо голосове повідомлення
            from modules.telegram_webhook.voice_handler import transcribe_voice_message
            text = transcribe_voice_message(file_id, BOT_TOKEN)
            
            if not text:
                send_message(chat_id, "❌ Не вдалося розпізнати голосове повідомлення. Спробуйте ще раз або напишіть текстом.")
                return jsonify({"ok": True})
            
            # Повідомляємо що розпізнали
            send_message(chat_id, f"🎤 Розпізнано: \"{text}\"")
            logger.info(f"📝 Транскрипція: {text}")
        else:
            text = message.get('text', '').strip()
            logger.info(f"📨 Від {user_name}: {text[:80]}")

        # Обробляємо команди
        if text.startswith('/'):
            if text == '/start':
                reply = (
                    "👋 Привіт, Андрію!\n\n"
                    "Я ваш особистий асистент Abacus.\n\n"
                    "Доступні команди:\n"
                    "/help — довідка\n"
                    "/status — статус системи\n"
                    "/briefing — інформація про брифінг\n\n"
                    "💬 Напишіть текстом або 🎤 надішліть голосове повідомлення!"
                )
            elif text == '/help':
                reply = (
                    "ℹ️ Команди:\n"
                    "/start — Початок\n"
                    "/status — Статус системи\n"
                    "/briefing — Про щоденний брифінг\n\n"
                    "💬 Можете писати текстом або 🎤 надіслати голосове повідомлення — я його розпізнаю і відповім!"
                )
            elif text == '/status':
                reply = "✅ Система працює!\n\nWebhook: активний\nBrief: щодня о 06:00 Kyiv\nEmail: andrewbelin6@gmail.com"
            elif text == '/briefing':
                reply = "📋 Щоденний брифінг генерується о 06:00 за Київським часом і надходить на andrewbelin6@gmail.com та зберігається в Google Drive."
            else:
                reply = f"❓ Невідома команда. Введіть /help"
        else:
            reply = get_agent_response(text, message.get('from', {}).get('id', 0), chat_id)

        send_message(chat_id, reply)
        return jsonify({"ok": True})

    except Exception as e:
        logger.error(f"❌ Помилка: {e}", exc_info=True)
        return jsonify({"ok": True})  # Завжди повертаємо 200 Telegram


@app.route('/send-test', methods=['GET'])
def send_test():
    """Debug: надіслати тестове повідомлення."""
    ok = send_message(CHAT_ID, "🔧 Тест з Render сервера — сервер надсилає повідомлення!")
    token_set = bool(BOT_TOKEN)
    return jsonify({
        "token_set": token_set,
        "token_prefix": BOT_TOKEN[:10] + "..." if BOT_TOKEN else "EMPTY",
        "message_sent": ok,
        "chat_id": CHAT_ID
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "token_set": bool(BOT_TOKEN)})


@app.route('/', methods=['GET'])
def index():
    return jsonify({"service": "Abacus Telegram Webhook", "version": "1.1"})


if __name__ == '__main__':
    logger.info(f"🤖 Запуск на порту {PORT} | Token set: {bool(BOT_TOKEN)}")
    app.run(host='0.0.0.0', port=PORT, debug=False)


def _setup_google_credentials():
    """Записати Google credentials з env variables на диск якщо є."""
    import json
    base = Path(__file__).resolve().parent.parent.parent

    google_token = os.environ.get('GOOGLE_TOKEN', '')
    google_creds = os.environ.get('GOOGLE_CREDENTIALS', '')

    if google_token:
        try:
            token_path = base / 'token.json'
            token_path.write_text(google_token)
            logger.info(f"✅ token.json записано ({len(google_token)} символів)")
        except Exception as e:
            logger.error(f"Помилка запису token.json: {e}")

    if google_creds:
        try:
            creds_path = base / 'credentials.json'
            creds_path.write_text(google_creds)
            logger.info(f"✅ credentials.json записано ({len(google_creds)} символів)")
        except Exception as e:
            logger.error(f"Помилка запису credentials.json: {e}")


# Налаштовуємо Google credentials при старті
from pathlib import Path
_setup_google_credentials()
