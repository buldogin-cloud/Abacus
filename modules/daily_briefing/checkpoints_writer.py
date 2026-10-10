"""
Утиліта для запису checkpoints у Google Drive.
Abacus — єдиний writer, UTC-модель.

Req 2: пише ТІЛЬКИ в канонічні файли за явними ID (корінь Command Center).
Жодних паралельних копій у теці handoffs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from integrations.drive_client import DriveClient
from modules.daily_briefing.canonical import (
    AUTOMATION_LOG_FILE_ID,
    CHECKPOINTS_FILE_ID,
    HANDOFF_RUNS_FILE_ID,
    drive_file_url,
)


class CheckpointsWriter:
    """Писач checkpoints у Google Drive (Abacus) — лише канонічні файли за ID."""

    def __init__(self):
        """Ініціалізація"""
        self.drive = DriveClient()
        self.now_utc = datetime.now(timezone.utc)
        self.timestamp = self.now_utc.isoformat().replace('+00:00', 'Z')

    # Публічні посилання на канонічні файли (для summary, Req 5)
    @property
    def checkpoints_url(self) -> str:
        return drive_file_url(CHECKPOINTS_FILE_ID)

    @property
    def automation_log_url(self) -> str:
        return drive_file_url(AUTOMATION_LOG_FILE_ID)

    def update_checkpoints(
        self,
        sources: dict,
        status: str = 'success',
        details: dict | None = None,
    ) -> bool:
        """
        Оновити канонічний checkpoints.md після прогону.

        Args:
            sources: {'gmail': True/False, 'calendar': ..., 'tasks': ..., 'researcher': ...}
            status: 'success' | 'partial' | 'failed'
            details: опційні деталі (radar, readback тощо) для нотаток.

        Returns:
            True якщо успішно, False якщо помилка
        """
        try:
            # Never perform a blind overwrite: if the current canonical object
            # cannot be read, leave it untouched and keep the old window.
            previous = self.drive.read_file(CHECKPOINTS_FILE_ID)
            if previous is None:
                print("❌ Checkpoint pre-read недоступний; запис заблоковано")
                return False
            content = self._generate_checkpoints_content(sources, status, details or {})
            # Req 2: пишемо за ЯВНИМ ID канонічного файлу.
            file_id = self.drive.update_file_by_id(
                file_id=CHECKPOINTS_FILE_ID,
                content=content,
                mime_type='text/markdown',
            )
            # update() success is not sufficient: verify that the canonical
            # object now contains exactly the checkpoint we intended to persist.
            persisted = self.drive.read_file(CHECKPOINTS_FILE_ID) if file_id else None
            if file_id and persisted == content:
                print(f"✅ Канонічний checkpoints.md оновлено: {self.timestamp}")
                return True
            if file_id:
                print("❌ Checkpoint update повернув ID, але read-back не збігся")
                return False
            print("❌ Помилка оновлення канонічного checkpoints.md")
            return False
        except Exception as e:
            print(f"❌ Помилка запису checkpoints: {e}")
            return False

    def _status_value(self, ok: bool, run_status: str) -> str:
        """Статус джерела у YAML: success / failed (залежно від перевірки)."""
        if ok:
            return "success"
        # partial-прогін: джерело не підтверджено.
        return "failed" if run_status in ("partial", "failed") else "pending"

    def _generate_checkpoints_content(
        self, sources: dict, status: str, details: dict
    ) -> str:
        """Генерувати вміст канонічного checkpoints.md"""
        status_icon = {
            'success': '✅',
            'partial': '⚠️',
            'failed': '❌',
        }.get(status, '?')

        def ts(ok: bool) -> str:
            # Просуваємо _last_success лише для реально підтверджених джерел.
            return self.timestamp if ok else 'N/A'

        radar_note = ""
        if details:
            radar_note = (
                f"radar_status: {details.get('radar_status', 'unknown')}\n"
                f"radar_rows_in_tab: {details.get('radar_rows_total', 0)}\n"
                f"radar_source_verified: {details.get('radar_source_verified', 0)}\n"
                f"radar_write_verified: {details.get('radar_write_verified', 0)}\n"
                f"radar_unresolved_count: {details.get('radar_unresolved_count', 0)}\n"
                f"readback_ok: {details.get('readback_ok', False)}\n"
            )

        return f"""# Command Center — Checkpoints

