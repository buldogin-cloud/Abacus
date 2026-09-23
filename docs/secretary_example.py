#!/usr/bin/env python3
"""
Приклад скрипту для ChatGPT Secretary
Демонструє, як читати та обробляти handoff файли від Abacus

Використання:
    python docs/secretary_example.py --date 2026-09-23
    python docs/secretary_example.py  # сьогоднішня дата
"""

import json
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Додати integrations до path
sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.drive_client import DriveClient
from modules.task_manager.tasks import TaskManager


# Константи
SECRETARY_FOLDER_ID = "1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj"
TASK_REGISTRY_SHEET_ID = "11oqxqRm7cAH6jpKf9T7CYtIY0j01LAV259F7NGkd5so"


def read_handoff_file(date_str: str) -> list:
    """
    Прочитати handoff файл з Drive
    
    Args:
        date_str: Дата у форматі YYYY-MM-DD
        
    Returns:
        Список handoff записів (dict)
    """
    dc = DriveClient()
    filename = f"handoff_{date_str}.jsonl"
    
    print(f"[→] Читання handoff файлу: {filename}")
    
    # Знайти файл
    handoff_file = dc.find_file(filename, parent_id=SECRETARY_FOLDER_ID)
    
    if not handoff_file:
        print(f"❌ Файл {filename} не знайдено на Drive")
        print(f"   Папка: Command Center/handoffs/secretary/")
        print(f"   Можливо, Abacus ще не запустився сьогодні?")
        return []
    
    print(f"[✓] Файл знайдено: {handoff_file['id']}")
    print(f"    Оновлено: {handoff_file['modifiedTime']}")
    
    # Прочитати вміст
    content = dc.read_file(handoff_file['id'])
    
    if not content:
        print("❌ Не вдалося прочитати вміст файлу")
        return []
    
    # Розпарсити JSONL
    lines = content.strip().split('\n')
    handoffs = []
    
    for i, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
            handoffs.append(record)
        except json.JSONDecodeError as e:
            print(f"⚠️ Помилка парсингу рядка {i}: {e}")
            continue
    
    print(f"[✓] Прочитано {len(handoffs)} handoff записів\n")
    return handoffs


def analyze_handoffs(handoffs: list):
    """
    Аналіз та статистика handoffs
    """
    if not handoffs:
        return
    
    # Статистика по джерелах
    sources = {}
    classifications = {}
    priorities = 0
    
    for h in handoffs:
        src = h.get('source', 'unknown')
        cls = h.get('classification', 'unknown')
        
        sources[src] = sources.get(src, 0) + 1
        classifications[cls] = classifications.get(cls, 0) + 1
        
        if h.get('is_priority'):
            priorities += 1
    
    print("="*60)
    print("  📊 СТАТИСТИКА HANDOFF")
    print("="*60)
    print(f"Всього записів: {len(handoffs)}")
    print(f"Пріоритетні: {priorities}")
    print(f"\nДжерела:")
    for src, count in sources.items():
        print(f"  • {src}: {count}")
    print(f"\nКласифікація:")
    for cls, count in classifications.items():
        print(f"  • {cls}: {count}")
    print("="*60 + "\n")


def process_new_tasks(handoffs: list, dry_run: bool = True):
    """
    Обробка new_task записів
    
    Args:
        handoffs: Список handoff записів
        dry_run: Якщо True, не додавати в реєстр (тільки показати)
    """
    new_tasks = [h for h in handoffs if h['classification'] == 'new_task']
    
    if not new_tasks:
        print("[i] Немає нових задач (new_task)\n")
        return
    
    print(f"[→] Обробка {len(new_tasks)} нових задач...\n")
    
    if not dry_run:
        tm = TaskManager(TASK_REGISTRY_SHEET_ID, "Завдання")
    
    for i, h in enumerate(new_tasks, 1):
        print(f"{i}. {h['title']}")
        print(f"   Категорія: {h['category']}")
        print(f"   Джерело: {h['source']} ({h['source_id']})")
        print(f"   Дедлайн: {h.get('explicit_deadline', 'не вказано')}")
        print(f"   Пріоритет: {'🔴 ТАК' if h['is_priority'] else 'ні'}")
        
        if h.get('related_task_candidate'):
            print(f"   ⚠️ Можливий дублікат задачі: {h['related_task_candidate']}")
        
        print(f"   Рекомендація: {h.get('suggested_check', 'N/A')}")
        
        if dry_run:
            print(f"   [DRY RUN] Не додано в реєстр")
        else:
            # Реально додати задачу
            try:
                tm.add_task(
                    task_text=h['title'],
                    assignee="TBD",  # Secretary вирішує
                    deadline=h.get('explicit_deadline') or "TBD",
                    status="Нова",
                    notes=f"Джерело: {h['source']}, ID: {h['source_id']}, Категорія: {h['category']}"
                )
                print(f"   ✅ Додано в реєстр завдань")
            except Exception as e:
                print(f"   ❌ Помилка: {e}")
        
        print()


def process_updates(handoffs: list, dry_run: bool = True):
    """
    Обробка update_existing записів
    """
    updates = [h for h in handoffs if h['classification'] == 'update_existing']
    
    if not updates:
        print("[i] Немає оновлень існуючих задач (update_existing)\n")
        return
    
    print(f"[→] Обробка {len(updates)} оновлень...\n")
    
    for i, h in enumerate(updates, 1):
        print(f"{i}. {h['title']}")
        print(f"   Пов'язана задача: {h.get('related_task_candidate', 'N/A')}")
        print(f"   Джерело: {h['source']}")
        
        if dry_run:
            print(f"   [DRY RUN] Не оновлено в реєстрі")
        else:
            print(f"   [TODO] Реалізувати update_task_status()")
        
        print()


