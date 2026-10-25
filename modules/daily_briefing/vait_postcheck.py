"""Offline verification of actual VAIT receipts; never manufacture success.

Without explicit receipt inputs this check fails as unconfigured. It does not
read patient data from live sources, write Drive, or change checkpoints.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.command_center.report_gate import validate_report, ReportValidationError, timestamp


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", help="Local manifest with expected window and two unit receipts")
    parser.add_argument("--report", help="Sanitized local health output")
    args = parser.parse_args(argv)
    report = {"task": "vait_postcheck", "status": "blocked",
              "checked_at": datetime.now(timezone.utc).isoformat(), "units_verified": 0}
    try:
        if not args.manifest:
            raise ReportValidationError("receipt_manifest_not_configured")
        manifest_path = Path(args.manifest).resolve()
        manifest = json.loads(manifest_path.read_text())
        units = manifest.get("units", [])
        if len(units) != 2 or {u.get("task_id") for u in units} != {"vait_1", "vait_2"}:
            raise ReportValidationError("both_units_required")
        window = (manifest["window_start"], manifest["window_end"])
        age = (datetime.now(timezone.utc) - timestamp(window[1])).total_seconds()
        if not 0 <= age <= 24 * 3600:
            raise ReportValidationError("stale_or_future_window")
        # Files must be explicit local artifacts under the manifest directory.
        for unit in units:
            paths = []
            for field in ("receipt_path", "body_path"):
                path = (manifest_path.parent / unit[field]).resolve()
                if not path.is_relative_to(manifest_path.parent):
                    raise ReportValidationError("artifact_outside_manifest_directory")
                paths.append(path)
            receipt = json.loads(paths[0].read_text())
            validate_report(receipt, paths[1].read_bytes(), expected_task=unit["task_id"],
                            expected_run=unit["run_id"], expected_window=window)
            report["units_verified"] += 1
        report["status"] = "receipt_verified"
    except ReportValidationError as exc:
        report["error_code"] = str(exc)
    except Exception:
        report["error_code"] = "manifest_or_artifact_unreadable"
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["status"] == "receipt_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
