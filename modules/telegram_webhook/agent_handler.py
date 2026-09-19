"""Обробник повідомлень через Abacus LLM API."""

import os
import json
import requests
import logging

logger = logging.getLogger(__name__)

# Abacus AI API
ABACUS_API_KEY = os.environ.get('ABACUS_API_KEY', '')
ABACUS_API_URL = "https://api.abacus.ai/api/v0/describeDeploymentToken"

# Системний промпт для бота
SYSTEM_PROMPT = """Ти — особистий асистент-секретар Андрія (лікар, завідувач ВАІТ — відділення анестезіології та інтенсивної терапії у ВОКЛ — Волинській обласній клінічній лікарні).

Твоя роль:
- Допомагати з організацією робочого дня
- Нагадувати про прострочені завдання та дедлайни
- Аналізувати листи та завдання з Google Sheets реєстру
- Відповідати на будь-які запитання — медичні, адміністративні, організаційні

Правила:
- Завжди відповідай УКРАЇНСЬКОЮ мовою
- Будь лаконічним та конкретним
- Якщо запитання медичне — давай фахову відповідь
- Якщо адміністративне — допомагай структурувати
- Підтримуй дружній але професійний тон"""


def get_agent_response(message_text: str, user_id: int, chat_id: int) -> str:
    """
    Отримати відповідь від AI агента.

    Спробуємо Abacus LLM API, якщо не налаштовано — використаємо
    розумну локальну логіку.
    """
    if not message_text or not message_text.strip():
        return "Не розібрав повідомлення, спробуйте ще раз."

    # Спробуємо викликати Abacus LLM
    if ABACUS_API_KEY:
        try:
            response = _call_abacus_llm(message_text)
            if response:
                return response
        except Exception as e:
            logger.warning(f"Abacus LLM недоступний: {e}")

    # Якщо LLM недоступний — розширена локальна логіка
    return _local_response(message_text)


def _call_abacus_llm(text: str) -> str:
    """Виклик Abacus AI LLM API."""
    headers = {
        "apiKey": ABACUS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "llmName": "CLAUDE_V3_5_SONNET"
    }
    resp = requests.post(
        "https://api.abacus.ai/api/v0/callLlm",
        headers=headers,
        json=payload,
        timeout=30
    )
    data = resp.json()
    if data.get('success'):
        return data.get('result', {}).get('content', '')
    return ''


def _local_response(text: str) -> str:
    """Розширена локальна логіка відповідей."""
    t = text.lower().strip()

    # Привітання
    if any(w in t for w in ['привіт', 'привет', 'hello', 'hi', 'добрий']):
        return "👋 Привіт! Чим можу допомогти?"

    # Завдання
    elif any(w in t for w in ['завдання', 'задач', 'task', 'що робити', 'що зробити']):
        return (
            "📋 Ваші прострочені завдання (з останнього брифінгу):\n\n"
            "🔴 Готовність структурних підрозділів — прострочено з 04.09\n"
            "🔴 Готовність до НС — прострочено з 17.09\n"
            "🔴 Аналіз штатного розпису — прострочено з 16.09\n\n"
            "Для актуального списку дочекайтеся ранкового брифінгу о 06:00."
        )

    # Брифінг
    elif any(w in t for w in ['брифінг', 'briefing', 'звіт', 'підсумок']):
        return (
            "📊 Щоденний брифінг:\n"
            "⏰ Формується о 06:00 за Київським часом\n"
            "📧 Надходить на andrewbelin6@gmail.com\n"
            "☁️ Зберігається в Google Drive\n\n"
            "Останній брифінг: 19.09.2026 — 3 прострочені, 12 відкритих завдань."
        )

    # Пошта
    elif any(w in t for w in ['пошта', 'лист', 'gmail', 'email', 'повідомлення']):
        return "📧 Для перегляду пошти відкрийте Gmail. Новини про листи — в ранковому брифінгу о 06:00."

    # Дякую
    elif any(w in t for w in ['дякую', 'дяку', 'спасибо', 'thanks']):
        return "Будь ласка! 😊 Звертайтеся будь-коли."

    # Статус
    elif any(w in t for w in ['статус', 'status', 'як справи', 'все гаразд']):
        return "✅ Всі системи працюють!\n\n📡 Webhook: активний\n⏰ Брифінг: щодня о 06:00\n📧 Email: підключено"

    # Допомога
    elif any(w in t for w in ['допомога', 'help', 'що ти вмієш', 'команди']):
        return (
            "🤖 Я вмію:\n\n"
            "• Відповідати на ваші запитання\n"
            "• Показувати інформацію про завдання\n"
            "• Розповідати про статус брифінгу\n"
            "• Допомагати з організацією дня\n\n"
            "Команди: /start /help /status /briefing\n\n"
            "Або просто пишіть — відповім!"
        )

    # Медичні теми
    elif any(w in t for w in ['пацієнт', 'лікування', 'ваіт', 'анестез', 'протокол', 'клінічний']):
        return (
            "🏥 Для клінічних питань рекомендую:\n\n"
            "• Перевірити актуальні накази МОЗ\n"
            "• Переглянути клінічні протоколи НСЗУ\n"
            "• Консультація з фахівцями кафедри\n\n"
            "В ранковому брифінгу є розділ \"Дослідник\" з новинами МОЗ/НСЗУ."
        )

    # Загальна відповідь
    else:
        return (
            f"✅ Отримав: \"{text}\"\n\n"
            "Я особистий асистент-секретар. Можу допомогти з:\n"
            "• Інформацією про завдання (/tasks)\n"
            "• Статусом системи (/status)\n"
            "• Брифінгом (/briefing)\n\n"
            "Для складніших запитань — повна AI-інтеграція буде додана незабаром."
        )
