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
from modules.command_center.report_gate import ReportValidationError
from modules.command_center.vait_receipts import verify_manifest


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", help="Local manifest with expected window and two unit receipts")
    parser.add_argument("--report", help="Sanitized local health output")
    args = parser.parse_args(argv)
    report = {"task": "vait_postcheck", "status": "blocked",
              "checked_at": datetime.now(timezone.utc).isoformat(), "units_verified": 0}
    write_health = True
    try:
        if not args.manifest:
            raise ReportValidationError("receipt_manifest_not_configured")
        manifest_path = Path(args.manifest).resolve()
        if args.report:
            # Until collision checks finish, failures may only emit stdout.
            write_health = False
            output = Path(args.report).resolve()
            if output == manifest_path or (output.exists() and manifest_path.exists()
                                           and output.samefile(manifest_path)):
                raise ReportValidationError("health_output_overwrites_artifact")
        manifest = json.loads(manifest_path.read_text())
        if args.report:
            output = Path(args.report).resolve()
            protected = [manifest_path]
            units = manifest.get("units", []) if isinstance(manifest, dict) else []
            for unit in units if isinstance(units, list) else []:
                if isinstance(unit, dict):
                    for key in ("receipt_path", "body_path"):
                        if isinstance(unit.get(key), str):
                            protected.append((manifest_path.parent / unit[key]).resolve())
            for path in protected:
                if output == path or (output.exists() and path.exists() and output.samefile(path)):
                    write_health = False
                    raise ReportValidationError("health_output_overwrites_artifact")
            write_health = True
        report.update(verify_manifest(manifest, manifest_path.parent))
        report["status"] = "receipt_verified"
    except ReportValidationError as exc:
        report["error_code"] = str(exc)
    except Exception:
        report["error_code"] = "manifest_or_artifact_unreadable"
    if args.report and write_health:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["status"] == "receipt_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
