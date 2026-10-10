"""Оркестратор handoff-пайплайну (Abacus, Phase 1).

Abacus — технічний фоновий агент. Цей скрипт:

    1. Зчитує checkpoints з Drive → визначає window_start
    2. Запускає Collector — збирає сирі дані (Gmail / Calendar / Tasks / Researcher)
    3. Запускає Handoff — дедуплікує, класифікує, записує SECRETARY_HANDOFF на Drive
    4. Оновлює checkpoints (реальні статуси джерел)
    5. Записує структурований лог прогону (handoff_runs.jsonl + automation_log.md)
    6. Надсилає у Telegram короткий summary — НЕ брифінг

НЕ виконує:
    - НЕ інтерпретує зміст листів / документів
    - НЕ формує управлінський брифінг (це робить Secretary)
    - НЕ змінює реєстр завдань, дедлайни, статуси, Calendar

Формат handoff-файлу на Drive:
    Command Center / handoffs / secretary / handoff_YYYY-MM-DD.jsonl

ChatGPT Secretary читає handoff → оновлює реєстр → формує брифінг.

Запуск:
    python modules/daily_briefing/briefing.py [--config config.yaml]
    python modules/daily_briefing/briefing.py --stdout          # детальна статистика
    python modules/daily_briefing/briefing.py --no-drive        # без Drive (тест)
    python modules/daily_briefing/briefing.py --stdout --no-drive
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Дозволяємо запуск як окремого скрипта (корінь проекту у sys.path).
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from modules.daily_briefing.checkpoints_util import CheckpointsReader    # noqa: E402
from modules.daily_briefing.checkpoints_writer import CheckpointsWriter  # noqa: E402
from modules.daily_briefing.collector import CollectedData, collect_all  # noqa: E402
from modules.daily_briefing.handoff import process_handoffs              # noqa: E402
from modules.daily_briefing.radar import process_radar                   # noqa: E402


# ────────────────────────────────────────────────────────────
# Конфігурація
# ────────────────────────────────────────────────────────────

def load_config(path: str | None) -> dict[str, Any]:
    """Завантажує конфігурацію з YAML-файлу.

    Порядок пошуку:
        1. Шлях із аргументу --config
        2. config.yaml у корені проекту
        3. modules/daily_briefing/config.example.yaml (fallback)
    """
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(_ROOT / "config.yaml")
    candidates.append(_ROOT / "modules" / "daily_briefing" / "config.example.yaml")

    for candidate in candidates:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                print(f"[i] Конфігурація: {candidate}")
                return yaml.safe_load(f) or {}
    print("[!] config.yaml не знайдено, використовуємо порожній конфіг")
    return {}


# ────────────────────────────────────────────────────────────
# Вікно збору
# ────────────────────────────────────────────────────────────

def get_window_start(checkpoints: dict) -> str:
    """Визначити window_start з checkpoints.

    Бере мінімальний (найстаріший) timestamp серед успішних перевірок,
    щоб не пропустити жодного джерела. Fallback — 24 години тому.
    """
    from datetime import timedelta

    fallback = (
        datetime.now(timezone.utc) - timedelta(hours=24)
    ).isoformat().replace("+00:00", "Z")

    keys = [
        "gmail_last_success",
        "calendar_last_success",
        "tasks_last_success",
        "researcher_last_success",
    ]
    timestamps = []
    for k in keys:
        v = checkpoints.get(k, "")
        if v and v not in ("N/A", ""):
            timestamps.append(v)

    if not timestamps:
        return fallback

    # Найстаріший timestamp — найширше вікно → нічого не пропустимо
    return min(timestamps)


# ────────────────────────────────────────────────────────────
# Допоміжні функції
# ────────────────────────────────────────────────────────────

def _sources_status_dict(collected: CollectedData) -> dict:
    """Побудувати {source: bool} для update_checkpoints()."""
    return {
        source: result.status == "success"
        for source, result in collected.sources.items()
    }


def _gmail_connection_ok(collected: CollectedData) -> bool:
    """Чи підтверджено з'єднання з Gmail (Req 1/6)."""
    gmail = collected.sources.get("gmail")
    if gmail is None:
        return False
    return bool(getattr(gmail, "connection_verified", False))