def process_reviews(handoffs: list):
    """
    Показати записи, що потребують ручної перевірки
    """
    reviews = [h for h in handoffs if h['classification'] == 'needs_review']
    
    if not reviews:
        print("[i] Немає записів для ручної перевірки (needs_review)\n")
        return
    
    print(f"[→] ⚠️ {len(reviews)} записів потребують ручної перевірки:\n")
    
    for i, h in enumerate(reviews, 1):
        print(f"{i}. {h['title']}")
        print(f"   Категорія: {h['category']}")
        print(f"   Причина: {h.get('reason_for_handoff', 'N/A')}")
        print(f"   Confidence: {h.get('confidence', 'N/A')}")
        print()


def generate_briefing_draft(handoffs: list):
    """
    Згенерувати чернетку daily briefing
    """
    if not handoffs:
        print("[i] Немає даних для брифінгу\n")
        return
    
    date_str = handoffs[0].get('produced_at', '')[:10]
    run_id = handoffs[0].get('run_id', 'unknown')
    
    # Розділити по категоріях
    new_tasks = [h for h in handoffs if h['classification'] == 'new_task']
    updates = [h for h in handoffs if h['classification'] == 'update_existing']
    info = [h for h in handoffs if h['classification'] == 'info_only']
    reviews = [h for h in handoffs if h['classification'] == 'needs_review']
    priorities = [h for h in handoffs if h.get('is_priority')]
    
    # Формувати Markdown
    briefing = f"""# Daily Briefing — {date_str}

**Згенеровано**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  
**Джерело**: Abacus Handoff (run: {run_id})

---

## 🔴 Пріоритетні завдання

"""
    
    if priorities:
        for h in priorities:
            briefing += f"- **{h['title']}** ({h['category']})\n"
            if h.get('explicit_deadline'):
                briefing += f"  - Дедлайн: {h['explicit_deadline']}\n"
    else:
        briefing += "_Немає пріоритетних завдань_\n"
    
    briefing += f"""
---

## ✅ Нові задачі до реєстру ({len(new_tasks)})

"""
    
    if new_tasks:
        for h in new_tasks:
            briefing += f"- {h['title']} ({h['source']})\n"
    else:
        briefing += "_Немає нових задач_\n"
    
    briefing += f"""
---

## 🔄 Оновлення існуючих задач ({len(updates)})

"""
    
    if updates:
        for h in updates:
            briefing += f"- {h['title']}\n"
            if h.get('related_task_candidate'):
                briefing += f"  - Задача: {h['related_task_candidate']}\n"
    else:
        briefing += "_Немає оновлень_\n"
    
    briefing += f"""
---

## ℹ️ Інформаційні повідомлення ({len(info)})

"""
    
    if info:
        for h in info:
            briefing += f"- {h['title']} ({h['source']})\n"
    else:
        briefing += "_Немає інформаційних повідомлень_\n"
    
    briefing += f"""
---

## ⚠️ Потребує ручної перевірки ({len(reviews)})

"""
    
    if reviews:
        for h in reviews:
            briefing += f"- {h['title']}\n"
            briefing += f"  - Причина: {h.get('reason_for_handoff', 'N/A')}\n"
    else:
        briefing += "_Все класифіковано автоматично_\n"
    
    briefing += """
---

**Примітка**: Це автоматично згенерована чернетка. Secretary має доповнити деталями та прийняти управлінські рішення.
"""
    
    # Зберегти в файл
    output_path = Path(__file__).parent.parent / "briefings" / f"briefing_draft_{date_str}.md"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(briefing, encoding='utf-8')
    
    print(f"[✓] Чернетка брифінгу збережена: {output_path}\n")
    print(briefing)


def main():
    parser = argparse.ArgumentParser(description='Secretary Handoff Processor (приклад)')
    parser.add_argument('--date', type=str, help='Дата у форматі YYYY-MM-DD (за замовчуванням: сьогодні)')
    parser.add_argument('--no-dry-run', action='store_true', help='Реально додати задачі в реєстр')
    parser.add_argument('--briefing', action='store_true', help='Згенерувати чернетку брифінгу')
    
    args = parser.parse_args()
    
    # Визначити дату
    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    print(f"{'='*60}")
    print(f"  SECRETARY HANDOFF PROCESSOR")
    print(f"  Дата: {date_str}")
    print(f"{'='*60}\n")
    
    # Читання handoff файлу
    handoffs = read_handoff_file(date_str)
    
    if not handoffs:
        print("\n❌ Немає даних для обробки")
        return
    
    # Аналіз
    analyze_handoffs(handoffs)
    
    # Обробка категорій
    dry_run = not args.no_dry_run
    
    if dry_run:
        print("⚠️ DRY RUN MODE — зміни не записуються в реєстр\n")
    
    process_new_tasks(handoffs, dry_run=dry_run)
    process_updates(handoffs, dry_run=dry_run)
    process_reviews(handoffs)
    
    # Брифінг
    if args.briefing:
        generate_briefing_draft(handoffs)
    
    print("="*60)
    print("✅ Обробка завершена")
    print("="*60)


if __name__ == "__main__":
    main()
