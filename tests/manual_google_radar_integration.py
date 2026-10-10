"""Manual, fail-closed Google integration test for PR #1.

This harness is intentionally excluded from unittest discovery and must be run
explicitly.  It uses only an already-issued ``GOOGLE_TOKEN`` or ``token.json``;
it never starts an OAuth flow, imports Gmail/Telegram code, or touches any
canonical Command Center identifier.

The HTTP/source outcomes are simulated.  Google Sheets and Drive persistence
calls are real when ``--execute`` passes preflight.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.daily_briefing import radar

# The repository's runtime target is Python 3.11, while this Mac's system
# Python is 3.9 and cannot evaluate one unrelated ``Credentials | None``
# annotation in integrations.google_auth.  The integration harness injects its
# own guarded Drive adapter, so avoid importing that unrelated client here.
drive_module = types.ModuleType("integrations.drive_client")
drive_module.DriveClient = object
sys.modules.setdefault("integrations.drive_client", drive_module)
from modules.daily_briefing.checkpoints_writer import CheckpointsWriter
from modules.daily_briefing.collector import CollectedData, SourceResult
from modules.daily_briefing.canonical import (
    AUTOMATION_LOG_FILE_ID,
    CHECKPOINTS_FILE_ID,
    HANDOFF_RUNS_FILE_ID,
    RADAR_SPREADSHEET_ID,
    TASKS_REGISTRY_SHEET_ID,
)


APPROVED_COMMIT = "08df0ba05cb61c657b6b457f72a84564e38773fd"
EXPECTED_REF = "refs/heads/test/radar-google-integration"
APPROVED_FILES = (
    "modules/daily_briefing/briefing.py",
    "modules/daily_briefing/checkpoints_writer.py",
    "modules/daily_briefing/radar.py",
    "tests/test_checkpoint_persistence.py",
    "tests/test_radar_guards.py",
)
TEST_TAB = "Radar integration test"
HEADERS = [
    "ID документа",
    "Вид",
    "Орган",
    "Дата документа",
    "Номер",
    "Назва",
    "Офіційне посилання",
    "Статус / редакція",
    "Джерело статусу",
    "Що змінилось",
]
DENIED_IDS = {
    RADAR_SPREADSHEET_ID,
    TASKS_REGISTRY_SHEET_ID,
    CHECKPOINTS_FILE_ID,
    AUTOMATION_LOG_FILE_ID,
    HANDOFF_RUNS_FILE_ID,
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def verify_approved_tree() -> dict[str, str]:
    """Require the five worktree blobs to equal the approved PR head."""
    _git("cat-file", "-e", f"{APPROVED_COMMIT}^{{commit}}")
    hashes: dict[str, str] = {}
    for name in APPROVED_FILES:
        local_hash = _git("hash-object", name)
        approved_hash = _git("rev-parse", f"{APPROVED_COMMIT}:{name}")
        if local_hash != approved_hash:
            raise RuntimeError(f"approved-tree mismatch: {name}")
        hashes[name] = local_hash
    return hashes


def verify_execution_context() -> str:
    """Permit external writes only in the one intended manual Actions run."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("EXECUTION_CONTEXT_BLOCKED")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("NON_MANUAL_EVENT_BLOCKED")
    if os.environ.get("GITHUB_REF") != EXPECTED_REF:
        raise RuntimeError("UNEXPECTED_GIT_REF_BLOCKED")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id.isdigit():
        raise RuntimeError("INVALID_RUN_ID_BLOCKED")
    return run_id


def _mask(value: str | None) -> None:
    """Mask new private identifiers before any later Actions log output."""
    if value and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}", flush=True)