def compute_run_status(collected: CollectedData, handoff_result: dict) -> str:
    """Чесний підсумковий статус прогону (Req 6).

    success — ЛИШЕ коли виконано ВСІ умови:
        • Gmail доступний (connection_verified);
        • збір джерел success;
        • handoff записано і підтверджено read-back;
        • радар: success або skipped (немає нормативних док.), з підтвердженими рядками;
        • немає критичних помилок запису.
    Інакше — partial (або failed, якщо все впало).
    """
    overall = collected.overall_status()
    storage = handoff_result.get("storage_status", "unknown")
    readback_ok = handoff_result.get("readback_ok", False)
    radar_status = handoff_result.get("radar_status", "unknown")
    gmail_ok = _gmail_connection_ok(collected)

    # Повний провал: збір впав або запис впав.
    if overall == "failed" or storage == "failed":
        return "failed"

    all_ok = (
        gmail_ok
        and overall == "success"
        and storage in ("success", "skipped")
        and readback_ok
        and radar_status in ("success", "skipped")
    )
    return "success" if all_ok else "partial"


def _build_run_detail(
    collected: CollectedData,
    handoff_result: dict,
    window_start: str,
    started_at: str,
    finished_at: str,
) -> dict:
    """Побудувати повний структурований запис прогону для handoff_runs.jsonl."""
    overall = collected.overall_status()
    storage = handoff_result.get("storage_status", "unknown")
    run_status = compute_run_status(collected, handoff_result)

    sources_detail = {
        source: {
            "status": result.status,
            "records_count": len(result.records),
            "window_start": result.window_start,
            "window_end": result.window_end,
            "error": result.error,
        }
        for source, result in collected.sources.items()
    }

    return {
        # Мета прогону
        "run_id": collected.run_id,
        "trigger": "scheduled",
        "produced_by": "Abacus",
        "started_at": started_at,
        "finished_at": finished_at,
        # Вікно збору
        "window_start": window_start,
        "window_end": finished_at,
        # Зведені статуси
        "overall_status": run_status,
        "source_check_status": overall,
        "analysis_status": "done",
        "storage_status": storage,
        "handoff_status": storage,
        # Деталі по кожному джерелу
        "sources_checked": sources_detail,
        # Результати handoff
        "handoffs_written": handoff_result.get("handoffs_written", 0),
        "handoffs_skipped_duplicate": handoff_result.get("handoffs_skipped_duplicate", 0),
        "invalid_dropped": handoff_result.get("invalid_dropped", 0),
        "skipped_non_handoff": handoff_result.get("skipped_non_handoff", 0),
        "priority_handoffs": handoff_result.get("priority_handoffs", 0),
        "classification_counts": handoff_result.get("classification_counts", {}),
        "readback_confirmed": handoff_result.get("readback_confirmed", 0),
        "readback_ok": handoff_result.get("readback_ok", False),
        # Gmail (Req 1/6)
        "gmail_connection_verified": _gmail_connection_ok(collected),
        # Радар нормативних документів (Req 3/5)
        "radar_rows_total": handoff_result.get("radar_rows_total", 0),
        "radar_rows_added": handoff_result.get("radar_rows_added", 0),
        "radar_rows_updated": handoff_result.get("radar_rows_updated", 0),
        "radar_rows_unchanged": handoff_result.get("radar_rows_unchanged", 0),
        "radar_confirmed": handoff_result.get("radar_confirmed", 0),
        "radar_source_verified": handoff_result.get("radar_source_verified", 0),
        "radar_write_verified": handoff_result.get("radar_write_verified", 0),
        "radar_persistence_verified": handoff_result.get("radar_persistence_verified", 0),
        "radar_unresolved_count": handoff_result.get("radar_unresolved_count", 0),
        "radar_unresolved": handoff_result.get("radar_unresolved", []),
        "radar_fetch_verified": handoff_result.get("radar_fetch_verified", 0),
        "radar_docs_downloaded": handoff_result.get("radar_docs_downloaded", 0),
        "radar_status": handoff_result.get("radar_status", "unknown"),
        "radar_target": handoff_result.get("radar_target", ""),
        # Посилання на файли
        "output_refs": {
            "handoff_file": handoff_result.get("handoff_file", ""),
            "handoff_folder_id": handoff_result.get("handoff_folder_id", ""),
            "radar_target": handoff_result.get("radar_target", ""),
        },
        # Помилки / необроблене (Req 8)
        "errors": handoff_result.get("errors", []),
        "error": handoff_result.get("error"),
    }