Цей файл відстежує останні успішні перевірки джерел даних.  
**Останнє оновлення:** {self.timestamp} (UTC, Abacus)  
**Статус прогону:** {status_icon} {status.upper()}

---

## Останні успішні перевірки

```yaml
run_status: {status}
run_timestamp: {self.timestamp}
run_agent: Abacus
timezone_canonical: UTC

gmail_last_success: {ts(sources.get('gmail'))}
gmail_last_checked: {self.timestamp}
gmail_status: {self._status_value(sources.get('gmail'), status)}

calendar_last_success: {ts(sources.get('calendar'))}
calendar_last_checked: {self.timestamp}
calendar_status: {self._status_value(sources.get('calendar'), status)}

tasks_last_success: {ts(sources.get('tasks'))}
tasks_last_checked: {self.timestamp}
tasks_status: {self._status_value(sources.get('tasks'), status)}

researcher_last_success: {ts(sources.get('researcher'))}
researcher_last_checked: {self.timestamp}
researcher_status: {self._status_value(sources.get('researcher'), status)}

{radar_note}```

---

## Notes

- **Тільки UTC** у файлі (відображення в Kyiv — у `automation_log.md` та брифінгах)
- **Єдиний writer:** тільки Abacus записує цей файл (канонічний, за ID)
- **Мета:** Delta-only обробка (Abacus читає `_last_success`, збирає лише нове)
- **Checkpoint просувається** лише коли джерело підтверджено, handoff записаний
  і підтверджений read-back (Req 6). Інакше вікно лишається, щоб не втратити дані.
- **ChatGPT:** читає для аналізу, не редагує
"""

    def append_to_automation_log(self, event: dict) -> bool:
        """
        Додати event до канонічного automation_log.md (за ID).

        Args:
            event: {'time_utc','task','status','result','file'}

        Returns:
            True якщо успішно
        """
        try:
            current_content = self.drive.read_file(AUTOMATION_LOG_FILE_ID)
            if not current_content:
                current_content = self._generate_automation_log_header()

            status_icon = {'success': '✅', 'partial': '⚠️', 'failed': '❌'}.get(
                event.get('status', '?'), '?'
            )
            new_row = (
                f"| {event['time_utc']} UTC | {event['task']} | "
                f"{status_icon} {event['status'].upper()} | {event['result']} | "
                f"{event.get('file', '—')} |\n"
            )

            if '\n---' in current_content:
                updated = current_content.replace('\n---', f'\n{new_row}---', 1)
            else:
                updated = current_content.rstrip() + '\n' + new_row

            file_id = self.drive.update_file_by_id(
                file_id=AUTOMATION_LOG_FILE_ID,
                content=updated,
                mime_type='text/markdown',
            )
            if file_id:
                print(f"✅ Канонічний automation_log.md: {event['task']}")
                return True
            return False
        except Exception as e:
            print(f"❌ Помилка логу: {e}")
            return False

    def log_handoff_run(self, run_detail: dict) -> bool:
        """
        Дозаписати структурований запис прогону у канонічний handoff_runs.jsonl (за ID).
        Кожен рядок — JSON одного прогону.
        """
        try:
            current_content = self.drive.read_file(HANDOFF_RUNS_FILE_ID) or ""
            new_line = json.dumps(run_detail, ensure_ascii=False) + "\n"
            updated = current_content + new_line

            file_id = self.drive.update_file_by_id(
                file_id=HANDOFF_RUNS_FILE_ID,
                content=updated,
                mime_type="text/plain",
            )
            if file_id:
                print(f"✅ handoff_runs.jsonl: {run_detail.get('run_id', '?')} записано")
                return True
            print("❌ log_handoff_run: update_file_by_id повернув None")
            return False
        except Exception as exc:
            print(f"❌ log_handoff_run: {exc}")
            return False

    def _generate_automation_log_header(self) -> str:
        """Генерувати header для automation_log.md"""
        return f"""# Automation Log

**Останнє оновлення:** {self.timestamp} (UTC)

Зведена таблиця прогонів Abacus. Детальна структура — `handoff_runs.jsonl`.

---

## Daily Runs

| Час (UTC) | Задача | Статус | Результат | Файл |
|-----------|--------|--------|-----------|------|

"""
