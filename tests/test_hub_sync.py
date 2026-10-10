from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from modules.hub_sync.config import ConfigError, HubSyncConfig
from modules.hub_sync.mapping_store import (
    CONTROL_HEADERS,
    MAPPING_HEADERS,
    SYNC_LOG_HEADERS,
    build_mapping_update,
    build_verified_log_update,
    load_private_bootstrap,
)
from modules.hub_sync.model import CellUpdate, ExcludedSourceRow, HistoryRow, SourceRecord
from modules.hub_sync.planner import HubSyncPlanner, SyncValidationError
from modules.hub_sync.runner import (
    atomic_requests_for_plan,
    assert_sync_write_boundary,
    qualify_guard_range,
    read_history,
    sanitized_http_error,
)


def snapshot(
    label: str,
    *,
    link: str = "row-link",
    changed_at: float = 46000.5,
    first_seen: float = 45000.25,
) -> tuple[object, ...]:
    # A:K are material. L is a row link and M is material-change/import time.
    return (
        first_seen,
        "synthetic-source",
        "synthetic-domain",
        label,
        "raw evidence",
        "risk",
        "next",
        46000,
        "High",
        "Active",
        "owner",
        link,
        changed_at,
    )


def source_records(changed: dict[int, str] | None = None) -> list[SourceRecord]:
    changed = changed or {}
    return [
        SourceRecord(
            source_row=row,
            stable_id=f"task-{row}",
            snapshot=snapshot(changed.get(row, f"state-{row}")),
            source_status="synthetic",
        )
        for row in range(2, 28)
    ]


def history_from_source(records: list[SourceRecord], *, start_row: int = 6) -> list[HistoryRow]:
    planner = HubSyncPlanner()
    initial = planner.plan(records, [])
    return initial.appended_rows


