# Command Center — персональний цифровий секретар

> **Мета:** менше рутини — більше часу на стратегічні рішення та розвиток служби анестезіології.

**Command Center** — це єдиний цифровий контур для управління інформацією, завданнями, обладнанням, документами та розвитком служби. Проект автоматизує збір даних із Gmail, Google Calendar та Google Sheets, формує щоденний брифінг, веде реєстр завдань і моніторить офіційні джерела (МОЗ, НСЗУ).

---

## 🧭 Огляд екосистеми

Система побудована навколо п'яти рівнів:

1. **Джерела даних** — Gmail, Google Calendar, Google Drive, офіційні джерела (МОЗ, НСЗУ, закупівлі, стандарти), внутрішні звіти.
2. **Автоматизації та агенти** — Command Center (цифровий секретар), Дослідник, Автоматизації, Інтеграція з Obsidian.
3. **Проекти та процеси** — управління завданнями, оновлення протоколів, облік обладнання, моніторинг закупівель, індексація документів, Second Brain, планування розвитку.
4. **Бази даних та знань** — Google Sheets, Google Drive, Obsidian, спеціалізовані бази.
5. **Користувач та результати** — щоденний брифінг, оперативне управління, стратегічні рішення, економія часу, підвищення якості.

Докладніше — у [docs/architecture.md](docs/architecture.md).

---

## 📦 Структура проекту

```
/
├── README.md                    # Цей файл
├── .github/workflows/
│   └── daily_briefing.yml       # Щоденний брифінг через GitHub Actions
├── modules/
│   ├── daily_briefing/          # Формування щоденного брифінгу
│   ├── task_manager/            # Управління завданнями
│   └── researcher/              # Моніторинг МОЗ / НСЗУ / новин
├── integrations/
│   ├── gmail_client.py          # Gmail API
│   ├── calendar_client.py       # Google Calendar API
│   └── sheets_client.py         # Google Sheets API
├── docs/
│   └── architecture.md          # Опис архітектури
├── requirements.txt
└── .gitignore
```

---

## 🚀 Швидкий старт

### 1. Клонування та залежності

```bash
git clone https://github.com/buldogin-cloud/Abacus.git
cd Abacus
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Налаштування Google API

1. Створіть проект у [Google Cloud Console](https://console.cloud.google.com/).
2. Увімкніть API: **Gmail API**, **Google Calendar API**, **Google Sheets API**.
3. Створіть **OAuth 2.0 Client ID** (тип «Desktop app») та завантажте `credentials.json` у корінь проекту.
4. Під час першого запуску відкриється браузер для авторизації — після згоди буде створено `token.json` (зберігає токени доступу).

> ⚠️ Файли `credentials.json`, `token.json`, `config.yaml`, `.env` **ніколи** не потрапляють у git (див. `.gitignore`).

### 3. Конфігурація

```bash
cp modules/daily_briefing/config.example.yaml config.yaml
# Відредагуйте config.yaml: вкажіть ID таблиці завдань, email, ключові слова тощо.
```

### 4. Запуск щоденного брифінгу

```bash
python modules/daily_briefing/briefing.py
```

Результат буде збережено у `briefings/briefing_YYYY-MM-DD.md`.

---

## 🤖 Автоматизація (GitHub Actions)

Workflow [`daily_briefing.yml`](.github/workflows/daily_briefing.yml) запускається **щодня о 07:00 за Києвом (04:00 UTC)**, виконує `briefing.py` та зберігає результат як артефакт.

Для роботи у CI додайте у **Settings → Secrets and variables → Actions** секрети:

| Секрет | Опис |
|--------|------|
| `GOOGLE_CREDENTIALS` | вміст `credentials.json` (OAuth client) |
| `GOOGLE_TOKEN` | вміст `token.json` (отриманий локально після першої авторизації) |
| `CONFIG_YAML` | вміст `config.yaml` |

---

## 🧩 Модулі

| Модуль | Призначення |
|--------|-------------|
| **daily_briefing** | Оркестрація handoff pipeline: збір → дедуплікація → класифікація → передача Secretary. |
| **task_manager** | Реєстр завдань у Google Sheets: створення, оновлення статусу, прострочені задачі. |
| **researcher** | Моніторинг оновлень МОЗ, НСЗУ та профільних новин → дайджест. |
| **doc_generator** | Генерація службових документів (службові записки) з автозавантаженням на Drive. |

Кожен модуль має власний `README.md` з деталями.

---

## 🔄 Handoff Pipeline (Phase 1 — завершено)

**Проблема**: Попередня версія `briefing.py` була монолітною — збирала дані, формувала брифінг, приймала управлінські рішення. Це змішувало технічну та управлінську відповідальність.

**Рішення**: Розділення ролей між **Abacus** (фоновий технічний агент) та **ChatGPT Secretary** (основний секретар).

### Архітектура

```
┌─────────────────────────────────────────┐
│  ABACUS (фоновий агент)                  │
│  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │collector │→ │ handoff  │→ │ Drive  │ │
│  │(Layer 1) │  │(Layer 2) │  │ Writer │ │
│  └──────────┘  └──────────┘  └────────┘ │
└────────────────────┬────────────────────┘
                     │
       handoff_YYYY-MM-DD.jsonl
       (Drive: Command Center/handoffs/secretary/)
                     │
                     ▼
