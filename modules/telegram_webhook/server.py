"""Flask сервер для Telegram webhook."""

import os
import sys
import json
import logging
from flask import Flask, request, jsonify

# Додаємо батьківську директорію в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from modules.telegram_webhook.webhook import handle_webhook_request

# Налаштування Flask
app = Flask(__name__)

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Webhook маршрут
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv('PORT', 8443))


@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    """Обробник webhook запиту від Telegram."""
    try:
        # Отримуємо тіло запиту
        body = request.get_data(as_text=True)
        logger.info(f"📥 POST {WEBHOOK_PATH}")
        
        # Обробляємо
        status_code, response_text = handle_webhook_request(body)
        
        # Повертаємо відповідь
        return response_text, status_code, {'Content-Type': 'application/json'}
        
    except Exception as e:
        logger.error(f"❌ Помилка на {WEBHOOK_PATH}: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Перевірка здоров'я сервера."""
    return jsonify({"status": "ok", "service": "telegram-webhook"}), 200


@app.route('/', methods=['GET'])
def index():
    """Домашня сторінка."""
    return jsonify({
        "service": "Abacus Telegram Webhook Server",
        "version": "1.0",
        "webhook_path": WEBHOOK_PATH,
        "health": "/health"
    }), 200


@app.errorhandler(404)
def not_found(error):
    """Обробник 404."""
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.errorhandler(500)
def server_error(error):
    """Обробник 500."""
    return jsonify({"ok": False, "error": "Internal server error"}), 500


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🤖 Abacus Telegram Webhook Server")
    logger.info("=" * 60)
    logger.info(f"📡 Webhook запускається на порту {PORT}")
    logger.info(f"📍 Endpoint: http://localhost:{PORT}{WEBHOOK_PATH}")
    logger.info(f"❤️  Health check: http://localhost:{PORT}/health")
    logger.info("=" * 60)
    
    # Запускаємо Flask (з debug=False для production)
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        use_reloader=False
    )