def _md_escape(text: str) -> str:
    """Екранувати символи legacy-Markdown Telegram (_ * ` [)."""
    out = str(text)
    for ch in "_*`[":
        out = out.replace(ch, "\\" + ch)
    return out


def _build_telegram_summary(
    collected: CollectedData,
    handoff_result: dict,
    window_start: str,
    run_status: str = "partial",
    writer: "CheckpointsWriter | None" = None,
) -> str:
    """Формує короткий Telegram-summary для Андрія (Req 5 — синхронізація статусів).

    НЕ є брифінгом — лише технічна інформація про прогін.
    Брифінг формує Secretary після читання handoff. Показує ОКРЕМО:
    стан кожного джерела, створені handoff, дублі, невалідні записи,
    результати класифікації, фактичну кількість рядків радара у вкладці,
    завантажені документи, підтверджені read-back, посилання на канонічні
    checkpoint + log.
    """
    now = datetime.now()
    source_icons = {
        "success": "✅", "partial": "⚠️", "failed": "❌",
        "source_unavailable": "—", "skipped": "—",
    }
    run_icon = source_icons.get(run_status, "?")
    source_labels = {
        "gmail": "Gmail", "calendar": "Calendar",
        "tasks": "Реєстр завдань", "researcher": "МОЗ/НСЗУ",
    }

    lines = [
        f"📡 *Abacus — прогін {now:%d.%m.%Y %H:%M}*",
        f"*Підсумок:* {run_icon} {run_status.upper()}",
        "",
        "*Джерела (стан кожного):*",
    ]
    for source, result in collected.sources.items():
        icon = source_icons.get(result.status, "?")
        label = source_labels.get(source, source)
        count = len(result.records)
        conn = ""
        if source == "gmail":
            conn = " (з'єднання підтв.)" if getattr(result, "connection_verified", False) \
                else " (з'єднання НЕ підтв.)"
        lines.append(f"  {icon} {label}: {count} записів{conn}")

    lines.append("")

    n_written = handoff_result.get("handoffs_written", 0)
    n_skipped = handoff_result.get("handoffs_skipped_duplicate", 0)
    n_priority = handoff_result.get("priority_handoffs", 0)
    n_invalid = handoff_result.get("invalid_dropped", 0)
    storage = handoff_result.get("storage_status", "unknown")
    storage_icon = source_icons.get(storage, "?")

    lines.append(f"*SECRETARY\\_HANDOFF:* {storage_icon} {n_written} створено")
    lines.append(f"  ↩ дублів: {n_skipped} / 🗑 невалідних: {n_invalid}")
    if n_priority:
        lines.append(f"  ‼️ пріоритетних: {n_priority}")

    cc = handoff_result.get("classification_counts", {}) or {}
    lines.append(
        f"  🧩 класифікація: new\\_task {cc.get('new_task', 0)} / "
        f"update {cc.get('update_existing', 0)} / "
        f"info {cc.get('info_only', 0)} / "
        f"review {cc.get('needs_review', 0)}"
    )

    rb_ok = handoff_result.get("readback_ok", False)
    rb_icon = "✅" if rb_ok else "⚠️"
    lines.append(f"  {rb_icon} read-back handoff: {handoff_result.get('readback_confirmed', 0)} підтв.")

    # ── Радар нормативних документів (Req 3/5) ────────────────
    radar_status = handoff_result.get("radar_status", "unknown")
    radar_icon = source_icons.get(radar_status, "?")
    lines.append("")
    lines.append(f"*Радар норм. док.:* {radar_icon} {radar_status}")
    lines.append(
        f"  📑 рядків у вкладці: {handoff_result.get('radar_rows_total', 0)} "
        f"(+{handoff_result.get('radar_rows_added', 0)} / "
        f"~{handoff_result.get('radar_rows_updated', 0)})"
    )
    lines.append(
        f"  🌐 source_verified: {handoff_result.get('radar_source_verified', 0)} / "
        f"💾 write_verified: {handoff_result.get('radar_write_verified', 0)} / "
        f"⏳ unresolved: {handoff_result.get('radar_unresolved_count', 0)}"
    )
    lines.append(f"  📥 завантажено док.: {handoff_result.get('radar_docs_downloaded', 0)}")

    # ── Канонічні файли (Req 2/5) ─────────────────────────────
    if writer is not None:
        lines.append("")
        lines.append("*Канонічні файли:*")
        lines.append(f"  📌 [checkpoints]({writer.checkpoints_url})")
        lines.append(f"  📜 [automation log]({writer.automation_log_url})")

    errs = handoff_result.get("errors", []) or []
    if errs:
        lines.append("")
        lines.append(f"⚠️ зауваги/необроблене: {len(errs)}")
        for e in errs[:3]:
            lines.append(f"  • {_md_escape(str(e)[:90])}")

    lines.append("")
    window_date = window_start[:10] if len(window_start) >= 10 else window_start
    lines.append(f"_Вікно: {window_date} → {now:%Y-%m-%d}_")
    lines.append("_Брифінг формує Secretary після читання handoff._")

    return "\n".join(lines)


