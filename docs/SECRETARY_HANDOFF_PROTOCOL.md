# SECRETARY HANDOFF PROTOCOL

**Версія**: 1.0  
**Дата**: 2026-09-23  
**Аудиторія**: ChatGPT Secretary

---

## Огляд

Цей протокол описує, як **ChatGPT Secretary** (основний секретар) читає та обробляє дані від **Abacus** (фонового технічного агента).

### Розподіл ролей

| Агент | Роль | Відповідальність |
|-------|------|------------------|
| **Abacus** | Фоновий технічний агент | Збір даних, дедуплікація, класифікація, передача через Drive |
| **Secretary** | Основний секретар | Читання handoff, формування брифінгу, оновлення реєстру завдань, управлінські рішення |

### Критичні обмеження Abacus

⚠️ **Abacus НЕ МОЖЕ**:
- Змінювати статуси завдань у реєстрі
- Встановлювати/змінювати дедлайни
- Призначати відповідальних
- Вносити записи в Calendar
- Приймати управлінські рішення

✅ **Abacus МОЖЕ**:
- Читати реєстр завдань (read-only)
- Збирати дані з Gmail/Calendar/Tasks/Researcher
- Виявляти дублікати
- Класифікувати записи
- Передавати структуровані дані через Drive

---

## Архітектура передачі даних

```
┌─────────────────────────────────────────────────────────────┐
│  ABACUS (фоновий агент)                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                 │
│  │ collector│ → │ handoff  │ → │  Drive   │                 │
│  │ (Layer 1)│   │ (Layer 2)│   │  Writer  │                 │
│  └──────────┘   └──────────┘   └──────────┘                 │
└───────────────────────────────────┬─────────────────────────┘
                                    │
                        handoff_YYYY-MM-DD.jsonl
                        (Google Drive: Command Center/handoffs/secretary/)
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────┐
│  SECRETARY (основний секретар)                               │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                 │
│  │  Reader  │ → │ Briefing │ → │ Registry │                 │
│  │          │   │ Generator│   │ Updater  │                 │
│  └──────────┘   └──────────┘   └──────────┘                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Формат handoff файлів

### Локація на Drive

```
Command Center/
  └── handoffs/
      └── secretary/
          ├── handoff_2026-09-23.jsonl
          ├── handoff_2026-09-22.jsonl
          └── ...
```

**Folder ID**: `1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj` (secretary)

### Формат файлу

- **Назва**: `handoff_YYYY-MM-DD.jsonl`
- **Формат**: JSONL (JSON Lines) — кожен рядок окремий JSON запис
- **Encoding**: UTF-8
- **Створюється**: щодня Abacus після збору даних

---

## Структура handoff запису

Кожен рядок у `.jsonl` файлі — це окремий JSON об'єкт з такою структурою:

```json
{
  "handoff_id": "e8f84fee-c303-4953-8914-417e6ea3c417",
  "run_id": "run_20260923_223819",
  "produced_by": "Abacus",
  "produced_at": "2026-09-23T22:38:24.579333Z",
  "detected_at": "2026-09-23T22:38:19.319215Z",
  
  "source": "gmail",
  "source_id": "1a0cdfb231055d2b",
  "source_url": "",
  
  "category": "наказ_МОЗ_або_керівництва",
  "title": "Наказ №874-Адм",
  "factual_summary": "",
  "explicit_deadline": null,
  "people_mentioned": [],
  
  "attachments": {
    "has_attachments": false,
    "types": []
  },
  
  "related_task_candidate": null,
  "confidence": "high",
  "reason_for_handoff": "потенційна нова задача — потребує управлінського рішення Secretary",
  "suggested_check": "Оцінити, чи потрібно додати нову задачу до реєстру",
  
  "source_verified": false,
  "processing_status": "pending_secretary",
  "classification": "new_task",
  "is_priority": true,
  
  "_dedup_keys": {
    "source_id": "1a0cdfb231055d2b",
    "message_id": "1a0cdfb231055d2b",
    "thread_id": "",
    "url": ""
  },
  
  "_readonly_note": "Abacus не змінює реєстр. Лише Secretary може оновити статус/дедлайн/відповідального."
}
```

### Ключові поля

| Поле | Тип | Опис |
|------|-----|------|
| `handoff_id` | string (UUID) | Унікальний ID цього handoff запису |
| `run_id` | string | ID запуску Abacus (для трасування) |
| `source` | string | Джерело: `gmail`, `calendar`, `tasks_registry`, `researcher` |
| `source_id` | string | ID у джерелі (message_id, event_id, task_id) |
| `category` | string | Категорія: `наказ_МОЗ_або_керівництва`, `внутрішня_комунікація`, `закупівлі`, тощо |
| `title` | string | Стислий заголовок |
| `factual_summary` | string | Фактичний опис (без інтерпретацій) |
| `explicit_deadline` | string/null | Дедлайн у форматі ISO 8601 (якщо виявлено) |
| `people_mentioned` | array | Список згаданих людей |
| `classification` | string | **Ключове поле**: `new_task`, `update_existing`, `info_only`, `needs_review` |
| `is_priority` | boolean | Чи є це пріоритетним |
| `related_task_candidate` | string/null | ID завдання в реєстрі, якщо є зв'язок |

---

## Класифікація записів

Abacus класифікує кожен запис у одну з 4 категорій:

| Classification | Що означає | Дія Secretary |
|----------------|------------|---------------|
| `new_task` | Потенційна нова задача | **Розглянути → додати в реєстр** (якщо підтвердиться) |
| `update_existing` | Оновлення до існуючої задачі | **Оновити** відповідний запис у реєстрі |
| `info_only` | Інформаційний запис | **Включити в брифінг**, не потребує action item |
| `needs_review` | Потребує ручної перевірки | **Прапорець для уваги** — ручне рішення |

---

## Робочий процес Secretary

### 1. Щоранку: читання handoff файлу

```python
# Псевдокод для Secretary
from integrations.drive_client import DriveClient
import json
from datetime import datetime

