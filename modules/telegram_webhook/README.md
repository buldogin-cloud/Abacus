# Telegram Webhook — Обробник повідомлень

Цей модуль дозволяє боту Abacus отримувати та обробляти повідомлення з Telegram у реальному часі.

## Архітектура

```
Користувач у Telegram
         ↓
    @AbacusSECbot
         ↓
  [Telegram API]
         ↓
    [Webhook URL]
         ↓
  webhook.py (обробник)
         ↓
  agent_handler.py (логіка)
         ↓
    [Відповідь]
         ↓
  Telegram API
         ↓
  Користувач отримує відповідь
```

## Компоненти

### `webhook.py`
- **TelegramWebhookHandler** — основний обробник
- `process_update()` — обробка оновлення від Telegram
- `_send_message()` — надсилання відповіді

### `agent_handler.py`
- `get_agent_response()` — логіка обробки повідомлень
- Простий інтелект на основі ключових слів
- Легко розширюється для більш складної логіки

## Як це працює

1. **Користувач пише боту** → Telegram надсилає POST запит на ваш webhook URL
2. **Webhook обробляє** → `webhook.py` парсить повідомлення
3. **Агент відповідає** → `agent_handler.py` генерує відповідь
4. **Бот відповідає** → Відповідь надсилається назад користувачу

## Налаштування

### Крок 1: Запустити webhook сервер

```bash
python modules/telegram_webhook/server.py
```

Це запустить Flask сервер на `http://localhost:8443` (або іншому порту).

### Крок 2: Відкрити webhook наружу (ngrok або подібне)

Telegram не може звертатися до `localhost`, тому потрібна публічна URL:

```bash
# Встановити ngrok
brew install ngrok  # або скачати з https://ngrok.com

# Запустити
ngrok http 8443
```

Ви отримаєте URL типу `https://abc123.ngrok.io`

### Крок 3: Зареєструвати webhook в Telegram

```bash
curl -X POST https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook \
  -F "url=https://abc123.ngrok.io/webhook"
```

### Крок 4: Перевірити

Напишіть боту — він повинен відповісти.

## Команди

- `/start` — Привітання
- `/help` — Справка
- `/tasks` — Показати завдання
- `/briefing` — Останній брифінг
- `/status` — Статус системи

## Розширення

Щоб додати нову логіку:

1. Відредагуйте `agent_handler.py` → `get_agent_response()`
2. Додайте новий умовний блок для обробки запиту
3. Повертайте текст відповіді

Приклад:

```python
elif "погода" in text_lower:
    return get_weather_from_api()  # ваша функція
```

## GitHub Actions

Для запуску webhook в GitHub Actions створіть workflow:

```yaml
name: Telegram Webhook Server

on:
  workflow_dispatch:  # Ручний запуск

jobs:
  webhook:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Встановлення залежностей
        run: pip install -r requirements.txt
      - name: Запуск webhook
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        run: python modules/telegram_webhook/server.py
```

## Проблеми

### Бот не відповідає
- Перевірити чи webhook сервер працює
- Перевірити чи webhook URL зареєстрована в Telegram
- Подивитися на логи: `python -m pdb modules/telegram_webhook/server.py`

### Помилка "Cannot find socket"
- Перевірити чи порт не зайнятий: `lsof -i :8443`
- Змінити порт у `server.py`

### Telegram говорить "Invalid URL"
- URL повинна бути HTTPS (не HTTP)
- URL повинна бути публічна (не localhost)
- Скористайтеся ngrok, Vercel, або подібним сервісом

## Стан розробки

✅ Базова обробка повідомлень  
✅ Команди (/start, /help, тощо)  
🔄 Інтеграція з Google Sheets (завдання)  
🔄 Інтеграція з Gmail  
🔄 Інтеграція з Calendar  
⏳ Розширена AI логіка

