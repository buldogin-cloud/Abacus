# Google access recovery (owner approval required)

A green unit-test run is not proof of working Google access. Code changes cannot
repair a revoked OAuth grant or invent a missing Gmail app password.

## Safe diagnostic codes

The runtime raises `GoogleAuthError` without printing provider payloads, JSON,
refresh tokens, client secrets, or filesystem contents:

- `missing_token`: `GOOGLE_TOKEN` is explicitly empty, or no local token exists.
- `malformed_token`: configured JSON or required authorized-user fields are invalid.
- `missing_refresh_token`: configured credentials cannot be refreshed.
- `reauthorization_required`: Google rejected refresh with `invalid_grant`. This
  does not by itself identify whether expiry, revocation, account policy or another
  cause is responsible. The owner must review and reauthorize the intended client.
- `refresh_rejected`: another OAuth refresh rejection; review the account/client
  configuration privately, not by posting raw errors or tokens.
- `refresh_unavailable`: refresh transport/service failure; retry only after checking
  connectivity and service health. Do not assume the grant was revoked.
- `invalid_refreshed_token`: refresh did not produce usable credentials.

An explicitly configured environment token is authoritative. A bad environment
value never silently falls back to a local account. Runtime clients never open a
browser or write tokens. Existing token refresh occurs in memory only.

## Owner-controlled recovery

1. Review and approve the concrete authentication action for the intended Google
   account and existing OAuth client. Do not grant broader permissions as a shortcut.
2. On a trusted local computer, obtain the existing client's `credentials.json`
   securely. Do not upload it or any token to this repository or a conversation.
3. Review `authorize.py`. The revised script requests exactly the current runtime
   scopes: Calendar read-only, Drive files accessible to this app (`drive.file`),
   and Sheets. It does not request Gmail send/modify or full Drive read access.
   Sheets is not a read-only scope; review this explicitly before accepting consent.
   If these scopes do not cover an approved feature, stop and seek separate approval
   rather than adding permissions automatically.
4. After approval, the owner can run `python authorize.py --authorize` locally.
   If `token.json` exists, choose a new private location with `--token-output`; the
   script deliberately refuses to overwrite existing credentials. CI is rejected.
   Check the intended Google account and the exact consent screen. Do not select
   additional permissions. This step creates persistent access and is not run by
   tests or scheduled jobs.
5. Using the GitHub repository's Settings → Secrets and variables → Actions secure
   editor, update `GOOGLE_TOKEN` with the private authorized-user JSON. The editor is
   at https://github.com/buldogin-cloud/Abacus/settings/secrets/actions . Do not paste
   it in chat, a PR, issue, workflow file, terminal output, or a screenshot.
   Updating a credential for ongoing service access requires the owner's approval.
6. Gmail is separate: `integrations/gmail_client.py` uses IMAP with
   `GMAIL_APP_PASSWORD`, not this OAuth token. An already-approved app password must
   be stored only via the secure Actions secret editor and made available to the
   Gmail job. If a new app password is needed, creation requires separate explicit
   owner approval through Google's account security flow. Do not enable or weaken
   account security automatically. App passwords can be revoked; they are not
   permanent guarantees of access.
7. First validate source reads without publishing or sending anything. Then perform
   an explicitly approved, bounded persistence check and read back the saved result.
   Check source statuses, capture completeness, write counts and content read-back;
   do not substitute passing unit tests or an empty query for this evidence.

No recovery command should silently send Telegram/email, mutate clinical records,
change registry tasks, or advance checkpoints. Review each live command's effects
before execution. The existing operational scripts are not necessarily read-only.
