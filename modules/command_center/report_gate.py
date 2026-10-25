"""Fail-closed receipt checks before claiming a completed report.

No network calls or clinical interpretation. A valid receipt proves only the
specified mechanical checks, not completeness, truth or medical correctness.
Trusted producers must bind receipts to observed source and read-back evidence.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone


class ReportValidationError(ValueError):
    """Contains a stable code only, never report content."""


def timestamp(value):
    if not isinstance(value, str):
        raise ReportValidationError("invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReportValidationError("invalid_timestamp") from None
    if result.tzinfo is None:
        raise ReportValidationError("timezone_required")
    return result.astimezone(timezone.utc)


def validate_report(receipt, body: bytes, *, expected_task: str,
                    expected_run: str, expected_window: tuple[str, str],
                    require_delivery: bool = True, now: datetime | None = None):
    """Validate against caller's expected identity/window, not filename alone."""
    if not isinstance(receipt, dict):
        raise ReportValidationError("invalid_receipt")
    if receipt.get("schema_version") != 1:
        raise ReportValidationError("unsupported_schema")
    if receipt.get("task_id") != expected_task:
        raise ReportValidationError("task_mismatch")
    if receipt.get("run_id") != expected_run or not expected_run.startswith(expected_task + "__"):
        raise ReportValidationError("run_mismatch")
    start, end = map(timestamp, expected_window)
    boundary = now or datetime.now(timezone.utc)
    if boundary.tzinfo is None:
        raise ReportValidationError("timezone_required")
    if end > boundary:
        raise ReportValidationError("future_window")
    if start >= end:
        raise ReportValidationError("invalid_window")
    captured = timestamp(receipt.get("captured_at"))
    if captured > boundary:
        raise ReportValidationError("future_capture")
    window = receipt.get("window") or {}
    if timestamp(window.get("start")) != start or timestamp(window.get("end")) != end:
        raise ReportValidationError("window_mismatch")
    if not isinstance(body, bytes) or not body.strip():
        raise ReportValidationError("empty_body")
    digest = hashlib.sha256(body).hexdigest()
    if receipt.get("sha256") != digest:
        raise ReportValidationError("content_hash_mismatch")
    if receipt.get("readback_sha256") != digest:
        raise ReportValidationError("readback_hash_mismatch")
    if not isinstance(receipt.get("stages"), dict):
        raise ReportValidationError("invalid_stages")
    for stage in ("source_check", "analysis", "storage"):
        if receipt.get("stages", {}).get(stage) != "success":
            raise ReportValidationError(stage + "_incomplete")
    if receipt.get("stages", {}).get("registry") not in ("success", "not_required"):
        raise ReportValidationError("registry_incomplete")
    if require_delivery and receipt.get("stages", {}).get("delivery") != "success":
        raise ReportValidationError("delivery_unverified")
    sources = receipt.get("source_evidence")
    if not isinstance(sources, list) or not sources:
        raise ReportValidationError("source_evidence_missing")
    for source in sources:
        if not isinstance(source, dict) or not source.get("source_ref") or source.get("verified") is not True:
            raise ReportValidationError("source_evidence_unverified")
        checked = timestamp(source.get("checked_at"))
        if checked < start or checked > timestamp(receipt.get("captured_at")):
            raise ReportValidationError("source_check_time_invalid")
    reviewed = receipt.get("content_review", {})
    if reviewed.get("task_id") != expected_task or reviewed.get("sha256") != digest or reviewed.get("status") != "verified":
        raise ReportValidationError("topic_review_unverified")
    captured = timestamp(receipt.get("captured_at"))
    if captured < end:
        raise ReportValidationError("capture_before_window_end")
    return {"status": "receipt_verified", "task_id": expected_task,
            "run_id": expected_run, "sha256": digest}
