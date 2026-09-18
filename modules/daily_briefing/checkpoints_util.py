"""
Утиліта для роботи з checkpoints
"""

import re
from datetime import datetime
from integrations.drive_client import DriveClient


class CheckpointsReader:
    """Читач checkpoints з Google Drive"""
    
    # ID папок у Command Center (hardcoded для стабільності)
    COMMAND_CENTER_ID = '1eh47d2AtLZwfuYdthzVfJ-5pz_x2Qgf5'
    CADENCE_FOLDER_ID = '1FsfbDWu9mxRSVWr72SaxMVz4YAVv49zE'
    
    def __init__(self):
        """Ініціалізація"""
        self.drive = DriveClient()
        self.checkpoints = {}
    
    def load_from_drive(self):
        """
        Завантажити checkpoints з Drive
        
        Returns:
            Словник з timestamps або None при помилці
        """
        try:
            # Знаходимо файл checkpoints.md
            file = self.drive.find_file('checkpoints.md', self.CADENCE_FOLDER_ID)
            
            if not file:
                print("⚠️ Файл checkpoints.md не знайдено, використовуємо defaults")
                return self._get_defaults()
            
            # Читаємо вміст
            content = self.drive.read_file(file['id'])
            
            if not content:
                print("⚠️ Не вдалося прочитати checkpoints.md")
                return self._get_defaults()
            
            # Парсимо YAML з markdown
            self.checkpoints = self._parse_checkpoints(content)
            return self.checkpoints
            
        except Exception as e:
            print(f"❌ Помилка завантаження checkpoints: {e}")
            return self._get_defaults()
    
    def _parse_checkpoints(self, content):
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
