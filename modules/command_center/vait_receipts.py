"""Local VAIT artifact binding. Never manufactures producer evidence or stages.

Only trusted producers may supply these artifacts. Hashes detect changes, not
source authenticity, clinical validity or actual external delivery.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from modules.command_center.report_gate import ReportValidationError, timestamp, validate_report

UNITS = {"vait_1", "vait_2"}


def _path(root: Path, value) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ReportValidationError("invalid_artifact_path")
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ReportValidationError("artifact_outside_manifest_directory")
    if not path.is_file():
        raise ReportValidationError("artifact_missing")
    return path


def _window(window, now):
    start, end = map(timestamp, window)
    if start >= end:
        raise ReportValidationError("invalid_window")
    if now.tzinfo is None:
        raise ReportValidationError("timezone_required")
    if not 0 <= (now - end).total_seconds() <= 24 * 3600:
        raise ReportValidationError("stale_or_future_window")


def verify_manifest(manifest, root: Path, *, now=None):
    """Verify existing receipts. Supports legacy unpinned and v2 pinned manifests."""
    root = Path(root).resolve()
    now = now or datetime.now(timezone.utc)
    if not isinstance(manifest, dict) or manifest.get("schema_version", 1) not in (1, 2):
        raise ReportValidationError("invalid_manifest_schema")
    units = manifest.get("units")
    if (not isinstance(units, list) or len(units) != 2
            or any(not isinstance(u, dict) for u in units)
            or {u.get("task_id") for u in units} != UNITS):
        raise ReportValidationError("both_units_required")
    window = (manifest.get("window_start"), manifest.get("window_end"))
    _window(window, now)
    paths = []
    for unit in units:
        receipt_path = _path(root, unit.get("receipt_path"))
        body_path = _path(root, unit.get("body_path"))
        paths.extend((receipt_path, body_path))
        receipt_bytes, body = receipt_path.read_bytes(), body_path.read_bytes()
        if manifest.get("schema_version", 1) == 2:
            for field, data in (("receipt_sha256", receipt_bytes), ("body_sha256", body)):
                if unit.get(field) != hashlib.sha256(data).hexdigest():
                    raise ReportValidationError("artifact_pin_mismatch")
        receipt = json.loads(receipt_bytes)
        run = unit.get("run_id")
        if not isinstance(run, str):
            raise ReportValidationError("invalid_run_id")
        validate_report(receipt, body, expected_task=unit["task_id"], expected_run=run,
                        expected_window=window, now=now)
    if len(set(paths)) != 4:
        raise ReportValidationError("artifact_path_reused")
    return {"status": "receipt_verified", "units_verified": 2}


def assemble_manifest(root: Path, *, window_start, window_end, units, now=None):
    """Bind caller-selected identities to existing artifacts; no discovery or inference."""
    root = Path(root).resolve()
    now = now or datetime.now(timezone.utc)
    # Validate a legacy-shape candidate first, so errors never produce a manifest.
    candidate = {"window_start": window_start, "window_end": window_end, "units": units}
    verify_manifest(candidate, root, now=now)
    bound = []
    for unit in units:
        entry = {key: unit[key] for key in ("task_id", "run_id", "receipt_path", "body_path")}
        for key, field in (("receipt_path", "receipt_sha256"), ("body_path", "body_sha256")):
            entry[field] = hashlib.sha256(_path(root, entry[key]).read_bytes()).hexdigest()
        bound.append(entry)
    manifest = {"schema_version": 2, "window_start": window_start,
                "window_end": window_end, "units": sorted(bound, key=lambda u: u["task_id"])}
    # Recheck hashes AND validity on the final snapshot before returning it.
    verify_manifest(manifest, root, now=now)
    return manifest


def save_manifest(path: Path, manifest, *, now=None):
    """Create exclusively, or accept an identical verified manifest on retry.

Inputs must already exist below the output's parent. Never overwrite artifacts.
No network, credential access or source/report mutation is performed.
"""
    path = Path(path)
    if path.is_symlink():
        raise ReportValidationError("manifest_target_symlink")
    root = path.parent.resolve()
    target = root / path.name
    verify_manifest(manifest, root, now=now)
    source_paths = {_path(root, u[k]) for u in manifest["units"] for k in ("receipt_path", "body_path")}
    if target in source_paths:
        raise ReportValidationError("manifest_overwrites_artifact")
    encoded = json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    try:
        # Exclusive create prevents replacing an existing manifest or artifact.
        with target.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    except FileExistsError:
        if target.is_symlink() or target.read_text(encoding="utf-8") != encoded:
            raise ReportValidationError("manifest_conflict") from None
    return target
