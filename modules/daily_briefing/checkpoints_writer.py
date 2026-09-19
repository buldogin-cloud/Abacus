"""
Утиліта для запису checkpoints у Google Drive.
Abacus — єдиний writer, UTC-модель.
"""

from datetime import datetime, timezone
from integrations.drive_client import DriveClient


class CheckpointsWriter:
    """Писач checkpoints у Google Drive (Abacus)"""
    
    CADENCE_FOLDER_ID = '1FsfbDWu9mxRSVWr72SaxMVz4YAVv49zE'
    CHECKPOINT_FILE_NAME = 'checkpoints.md'
    
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
            # Шаблон checkpoints
            content = self._generate_checkpoints_content(sources, status)
            
            # Знаходимо або створюємо файл
            checkpoint_file = self.drive.find_file(
                self.CHECKPOINT_FILE_NAME,
                self.CADENCE_FOLDER_ID
            )
            
            if checkpoint_file:
                # Оновляємо існуючий файл
                success = self.drive.update_file(checkpoint_file['id'], content)
                if success:
                    print(f"✅ Checkpoints оновлено: {self.timestamp}")
                    return True
                else:
                    print("❌ Помилка оновлення checkpoints")
                    return False
            else:
                # Створюємо новий файл
                success = self.drive.create_file(
                    self.CHECKPOINT_FILE_NAME,
                    content,
                    self.CADENCE_FOLDER_ID,
                    'text/markdown'
                )
                if success:
                    print(f"✅ Checkpoints створено: {self.timestamp}")
                    return True
                else:
                    print("❌ Помилка створення checkpoints")
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
        """
        try:
            log_file = self.drive.find_file('automation_log.md', self.CADENCE_FOLDER_ID)
            
            if not log_file:
                initial_log = self._generate_automation_log_header()
                self.drive.create_file(
                    'automation_log.md',
                    initial_log,
                    self.CADENCE_FOLDER_ID,
                    'text/markdown'
                )
                log_file = self.drive.find_file('automation_log.md', self.CADENCE_FOLDER_ID)
            
            current_content = self.drive.read_file(log_file['id'])
            status_icon = {'success': '✅', 'partial': '⚠️', 'failed': '❌'}.get(
                event.get('status', '?'), '?'
            )
            new_row = f"| {event['time_utc']} UTC | {event['task']} | {status_icon} {event['status'].upper()} | {event['result']} | {event.get('file', '—')} |\n"
            
            if '|\n| ' in current_content:
                updated = current_content.replace('|\n| ', f'{new_row}| ', 1)
            else:
                updated = current_content.rstrip() + '\n' + new_row
            
            success = self.drive.update_file(log_file['id'], updated)
            if success:
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

---

## Daily Runs

| Час (UTC) | Задача | Статус | Результат | Файл |
|-----------|--------|--------|-----------|------|

"""
