"""Idempotent, append-only planning for Command Center Hub history."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

from .model import CellUpdate, ExcludedSourceRow, HistoryRow, SourceRecord, SyncPlan


class SyncValidationError(RuntimeError):
    """Raised before any write when a fail-closed guard is violated."""


def _canon(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    if value is None:
        return ""
    return value


def make_state_id(
    stable_id: str,
    snapshot: Iterable[Any],
    material_indexes: Iterable[int] = range(11),
    predecessor_state_id: str = "",
) -> str:
    """Create a deterministic ID from material values only.

    Hub L contains a row hyperlink and M contains the material-change/import
    timestamp.  Neither is allowed to manufacture a new state on every poll or
    after a source sort, so the default fingerprint is A:K.
    """
    values = list(snapshot)
    payload = [
        _canon(stable_id),
        *(_canon(values[index]) for index in material_indexes),
        _canon(predecessor_state_id),
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    return "state_" + hashlib.sha256(encoded).hexdigest()[:24]


def material_fingerprint(
    snapshot: Iterable[Any],
    material_indexes: Iterable[int] = range(11),
) -> str:
    values = list(snapshot)
    encoded = json.dumps(
        [_canon(values[index]) for index in material_indexes],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_current(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "так"}


class HubSyncPlanner:
    """Build the only allowed writes: O=false and append A:P in history."""

    def __init__(
        self,
        *,
        history_sheet: str = "90 — ІСТОРІЯ",
        first_history_row: int = 6,
        last_history_row: int = 305,
        minimum_source_count: int = 26,
        material_indexes: tuple[int, ...] = tuple(range(11)),
    ) -> None:
        self.history_sheet = history_sheet
        self.first_history_row = first_history_row
        self.last_history_row = last_history_row
        self.minimum_source_count = minimum_source_count
        self.material_indexes = material_indexes

    def plan(
        self,
        source: list[SourceRecord],
        history: list[HistoryRow],
        *,
        excluded: list[ExcludedSourceRow] | None = None,
    ) -> SyncPlan:
        excluded = list(excluded or [])
        self._validate_accounting(source, excluded)
        self._validate_unique_source_ids(source)
        self._validate_snapshot_types(source)
        self._validate_history(history)

        history_by_stable: dict[str, list[HistoryRow]] = defaultdict(list)
        existing_state_ids: set[str] = set()
        occupied_rows: set[int] = set()
        for row in history:
            if row.stable_id:
                history_by_stable[row.stable_id].append(row)
            if row.state_id:
                if row.state_id in existing_state_ids:
                    raise SyncValidationError(
                        f"Duplicate history state ID detected: {row.state_id}"
                    )
                existing_state_ids.add(row.state_id)
            occupied_rows.add(row.sheet_row)

        free_rows = [
            row
            for row in range(self.first_history_row, self.last_history_row + 1)
            if row not in occupied_rows
        ]

        updates: list[CellUpdate] = []
        appended: list[HistoryRow] = []
        changed_ids: list[str] = []
        unchanged = 0

        for record in sorted(source, key=lambda item: item.source_row):
            prior_rows = history_by_stable.get(record.stable_id, [])
            current_rows = [row for row in prior_rows if row.current]
            effective_snapshot = self._snapshot_with_preserved_first_seen(
                record.snapshot, prior_rows
            )

            if len(current_rows) > 1:
                raise SyncValidationError(
                    f"Stable ID {record.stable_id!r} has multiple current states"
                )

            if current_rows and self._material_values(
                current_rows[0].snapshot
            ) == self._material_values(effective_snapshot):
                if _canon(current_rows[0].snapshot[11]) != _canon(effective_snapshot[11]):
                    updates.append(
                        CellUpdate(
                            range_name=(
                                f"'{self.history_sheet}'!L{current_rows[0].sheet_row}"
                            ),
                            values=((effective_snapshot[11],),),
                        )
                    )
                unchanged += 1
                continue

            predecessor_state_id = current_rows[0].state_id if current_rows else ""
            expected_state_id = make_state_id(
                record.stable_id,
                effective_snapshot,
                self.material_indexes,
                predecessor_state_id,
            )

            if expected_state_id in existing_state_ids:
                raise SyncValidationError(
                    f"Existing non-current state would be reused for {record.stable_id!r}; "
                    "manual review is required"
                )

            if not free_rows:
                raise SyncValidationError(
                    f"History capacity exceeded at row {self.last_history_row}"
                )
            target_row = free_rows.pop(0)

            for old in current_rows:
                updates.append(
                    CellUpdate(
                        range_name=f"'{self.history_sheet}'!O{old.sheet_row}",
                        values=((False,),),
                    )
                )

            appended_row = HistoryRow(
                sheet_row=target_row,
                snapshot=effective_snapshot,
                stable_id=record.stable_id,
                current=True,
                state_id=expected_state_id,
            )
            appended.append(appended_row)
            changed_ids.append(record.stable_id)
            updates.append(
                CellUpdate(
                    range_name=f"'{self.history_sheet}'!A{target_row}:P{target_row}",
                    values=((
                        *effective_snapshot,
                        record.stable_id,
                        True,
                        expected_state_id,
                    ),),
                )
            )
            existing_state_ids.add(expected_state_id)

        return SyncPlan(
            source_count=len(source) + len(excluded),
            mapped_count=len(source),
            excluded=excluded,
            unchanged_count=unchanged,
            changed_stable_ids=changed_ids,
            appended_rows=appended,
            updates=updates,
        )

    def verify_readback(
        self,
        source: list[SourceRecord],
        history_after: list[HistoryRow],
        plan: SyncPlan,
    ) -> None:
        """Require exact rows, one current state, and a no-op second plan."""
        by_stable: dict[str, list[HistoryRow]] = defaultdict(list)
        for row in history_after:
            if row.stable_id:
                by_stable[row.stable_id].append(row)

        for record in source:
            current_rows = [row for row in by_stable[record.stable_id] if row.current]
            if len(current_rows) != 1:
                raise SyncValidationError(
                    f"Readback failed: {record.stable_id!r} has "
                    f"{len(current_rows)} current states"
                )
            current = current_rows[0]
            effective_snapshot = self._snapshot_with_preserved_first_seen(
                record.snapshot, by_stable[record.stable_id]
            )
            if not current.state_id:
                raise SyncValidationError(
                    f"Readback failed: missing state ID for {record.stable_id!r}"
                )
            if self._material_values(current.snapshot) != self._material_values(
                effective_snapshot
            ):
                raise SyncValidationError(
                    f"Readback failed: snapshot mismatch for {record.stable_id!r}"
                )

        rerun = self.plan(source, history_after, excluded=plan.excluded)
        if rerun.write_count != 0:
            raise SyncValidationError(
                "Readback failed: immediate rerun is not idempotent"
            )

    def _validate_accounting(
        self,
        source: list[SourceRecord],
        excluded: list[ExcludedSourceRow],
    ) -> None:
        total = len(source) + len(excluded)
        if total < self.minimum_source_count:
            raise SyncValidationError(
                f"Expected at least {self.minimum_source_count} accounted source rows, "
                f"got {total}"
            )
        rows = [item.source_row for item in source] + [item.source_row for item in excluded]
        if len(rows) != len(set(rows)):
            raise SyncValidationError("A source row is mapped and/or excluded more than once")
        for item in excluded:
            if not item.reason.strip():
                raise SyncValidationError(
                    f"Excluded source row {item.source_row} has no reason"
                )

    @staticmethod
    def _validate_unique_source_ids(source: list[SourceRecord]) -> None:
        seen: dict[str, int] = {}
        for record in source:
            prior = seen.get(record.stable_id)
            if prior is not None:
                raise SyncValidationError(
                    f"Source rows {prior} and {record.source_row} share stable ID "
                    f"{record.stable_id!r}"
                )
            seen[record.stable_id] = record.source_row

    @staticmethod
    def _validate_snapshot_types(source: list[SourceRecord]) -> None:
        for record in source:
            for index, label in ((0, "first detected"), (12, "material time")):
                value = record.snapshot[index]
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or value <= 0
                ):
                    raise SyncValidationError(
                        f"Source row {record.source_row} has nonnumeric {label}"
                    )
            deadline = record.snapshot[7]
            if deadline != "" and (
                not isinstance(deadline, (int, float))
                or isinstance(deadline, bool)
                or deadline <= 0
            ):
                raise SyncValidationError(
                    f"Source row {record.source_row} has nonnumeric Hub deadline"
                )

    def _validate_history(self, history: list[HistoryRow]) -> None:
        rows = [item.sheet_row for item in history]
        if len(rows) != len(set(rows)):
            raise SyncValidationError("History contains duplicate physical row numbers")
        for row in rows:
            if row < self.first_history_row or row > self.last_history_row:
                raise SyncValidationError(
                    f"History row {row} is outside configured bounds "
                    f"{self.first_history_row}:{self.last_history_row}"
                )

    def _material_values(self, snapshot: Iterable[Any]) -> tuple[Any, ...]:
        values = list(snapshot)
        return tuple(_canon(values[index]) for index in self.material_indexes)

    @staticmethod
    def _snapshot_with_preserved_first_seen(
        snapshot: Iterable[Any], prior_rows: list[HistoryRow]
    ) -> tuple[Any, ...]:
        values = list(snapshot)
        if prior_rows:
            earliest = min(prior_rows, key=lambda row: row.sheet_row)
            values[0] = earliest.snapshot[0]
        return tuple(values)
