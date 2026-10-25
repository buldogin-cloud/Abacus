"""Read-only probes of existing Google access; never expose provider payloads.

Uses only the existing grant and metadata GETs. Refresh, if necessary, is in
memory. No credential configuration, file creation, permission edits or sends.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from googleapiclient.discovery import build
from integrations.google_auth import get_credentials
from modules.command_center.health import safe_auth_diagnostic
from modules.daily_briefing.canonical import CHECKPOINTS_FILE_ID, TASKS_REGISTRY_SHEET_ID


def run():
    result = {'task': 'auth_preflight', 'status': 'blocked',
              'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': {}}
    try:
        credentials = get_credentials()
        service = build('drive', 'v3', credentials=credentials, cache_discovery=False)
        for label, file_id in [('checkpoint_metadata', CHECKPOINTS_FILE_ID),
                               ('secretary_metadata', TASKS_REGISTRY_SHEET_ID)]:
            metadata = service.files().get(fileId=file_id, fields='id,trashed').execute()
            if metadata.get('id') != file_id or metadata.get('trashed') is not False:
                result['error_code'] = 'target_unverified'
                return result
            result['checks'][label] = 'read_verified'
        result['status'] = 'read_access_verified'
    except Exception as exc:
        result.update(safe_auth_diagnostic(exc))
        result['error_code'] = ('auth_unavailable' if result.get('auth_error_code')
                                else 'metadata_read_failed')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    result = run()
    # Only this sanitized local artifact is written; no provider content.
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'read_access_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
