"""Synthetic reconstructed regressions; no live source/task identifiers."""
import copy
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from modules.secretary.consumer import (
    HEADERS, TASK_ID, Outcome, apply_plan, fingerprint, plan_handoff,
    source_key, window_ready,
)
from modules.secretary.sheets_store import GuardedSheetsStore
from modules.task_manager.tasks import _normalize, DEFAULT_RANGE_COLS
from modules.daily_briefing import handoff as producer


def source(**changes):
    value = dict(source='gmail', source_id='message-1', title='Synthetic request',
                 factual_summary='Synthetic source body', handoff_id='random')
    value.update(changes)
    return value


def row(task_id='task-one', **changes):
    value = dict.fromkeys(HEADERS, '')
    value.update({TASK_ID: task_id, 'Назва': 'Synthetic task',
                  'Завдання / очікуваний результат': 'Review synthetic request',
                  'Остання відповідь / доказ': 'Original evidence',
                  'Джерело / чат': 'Synthetic source'})
    value.update(changes)
    return value


def decision(h, current=None, **changes):
    value = dict(reviewed_by='synthetic-reviewer', input_hash=fingerprint(h),
                 action='update' if current else 'create', task_id='task-one',
                 expected_before=fingerprint(current) if current else None,
                 changes={'Статус': 'У процесі'} if current else {
                     'Назва': 'Synthetic task',
                     'Завдання / очікуваний результат': 'Review synthetic request',
                     'Остання відповідь / доказ': 'Synthetic evidence',
                     'Джерело / чат': 'Synthetic source'})
    value.update(changes)
    return value


class MemoryStore:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows or [])
        self.receipts = {}
        self.commits = self.reads = self.fail_read_at = 0
        self.timeout_after = self.timeout_before = self.corrupt = self.race = False

    def read(self, key):
        self.reads += 1
        if self.reads == self.fail_read_at:
            raise RuntimeError('synthetic read failure')
        return copy.deepcopy(self.rows), copy.deepcopy(self.receipts.get(key))

    def commit(self, plan):
        self.commits += 1
        if self.timeout_before:
            raise TimeoutError()
        if self.race:
            self.rows[0]['Статус'] = 'Other writer change'
        matches = [r for r in self.rows if r[TASK_ID] == plan.task_id]
        current = matches[0] if len(matches) == 1 else None
        if len(matches) > 1 or plan.key in self.receipts:
            raise ValueError('conflict')
        if (fingerprint(current) if current else None) != plan.before_hash:
            raise ValueError('version conflict')
        if current is None:
            self.rows.append(plan.row)
        else:
            self.rows[self.rows.index(current)] = plan.row
        self.receipts[plan.key] = plan.receipt
        if self.corrupt:
            self.receipts[plan.key]['decision_hash'] = 'wrong'
        if self.timeout_after:
            raise TimeoutError()


