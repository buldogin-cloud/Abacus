"""Load existing Google credentials without implicit login or secret disclosure.

Gmail uses a separate IMAP app password. This module never creates credentials,
opens a browser, writes tokens, or silently switches accounts after an error.
"""

import json
import os
from pathlib import Path
from typing import Iterable

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

REPO_ROOT = Path(__file__).parent.parent
CREDENTIALS_FILE = REPO_ROOT / "credentials.json"
TOKEN_FILE = REPO_ROOT / "token.json"

# Existing runtime scopes, unchanged. Recovery must not broaden these implicitly.
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


class GoogleAuthError(RuntimeError):
    """A safe, stable diagnostic suitable for logs (never contains token data)."""

    def __init__(self, code: str, source: str) -> None:
        self.code = code
        self.source = source
        super().__init__(
            f"Google authentication unavailable [{code}; source={source}]. "
            "See docs/AUTH_RECOVERY.md. Do not paste credentials into chat or logs."
        )


def _load_token_from_env(scopes: Iterable[str]) -> Credentials | None:
    """Explicit environment configuration takes precedence, including errors."""
    if "GOOGLE_TOKEN" not in os.environ:
        return None
    token_json = os.environ["GOOGLE_TOKEN"]
    if not token_json.strip():
        raise GoogleAuthError("missing_token", "GOOGLE_TOKEN")
    try:
        info = json.loads(token_json)
        if not isinstance(info, dict):
            raise ValueError("Token must be an object")
        return Credentials.from_authorized_user_info(info, list(scopes))
    except Exception:
        # Library exception strings may include submitted values. Never echo them.
        raise GoogleAuthError("malformed_token", "GOOGLE_TOKEN") from None


def _validate_or_refresh(creds: Credentials, source: str) -> Credentials:
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise GoogleAuthError("missing_refresh_token", source)
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        # Inspect only to classify; do not propagate the provider payload.
        code = "reauthorization_required" if "invalid_grant" in str(exc) else "refresh_rejected"
        raise GoogleAuthError(code, source) from None
    except Exception:
        raise GoogleAuthError("refresh_unavailable", source) from None
    if not creds.valid:
        raise GoogleAuthError("invalid_refreshed_token", source)
    return creds


def get_credentials(scopes: Iterable[str] | None = None) -> Credentials:
    """Return usable existing credentials, or fail closed with a safe error.

    GOOGLE_TOKEN is authoritative when present. Otherwise token.json is used.
    Invalid configured credentials never fall back to another account, a browser
    login or credentials.json. Reauthorization is an explicit owner operation.
    Refreshing an existing grant happens in memory only; no secret files are saved.
    """
    scopes = list(DEFAULT_SCOPES if scopes is None else scopes)
    creds = _load_token_from_env(scopes)
    if creds is not None:
        return _validate_or_refresh(creds, "GOOGLE_TOKEN")
    if not TOKEN_FILE.exists():
        raise GoogleAuthError("missing_token", "token.json")
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), scopes)
    except Exception:
        raise GoogleAuthError("malformed_token", "token.json") from None
    return _validate_or_refresh(creds, "token.json")
