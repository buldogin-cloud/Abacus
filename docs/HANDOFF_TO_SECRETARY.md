# Пакет передачі Command Center та нормативного радара ChatGPT-Секретарю

> Підготував: Abacus (фоновий технічний агент). Дата: 2026-10-06.
> Призначення: передати супровід і розвиток Command Center та нормативного радара на бік ChatGPT/Codex.
> **Жодних секретів (паролів, токенів, App Password, приватних ключів) у цьому документі немає — лише назви й розташування.**

---

## 1. Репозиторій і код

| Параметр | Значення |
|---|---|
| **URL репозиторію** | https://github.com/buldogin-cloud/Abacus |
| **Clone (HTTPS)** | `https://github.com/buldogin-cloud/Abacus.git` |
| **Власник** | `buldogin-cloud` (обліковий запис GitHub) |
| **Робоча гілка** | `main` |
| **Останній commit SHA** | `c97c36132afe593e399d7bd4ee3e4fd8250ca470` (`c97c361`) |
| **Робоча директорія (на цій VM)** | `/home/ubuntu/github_repos/Abacus` |
| **Синхронізація** | `origin/main == HEAD == c97c361`, `git status` чистий — усе збережено й запушено |

### Підтвердження збереження
`git fetch` + `git rev-list --count HEAD...origin/main` → `0 0`. Локальна й віддалена гілки ідентичні. Незакомічених змін немає.

### Останні коміти
- `c97c361` — Виконати 6 вимог Секретаря: Gmail, канонічні файли, радар, класифікація, синхронізація статусів, контрольний прогін *(цей сеанс)*
- `cc38d22` — Привести пайплайн у відповідність до 8 вимог командного центру
- `c4b1555` — замінити Gmail OAuth на IMAP/SMTP, прибрати restricted scopes

### Файли, створені/змінені в комітах передачі

**Коміт `c97c361` (6 вимог Секретаря):**
| Файл | Статус | Що зроблено |
|---|---|---|
| `modules/daily_briefing/canonical.py` | **новий** | Єдине джерело правди для всіх Drive ID |
| `integrations/drive_client.py` | змінено | `update_file_by_id()`, `trash_file()` |
| `modules/daily_briefing/checkpoints_util.py` | змінено | Читання канону за ID |
| `modules/daily_briefing/checkpoints_writer.py` | змінено | Запис канону за ID, `update_checkpoints()`, властивості URL |
| `modules/daily_briefing/radar.py` | змінено | Запис у вкладку Sheet, дедуп, read-back, чистка URL |
| `modules/daily_briefing/collector.py` | змінено | Класифікація подій календаря (`_classify_calendar_event`) |
| `modules/daily_briefing/handoff.py` | змінено | Guard календаря, матч реєстру за номером+контентом |
| `modules/daily_briefing/briefing.py` | змінено | `compute_run_status()`, Telegram summary, статистика |

**Коміт `cc38d22` (8 вимог, попередній етап):** `gmail_client.py`, `briefing.py`, `checkpoints_util.py`, `collector.py`, `handoff.py`, `radar.py` (створено).

---

## 2. Права доступу — ПОТРЕБУЄ ДІЇ АНДРІЯ

> **Важливо:** я (Abacus) працюю через GitHub App з обмеженими правами. Я **не можу** додавати співавторів, створювати токени для третіх сторін чи видавати доступ ChatGPT/Codex. Це робить **лише власник репозиторію (Андрій)** вручну.

### Що саме потрібно від Андрія, щоб надати ChatGPT/Codex доступ read+write

Є два штатні механізми GitHub (оберіть один):

**Варіант A — додати як співавтора (collaborator):**
1. Відкрити https://github.com/buldogin-cloud/Abacus/settings/access
2. **Add people** → ввести **GitHub-username акаунта ChatGPT/Codex** → роль **Write**.
3. ChatGPT/Codex прийме запрошення.

**Варіант B — fine-grained Personal Access Token (якщо ChatGPT працює без власного GitHub-акаунта):**
1. https://github.com/settings/tokens?type=beta → **Generate new token**
2. Repository access → **Only select repositories** → `buldogin-cloud/Abacus`
3. Permissions → **Contents: Read and write**, **Metadata: Read-only** (за потреби **Pull requests: Read and write**).
4. Передати токен ChatGPT/Codex **безпечним каналом** (не в чат).

