# Deterministic Secretary to Hub sync

This module copies reviewed task states from a Secretary registry into the
append-only Command Center Hub history. It does not call an AI model.

## Safety boundary

- The Secretary spreadsheet is read-only in this workflow.
- A run must reconcile at least the verified 26-row baseline. The source range
  is open-ended or safely over-allocated, empty rows are ignored, and every
  additional non-empty row must resolve to an exact mapping or explicit
  source-policy suppression before any write.
- Missing task IDs resolve only by an exact persisted canonical anchor. There
  is no fuzzy title matching and the source row number is never identity.
- Only Hub history `A:P`, the private mapping tab, and link-only repairs in
  current history rows can be written.
- Existing frontend formulas are not rewritten. History capacity is checked
  before any write.
- The history/current-flag changes use one atomic Sheets batch. A network error
  triggers readback instead of a blind retry.
- A checkpoint advances only after exact history and private-mapping readback,
  plus an immediate idempotency check.
- Logs and CI artifacts contain counts and status only.

## Runtime configuration

The public repository contains only structural code, a production contract
without task data, and a synthetic example. GitHub Actions configuration
variables contain only the two technical spreadsheet IDs:

- `SECRETARY_SPREADSHEET_ID`
- `HUB_SPREADSHEET_ID`

This reuses the existing `GOOGLE_TOKEN`; it does not add an OAuth scope or a
new credential. If that token cannot read the source and write the Hub, the job
must stop with the Google API error. Do not request broader access silently.

The owner-only Hub stores the operational identity map and runtime schema in
`98 — SECRETARY SYNC`. A:L contains only hashes, technical IDs, disposition,
fingerprints and checkpoint metadata. N:O contains one `runtime_config_json`
setting with column/range rules, exact status/priority normalization,
reviewed Stable-ID-to-domain categories, and the neutral Today guard range. Titles,
descriptions, people, raw anchors,
raw task statuses, and snapshots are not persisted in this tab. Hiding is only
a convenience; spreadsheet sharing is the actual access boundary and this
workflow never changes it.

Q:Z is an append-only verified-run log containing only UTC verification time,
three SHA-256 fingerprints, counts, the immediate-rerun append count, and the
literal status `verified`. A frontend freshness label may read the latest Q
whose Z is `verified` and Y is zero; it must not use an attempted-run time.

The recurring workflow refuses to create the bootstrap tab. It must already
exist with 26 reviewed mappings before a credentialed dry-run can succeed.

Production projection is fail-closed. Hub A and M are numeric Google Sheets
datetime serials in the configured spreadsheet timezone. Hub H is either blank
or a numeric date serial parsed only from exact `dd.mm.yyyy` or
`До dd.mm.yyyy`. Narrative, ambiguous, and multiple-date deadline text remains
verbatim in Hub E while H stays blank. Priority is mapped to exactly four
configured Hub values. Domain category is selected from the reviewed Stable ID,
not the source's generic type column. Unknown, unmapped priority, status,
identity, or Stable-ID domain values stop the run.

## Schedule

The separate workflow is prepared for `06:30` and `13:30` UTC. GitHub cron is
UTC-only: this corresponds to 08:30/15:30 Kyiv in winter and 09:30/16:30 in
summer. It intentionally stays after the approximate Secretary passes without
claiming automatic daylight-saving adjustment.

## First-write procedure

1. Confirm the private live mapping against a fresh two-read source snapshot.
2. Run the full unit suite and a credentialed dry-run. The dry-run must report
   at least 26 accounted rows, the expected included/suppressed split, no capacity
   breach, and no unmapped status or identity.
3. Confirm the Hub file remains owner-only, and review the exact file set.
4. Execute once. Verify source values are unchanged, every included Stable ID
   has exactly one current state, appended rows match exactly, and the private
   mapping readback matches.
5. Execute the same snapshot again. It must append zero states. A source sort
   must also append zero states; only current-row hyperlinks may be repaired.

The sync never writes frontend formulas and compares only Google Sheets
`userEnteredValue` cells in the configured Today range before and after every
execute run. Calculated/spilled values may change normally and are not part of
the guard. In particular, `Today` remains a
separate verified view: only current unfinished Secretary tasks whose explicit
task/plan date equals today in `Europe/Kiev` may appear there. Overdue, future,
completed, undated, news, and recommendation rows are excluded; import time is
never interpreted as a task date.