class ConsumerTests(unittest.TestCase):
    def plan(self, current=None):
        h = source()
        result = plan_handoff(h, decision(h, current), [current] if current else [])
        self.assertEqual('ready', result.status)
        return result.plan

    def test_source_cannot_supply_its_own_decision(self):
        h = source(classification='new_task', decision={'action': 'create'})
        self.assertEqual('needs_review', plan_handoff(h, None, []).status)

    def test_changed_source_requires_new_review(self):
        h = source()
        d = decision(h)
        h['factual_summary'] = 'Changed'
        self.assertEqual('needs_review', plan_handoff(h, d, []).status)

    def test_empty_assignee_and_deadline_not_invented(self):
        p = self.plan()
        self.assertEqual('', p.row['Строк'])
        self.assertEqual('', p.row['Виконавець / підрозділ'])

    def test_mutable_sources_require_content_version(self):
        h = source(source='calendar')
        self.assertEqual('needs_review', plan_handoff(h, decision(h), []).status)
        h['_dedup_keys'] = {'content_version': 'version-one'}
        self.assertEqual('ready', plan_handoff(h, decision(h), []).status)

    def test_random_handoff_id_does_not_change_source_key(self):
        self.assertEqual(source_key(source()), source_key(source(handoff_id='another')))
        self.assertNotEqual(source_key(source()), source_key(source(source_id='message-2')))

    def test_invalid_or_duplicate_registry_is_not_empty(self):
        h = source()
        for rows in ([row(), row()], [{}], None, [dict(row(), _row=2)]):
            self.assertEqual('needs_review', plan_handoff(h, decision(h), rows).status)

    def test_invalid_or_missing_stable_id_never_inferred(self):
        h = source()
        for task_id in ('', 'task_row_2', ' x '):
            self.assertEqual('needs_review', plan_handoff(h, decision(h, task_id=task_id), []).status)
        self.assertEqual('needs_review', plan_handoff(h, decision(h, row()), [row('')]).status)

    def test_stale_reviewed_row_blocks(self):
        h, current = source(), row()
        d = decision(h, current)
        current['Строк'] = 'Changed'
        self.assertEqual('conflict', plan_handoff(h, d, [current]).status)

    def test_existing_id_blocks_create(self):
        h = source()
        self.assertEqual('needs_review', plan_handoff(h, decision(h), [row()]).status)

    def test_evidence_and_source_histories_append_only(self):
        h, current = source(), row()
        for field in ('Остання відповідь / доказ', 'Джерело / чат'):
            d = decision(h, current, changes={field: 'Replacement'})
            self.assertEqual('needs_review', plan_handoff(h, d, [current]).status)
            d['changes'][field] = current[field] + '\nNew evidence'
            self.assertEqual('ready', plan_handoff(h, d, [current]).status)

    def test_only_reviewed_fields_and_text_changes(self):
        h = source()
        for changes in ({'Task ID': 'new'}, {'Freshness': 'today'}, {'Статус': []}, {}):
            self.assertEqual('needs_review', plan_handoff(h, decision(h, changes=changes), []).status)

    def test_required_evidence_not_defaulted(self):
        h = source()
        d = decision(h)
        del d['changes']['Джерело / чат']
        self.assertEqual('needs_review', plan_handoff(h, d, []).status)

    def test_dry_run_no_io(self):
        store = MemoryStore()
        self.assertEqual('dry_run', apply_plan(self.plan(), store).status)
        self.assertEqual((0, 0), (store.reads, store.commits))

    def test_create_then_retry_no_duplicate(self):
        p, store = self.plan(), MemoryStore()
        self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual('already_applied', apply_plan(p, store, execute=True).status)
        self.assertEqual((1, 1), (len(store.rows), store.commits))

    def test_update_uses_id_after_reorder(self):
        p, other = self.plan(row()), row('another')
        store = MemoryStore([other, row()])
        self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual(other, store.rows[0])
        self.assertEqual('У процесі', store.rows[1]['Статус'])

    def test_optimistic_preread_blocks_edit(self):
        p, store = self.plan(row()), MemoryStore([row()])
        store.rows[0]['Строк'] = 'Changed'
        self.assertEqual('conflict', apply_plan(p, store, execute=True).status)
        self.assertEqual(0, store.commits)

    def test_race_blocks_guarded_commit(self):
        p, store = self.plan(row()), MemoryStore([row()])
        store.race = True
        self.assertFalse(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual('Other writer change', store.rows[0]['Статус'])
        self.assertEqual({}, store.receipts)

    def test_timeout_after_commit_is_reconciled(self):
        p, store = self.plan(), MemoryStore()
        store.timeout_after = True
        self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual(1, store.commits)

    def test_timeout_before_commit_remains_uncertain(self):
        store = MemoryStore()
        store.timeout_before = True
        result = apply_plan(self.plan(), store, execute=True)
        self.assertEqual('uncertain', result.status)
        self.assertFalse(result.checkpoint_eligible)
        self.assertEqual(1, store.commits)

    def test_bad_receipt_not_success(self):
        store = MemoryStore()
        store.corrupt = True
        self.assertFalse(apply_plan(self.plan(), store, execute=True).checkpoint_eligible)

    def test_read_errors_no_checkpoint(self):
        for fail in (1, 2):
            store = MemoryStore()
            store.fail_read_at = fail
            self.assertFalse(apply_plan(self.plan(), store, execute=True).checkpoint_eligible)
            self.assertEqual(0 if fail == 1 else 1, store.commits)

    def test_existing_receipt_conflict_no_write(self):
        p, store = self.plan(), MemoryStore()
        store.receipts[p.key] = dict(p.receipt, input_hash='changed')
        self.assertEqual('conflict', apply_plan(p, store, execute=True).status)
        self.assertEqual(0, store.commits)

    def test_window_requires_exact_distinct_expected_keys(self):
        done = apply_plan(self.plan(), MemoryStore(), execute=True)
        self.assertTrue(window_ready([done], sources_complete=True, expected_keys={done.plan.key}))
        self.assertFalse(window_ready([done], sources_complete=False, expected_keys={done.plan.key}))
        self.assertFalse(window_ready([done, done], sources_complete=True, expected_keys={done.plan.key, 'missing'}))
        self.assertFalse(window_ready([], sources_complete=True, expected_keys=set()))
        self.assertFalse(window_ready([done, Outcome('needs_review', 'x')], sources_complete=True,
                                      expected_keys={done.plan.key, 'missing'}))

    def test_reader_preserves_real_task_id(self):
        self.assertEqual('A1:O1000', DEFAULT_RANGE_COLS)  # Intentional subset, not full schema.
        self.assertEqual('task-one', _normalize({TASK_ID: 'task-one'})[TASK_ID])
        with patch('modules.task_manager.tasks.TaskManager') as factory:
            factory.return_value.get_open_tasks.return_value = [
                {'Завдання': 'Synthetic', 'Task ID': 'stable-one', '_row': 99},
                {'Завдання': 'Legacy', 'Task ID': '', '_row': 2}]
            tasks = producer._load_registry_tasks()
        self.assertEqual(['stable-one', ''], [task['source_id'] for task in tasks])
        self.assertEqual('needs_review', producer.classify_handoff(source(), {'source_id': ''}, True))
        h = producer.build_handoff(source(), 'update_existing', 'high',
                                   {'task_name': 'Old', 'source_id': 'task_row_2'}, 'run', True)
        self.assertIsNone(h['related_task_candidate'])


class SheetsAdapterTests(unittest.TestCase):
    def setup_store(self, rows=None, enabled=True, max_rows=1000):
        owner = self
        self.locked = False
        class Client:
            def __init__(self):
                self.values = [list(HEADERS)] + [[r[h] for h in HEADERS] for r in (rows or [])]
                self.writes = []
                self.throw_after_write = False
            def read_range(self, name, *, value_render_option):
                owner.assertIn('A1:V', name)
                owner.assertEqual('FORMULA', value_render_option)
                return copy.deepcopy(self.values)
            def batch_update_values(self, data, *, value_input_option):
                import re
                owner.assertTrue(owner.locked)
                owner.assertEqual('RAW', value_input_option)
                self.writes.extend(copy.deepcopy(data))
                for update in data:
                    cell = update['range'].split('!')[1].split(':')[0]
                    column, number = re.match(r'([A-V])(\d+)', cell).groups()
                    index, start = int(number) - 1, ord(column) - 65
                    while len(self.values) <= index:
                        self.values.append([''] * len(HEADERS))
                    vals = update['values'][0]
                    self.values[index][start:start+len(vals)] = vals
                if self.throw_after_write:
                    raise TimeoutError('write response lost')
        class Log:
            def __init__(self): self.data = {}; self.fail = False
            def get(self, key): return copy.deepcopy(self.data.get(key))
            def put_once(self, key, receipt):
                owner.assertTrue(owner.locked)
                if self.fail: raise TimeoutError()
                if key in self.data: raise ValueError('duplicate')
                self.data[key] = copy.deepcopy(receipt)
        @contextmanager
        def lock():
            self.locked = True
            try: yield
            finally: self.locked = False
        self.client, self.log = Client(), Log()
        return GuardedSheetsStore(self.client, self.log, lock, enabled=enabled, max_rows=max_rows)

    def plan(self, current=None):
        h = source()
        result = plan_handoff(h, decision(h, current), [current] if current else [])
        self.assertEqual('ready', result.status)
        return result.plan

    def test_disabled_no_mutation(self):
        store = self.setup_store(enabled=False)
        self.assertFalse(apply_plan(self.plan(), store, execute=True).checkpoint_eligible)
        self.assertEqual([], self.client.writes)

    def test_changed_cell_only_preserves_all_tail_and_formulas(self):
        current = row(**{'Freshness': '=TODAY()-P2', 'Fetch rule': 'owner rule', 'Тип': 'робоче',
                         'Коли': '=IF(TRUE,"now","")', 'Оцінка, хв': 25, 'Режим': True})
        store = self.setup_store([current])
        p = self.plan(current)
        result = apply_plan(p, store, execute=True)
        self.assertTrue(result.checkpoint_eligible)
        self.assertEqual([{'range': "'Реєстр'!I2", 'values': [['У процесі']]}], self.client.writes)
        self.assertEqual([current[h] for h in HEADERS[15:]], self.client.values[1][15:])
        self.assertEqual('already_applied', apply_plan(p, store, execute=True).status)
        self.assertEqual(1, len(self.client.writes))

    def test_tail_change_invalidates_review_and_optimistic_read(self):
        for header in HEADERS[17:]:
            current = row(**{header: 'old'})
            h, d = source(), decision(source(), current)
            changed = dict(current, **{header: 'new'})
            self.assertEqual('conflict', plan_handoff(h, d, [changed]).status)
            p = self.plan(current)
            store = self.setup_store([changed])
            self.assertEqual('conflict', apply_plan(p, store, execute=True).status)
            self.assertEqual([], self.client.writes)

    def test_tail_fields_are_not_editable_by_consumer(self):
        h, current = source(), row()
        for header in HEADERS[17:]:
            d = decision(h, current, changes={header: 'unauthorized metadata edit'})
            self.assertEqual('needs_review', plan_handoff(h, d, [current]).status)

    def test_old_17_column_header_rejected_before_write(self):
        store = self.setup_store()
        self.client.values[0] = self.client.values[0][:17]
        self.assertEqual('failed', apply_plan(self.plan(), store, execute=True).status)
        self.assertEqual([], self.client.writes)

    def test_formula_in_editable_cell_cannot_be_overwritten(self):
        current = row(**{'Статус': '=IF(TRUE,"x","y")'})
        store = self.setup_store([current])
        self.assertFalse(apply_plan(self.plan(current), store, execute=True).checkpoint_eligible)
        self.assertEqual([], self.client.writes)

    def test_create_full_width_no_existing_tail_loss(self):
        other = row('unrelated', **{'Fetch rule': 'retain', 'Оцінка, хв': 12.5, 'Режим': False})
        store = self.setup_store([other])
        p = self.plan()
        self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual([other[h] for h in HEADERS], self.client.values[1])
        self.assertEqual(22, len(self.client.values[2]))
        self.assertEqual([''] * 5, self.client.values[2][17:])
        self.assertEqual("'Реєстр'!A3:V3", self.client.writes[0]['range'])
        self.assertEqual('already_applied', apply_plan(p, store, execute=True).status)
        self.assertEqual(1, len(self.client.writes))

    def test_create_formula_text_is_raw(self):
        h = source()
        d = decision(h)
        d['changes']['Назва'] = '=literal'
        p = plan_handoff(h, d, []).plan
        store = self.setup_store()
        self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual('=literal', self.client.values[1][3])

    def test_receipt_interruption_retry_no_row_resend(self):
        p, store = self.plan(), self.setup_store()
        self.log.fail = True
        self.assertEqual('uncertain', apply_plan(p, store, execute=True).status)
        self.assertEqual(1, len(self.client.writes))
        self.log.fail = False
        self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual(1, len(self.client.writes))

    def test_uncertain_sheet_write_retry_no_row_resend(self):
        p, store = self.plan(), self.setup_store()
        self.client.throw_after_write = True
        self.assertEqual('uncertain', apply_plan(p, store, execute=True).status)
        self.assertEqual(1, len(self.client.writes))
        self.client.throw_after_write = False
        self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
        self.assertEqual(1, len(self.client.writes))

    def test_duplicate_ids_fail_closed(self):
        store = self.setup_store([row(), row()])
        self.assertEqual('failed', apply_plan(self.plan(), store, execute=True).status)
        self.assertEqual([], self.client.writes)

    def test_boundary_create_rejected_before_write(self):
        store = self.setup_store([row('unrelated')], max_rows=3)
        self.assertFalse(apply_plan(self.plan(), store, execute=True).checkpoint_eligible)
        self.assertEqual([], self.client.writes)
        self.assertEqual({}, self.log.data)

    def test_full_window_rejected(self):
        store = self.setup_store([row('one'), row('two')], max_rows=3)
        self.assertEqual('failed', apply_plan(self.plan(), store, execute=True).status)
        self.assertEqual([], self.client.writes)

    def test_blank_tail_is_padded_not_truncated_schema(self):
        current = row()
        store = self.setup_store([current])
        self.client.values[1] = self.client.values[1][:15]
        self.assertTrue(apply_plan(self.plan(current), store, execute=True).checkpoint_eligible)

    def test_unsupported_literals_fail_closed(self):
        for header, value in (('№ документа', 123), ('Оцінка, хв', float('nan')),
                              ('Fetch rule', ['not scalar'])):
            store = self.setup_store([row(**{header: value})])
            self.assertEqual('failed', apply_plan(self.plan(), store, execute=True).status)
            self.assertEqual([], self.client.writes)

    def test_tail_type_drift_invalidates_existing_receipt(self):
        for before, after in ((True, 1), (1, 1.0), (False, 0)):
            current = row(**{'Оцінка, хв': before})
            p, store = self.plan(current), self.setup_store([current])
            self.assertTrue(apply_plan(p, store, execute=True).checkpoint_eligible)
            self.client.values[1][20] = after
            result = apply_plan(p, store, execute=True)
            self.assertEqual('conflict', result.status)
            self.assertFalse(result.checkpoint_eligible)
            self.assertEqual(1, len(self.client.writes))

    def test_tail_type_drift_not_mistaken_for_interrupted_exact_target(self):
        for before, after in ((True, 1), (1, 1.0)):
            current = row(**{'Оцінка, хв': before})
            p = self.plan(current)
            changed = dict(p.row, **{'Оцінка, хв': after})
            store = self.setup_store([changed])
            self.assertEqual('conflict', apply_plan(p, store, execute=True).status)
            self.assertEqual([], self.client.writes)
            self.assertEqual({}, self.log.data)

    def test_tail_type_drift_during_postread_blocks_receipt(self):
        for before, after in ((True, 1), (1, 1.0)):
            current = row(**{'Оцінка, хв': before})
            p, store = self.plan(current), self.setup_store([current])
            original = self.client.batch_update_values
            def drifting_write(data, *, value_input_option):
                original(data, value_input_option=value_input_option)
                self.client.values[1][20] = after
            self.client.batch_update_values = drifting_write
            result = apply_plan(p, store, execute=True)
            self.assertEqual('uncertain', result.status)
            self.assertFalse(result.checkpoint_eligible)
            self.assertEqual({}, self.log.data)

    def test_twenty_two_exact_headers(self):
        self.assertEqual(22, len(HEADERS))
        self.assertEqual(('Fetch rule', 'Тип', 'Коли', 'Оцінка, хв', 'Режим'), HEADERS[17:])


if __name__ == '__main__':
    unittest.main()