### ❓ Дані, яких я потребую від вас, щоб підготувати доступ
Перш ніж щось робити з доступом, повідомте **одне** з двох:
- **GitHub-username** акаунта ChatGPT/Codex (для Варіанта A), **або**
- підтвердження, що ChatGPT/Codex використовуватиме **fine-grained PAT** (Варіант B) — тоді токен генеруєте ви.

> Доступ до **середовища виконання** (GitHub Actions) надається автоматично разом із правом Write на репозиторій: Secrets бачить лише власник, а workflow запускається від імені репозиторію.

---

## 3. Інструкція з роботи скрипту

### 3.1 Архітектура та призначення модулів

Пайплайн — трирівневий: **збір → обробка (дедуп/класифікація/запис) → оркестрація/звіт**.

```
briefing.py (оркестратор)
├── canonical.py            ← ID усіх ресурсів (єдине джерело правди)
├── collector.py            ← збір: Gmail, Calendar, Tasks, Researcher → CollectedData
│   ├── integrations/gmail_client.py      (IMAP/SMTP)
│   ├── integrations/calendar_client.py   (Calendar API)
│   ├── integrations/sheets_client.py     (Sheets API, реєстр завдань)
│   └── modules/researcher/researcher.py  (моніторинг МОЗ/НСЗУ/RSS)
├── handoff.py              ← дедуп + класифікація + запис handoff_YYYY-MM-DD.jsonl
├── radar.py                ← нормативний радар → вкладка Google Sheet
├── checkpoints_util.py     ← читання canonical checkpoints (window_start)
├── checkpoints_writer.py   ← запис canonical checkpoints.md / automation_log.md / handoff_runs.jsonl
└── integrations/telegram_sender.py  ← технічний summary у Telegram
```

- **Abacus НІКОЛИ не пише реєстр завдань** — лише читає його для зв'язування. Реєстр веде Секретар.
- Handoff — це «сире» корисне навантаження для Секретаря; **брифінг формує Секретар**, а не Abacus.

### 3.2 Точки входу
- **Головна:** `modules/daily_briefing/briefing.py` → `main()` (рядок 414), `if __name__ == "__main__"` (рядок 677).
- **Пост-перевірка VAIT:** `modules/daily_briefing/vait_postcheck.py`.
- `radar.py` не має власного CLI — викликається з `briefing.py` (функція радара).

### 3.3 Команди запуску
```bash
cd /home/ubuntu/github_repos/Abacus
export GMAIL_APP_PASSWORD="$(cat /home/ubuntu/gmail_app_password.txt)"   # локально

# Повний прогін (запис у Drive + Telegram summary):
python modules/daily_briefing/briefing.py

# Детальна статистика в консоль:
python modules/daily_briefing/briefing.py --stdout

# Тестовий режим без запису у Drive:
python modules/daily_briefing/briefing.py --no-drive --stdout

# Інший конфіг:
python modules/daily_briefing/briefing.py --config /шлях/до/config.yaml
```

### 3.4 Залежності
`requirements.txt`: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, `PyYAML`, `python-dotenv`, `requests`, `beautifulsoup4`, `feedparser`, `python-dateutil`, `Flask`, `abacusai`, `python-docx`.
```bash
pip install -r requirements.txt
```
Python 3.11 (як у GitHub Actions).

