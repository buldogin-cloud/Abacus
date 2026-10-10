import unittest
from datetime import datetime, timezone
import sys
import types

# The production runtime is Python 3.11. The Mac system Python is 3.9 and
# cannot import one unrelated Google-auth annotation, so isolate this unit
# from external Google dependencies before importing the writer.
drive_module = types.ModuleType("integrations.drive_client")
drive_module.DriveClient = object
sys.modules.setdefault("integrations.drive_client", drive_module)
from modules.daily_briefing.checkpoints_writer import CheckpointsWriter


class _FakeDrive:
    def __init__(self, readback=None, initial="previous checkpoint"):
        self.readback = readback
        self.initial = initial
        self.written = None
        self.update_calls = 0

    def update_file_by_id(self, *, file_id, content, mime_type):
        self.update_calls += 1
        self.written = content
        return file_id

    def read_file(self, file_id):
        if self.written is None:
            return self.initial
        return self.written if self.readback is None else self.readback


def _writer(drive):
    writer = CheckpointsWriter.__new__(CheckpointsWriter)
    writer.drive = drive
    writer.now_utc = datetime(2026, 10, 10, tzinfo=timezone.utc)
    writer.timestamp = "2026-10-10T00:00:00Z"
    return writer


class CheckpointPersistenceTests(unittest.TestCase):
    def test_checkpoint_success_requires_exact_readback(self):
        self.assertTrue(_writer(_FakeDrive()).update_checkpoints({"gmail": True}))

    def test_checkpoint_mismatch_is_not_reported_as_success(self):
        self.assertFalse(
            _writer(_FakeDrive(readback="stale checkpoint")).update_checkpoints(
                {"gmail": True}
            )
        )

    def test_unreadable_checkpoint_is_not_blindly_overwritten(self):
        drive = _FakeDrive(initial=None)

        self.assertFalse(_writer(drive).update_checkpoints({"gmail": True}))
        self.assertEqual(0, drive.update_calls)


if __name__ == "__main__":
    unittest.main()
