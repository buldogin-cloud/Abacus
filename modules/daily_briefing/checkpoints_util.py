"""
Утиліта для роботи з checkpoints
"""

import re
from datetime import datetime, timezone
from integrations.drive_client import DriveClient
from modules.daily_briefing.canonical import CHECKPOINTS_FILE_ID


def parse_checkpoint_timestamp(value, now=None):
    """Validate an aware, non-future watermark and normalize its instant to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError("Checkpoint timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("Checkpoint lacks timezone")
    parsed = parsed.astimezone(timezone.utc)
    boundary = now if now is not None else datetime.now(timezone.utc)
    if boundary.tzinfo is None or boundary.utcoffset() is None:
        raise ValueError("Validation time must include timezone")
    if parsed > boundary.astimezone(timezone.utc):
        raise RuntimeError("Checkpoint is in the future")
    return parsed


def earliest_checkpoint_timestamp(timestamps, now=None):
    """Compare instants, not lexicographic ISO strings with different offsets."""
    boundary = now if now is not None else datetime.now(timezone.utc)
    earliest = min(parse_checkpoint_timestamp(value, boundary) for value in timestamps)
    return earliest.isoformat().replace("+00:00", "Z")


class CheckpointsReader:
    """Читач checkpoints з Google Drive (ТІЛЬКИ канонічний файл за ID)."""

    def __init__(self):
        """Ініціалізація"""
        self.drive = DriveClient()
        self.checkpoints = {}
    
    def load_from_drive(self, strict=False):
        """
        Завантажити checkpoints з Drive.

        Req 2: читаємо КАНОНІЧНИЙ checkpoints.md за явним ID у корені Command
        Center, а не шукаємо за назвою у теці handoffs (де лежала паралельна
        копія). Так Reader і Writer завжди працюють з одним файлом.

        Returns:
            Словник з timestamps або defaults при помилці
        """
        try:
            # Читаємо вміст канонічного файлу прямо за ID.
            content = self.drive.read_file(CHECKPOINTS_FILE_ID)

            if not content:
                print("⚠️ Не вдалося прочитати канонічний checkpoints.md")
                if strict:
                    raise RuntimeError("Canonical checkpoint is unreadable")
                return self._get_defaults()
            
            # Парсимо YAML з markdown
            self.checkpoints = self._parse_checkpoints(content, strict=strict)
            return self.checkpoints
            
        except Exception as e:
            if strict:
                raise RuntimeError("Canonical checkpoint could not be verified") from e
            print(f"❌ Помилка завантаження checkpoints: {type(e).__name__}")
            return self._get_defaults()
    
    def _parse_checkpoints(self, content, strict=False):
        """
        Парсинг checkpoints з markdown/yaml
        
        Args:
            content: Текстовий вміст файлу
            
        Returns:
            Словник з timestamps
        """
        checkpoints = {}
        
        # Знаходимо блок з YAML
        yaml_match = re.search(r'```yaml\n(.*?)\n```', content, re.DOTALL)
        
        if not yaml_match:
            if strict:
                raise RuntimeError("Checkpoint YAML block missing")
            return self._get_defaults()
        
        yaml_content = yaml_match.group(1)
        
        # Парсимо рядки виду "key: value"
        for line in yaml_content.split('\n'):
            line = line.strip()
            
            # Пропускаємо коментарі та порожні рядки
            if not line or line.startswith('#'):
                continue
            
            # Парсимо key: value
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                # Зберігаємо лише _last_success timestamps
                if key.endswith('_last_success'):
                    checkpoints[key] = value
        
        if strict:
            for key in ("gmail", "calendar", "tasks", "researcher"):
                value = checkpoints.get(key + "_last_success", "")
                parse_checkpoint_timestamp(value)
        return checkpoints if checkpoints else self._get_defaults()
    
    def _get_defaults(self):
        """
        Отримати початкові defaults (24 години тому)
        
        Returns:
            Словник з дефолтними timestamps
        """
        from datetime import timedelta
        
        default_time = datetime.utcnow() - timedelta(hours=24)
        default_str = default_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        return {
            'gmail_last_success': default_str,
            'calendar_last_success': default_str,
            'tasks_last_success': default_str,
            'researcher_last_success': default_str,
            'reports_last_success': default_str
        }
    
    def get_timestamp(self, key):
        """
        Отримати timestamp для конкретного джерела
        
        Args:
            key: Назва ключа (наприклад 'gmail_last_success')
            
        Returns:
            ISO timestamp string
        """
        return self.checkpoints.get(key, self._get_defaults()[key])

