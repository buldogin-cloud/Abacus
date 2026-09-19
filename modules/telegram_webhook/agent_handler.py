"""Обробник повідомлень через Abacus LLM з реальними даними з Google Sheets."""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Додаємо корінь проєкту в path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

SYSTEM_PROMPT_TEMPLATE = """Ти — особистий асистент-секретар Андрія.
Андрій — лікар, завідувач ВАІТ (відділення анестезіології та інтенсивної терапії) у ВОКЛ (Волинська обласна клінічна лікарня).

Відповідай ВИКЛЮЧНО українською мовою. Будь лаконічним та конкретним.

=== АКТУАЛЬНІ ДАНІ (оновлено {timestamp}) ===

{context}

=== КІНЕЦЬ ДАНИХ ===

Відповідай на запитання використовуючи ці актуальні дані. Якщо запитують про завдання — відповідай конкретно з даних вище."""


def _load_tasks_context() -> str:
    """Завантажити завдання з Google Sheets."""
    try:
        from modules.task_manager.tasks import TaskManager
        tm = TaskManager()
        overdue = tm.get_overdue_tasks()
        open_tasks = tm.get_open_tasks()

        lines = []

        if overdue:
            lines.append(f"⚠️ ПРОСТРОЧЕНІ ЗАВДАННЯ ({len(overdue)}):")
            for t in overdue:
                deadline = t.get('Строк', 'не вказано')
                title = t.get('Назва', '?')
                priority = t.get('Пріоритет', '')
                assignee = t.get('Виконавець / підрозділ', '')
                lines.append(f"  • {priority} {title} — строк: {deadline} ({assignee})")

        if open_tasks:
            lines.append(f"\n📋 ВІДКРИТІ ЗАВДАННЯ ({len(open_tasks)}):")
            for t in open_tasks[:10]:  # Топ 10
                deadline = t.get('Строк', 'не вказано')
                title = t.get('Назва', '?')
                priority = t.get('Пріоритет', '')
                status = t.get('Статус', '')
                lines.append(f"  • {priority} {title} — {status} (строк: {deadline})")

        return "\n".join(lines) if lines else "Завдань не знайдено."

    except Exception as e:
        logger.warning(f"Не вдалося завантажити завдання: {e}")
        return f"[Завдання тимчасово недоступні: {e}]"


def _load_last_briefing() -> str:
    """Завантажити останній брифінг."""
    try:
        briefings_dir = Path(__file__).resolve().parent.parent.parent / "briefings"
        files = sorted(briefings_dir.glob("briefing_*.md"), reverse=True)
        if not files:
            return "[Брифінгів не знайдено]"
        content = files[0].read_text(encoding='utf-8')
        # Обрізаємо до 1500 символів
        return content[:1500] + ("..." if len(content) > 1500 else "")
    except Exception as e:
        return f"[Брифінг тимчасово недоступний: {e}]"


def get_agent_response(message_text: str, user_id: int, chat_id: int) -> str:
    """Отримати відповідь від AI агента з реальним контекстом."""
    if not message_text or not message_text.strip():
        return "Не розібрав повідомлення, спробуйте ще раз."

    try:
        import abacusai
        api_key = os.environ.get('ABACUS_API_KEY', '')
        if not api_key:
            logger.warning("ABACUS_API_KEY не встановлено")
            return _fallback_response(message_text)

        # Завантажуємо реальні дані
        logger.info("Завантаження завдань з Google Sheets...")
        tasks_context = _load_tasks_context()

        logger.info("Завантаження останнього брифінгу...")
        briefing_context = _load_last_briefing()

        # Формуємо повний контекст
        full_context = f"--- ЗАВДАННЯ ---\n{tasks_context}\n\n--- ОСТАННІЙ БРИФІНГ ---\n{briefing_context}"

        system_message = SYSTEM_PROMPT_TEMPLATE.format(
            timestamp=datetime.now().strftime("%d.%m.%Y %H:%M"),
            context=full_context
        )

        client = abacusai.ApiClient(api_key=api_key)
        response = client.evaluate_prompt(
            system_message=system_message,
            messages=[{"role": "user", "content": message_text}],
            llm_name="CLAUDE_V3_5_SONNET",
            max_tokens=800,
            temperature=0.5
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
        return "✅ Отримав ваше повідомлення. Команди: /start /help /status /briefing"