dc = DriveClient()
secretary_folder = "1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj"

# Сьогоднішня дата
today = datetime.now().strftime("%Y-%m-%d")
filename = f"handoff_{today}.jsonl"

# Знайти файл
handoff_file = dc.find_file(filename, parent_id=secretary_folder)

if handoff_file:
    # Прочитати вміст
    content = dc.read_file(handoff_file['id'])
    lines = content.strip().split('\n')
    
    # Розпарсити кожен запис
    handoffs = [json.loads(line) for line in lines]
    
    print(f"📥 Отримано {len(handoffs)} handoff записів")
else:
    print("❌ Handoff файл не знайдено — Abacus не запустився?")
```

### 2. Обробка записів по категоріях

#### 2.1 `new_task` — додати в реєстр

```python
# Приклад обробки new_task
for h in handoffs:
    if h['classification'] == 'new_task':
        # Перевірити related_task_candidate
        if h['related_task_candidate']:
            # Можливо це дублікат — перевірити вручну
            print(f"⚠️ Можливий дублікат: {h['title']} → task {h['related_task_candidate']}")
            continue
        
        # Додати нову задачу
        task_manager.add_task(
            task_text=h['title'],
            assignee="Бєлінський" if 'IT' in h['category'] else "TBD",
            deadline=h['explicit_deadline'] or "TBD",
            status="Нова",
            notes=f"Джерело: {h['source']}, ID: {h['source_id']}"
        )
        print(f"✅ Додано задачу: {h['title']}")
```

#### 2.2 `update_existing` — оновити реєстр

```python
for h in handoffs:
    if h['classification'] == 'update_existing' and h['related_task_candidate']:
        task_id = h['related_task_candidate']
        
        # Оновити статус/примітки
        task_manager.update_task_status(task_id, "В роботі")
        print(f"✅ Оновлено задачу {task_id}: {h['title']}")
```

#### 2.3 `info_only` — включити в брифінг

```python
info_items = [h for h in handoffs if h['classification'] == 'info_only']
# Включити в секцію "Інформаційні повідомлення" брифінгу
```

#### 2.4 `needs_review` — прапорець

```python
review_items = [h for h in handoffs if h['classification'] == 'needs_review']
# Помітити у брифінгу як "Потребує уваги"
```

### 3. Формування daily briefing

Брифінг має включати:

```markdown
# Daily Briefing — [YYYY-MM-DD]

## 🔴 Пріоритетні завдання
<!-- new_task з is_priority: true -->

## ✅ Нові задачі до реєстру
<!-- new_task -->

## 🔄 Оновлення існуючих завдань
<!-- update_existing -->

## ℹ️ Інформаційні повідомлення
<!-- info_only -->

## ⚠️ Потребує ручної перевірки
<!-- needs_review -->

## 📅 Події сьогодні
<!-- calendar events -->

## 📧 Важливі листи
<!-- gmail highlights -->

