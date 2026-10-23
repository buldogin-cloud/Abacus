import unittest
from datetime import datetime, timezone
from modules.daily_briefing.checkpoints_util import (
    CheckpointsReader, earliest_checkpoint_timestamp, parse_checkpoint_timestamp,
)

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


class CheckpointTimestampTests(unittest.TestCase):
    def test_offsets_compared_as_instants_not_strings(self):
        self.assertEqual('2026-10-22T23:30:00Z', earliest_checkpoint_timestamp(
            ['2026-10-22T23:45:00Z', '2026-10-23T01:30:00+02:00'], now=NOW))

    def test_equivalent_instants_are_normalized(self):
        self.assertEqual('2026-10-23T10:00:00Z', earliest_checkpoint_timestamp(
            ['2026-10-23T12:00:00+02:00', '2026-10-23T06:00:00-04:00'], now=NOW))

    def test_future_checkpoint_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'future'):
            parse_checkpoint_timestamp('2026-10-23T12:00:01Z', now=NOW)

    def test_exact_boundary_allowed(self):
        self.assertEqual(NOW, parse_checkpoint_timestamp('2026-10-23T14:00:00+02:00', now=NOW))

    def test_invalid_or_naive_checkpoint_rejected_without_echo(self):
        for value in ('private-provider-payload', '2026-10-23T10:00:00', None):
            with self.subTest(value=value), self.assertRaises(RuntimeError) as error:
                parse_checkpoint_timestamp(value, now=NOW)
            self.assertNotIn('private-provider-payload', str(error.exception))

    def test_strict_reader_rejects_future_checkpoint(self):
        content = '```yaml\n' + '\n'.join(
            key + '_last_success: 9999-01-01T00:00:00Z'
            for key in ('gmail', 'calendar', 'tasks', 'researcher')) + '\n```'
        reader = CheckpointsReader.__new__(CheckpointsReader)
        with self.assertRaisesRegex(RuntimeError, 'future'):
            reader._parse_checkpoints(content, strict=True)


if __name__ == '__main__':
    unittest.main()
