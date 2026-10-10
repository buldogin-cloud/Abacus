"""
radar.py — Нормативний радар у цільовій таблиці Секретаря (Abacus, Req 3).

Пише лише ПІДТВЕРДЖЕНІ нормативні документи у вкладку
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
    3. Дедуплікація за парою «ID документа + нормалізований офіційний URL».
       Повторне виявлення не перезаписує наявний стан рядка як NEW.
    4. READ-BACK: після кожного запису перечитуємо відповідний рядок.
    5. source_verified і write_verified — різні сигнали. radar_status = success
       ЛИШЕ коли всі кандидати перевірені у першоджерелі та кожен новий запис
       підтверджено повторним читанням. HTTP 403 → partial + unresolved.

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


def _document_id(record: dict) -> str:
    """Стабільний ID документа, який також записується в колонку A."""
    source = record.get("source", "")
    organ = _ORGAN_BY_SOURCE.get(source, source)
    organ_code = {
        "МОЗ України": "МОЗ",
        "НСЗУ": "НСЗУ",
        "Кабінет Міністрів України": "КМУ",
    }.get(organ, "DOC")
    order_no = (record.get("order_no") or "").strip()
    doc_date = (record.get("doc_date") or record.get("published_at") or "").strip()
    return f"{organ_code}-{order_no}-{doc_date}"


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


def _provenance_key(record: dict) -> tuple[str, str]:
    """Канонічний ключ походження: ID документа + очищений official URL."""
    return _document_id(record), _clean_official_url(record.get("url", ""))


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
    doc_id = _document_id(record)
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
        "radar_rows_unchanged": 0,   # повтори, які не перезаписували рядок
        "radar_confirmed": 0,        # compatibility alias для persistence_verified
        "radar_source_verified": 0,  # зміст підтверджено у першоджерелі
        "radar_write_verified": 0,   # новий запис підтверджено read-back
        "radar_persistence_verified": 0,  # новий або наявний рядок прочитано
        "radar_fetch_verified": 0,   # перевірено живим завантаженням офіц. сторінки
        "radar_docs_downloaded": 0,  # файли документів (Req 3) — чесно
        "radar_unresolved": [],      # неперевірені leads; downstream їх не виконує
        "radar_unresolved_count": 0,
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

    # Спочатку перевіряємо першоджерела. Неперевірені leads не мають права
    # створювати чи перезаписувати рядки shared Sheet.
    verified_candidates: list[tuple[dict, str]] = []
    seen_in_run: dict[tuple[str, str], dict] = {}
    for rec in normative:
        key = _provenance_key(rec)
        if key not in seen_in_run:
            seen_in_run[key] = rec

    for key, rec in seen_in_run.items():
        order_no = (rec.get("order_no") or "").strip()
        status_note, source_verified = _verify_official_source(rec.get("url", ""), order_no)
        channel = rec.get("source_channel", "")
        if channel:
            status_note = f"{status_note} [канал: {channel}]"
        if source_verified:
            result["radar_source_verified"] += 1
            result["radar_fetch_verified"] += 1
            verified_candidates.append((rec, status_note))
        else:
            result["radar_unresolved"].append({
                "document_id": key[0],
                "official_url": key[1],
                "order_no": order_no,
                "reason": status_note,
                "state": "unresolved",
            })

    result["radar_unresolved_count"] = len(result["radar_unresolved"])
    if not verified_candidates:
        result["radar_status"] = "partial"
        result["error"] = (
            f"0/{len(seen_in_run)} нормативних кандидатів підтверджено "
            "у першоджерелі; записи залишено unresolved"
        )
        result["errors"].append(result["error"])
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

    # 1. Індекси provenance. Legacy №|дата лишається тільки як запобіжник
    # від дублювання старих рядків; такі рядки не перезаписуємо автоматично.
    try:
        existing = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A2:J100000"
        ).execute().get("values", [])
    except Exception as exc:
        result["radar_status"] = "failed"
        result["error"] = f"read tab: {exc}"
        result["errors"].append(f"read_tab: {exc}")
        return result

    provenance_index: dict[tuple[str, str], list[int]] = {}
    document_index: dict[str, list[int]] = {}
    url_index: dict[str, list[int]] = {}
    legacy_index: dict[str, list[int]] = {}
    for i, row in enumerate(existing):
        doc_id = row[0].strip() if len(row) > 0 else ""
        url = _clean_official_url(row[6].strip()) if len(row) > 6 else ""
        num = row[4] if len(row) > 4 else ""
        date = row[3] if len(row) > 3 else ""
        row_num = i + 2
        if doc_id and url:
            provenance_index.setdefault((doc_id, url), []).append(row_num)
        if doc_id:
            document_index.setdefault(doc_id, []).append(row_num)
        if url:
            url_index.setdefault(url, []).append(row_num)
        legacy = _norm_key(num, date)
        if legacy.strip("|"):
            legacy_index.setdefault(legacy, []).append(row_num)

    persisted_keys: set[tuple[str, str]] = set()

    for rec, status_note in verified_candidates:
        key = _provenance_key(rec)
        doc_id, clean_url = key
        legacy = _norm_key(
            rec.get("order_no", ""),
            rec.get("doc_date") or rec.get("published_at", ""),
        )

        try:
            did_write = False
            expected_written_row: list[str] | None = None
            exact_rows = provenance_index.get(key, [])
            if len(exact_rows) > 1:
                result["radar_rows_unchanged"] += len(exact_rows)
                result["radar_unresolved"].append({
                    "document_id": doc_id,
                    "official_url": clean_url,
                    "order_no": (rec.get("order_no") or "").strip(),
                    "reason": f"duplicate provenance rows: {exact_rows}",
                    "state": "unresolved",
                })
                continue

            row_num = exact_rows[0] if exact_rows else None
            if row_num is None:
                conflict_rows = set(document_index.get(doc_id, []))
                conflict_rows.update(url_index.get(clean_url, []))
                conflict_rows.update(legacy_index.get(legacy, []))
                if conflict_rows:
                    result["radar_rows_unchanged"] += 1
                    result["radar_unresolved"].append({
                        "document_id": doc_id,
                        "official_url": clean_url,
                        "order_no": (rec.get("order_no") or "").strip(),
                        "reason": (
                            "provenance conflict with existing/legacy rows: "
                            f"{sorted(conflict_rows)}"
                        ),
                        "state": "unresolved",
                    })
                    continue

            if row_num is not None:
                # Repeat/conflict: тільки read-back. Не змінюємо historical state,
                # зокрема H=NEW, поки material change незалежно не доведено.
                rng = f"'{tab}'!A{row_num}:J{row_num}"
                result["radar_rows_unchanged"] += 1
            else:
                # Додаємо лише source-verified документ.
                row_vals = _build_row(rec, status_note, "Додано в радар")
                resp = svc.spreadsheets().values().append(
                    spreadsheetId=sid, range=f"'{tab}'!A:J",
                    valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                    body={"values": [row_vals]},
                ).execute()
                rng = resp["updates"]["updatedRange"]
                row_num = int(rng.split("!")[1].split(":")[0][1:])
                provenance_index[key] = [row_num]
                document_index.setdefault(doc_id, []).append(row_num)
                url_index.setdefault(clean_url, []).append(row_num)
                result["radar_rows_added"] += 1
                did_write = True
                expected_written_row = row_vals

            # READ-BACK рівно цього рядка (Req 3).
            rb = svc.spreadsheets().values().get(
                spreadsheetId=sid, range=rng
            ).execute().get("values", [])
            rb_row = rb[0] if rb else []
            rb_doc_id = rb_row[0].strip() if len(rb_row) > 0 else ""
            rb_url = _clean_official_url(rb_row[6].strip()) if len(rb_row) > 6 else ""
            provenance_matches = (rb_doc_id, rb_url) == key
            full_write_matches = (
                expected_written_row is not None
                and [str(value) for value in rb_row[:10]] == expected_written_row
            )
            if provenance_matches and (not did_write or full_write_matches):
                result["radar_persistence_verified"] += 1
                persisted_keys.add(key)
                if did_write:
                    result["radar_write_verified"] += 1
            else:
                result["errors"].append(f"readback_mismatch: {doc_id}|{clean_url}")
        except Exception as exc:
            result["errors"].append(f"persistence[{doc_id}|{clean_url}]: {exc}")

    # Backward-compatible meaning: every row whose persistence was confirmed,
    # including a safe repeat that required no write.
    result["radar_confirmed"] = result["radar_persistence_verified"]
    result["radar_unresolved_count"] = len(result["radar_unresolved"])

    # 3. Порахувати підсумкову кількість рядків у вкладці.
    try:
        after = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A2:A100000"
        ).execute().get("values", [])
        result["radar_rows_total"] = len([r for r in after if r and r[0].strip()])
    except Exception:
        result["radar_rows_total"] = len(provenance_index)

    # Прозорість (Req 3, честь): якщо офіційне джерело не вдалося перевірити
    # «живим» завантаженням (напр., Cloudflare блокує runner) — фіксуємо це
    # явно. Посилання офіційного домену записані, але контент не завантажено.
    # 4. Success вимагає source verification ДЛЯ ВСІХ кандидатів і фактичної
    # persistence/read-back для кожного verified документа.
    all_sources_verified = result["radar_source_verified"] == len(seen_in_run)
    all_persisted = len(persisted_keys) == len(verified_candidates)
    if all_sources_verified and all_persisted and not result["errors"]:
        result["radar_status"] = "success"
        result["readback_ok"] = True
    elif persisted_keys or result["radar_unresolved_count"]:
        result["radar_status"] = "partial"
        result["readback_ok"] = all_persisted
        result["error"] = (
            f"source_verified={result['radar_source_verified']}/{len(seen_in_run)}, "
            f"persistence_verified={len(persisted_keys)}/{len(verified_candidates)}, "
            f"unresolved={result['radar_unresolved_count']}"
        )
    else:
        result["radar_status"] = "failed"
        result["readback_ok"] = False
        result["error"] = "жоден рядок радара не підтверджено read-back"

    return result