---
**Згенеровано Secretary на основі handoff від Abacus**  
Run ID: [run_id]
```

### 4. Відмітка оброблених записів

**ВАЖЛИВО**: Abacus НЕ відстежує, які handoffs вже оброблені. Secretary має:

- Вести власний лог оброблених `handoff_id`
- Або зберігати `processed_handoffs_YYYY-MM.jsonl` на Drive
- Або використовувати окрему колонку в Google Sheets

**Формат processed log**:
```jsonl
{"handoff_id": "e8f84fee...", "processed_at": "2026-09-23T08:00:00Z", "action": "added_to_registry", "task_id": "12345"}
{"handoff_id": "a1b2c3d4...", "processed_at": "2026-09-23T08:01:00Z", "action": "included_in_briefing"}
```

---

## Критичні сценарії

### Сценарій 1: Abacus не запустився

**Ознака**: Файл `handoff_YYYY-MM-DD.jsonl` відсутній на Drive

**Дія Secretary**:
1. Перевірити останній успішний handoff (вчорашній?)
2. Сформувати брифінг вручну (fallback режим)
3. Повідомити Андрія про збій Abacus

### Сценарій 2: Дублікати в handoff

**Ознака**: `related_task_candidate` заповнений для `new_task`

**Дія Secretary**:
1. Перевірити існуючу задачу в реєстрі
2. Якщо це дублікат → пропустити, не додавати
3. Якщо це оновлення → перекласифікувати як `update_existing`

### Сценарій 3: Невідома категорія

**Ознака**: `category` має незнайоме значення

**Дія Secretary**:
1. Перенести в `needs_review`
2. Розглянути вручну
3. Оновити категорійний словник Abacus (якщо потрібно)

---

## Інтеграція з реєстром завдань

**Task Registry Sheet ID**: `11oqxqRm7cAH6jpKf9T7CYtIY0j01LAV259F7NGkd5so`

### Поля реєстру

| Колонка | Тип | Джерело з handoff |
|---------|-----|-------------------|
| ID | auto | генерує TaskManager |
| Завдання | string | `title` |
| Відповідальний | string | управлінське рішення Secretary |
| Термін | date | `explicit_deadline` або TBD |
| Статус | enum | "Нова" для new_task |
| Примітки | string | `source` + `source_id` + `category` |

### Приклад запису в реєстр

```
ID: 123
Завдання: Наказ №874-Адм
Відповідальний: Бєлінський
Термін: 2026-09-30
Статус: Нова
Примітки: Джерело: gmail, ID: 1a0cdfb231055d2b, Категорія: наказ_МОЗ_або_керівництва
```

---

## Метрики та моніторинг

Secretary має відстежувати:

| Метрика | Що вимірює |
|---------|------------|
| `handoffs_received` | Скільки записів отримано сьогодні |
| `tasks_added` | Скільки нових задач додано в реєстр |
| `tasks_updated` | Скільки існуючих задач оновлено |
| `items_flagged` | Скільки потребує ручної перевірки |
| `processing_time` | Час обробки handoff файлу |

**Формат метрик** (опціонально, зберігати в Drive):
```jsonl
{"date": "2026-09-23", "handoffs_received": 21, "tasks_added": 2, "tasks_updated": 0, "items_flagged": 6, "processing_time_sec": 15}
```

---

## Troubleshooting

### Проблема: Не можу знайти handoff файл

**Рішення**:
1. Перевірити folder ID: `1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj`
2. Перевірити формат дати: `YYYY-MM-DD`
3. Перевірити timezone: Kyiv (Europe/Kyiv)
4. Подивитися в `handoff_runs.jsonl` — чи запустився Abacus?

### Проблема: JSON parse error

**Рішення**:
1. Перевірити encoding файлу (має бути UTF-8)
2. Перевірити формат JSONL (кожен рядок окремий JSON)
3. Якщо помилка — повідомити про corrupted файл

### Проблема: Занадто багато `needs_review`

**Рішення**:
1. Це означає, що Abacus не впевнений у класифікації
2. Можливо треба покращити правила дедуплікації
3. Або додати нові категорії в словник

---

## Контакт

**Власник системи**: Андрій Бєлінський  
**Telegram Bot**: @AbacusSECbot  
**Chat ID**: 5456389264  
**Timezone**: Europe/Kyiv

**Репозиторій**: [buldogin-cloud/Abacus](https://github.com/buldogin-cloud/Abacus)

---

**Кінець протоколу**
