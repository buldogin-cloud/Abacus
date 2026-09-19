"""
Маппінг типів документів до папок на Google Drive
"""

# ID кореневої робочої папки
ROOT_WORK_FOLDER = '1ggqIXmadUUZe9xO3itcqKFq-3zNqcuRj'

# Маппінг типів документів до ID папок на Drive
DOCUMENT_TYPE_FOLDERS = {
    # Типи документів з окремими папками
    "sluzhbova_zapyska": "1oOyxVLLvfFcmWVtIGD5EWLeGA3v0Pynx",  # папка "службові"
    "zvit": "1LasnBcejrk7lIwwCb2XEWkM6-30t759b",               # папка "Звіти"
    
    # Типи документів без окремих папок (зберігаються в корені)
    "rozporiadzhennia": ROOT_WORK_FOLDER,  # Розпорядження
    "nakaz": ROOT_WORK_FOLDER,              # Накази
    "polozhennia": ROOT_WORK_FOLDER,        # Положення
    "zakupivli_tz": ROOT_WORK_FOLDER,       # Технічні завдання для закупівель
    "posadova_instruktsiia": ROOT_WORK_FOLDER,  # Посадові інструкції
    "list": ROOT_WORK_FOLDER,               # Листи
    "klopotannia": ROOT_WORK_FOLDER,        # Клопотання
    
    # Спеціалізовані папки
    "dokument_vokl": "1qs92ASAqU_Cum5_eOY2mQTXhQZPwGrJm",  # Документи ВОКЛ
    "dokument_moz": "1nEMks27EQxeAUsfMAVVC06n-C-ohlhax",   # Документи ДОЗ, МОЗ, відповіді на листи
}

# Префікс для автоматично згенерованих документів
AUTO_PREFIX = "АВ"


def get_folder_id(doc_type):
    """
    Отримати ID папки для заданого типу документа
    
    Args:
        doc_type: Тип документа (наприклад, "sluzhbova_zapyska")
        
    Returns:
        ID папки на Google Drive або None якщо тип не знайдено
    """
    return DOCUMENT_TYPE_FOLDERS.get(doc_type, ROOT_WORK_FOLDER)


def format_filename(base_name, doc_type=None):
    """
    Форматувати назву файлу з префіксом АВ
    
    Args:
        base_name: Базова назва файлу (без префіксу)
        doc_type: Тип документа (опціонально)
        
    Returns:
        Назва файлу з префіксом АВ
    """
    # Видаляємо розширення якщо є
    if base_name.endswith('.docx'):
        base_name = base_name[:-5]
    
    # Додаємо префікс якщо його ще немає
    if not base_name.startswith(AUTO_PREFIX):
        return f"{AUTO_PREFIX} {base_name}.docx"
    
    return f"{base_name}.docx"