┌─────────────────────────────────────────┐
│  SECRETARY (основний секретар)           │
│  ┌────────┐  ┌──────────┐  ┌─────────┐ │
│  │ Reader │→ │ Briefing │→ │Registry │ │
│  │        │  │Generator │  │ Updater │ │
│  └────────┘  └──────────┘  └─────────┘ │
└─────────────────────────────────────────┘
```

### Компоненти

| Файл | Відповідальність |
|------|------------------|
| **collector.py** | Layer 1: Збір сирих даних з Gmail/Calendar/Tasks/Researcher |
| **handoff.py** | Layer 2: Дедуплікація + класифікація + запис на Drive |
| **briefing.py** | Оркестратор: checkpoints → collect → handoff → logs → Telegram |
| **checkpoints_writer.py** | Стан останнього запуску + structured logs у `handoff_runs.jsonl` |

### Обмеження Abacus

⚠️ **Abacus НЕ МОЖЕ**:
- Змінювати статуси завдань у реєстрі
- Встановлювати/змінювати дедлайни
- Призначати відповідальних
- Вносити записи в Calendar

✅ **Abacus МОЖЕ**:
- Читати реєстр завдань (read-only)
- Збирати дані з усіх джерел
- Виявляти дублікати
- Класифікувати записи: `new_task`, `update_existing`, `info_only`, `needs_review`
- Передавати структуровані дані через Drive

### Для Secretary

Документація для ChatGPT Secretary:
- **[SECRETARY_HANDOFF_PROTOCOL.md](docs/SECRETARY_HANDOFF_PROTOCOL.md)** — повний протокол обробки handoff
- **[secretary_example.py](docs/secretary_example.py)** — робочий приклад Python скрипту

**Запуск прикладу**:
```bash
# Dry-run (без записів у реєстр)
python docs/secretary_example.py --date 2026-09-23 --briefing

# Реальна обробка (додати задачі в реєстр)
python docs/secretary_example.py --date 2026-09-23 --no-dry-run --briefing
```

---

## 🗺️ Дорожня карта

- [x] Command Center (базовий): Gmail, Calendar, завдання, звіти
- [ ] Реєстр протоколів з контролем актуальності
- [ ] Облік обладнання (заявки, ремонти, дефіцит)
- [ ] Моніторинг закупівель ВОКЛ
- [ ] Інтеграція Дослідника з Obsidian (автоматичний імпорт)
- [ ] Повна індексація папки «Робоча»

---

> *«Системи не замінюють людей, вони дають людям можливість бути ефективнішими.»*