def existing_credentials() -> Credentials:
    """Load existing authorization only; never start or persist OAuth."""
    scopes = [
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    raw = os.environ.get("GOOGLE_TOKEN")
    token_path = ROOT / "token.json"
    if raw:
        creds = Credentials.from_authorized_user_info(json.loads(raw), scopes)
        source = "GOOGLE_TOKEN"
    elif token_path.is_file():
        creds = Credentials.from_authorized_user_file(token_path, scopes)
        source = "token.json"
    else:
        raise RuntimeError(
            "NO_EXISTING_REPO_GOOGLE_AUTH: neither GOOGLE_TOKEN nor token.json "
            "is available; connector/browser sessions cannot be consumed by this script"
        )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError(f"existing Google authorization is invalid ({source})")
    return creds


def _assert_private(drive_service: Any, file_id: str) -> None:
    permissions = (
        drive_service.permissions()
        .list(fileId=file_id, fields="permissions(type,role)")
        .execute()
        .get("permissions", [])
    )
    unsafe = [p for p in permissions if p.get("type") in {"anyone", "domain"}]
    if unsafe:
        raise RuntimeError("PRIVATE_PERMISSION_CHECK_FAILED")


class _GuardedValues:
    def __init__(self, values_api: Any, target_id: str, tab: str):
        self._api = values_api
        self._target_id = target_id
        self._tab = tab

    def _check(self, spreadsheet_id: str, range_name: str) -> None:
        if spreadsheet_id != self._target_id or spreadsheet_id in DENIED_IDS:
            raise RuntimeError("NON_TEST_SPREADSHEET_BLOCKED")
        normalized = range_name.lstrip("'")
        if not normalized.startswith(self._tab):
            raise RuntimeError("NON_TEST_TAB_BLOCKED")

    def get(self, *, spreadsheetId: str, range: str, **kwargs: Any) -> Any:
        self._check(spreadsheetId, range)
        return self._api.get(spreadsheetId=spreadsheetId, range=range, **kwargs)

    def append(self, *, spreadsheetId: str, range: str, **kwargs: Any) -> Any:
        self._check(spreadsheetId, range)
        return self._api.append(spreadsheetId=spreadsheetId, range=range, **kwargs)


class _GuardedSheets:
    def __init__(self, service: Any, target_id: str, tab: str):
        self._values = _GuardedValues(service.spreadsheets().values(), target_id, tab)

    def spreadsheets(self) -> "_GuardedSheets":
        return self

    def values(self) -> _GuardedValues:
        return self._values


class _GuardedDrive:
    """Minimal DriveClient-compatible adapter limited to one new file ID."""

    def __init__(self, service: Any, file_id: str):
        if file_id in DENIED_IDS:
            raise RuntimeError("blocked canonical Drive target")
        self.service = service
        self.file_id = file_id

    def _check(self, file_id: str) -> None:
        if file_id != self.file_id or file_id in DENIED_IDS:
            raise RuntimeError("NON_TEST_DRIVE_TARGET_BLOCKED")

    def read_file(self, file_id: str) -> str | None:
        self._check(file_id)
        request = self.service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return fh.getvalue().decode("utf-8")

    def update_file_by_id(self, *, file_id: str, content: str, mime_type: str) -> str:
        self._check(file_id)
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False
        )
        result = self.service.files().update(
            fileId=file_id, media_body=media, fields="id"
        ).execute()
        return result["id"]


def _record(*, number: str = "INT-001", status: str = "ACTIVE") -> dict[str, str]:
    return {
        "source": "moz_ukraine",
        "order_no": number,
        "doc_date": "2099-01-01",
        "url": f"https://moz.gov.ua/integration-fixture/{number}?utm_source=test",
        "title": f"Synthetic integration fixture {number}",
        "document_type": "наказ",
        "change_status": status,
    }


def _collected(record: dict[str, str]) -> CollectedData:
    source = SourceResult(
        source="researcher",
        status="success",
        checked_at="2099-01-01T00:00:00Z",
        window_start="2098-12-31T00:00:00Z",
        window_end="2099-01-01T00:00:00Z",
        records=[record],
        connection_verified=True,
    )
    return CollectedData("integration_test", "2099-01-01T00:00:00Z", {"researcher": source})


def _row_count(service: Any, spreadsheet_id: str) -> int:
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{TEST_TAB}'!A2:A1000"
    ).execute().get("values", [])
    return len([row for row in values if row and row[0]])


