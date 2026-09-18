# Модуль «Щоденний брифінг» (Daily Briefing)

Формує структурований щоденний брифінг у markdown-форматі, збираючи дані з:

- **Gmail** — непрочитані та важливі листи за останні 24 години;
- **Google Calendar** — події на сьогодні та найближчі дні;
- **Google Sheets** — відкриті та прострочені завдання;
- **Дослідник** — дайджест оновлень МОЗ / НСЗУ (за потреби).

## Запуск

```bash
python modules/daily_briefing/briefing.py            # зберегти у briefings/
python modules/daily_briefing/briefing.py --stdout   # ще й вивести у консоль
python modules/daily_briefing/briefing.py --config config.yaml
```

Результат зберігається у `briefings/briefing_YYYY-MM-DD.md`.

## Конфігурація

Скопіюйте `config.example.yaml` у корінь проекту як `config.yaml` та заповніть:

- `tasks.spreadsheet_id` — ID таблиці Google Sheets із завданнями;
- `researcher.keywords` — ключові слова для фільтрації новин;
- `delivery.send_email` / `email_to` — за потреби надсилати брифінг поштою.

## Стійкість до збоїв

Кожне джерело обробляється незалежно: якщо, наприклад, недоступний
Google Sheets, решта секцій (пошта, календар) все одно потраплять у брифінг,
а проблемна секція міститиме позначку про помилку.
