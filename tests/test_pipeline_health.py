import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from modules.daily_briefing import briefing
from modules.daily_briefing.collector import CollectedData, SourceResult


def collected(status='success'):
    return CollectedData('fixture', '2026-10-23T04:00:00Z', {
        name: SourceResult(name, status, '', '', '', [], connection_verified=True)
        for name in ('gmail', 'calendar', 'tasks', 'researcher')
    })


def handoff():
    return dict(storage_status='success', readback_ok=True, radar_status='success',
                radar_readback_ok=True, handoffs_written=0, priority_handoffs=0,
                handoffs_skipped_duplicate=0, errors=[])


class PipelineHealthTests(unittest.TestCase):
    def test_success_requires_both_readbacks(self):
        data = handoff()
        self.assertEqual('success', briefing.compute_run_status(collected(), data))
        for key in ('readback_ok', 'radar_readback_ok'):
            bad = {**data, key: False}
            self.assertNotEqual('success', briefing.compute_run_status(collected(), bad))

    def test_skipped_storage_and_errors_are_not_success(self):
        for changes in ({'storage_status': 'skipped'}, {'errors': ['failure']},
                        {'error': 'failure'}, {'radar_status': 'failed'}):
            self.assertNotEqual('success', briefing.compute_run_status(collected(), {**handoff(), **changes}))

    def run_mocked(self, data=None, storage=None, checkpoint_ok=True, argv=None):
        reader = Mock()
        reader.load_from_drive.return_value = {'gmail_last_success': '2026-10-22T00:00:00Z'}
        writer = Mock()
        writer.update_checkpoints.return_value = checkpoint_ok
        radar = dict(radar_rows_added=0, radar_rows_updated=0, radar_rows_total=0,
                     radar_status='success', readback_ok=True)
        with patch.object(briefing, 'CheckpointsReader', return_value=reader), \
             patch.object(briefing, 'CheckpointsWriter', return_value=writer), \
             patch.object(briefing, 'load_config', return_value={'delivery': {'send_telegram': False}}), \
             patch.object(briefing, 'collect_all', return_value=data or collected()), \
             patch.object(briefing, 'process_handoffs', return_value=storage or handoff()), \
             patch.object(briefing, 'process_radar', return_value=radar), \
             contextlib.redirect_stdout(io.StringIO()):
            return briefing.main(argv or []), writer

    def test_failed_storage_exits_nonzero_and_never_advances(self):
        code, writer = self.run_mocked(storage={**handoff(), 'storage_status': 'failed'})
        self.assertEqual(1, code)
        writer.update_checkpoints.assert_not_called()

    def test_partial_sources_exits_nonzero(self):
        code, _ = self.run_mocked(data=collected('partial'))
        self.assertEqual(1, code)

    def test_checkpoint_write_failure_exits_nonzero(self):
        code, writer = self.run_mocked(checkpoint_ok=False)
        self.assertEqual(1, code)
        self.assertEqual("failed", writer.log_handoff_run.call_args.args[0]["overall_status"])
        self.assertEqual("failed", writer.append_to_automation_log.call_args.args[0]["status"])

    def test_checkpoint_watermark_is_capture_start(self):
        code, writer = self.run_mocked()
        self.assertEqual(0, code)
        details = writer.update_checkpoints.call_args.args[2]
        self.assertEqual(writer.log_handoff_run.call_args.args[0]["started_at"], details["captured_until"])

    def test_success_writes_sanitized_report(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'health.json'
            code, _ = self.run_mocked(argv=['--report', str(path)])
            report = json.loads(path.read_text())
            self.assertEqual(0, code)
            self.assertEqual('success', report['status'])
            self.assertNotIn('records', report['sources']['gmail'])

    def test_dry_run_not_reported_as_persisted_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'health.json'
            code, writer = self.run_mocked(argv=['--no-drive', '--report', str(path)])
            self.assertEqual(0, code)
            self.assertEqual('dry_run_ok', json.loads(path.read_text())['status'])
            writer.update_checkpoints.assert_not_called()

    def test_checkpoint_read_failure_stops_before_collection(self):
        with patch.object(briefing, 'load_config', return_value={}), \
             patch.object(briefing, 'CheckpointsReader', side_effect=RuntimeError('private')), \
             patch.object(briefing, 'collect_all') as collect, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1, briefing.main([]))
            collect.assert_not_called()
