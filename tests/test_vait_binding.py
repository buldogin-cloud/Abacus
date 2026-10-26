import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from modules.command_center.report_gate import ReportValidationError
from modules.command_center.vait_receipts import assemble_manifest, save_manifest, verify_manifest
from modules.daily_briefing import vait_manifest, vait_postcheck

NOW = datetime.now(timezone.utc)
END = (NOW - timedelta(hours=1)).isoformat()
START = (NOW - timedelta(hours=25)).isoformat()


class VAITBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.units = []
        for task in ('vait_1', 'vait_2'):
            body = ('Synthetic checked summary for ' + task).encode()
            digest = hashlib.sha256(body).hexdigest()
            run = task + '__test_run'
            receipt = dict(schema_version=1, task_id=task, run_id=run,
                           window={'start': START, 'end': END}, captured_at=END,
                           sha256=digest, readback_sha256=digest,
                           stages=dict(source_check='success', analysis='success', storage='success',
                                       registry='not_required', delivery='success'),
                           source_evidence=[dict(source_ref='synthetic-source', verified=True, checked_at=END)],
                           content_review=dict(task_id=task, sha256=digest, status='verified'))
            (self.root / (task+'.body')).write_bytes(body)
            (self.root / (task+'.json')).write_text(json.dumps(receipt))
            self.units.append(dict(task_id=task, run_id=run, receipt_path=task+'.json', body_path=task+'.body'))

    def build(self, **kwargs):
        return assemble_manifest(self.root, window_start=kwargs.get('start', START),
                                 window_end=kwargs.get('end', END),
                                 units=kwargs.get('units', self.units), now=NOW)

    def receipt_change(self, key, value):
        path = self.root/'vait_2.json'
        value_dict = json.loads(path.read_text()); value_dict[key] = value
        path.write_text(json.dumps(value_dict))

    def test_valid_manifest_binds_existing_artifacts(self):
        manifest = self.build()
        self.assertEqual(2, manifest['schema_version'])
        self.assertEqual({'status':'receipt_verified', 'units_verified':2}, verify_manifest(manifest, self.root, now=NOW))
        self.assertEqual(64, len(manifest['units'][0]['receipt_sha256']))

    def test_legacy_manifest_still_verified(self):
        self.assertEqual(2, verify_manifest(dict(window_start=START, window_end=END, units=self.units), self.root, now=NOW)['units_verified'])

    def test_both_units_and_distinct_identities_required(self):
        for units in (self.units[:1], [self.units[0], self.units[0]], self.units+[self.units[0]], ['bad', self.units[1]]):
            with self.subTest(units=units), self.assertRaisesRegex(ReportValidationError, 'both_units_required'):
                self.build(units=units)

    def test_expected_run_not_inferred_from_receipt(self):
        units = copy.deepcopy(self.units); units[1]['run_id'] = 'vait_2__different'
        with self.assertRaisesRegex(ReportValidationError, 'run_mismatch'): self.build(units=units)

    def test_window_not_inferred_from_receipt(self):
        with self.assertRaisesRegex(ReportValidationError, 'window_mismatch'):
            self.build(start=(NOW-timedelta(hours=24)).isoformat())

    def test_stale_future_naive_invalid_window(self):
        for start,end in [('2020-01-01T00:00:00Z','2020-01-02T00:00:00Z'),
                          (START,(NOW+timedelta(hours=1)).isoformat()),
                          ('2020-01-01T00:00:00',END), (END,START)]:
            with self.subTest(end=end), self.assertRaises(ReportValidationError): self.build(start=start,end=end)

    def test_incomplete_stages_never_create_manifest(self):
        path = self.root/'vait_2.json'; original = json.loads(path.read_text())
        for stage in ('source_check','analysis','storage','registry','delivery'):
            receipt = copy.deepcopy(original); receipt['stages'][stage] = 'unknown'; path.write_text(json.dumps(receipt))
            with self.subTest(stage=stage), self.assertRaises(ReportValidationError): self.build()
        self.assertFalse((self.root/'manifest.json').exists())

    def test_wrong_topic_and_source_evidence_rejected(self):
        self.receipt_change('content_review', {})
        with self.assertRaisesRegex(ReportValidationError, 'topic_review_unverified'): self.build()

    def test_missing_and_malformed_receipt_fail_closed(self):
        (self.root/'vait_2.json').write_text('{broken')
        with self.assertRaises(ValueError): self.build()
        (self.root/'vait_2.json').unlink()
        with self.assertRaisesRegex(ReportValidationError,'artifact_missing'): self.build()

    def test_changed_receipt_after_binding_rejected(self):
        manifest = self.build()
        # Even a still-valid whitespace edit must not silently replace the bound artifact.
        path = self.root/'vait_1.json'; path.write_text(path.read_text()+' ')
        with self.assertRaisesRegex(ReportValidationError,'artifact_pin_mismatch'):
            verify_manifest(manifest,self.root,now=NOW)

    def test_changed_body_after_binding_rejected(self):
        manifest = self.build(); (self.root/'vait_1.body').write_text('changed')
        with self.assertRaisesRegex(ReportValidationError,'artifact_pin_mismatch'):
            verify_manifest(manifest,self.root,now=NOW)

    def test_missing_pin_rejected(self):
        manifest = self.build(); del manifest['units'][0]['receipt_sha256']
        with self.assertRaisesRegex(ReportValidationError,'artifact_pin_mismatch'):
            verify_manifest(manifest,self.root,now=NOW)

    def test_paths_cannot_escape_or_be_absolute(self):
        for path in ('../outside', str(self.root/'vait_1.json')):
            units=copy.deepcopy(self.units); units[0]['receipt_path']=path
            with self.subTest(path=path), self.assertRaises(ReportValidationError): self.build(units=units)

    def test_symlink_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            source=Path(outside)/'receipt.json'; source.write_text('{}')
            (self.root/'link').symlink_to(source)
            units=copy.deepcopy(self.units); units[0]['receipt_path']='link'
            with self.assertRaisesRegex(ReportValidationError,'artifact_outside_manifest_directory'): self.build(units=units)

    def test_save_retry_idempotent_conflict_does_not_overwrite(self):
        manifest=self.build(); target=self.root/'manifest.json'
        save_manifest(target,manifest,now=NOW); original=target.read_bytes()
        save_manifest(target,manifest,now=NOW)
        self.assertEqual(original,target.read_bytes())
        target.write_text('existing other manifest')
        with self.assertRaisesRegex(ReportValidationError,'manifest_conflict'): save_manifest(target,manifest,now=NOW)
        self.assertEqual('existing other manifest',target.read_text())

    def test_never_overwrites_input_artifact(self):
        manifest=self.build(); source=self.root/'vait_1.json'; original=source.read_bytes()
        with self.assertRaisesRegex(ReportValidationError,'manifest_overwrites_artifact'): save_manifest(source,manifest,now=NOW)
        self.assertEqual(original,source.read_bytes())

    def test_output_symlink_rejected(self):
        manifest=self.build(); target=self.root/'manifest.json'; target.symlink_to(self.root/'vait_1.json')
        with self.assertRaisesRegex(ReportValidationError,'manifest_target_symlink'): save_manifest(target,manifest,now=NOW)

    def test_cli_end_to_end_and_sanitized_postcheck(self):
        args=['--manifest-out',str(self.root/'manifest.json'),'--window-start',START,'--window-end',END]
        for u in self.units: args+=['--unit',u['task_id'],u['run_id'],u['receipt_path'],u['body_path']]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0,vait_manifest.main(args))
            self.assertEqual(0,vait_postcheck.main(['--manifest',str(self.root/'manifest.json')]))
        self.assertNotIn(str(self.root),out.getvalue())
        self.assertNotIn('Synthetic',out.getvalue())
        self.assertIn('receipt_verified',out.getvalue())

    def test_cli_malformed_second_unit_never_claims_partial_success(self):
        manifest=self.build(); target=self.root/'manifest.json'; save_manifest(target,manifest,now=NOW)
        (self.root/'vait_2.json').write_text('private patient content')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(1,vait_postcheck.main(['--manifest',str(target)]))
        result=json.loads(out.getvalue())
        self.assertEqual(0,result['units_verified']); self.assertEqual('blocked',result['status'])
        self.assertNotIn('private patient',out.getvalue())

    def test_health_output_cannot_replace_manifest_receipt_body_or_alias(self):
        manifest=self.build(); target=self.root/'manifest.json'; save_manifest(target,manifest,now=NOW)
        alias=self.root/'alias'; alias.symlink_to(self.root/'vait_1.body')
        hardlink=self.root/'hardlink'; hardlink.hardlink_to(self.root/'vait_2.json')
        sources=[target, self.root/'vait_1.json', self.root/'vait_1.body', self.root/'vait_2.json', self.root/'vait_2.body']
        originals={str(p):p.read_bytes() for p in sources}
        for output in sources+[alias,hardlink]:
            with self.subTest(output=output), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(1,vait_postcheck.main(['--manifest',str(target),'--report',str(output)]))
            self.assertEqual('health_output_overwrites_artifact',json.loads(out.getvalue())['error_code'])
            for p in sources: self.assertEqual(originals[str(p)],p.read_bytes())

    def test_malformed_manifest_hardlink_health_output_is_not_overwritten(self):
        target=self.root/'manifest.json'; target.write_text('private malformed content')
        output=self.root/'health.json'; output.hardlink_to(target)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(1,vait_postcheck.main(['--manifest',str(target),'--report',str(output)]))
        self.assertEqual('health_output_overwrites_artifact',json.loads(out.getvalue())['error_code'])
        self.assertEqual('private malformed content',target.read_text())
        self.assertNotIn('private malformed',out.getvalue())

    def test_no_artifact_creation_when_receipts_not_available(self):
        args=['--manifest-out',str(self.root/'manifest.json'),'--window-start',START,'--window-end',END,
              '--unit','vait_1','vait_1__missing','missing.json','missing.body']
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(1,vait_manifest.main(args))
        self.assertFalse((self.root/'manifest.json').exists())

if __name__ == '__main__': unittest.main()
