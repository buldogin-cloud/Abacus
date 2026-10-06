"""canonical.py — Єдине джерело правди для ID канонічних ресурсів Command Center.

Вимога Секретаря (Req 2): НЕ створювати паралельних checkpoint/журналів.
Усі модулі (reader/writer/radar/briefing) посилаються ТІЛЬКИ на ці ID.

Канонічні файли лежать у корені Command Center:
    Command Center/
        checkpoints.md        ← 1leDnfV8IkLiceOTU7Cc-iHqLprtsUtXy
        automation_log.md     ← 1pPoB1qKlZhN3g61tXkLtQb385M-Zjbxc
        handoff_runs.jsonl    ← 189AmK-uQRqpUwboDBRO3gMjS8dyZDtCU
        handoffs/             ← 1tmeJKjy_P-T38AKXHv9LtdjjqOFf4lgl
            secretary/        ← 1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj
                handoff_YYYY-MM-DD.jsonl

ПАРАЛЕЛЬНІ копії у теці handoffs/ (checkpoints.md, automation_log.md,
handoff_runs.jsonl) — застарілі, НЕ є джерелом правди, прибрані (trashed).
"""

from __future__ import annotations

# Корінь Command Center
COMMAND_CENTER_ROOT_ID = "1eh47d2AtLZwfuYdthzVfJ-5pz_x2Qgf5"

# Канонічні файли (писати/читати ТІЛЬКИ за цими ID)
CHECKPOINTS_FILE_ID = "1leDnfV8IkLiceOTU7Cc-iHqLprtsUtXy"
AUTOMATION_LOG_FILE_ID = "1pPoB1qKlZhN3g61tXkLtQb385M-Zjbxc"
HANDOFF_RUNS_FILE_ID = "189AmK-uQRqpUwboDBRO3gMjS8dyZDtCU"

# Теки для handoff-даних (не канонічні журнали — це корисне навантаження)
HANDOFFS_FOLDER_ID = "1tmeJKjy_P-T38AKXHv9LtdjjqOFf4lgl"
SECRETARY_FOLDER_ID = "1X9GvVOAo9iIWHrrua0Un6S9ytiScpEmj"

# Реєстр завдань Секретаря (read-only для Abacus)
TASKS_REGISTRY_SHEET_ID = "11oqxqRm7cAH6jpKf9T7CYtIY0j01LAV259F7NGkd5so"

# Нормативний радар — цільова таблиця та вкладка (Req 3)
RADAR_SPREADSHEET_ID = "1CJrhOora2_vTMVmwO2iBzGGpCsurx8CwDnR-9DA6Y1M"
RADAR_SHEET_TITLE = "Радар — нові документи"
RADAR_SPREADSHEET_TITLE = "Реєстр наказів та листів МОЗ — робоча копія для актуалізації 2026"

# Застарілі паралельні копії у теці handoffs/ (прибираються, не використовуються)
STALE_PARALLEL_FILE_IDS = {
    "handoffs/checkpoints.md": "1Ohp9ihVWF7qpkto4ukkCoEoOmD6vdNm5",
    "handoffs/automation_log.md": "1Gywh7HB5eCZSp6Ph2ShkdadjNCqHX1Cv",
    "handoffs/handoff_runs.jsonl": "14KK2s0p8boxe_908heagvkMpcm6BerQK",
}


def drive_file_url(file_id: str) -> str:
    """Посилання на файл Drive для показу у summary (Req 5)."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def sheet_tab_url(spreadsheet_id: str, sheet_title: str = "") -> str:
    """Посилання на таблицю (вкладку) радару для summary."""
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
