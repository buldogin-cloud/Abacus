import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from integrations.google_auth import GoogleAuthError
from modules.command_center.health import safe_auth_diagnostic, STATUS_TEXT, BRIEFING_TEXT
from modules.command_center.report_gate import validate_report, ReportValidationError
from modules.command_center.schedule import should_run
from modules.command_center import auth_preflight
from modules.daily_briefing import briefing, vait_postcheck

BODY = b'A source-grounded report with reviewed findings.'
WINDOW = ('2026-10-24T05:00:00Z', '2026-10-25T05:00:00Z')


def receipt():
    digest = hashlib.sha256(BODY).hexdigest()
    return dict(schema_version=1, task_id='ai_radar', run_id='ai_radar__20261025T050000Z',
                window=dict(zip(('start', 'end'), WINDOW)), captured_at=WINDOW[1],
                sha256=digest, readback_sha256=digest,
                stages=dict(source_check='success', analysis='success', storage='success',
                            registry='not_required', delivery='success'),
                source_evidence=[dict(source_ref='https://example.org/source', verified=True, checked_at=WINDOW[1])],
                content_review=dict(task_id='ai_radar', sha256=digest, status='verified'))


def validate(data, body=BODY):
    return validate_report(data, body, expected_task='ai_radar',
                           expected_run='ai_radar__20261025T050000Z', expected_window=WINDOW)


