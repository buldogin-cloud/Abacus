"""Declarative, exact-match source adapter for Hub synchronization."""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .model import ExcludedSourceRow, SourceRecord


class ConfigError(RuntimeError):
    """A production mapping is incomplete or unsafe."""


def normalize_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def normalize_key(value: Any) -> str:
    return normalize_text(value).casefold()


def pad(row: list[Any], width: int) -> list[Any]:
    return list(row) + [""] * max(0, width - len(row))


@dataclass(frozen=True)
class HubSyncConfig:
    source_sheet: str
    source_range: str
    history_sheet: str
    mapping_sheet: str
    history_first_row: int
    history_last_row: int
    minimum_source_count: int
    task_id_column: int
    status_column: int
    identity_columns: tuple[int, ...]
    anchor_rules: tuple[dict[str, Any], ...]
    expected_headers: dict[int, str]
    use_task_id_direct: bool
    task_id_to_stable_id: dict[str, str]
    identity_to_stable_id: dict[str, str]
    anchor_sha256_to_stable_id: dict[str, str]
    excluded_identities: dict[str, str]
    status_map: dict[str, str]
    priority_map: dict[str, str]
    allowed_priorities: tuple[str, ...]
    category_by_stable_id: dict[str, str]
    allowed_categories: tuple[str, ...]
    spreadsheet_timezone: str
    snapshot_rules: tuple[dict[str, Any], ...]
    mapping_columns: dict[str, int]

    @classmethod
    def load(cls, path: str | Path) -> "HubSyncConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_raw(raw)

    @classmethod
    def load_with_private_bootstrap(
        cls,
        path: str | Path,
        *,
        runtime_config: dict[str, Any],
        task_id_to_stable_id: dict[str, str],
        anchor_sha256_to_stable_id: dict[str, str],
        excluded_anchor_sha256_to_reason: dict[str, str],
    ) -> "HubSyncConfig":
        """Combine a public structural contract with private Hub-only data."""
        public = json.loads(Path(path).read_text(encoding="utf-8"))
        private_keys = {
            "task_id_to_stable_id",
            "identity_to_stable_id",
            "anchor_sha256_to_stable_id",
            "excluded_identities",
        }
        for key in private_keys:
            if public.get(key):
                raise ConfigError(f"Public sync contract must not contain {key}")
        allowed_runtime = {
            "source_sheet",
            "source_range",
            "anchor_rules",
            "expected_headers",
            "status_map",
            "priority_map",
            "allowed_priorities",
            "category_by_stable_id",
            "allowed_categories",
            "snapshot_rules",
            "mapping_columns",
            "today_sheet",
            "today_guard_range",
            "spreadsheet_timezone",
        }
        unexpected = set(runtime_config) - allowed_runtime
        if unexpected:
            raise ConfigError(
                "Private runtime config has unsupported keys: "
                + ", ".join(sorted(unexpected))
            )
        merged = {**public, **runtime_config}
        merged.update(
            task_id_to_stable_id=task_id_to_stable_id,
            identity_to_stable_id={},
            anchor_sha256_to_stable_id=anchor_sha256_to_stable_id,
            excluded_identities=excluded_anchor_sha256_to_reason,
        )
        return cls.from_raw(merged)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "HubSyncConfig":
        config = cls(
            source_sheet=raw["source_sheet"],
            source_range=raw["source_range"],
            history_sheet=raw.get("history_sheet", "90 — ІСТОРІЯ"),
            mapping_sheet=raw.get("mapping_sheet", "98 — SECRETARY SYNC"),
            history_first_row=int(raw.get("history_first_row", 6)),
            history_last_row=int(raw.get("history_last_row", 305)),
            minimum_source_count=int(raw.get("minimum_source_count", 26)),
            task_id_column=int(raw["task_id_column"]),
            status_column=int(raw["status_column"]),
            identity_columns=tuple(int(value) for value in raw["identity_columns"]),
            anchor_rules=tuple(raw.get("anchor_rules", ())),
            expected_headers={int(k): str(v) for k, v in raw["expected_headers"].items()},
            use_task_id_direct=bool(raw.get("use_task_id_direct", False)),
            task_id_to_stable_id={
                normalize_text(k): normalize_text(v)
                for k, v in raw.get("task_id_to_stable_id", {}).items()
            },
            identity_to_stable_id={
                normalize_text(k): normalize_text(v)
                for k, v in raw.get("identity_to_stable_id", {}).items()
            },
            anchor_sha256_to_stable_id={
                normalize_text(k): normalize_text(v)
                for k, v in raw.get("anchor_sha256_to_stable_id", {}).items()
            },
            excluded_identities={
                normalize_text(k): normalize_text(v)
                for k, v in raw.get("excluded_identities", {}).items()
            },
            status_map={
                normalize_key(k): normalize_text(v)
                for k, v in raw["status_map"].items()
            },
            priority_map={
                normalize_key(k): normalize_text(v)
                for k, v in raw.get("priority_map", {}).items()
            },
            allowed_priorities=tuple(
                normalize_text(value) for value in raw.get("allowed_priorities", ())
            ),
            category_by_stable_id={
                normalize_text(k): normalize_text(v)
                for k, v in raw.get("category_by_stable_id", {}).items()
            },
            allowed_categories=tuple(
                normalize_text(value) for value in raw.get("allowed_categories", ())
            ),
            spreadsheet_timezone=str(
                raw.get("spreadsheet_timezone", "Europe/Kiev")
            ),
            snapshot_rules=tuple(raw["snapshot_rules"]),
            mapping_columns={
                str(k): int(v) for k, v in raw.get("mapping_columns", {}).items()
            },
        )
        config.validate()
        return config

    def validate(self) -> None:
        if len(self.snapshot_rules) != 13:
            raise ConfigError("snapshot_rules must define Hub columns A:M exactly")
        rule_kinds = {str(rule.get("kind", "")) for rule in self.snapshot_rules}
        raw_evidence_rules = [
            rule for rule in self.snapshot_rules if rule.get("kind") == "raw_evidence"
        ]
        raw_evidence_has_status = any(
            any(
                int(field.get("column", -1)) == self.status_column
                for field in rule.get("fields", ())
            )
            for rule in raw_evidence_rules
        )
        if "source_status" not in rule_kinds and not raw_evidence_has_status:
            raise ConfigError("A:M mapping must preserve exact source status text")
        if "checked_at" in rule_kinds:
            raise ConfigError(
                "Poll checked_at must live in the private mapping/run report, not A:M"
            )
        if "material_changed_at" not in rule_kinds:
            raise ConfigError("Hub M must be an explicit material_changed_at field")
        if "priority" in rule_kinds:
            if len(set(self.allowed_priorities)) != 4:
                raise ConfigError("Priority projection requires exactly four Hub values")
            if not self.priority_map:
                raise ConfigError("Priority projection requires an exact priority_map")
            if not set(self.priority_map.values()).issubset(set(self.allowed_priorities)):
                raise ConfigError("priority_map emits a value outside allowed_priorities")
        if "stable_category" in rule_kinds:
            if not self.allowed_categories or not self.category_by_stable_id:
                raise ConfigError(
                    "Stable category projection requires a private stable-ID map"
                )
            if not set(self.category_by_stable_id.values()).issubset(
                set(self.allowed_categories)
            ):
                raise ConfigError(
                    "category_by_stable_id emits a value outside allowed_categories"
                )
        for rule in raw_evidence_rules:
            fields = rule.get("fields", ())
            if not fields:
                raise ConfigError("raw_evidence requires labeled source fields")
            if any(int(field.get("column", -1)) in {15, 16} for field in fields):
                raise ConfigError(
                    "raw_evidence must not contain poll timestamp/freshness columns"
                )
        if self.task_id_column != 14:
            raise ConfigError("Verified Secretary Task ID column must be O (zero-based 14)")
        if self.minimum_source_count != 26:
            raise ConfigError("Verified first-run source count must be 26")
        if self.history_first_row < 1 or self.history_last_row < self.history_first_row:
            raise ConfigError("Invalid history bounds")
        try:
            ZoneInfo(self.spreadsheet_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError("Unknown spreadsheet timezone") from exc
        for key, value in self.excluded_identities.items():
            if not key or not value:
                raise ConfigError("Every exclusion needs an exact identity and reason")

    def identity_for(self, row: list[Any]) -> str:
        width = max(self.identity_columns, default=0) + 1
        values = pad(row, width)
        return " ¦ ".join(normalize_text(values[index]) for index in self.identity_columns)

    def anchor_for(self, row: list[Any]) -> tuple[str, str]:
        """Build the first valid exact anchor as canonical sorted-key JSON."""
        if not self.anchor_rules:
            fallback = self.identity_for(row)
            payload = json.dumps(
                {"legacy_exact_identity": fallback},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()
        for rule in self.anchor_rules:
            condition = rule.get("when", {})
            condition_column = condition.get("column")
            if condition_column is not None:
                candidate = normalize_text(pad(row, int(condition_column) + 1)[int(condition_column)])
                if condition.get("nonempty") and not candidate:
                    continue
                excluded = {normalize_key(value) for value in condition.get("not_in", [])}
                if normalize_key(candidate) in excluded:
                    continue
            fields: dict[str, str] = {}
            valid = True
            for name, column in rule.get("fields", {}).items():
                value = normalize_text(pad(row, int(column) + 1)[int(column)])
                if not value:
                    valid = False
                    break
                fields[str(name)] = value
            if not valid or not fields:
                continue
            payload = json.dumps(
                fields,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()
        raise ConfigError("No deterministic identity anchor can be built")

    def adapt_source_rows(
        self,
        rows: list[list[Any]],
        *,
        checked_at: datetime | None = None,
        source_spreadsheet_id: str = "",
        source_sheet_id: int | None = None,
    ) -> tuple[list[SourceRecord], list[ExcludedSourceRow]]:
        if not rows:
            raise ConfigError("Secretary source range is empty")
        checked_at = checked_at or datetime.now(timezone.utc)
        checked_serial = self._datetime_serial(checked_at)
        projection_columns = [
            int(rule["column"])
            for rule in self.snapshot_rules
            if "column" in rule
        ]
        projection_columns.extend(
            int(column)
            for rule in self.snapshot_rules
            for column in rule.get("columns", ())
        )
        projection_columns.extend(
            int(field["column"])
            for rule in self.snapshot_rules
            for field in rule.get("fields", ())
        )
        max_index = max(
            [
                self.task_id_column,
                self.status_column,
                *self.identity_columns,
                *self.expected_headers.keys(),
                *self.mapping_columns.values(),
                *projection_columns,
            ]
        )
        header = pad(rows[0], max_index + 1)
        for index, expected in self.expected_headers.items():
            if normalize_text(header[index]) != normalize_text(expected):
                raise ConfigError(
                    f"Unexpected header at column {index}: expected {expected!r}"
                )

        records: list[SourceRecord] = []
        excluded: list[ExcludedSourceRow] = []
        nonempty_count = 0
        for offset, raw_row in enumerate(rows[1:], start=2):
            row = pad(raw_row, max_index + 1)
            if not any(normalize_text(value) for value in row):
                continue
            nonempty_count += 1
            identity = self.identity_for(row)
            anchor_json, anchor_sha256 = self.anchor_for(row)
            task_id = normalize_text(row[self.task_id_column])
            stable_id = ""
            resolution = ""
            if task_id:
                stable_id = self.task_id_to_stable_id.get(task_id, "")
                if not stable_id and self.use_task_id_direct:
                    stable_id = task_id
                if stable_id:
                    resolution = "task_id"
            else:
                stable_id = self.anchor_sha256_to_stable_id.get(anchor_sha256, "")
                if not stable_id:
                    stable_id = self.identity_to_stable_id.get(identity, "")
                if stable_id:
                    resolution = "exact_anchor"

            anchor_stable_id = self.anchor_sha256_to_stable_id.get(
                anchor_sha256, ""
            )
            if stable_id and anchor_stable_id and stable_id != anchor_stable_id:
                raise ConfigError(
                    f"Source row {offset} has conflicting Task-ID and anchor mappings"
                )

            suppression_reason = self.excluded_identities.get(anchor_sha256, "")
            if not suppression_reason:
                suppression_reason = self.excluded_identities.get(identity, "")
            if suppression_reason:
                metadata = self._mapping_metadata(row)
                excluded.append(
                    ExcludedSourceRow(
                        offset,
                        suppression_reason,
                        identity_anchor_json=anchor_json,
                        anchor_sha256=anchor_sha256,
                        stable_id=stable_id,
                        source_task_id=task_id,
                        source_status=str(row[self.status_column] or ""),
                        source_fingerprint=self._source_fingerprint(row),
                        **metadata,
                    )
                )
                continue

            if not stable_id:
                raise ConfigError(
                    f"Source row {offset} has no exact stable-ID mapping or exclusion"
                )

            source_status = normalize_text(row[self.status_column])
            normalized_status = self.status_map.get(normalize_key(source_status))
            if normalized_status is None:
                raise ConfigError(
                    f"Source row {offset} has an unmapped descriptive status"
                )

            snapshot: list[Any] = []
            for rule in self.snapshot_rules:
                kind = str(rule.get("kind", ""))
                if kind == "source":
                    snapshot.append(row[int(rule["column"])])
                elif kind == "source_status":
                    snapshot.append(row[self.status_column])
                elif kind == "normalized_status":
                    snapshot.append(normalized_status)
                elif kind == "raw_evidence":
                    parts: list[str] = []
                    for field in rule.get("fields", ()):
                        label = normalize_text(field.get("label", ""))
                        column = int(field["column"])
                        value = normalize_text(pad(row, column + 1)[column])
                        if value:
                            parts.append(f"{label}: {value}" if label else value)
                    snapshot.append("\n".join(parts))
                elif kind == "priority":
                    snapshot.append(
                        self._mapped_value(
                            row,
                            int(rule["column"]),
                            self.priority_map,
                            "priority",
                            offset,
                        )
                    )
                elif kind == "stable_category":
                    projected_category = self.category_by_stable_id.get(stable_id)
                    if projected_category is None:
                        raise ConfigError(
                            f"Source row {offset} has no reviewed domain category"
                        )
                    snapshot.append(projected_category)
                elif kind == "date":
                    columns = [int(value) for value in rule.get("columns", ())]
                    if not columns:
                        columns = [int(rule["column"])]
                    snapshot.append(self._project_date(row, columns, offset))
                elif kind == "material_changed_at":
                    snapshot.append(checked_serial)
                elif kind == "first_detected_at":
                    snapshot.append(checked_serial)
                elif kind == "source_row_url":
                    if not source_spreadsheet_id or source_sheet_id is None:
                        raise ConfigError(
                            "source_row_url requires spreadsheet and sheet IDs"
                        )
                    snapshot.append(
                        "https://docs.google.com/spreadsheets/d/"
                        f"{source_spreadsheet_id}/edit#gid={source_sheet_id}"
                        f"&range=A{offset}:V{offset}"
                    )
                elif kind == "literal":
                    snapshot.append(rule.get("value", ""))
                else:
                    raise ConfigError(f"Unsupported snapshot rule kind: {kind!r}")

            records.append(
                SourceRecord(
                    source_row=offset,
                    stable_id=stable_id,
                    snapshot=tuple(snapshot),
                    source_status=str(row[self.status_column] or ""),
                    source_task_id=task_id,
                    identity_anchor_json=anchor_json,
                    anchor_sha256=anchor_sha256,
                    identity_resolution=resolution,
                    source_fingerprint=self._source_fingerprint(row),
                    **self._mapping_metadata(row),
                )
            )

        if nonempty_count < self.minimum_source_count:
            raise ConfigError(
                f"Expected at least {self.minimum_source_count} non-empty source rows, got "
                f"{nonempty_count}"
            )
        anchors = [item.anchor_sha256 for item in records] + [
            item.anchor_sha256 for item in excluded
        ]
        if len(anchors) != len(set(anchors)):
            raise ConfigError("Source identity anchors are not unique")
        return records, excluded

    def _datetime_serial(self, value: datetime) -> float:
        if value.tzinfo is None:
            raise ConfigError("Timestamp projection requires a timezone-aware datetime")
        local = value.astimezone(ZoneInfo(self.spreadsheet_timezone)).replace(
            tzinfo=None
        )
        epoch = datetime(1899, 12, 30)
        return (local - epoch).total_seconds() / 86400

    @staticmethod
    def _mapped_value(
        row: list[Any],
        column: int,
        mapping: dict[str, str],
        label: str,
        source_row: int,
    ) -> str:
        raw = normalize_text(pad(row, column + 1)[column])
        projected = mapping.get(normalize_key(raw))
        if projected is None:
            raise ConfigError(
                f"Source row {source_row} has an unmapped {label} value"
            )
        return projected

    def _project_date(
        self,
        row: list[Any],
        columns: list[int],
        source_row: int,
    ) -> int | float | str:
        projected: list[int | float] = []
        for column in columns:
            raw_value = pad(row, column + 1)[column]
            text = normalize_text(raw_value)
            if not text:
                continue
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                serial = float(raw_value)
                if serial <= 0:
                    raise ConfigError(f"Source row {source_row} has invalid date serial")
                projected.append(int(serial) if serial.is_integer() else serial)
                continue
            match = re.fullmatch(r"(?:До )?(\d{2}\.\d{2}\.\d{4})", text)
            if not match:
                # Candidate columns can also contain narrative scheduling text.
                # Ignore that text and keep looking for one exact date in the
                # remaining configured columns.
                continue
            try:
                parsed = datetime.strptime(match.group(1), "%d.%m.%Y").date()
            except ValueError:
                # A value shaped like an exact date but invalid is ambiguous;
                # fail closed rather than silently using a different column.
                return ""
            projected.append((parsed - date(1899, 12, 30)).days)
        if not projected:
            return ""
        if len(set(projected)) != 1:
            return ""
        return projected[0]

    def _source_fingerprint(self, row: list[Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                [normalize_text(value) for value in row],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _mapping_metadata(self, row: list[Any]) -> dict[str, str]:
        def value(name: str) -> str:
            column = self.mapping_columns.get(name)
            if column is None:
                return ""
            return str(pad(row, column + 1)[column] or "")

        return {
            "source_updated_raw": value("source_updated_raw"),
            "source_checked_raw": value("source_checked_raw"),
            "source_when": value("source_when"),
            "source_type": value("source_type"),
        }
