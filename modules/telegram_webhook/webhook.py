"""Webhook сервер для обробки Telegram повідомлень."""

import json
import os
import sys
import logging
from typing import Dict, Any
import requests

# Додаємо батьківську директорію в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from modules.telegram_webhook.agent_handler import get_agent_response, format_telegram_response

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Отримання Bot Token з оточення або файлу
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    # Спробуємо прочитати з файлу secrets
    try:
        import json
        with open(os.path.expanduser('~/.config/abacusai_auth_secrets.json')) as f:
            secrets = json.load(f)
            for key in ['TELEGRAMBOT', 'telegrambot', 'TELEGRAM', 'telegram']:
                if key in secrets and 'secrets' in secrets[key]:
                    if 'bot_token' in secrets[key]['secrets']:
                        BOT_TOKEN = secrets[key]['secrets']['bot_token']['value']
                        break
    except Exception as e:
        logger.warning(f"Не вдалося прочитати BOT_TOKEN з файлу: {e}")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не знайдено! Встановіть змінну оточення TELEGRAM_BOT_TOKEN")
    sys.exit(1)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


class TelegramWebhookHandler:
    """Обробник webhook запитів від Telegram."""
    
    @staticmethod
    def process_update(update: Dict[str, Any]) -> None:
        """
        Обробити оновлення від Telegram.
        
        Args:
            update: Словник з оновленням від Telegram
        """
        try:
            # Перевіримо чи це повідомлення
            if 'message' not in update:
                logger.debug("Оновлення не містить повідомлення, ігноруємо")
                return
            
            message = update['message']
            chat_id = message['chat']['id']
            user_id = message['from']['id']
            message_id = message.get('message_id')
            text = message.get('text', '').strip()
            
            # Логуємо вхідне повідомлення
            user_name = message['from'].get('first_name', 'Unknown')
            logger.info(f"📨 Від {user_name} (ID:{user_id}): {text[:100]}")
            
            # Перевіримо чи це команда /start або /help
            if text.startswith('/'):
                response_text = TelegramWebhookHandler._handle_command(text, user_id)
            else:
                # Отримуємо відповідь від агента
                response_text = get_agent_response(text, user_id, chat_id)
            
            # Надсилаємо відповідь
            TelegramWebhookHandler._send_message(chat_id, response_text)
            logger.info(f"✅ Відповідь надіслана в чат {chat_id}")
            
        except Exception as e:
            logger.error(f"❌ Помилка обробки оновлення: {e}", exc_info=True)
            # Намагаємось надіслати повідомлення про помилку
            if 'message' in update:
                try:
                    chat_id = update['message']['chat']['id']
                    TelegramWebhookHandler._send_message(
                        chat_id, 
                        "❌ Виникла помилка при обробці вашого запиту. Спробуйте ще раз."
                    )
                except:
                    pass
    
    @staticmethod
    def _handle_command(text: str, user_id: int) -> str:
        """Обробити команду."""
        if text == '/start':
            return (
                "👋 Привіт! Я ваш особистий асистент-секретар.\n\n"
                "Я можу:\n"
                "• 📋 Показати ваші завдання та прострочені пункти\n"
                "• 📅 Нагадати про важливі дати\n"
                "• 📧 Проаналізувати нові листи\n"
                "• 🤖 Допомогти з організацією роботи\n\n"
                "Просто напишіть мені, що вам потрібно!"
            )
        elif text == '/help':
            return (
                "ℹ️ **Доступні команди:**\n"
                "/start - Почати\n"
                "/help - Ця справка\n"
                "/tasks - Показати ваші завдання\n"
                "/briefing - Останній брифінг\n"
                "/status - Статус системи\n\n"
                "Або просто напишіть що завгодно, і я постараюсь допомогти!"
            )
        elif text == '/tasks':
            return "🔍 Завантажую ваші завдання..."
        elif text == '/briefing':
            return "📋 Завантажую останній брифінг..."
        elif text == '/status':
            return "✅ Система працює нормально!"
        else:
            return f"❓ Невідома команда: {text}. Введіть /help для справки."
    
    @staticmethod
    def _send_message(chat_id: int, text: str) -> bool:
        """
        Надіслати повідомлення в Telegram.
        
        Args:
            chat_id: ID чату
            text: Текст повідомлення
            
        Returns:
            True якщо успішно, False інакше
        """
        try:
            payload = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'Markdown'
            }
            
            response = requests.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Помилка надсилання: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Помилка при надсиланні повідомлення: {e}")
            return False


def handle_webhook_request(body: str) -> tuple[int, str]:
    """
    Обробити webhook запит від Telegram.
    
    Args:
        body: JSON тіло запиту
        
    Returns:
        Кортеж (status_code, response_text)
    """
    try:
        logger.info("📥 Отримано webhook запит")
        
        # Парсимо JSON
        update = json.loads(body)
        logger.debug(f"Update: {update.get('update_id')}")
        
        # Обробляємо оновлення
        TelegramWebhookHandler.process_update(update)
        
        # Повертаємо успішну відповідь
        return 200, json.dumps({"ok": True})
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ Помилка парсингу JSON: {e}")
        return 400, json.dumps({"ok": False, "error": "Invalid JSON"})
    except Exception as e:
        logger.error(f"❌ Помилка обробки запиту: {e}", exc_info=True)
        return 500, json.dumps({"ok": False, "error": "Internal server error"})


if __name__ == "__main__":
    # Тестування
    logger.info("Webhook модуль завантажено успішно")
    logger.info(f"Bot Token активний: {bool(BOT_TOKEN)}")
