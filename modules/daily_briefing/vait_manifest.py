"""Bind existing trusted per-unit artifacts into a local, pinned VAIT manifest."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from modules.command_center.report_gate import ReportValidationError
from modules.command_center.vait_receipts import assemble_manifest, save_manifest


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-out", required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--unit", action="append", nargs=4, required=True,
                        metavar=("TASK", "RUN", "RECEIPT_RELATIVE", "BODY_RELATIVE"))
    args = parser.parse_args(argv)
    result = {"task": "vait_manifest", "status": "blocked", "units_verified": 0}
    try:
        output = Path(args.manifest_out)
        units = [dict(zip(("task_id", "run_id", "receipt_path", "body_path"), item))
                 for item in args.unit]
        manifest = assemble_manifest(output.parent, window_start=args.window_start,
                                     window_end=args.window_end, units=units)
        save_manifest(output, manifest)
        result.update(status="manifest_bound", units_verified=2)
    except ReportValidationError as exc:
        result["error_code"] = str(exc)
    except Exception:
        result["error_code"] = "manifest_or_artifact_unreadable"
    # No clinical content, URLs, paths or run IDs in public diagnostics.
    print(json.dumps(result))
    return 0 if result["status"] == "manifest_bound" else 1


if __name__ == "__main__":
    raise SystemExit(main())
