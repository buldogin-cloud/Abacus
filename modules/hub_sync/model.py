"""Pure data structures for the Hub synchronization.

No Google client is imported here.  Keeping the model pure makes the safety
rules testable without credentials or real registry data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceRecord:
    """One accounted source row converted to the Hub A:M snapshot shape."""

    source_row: int
    stable_id: str
    snapshot: tuple[Any, ...]
    source_status: str
    source_task_id: str = ""
    identity_anchor_json: str = ""
    anchor_sha256: str = ""
    identity_resolution: str = ""
    source_fingerprint: str = ""
    source_updated_raw: str = ""
    source_checked_raw: str = ""
    source_when: str = ""
    source_type: str = ""

    def __post_init__(self) -> None:
        if not self.stable_id.strip():
            raise ValueError("stable_id is required")
        if len(self.snapshot) != 13:
            raise ValueError("Hub snapshot must contain exactly 13 values (A:M)")


@dataclass(frozen=True)
class ExcludedSourceRow:
    """A source row intentionally excluded with a reviewable reason."""

    source_row: int
    reason: str
    identity_anchor_json: str = ""
    anchor_sha256: str = ""
    stable_id: str = ""
    source_task_id: str = ""
    source_status: str = ""
    source_fingerprint: str = ""
    source_updated_raw: str = ""
    source_checked_raw: str = ""
    source_when: str = ""
    source_type: str = ""


@dataclass(frozen=True)
class HistoryRow:
    """One row from Hub history A:P."""

    sheet_row: int
    snapshot: tuple[Any, ...]
    stable_id: str
    current: bool
    state_id: str

    def __post_init__(self) -> None:
        if len(self.snapshot) != 13:
            raise ValueError("History snapshot must contain exactly 13 values (A:M)")


@dataclass(frozen=True)
class CellUpdate:
    """A narrow values update that can be sent in one batch."""

    range_name: str
    values: tuple[tuple[Any, ...], ...]


@dataclass
class SyncPlan:
    """Validated delta and its non-sensitive accounting metadata."""

    source_count: int
    mapped_count: int
    excluded: list[ExcludedSourceRow]
    unchanged_count: int
    changed_stable_ids: list[str]
    appended_rows: list[HistoryRow]
    updates: list[CellUpdate]
    warnings: list[str] = field(default_factory=list)

    @property
    def write_count(self) -> int:
        return len(self.appended_rows)

    @property
    def accounted_count(self) -> int:
        return self.mapped_count + len(self.excluded)

    def summary(self) -> dict[str, Any]:
        """Return counts only; never expose registry or personnel content."""
        return {
            "source_count": self.source_count,
            "accounted_count": self.accounted_count,
            "mapped_count": self.mapped_count,
            "excluded_count": len(self.excluded),
            "unchanged_count": self.unchanged_count,
            "write_count": self.write_count,
            "warning_count": len(self.warnings),
        }