### 3.5 Конфігурація scheduler
Файл `.github/workflows/daily_briefing.yml`:
- **Handoff Pipeline:** cron `0 4 * * *` = **07:00 Europe/Kyiv** → `python modules/daily_briefing/briefing.py`
- **VAIT Post-Check:** cron `35 6 * * *` = **08:35 Europe/Kyiv**
- `workflow_dispatch` — ручний запуск.
- Env у workflow: `GOOGLE_CREDENTIALS`, `GOOGLE_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (з GitHub Secrets).

### 3.6 Тести
Окремого формального test-suite немає. Валідація виконується:
- `python -c "import ast; ast.parse(open('файл').read())"` — синтаксис;
- контрольним прогоном `--no-drive --stdout` (без побічних ефектів);
- повторним прогоном радара (ідемпотентність: 0 нових, без дублів).

### 3.7 Порядок розгортання
1. Налаштувати GitHub Secrets (див. §5).
2. `pip install -r requirements.txt`.
3. Перевірити `config.yaml` (`send_telegram: true`, `send_email: false`).
4. Запуск: GitHub Actions за розкладом або `workflow_dispatch`.

### 3.8 Порядок аварійного відновлення
- **Checkpoints не просунулись** — вікно `window_start` лишається, наступний прогін повторно збере записи (втрати даних немає — закладено в логіку).
- **Збій Google-авторизації** — перевипустити `GOOGLE_TOKEN` (`authorize.py`).
- **Пошкоджений canonical-файл** — відновити з git-історії та з бекапу `/home/ubuntu/abacus_parallel_backup/`.
- **Відкотити код** — `git revert <SHA>` або `git checkout cc38d22` (попередній стабільний стан).

### 3.9 Контрольний запуск
```bash
cd /home/ubuntu/github_repos/Abacus
export GMAIL_APP_PASSWORD="$(cat /home/ubuntu/gmail_app_password.txt)"
python modules/daily_briefing/briefing.py --stdout
```
Наприкінці блок «СТАТИСТИКА» показує `RUN STATUS` і всі лічильники (§7).

### 3.10 Критерії статусів (`compute_run_status`, briefing.py р. 132)
| Статус | Коли |
|---|---|
| **success** | Gmail доступний (`connection_verified`) **І** збір = success **І** handoff записано + read-back ok **І** `radar_status ∈ {success, skipped}` **І** немає критичних помилок запису |
| **partial** | Не всі умови success, але немає повного провалу (напр., радар partial або Gmail недоступний) |
| **failed** | Збір упав (`overall=failed`) **АБО** запис у Drive упав (`storage=failed`) |
| **needs_review** | *(класифікація окремого запису)* — нагадування/документ без точного збігу з реєстром за номером І контентом → Секретар переглядає вручну |

> **Увага (визнаний недолік, див. §6):** поточний `radar_status=success` виставляється вже за фактом запису рядка + read-back, **без** перевірки змісту документа (`fetch_verified`) і без заповнення колонок аналізу. Через це загальний `success` може бути завищеним — коректніше `partial`, поки зміст не перевірено. Рекомендація до доопрацювання на боці Секретаря.

---

## 4. Карта зовнішніх ресурсів (точні ідентифікатори)

### Google Drive — папки
| Ресурс | ID | Посилання |
|---|---|---|
| **Command Center (корінь)** | `1eh47d2AtLZwfuYdthzVfJ-5pz_x2Qgf5` | https://drive.google.com/drive/folders/1eh47d2AtLZwfuYdthzVfJ-5pz_x2Qgf5 |
| **handoffs/** (тека) | `1tmeJKjy_P-T38AKXHv9LtdjjqOFf4lgl` | https://drive.google.com/drive/folders/1tmeJKjy_P-T38AKXHv9LtdjjqOFf4lgl |
| **handoffs/secretary/** | `1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj` | https://drive.google.com/drive/folders/1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj |

### Канонічні файли (писати/читати ТІЛЬКИ за цими ID — у корені Command Center)
| Файл | ID | Посилання |
|---|---|---|
| **checkpoints.md** | `1leDnfV8IkLiceOTU7Cc-iHqLprtsUtXy` | https://drive.google.com/file/d/1leDnfV8IkLiceOTU7Cc-iHqLprtsUtXy/view |
| **automation_log.md** | `1pPoB1qKlZhN3g61tXkLtQb385M-Zjbxc` | https://drive.google.com/file/d/1pPoB1qKlZhN3g61tXkLtQb385M-Zjbxc/view |
| **handoff_runs.jsonl** | `189AmK-uQRqpUwboDBRO3gMjS8dyZDtCU` | https://drive.google.com/file/d/189AmK-uQRqpUwboDBRO3gMjS8dyZDtCU/view |

### Нормативний радар (Google Sheets)
| Ресурс | Значення |
|---|---|
| **Spreadsheet ID** | `1CJrhOora2_vTMVmwO2iBzGGpCsurx8CwDnR-9DA6Y1M` |
| **Назва таблиці** | Реєстр наказів та листів МОЗ — робоча копія для актуалізації 2026 |
| **Вкладка** | `Радар — нові документи` |
| **Посилання** | https://docs.google.com/spreadsheets/d/1CJrhOora2_vTMVmwO2iBzGGpCsurx8CwDnR-9DA6Y1M/edit |
| **Колонки A–J** | ID документа \| Вид \| Орган \| Дата документа \| Номер \| Назва \| Офіційне посилання \| Статус/редакція \| Джерело статусу \| Що змінилось |

### Реєстр завдань Секретаря (read-only для Abacus)
| Ресурс | Значення |
|---|---|
| **Spreadsheet ID** | `11oqxqRm7cAH6jpKf9T7CYtIY0j01LAV259F7NGkd5so` |
| **Вкладка** | `Завдання` |
| **Посилання** | https://docs.google.com/spreadsheets/d/11oqxqRm7cAH6jpKf9T7CYtIY0j01LAV259F7NGkd5so/edit |

### Gmail та Calendar
| Інтеграція | Деталі |
|---|---|
| **Gmail** | IMAP/SMTP, App Password; обліковий запис `andrewbelin6@gmail.com`; клієнт `integrations/gmail_client.py` |
| **Calendar** | Google Calendar API, `calendar_id: primary`, scope `calendar.readonly`; клієнт `integrations/calendar_client.py` |

### Застарілі паралельні копії — ПРИБРАНО (trashed), НЕ використовувати
| Файл | ID (у кошику) |
|---|---|
| handoffs/checkpoints.md | `1Ohp9ihVWF7qpkto4ukkCoEoOmD6vdNm5` |
| handoffs/automation_log.md | `1Gywh7HB5eCZSp6Ph2ShkdadjNCqHX1Cv` |
| handoffs/handoff_runs.jsonl | `14KK2s0p8boxe_908heagvkMpcm6BerQK` |
Бекап перед знищенням: `/home/ubuntu/abacus_parallel_backup/`.

### Command Center Hub і «99 — LOG»
> ⚠️ **НЕ інтегровано.** «Hub 99 — LOG» відсутній у конфігурації пайплайну — я не маю його ID/розташування, тому контрольні запуски туди **не записуються**. Якщо потрібна інтеграція — надайте ID/посилання, це окреме доопрацювання (див. §6).

### Сервісні облікові записи та ролі
| Обліковий запис | Роль |
|---|---|
| **Google-користувач** `andrewbelin6@gmail.com` | OAuth-користувач (НЕ service account): Gmail (IMAP/SMTP), Calendar, Sheets, Drive. Scopes: `calendar.readonly`, `drive.file`, `spreadsheets` |
| **Telegram-бот** | Надсилання технічного summary у чат |
| **GitHub** `buldogin-cloud` | Власник репозиторію, носій GitHub Secrets |

---

## 5. Змінні середовища і секрети (лише опис, без значень)

| Змінна | Призначення | Де зберігається | Як надати/перевипустити | Що зламається без неї |
|---|---|---|---|---|
| **GOOGLE_CREDENTIALS** | OAuth client (client_id/secret) Google | GitHub Actions Secret | Google Cloud Console → Credentials → OAuth client | Уся Google-авторизація: Calendar, Sheets, Drive, радар, checkpoints |
| **GOOGLE_TOKEN** | OAuth token (refresh token), scopes calendar.readonly/drive.file/spreadsheets | GitHub Actions Secret + локальний `token.json` | Перевипустити через `authorize.py` (OAuth flow) | Те саме — всі Google-сервіси падають |
| **GMAIL_APP_PASSWORD** | App Password для IMAP/SMTP Gmail | GitHub Actions Secret + файл `/home/ubuntu/gmail_app_password.txt` + abacus secret store | Google Account → Security → App Passwords (створити новий) | Джерело `gmail` → failed → загальний статус `partial` |
| **TELEGRAM_BOT_TOKEN** | Токен бота для summary | GitHub Actions Secret + abacus secret store | @BotFather → новий токен | Технічний summary не надсилається |
| **TELEGRAM_CHAT_ID** | Цільовий чат для summary | GitHub Actions Secret + `chat_ids.json` | Отримати з webhook (`chat_ids.json`) | summary нема куди надсилати |

> Жодне значення в цьому документі не наведено. Для перевипуску доступу — через відповідну консоль (Google/BotFather/GitHub), значення передавати безпечним каналом, не в чат.

---

## 6. Поточний стан

### ✅ Реально працює
- Збір джерел: Gmail (IMAP/SMTP), Calendar, реєстр завдань (read-only), Researcher.
- Gmail-з'єднання: `verify_connection()` (реальний IMAP LOGIN+SELECT) → conn:ok.
- Дедуплікація (крос-денна, вікно 14 днів) та класифікація handoff.
- Канонічні файли в корені Command Center: checkpoints.md, automation_log.md, handoff_runs.jsonl (read-back після запису).
- Паралельні копії в теці handoffs/ прибрані (trashed) — єдине джерело правди відновлено.
- Класифікація подій календаря: ніколи не оновлює задачі авто; особисті/звичайні → info_only; нагадування з № → матч за номером+контентом; без точного збігу → needs_review.
- Запис рядків радара у вкладку Google Sheet, дедуп за `№|дата`, read-back, чистка URL (fbclid/utm_).

### ⚠️ Працює частково
- **Нормативний радар:** рядки записуються і підтверджуються read-back (5 наказів: №1328, №1044, №870, №1984, №1675), **але**:
  - зміст документів не перевіряється (`radar_fetch_verified = 0`);
  - колонки аналізу не заповнюються (див. нижче A–O);
  - статуси NEW/rediscovered не розрізняються.

### ❌ Не працює / не реалізовано
- **Завантаження нормативних документів:** `radar_docs_downloaded = 0` — файли не кладуться в папку.
- **Перевірка змісту першоджерела** з runner — заблокована (HTTP 403).
- **Інтеграція з Hub «99 — LOG»** — відсутня (немає ID).
- **Колонки «Вплив на ВОКЛ», «Дія/строк», «Напрям», «Виявлено», «Оновлено»** — не заповнюються.

### Проблема HTTP 403
`moz.gov.ua` (і споріднені держсайти) віддають **HTTP 403 — Cloudflare блокує саме цю VM / спільний IP**. Наслідок: runner не може ні відкрити офіційну сторінку, ні завантажити PDF. Тому `radar_fetch_verified=0` і `radar_docs_downloaded=0` — **структурно з цієї машини**. Рядки містять лінк на офіційний домен (НЕ перевірений зміст); чесна нотатка стоїть у колонці «Що змінилось» і в `errors`. **Обхід потребує іншого маршруту до сайту (бік Андрія / розблокування IP / проксі на довіреній машині) — на цій VM недосяжно і проксі тут заборонені політикою.**

### Логіка `radar_status` (визнаний технічний борг)
`radar_status=success` виставляється вже за умови «рядок записано + read-back ok», **без** `fetch_verified` і без повноти колонок. Це завищує оцінку. **Рекомендація:** на боці Секретаря зробити `success` лише коли зміст перевірено ТА ключові колонки заповнено; інакше `partial`. Відповідне місце — `radar.py` рядки 307–318 та `compute_run_status()` у `briefing.py` р. 132–160.

### NEW проти rediscovered (технічний борг)
Дедуп радара працює лише в межах вкладки (чи є рядок із таким `№|дата`). Історія «коли документ вперше виявлено» не ведеться, тому всі нові рядки позначаються як NEW, навіть якщо документ уже бачили раніше. **Потрібен окремий журнал виявлення** (first_seen/last_seen) для коректних «Виявлено»/«Оновлено».

### Заповнення колонок A–O (технічний борг)
Код пише лише **A–J**. Колонки K–O («Вплив на ВОКЛ», «Дія/строк», «Напрям», «Виявлено», «Оновлено») поза діапазоном запису. Частина з них (вплив/дія/напрям) вимагає аналізу змісту (заблоковано 403), а «Виявлено/Оновлено» — журналу виявлення (також відсутній).

### Відомі помилки й технічний борг (зведено)
1. `radar_status` ігнорує перевірку змісту → завищений success.
2. Немає журналу first_seen/last_seen → NEW замість rediscovered.
3. Колонки K–O не заповнюються.
4. Документи не завантажуються (403).
5. Hub «99 — LOG» не інтегрований.
6. Старіші відкриті пункти: конфлікт `sheet_name` у config, оновити `РОЗПОДІЛ_РОБОТИ.md`, каркас реєстрів протоколів/обладнання.

---

## 7. Контрольний звіт — останній прогін

| Параметр | Значення |
|---|---|
| **run_id** | `run_20261006_145108` |
| **started_at** | 2026-10-06T14:51:07Z |
| **finished_at** | 2026-10-06T14:51:33Z (≈17:51 Київ) |
| **window_start** | 2026-10-03T09:36:17Z |
| **overall / RUN STATUS** | success *(за поточною логікою; чесніше — partial, див. §6)* |

**Перевірені джерела:**
- gmail: success (0 записів) [conn:ok] — за вікно нових листів немає
- calendar: success (5 записів) [conn:ok]
- tasks: success (19 записів) [conn:ok]
- researcher: success (16 записів) [conn:ok]

**Handoff:**
- створено нових: **0**; дублів пропущено: **21**; відкинуто неповних: **0**; пропущено реєстрових: 19
- класифікація: new_task=0, update_existing=0, info_only=0, needs_review=0
- storage: success; файл `handoff_2026-10-06.jsonl`

**Радар:**
- рядків записано (total): **5**; додано/оновлено: **5/0**; підтверджено read-back: **5**
- fetch_verified: **0**; docs_downloaded: **0**; radar_status: success
- target: https://docs.google.com/spreadsheets/d/1CJrhOora2_vTMVmwO2iBzGGpCsurx8CwDnR-9DA6Y1M/edit

**Read-back:** canonical checkpoints.md, automation_log.md, handoff_runs.jsonl — підтверджено (прочитано після запису).

**Оновлені checkpoint і журнали:**
- checkpoints.md оновлено: 2026-10-06T14:51:33Z
- handoff_runs.jsonl: run_20261006_145108 записано
- automation_log.md: handoff_pipeline

**Помилки та обмеження:**
- Радар: «джерело НЕ перевірено завантаженням» для 5/5 рядків (офіц. домен у посиланні, контент недоступний з runner — HTTP 403).
- docs_downloaded=0, fetch_verified=0 (див. §6).

---

## Підсумок статусу передачі

### ✅ Що вже надано (з мого боку)
- Увесь код збережено й запушено на `main` (`c97c361`); репозиторій синхронізовано.
- Повна карта ресурсів, ID, інструкції, звіт — цей документ (`docs/HANDOFF_TO_SECRETARY.md`).
- Опис усіх секретів без значень.

### ⏳ Що очікує дії Андрія
1. **Надати ChatGPT/Codex доступ read+write** до репозиторію (Варіант A — collaborator, або Варіант B — fine-grained PAT; §2).
2. Повідомити мені **GitHub-username ChatGPT/Codex** АБО підтвердити використання PAT (без значення токена).
3. За потреби — надати ID/посилання на **Hub «99 — LOG»** для інтеграції.
4. Переконатися, що GitHub Secrets (§5) присутні в репозиторії для роботи scheduler.

### Що саме зробити Андрію, щоб завершити передачу
1. Відкрити https://github.com/buldogin-cloud/Abacus/settings/access → додати акаунт ChatGPT/Codex із роллю **Write** (або згенерувати fine-grained PAT з Contents: RW).
2. Передати ChatGPT/Codex доступ до Google-ресурсів (Drive/Sheets) — спільний доступ до папки Command Center і таблиць радара/реєстру на відповідний акаунт.
3. Повідомити ChatGPT-Секретарю розташування цього документа: `docs/HANDOFF_TO_SECRETARY.md` у репозиторії.

### Умови на період приймання (дотримано)
Код, файли, історія, дані — **не видалено**; чинні доступи — **не відкликано**; робочий процес — **не вимкнено**; нові паралельні checkpoint/журнали — **не створено**; нових архітектурних змін **не внесено**.
