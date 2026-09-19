# Генератор робочих документів

Модуль для автоматичної генерації службових документів ВОКЛ у форматі `.docx` з фірмовою шапкою та автоматичним збереженням у відповідні тематичні папки на Google Drive.

## Особливості

✅ **Автоматична шапка** — фірмовий бланк ВОКЛ завантажується з `templates/letterhead.txt`  
✅ **Правильне форматування** — Times New Roman 12pt, відступи, вирівнювання  
✅ **Префікс "АВ"** — усі згенеровані документи отримують префікс `АВ` у назві  
✅ **Розумний маппінг** — документи зберігаються у відповідні папки на Drive за типом  
✅ **Інтеграція з Drive** — автоматичне завантаження після генерації  

---

## Структура файлів

```
modules/doc_generator/
├── __init__.py
├── README.md                      # Ця документація
├── folder_mapping.py              # Маппінг типів документів → папок Drive
├── sluzhbova_generator.py         # Генератор службових записок
└── templates/
    ├── letterhead.txt             # Фірмова шапка ВОКЛ
    └── sluzhbova_zapyska.md       # Шаблон структури службової записки
```

---

## Маппінг типів документів

| Тип документа | Папка на Google Drive | ID папки |
|--------------|----------------------|----------|
| Службові записки | `службові` | `1oOyxVLLvfFcmWVtIGD5EWLeGA3v0Pynx` |
| Звіти | `Звіти` | `1LasnBcejrk7lIwwCb2XEWkM6-30t759b` |
| Розпорядження, Накази | (корінь "Робоча") | `1ggqIXmadUUZe9xO3itcqKFq-3zNqcuRj` |
| Положення | (корінь "Робоча") | `1ggqIXmadUUZe9xO3itcqKFq-3zNqcuRj` |
| Закупівлі (ТЗ) | (корінь "Робоча") | `1ggqIXmadUUZe9xO3itcqKFq-3zNqcuRj` |
| Посадові інструкції | (корінь "Робоча") | `1ggqIXmadUUZe9xO3itcqKFq-3zNqcuRj` |

**Примітка:** Типи документів без окремих папок зберігаються безпосередньо в кореневій робочій папці.

---

## Використання

### 1. Генерація службової записки (локально)

```python
from modules.doc_generator.sluzhbova_generator import generate_sluzhbova

path = generate_sluzhbova(
    addressee=[
        "Медичному директору",
        "КНП «Вінницька обласна лікарня ім. М.І.Пирогова»",
        "Мартинюку В.А.",
    ],
    body_paragraphs=[
        "Повідомляю Вам про необхідність проведення технічного обслуговування...",
    ],
    request_text="Прошу розглянути можливість виділення коштів...",
)

print(f"✅ Документ створено: {path}")
```

### 2. Генерація з автоматичним завантаженням на Drive

```python
path = generate_sluzhbova(
    addressee=["..."],
    body_paragraphs=["..."],
    request_text="...",
    upload_to_drive_flag=True  # ✅ Завантажує на Drive автоматично
)
```

**Результат:**
- Локальний файл: `briefings/sluzhbova_2026-09-19_1938.docx`
- На Google Drive: **`АВ sluzhbova_2026-09-19_1938.docx`** у папці **"службові"**

---

## Формат назв файлів

Усі згенеровані документи автоматично отримують префікс **`АВ`** (Андрій + Abacus):

```
АВ sluzhbova_2026-09-19_1938.docx
АВ zvit_2026-09-20_1045.docx
АВ rozporiadzhennia_2026-09-21_1120.docx
```

**Чому "АВ"?**  
Це лаконічна та непомітна для інших позначка, що дозволяє легко знайти автоматично згенеровані документи серед ваших власних файлів через пошук на Drive.

---

## Розширення функціональності

### Додавання нового типу документа

1. **Додати шаблон** у `templates/` (наприклад, `nakaz.md`)
2. **Створити генератор** (наприклад, `nakaz_generator.py`)
3. **Оновити маппінг** у `folder_mapping.py`:

```python
DOCUMENT_TYPE_FOLDERS = {
    # ...
    "nakaz": "1ggqIXmadUUZe9xO3itcqKFq-3zNqcuRj",  # або ID окремої папки
}
```

4. **Використовувати** функцію `upload_to_drive(local_path, doc_type="nakaz")`

---

## Інтеграція з Telegram Bot

Генератор інтегрується з Telegram webhook для створення документів за голосовими/текстовими запитами:

```python
# У webhook.py або agent_handler.py
from modules.doc_generator.sluzhbova_generator import generate_sluzhbova

# Отримати запит від користувача
# LLM розпарсити структуру (адресат, тіло, прохання)
# Згенерувати документ
path = generate_sluzhbova(
    addressee=extracted_addressee,
    body_paragraphs=extracted_body,
    request_text=extracted_request,
    upload_to_drive_flag=True
)

# Надіслати посилання користувачу у Telegram
```

---

## API

### `generate_sluzhbova()`

Генерує службову записку у форматі `.docx`.

**Параметри:**
- `addressee` (list[str]) — рядки адресата (посада, установа, ПІБ у дав. відмінку)
- `body_paragraphs` (list[str]) — абзаци тексту (факти, обґрунтування)
- `request_text` (str) — текст прохання
- `author_position` (str, optional) — посада автора (за замовчуванням: "Заступник медичного директора з АДІТ")
- `author_name` (str, optional) — ПІБ автора (за замовчуванням: "А.В. Белінський")
- `output_path` (str, optional) — шлях збереження (за замовчуванням: `briefings/`)
- `upload_to_drive_flag` (bool, optional) — чи завантажувати на Drive (за замовчуванням: `False`)

**Повертає:**  
Шлях до створеного `.docx` файлу

---

### `upload_to_drive()`

Завантажує готовий документ на Google Drive.

**Параметри:**
- `local_path` (str) — шлях до локального `.docx` файлу
- `doc_type` (str, optional) — тип документа (за замовчуванням: `"sluzhbova_zapyska"`)

**Повертає:**  
ID файлу на Drive або `None` при помилці

---

## Тестування

```bash
cd /home/ubuntu
python3 test_doc_generator.py
```

**Очікуваний результат:**
```
✅ Завантажено на Drive: АВ sluzhbova_2026-09-19_1938.docx
   ID: 1r9VOIFQLKnDZqmr5Ny_NIs33jY8uNiLK
   URL: https://docs.google.com/document/d/1r9VOIFQLKnDZqmr5Ny_NIs33jY8uNiLK/edit
✅ Документ успішно завантажено на Google Drive
```

---

## Примітки

- 📁 **Папки створені автоматично** — система аналізувала вашу робочу папку на Drive
- 🔒 **Префікс "АВ" обов'язковий** — додається автоматично через `format_filename()`
- 🎯 **Фірмова шапка** — завантажується з `templates/letterhead.txt`
- 📝 **Стиль документа** — Times New Roman 12pt, відступ першого рядка 1.25 см

---

## Майбутні вдосконалення

- [ ] Генератори для інших типів документів (Розпорядження, Накази, Положення)
- [ ] Автоматична реєстрація в `automation_log.md` на Drive
- [ ] Інтеграція з LLM для автоматичного парсингу голосових/текстових запитів
- [ ] Підтримка водяних знаків/меток у самому документі
- [ ] Генерація QR-коду з посиланням на оригінал на Drive
