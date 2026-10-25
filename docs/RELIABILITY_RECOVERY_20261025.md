# Reliability recovery, 25 October 2026

This patch is not evidence that production is restored. Unit tests use synthetic
fixtures, never real patients, and do not prove external access or delivery.

## Implemented

- Allowlisted auth diagnostics in the capture and Hub reports. Raw provider
  payloads are not added to these diagnostics.
- Manual read-only auth preflight: existing Google grant, then metadata GET on
  the canonical checkpoint and Secretary registry. No new grants, token writes,
  sharing edits, emails or Telegram messages. Its status proves those two reads
  only, not source ingestion, attachment parsing, Hub sync or delivery.
- Webhook health describes process liveness only. Status/briefing fallback text
  does not invent source coverage. Removed the public send-test route and its
  token-prefix disclosure, and import-time credential-file setup.
- VAIT postcheck is fail-closed. No manifest means blocked, not success. It no
  longer writes a fabricated postcheck report to Drive.
- Offline receipt validation binds task, run, timezone-aware window, body hash,
  read-back hash, required stages, observed source times and review hash. Empty,
  altered, wrong-identity, future or incompletely staged evidence is rejected.
- Kyiv 07:00 capture and 08:35 postcheck have paired UTC cron slots with a DST
  guard based on the scheduled slot, not the delayed execution hour.

## Important limits and release gates

- Receipt checks are mechanical. A self-asserted `content_review: verified` is
  not independent semantic or clinical verification. A producer could lie about
  source evidence; no signatures or trusted reviewer integration are added here.
- The VAIT producer/manifest integration is absent. The scheduled postcheck is
  intentionally blocked until real per-unit receipts and local body files are
  supplied. Do not manufacture receipts to make CI green. CLI rejects manifests
  whose window end is older than 24 hours or in the future; the general validator
  does not impose a universal maximum age, which varies by report type.
- The existing pipeline retains a conservative all-or-nothing capture cursor.
  It does NOT yet implement a separate acknowledged-processing cursor or a
  durable unresolved queue for invalid captures. Invalid records prevent cursor
  advancement. No parallel canonical queue/checkpoint has been invented here.
- The existing 14-day dedup scan is bounded, not permanent deduplication.
- The existing collector upper time bounds and end-to-end Secretary consumer
  acknowledgment still need review before claiming exactly-once recovery.
- Research source configuration/fixtures from the source audit are not deployed
  by this patch. No clinical/source completeness claim is implied.
- No production pipeline, webhook deployment, mail send or Drive mutation was
  executed during this offline engineering work.

## Validation and safe rollout

Run `python -m compileall -q integrations modules tests authorize.py` and
`python -m unittest discover -s tests -q` with requirements installed.
Review diff, then publish the branch for review. After explicit rollout approval,
run only the manual read-only preflight first. If auth is unavailable, require
owner reauthorization through the documented secure flow; never retrieve or
paste secrets into chat. Actual production jobs require separate confirmation
of their writes/sends. A passing preflight does not authorize those side effects.
