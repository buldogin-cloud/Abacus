"""Minimal private identity map stored only in the owner-controlled Hub.

The mapping intentionally excludes source titles, descriptions, people, raw
anchors, statuses, and snapshots. GitHub variables never carry these rows.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .model import CellUpdate, ExcludedSourceRow, HistoryRow, SourceRecord
from .planner import SyncValidationError


MAPPING_SCHEMA_VERSION = "1"
MAPPING_HEADERS = (
    "schema_version",
    "anchor_sha256",
    "source_task_id",
    "hub_stable_id",
    "disposition",
    "reason_code",
    "last_source_fingerprint",
    "last_hub_state_id",
    "last_hub_row",
    "last_seen_at",
    "last_source_row",
    "identity_resolution",
)
CONTROL_HEADERS = ("setting_key", "setting_value")
RUNTIME_CONFIG_KEY = "runtime_config_json"
SYNC_LOG_HEADERS = (
    "verified_at_utc",
    "source_snapshot_sha256",
    "history_snapshot_sha256",
    "mapping_snapshot_sha256",
    "source_count",
    "mapped_count",
    "excluded_count",
    "appended_count",
    "idempotent_rerun_appends",
    "readback_status",
)


@dataclass(frozen=True)
class PrivateBootstrap:
    task_id_to_stable_id: dict[str, str]
    anchor_sha256_to_stable_id: dict[str, str]
    excluded_anchor_sha256_to_reason: dict[str, str]
    runtime_config: dict[str, Any]


def _pad(row: list[Any], width: int) -> list[Any]:
    return list(row) + [""] * max(0, width - len(row))


def load_private_bootstrap(rows: list[list[Any]]) -> PrivateBootstrap:
    """Read A:L identity rows and N:O runtime settings from the Hub."""
    if not rows:
        raise SyncValidationError("Private Hub bootstrap is missing")
    header = _pad(rows[0], 15)
    if tuple(str(value) for value in header[:12]) != MAPPING_HEADERS:
        raise SyncValidationError("Private mapping headers A:L do not match")
    if tuple(str(value) for value in header[13:15]) != CONTROL_HEADERS:
        raise SyncValidationError("Private runtime headers N:O do not match")

    task_map: dict[str, str] = {}
    anchor_map: dict[str, str] = {}
    exclusions: dict[str, str] = {}
    settings: dict[str, str] = {}
    for raw in rows[1:]:
        row = _pad(raw, 15)
        key = str(row[13] or "").strip()
        value = str(row[14] or "").strip()
        if key:
            if key in settings:
                raise SyncValidationError(f"Duplicate private runtime setting: {key}")
            settings[key] = value

        if not any(str(item or "").strip() for item in row[:12]):
            continue
        if str(row[0] or "").strip() != MAPPING_SCHEMA_VERSION:
            raise SyncValidationError("Unsupported private mapping schema version")
        anchor = str(row[1] or "").strip()
        task_id = str(row[2] or "").strip()
        stable_id = str(row[3] or "").strip()
        disposition = str(row[4] or "").strip().lower()
        reason_code = str(row[5] or "").strip()
        if not anchor:
            raise SyncValidationError("Every private mapping row needs anchor_sha256")
        if disposition == "include":
            if not stable_id:
                raise SyncValidationError("Included mapping lacks hub_stable_id")
            if anchor in anchor_map and anchor_map[anchor] != stable_id:
                raise SyncValidationError("Conflicting stable IDs for one anchor")
            anchor_map[anchor] = stable_id
            if task_id:
                if task_id in task_map and task_map[task_id] != stable_id:
                    raise SyncValidationError("Conflicting stable IDs for one Task ID")
                task_map[task_id] = stable_id
        elif disposition == "exclude":
            if not reason_code:
                raise SyncValidationError("Excluded mapping lacks reason_code")
            if anchor in exclusions and exclusions[anchor] != reason_code:
                raise SyncValidationError("Conflicting exclusion reasons for one anchor")
            exclusions[anchor] = reason_code
        else:
            raise SyncValidationError("Mapping disposition must be include or exclude")

    raw_runtime = settings.get(RUNTIME_CONFIG_KEY, "")
    if not raw_runtime:
        raise SyncValidationError("Private runtime_config_json is missing")
    try:
        runtime_config = json.loads(raw_runtime)
    except json.JSONDecodeError as exc:
        raise SyncValidationError("Private runtime_config_json is invalid") from exc
    if not isinstance(runtime_config, dict):
        raise SyncValidationError("Private runtime_config_json must be an object")
    return PrivateBootstrap(task_map, anchor_map, exclusions, runtime_config)


def build_mapping_update(
    *,
    mapping_sheet: str,
    records: list[SourceRecord],
    exclusions: list[ExcludedSourceRow],
    history_after: list[HistoryRow],
    seen_at: datetime | None = None,
) -> CellUpdate:
    """Build the minimal A:L projection after verified Hub readback."""
    if len(records) + len(exclusions) != 26:
        raise SyncValidationError("Private mapping must reconcile 26 source rows")
    seen_at = seen_at or datetime.now(timezone.utc)
    seen_iso = seen_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    current_by_stable: dict[str, list[HistoryRow]] = defaultdict(list)
    for row in history_after:
        if row.stable_id and row.current:
            current_by_stable[row.stable_id].append(row)

    output: list[list[Any]] = [list(MAPPING_HEADERS)]
    all_items: list[tuple[int, bool, SourceRecord | ExcludedSourceRow]] = [
        (record.source_row, True, record) for record in records
    ] + [(item.source_row, False, item) for item in exclusions]
    for _, included, item in sorted(all_items, key=lambda value: value[0]):
        stable_id = item.stable_id
        current_rows = current_by_stable.get(stable_id, []) if stable_id else []
        if included and len(current_rows) != 1:
            raise SyncValidationError(
                f"Cannot persist mapping: {stable_id!r} lacks one verified current state"
            )
        current = current_rows[0] if current_rows else None
        output.append(
            [
                MAPPING_SCHEMA_VERSION,
                item.anchor_sha256,
                item.source_task_id,
                stable_id,
                "include" if included else "exclude",
                "" if included else item.reason,
                item.source_fingerprint,
                current.state_id if current else "",
                str(current.sheet_row) if current else "",
                seen_iso,
                str(item.source_row),
                item.identity_resolution if included else "source_policy",
            ]
        )

    return CellUpdate(
        range_name=f"'{mapping_sheet}'!A1:L{len(output)}",
        values=tuple(tuple(row) for row in output),
    )


def build_verified_log_update(
    *,
    mapping_sheet: str,
    existing_log: list[list[Any]],
    verified_at_utc: str,
    source_snapshot_sha256: str,
    history_snapshot_sha256: str,
    mapping_snapshot_sha256: str,
    source_count: int,
    mapped_count: int,
    excluded_count: int,
    appended_count: int,
) -> CellUpdate:
    """Append a counters-and-hashes-only verified-run checkpoint in Q:Z."""
    validate_sync_log(existing_log)
    used_rows = [
        row_number
        for row_number, row in enumerate(existing_log[1:], start=2)
        if any(str(value or "").strip() for value in row)
    ]
    target_row = max(used_rows, default=1) + 1
    if target_row > 200:
        raise SyncValidationError("Private sync log capacity exceeded")
    values = ((
        verified_at_utc,
        source_snapshot_sha256,
        history_snapshot_sha256,
        mapping_snapshot_sha256,
        str(source_count),
        str(mapped_count),
        str(excluded_count),
        str(appended_count),
        "0",
        "verified",
    ),)
    return CellUpdate(
        range_name=f"'{mapping_sheet}'!Q{target_row}:Z{target_row}",
        values=values,
    )


def validate_sync_log(existing_log: list[list[Any]]) -> None:
    """Validate the private Q:Z checkpoint area before any Hub mutation."""
    if not existing_log:
        raise SyncValidationError("Private sync log is missing")
    header = _pad(existing_log[0], len(SYNC_LOG_HEADERS))
    if tuple(str(value) for value in header[:10]) != SYNC_LOG_HEADERS:
        raise SyncValidationError("Private sync log headers Q:Z do not match")
    used_rows = [
        row_number
        for row_number, row in enumerate(existing_log[1:], start=2)
        if any(str(value or "").strip() for value in row)
    ]
    if max(used_rows, default=1) + 1 > 200:
        raise SyncValidationError("Private sync log capacity exceeded")
