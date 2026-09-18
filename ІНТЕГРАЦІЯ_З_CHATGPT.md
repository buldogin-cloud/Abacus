# Інтеграція Command Center: ChatGPT ↔ Abacus

## Що вже налаштовано

### ✅ У Google Drive (`Command Center/`)

1. **`cadence/checkpoints.md`** — файл синхронізації
   - Містить timestamps останніх успішних перевірок
   - ChatGPT **оновлює** після кожної перевірки
   - Abacus **читає** перед запуском (delta-only)

2. **`daily/`** — папка для щоденних брифінгів
   - Abacus автоматично зберігає сюди `briefing_YYYY-MM-DD.md`
   - ChatGPT читає звідси готові брифінги

### ✅ У GitHub (`buldogin-cloud/Abacus`)

- `integrations/drive_client.py` — клієнт Google Drive
- `modules/daily_briefing/checkpoints_util.py` — читач checkpoints
- `modules/daily_briefing/briefing.py` — оновлений з підтримкою Drive

---

## Інструкція для ChatGPT Координатора

### 1. Читання checkpoints

**Перед кожним ранковим брифінгом:**

```python
# Прочитати Command Center/cadence/checkpoints.md
# Знайти останні успішні timestamps:
gmail_last_success: 2026-09-18T18:00:00Z
calendar_last_success: 2026-09-18T18:00:00Z
tasks_last_success: 2026-09-18T18:00:00Z
```

Це відправна точка — збирати дані **після** цього часу.

---

### 2. Проведення перевірки

1. Перевірити Gmail (нові листи після `gmail_last_success`)
2. Перевірити Calendar (події на сьогодні)
3. Перевірити Google Sheets реєстр завдань
4. Проаналізувати та показати користувачу

---

### 3. Оновлення checkpoints (ОБОВ'ЯЗКОВО!)

**Після успішної перевірки:**

Оновити `cadence/checkpoints.md` у блоці YAML:

```yaml
gmail_last_success: 2026-09-19T05:15:00Z      # ← НОВИЙ час (UTC)
gmail_last_checked: 2026-09-19T05:15:00Z
gmail_status: success

calendar_last_success: 2026-09-19T05:15:00Z
calendar_last_checked: 2026-09-19T05:15:00Z
calendar_status: success

tasks_last_success: 2026-09-19T05:15:00Z
tasks_last_checked: 2026-09-19T05:15:00Z
tasks_status: success
```

**Якщо джерело недоступне:**

```yaml
gmail_last_success: 2026-09-18T18:00:00Z  # ← НЕ ЗМІНЮЄТЬСЯ!
gmail_last_checked: 2026-09-19T05:15:00Z  # ← Час спроби
gmail_status: failed
```

---

### 4. Читання брифінгів від Abacus

**Якщо Abacus вже створив брифінг:**

Прочитати `daily/briefing_2026-09-19.md` замість повторного парсингу Gmail.

Додати **інтелектуальний аналіз**:
- Що термінове
- Що потребує рішення користувача
- Пріоритизація

---

## Приклад сценарію

### 06:55 — Користувач спить

**Нічого не відбувається** (Abacus запускається о 7:00)

---

### 07:00 — Abacus (автоматично)

1. Читає `checkpoints.md` → останній checkpoint `2026-09-18T18:00:00Z`
2. Парсить Gmail після 18:00 вчора → знаходить 3 нові листи
3. Читає Calendar на сьогодні → 6 подій
4. Генерує `briefing_2026-09-19.md` → зберігає в `daily/`
5. **НЕ** оновлює checkpoints (це робить ChatGPT)

---

### 08:15 — Користувач прокинувся

**Користувач:** "Що нового?"

**ChatGPT:**

```
[читає daily/briefing_2026-09-19.md від Abacus]

✅ Брифінг сформовано о 07:00

📧 Нові листи (3):
1. Головлікар — ТЕРМІНОВА нарада о 10:00 [ПОТРЕБУЄ ПІДГОТОВКИ]
2. Бухгалтерія — звіт ВОКЛ до 17:00 [ДЕДЛАЙН СЬОГОДНІ]
3. Постачальник — пропозиція обладнання [FYI]

📅 Події сьогодні (6):
- 09:00 — Обхід ВАІТ
- 10:00 — Нарада з головлікарем [ТЕРМІНОВА]
- 14:00 — ...

⚠️ Прострочені завдання (2):
- Звіт ВОКЛ (строк: 16.09) — потребує рішення
- ...

💡 Дії на сьогодні:
1. Підготувати матеріали до наради (до 09:45)
2. Закрити звіт ВОКЛ (до 17:00)
3. Відповісти постачальнику (до кінця дня)
```

**Потім оновлює checkpoints:**

```yaml
gmail_last_success: 2026-09-19T05:15:00Z
calendar_last_success: 2026-09-19T05:15:00Z
tasks_last_success: 2026-09-19T05:15:00Z
```

---

## Критично важливо

### ✅ ChatGPT завжди оновлює checkpoints після перевірки
### ❌ Abacus НІКОЛИ не оновлює checkpoints
### ✅ Обидва читають та пишуть у ті самі файли Drive
### ✅ Delta-only: збирати лише нове після checkpoint

---

## Структура файлів у Drive

```
Command Center/
├── cadence/
│   └── checkpoints.md          [ChatGPT пише, Abacus читає]
├── daily/
│   ├── briefing_2026-09-19.md  [Abacus пише, ChatGPT читає]
│   ├── briefing_2026-09-20.md
│   └── ...
├── context/
│   └── ...
└── РОЗПОДІЛ_РОБОТИ.md
```

---

## Часові зони

- **Checkpoints:** завжди UTC (ISO 8601: `YYYY-MM-DDTHH:MM:SSZ`)
- **Abacus запуск:** 07:00 Київ = 04:00 UTC (GitHub Actions)
- **Користувач:** Київ (UTC+3)

При оновленні checkpoints використовувати UTC!

---

## Налаштування для ChatGPT

### Додати до Custom Instructions / Projects:

```
Я працюю з Abacus AI Agent синхронно:
- Перед кожним брифінгом читаю Command Center/cadence/checkpoints.md
- Після успішної перевірки оновлюю timestamps у checkpoints.md
- Можу читати готові брифінги з Command Center/daily/
- Завжди використовую delta-only підхід (лише нове після checkpoint)
```

---

## Тестування

### Перевірити що все працює:

1. Відкрити `Command Center/cadence/checkpoints.md`
2. Переконатися що timestamps оновлюються після кожної перевірки
3. Перевірити що `daily/` містить згенеровані брифінги
4. Порівняти час checkpoint з часом останнього брифінгу

---

**Оновлено:** 2026-09-19  
**Статус:** Готово до використання ✅
