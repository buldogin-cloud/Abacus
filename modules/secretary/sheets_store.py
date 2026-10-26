"""Disabled dependency-injected Sheets adapter; no client, credentials or runner.

Lock excludes cooperating writers, not human edits. Sheets has no conditional
ETag cell write; reread narrows but cannot eliminate the final read/write race.
Postread cannot prove no racing edit was overwritten. Approve residual risk and
exact target before enabling. ReceiptLog must bind the existing canonical log.
"""
from contextlib import AbstractContextManager
from typing import Callable, Protocol
from .consumer import HEADERS, TASK_ID, EDITABLE, Plan, _index, fingerprint, same_row


class ReceiptLog(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put_once(self, key: str, receipt: dict) -> None: ...


class GuardedSheetsStore:
    def __init__(self, client, receipts: ReceiptLog,
                 writer_lock: Callable[[], AbstractContextManager], *,
                 enabled: bool = False, max_rows: int = 1000):
        if type(enabled) is not bool or type(max_rows) is not int or max_rows < 3:
            raise ValueError('invalid_adapter_configuration')
        if not callable(writer_lock):
            raise ValueError('writer_lock_required')
        self.client, self.receipts, self.writer_lock = client, receipts, writer_lock
        self.enabled, self.max_rows = enabled, max_rows
        self.range = f"'Реєстр'!A1:V{max_rows}"

    def _snapshot(self):
        values = self.client.read_range(self.range, value_render_option='FORMULA')
        if not isinstance(values, list) or not values or tuple(values[0]) != HEADERS:
            raise ValueError('registry_header_mismatch')
        if len(values) >= self.max_rows:
            raise ValueError('bounded_snapshot_may_be_truncated')
        rows, positions = [], {}
        for number, raw in enumerate(values[1:], start=2):
            if not isinstance(raw, list) or len(raw) > len(HEADERS):
                raise ValueError('invalid_registry_row')
            padded = ['' if v is None else v for v in raw]
            padded += [''] * (len(HEADERS) - len(padded))
            if not any(value != '' for value in padded):
                continue
            row = dict(zip(HEADERS, padded))
            rows.append(row)
            if row[TASK_ID]:
                if row[TASK_ID] in positions:
                    raise ValueError('duplicate_task_id')
                positions[row[TASK_ID]] = number
        _index(rows)
        return rows, positions, len(values) + 1

    def read(self, key):
        rows, _, _ = self._snapshot()
        return rows, self.receipts.get(key)

    def commit(self, plan: Plan):
        if not self.enabled:
            raise RuntimeError('adapter_disabled')
        with self.writer_lock():
            rows, positions, next_row = self._snapshot()
            if self.receipts.get(plan.key) is not None:
                raise RuntimeError('receipt_already_exists')
            existing, after = _index(rows).get(plan.task_id), plan.row
            before = fingerprint(existing) if existing is not None else None
            if before != plan.before_hash and not same_row(existing, after):
                raise RuntimeError('registry_changed_under_lock')
            if tuple(after) != HEADERS or after[TASK_ID] != plan.task_id:
                raise ValueError('invalid_plan_row')
            if same_row(existing, after):
                updates = []  # Interrupted target write: reconcile receipt only.
            elif existing is None:
                if next_row >= self.max_rows:
                    raise ValueError('no_verified_capacity_for_create')
                if plan.action != 'create' or any(after[h] for h in HEADERS[15:]):
                    raise ValueError('invalid_create_plan')
                updates = [{'range': f"'Реєстр'!A{next_row}:V{next_row}",
                            'values': [[after[h] for h in HEADERS]]}]
            else:
                if plan.action != 'update':
                    raise ValueError('invalid_update_plan')
                updates = []
                for column, header in enumerate(HEADERS):
                    if after[header] == existing[header]:
                        continue
                    if header not in EDITABLE or existing[header].startswith('='):
                        raise ValueError('protected_cell_change')
                    updates.append({'range': f"'Реєстр'!{chr(65+column)}{positions[plan.task_id]}",
                                    'values': [[after[header]]]})
            if updates:
                self.client.batch_update_values(updates, value_input_option='RAW')
            verified, _, _ = self._snapshot()
            if not same_row(_index(verified).get(plan.task_id), after):
                raise RuntimeError('target_readback_mismatch')
            self.receipts.put_once(plan.key, plan.receipt)
            if self.receipts.get(plan.key) != plan.receipt:
                raise RuntimeError('receipt_readback_mismatch')