class PlannerTests(unittest.TestCase):
    def test_guard_range_accepts_relative_or_matching_qualified_a1(self) -> None:
        sheet = "00 — СЬОГОДНІ"
        expected = "'00 — СЬОГОДНІ'!A1:N47"
        self.assertEqual(qualify_guard_range(sheet, "A1:N47"), expected)
        self.assertEqual(qualify_guard_range(sheet, expected), expected)

    def test_guard_range_rejects_different_sheet(self) -> None:
        with self.assertRaises(SyncValidationError):
            qualify_guard_range("00 — СЬОГОДНІ", "'90 — ІСТОРІЯ'!A1:N47")

    def test_http_diagnostics_are_status_only(self) -> None:
        class Response:
            status = 403

        class PrivateHttpError(Exception):
            resp = Response()

            def __str__(self) -> str:
                return "private spreadsheet id and response body"

        self.assertEqual(
            sanitized_http_error(PrivateHttpError()),
            {"http_status": 403, "http_category": "permission_or_scope"},
        )

    def test_second_identical_sync_is_noop(self) -> None:
        records = source_records()
        history = history_from_source(records)
        plan = HubSyncPlanner().plan(records, history)
        self.assertEqual(plan.write_count, 0)
        self.assertEqual(plan.unchanged_count, 26)

    def test_sorted_source_is_noop(self) -> None:
        records = source_records()
        history = history_from_source(records)
        plan = HubSyncPlanner().plan(list(reversed(records)), history)
        self.assertEqual(plan.write_count, 0)

    def test_row_link_repair_does_not_create_state(self) -> None:
        records = source_records()
        history = history_from_source(records)
        reordered = list(records)
        first = reordered[0]
        reordered[0] = SourceRecord(
            source_row=first.source_row,
            stable_id=first.stable_id,
            snapshot=snapshot("state-2", link="different-row-link", changed_at=46001.5),
            source_status=first.source_status,
        )
        plan = HubSyncPlanner().plan(reordered, history)
        self.assertEqual(plan.write_count, 0)
        self.assertEqual(len(plan.updates), 1)
        self.assertEqual(plan.updates[0].range_name, "'90 — ІСТОРІЯ'!L6")

    def test_changed_state_appends_and_flips_only_prior_current(self) -> None:
        before = source_records()
        history = history_from_source(before)
        after = source_records({5: "changed"})
        plan = HubSyncPlanner().plan(after, history)
        self.assertEqual(plan.write_count, 1)
        self.assertEqual(plan.changed_stable_ids, ["task-5"])
        ranges = [update.range_name for update in plan.updates]
        self.assertIn("'90 — ІСТОРІЯ'!O9", ranges)
        self.assertIn("'90 — ІСТОРІЯ'!A32:P32", ranges)

    def test_a_to_b_to_a_creates_third_history_state(self) -> None:
        planner = HubSyncPlanner(minimum_source_count=1)
        state_a = [SourceRecord(2, "task-x", snapshot("A"), "raw")]
        first = planner.plan(state_a, [])
        history = list(first.appended_rows)

        state_b = [SourceRecord(2, "task-x", snapshot("B"), "raw")]
        second = planner.plan(state_b, history)
        history[0] = HistoryRow(
            history[0].sheet_row,
            history[0].snapshot,
            history[0].stable_id,
            False,
            history[0].state_id,
        )
        history.extend(second.appended_rows)

        third = planner.plan(state_a, history)
        self.assertEqual(third.write_count, 1)
        self.assertNotEqual(third.appended_rows[0].state_id, first.appended_rows[0].state_id)

    def test_one_explicit_exclusion_keeps_26_of_26_accounted(self) -> None:
        records = source_records()[:-1]
        plan = HubSyncPlanner().plan(
            records,
            [],
            excluded=[ExcludedSourceRow(27, "source policy: hidden routine")],
        )
        self.assertEqual(plan.source_count, 26)
        self.assertEqual(plan.accounted_count, 26)
        self.assertEqual(plan.mapped_count, 25)

    def test_additional_mapped_source_row_is_not_silently_frozen_out(self) -> None:
        records = source_records()
        records.append(SourceRecord(28, "task-28", snapshot("state-28"), "synthetic"))
        plan = HubSyncPlanner().plan(records, [])
        self.assertEqual(plan.accounted_count, 27)
        self.assertEqual(plan.write_count, 27)

    def test_duplicate_source_stable_id_fails_closed(self) -> None:
        records = source_records()
        records[-1] = SourceRecord(27, records[0].stable_id, snapshot("x"), "raw")
        with self.assertRaises(SyncValidationError):
            HubSyncPlanner().plan(records, [])

    def test_history_overflow_fails_before_writes(self) -> None:
        records = source_records()
        planner = HubSyncPlanner(first_history_row=6, last_history_row=30)
        with self.assertRaises(SyncValidationError):
            planner.plan(records, [])

    def test_multiple_current_states_fail_closed(self) -> None:
        records = source_records()
        history = history_from_source(records)
        history.append(
            HistoryRow(40, snapshot("old"), "task-2", True, "different-state")
        )
        with self.assertRaises(SyncValidationError):
            HubSyncPlanner().plan(records, history)

    def test_existing_first_seen_is_preserved_with_numeric_new_material_time(self) -> None:
        planner = HubSyncPlanner(minimum_source_count=1)
        prior = HistoryRow(
            6,
            snapshot("old", first_seen=44000.25, changed_at=44500.5),
            "task-x",
            True,
            "state-old",
        )
        incoming = SourceRecord(
            2,
            "task-x",
            snapshot("changed", first_seen=47000.25, changed_at=47000.5),
            "raw",
        )
        plan = planner.plan([incoming], [prior])
        self.assertEqual(plan.write_count, 1)
        self.assertEqual(plan.appended_rows[0].snapshot[0], 44000.25)
        self.assertEqual(plan.appended_rows[0].snapshot[12], 47000.5)
        append_update = next(
            update for update in plan.updates if update.range_name.endswith("A7:P7")
        )
        self.assertEqual(append_update.values[0][0], 44000.25)

    def test_history_timestamp_and_deadline_types_fail_closed(self) -> None:
        planner = HubSyncPlanner(minimum_source_count=1)
        bad_first = list(snapshot("x"))
        bad_first[0] = "2026-10-10T10:00:00Z"
        with self.assertRaisesRegex(SyncValidationError, "nonnumeric first detected"):
            planner.plan([SourceRecord(2, "task-x", tuple(bad_first), "raw")], [])
        bad_deadline = list(snapshot("x"))
        bad_deadline[7] = "10.10.2026"
        with self.assertRaisesRegex(SyncValidationError, "nonnumeric Hub deadline"):
            planner.plan([SourceRecord(2, "task-x", tuple(bad_deadline), "raw")], [])