def _print_stdout_stats(
    collected: CollectedData,
    handoff_result: dict,
    window_start: str,
    started_at: str,
    finished_at: str,
) -> None:
    """Детальна статистика у консоль (прапор --stdout)."""
    sep = "=" * 62
    print(f"\n{sep}")
    print("  СТАТИСТИКА — Abacus Handoff Pipeline")
    print(sep)
    print(f"  run_id       : {collected.run_id}")
    print(f"  started_at   : {started_at}")
    print(f"  finished_at  : {finished_at}")
    print(f"  window_start : {window_start}")
    print(f"  overall      : {collected.overall_status()}")
    print()
    print(f"  RUN STATUS   : {compute_run_status(collected, handoff_result).upper()}")
    print()
    print("  Джерела:")
    for src, res in collected.sources.items():
        err = f" | {res.error}" if res.error else ""
        conn = "conn:ok" if getattr(res, "connection_verified", False) else "conn:НЕ ПІДТВ."
        print(f"    {src:<14}: {res.status} ({len(res.records)} записів) [{conn}]{err}")
    print()
    cc = handoff_result.get("classification_counts", {}) or {}
    print("  SECRETARY_HANDOFF:")
    print(f"    written          : {handoff_result.get('handoffs_written', 0)}")
    print(f"    priority         : {handoff_result.get('priority_handoffs', 0)}")
    print(f"    skipped_dup      : {handoff_result.get('handoffs_skipped_duplicate', 0)}")
    print(f"    invalid_dropped  : {handoff_result.get('invalid_dropped', 0)}")
    print(f"    skip_registry    : {handoff_result.get('skipped_non_handoff', 0)}")
    print(f"    classification   : new_task={cc.get('new_task', 0)}, "
          f"update_existing={cc.get('update_existing', 0)}, "
          f"info_only={cc.get('info_only', 0)}, needs_review={cc.get('needs_review', 0)}")
    print(f"    readback_ok      : {handoff_result.get('readback_ok', False)} "
          f"({handoff_result.get('readback_confirmed', 0)} підтверджено)")
    print(f"    storage          : {handoff_result.get('storage_status')}")
    print(f"    file             : {handoff_result.get('handoff_file', '—')}")
    print("  РАДАР нормативних док.:")
    print(f"    rows_total       : {handoff_result.get('radar_rows_total', 0)}")
    print(f"    added/updated    : {handoff_result.get('radar_rows_added', 0)}"
          f"/{handoff_result.get('radar_rows_updated', 0)}")
    print(f"    confirmed(rb)    : {handoff_result.get('radar_confirmed', 0)}")
    print(f"    source_verified  : {handoff_result.get('radar_source_verified', 0)}")
    print(f"    write_verified   : {handoff_result.get('radar_write_verified', 0)}")
    print(f"    unresolved       : {handoff_result.get('radar_unresolved_count', 0)}")
    print(f"    fetch_verified   : {handoff_result.get('radar_fetch_verified', 0)}")
    print(f"    docs_downloaded  : {handoff_result.get('radar_docs_downloaded', 0)}")
    print(f"    radar_status     : {handoff_result.get('radar_status', '—')}")
    print(f"    radar_target     : {handoff_result.get('radar_target', '—')}")
    errs = handoff_result.get("errors", []) or []
    if errs:
        print(f"    errors/unproc.   : {len(errs)}")
        for e in errs[:10]:
            print(f"        - {e}")
    if handoff_result.get("error"):
        print(f"    error            : {handoff_result['error']}")
    print(sep)