class CommandCenterTests(unittest.TestCase):
    def test_auth_code_is_allowlisted(self):
        self.assertEqual({'auth_error_code': 'reauthorization_required', 'auth_source': 'GOOGLE_TOKEN'},
                         safe_auth_diagnostic(GoogleAuthError('reauthorization_required', 'GOOGLE_TOKEN')))
        evil = GoogleAuthError('SECRET_TOKEN', 'private/path/SECRET')
        self.assertNotIn('SECRET', json.dumps(safe_auth_diagnostic(evil)))
        self.assertEqual({}, safe_auth_diagnostic(RuntimeError('secret')))

    def test_pipeline_auth_report_retains_safe_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'health.json'
            with patch.object(briefing, 'load_config', return_value={}), \
                 patch.object(briefing, 'CheckpointsReader', side_effect=GoogleAuthError('malformed_token', 'GOOGLE_TOKEN')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, briefing.main(['--report', str(path)]))
            data = json.loads(path.read_text())
            self.assertEqual('malformed_token', data['auth_error_code'])
            self.assertEqual('checkpoint_read', data['error_stage'])

    def test_preflight_is_read_only_and_sanitized(self):
        service = MagicMock()
        service.files.return_value.get.return_value.execute.side_effect = [
            {'id': auth_preflight.CHECKPOINTS_FILE_ID, 'trashed': False},
            {'id': auth_preflight.TASKS_REGISTRY_SHEET_ID, 'trashed': False}]
        with patch.object(auth_preflight, 'get_credentials'), patch.object(auth_preflight, 'build', return_value=service):
            result = auth_preflight.run()
        self.assertEqual('read_access_verified', result['status'])
        self.assertEqual(2, service.files.return_value.get.call_count)
        for method in ('create', 'update', 'delete', 'list'):
            getattr(service.files.return_value, method).assert_not_called()
        with patch.object(auth_preflight, 'get_credentials', side_effect=GoogleAuthError('malformed_token', 'GOOGLE_TOKEN')), patch.object(auth_preflight, 'build') as build:
            self.assertEqual('malformed_token', auth_preflight.run()['auth_error_code'])
            build.assert_not_called()
        with patch.object(auth_preflight, 'get_credentials', side_effect=ValueError('SECRET')):
            self.assertNotIn('SECRET', json.dumps(auth_preflight.run()))

    def test_preflight_partial_or_trashed_is_blocked(self):
        service = MagicMock()
        service.files.return_value.get.return_value.execute.side_effect = [
            {'id': auth_preflight.CHECKPOINTS_FILE_ID, 'trashed': False}, RuntimeError('SECRET')]
        with patch.object(auth_preflight, 'get_credentials'), patch.object(auth_preflight, 'build', return_value=service):
            result = auth_preflight.run()
        self.assertEqual('blocked', result['status'])
        self.assertEqual({'checkpoint_metadata': 'read_verified'}, result['checks'])
        self.assertNotIn('SECRET', json.dumps(result))
        service.files.return_value.get.return_value.execute.side_effect = None
        service.files.return_value.get.return_value.execute.return_value = {'id': auth_preflight.CHECKPOINTS_FILE_ID, 'trashed': True}
        with patch.object(auth_preflight, 'get_credentials'), patch.object(auth_preflight, 'build', return_value=service):
            self.assertEqual('target_unverified', auth_preflight.run()['error_code'])

    def test_future_receipt_rejected(self):
        r = receipt(); r['captured_at'] = '2099-01-01T00:00:00Z'
        with self.assertRaisesRegex(ReportValidationError, 'future_capture'): validate(r)
        with self.assertRaisesRegex(ReportValidationError, 'future_window'):
            validate_report(receipt(), BODY, expected_task='ai_radar',
                            expected_run='ai_radar__20261025T050000Z', expected_window=WINDOW,
                            now=datetime(2026, 10, 24, tzinfo=timezone.utc))

    def test_complete_receipt(self):
        self.assertEqual('receipt_verified', validate(receipt())['status'])

    def test_wrong_task_copied_report_rejected(self):
        r = receipt(); r['task_id'] = 'equipment_need'
        with self.assertRaisesRegex(ReportValidationError, 'task_mismatch'): validate(r)
        r = receipt(); r['run_id'] = 'procurement__20261025T050000Z'
        with self.assertRaisesRegex(ReportValidationError, 'run_mismatch'): validate(r)

    def test_stale_window_rejected(self):
        r = receipt(); r['window']['end'] = '2026-10-07T05:00:00Z'
        with self.assertRaisesRegex(ReportValidationError, 'window_mismatch'): validate(r)

    def test_empty_changed_and_unverified_readback_rejected(self):
        with self.assertRaisesRegex(ReportValidationError, 'empty_body'): validate(receipt(), b'  ')
        with self.assertRaisesRegex(ReportValidationError, 'content_hash_mismatch'): validate(receipt(), b'old report')
        r = receipt(); r['readback_sha256'] = '0'*64
        with self.assertRaisesRegex(ReportValidationError, 'readback_hash_mismatch'): validate(r)

    def test_all_stages_and_topic_binding_required(self):
        for stage in ('source_check', 'analysis', 'storage', 'registry', 'delivery'):
            r = receipt(); r['stages'][stage] = 'unknown'
            with self.subTest(stage=stage), self.assertRaises(ReportValidationError): validate(r)
        r = receipt(); r['content_review']['task_id'] = 'old_task'
        with self.assertRaisesRegex(ReportValidationError, 'topic_review_unverified'): validate(r)

    def test_sources_and_timezone_required(self):
        r = receipt(); r['source_evidence'] = []
        with self.assertRaisesRegex(ReportValidationError, 'source_evidence_missing'): validate(r)
        r = receipt(); r['captured_at'] = '2026-10-25T05:00:00'
        with self.assertRaisesRegex(ReportValidationError, 'timezone_required'): validate(r)

    def test_no_receipts_never_claim_vait_success(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1, vait_postcheck.main([]))
        report = json.loads(output.getvalue())
        self.assertEqual('blocked', report['status'])
        self.assertEqual(0, report['units_verified'])
        self.assertEqual('receipt_manifest_not_configured', report['error_code'])

    def test_dst_one_slot_per_day(self):
        for day, summer in [('2026-10-24', True), ('2026-10-25', False),
                            ('2026-03-28', False), ('2026-03-29', True)]:
            now = datetime.fromisoformat(day+'T12:00:00+00:00')
            self.assertEqual(summer, should_run('handoff', '0 4 * * *', now))
            self.assertEqual(not summer, should_run('handoff', '0 5 * * *', now))
            self.assertEqual(summer, should_run('vait_postcheck', '35 5 * * *', now))
            self.assertEqual(not summer, should_run('vait_postcheck', '35 6 * * *', now))

    def test_delayed_execution_uses_scheduled_slot(self):
        now = datetime(2026, 10, 25, 9, tzinfo=timezone.utc)
        self.assertTrue(should_run('handoff', '0 5 * * *', now))
        self.assertFalse(should_run('handoff', '0 4 * * *', now))

    def test_status_does_not_claim_verified_integrations(self):
        self.assertIn('не підтверджує', STATUS_TEXT)
        self.assertIn('не перевіряє', BRIEFING_TEXT)
        source = Path('modules/telegram_webhook/server.py').read_text()
        self.assertNotIn("@app.route('/send-test'", source)
        self.assertNotIn('token_prefix', source)
        self.assertNotIn('_setup_google_credentials', source)
        self.assertIn('webhook_process_only', source)

if __name__ == '__main__': unittest.main()
