"""CLI runner for deterministic Secretary -> Hub synchronization.

Dry-run is the default.  A real write requires the explicit ``--execute`` flag.
Only counts and operational status are written to logs/reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.hub_sync.config import HubSyncConfig, pad
from modules.hub_sync.model import HistoryRow
from modules.hub_sync.mapping_store import (
    MAPPING_HEADERS,
    build_mapping_update,
    build_verified_log_update,
    load_private_bootstrap,
    validate_sync_log,
)
from modules.hub_sync.planner import HubSyncPlanner, SyncValidationError, parse_current


_A1_ROW_RANGE = re.compile(
    r"^'(?P<sheet>[^']+)'!(?P<start_col>[A-Z]+)(?P<start_row>\d+)"
    r"(?::(?P<end_col>[A-Z]+)(?P<end_row>\d+))?$"
)

_A1_GRID_RANGE = re.compile(
    r"^(?:'(?P<sheet>[^']+)'!)?"
    r"(?P<grid>[A-Z]+\d+(?::[A-Z]+\d+)?)$"
)


def column_index(label: str) -> int:
    result = 0
    for char in label:
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def qualify_guard_range(sheet: str, configured_range: str) -> str:
    """Return one validated A1 range without accidentally prefixing it twice."""
    match = _A1_GRID_RANGE.fullmatch(configured_range.strip())
    if not match:
        raise SyncValidationError("Today guard range is not valid A1 notation")
    configured_sheet = match.group("sheet")
    if configured_sheet and configured_sheet != sheet:
        raise SyncValidationError("Today guard range targets a different sheet")
    escaped_sheet = sheet.replace("'", "''")
    return f"'{escaped_sheet}'!{match.group('grid')}"


def extended_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"numberValue": value}
    return {"stringValue": str(value or "")}


def atomic_requests_for_plan(
    updates: list[Any],
    sheet_ids: dict[str, int],
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for update in updates:
        match = _A1_ROW_RANGE.match(update.range_name)
        if not match:
            raise SyncValidationError(
                f"Unsupported update range shape: {update.range_name!r}"
            )
        sheet = match.group("sheet")
        if sheet not in sheet_ids:
            raise SyncValidationError(f"Hub sheet not found: {sheet!r}")
        start_row = int(match.group("start_row")) - 1
        end_row = int(match.group("end_row") or match.group("start_row"))
        start_col = column_index(match.group("start_col"))
        end_col = column_index(match.group("end_col") or match.group("start_col")) + 1
        values = [
            {"values": [{"userEnteredValue": extended_value(value)} for value in row]}
            for row in update.values
        ]
        if len(values) != end_row - start_row:
            raise SyncValidationError("Update row count does not match target range")
        if any(len(row["values"]) != end_col - start_col for row in values):
            raise SyncValidationError("Update column count does not match target range")
        requests.append(
            {
                "updateCells": {
                    "range": {
                        "sheetId": sheet_ids[sheet],
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_col,
                        "endColumnIndex": end_col,
                    },
                    "rows": values,
                    "fields": "userEnteredValue",
                }
            }
        )
    return requests


def assert_sync_write_boundary(
    updates: list[Any],
    *,
    history_sheet: str,
    mapping_sheet: str,
) -> None:
    """Fail closed if an update could touch a frontend or unrelated sheet."""
    for update in updates:
        match = _A1_ROW_RANGE.match(update.range_name)
        if not match:
            raise SyncValidationError("Unparseable update outside sync boundary")
        sheet = match.group("sheet")
        start_col = match.group("start_col")
        end_col = match.group("end_col") or start_col
        if sheet == history_sheet and (start_col, end_col) in {
            ("A", "P"),
            ("L", "L"),
            ("O", "O"),
        }:
            continue
        if sheet == mapping_sheet and (start_col, end_col) in {
            ("A", "L"),
            ("Q", "Z"),
        }:
            continue
        raise SyncValidationError(
            f"Sync write boundary rejected range {update.range_name!r}"
        )


def parse_history(rows: list[list[Any]], first_row: int) -> list[HistoryRow]:
    result: list[HistoryRow] = []
    for offset, raw in enumerate(rows, start=first_row):
        row = pad(raw, 16)
        if not any(str(value or "").strip() for value in row):
            continue
        result.append(
            HistoryRow(
                sheet_row=offset,
                snapshot=tuple(row[:13]),
                stable_id=str(row[13] or "").strip(),
                current=parse_current(row[14]),
                state_id=str(row[15] or "").strip(),
            )
        )
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitized_http_error(exc: Exception) -> dict[str, Any]:
    """Return non-sensitive HTTP diagnostics suitable for public CI logs."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if not isinstance(status, int):
        return {}
    category = {
        400: "invalid_request",
        401: "authentication",
        403: "permission_or_scope",
        404: "resource_unavailable",
        409: "conflict",
        429: "rate_limited",
        500: "google_service_error",
        502: "google_service_error",
        503: "google_service_error",
        504: "google_service_error",
    }.get(status, "http_error")
    return {"http_status": status, "http_category": category}


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Verified JSON mapping path")
    parser.add_argument("--execute", action="store_true", help="Apply validated Hub writes")
    parser.add_argument("--report", help="Write a sanitized JSON run report")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Keep the pure planner/request builders importable without Google client
    # initialization; the scheduled job uses Python 3.11 per workflow.
    from integrations.sheets_client import SheetsClient

    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {
        "started_at_utc": utc_now(),
        "mode": "execute" if args.execute else "dry_run",
        "status": "error",
        "checkpoint_advanced": False,
    }
    stage = "initialize"
    try:
        source_id = os.environ.get("SECRETARY_SPREADSHEET_ID", "").strip()
        hub_id = os.environ.get("HUB_SPREADSHEET_ID", "").strip()
        if not source_id or not hub_id:
            raise SyncValidationError(
                "SECRETARY_SPREADSHEET_ID and HUB_SPREADSHEET_ID are required"
            )
        if source_id == hub_id:
            raise SyncValidationError("Source and Hub spreadsheet IDs must differ")

        source_client = SheetsClient(source_id)
        hub_client = SheetsClient(hub_id)
        public_contract = json.loads(Path(args.config).read_text(encoding="utf-8"))
        mapping_sheet = str(
            public_contract.get("mapping_sheet", "98 — SECRETARY SYNC")
        )
        stage = "hub_sheet_metadata"
        if mapping_sheet not in hub_client.get_sheet_ids():
            raise SyncValidationError(
                "Private Hub bootstrap tab is missing; recurring sync cannot create it"
            )
        stage = "hub_private_bootstrap"
        private_rows = hub_client.read_range(f"'{mapping_sheet}'!A1:O200")
        bootstrap = load_private_bootstrap(private_rows)
        stage = "hub_verified_log"
        validate_sync_log(
            hub_client.read_range(f"'{mapping_sheet}'!Q1:Z200")
        )
        today_sheet = str(bootstrap.runtime_config.get("today_sheet", "")).strip()
        today_guard_range = str(
            bootstrap.runtime_config.get("today_guard_range", "A1:N47")
        ).strip()
        if not today_sheet:
            raise SyncValidationError("Private runtime config lacks today_sheet")
        stage = "hub_today_sheet_metadata"
        if today_sheet not in hub_client.get_sheet_ids():
            raise SyncValidationError("Configured Today sheet is unavailable")
        today_a1 = qualify_guard_range(today_sheet, today_guard_range)
        stage = "hub_today_guard"
        today_formulas_before = hub_client.read_user_entered_values(today_a1)
        config = HubSyncConfig.load_with_private_bootstrap(
            args.config,
            runtime_config=bootstrap.runtime_config,
            task_id_to_stable_id=bootstrap.task_id_to_stable_id,
            anchor_sha256_to_stable_id=bootstrap.anchor_sha256_to_stable_id,
            excluded_anchor_sha256_to_reason=(
                bootstrap.excluded_anchor_sha256_to_reason
            ),
        )
        stage = "secretary_sheet_metadata"
        source_sheet_ids = source_client.get_sheet_ids()
        if config.source_sheet not in source_sheet_ids:
            raise SyncValidationError("Secretary source sheet ID is unavailable")
        source_sheet_id = source_sheet_ids[config.source_sheet]
        stage = "secretary_snapshot_first"
        first_source = source_client.read_range(config.source_range)
        stage = "secretary_snapshot_second"
        second_source = source_client.read_range(config.source_range)
        if first_source != second_source:
            raise SyncValidationError("Secretary range changed between preflight reads")

        checked_at = datetime.now(timezone.utc)
        records, exclusions = config.adapt_source_rows(
            first_source,
            checked_at=checked_at,
            source_spreadsheet_id=source_id,
            source_sheet_id=source_sheet_id,
        )
        history_range = (
            f"'{config.history_sheet}'!A{config.history_first_row}:"
            f"P{config.history_last_row}"
        )
        stage = "hub_history"
        history = parse_history(
            hub_client.read_range(history_range),
            config.history_first_row,
        )
        planner = HubSyncPlanner(
            history_sheet=config.history_sheet,
            first_history_row=config.history_first_row,
            last_history_row=config.history_last_row,
            minimum_source_count=config.minimum_source_count,
        )
        plan = planner.plan(records, history, excluded=exclusions)
        report.update(plan.summary())

        if not args.execute:
            report.update(status="dry_run_ok", finished_at_utc=utc_now())
            write_report(args.report, report)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0

        # Lock/re-read precondition: source and history must still produce the
        # exact same plan immediately before the atomic write.
        source_prewrite = source_client.read_range(config.source_range)
        history_prewrite = parse_history(
            hub_client.read_range(history_range),
            config.history_first_row,
        )
        if source_prewrite != first_source:
            raise SyncValidationError("Secretary source changed before write")
        prewrite_plan = planner.plan(records, history_prewrite, excluded=exclusions)
        if prewrite_plan.updates != plan.updates:
            raise SyncValidationError("Hub history changed before write")

        if plan.updates:
            assert_sync_write_boundary(
                plan.updates,
                history_sheet=config.history_sheet,
                mapping_sheet=config.mapping_sheet,
            )
            requests = atomic_requests_for_plan(plan.updates, hub_client.get_sheet_ids())
            try:
                hub_client.atomic_batch_update(requests)
            except Exception:
                # The transport can fail after Google accepted the batch.  Read
                # P/current state first; never retry blindly and duplicate rows.
                uncertain_history = parse_history(
                    hub_client.read_range(history_range),
                    config.history_first_row,
                )
                planner.verify_readback(records, uncertain_history, plan)

        source_after = source_client.read_range(config.source_range)
        if source_after != first_source:
            raise SyncValidationError(
                "Secretary source changed during sync; no checkpoint may advance"
            )
        history_after = parse_history(
            hub_client.read_range(history_range),
            config.history_first_row,
        )
        planner.verify_readback(records, history_after, plan)

        # Persist the private identity/checkpoint table only after the history
        # write passed exact readback.  A source-policy exclusion is accounted,
        # not an unresolved partial run.
        mapping_update = build_mapping_update(
            mapping_sheet=config.mapping_sheet,
            records=records,
            exclusions=exclusions,
            history_after=history_after,
        )
        assert_sync_write_boundary(
            [mapping_update],
            history_sheet=config.history_sheet,
            mapping_sheet=config.mapping_sheet,
        )
        hub_client.atomic_batch_update(
            atomic_requests_for_plan([mapping_update], hub_client.get_sheet_ids())
        )
        mapping_readback = hub_client.read_range(
            f"'{config.mapping_sheet}'!A1:L{len(mapping_update.values)}"
        )
        expected_mapping = [list(row) for row in mapping_update.values]
        normalized_readback = [pad(row, len(MAPPING_HEADERS)) for row in mapping_readback]
        if normalized_readback != expected_mapping:
            raise SyncValidationError("Private mapping readback mismatch")
        today_formulas_after = hub_client.read_user_entered_values(today_a1)
        if today_formulas_after != today_formulas_before:
            raise SyncValidationError("Today formulas changed during Hub sync")

        history_log_projection = [
            [
                row.sheet_row,
                *row.snapshot,
                row.stable_id,
                row.current,
                row.state_id,
            ]
            for row in history_after
        ]
        existing_log = hub_client.read_range(
            f"'{config.mapping_sheet}'!Q1:Z200"
        )
        log_update = build_verified_log_update(
            mapping_sheet=config.mapping_sheet,
            existing_log=existing_log,
            verified_at_utc=utc_now(),
            source_snapshot_sha256=canonical_sha256(first_source),
            history_snapshot_sha256=canonical_sha256(history_log_projection),
            mapping_snapshot_sha256=canonical_sha256(expected_mapping),
            source_count=plan.source_count,
            mapped_count=plan.mapped_count,
            excluded_count=len(plan.excluded),
            appended_count=plan.write_count,
        )
        assert_sync_write_boundary(
            [log_update],
            history_sheet=config.history_sheet,
            mapping_sheet=config.mapping_sheet,
        )
        hub_client.atomic_batch_update(
            atomic_requests_for_plan([log_update], hub_client.get_sheet_ids())
        )
        log_readback = hub_client.read_range(log_update.range_name)
        expected_log = [list(row) for row in log_update.values]
        if [pad(row, 10) for row in log_readback] != expected_log:
            raise SyncValidationError("Verified sync log readback mismatch")

        run_status = "success"
        report.update(
            status=run_status,
            readback_ok=True,
            checkpoint_advanced=True,
            finished_at_utc=utc_now(),
        )
        write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if run_status == "success" else 2
    except Exception as exc:
        report.update(
            status="error",
            error_type=type(exc).__name__,
            error_stage=stage,
            finished_at_utc=utc_now(),
        )
        report.update(sanitized_http_error(exc))
        write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
