"""Обробник повідомлень через Abacus LLM API (evaluate_prompt)."""

import os
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ти — особистий асистент-секретар Андрія. 
Андрій — лікар, завідувач ВАІТ (відділення анестезіології та інтенсивної терапії) у ВОКЛ (Волинська обласна клінічна лікарня).

Твоя роль:
- Допомагати з організацією робочого дня
- Відповідати на будь-які запитання — медичні, адміністративні, організаційні
- Нагадувати про завдання та дедлайни
- Щоденний брифінг формується о 06:00 і надходить на email та в Google Drive

Правила:
- Відповідай ВИКЛЮЧНО українською мовою
- Будь лаконічним та конкретним
- Підтримуй дружній але професійний тон"""


def get_agent_response(message_text: str, user_id: int, chat_id: int) -> str:
    """Отримати відповідь від AI агента через Abacus LLM."""
    if not message_text or not message_text.strip():
        return "Не розібрав повідомлення, спробуйте ще раз."

    try:
        import abacusai
        api_key = os.environ.get('ABACUS_API_KEY', '')
        if not api_key:
            logger.warning("ABACUS_API_KEY не встановлено, використовую шаблонні відповіді")
            return _fallback_response(message_text)

        client = abacusai.ApiClient(api_key=api_key)
        response = client.evaluate_prompt(
            system_message=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message_text}],
            llm_name="CLAUDE_V3_5_SONNET",
            max_tokens=800,
            temperature=0.7
        )
        return response.content or _fallback_response(message_text)

    except Exception as e:
        logger.error(f"LLM помилка: {e}")
        return _fallback_response(message_text)


def _fallback_response(text: str) -> str:
    """Резервна відповідь якщо LLM недоступний."""
    t = text.lower().strip()
    if any(w in t for w in ['привіт', 'hello', 'добрий']):
        return "👋 Привіт! Чим можу допомогти?"
    elif any(w in t for w in ['статус', 'status']):
        return "✅ Система працює!\n⏰ Брифінг: щодня о 06:00\n📧 Email: підключено"
    elif any(w in t for w in ['брифінг', 'briefing']):
        return "📋 Брифінг формується щодня о 06:00 і надходить на andrewbelin6@gmail.com"
    elif any(w in t for w in ['дякую', 'дяку']):
        return "Будь ласка! 😊"
    else:
        return (
            f"✅ Отримав ваше повідомлення.\n\n"
            "Команди: /start /help /status /briefing\n"
            "AI відповіді тимчасово недоступні — ABACUS_API_KEY не встановлено на сервері."
        )