def _scenario_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Return only non-sensitive counters suitable for a public Actions log."""
    keys = (
        "radar_status",
        "radar_rows_added",
        "radar_rows_unchanged",
        "radar_source_verified",
        "radar_write_verified",
        "radar_persistence_verified",
        "radar_unresolved_count",
        "readback_ok",
    )
    return {key: result.get(key) for key in keys}


def run() -> dict[str, Any]:
    tree_hashes = verify_approved_tree()
    run_id = verify_execution_context()
    if any(
        os.environ.get(name)
        for name in (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "GMAIL_APP_PASSWORD",
            "GOOGLE_CREDENTIALS",
        )
    ):
        raise RuntimeError("delivery credentials present; refusing isolated integration run")

    creds = existing_credentials()
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    # Read-only auth check before creating any object.
    drive.about().get(fields="user(permissionId)").execute()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    title = f"Abacus radar integration test run {run_id} {stamp}"
    created = sheets.spreadsheets().create(
        body={"properties": {"title": title}, "sheets": [{"properties": {"title": TEST_TAB}}]},
        fields="spreadsheetId,spreadsheetUrl,properties.title",
    ).execute()
    sheet_id = created["spreadsheetId"]
    _mask(sheet_id)
    _mask(created.get("spreadsheetUrl"))
    if sheet_id in DENIED_IDS or created.get("properties", {}).get("title") != title:
        raise RuntimeError("created spreadsheet identity failed validation")
    _assert_private(drive, sheet_id)
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{TEST_TAB}'!A1:J1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()

    guarded = _GuardedSheets(sheets, sheet_id, TEST_TAB)
    scenarios: dict[str, Any] = {}
    with patch.object(radar, "RADAR_SPREADSHEET_ID", sheet_id), patch.object(
        radar, "RADAR_SHEET_TITLE", TEST_TAB
    ), patch.object(radar, "_sheets_service", return_value=guarded):
        with patch.object(radar, "_verify_official_source", return_value=("simulated verified", True)):
            first = radar.process_radar(_collected(_record()))
        if not (
            first["radar_status"] == "success"
            and first["radar_write_verified"] == 1
            and first["radar_persistence_verified"] == 1
        ):
            raise AssertionError(f"first write failed: {first}")
        scenarios["first_verified_write"] = _scenario_summary(first)

        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{TEST_TAB}'!H2",
            valueInputOption="RAW",
            body={"values": [["MANUALLY_CHANGED"]]},
        ).execute()
        with patch.object(radar, "_verify_official_source", return_value=("simulated verified", True)):
            repeat = radar.process_radar(_collected(_record(status="NEW")))
        persisted_status = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{TEST_TAB}'!H2"
        ).execute().get("values", [[""]])[0][0]
        if not (
            repeat["radar_status"] == "success"
            and repeat["radar_write_verified"] == 0
            and repeat["radar_rows_unchanged"] == 1
            and persisted_status == "MANUALLY_CHANGED"
        ):
            raise AssertionError(f"repeat preservation failed: {repeat}|{persisted_status}")
        scenarios["repeat_preserves_changed_state"] = _scenario_summary(repeat)

        before = _row_count(sheets, sheet_id)
        with patch.object(radar, "_verify_official_source", return_value=("simulated HTTP 403", False)):
            blocked = radar.process_radar(_collected(_record(number="INT-403")))
        after = _row_count(sheets, sheet_id)
        if not (
            blocked["radar_status"] == "partial"
            and blocked["radar_source_verified"] == 0
            and blocked["radar_write_verified"] == 0
            and before == after
        ):
            raise AssertionError(f"403 guard failed: {blocked}|rows={before}->{after}")
        scenarios["http_403_no_write"] = _scenario_summary(blocked)

        unknown = _record(number="INT-UNKNOWN")
        unknown["source"] = "unknown_source"
        before = _row_count(sheets, sheet_id)
        skipped = radar.process_radar(_collected(unknown))
        after = _row_count(sheets, sheet_id)
        if skipped["radar_status"] != "skipped" or before != after:
            raise AssertionError(f"unknown-source guard failed: {skipped}|rows={before}->{after}")
        scenarios["unknown_source_no_write"] = _scenario_summary(skipped)

    checkpoint_name = f"Abacus checkpoint integration test run {run_id} {stamp}.md"
    checkpoint_media = MediaIoBaseUpload(
        io.BytesIO(b"previous synthetic checkpoint"), mimetype="text/markdown", resumable=False
    )
    checkpoint = drive.files().create(
        body={"name": checkpoint_name, "mimeType": "text/markdown"},
        media_body=checkpoint_media,
        fields="id,webViewLink",
    ).execute()
    checkpoint_id = checkpoint["id"]
    _mask(checkpoint_id)
    _mask(checkpoint.get("webViewLink"))
    if checkpoint_id in DENIED_IDS:
        raise RuntimeError("created checkpoint collided with denied ID")
    _assert_private(drive, checkpoint_id)
    writer = CheckpointsWriter.__new__(CheckpointsWriter)
    writer.drive = _GuardedDrive(drive, checkpoint_id)
    writer.now_utc = datetime.now(timezone.utc)
    writer.timestamp = writer.now_utc.isoformat().replace("+00:00", "Z")
    # The production writer prints caught exception text; discard it here so a
    # Google request URI containing the private test ID can never reach logs.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        with patch("modules.daily_briefing.checkpoints_writer.CHECKPOINTS_FILE_ID", checkpoint_id):
            checkpoint_ok = writer.update_checkpoints(
                {"gmail": False, "calendar": False, "tasks": False, "researcher": True},
                status="partial",
                details=first,
            )
    if not checkpoint_ok:
        raise AssertionError("checkpoint exact readback failed")
    scenarios["checkpoint_exact_readback"] = {"ok": True}

    return {
        "approved_commit": APPROVED_COMMIT,
        "tree_hashes": tree_hashes,
        "google_calls": "real Sheets/Drive API",
        "source_http": "simulated fixtures only",
        "spreadsheet_title": title,
        "checkpoint_title": checkpoint_name,
        "private_permissions_verified": True,
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        hashes = verify_approved_tree()
        if not args.execute:
            print(json.dumps({"preflight": "ok", "tree_hashes": hashes}, indent=2))
            return 0
        print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        # Never print raw API exceptions: request URIs can contain private IDs.
        safe_codes = {
            "EXECUTION_CONTEXT_BLOCKED",
            "NON_MANUAL_EVENT_BLOCKED",
            "UNEXPECTED_GIT_REF_BLOCKED",
            "INVALID_RUN_ID_BLOCKED",
            "PRIVATE_PERMISSION_CHECK_FAILED",
            "NON_TEST_SPREADSHEET_BLOCKED",
            "NON_TEST_TAB_BLOCKED",
            "NON_TEST_DRIVE_TARGET_BLOCKED",
        }
        message = str(exc)
        error = message if message in safe_codes or message.startswith("NO_EXISTING_") else type(exc).__name__
        print(json.dumps({"integration": "blocked", "error": error}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
