"""Обробник повідомлень через Abacus Agent."""

import os
import json
from typing import Optional


def get_agent_response(message_text: str, user_id: int, chat_id: int) -> str:
    """
    Отримати відповідь від агента Abacus на основі повідомлення користувача.
    
    Args:
        message_text: Текст повідомлення від користувача
        user_id: ID користувача в Telegram
        chat_id: ID чату
        
    Returns:
        Текст відповіді від агента
    """
    
    # На даний момент — простий эхо-відповід
    # Пізніше можна інтегрувати з повноцінним Abacus API
    
    if not message_text or not message_text.strip():
        return "Вибачте, я не розібрав ваше повідомлення. Спробуйте ще раз."
    
    # Проста логіка відповіді
    text_lower = message_text.lower()
    
    if any(word in text_lower for word in ['привіт', 'привет', 'hi', 'hello']):
        return "👋 Привіт! Я ваш особистий асистент. Чим я можу вам допомогти?"
    
    elif any(word in text_lower for word in ['дякую', 'спасибо', 'thanks']):
        return "Завжди раді допомогти! 😊"
    
    elif any(word in text_lower for word in ['поточні завдання', 'задачи', 'tasks', 'що робити']):
        return "🔍 Перевіряю ваші завдання... (Функція буде розширена)"
    
    elif any(word in text_lower for word in ['коли буде брифінг', 'брифинг', 'briefing']):
        return "📋 Брифінг генерується щодня о 06:00 за Київським часом. Наступний буде завтра."
    
    elif any(word in text_lower for word in ['допомога', 'помощь', 'help', 'що ти можеш']):
        return (
            "📚 Я можу:\n"
            "• Показати ваші завдання та прострочені пункти\n"
            "• Нагадати про важливі дати в календарі\n"
            "• Проаналізувати нові листи та повідомлення\n"
            "• Допомогти з організацією роботи\n\n"
            "Просто розповідайте мені чого вам потрібно!"
        )
    
    else:
        # Стандартна відповідь
        return (
            f"✅ Я отримав ваше повідомлення: \"{message_text}\"\n\n"
            "Детальна обробка запиту буде зроблена найближчим часом. "
            "Напишіть /help для списку команд."
        )


def format_telegram_response(text: str) -> dict:
    """Форматувати відповідь для надсилання в Telegram."""
    return {
        "text": text,
        "parse_mode": "Markdown"
    }