# ────────────────────────────────────────────────────────────
# Головна логіка
# ────────────────────────────────────────────────────────────

def main() -> None:
    """Точка входу оркестратора handoff-пайплайну."""
    parser = argparse.ArgumentParser(
        description="Abacus — handoff-пайплайн (фоновий технічний агент)"
    )
    parser.add_argument("--config", help="Шлях до config.yaml", default=None)
    parser.add_argument(
        "--stdout", action="store_true",
        help="Вивести детальну статистику у консоль"
    )
    parser.add_argument(
        "--no-drive", action="store_true",
        help="Пропустити запис у Drive (режим тестування)"
    )
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"\n{'=' * 60}")
    print(f"  Abacus — Handoff Pipeline [{started_at}]")
    print(f"{'=' * 60}\n")

    # ── КРОК 1: Конфігурація ─────────────────────────────────
    config = load_config(args.config)

    # ── КРОК 2: Checkpoints → window_start ───────────────────
    print("[→] Завантаження checkpoints з Drive...")
    try:
        reader = CheckpointsReader()
        checkpoints = reader.load_from_drive() or {}
    except Exception as exc:
        print(f"[!] Помилка читання checkpoints: {exc} → defaults")
        checkpoints = {}

    window_start = get_window_start(checkpoints)
    print(f"[✓] window_start = {window_start}")

    # ── КРОК 3: Collector — збір сирих даних ─────────────────
    print("\n[→] Collector: збір даних з усіх джерел...")
    try:
        collected = collect_all(config, window_start)
    except Exception as exc:
        print(f"[✗] Критична помилка Collector: {exc}")
        sys.exit(1)

    print(f"[✓] Collector завершено: {collected.overall_status()}")

    # ── КРОК 4: Handoff — дедуплікація → Drive ───────────────
    if args.no_drive:
        print("\n[⚠] --no-drive: Handoff пропущено (режим тестування)")
        handoff_result: dict[str, Any] = {
            "handoffs_written": 0,
            "handoffs_skipped_duplicate": 0,
            "storage_status": "skipped",
            "handoff_file": f"handoff_{datetime.now():%Y-%m-%d}.jsonl",
            "handoff_folder_id": None,
            "priority_handoffs": 0,
            "error": None,
        }
    else:
        print("\n[→] Handoff: дедуплікація → класифікація → запис на Drive...")
        try:
            handoff_result = process_handoffs(collected)
        except Exception as exc:
            print(f"[✗] Критична помилка Handoff: {exc}")
            handoff_result = {
                "handoffs_written": 0,
                "handoffs_skipped_duplicate": 0,
                "storage_status": "failed",
                "handoff_file": f"handoff_{datetime.now():%Y-%m-%d}.jsonl",
                "handoff_folder_id": None,
                "priority_handoffs": 0,
                "error": str(exc),
            }

        print(
            f"[✓] Handoff: written={handoff_result['handoffs_written']}, "
            f"priority={handoff_result['priority_handoffs']}, "
            f"skipped={handoff_result['handoffs_skipped_duplicate']}, "
            f"status={handoff_result['storage_status']}"
        )

    # ── КРОК 4b: Радар нормативних документів (Req 5) ─────────
    if args.no_drive:
        radar_result: dict[str, Any] = {
            "radar_rows_total": 0, "radar_rows_added": 0, "radar_rows_updated": 0,
            "radar_rows_unchanged": 0, "radar_confirmed": 0,
            "radar_source_verified": 0, "radar_write_verified": 0,
            "radar_persistence_verified": 0, "radar_unresolved_count": 0,
            "radar_unresolved": [], "radar_fetch_verified": 0, "radar_docs_downloaded": 0,
            "radar_status": "skipped", "readback_ok": True,
            "radar_target": "", "error": None, "errors": [],
        }
    else:
        print("\n[→] Радар: обробка нормативних документів...")
        try:
            radar_result = process_radar(collected)
        except Exception as exc:
            print(f"[✗] Помилка радара: {exc}")
            radar_result = {
                "radar_rows_total": 0, "radar_rows_added": 0, "radar_rows_updated": 0,
                "radar_rows_unchanged": 0, "radar_confirmed": 0,
                "radar_source_verified": 0, "radar_write_verified": 0,
                "radar_persistence_verified": 0, "radar_unresolved_count": 0,
                "radar_unresolved": [], "radar_fetch_verified": 0, "radar_docs_downloaded": 0,
                "radar_status": "failed", "readback_ok": False,
                "radar_target": "", "error": str(exc), "errors": [f"radar: {exc}"],
            }
        print(
            f"[✓] Радар: +{radar_result['radar_rows_added']} нових, "
            f"~{radar_result['radar_rows_updated']} оновлено, "
            f"всього {radar_result['radar_rows_total']}, "
            f"підтв.={radar_result.get('radar_confirmed', 0)}, "
            f"status={radar_result['radar_status']}"
        )

    # Зводимо радар у handoff_result, щоб summary/лог бачили його (Req 5).
    handoff_result["radar_rows_total"] = radar_result.get("radar_rows_total", 0)
    handoff_result["radar_rows_added"] = radar_result.get("radar_rows_added", 0)
    handoff_result["radar_rows_updated"] = radar_result.get("radar_rows_updated", 0)
    handoff_result["radar_rows_unchanged"] = radar_result.get("radar_rows_unchanged", 0)
    handoff_result["radar_confirmed"] = radar_result.get("radar_confirmed", 0)
    handoff_result["radar_source_verified"] = radar_result.get("radar_source_verified", 0)
    handoff_result["radar_write_verified"] = radar_result.get("radar_write_verified", 0)
    handoff_result["radar_persistence_verified"] = radar_result.get("radar_persistence_verified", 0)
    handoff_result["radar_unresolved_count"] = radar_result.get("radar_unresolved_count", 0)
    handoff_result["radar_unresolved"] = radar_result.get("radar_unresolved", [])
    handoff_result["radar_fetch_verified"] = radar_result.get("radar_fetch_verified", 0)
    handoff_result["radar_docs_downloaded"] = radar_result.get("radar_docs_downloaded", 0)
    handoff_result["radar_status"] = radar_result.get("radar_status", "unknown")
    handoff_result["radar_target"] = radar_result.get("radar_target", "")
    handoff_result["radar_readback_ok"] = radar_result.get("readback_ok", False)
    if radar_result.get("error"):
        handoff_result.setdefault("errors", []).append(f"radar: {radar_result['error']}")
    for e in radar_result.get("errors", []) or []:
        handoff_result.setdefault("errors", []).append(e)

    finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Чесний підсумковий статус прогону (Req 6) — єдине джерело істини.
    run_status = compute_run_status(collected, handoff_result)

    # ── КРОКИ 5–6: Checkpoints + Automation Log ──────────────
    writer: CheckpointsWriter | None = None

    if not args.no_drive:
        # КРОК 5: Оновити checkpoints.
        # Req 7: checkpoint (вікно delta) просувається ЛИШЕ коли джерело реально
        # перевірено, handoff валідний, запис відбувся і підтверджений read-back.
        # Інакше ризик втрати даних: вікно зсунеться вперед, а записи не збережені.
        storage_status = handoff_result.get("storage_status", "unknown")
        readback_ok = handoff_result.get("readback_ok", False)
        radar_status = handoff_result.get("radar_status", "unknown")
        gmail_ok = _gmail_connection_ok(collected)
        # Req 1/6: без підтвердженого Gmail прогін не може бути success,
        # тож і checkpoints не просуваємо.
        storage_fully_ok = (
            gmail_ok
            and storage_status == "success"
            and readback_ok
            and radar_status in ("success", "skipped")
        )

        print("\n[→] Оновлення checkpoints...")
        try:
            writer = CheckpointsWriter()
            # Per-source: просуваємо лише ті джерела, що success. Якщо ж запис/
            # read-back не підтверджено — НЕ просуваємо жодне джерело взагалі,
            # щоб наступний прогін повторно зібрав ці записи.
            ckpt_details = {
                "radar_status": radar_status,
                "radar_rows_total": handoff_result.get("radar_rows_total", 0),
                "radar_confirmed": handoff_result.get("radar_confirmed", 0),
                "radar_source_verified": handoff_result.get("radar_source_verified", 0),
                "radar_write_verified": handoff_result.get("radar_write_verified", 0),
                "radar_unresolved_count": handoff_result.get("radar_unresolved_count", 0),
                "readback_ok": readback_ok,
                "gmail_connection_verified": gmail_ok,
            }
            if storage_fully_ok:
                sources_status = _sources_status_dict(collected)
                if writer.update_checkpoints(sources_status, run_status, ckpt_details):
                    print("[✓] Checkpoints оновлено")
                else:
                    print("[!] update_checkpoints повернув False")
            else:
                print(
                    f"[⛔] Checkpoints НЕ просунуто: gmail_ok={gmail_ok}, "
                    f"storage={storage_status}, readback_ok={readback_ok}, "
                    f"radar={radar_status}. Вікно залишається, щоб не втратити записи."
                )
        except Exception as exc:
            print(f"[!] Помилка оновлення checkpoints: {exc}")
            writer = None

        # Для логів/Telegram потрібен writer навіть коли checkpoints не просунуто.
        if writer is None:
            try:
                writer = CheckpointsWriter()
            except Exception:
                writer = None

        if writer is not None:
            # КРОК 6a: Детальний лог прогону (handoff_runs.jsonl на Drive)
            try:
                run_detail = _build_run_detail(
                    collected=collected,
                    handoff_result=handoff_result,
                    window_start=window_start,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                if writer.log_handoff_run(run_detail):
                    print("[✓] handoff_runs.jsonl оновлено")
                else:
                    print("[!] log_handoff_run повернув False")
            except Exception as exc:
                print(f"[!] Помилка log_handoff_run: {exc}")

            # КРОК 6b: Рядок у зведеній таблиці automation_log.md
            try:
                # Req 5/6: статус рядка = чесний підсумковий статус прогону.
                log_status = run_status

                sources_line = " | ".join(
                    f"{s}:{r.status}({len(r.records)})"
                    for s, r in collected.sources.items()
                )
                event = {
                    "time_utc": datetime.now(timezone.utc).strftime("%H:%M"),
                    "task": "handoff_pipeline",
                    "status": log_status,
                    "result": (
                        f"written={handoff_result.get('handoffs_written', 0)}, "
                        f"priority={handoff_result.get('priority_handoffs', 0)}, "
                        f"radar_rows={handoff_result.get('radar_rows_total', 0)}"
                        f"(conf={handoff_result.get('radar_confirmed', 0)}) | "
                        f"{sources_line}"
                    ),
                    "file": handoff_result.get("handoff_file", "—"),
                }
                if writer.append_to_automation_log(event):
                    print("[✓] automation_log.md оновлено")
                else:
                    print("[!] append_to_automation_log повернув False")
            except Exception as exc:
                print(f"[!] Помилка automation_log: {exc}")

    # ── КРОК 7: Telegram summary ─────────────────────────────
    delivery = config.get("delivery", {})
    if delivery.get("send_telegram", True) and not args.no_drive:
        print("\n[→] Надсилання Telegram summary...")
        try:
            from integrations.telegram_sender import send_briefing  # noqa: E402
            summary = _build_telegram_summary(
                collected, handoff_result, window_start,
                run_status=run_status, writer=writer,
            )
            ok = send_briefing(summary)
            if ok:
                print("[✓] Telegram summary надіслано")
            else:
                print("[!] Telegram: не вдалося надіслати (chat_id не відомий?)")
        except Exception as exc:
            print(f"[!] Telegram помилка: {exc}")

    # ── КРОК 8: Stdout-статистика (прапор --stdout) ──────────
    if args.stdout:
        _print_stdout_stats(
            collected=collected,
            handoff_result=handoff_result,
            window_start=window_start,
            started_at=started_at,
            finished_at=finished_at,
        )

    print(f"\n[✓] Abacus — Handoff Pipeline завершено [{finished_at}]\n")


if __name__ == "__main__":
    main()
