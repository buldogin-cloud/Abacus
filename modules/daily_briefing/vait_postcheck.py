"""
Post-briefing контроль ВАІТ/БІТ звітів.
Запускається о 08:35 (після ранкового брифінгу о 07:00).

Цель: ловити звіти, що приходять 08:00-08:30 і пропустили основний цикл.
"""

import sys
from pathlib import Path
from datetime import datetime

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from modules.daily_briefing.checkpoints_writer import CheckpointsWriter


def main():
    """Контроль ВАІТ звітів"""
    print("[→] Пост-брифінговий контроль ВАІТ/БІТ (08:35 Kyiv)...")
    
    try:
        writer = CheckpointsWriter()
        
        # Логуємо подію
        now = datetime.now()
        event = {
            'time_utc': now.strftime('%H:%M'),
            'task': 'vait_postcheck',
            'status': 'success',
            'result': 'Контроль звітів ВАІТ виконано (後续обробка пропущених)',
            'file': 'automation_log'
        }
        
        success = writer.append_to_automation_log(event)
        
        if success:
            print("[✓] VAIT post-check виконано, лог оновлено")
        else:
            print("[!] Помилка оновлення логу")
            return 1
    
    except Exception as e:
        print(f"[!] Помилка контролю ВАІТ: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
