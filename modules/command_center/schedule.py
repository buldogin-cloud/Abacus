"""Map paired UTC cron triggers to a single Europe/Kyiv wall-clock schedule."""
import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TASKS = {"handoff": (7, 0), "vait_postcheck": (8, 35)}
CRONS = {"handoff": {"0 4 * * *", "0 5 * * *"},
         "vait_postcheck": {"35 5 * * *", "35 6 * * *"}}


def should_run(task, cron, now):
    if now.tzinfo is None:
        raise ValueError("timezone required")
    if cron not in CRONS[task]:
        return False
    # Use the scheduled slot rather than execution hour: Actions can be delayed.
    minute, hour, *_ = cron.split()
    slot = now.astimezone(timezone.utc).replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    local = slot.astimezone(ZoneInfo("Europe/Kyiv"))
    return (local.hour, local.minute) == TASKS[task]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("--cron", required=True)
    args = parser.parse_args()
    print("true" if should_run(args.task, args.cron, datetime.now(timezone.utc)) else "false")

if __name__ == "__main__":
    main()
