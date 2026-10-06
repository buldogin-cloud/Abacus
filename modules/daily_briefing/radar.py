"""
radar.py — Нормативний радар у цільовій таблиці Секретаря (Abacus, Req 3).

Пише ПІДТВЕРДЖЕНІ нормативні документи у вкладку
«Радар — нові документи» таблиці
«Реєстр наказів та листів МОЗ — робоча копія для актуалізації 2026».

Колонки вкладки (A..J):
    A ID документа | B Вид | C Орган | D Дата документа | E Номер |
    F Назва | G Офіційне посилання | H Статус / редакція |
    I Джерело статусу | J Що змінилось

Логіка (Req 3):
    1. Відбираємо нормативні записи з номером наказу та ОФІЦІЙНИМ посиланням
       (домен органу влади: moz.gov.ua / zakon.rada.gov.ua / nszu.gov.ua /
       kmu.gov.ua). Дзеркала (aaukr) та google_news самі по собі НЕ є
       підтвердженням — але вони дають офіційний URL першоджерела, який і
       перевіряємо.
    2. Перевіряємо офіційне першоджерело (best-effort GET). Результат чесно
       фіксуємо у колонці «Джерело статусу»:
         - "verified online"  — сторінка відповіла 200 і містить номер;
         - "офіц. посилання; fetch <код>" — домен офіційний, але завантаження
           заблоковане (напр. Cloudflare 403 з раннера).
    3. Дедуплікація за ключем №|дата у вже наявних рядках вкладки —
       додаємо новий АБО оновлюємо рівно один рядок, без дублікатів.
    4. READ-BACK: після кожного запису перечитуємо відповідний рядок.
    5. radar_status = success ЛИШЕ якщо у вкладці є підтверджені рядки,
       підтверджені повторним читанням. Якщо нормативних документів немає —
       status = skipped. Якщо запис/читання не вдалося — partial/failed.

Чесні обмеження:
    - Завантаження файлів документів (PDF) з moz.gov.ua заблоковане Cloudflare
      з цього раннера (403) → doc_file не зберігається, Drive ID НЕ вигадується.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from modules.daily_briefing.collector import CollectedData
from modules.daily_briefing.canonical import (
    RADAR_SHEET_TITLE,
    RADAR_SPREADSHEET_ID,
    sheet_tab_url,
)

# Офіційні домени органів влади (першоджерела).
_OFFICIAL_DOMAINS = {
    "moz.gov.ua", "www.moz.gov.ua",
    "zakon.rada.gov.ua", "www.zakon.rada.gov.ua",
    "nszu.gov.ua", "www.nszu.gov.ua",
    "kmu.gov.ua", "www.kmu.gov.ua",
}

_NORMATIVE_SOURCES = {"moz_ukraine", "nszu", "kmu_cabinet"}

_ORGAN_BY_SOURCE = {
    "moz_ukraine": "МОЗ України",
    "nszu": "НСЗУ",
    "kmu_cabinet": "Кабінет Міністрів України",
}


def _is_official_url(url: str) -> bool:
    """Чи є URL посиланням на офіційний домен органу влади."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _OFFICIAL_DOMAINS


def _is_normative(record: dict) -> bool:
    """Нормативний документ з номером наказу та офіційним посиланням."""
    if record.get("source") not in _NORMATIVE_SOURCES:
        return False
    if not record.get("order_no"):
        return False
    return _is_official_url(record.get("url", ""))


def _norm_key(order_no: str, doc_date: str) -> str:
    return f"{(order_no or '').strip()}|{(doc_date or '').strip()}"


