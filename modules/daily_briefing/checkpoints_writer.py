"""
Утиліта для запису checkpoints у Google Drive.
Abacus — єдиний writer, UTC-модель.
"""

from datetime import datetime, timezone
from integrations.drive_client import DriveClient


class CheckpointsWriter:
    """Писач checkpoints у Google Drive (Abacus)"""
    
    # ID папки Command Center (де у нас є права на запис)
    COMMAND_CENTER_FOLDER_ID = '1eh47d2AtLZwfuYdthzVfJ-5pz_x2Qgf5'
    CHECKPOINT_FILE_NAME = 'checkpoints.md'
    AUTOMATION_LOG_FILE_NAME = 'automation_log.md'
    
    def __init__(self):
        """Ініціалізація"""
        self.drive = DriveClient()
        self.now_utc = datetime.now(timezone.utc)
        self.timestamp = self.now_utc.isoformat().replace('+00:00', 'Z')
    
    def update_checkpoints(self, sources: dict, status: str = 'success') -> bool:
        """
        Оновити checkpoints після успішного прогону брифінгу.
        
        Args:
            sources: {
                'gmail': True/False,
                'calendar': True/False,
                'tasks': True/False,
                'researcher': True/False,
            }
            status: 'success', 'partial', 'failed'
            
        Returns:
            True якщо успішно, False якщо помилка
        """
        try:
            # Генеруємо вміст
            content = self._generate_checkpoints_content(sources, status)
            
            # write_file автоматично оновить або створить файл
            file_id = self.drive.write_file(
                content=content,
                filename=self.CHECKPOINT_FILE_NAME,
                parent_id=self.COMMAND_CENTER_FOLDER_ID,
                mime_type='text/markdown'
            )
            
            if file_id:
                print(f"✅ Checkpoints оновлено: {self.timestamp}")
                return True
            else:
                print("❌ Помилка оновлення checkpoints")
                return False
                    
        except Exception as e:
            print(f"❌ Помилка запису checkpoints: {e}")
            return False
    
    def _generate_checkpoints_content(self, sources: dict, status: str) -> str:
        """Генерувати вміст checkpoints.md"""
        status_icon = {
            'success': '✅',
            'partial': '⚠️',
            'failed': '❌'
        }.get(status, '?')
        
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

gmail_last_success: {self.timestamp if sources.get('gmail') else 'N/A'}
gmail_last_checked: {self.timestamp}
gmail_status: {"success" if sources.get('gmail') else "pending"}

calendar_last_success: {self.timestamp if sources.get('calendar') else 'N/A'}
calendar_last_checked: {self.timestamp}
calendar_status: {"success" if sources.get('calendar') else "pending"}

tasks_last_success: {self.timestamp if sources.get('tasks') else 'N/A'}
tasks_last_checked: {self.timestamp}
tasks_status: {"success" if sources.get('tasks') else "pending"}

researcher_last_success: {self.timestamp if sources.get('researcher') else 'N/A'}
researcher_last_checked: {self.timestamp}
researcher_status: {"success" if sources.get('researcher') else "pending"}
```

---

## Notes

- **Тільки UTC** у файлі (відображення в Kyiv — у `automation_log.md` та брифінгах)
- **Єдиний writer:** тільки Abacus записує цей файл
- **Мета:** Delta-only обробка (Abacus читає `_last_success`, збирає лише нове)
- **ChatGPT:** читає для аналізу, не редагує
"""
    
    def append_to_automation_log(self, event: dict) -> bool:
        """
        Додати event до automation_log.md.
        
        Args:
            event: {
                'time_utc': '07:00',
                'task': 'gmail_check',
                'status': 'success|partial|failed',
                'result': 'текст результату',
                'file': 'path/to/briefing_2026-09-19.md'
            }
            
        Returns:
            True якщо успішно
        """
        try:
            # Шукаємо існуючий файл
            log_file = self.drive.find_file(
                self.AUTOMATION_LOG_FILE_NAME,
                self.COMMAND_CENTER_FOLDER_ID
            )
            
            if log_file:
                # Читаємо поточний вміст
                current_content = self.drive.read_file(log_file['id'])
            else:
                # Створюємо новий файл з header
                current_content = self._generate_automation_log_header()
            
            # Форматуємо new row
            status_icon = {'success': '✅', 'partial': '⚠️', 'failed': '❌'}.get(
                event.get('status', '?'), '?'
            )
            new_row = f"| {event['time_utc']} UTC | {event['task']} | {status_icon} {event['status'].upper()} | {event['result']} | {event.get('file', '—')} |\n"
            
            # Додаємо новий рядок в кінець таблиці (перед примітками)
            if '\n---' in current_content:
                # Є секція примітки, додаємо перед нею
                updated = current_content.replace('\n---', f'\n{new_row}---')
            else:
                # Просто додаємо в кінець
                updated = current_content.rstrip() + '\n' + new_row
            
            # Записуємо
            file_id = self.drive.write_file(
                content=updated,
                filename=self.AUTOMATION_LOG_FILE_NAME,
                parent_id=self.COMMAND_CENTER_FOLDER_ID,
                mime_type='text/markdown'
            )
            
            if file_id:
                print(f"✅ Log event: {event['task']}")
                return True
            return False
                
        except Exception as e:
            print(f"❌ Помилка логу: {e}")
            return False
    
    def _generate_automation_log_header(self) -> str:
        """Генерувати header для automation_log.md"""
        return f"""# Automation Log

**Останнє оновлення:** {self.timestamp} (UTC)

Справжній час розпізнавання та результати кожного автоматичного прогону.

---

## Daily Runs

| Час (UTC) | Задача | Статус | Результат | Файл |
|-----------|--------|--------|-----------|------|

"""
