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

    if overall == "success" and storage in ("success", "skipped"):
        run_status = "success"
    elif overall == "failed" or storage == "failed":
        run_status = "failed"
    else:
        run_status = "partial"

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
        "priority_handoffs": handoff_result.get("priority_handoffs", 0),
        # Посилання на файли
        "output_refs": {
            "handoff_file": handoff_result.get("handoff_file", ""),
            "handoff_folder_id": handoff_result.get("handoff_folder_id", ""),
        },
        # Помилки
        "error": handoff_result.get("error"),
    }


def _build_telegram_summary(
    collected: CollectedData,
    handoff_result: dict,
    window_start: str,
) -> str:
    """Формує короткий Telegram-summary для Андрія.

    НЕ є брифінгом — лише технічна інформація про прогін.
    Брифінг формує Secretary після читання handoff.
    """
    now = datetime.now()
    source_icons = {
        "success": "✅",
        "partial": "⚠️",
        "failed": "❌",
        "source_unavailable": "—",
        "skipped": "—",
    }
    source_labels = {
        "gmail": "Gmail",
        "calendar": "Calendar",
        "tasks": "Реєстр завдань",
        "researcher": "МОЗ/НСЗУ",
    }

    lines = [
        f"📡 *Abacus — прогін {now:%d.%m.%Y %H:%M}*",
        "",
        "*Джерела:*",
    ]
    for source, result in collected.sources.items():
        icon = source_icons.get(result.status, "?")
        label = source_labels.get(source, source)
        count = len(result.records)
        lines.append(f"  {icon} {label}: {count} записів")

    lines.append("")

    n_written = handoff_result.get("handoffs_written", 0)
    n_skipped = handoff_result.get("handoffs_skipped_duplicate", 0)
    n_priority = handoff_result.get("priority_handoffs", 0)
    storage = handoff_result.get("storage_status", "unknown")
    storage_icon = source_icons.get(storage, "?")

    lines.append(f"*SECRETARY\\_HANDOFF:* {storage_icon} {n_written} нових записів")
    if n_priority:
        lines.append(f"  ‼️ у т.ч. {n_priority} пріоритетних")
    if n_skipped:
        lines.append(f"  ↩ {n_skipped} дублів пропущено")
    if handoff_result.get("error"):
        lines.append(f"  ⚠️ Помилка: `{str(handoff_result['error'])[:100]}`")

    lines.append("")
    window_date = window_start[:10] if len(window_start) >= 10 else window_start
    lines.append(f"_Вікно: {window_date} → {now:%Y\\-%m\\-%d}_")
    lines.append("_Брифінг формує Secretary після читання handoff\\._")

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
    print("  Джерела:")
    for src, res in collected.sources.items():
        err = f" | {res.error}" if res.error else ""
        print(f"    {src:<14}: {res.status} ({len(res.records)} записів){err}")
    print()
    print("  SECRETARY_HANDOFF:")
    print(f"    written      : {handoff_result.get('handoffs_written', 0)}")
    print(f"    priority     : {handoff_result.get('priority_handoffs', 0)}")
    print(f"    skipped_dup  : {handoff_result.get('handoffs_skipped_duplicate', 0)}")
    print(f"    storage      : {handoff_result.get('storage_status')}")
    print(f"    file         : {handoff_result.get('handoff_file', '—')}")
    if handoff_result.get("error"):
        print(f"    error        : {handoff_result['error']}")
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

    finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # ── КРОКИ 5–6: Checkpoints + Automation Log ──────────────
    writer: CheckpointsWriter | None = None

    if not args.no_drive:
        # КРОК 5: Оновити checkpoints
        print("\n[→] Оновлення checkpoints...")
        try:
            writer = CheckpointsWriter()
            sources_status = _sources_status_dict(collected)
            overall_status = collected.overall_status()
            if writer.update_checkpoints(sources_status, overall_status):
                print("[✓] Checkpoints оновлено")
            else:
                print("[!] update_checkpoints повернув False")
        except Exception as exc:
            print(f"[!] Помилка оновлення checkpoints: {exc}")
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
                overall = collected.overall_status()
                storage = handoff_result.get("storage_status", "unknown")
                if overall == "success" and storage in ("success", "skipped"):
                    log_status = "success"
                elif overall == "failed" or storage == "failed":
                    log_status = "failed"
                else:
                    log_status = "partial"

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
                        f"priority={handoff_result.get('priority_handoffs', 0)} | "
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
            summary = _build_telegram_summary(collected, handoff_result, window_start)
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
