"""Explicit owner-operated local OAuth recovery; never run from automation.

Review docs/AUTH_RECOVERY.md and the requested permissions before use.
No credentials should be shared through chat, email, commits, or CI logs.
"""
import argparse
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from integrations.google_auth import DEFAULT_SCOPES

SCOPES = list(DEFAULT_SCOPES)
ROOT = Path(__file__).resolve().parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Explicit local Google OAuth recovery")
    parser.add_argument("--authorize", action="store_true", help="Explicitly start owner-approved browser consent")
    parser.add_argument("--credentials", type=Path, default=ROOT / "credentials.json")
    parser.add_argument("--token-output", type=Path, default=ROOT / "token.json")
    args = parser.parse_args(argv)
    if not args.authorize:
        parser.error("Review docs/AUTH_RECOVERY.md, then use --authorize to explicitly start consent.")
    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        parser.error("Interactive authorization must run locally, not in CI.")
    if args.token_output.exists():
        parser.error("Output already exists. Choose a new private output path; no token will be overwritten.")
    if not args.credentials.is_file():
        parser.error("OAuth client file is missing. Obtain it from the authorized project owner.")
    print("Review these exact permissions in the browser; do not approve additional access:")
    for scope in SCOPES:
        print(f"  {scope}")
    print("Select the intended account. Keep all resulting credentials private.")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(args.credentials), SCOPES)
        creds = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
            success_message="Authorization complete. Return to the local terminal.",
        )
        if not creds.refresh_token:
            raise RuntimeError("No refresh token")
        # Exclusive creation avoids overwriting existing credentials or symlinks.
        # On POSIX the file is private from creation, not after writing the secret.
        fd = os.open(args.token_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
    except Exception:
        print("Authorization or private token save failed. No token contents are displayed.")
        return 1
    print("Saved a private token file. Update GOOGLE_TOKEN only through the repository's secure Actions secret editor.")
    print("Never send token.json to chat, email, a pull request, or workflow logs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
