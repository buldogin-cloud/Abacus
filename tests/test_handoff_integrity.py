import json
import types
import unittest
from unittest.mock import patch

from modules.daily_briefing.collector import CollectedData, SourceResult
from modules.daily_briefing import handoff


class FakeDrive:
    def __init__(self, initial='', corrupt=False, changed=False):
        self.initial = initial
        self.written = None
        self.writes = 0
        self.corrupt = corrupt
        self.changed = changed
        self.reads = 0

    def find_file(self, name, parent):
        today = handoff.datetime.now(handoff.ZoneInfo('Europe/Kyiv')).strftime('%Y-%m-%d')
        return {'id': 'old'} if name == f'handoff_{today}.jsonl' else None

    def read_file(self, file_id):
        self.assert_id(file_id)
        self.reads += 1
        if self.written is not None:
            return self.written.replace('New subject', 'tampered') if self.corrupt else self.written
        if self.changed and self.reads >= 3:
            return self.initial + '\n'
        return self.initial

    def assert_id(self, file_id):
        if file_id != 'old':
            raise AssertionError(file_id)

    def update_file_by_id(self, file_id, content, mime_type):
        self.assert_id(file_id)
        self.writes += 1
        self.written = content
        return file_id

    def write_file(self, **kwargs):
        raise AssertionError('Existing file must be updated by exact ID')


def record(mid='new'):
    return {'source': 'gmail', 'source_id': mid, 'message_id': mid,
            'thread_id': 'thread', 'subject': 'New subject',
            'factual_summary': 'Full source text', 'snippet': 'Short',
            'body': 'Entire original message', 'body_complete': True,
            'attachments': [{'filename': 'quote.pdf'}],
            'url': 'thread-url'}


def archive(mid='old'):
    return json.dumps({'source': 'gmail', 'handoff_id': 'old-handoff',
                       '_dedup_keys': {'source_id': mid, 'message_id': mid,
                                       'thread_id': 'thread', 'url': 'thread-url'}})


def process(drive, records=None):
    module = types.ModuleType('integrations.drive_client')
    module.DriveClient = lambda: drive
    source = SourceResult('gmail', 'success', 'now', 'start', 'end', records or [record()])
    with patch.dict('sys.modules', {'integrations.drive_client': module}), patch.object(handoff, '_load_registry_tasks', return_value=[]):
        return handoff.process_handoffs(CollectedData('run', 'now', {'gmail': source}))


class HandoffIntegrityTests(unittest.TestCase):
    def test_new_reply_in_same_thread_not_duplicate(self):
        drive = FakeDrive(archive())
        result = process(drive)
        self.assertEqual('success', result['storage_status'])
        self.assertEqual(1, result['handoffs_written'])
        lines = drive.written.splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual(archive(), lines[0])
        self.assertEqual('Full source text', json.loads(lines[1])['factual_summary'])
        self.assertEqual('Entire original message', json.loads(lines[1])['body'])
        self.assertEqual('not_analyzed', json.loads(lines[1])['attachments']['analysis_status'])

    def test_same_message_deduplicates(self):
        drive = FakeDrive(archive('new'))
        result = process(drive)
        self.assertEqual(1, result['handoffs_skipped_duplicate'])
        self.assertEqual(0, drive.writes)

    def test_two_distinct_messages_same_thread_within_run(self):
        drive = FakeDrive()
        result = process(drive, [record('one'), record('two')])
        self.assertEqual(2, result['handoffs_written'])

    def test_unreadable_existing_archive_blocks_write(self):
        drive = FakeDrive(None)
        self.assertEqual('failed', process(drive)['storage_status'])
        self.assertEqual(0, drive.writes)

    def test_malformed_existing_archive_blocks_write(self):
        for initial in ['{bad', '[]', '{"_dedup_keys": []}']:
            with self.subTest(initial=initial):
                drive = FakeDrive(initial)
                self.assertEqual('failed', process(drive)['storage_status'])
                self.assertEqual(0, drive.writes)

    def test_changed_archive_blocks_write(self):
        drive = FakeDrive(archive(), changed=True)
        self.assertEqual('failed', process(drive)['storage_status'])
        self.assertEqual(0, drive.writes)

    def test_same_ids_but_corrupted_content_not_verified(self):
        result = process(FakeDrive(archive(), corrupt=True))
        self.assertEqual('partial', result['storage_status'])
        self.assertFalse(result['readback_ok'])
        self.assertEqual(0, result['handoffs_written'])

    def test_invalid_record_not_success(self):
        result = process(FakeDrive(), [{'source': 'gmail'}])
        self.assertEqual('partial', result['storage_status'])
        self.assertFalse(result['readback_ok'])

    def test_calendar_version_dedup_and_legacy_upgrade(self):
        event = {'source': 'calendar', 'source_id': 'event-1', 'title': 'Meeting',
                 'factual_summary': 'Meeting at noon', 'start': '2026-10-23T12:00:00Z',
                 'end': '2026-10-23T13:00:00Z', 'detected_at': 'first', 'is_today': False}
        legacy = json.dumps({'source': 'calendar', '_dedup_keys': {'source_id': 'event-1'}})
        first = FakeDrive(legacy)
        self.assertEqual(1, process(first, [event])['handoffs_written'])
        saved = json.loads(first.written.splitlines()[-1])
        self.assertEqual(event['end'], saved['source_content']['end'])
        unchanged = dict(event, detected_at='later', is_today=True)
        same = FakeDrive(first.written)
        self.assertEqual(1, process(same, [unchanged])['handoffs_skipped_duplicate'])
        self.assertEqual(0, same.writes)
        changed = dict(unchanged, end='2026-10-23T14:00:00Z')
        updated = FakeDrive(first.written)
        self.assertEqual(1, process(updated, [changed])['handoffs_written'])
        self.assertTrue(updated.written.startswith(first.written))

    def test_research_content_change_and_intrarun_dedup(self):
        item = {'source': 'moz_ukraine', 'source_id': 'order', 'url': 'https://example.test/order',
                'title': 'Order', 'factual_summary': 'Original wording', 'order_no': '1'}
        drive = FakeDrive()
        self.assertEqual(1, process(drive, [item, dict(item)])['handoffs_written'])
        changed = dict(item, factual_summary='Amended wording')
        updated = FakeDrive(drive.written)
        self.assertEqual(1, process(updated, [changed])['handoffs_written'])

    def test_registry_error_blocks_write(self):
        drive = FakeDrive()
        module = types.ModuleType('integrations.drive_client')
        module.DriveClient = lambda: drive
        with patch.dict('sys.modules', {'integrations.drive_client': module}), patch.object(handoff, '_load_registry_tasks', side_effect=RuntimeError('unavailable')):
            result = handoff.process_handoffs(CollectedData('run', 'now'))
        self.assertEqual('failed', result['storage_status'])
        self.assertEqual(0, drive.writes)

if __name__ == '__main__':
    unittest.main()