class ConfigTests(unittest.TestCase):
    def make_config(self) -> dict:
        rules = [{"kind": "literal", "value": f"x{i}"} for i in range(13)]
        rules[4] = {"kind": "source_status"}
        rules[9] = {"kind": "normalized_status"}
        rules[12] = {"kind": "material_changed_at"}
        return {
            "source_sheet": "Synthetic",
            "source_range": "Synthetic!A1:V",
            "task_id_column": 14,
            "status_column": 8,
            "identity_columns": [1, 2],
            "expected_headers": {"8": "Status", "14": "Task ID"},
            "use_task_id_direct": True,
            "identity_to_stable_id": {},
            "excluded_identities": {},
            "status_map": {"raw open": "Active"},
            "snapshot_rules": rules,
        }

    def load(self, raw: dict) -> HubSyncConfig:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return HubSyncConfig.load(path)

    def test_requires_verified_task_id_column_o(self) -> None:
        raw = self.make_config()
        raw["task_id_column"] = 13
        with self.assertRaises(ConfigError):
            self.load(raw)

    def test_poll_checked_at_is_forbidden_in_history_snapshot(self) -> None:
        raw = self.make_config()
        raw["snapshot_rules"][12] = {"kind": "checked_at"}
        with self.assertRaises(ConfigError):
            self.load(raw)

    def test_exact_status_and_material_time_are_separate(self) -> None:
        raw = self.make_config()
        config = self.load(raw)
        header = [""] * 22
        header[8] = "Status"
        header[14] = "Task ID"
        rows = [header]
        for index in range(26):
            row = [""] * 22
            row[1] = f"doc-{index}"
            row[2] = "2026-10-10"
            row[8] = "raw open"
            row[14] = f"task-{index}"
            rows.append(row)
        checked = datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)
        records, excluded = config.adapt_source_rows(rows, checked_at=checked)
        self.assertFalse(excluded)
        self.assertEqual(records[0].snapshot[4], "raw open")
        self.assertEqual(records[0].snapshot[9], "Active")
        expected = (
            datetime(2026, 10, 10, 22, 0) - datetime(1899, 12, 30)
        ).total_seconds() / 86400
        self.assertEqual(records[0].snapshot[12], expected)

    def test_ambiguous_missing_task_id_is_quarantined(self) -> None:
        raw = self.make_config()
        raw["use_task_id_direct"] = False
        raw["task_id_to_stable_id"] = {
            f"task-{index}": f"task-{index}" for index in range(1, 26)
        }
        config = self.load(raw)
        header = [""] * 22
        header[8] = "Status"
        header[14] = "Task ID"
        rows = [header]
        for index in range(26):
            row = [""] * 22
            row[1] = f"document-{index}"
            row[2] = "2026-10-10"
            row[8] = "raw open"
            row[14] = "" if index == 0 else f"task-{index}"
            rows.append(row)
        with self.assertRaisesRegex(ConfigError, "no exact stable-ID mapping"):
            config.adapt_source_rows(rows)

    def projection_config(self) -> dict:
        raw = self.make_config()
        raw["snapshot_rules"][0] = {"kind": "first_detected_at"}
        raw["snapshot_rules"][2] = {"kind": "stable_category"}
        raw["snapshot_rules"][4] = {
            "kind": "raw_evidence",
            "fields": [
                {"label": "Task", "column": 4},
                {"label": "Status", "column": 8},
                {"label": "Deadline", "column": 7},
                {"label": "When", "column": 19},
                {"label": "Evidence", "column": 9}
            ]
        }
        raw["snapshot_rules"][7] = {"kind": "date", "columns": [7, 19]}
        raw["snapshot_rules"][8] = {"kind": "priority", "column": 0}
        raw["priority_map"] = {
            "P1": "Critical",
            "P2": "High",
            "P3": "Medium",
            "P4": "Low",
        }
        raw["allowed_priorities"] = ["Critical", "High", "Medium", "Low"]
        raw["allowed_categories"] = ["General", "ICU", "Equipment"]
        raw["category_by_stable_id"] = {
            f"task-{index}": "ICU" if index == 0 else "Equipment"
            for index in range(26)
        }
        return raw

    @staticmethod
    def projection_rows(*, priority: str = "P1", deadline: str = "10.10.2026") -> list[list[str]]:
        header = [""] * 22
        header[8] = "Status"
        header[14] = "Task ID"
        rows = [header]
        for index in range(26):
            row = [""] * 22
            row[0] = priority
            row[1] = f"doc-{index}"
            row[2] = "10.10.2026"
            row[4] = "expected source result"
            row[7] = deadline
            row[8] = "raw open"
            row[9] = "verified evidence"
            row[14] = f"task-{index}"
            rows.append(row)
        return rows

    def test_date_priority_and_stable_domain_are_projected_fail_closed(self) -> None:
        config = self.load(self.projection_config())
        records, _ = config.adapt_source_rows(self.projection_rows())
        expected_serial = (date(2026, 10, 10) - date(1899, 12, 30)).days
        self.assertEqual(records[0].snapshot[2], "ICU")
        self.assertEqual(records[1].snapshot[2], "Equipment")
        self.assertEqual(records[0].snapshot[7], expected_serial)
        self.assertEqual(records[0].snapshot[8], "Critical")
        self.assertEqual(
            records[0].snapshot[4],
            "Task: expected source result\nStatus: raw open\nDeadline: 10.10.2026"
            "\nEvidence: verified evidence",
        )

    def test_unknown_priority_fails_but_narrative_date_is_blank(self) -> None:
        config = self.load(self.projection_config())
        with self.assertRaisesRegex(ConfigError, "unmapped priority"):
            config.adapt_source_rows(self.projection_rows(priority="urgent-ish"))
        records, _ = config.adapt_source_rows(
            self.projection_rows(deadline="before review")
        )
        self.assertEqual(records[0].snapshot[7], "")
        self.assertIn("Deadline: before review", records[0].snapshot[4])

    def test_exact_do_date_parses_but_multiple_date_text_is_blank(self) -> None:
        config = self.load(self.projection_config())
        records, _ = config.adapt_source_rows(self.projection_rows(deadline="До 10.10.2026"))
        expected_serial = (date(2026, 10, 10) - date(1899, 12, 30)).days
        self.assertEqual(records[0].snapshot[7], expected_serial)
        records, _ = config.adapt_source_rows(
            self.projection_rows(deadline="10.10.2026 / 11.10.2026")
        )
        self.assertEqual(records[0].snapshot[7], "")

    def test_exact_deadline_survives_narrative_alternate_date_column(self) -> None:
        config = self.load(self.projection_config())
        rows = self.projection_rows(deadline="До 10.10.2026")
        for row in rows[1:]:
            row[19] = "після узгодження"

        records, _ = config.adapt_source_rows(rows)

        expected_serial = (date(2026, 10, 10) - date(1899, 12, 30)).days
        self.assertEqual(records[0].snapshot[7], expected_serial)

    def test_invalid_exact_date_beats_valid_alternate_fail_closed(self) -> None:
        config = self.load(self.projection_config())
        rows = self.projection_rows(deadline="31.02.2026")
        for row in rows[1:]:
            row[19] = "10.10.2026"

        records, _ = config.adapt_source_rows(rows)

        self.assertEqual(records[0].snapshot[7], "")

    def test_duplicate_canonical_anchors_are_rejected(self) -> None:
        raw = self.make_config()
        raw["anchor_rules"] = [
            {"fields": {"document_reference": 1, "document_date": 2}}
        ]
        config = self.load(raw)
        rows = self.projection_rows()
        rows[2][1] = rows[1][1]
        with self.assertRaisesRegex(ConfigError, "anchors are not unique"):
            config.adapt_source_rows(rows)

    def test_additional_nonempty_source_row_is_reconciled(self) -> None:
        config = self.load(self.make_config())
        header = [""] * 22
        header[8] = "Status"
        header[14] = "Task ID"
        rows = [header]
        for index in range(27):
            row = [""] * 22
            row[1] = f"doc-{index}"
            row[2] = "2026-10-10"
            row[8] = "raw open"
            row[14] = f"task-{index}"
            rows.append(row)
        records, excluded = config.adapt_source_rows(rows)
        self.assertEqual(len(records), 27)
        self.assertFalse(excluded)


