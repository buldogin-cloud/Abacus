"""
radar.py — Радар нормативних документів (Abacus, Req 5).

Відповідальність:
    - Відібрати з researcher-записів НОРМАТИВНІ документи (з номером наказу/постанови).
    - Для кожного: витягти номер, дату, назву, орган, статус, посилання.
    - Додати/оновити РІВНО ОДИН рядок у реєстрі «Радар — нові документи».
    - Дедуплікація за номером+датою (norm:№|дата) та канонічним URL —
      повторні запуски НЕ дублюють рядки.
    - Read-back після запису: рядок вважається доданим лише коли підтверджено
      повторним читанням файлу.

Обмеження (чесно):
    - Фонові джерела (aaukr-дзеркало, google_news) НЕ є офіційним першоджерелом,
      тому verification_status = "unverified_mirror". Поле стає "verified"
      лише після окремого кроку перевірки офіційної сторінки органу
      (ще не автоматизовано) — Secretary бачить це явно.
    - Реєстр зберігається як app-owned файл `radar_normative.jsonl` у теці
      handoffs/secretary (доступно через drive.file). Ручна таблиця Command
      Center недоступна через drive.file (404), тому писати в неї не можна.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from modules.daily_briefing.collector import CollectedData
from modules.daily_briefing.handoff import SECRETARY_FOLDER_ID, _norm_doc_key

RADAR_FILENAME = "radar_normative.jsonl"

# Джерела, у яких шукаємо нормативні документи.
_NORMATIVE_SOURCES = {"moz_ukraine", "nszu", "kmu_cabinet"}

_ORGAN_BY_SOURCE = {
    "moz_ukraine": "МОЗ України",
    "nszu": "НСЗУ",
    "kmu_cabinet": "Кабінет Міністрів України",
}


def _is_normative(record: dict) -> bool:
    """Чи є запис нормативним документом (а не загальною новиною)?"""
    if record.get("source") not in _NORMATIVE_SOURCES:
        return False
    return bool(record.get("order_no")) or record.get("document_type") == "наказ"


def _radar_row(record: dict) -> dict:
    """Зібрати один рядок радара з запису (Req 5: №, дата, назва, орган, статус)."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source = record.get("source", "")
    return {
        "norm_key": _norm_doc_key(record) or "",
        "order_no": record.get("order_no", ""),
        "doc_date": record.get("doc_date") or record.get("published_at", ""),
        "title": record.get("title", ""),
        "organ": _ORGAN_BY_SOURCE.get(source, source),
        "status": record.get("change_status", "NEW"),
        "official_url": record.get("url", ""),
        # Чесно: дзеркало/новина ≠ офіційне першоджерело (Req 2/5).
        "verification_status": "unverified_mirror",
        "source_channel": record.get("source_channel", ""),
        "impact_areas": record.get("potential_impact_areas", []),
        "first_seen_at": now,
        "last_seen_at": now,
        # Файл документа ще не завантажується автоматично (чесно позначаємо).
        "doc_file_drive_id": None,
        "doc_file_status": "not_downloaded",
    }


def _load_existing_radar(drive, folder_id: str) -> tuple[dict | None, dict[str, dict]]:
    """Завантажити існуючий радар. Повертає (file_obj, {norm_key|url: row})."""
    index: dict[str, dict] = {}
    f = drive.find_file(RADAR_FILENAME, folder_id)
    if not f:
        return None, index
    try:
        content = drive.read_file(f["id"]) or ""
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row.get("norm_key") or row.get("official_url")
            if key:
                index[key] = row
    except Exception:
        pass
    return f, index


def process_radar(collected: CollectedData) -> dict[str, Any]:
    """Оновити радар нормативних документів (Req 5).

    Returns:
        {radar_rows_total, radar_rows_added, radar_rows_updated,
         radar_status, readback_ok, radar_file, error}
    """
    result: dict[str, Any] = {
        "radar_rows_total": 0,
        "radar_rows_added": 0,
        "radar_rows_updated": 0,
        "radar_status": "unknown",
        "readback_ok": False,
        "radar_file": RADAR_FILENAME,
        "error": None,
    }

    # Відібрати нормативні записи з усіх джерел.
    normative: list[dict] = []
    for sr in collected.sources.values():
        for rec in sr.records:
            if _is_normative(rec):
                normative.append(rec)

    if not normative:
        result["radar_status"] = "success"
        result["readback_ok"] = True
        return result

    try:
        from integrations.drive_client import DriveClient
        drive = DriveClient()
    except Exception as exc:
        result["radar_status"] = "failed"
        result["error"] = str(exc)
        return result

    folder_id = SECRETARY_FOLDER_ID
    file_obj, index = _load_existing_radar(drive, folder_id)

    for rec in normative:
        row = _radar_row(rec)
        key = row["norm_key"] or row["official_url"]
        if not key:
            continue
        if key in index:
            # Дедуплікація: оновлюємо last_seen_at/статус, НЕ створюємо новий рядок.
            existing = index[key]
            existing["last_seen_at"] = row["last_seen_at"]
            existing["status"] = row["status"]
            if not existing.get("official_url") and row["official_url"]:
                existing["official_url"] = row["official_url"]
            result["radar_rows_updated"] += 1
        else:
            index[key] = row
            result["radar_rows_added"] += 1

    rows = list(index.values())
    result["radar_rows_total"] = len(rows)

    # Запис РІВНО одного файлу-реєстру (кожен рядок — один документ).
    content = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    try:
        file_id = drive.write_file(
            content=content,
            filename=RADAR_FILENAME,
            parent_id=folder_id,
            mime_type="text/plain",
        )
        if not file_id:
            result["radar_status"] = "failed"
            result["error"] = "drive.write_file повернув None"
            return result

        # Read-back: перечитуємо і перевіряємо, що всі ключі присутні.
        rb_file = drive.find_file(RADAR_FILENAME, folder_id)
        rb_content = drive.read_file(rb_file["id"]) if rb_file else ""
        found_keys: set[str] = set()
        for line in (rb_content or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                k = r.get("norm_key") or r.get("official_url")
                if k:
                    found_keys.add(k)
            except json.JSONDecodeError:
                pass

        if set(index.keys()).issubset(found_keys):
            result["radar_status"] = "success"
            result["readback_ok"] = True
        else:
            result["radar_status"] = "partial"
            result["readback_ok"] = False
            result["error"] = "read-back радара неповний"
    except Exception as exc:
        result["radar_status"] = "failed"
        result["error"] = str(exc)

    return result
