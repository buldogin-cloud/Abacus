"""Synthetic growth regressions; no spreadsheet clients or private identifiers."""
from dataclasses import replace
import unittest

from modules.hub_sync.config import ConfigError, HubSyncConfig
from modules.hub_sync.mapping_store import build_mapping_update, MAPPING_HEADERS
from modules.hub_sync.model import ExcludedSourceRow, SourceRecord
from modules.hub_sync.planner import HubSyncPlanner, SyncValidationError
import test_hub_sync as fixtures
from test_hub_sync import snapshot


def fixture(count):
    records = [SourceRecord(i + 2, f'task-{i}', snapshot(f'state-{i}'), 'raw',
                            source_task_id=f'id-{i}', anchor_sha256=f'anchor-{i}')
               for i in range(count - 1)]
    excluded = [ExcludedSourceRow(count + 1, 'synthetic policy exclusion',
                                 anchor_sha256='excluded-anchor', source_task_id='707')]
    history = HubSyncPlanner().plan(records, [], excluded=excluded).appended_rows
    return records, excluded, history


class GrowthTests(unittest.TestCase):
    def update(self, records, excluded, history, **kwargs):
        return build_mapping_update(mapping_sheet='Synthetic', records=records,
                                    exclusions=excluded, history_after=history, **kwargs)

    def test_legacy_current_and_growing_counts_preserve_exclusion_and_history(self):
        for count in (26, 29, 30, 40, 199):
            with self.subTest(count=count):
                records, excluded, history = fixture(count)
                before = list(history)
                update = self.update(records, excluded, history)
                self.assertEqual(update.range_name, f"'Synthetic'!A1:L{count + 1}")
                self.assertEqual(update.values[0], MAPPING_HEADERS)
                self.assertTrue(all(len(row) == 12 for row in update.values))
                self.assertEqual(update.values[-1][2:6],
                                 ('707', '', 'exclude', 'synthetic policy exclusion'))
                self.assertEqual([row[3] for row in update.values[1:-1]],
                                 [record.stable_id for record in records])
                self.assertEqual(history, before)
                self.assertEqual(HubSyncPlanner().plan(records, history, excluded=excluded).write_count, 0)

    def test_count_is_lower_bound_not_exact_or_disabled(self):
        records, excluded, history = fixture(29)
        self.update(records, excluded, history, minimum_source_count=29)
        with self.assertRaises(SyncValidationError):
            self.update(records, excluded, history, minimum_source_count=30)
        with self.assertRaises(SyncValidationError):
            self.update(records[:24], [], history)

    def test_explicit_lower_minimum_reaches_mapping_builder(self):
        records, _, history = fixture(26)
        self.update(records[:1], [], history, minimum_source_count=1)

    def test_invalid_minimum_rejected(self):
        records, excluded, history = fixture(26)
        for minimum in (0, -1, True, False, 26.5, '26', None):
            with self.subTest(minimum=minimum), self.assertRaises(SyncValidationError):
                self.update(records, excluded, history, minimum_source_count=minimum)

    def test_invalid_source_rows_rejected(self):
        records, excluded, history = fixture(29)
        for row in (0, 1, -1, True, '2', 2.5, records[1].source_row):
            with self.subTest(row=row), self.assertRaises(SyncValidationError):
                self.update([replace(records[0], source_row=row), *records[1:]], excluded, history)

    def test_duplicate_stable_task_and_anchor_ids_rejected(self):
        records, excluded, history = fixture(29)
        for field in ('stable_id', 'source_task_id', 'anchor_sha256'):
            with self.subTest(field=field), self.assertRaises(SyncValidationError):
                self.update([replace(records[0], **{field: getattr(records[1], field)}),
                             *records[1:]], excluded, history)
        with self.assertRaises(SyncValidationError):
            self.update(records, [replace(excluded[0], source_task_id=records[0].source_task_id)], history)

    def test_exclusion_requires_reason_and_distinct_row(self):
        records, excluded, history = fixture(29)
        for item in (replace(excluded[0], reason=' '),
                     replace(excluded[0], source_row=records[0].source_row)):
            with self.assertRaises(SyncValidationError):
                self.update(records, [item], history)

    def test_verified_current_history_required(self):
        records, excluded, history = fixture(29)
        for bad in (history[1:], [*history, history[0]]):
            with self.assertRaises(SyncValidationError):
                self.update(records, excluded, bad)

    def test_configuration_minimum_is_positive_integer_and_default_unchanged(self):
        raw = fixtures.ConfigTests().make_config()
        self.assertEqual(HubSyncConfig.from_raw(raw).minimum_source_count, 26)
        for minimum in (1, 26, 29, 201):
            self.assertEqual(HubSyncConfig.from_raw(dict(raw, minimum_source_count=minimum)).minimum_source_count, minimum)
        for minimum in (0, -1, True, 26.5, '26', None):
            with self.subTest(minimum=minimum), self.assertRaises(ConfigError):
                HubSyncConfig.from_raw(dict(raw, minimum_source_count=minimum))

    def test_capacity_overflow_and_missing_identity_rejected_before_mapping(self):
        records, excluded, history = fixture(200)
        with self.assertRaisesRegex(SyncValidationError, 'capacity'):
            self.update(records, excluded, history)
        records, excluded, history = fixture(29)
        with self.assertRaisesRegex(SyncValidationError, 'identity anchor'):
            self.update([replace(records[0], anchor_sha256=''), *records[1:]], excluded, history)

    def test_preflight_accounting_precedes_planning_and_execute(self):
        # The same pure validation protects runner dry-run and mutation paths.
        import inspect
        from modules.hub_sync import runner
        code = inspect.getsource(runner)
        self.assertLess(code.index('        validate_mapping_accounting('),
                        code.index('        plan = planner.plan('))
        self.assertIn('minimum_source_count=config.minimum_source_count,', code)

    def test_source_reader_does_not_truncate_at_capacity(self):
        from modules.hub_sync.runner import complete_source_range
        for source in ("Synthetic!A1:V200", "'Synthetic'!A1:V200", 'A1:V200', 'A1:V'):
            self.assertEqual(complete_source_range('Synthetic', source), "'Synthetic'!A1:V")
        for source in ('Other!A1:V200', 'A2:V200', 'A1:N200', 'A1', 'A1:V0'):
            with self.subTest(source=source), self.assertRaises(SyncValidationError):
                complete_source_range('Synthetic', source)

    def test_missing_stable_identity_rejected_but_anchor_fallback_preserved(self):
        with self.assertRaises(ValueError):
            SourceRecord(2, '', snapshot('invalid'), 'raw')
        records, excluded, history = fixture(29)
        changed = [replace(records[0], source_task_id='', identity_resolution='exact_anchor'), *records[1:]]
        update = self.update(changed, excluded, history)
        self.assertEqual(update.values[1][2], '')
        self.assertEqual(update.values[1][3], records[0].stable_id)
        self.assertEqual(update.values[1][11], 'exact_anchor')

    def test_overflow_after_gaps_cannot_be_reduced_to_capacity(self):
        from modules.hub_sync.mapping_store import validate_mapping_accounting
        records, excluded, _ = fixture(200)
        # Physical gaps do not alter the count of accounted canonical rows.
        sparse = [replace(record, source_row=record.source_row * 3) for record in records]
        excluded = [replace(excluded[0], source_row=1000)]
        with self.assertRaisesRegex(SyncValidationError, 'capacity'):
            validate_mapping_accounting(sparse, excluded)