class RunnerAndMappingTests(unittest.TestCase):
    def test_history_is_read_as_unformatted_values(self) -> None:
        client = Mock()
        client.read_range.return_value = [[46000.5, *([""] * 15)]]

        rows = read_history(client, "'Synthetic History'!A6:P30", 6)

        client.read_range.assert_called_once_with(
            "'Synthetic History'!A6:P30",
            value_render_option="UNFORMATTED_VALUE",
        )
        self.assertEqual(rows[0].snapshot[0], 46000.5)

    def test_mismatch_diagnostics_never_expose_ids_or_values(self) -> None:
        records = source_records()
        history = history_from_source(records)
        changed = list(history[0].snapshot)
        changed[3] = "different private value"
        history[0] = HistoryRow(
            history[0].sheet_row,
            tuple(changed),
            history[0].stable_id,
            True,
            history[0].state_id,
        )

        summary = HubSyncPlanner().diagnostic_summary(
            records, history
        )

        self.assertEqual(summary["material_mismatch_columns"], {"D": 1})
        self.assertEqual(summary["material_mismatch_types"], {"D:str->str": 1})
        serialized = json.dumps(summary)
        self.assertNotIn(records[0].stable_id, serialized)
        self.assertNotIn("different private value", serialized)

    def test_production_contract_and_workflow_contain_no_private_mapping(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (root / "modules/hub_sync/production_contract.json").read_text()
        )
        forbidden = {
            "task_id_to_stable_id",
            "identity_to_stable_id",
            "anchor_sha256_to_stable_id",
            "excluded_identities",
        }
        self.assertTrue(forbidden.isdisjoint(contract))
        workflow = (root / ".github/workflows/hub_sync.yml").read_text()
        self.assertNotIn("HUB_SYNC_CONFIG_JSON", workflow)

    def test_sync_write_boundary_rejects_today_and_other_frontends(self) -> None:
        with self.assertRaisesRegex(SyncValidationError, "write boundary"):
            assert_sync_write_boundary(
                [CellUpdate("'00 — TODAY'!A1:H30", (("x",),))],
                history_sheet="90 — ІСТОРІЯ",
                mapping_sheet="98 — SECRETARY SYNC",
            )

    def test_sync_write_boundary_allows_only_history_and_private_map(self) -> None:
        assert_sync_write_boundary(
            [
                CellUpdate("'90 — ІСТОРІЯ'!O6", ((False,),)),
                CellUpdate("'90 — ІСТОРІЯ'!A7:P7", (tuple(range(16)),)),
                CellUpdate("'98 — SECRETARY SYNC'!A1:L27", (tuple(range(12)),)),
                CellUpdate("'98 — SECRETARY SYNC'!Q2:Z2", (tuple(range(10)),)),
            ],
            history_sheet="90 — ІСТОРІЯ",
            mapping_sheet="98 — SECRETARY SYNC",
        )

    def test_atomic_request_preserves_exact_history_bounds(self) -> None:
        update = CellUpdate(
            range_name="'Synthetic History'!A6:P6",
            values=(tuple(["value"] * 13 + ["task-1", True, "state-1"]),),
        )
        requests = atomic_requests_for_plan(
            [update], {"Synthetic History": 123}
        )
        grid_range = requests[0]["updateCells"]["range"]
        self.assertEqual(
            grid_range,
            {
                "sheetId": 123,
                "startRowIndex": 5,
                "endRowIndex": 6,
                "startColumnIndex": 0,
                "endColumnIndex": 16,
            },
        )

    def test_private_mapping_uses_readback_stable_scalar_types(self) -> None:
        records = source_records()[:-1]
        history = HubSyncPlanner(minimum_source_count=25).plan(records, []).appended_rows
        exclusion = ExcludedSourceRow(
            27,
            "synthetic source policy",
            identity_anchor_json='{"synthetic":"excluded"}',
            anchor_sha256="anchor-excluded",
        )
        update = build_mapping_update(
            mapping_sheet="Synthetic Private Mapping",
            records=records,
            exclusions=[exclusion],
            history_after=history,
            seen_at=datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(update.range_name, "'Synthetic Private Mapping'!A1:L27")
        self.assertEqual(update.values[1][0], "1")
        self.assertEqual(update.values[1][4], "include")
        self.assertEqual(update.values[1][8], "6")
        self.assertEqual(update.values[1][10], "2")
        self.assertEqual(update.values[-1][4], "exclude")
        flattened = json.dumps(update.values)
        self.assertNotIn("synthetic-source-id", flattened)
        self.assertNotIn("{\"synthetic\":\"excluded\"}", flattened)

    def test_private_bootstrap_separates_identity_from_runtime_contract(self) -> None:
        runtime = {
            "source_sheet": "Synthetic",
            "source_range": "'Synthetic'!A1:V",
        }
        header = [*MAPPING_HEADERS, "", *CONTROL_HEADERS]
        included = [
            "1", "anchor-a", "source-a", "hub-a", "include", "",
            "", "", "", "", "", "task_id", "", "runtime_config_json",
            json.dumps(runtime),
        ]
        excluded = [
            "1", "anchor-b", "", "", "exclude", "source_policy", "",
            "", "", "", "", "source_policy",
        ]
        bootstrap = load_private_bootstrap([header, included, excluded])
        self.assertEqual(bootstrap.task_id_to_stable_id, {"source-a": "hub-a"})
        self.assertEqual(
            bootstrap.anchor_sha256_to_stable_id, {"anchor-a": "hub-a"}
        )
        self.assertEqual(
            bootstrap.excluded_anchor_sha256_to_reason,
            {"anchor-b": "source_policy"},
        )
        self.assertEqual(bootstrap.runtime_config, runtime)

    def test_verified_log_is_hashes_counts_and_status_only(self) -> None:
        update = build_verified_log_update(
            mapping_sheet="Synthetic Private Mapping",
            existing_log=[list(SYNC_LOG_HEADERS)],
            verified_at_utc="2026-10-10T22:00:00Z",
            source_snapshot_sha256="a" * 64,
            history_snapshot_sha256="b" * 64,
            mapping_snapshot_sha256="c" * 64,
            source_count=26,
            mapped_count=25,
            excluded_count=1,
            appended_count=25,
        )
        self.assertEqual(update.range_name, "'Synthetic Private Mapping'!Q2:Z2")
        self.assertEqual(update.values[0][8:], ("0", "verified"))
        self.assertEqual(len(update.values[0]), 10)

    def test_verified_log_never_overwrites_after_a_gap(self) -> None:
        prior = [list(SYNC_LOG_HEADERS), ["old"], [], ["newer"]]
        update = build_verified_log_update(
            mapping_sheet="Synthetic Private Mapping",
            existing_log=prior,
            verified_at_utc="2026-10-10T22:01:00Z",
            source_snapshot_sha256="a" * 64,
            history_snapshot_sha256="b" * 64,
            mapping_snapshot_sha256="c" * 64,
            source_count=26,
            mapped_count=25,
            excluded_count=1,
            appended_count=0,
        )
        self.assertEqual(update.range_name, "'Synthetic Private Mapping'!Q5:Z5")


if __name__ == "__main__":
    unittest.main()