def _clean_official_url(url: str) -> str:
    """Прибрати трекінгові параметри (fbclid, utm_*, aem тощо) з офіц. посилання."""
    if not url:
        return url
    try:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        p = urlparse(url)
        if not p.query:
            return url
        kept = [
            (k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
            if not (k.lower() == "fbclid" or k.lower().startswith("utm_")
                    or k.lower() in ("aem", "_aem", "fbclid"))
        ]
        return urlunparse(p._replace(query=urlencode(kept)))
    except Exception:
        return url


def _verify_official_source(url: str, order_no: str) -> tuple[str, bool]:
    """Best-effort перевірка офіційного першоджерела (Req 3).

    Повертає (нота_для_колонки_I, verified_online).
    Чесно: якщо завантаження заблоковане — verified_online=False, у ноті код.
    """
    try:
        import requests
        r = requests.get(
            url, timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        code = r.status_code
        if code == 200 and order_no and order_no in r.text:
            return (f"офіц. джерело (verified online, HTTP 200, №{order_no})", True)
        if code == 200:
            return ("офіц. джерело (HTTP 200, номер не знайдено у тексті)", False)
        return (f"офіц. посилання; автозавантаження заблоковане (HTTP {code})", False)
    except Exception as exc:
        return (f"офіц. посилання; перевірка недоступна ({type(exc).__name__})", False)


def _build_row(record: dict, status_note: str, change_note: str) -> list[str]:
    """Зібрати рядок з 10 колонок під заголовки вкладки."""
    source = record.get("source", "")
    organ = _ORGAN_BY_SOURCE.get(source, source)
    order_no = (record.get("order_no") or "").strip()
    doc_date = (record.get("doc_date") or record.get("published_at") or "").strip()
    organ_code = {"МОЗ України": "МОЗ", "НСЗУ": "НСЗУ",
                  "Кабінет Міністрів України": "КМУ"}.get(organ, "DOC")
    doc_id = f"{organ_code}-{order_no}-{doc_date}"
    # Назва без службового префікса "№.. від.." (він уже рознесений по колонках).
    title = record.get("title", "")
    return [
        doc_id,                                   # A ID документа
        record.get("document_type", "наказ"),     # B Вид
        organ,                                     # C Орган
        doc_date,                                  # D Дата документа
        order_no,                                  # E Номер
        title,                                     # F Назва
        _clean_official_url(record.get("url", "")), # G Офіційне посилання
        record.get("change_status", "NEW"),        # H Статус / редакція
        status_note,                               # I Джерело статусу
        change_note,                               # J Що змінилось
    ]


def _sheets_service():
    from integrations.google_auth import get_credentials
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def process_radar(collected: CollectedData) -> dict[str, Any]:
    """Оновити нормативний радар у цільовій вкладці (Req 3)."""
    result: dict[str, Any] = {
        "radar_rows_total": 0,       # рядків у вкладці після запису
        "radar_rows_added": 0,
        "radar_rows_updated": 0,
        "radar_confirmed": 0,        # підтверджено read-back
        "radar_fetch_verified": 0,   # перевірено живим завантаженням офіц. сторінки
        "radar_docs_downloaded": 0,  # файли документів (Req 3) — чесно
        "radar_status": "unknown",
        "readback_ok": False,
        "radar_target": sheet_tab_url(RADAR_SPREADSHEET_ID, RADAR_SHEET_TITLE),
        "error": None,
        "errors": [],
    }

    # Відбір нормативних записів (з номером + офіційним посиланням).
    normative: list[dict] = []
    for sr in collected.sources.values():
        for rec in sr.records:
            if _is_normative(rec):
                normative.append(rec)

    if not normative:
        # Нічого нормативного — це НЕ success із записом, а чесний skipped.
        result["radar_status"] = "skipped"
        result["readback_ok"] = True
        return result

    try:
        svc = _sheets_service()
        tab = RADAR_SHEET_TITLE
        sid = RADAR_SPREADSHEET_ID
    except Exception as exc:
        result["radar_status"] = "failed"
        result["error"] = str(exc)
        result["errors"].append(f"sheets_init: {exc}")
        return result

    # 1. Прочитати наявні рядки вкладки та побудувати індекс №|дата → row_number.
    try:
        existing = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A2:J100000"
        ).execute().get("values", [])
    except Exception as exc:
        result["radar_status"] = "failed"
        result["error"] = f"read tab: {exc}"
        result["errors"].append(f"read_tab: {exc}")
        return result

    index: dict[str, int] = {}
    for i, row in enumerate(existing):
        # row[4] = Номер (E), row[3] = Дата (D)
        num = row[4] if len(row) > 4 else ""
        date = row[3] if len(row) > 3 else ""
        key = _norm_key(num, date)
        if key.strip("|"):
            index[key] = i + 2  # 1-based + header

    # 2. Дедуплікувати ВХІДНІ записи за ключем (щоб не писати двічі в одному прогоні).
    seen_in_run: dict[str, dict] = {}
    for rec in normative:
        key = _norm_key(rec.get("order_no", ""),
                        rec.get("doc_date") or rec.get("published_at", ""))
        if key not in seen_in_run:
            seen_in_run[key] = rec

    confirmed_keys: set[str] = set()

    for key, rec in seen_in_run.items():
        order_no = (rec.get("order_no") or "").strip()
        url = rec.get("url", "")
        status_note, verified_online = _verify_official_source(url, order_no)
        channel = rec.get("source_channel", "")
        if channel:
            status_note = f"{status_note} [канал: {channel}]"
        if verified_online:
            result["radar_fetch_verified"] += 1

        try:
            if key in index:
                # ОНОВЛЕННЯ рівно одного наявного рядка (без дублю).
                row_num = index[key]
                row_vals = _build_row(rec, status_note, "Оновлено (повторно виявлено)")
                rng = f"'{tab}'!A{row_num}:J{row_num}"
                svc.spreadsheets().values().update(
                    spreadsheetId=sid, range=rng,
                    valueInputOption="RAW", body={"values": [row_vals]},
                ).execute()
                result["radar_rows_updated"] += 1
            else:
                # ДОДАВАННЯ нового рядка.
                row_vals = _build_row(rec, status_note, "Додано в радар")
                resp = svc.spreadsheets().values().append(
                    spreadsheetId=sid, range=f"'{tab}'!A:J",
                    valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                    body={"values": [row_vals]},
                ).execute()
                rng = resp["updates"]["updatedRange"]
                # Запам'ятати row_number з updatedRange для індексу.
                index[key] = int(rng.split("!")[1].split(":")[0][1:])
                result["radar_rows_added"] += 1

            # READ-BACK рівно цього рядка (Req 3).
            rb = svc.spreadsheets().values().get(
                spreadsheetId=sid, range=rng
            ).execute().get("values", [])
            rb_row = rb[0] if rb else []
            rb_num = rb_row[4] if len(rb_row) > 4 else ""
            rb_date = rb_row[3] if len(rb_row) > 3 else ""
            if _norm_key(rb_num, rb_date) == key:
                result["radar_confirmed"] += 1
                confirmed_keys.add(key)
            else:
                result["errors"].append(f"readback_mismatch: {key}")
        except Exception as exc:
            result["errors"].append(f"write[{key}]: {exc}")

    # 3. Порахувати підсумкову кількість рядків у вкладці.
    try:
        after = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A2:A100000"
        ).execute().get("values", [])
        result["radar_rows_total"] = len([r for r in after if r and r[0].strip()])
    except Exception:
        result["radar_rows_total"] = len(index)

    # Прозорість (Req 3, честь): якщо офіційне джерело не вдалося перевірити
    # «живим» завантаженням (напр., Cloudflare блокує runner) — фіксуємо це
    # явно. Посилання офіційного домену записані, але контент не завантажено.
    if result["radar_fetch_verified"] < len(confirmed_keys):
        result["errors"].append(
            f"джерело НЕ перевірено завантаженням для "
            f"{len(confirmed_keys) - result['radar_fetch_verified']}/{len(confirmed_keys)} "
            f"рядків (офіц. домен у посиланні, але контент недоступний з runner); "
            f"завантажено документів: {result['radar_docs_downloaded']}"
        )

    # 4. Статус радара (Req 3/6): success ЛИШЕ якщо є підтверджені рядки.
    if confirmed_keys and len(confirmed_keys) == len(seen_in_run):
        result["radar_status"] = "success"
        result["readback_ok"] = True
    elif confirmed_keys:
        result["radar_status"] = "partial"
        result["readback_ok"] = False
        result["error"] = (
            f"підтверджено {len(confirmed_keys)}/{len(seen_in_run)} рядків радара"
        )
    else:
        result["radar_status"] = "failed"
        result["readback_ok"] = False
        result["error"] = "жоден рядок радара не підтверджено read-back"

    return result
