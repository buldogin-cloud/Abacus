# VAIT receipt binding (local, not deployed)

This bridge consumes evidence; it does not produce clinical reports, source
checks, storage readbacks or delivery confirmations. It makes no network calls,
loads no credentials, writes no Sheets/Drive/Telegram state and advances no
checkpoint. The scheduled workflow is unchanged and remains blocked without a
real producer/export binding. Passing synthetic tests does not restore production.

## Input contract

A trusted producer/export step must supply, for each of `vait_1` and `vait_2`:

- A distinct JSON receipt accepted by `report_gate.validate_report`.
- Its distinct nonempty body file, whose bytes are identical to the report and
  storage readback hashes in the receipt.
- Observed source-check, review, storage/readback and delivery evidence. A failed
  or unknown delivery is not success. Missing units or incomplete sources do not
  constitute a complete two-unit result.
- Its actual run ID. The orchestrator supplies expected run IDs and start/end
  coverage boundaries independently of the artifact contents.

All four files must already exist under the manifest's parent directory. Paths
are explicit and relative; absolute paths and resolved escapes (including
symlinks outside the directory) are rejected. The bridge does not pick latest
files, search Drive or guess dates/runs from filenames. Patient bodies must stay
in authorized private staging; never commit them or upload that directory as a
public CI artifact. Only sanitized health output is suitable for the existing
workflow's health artifact.

Example using previously produced artifacts and caller-selected expectations:

```sh
python -m modules.daily_briefing.vait_manifest \
  --manifest-out private-staging/manifest.json \
  --window-start EXPECTED_START_WITH_TIMEZONE \
  --window-end EXPECTED_END_WITH_TIMEZONE \
  --unit vait_1 EXPECTED_VAIT1_RUN unit1/receipt.json unit1/body.txt \
  --unit vait_2 EXPECTED_VAIT2_RUN unit2/receipt.json unit2/body.txt
python -m modules.daily_briefing.vait_postcheck \
  --manifest private-staging/manifest.json --report sanitized-health.json
```

The positive expected window must not be future and must end no more than 24
hours before verification. Delayed runs outside this window remain blocked.

## Mechanical guarantees and limits

The assembler validates both units first, then creates a schema-v2 manifest with
SHA-256 pins of each existing receipt and body. It copies no receipt/body and
never upgrades their stages. Postcheck validates pins plus the original report
gate. Any edit after binding requires explicit reassembly; stale pins fail.
Existing schema-v1/unversioned manifests retain original unpinned checks; new
assemblies always use v2. Absolute artifact paths are no longer accepted.

Manifest creation is exclusive and never overwrites input artifacts. An identical
retry revalidates current artifacts; conflicting existing files/symlinks fail.
Interrupted writes can leave an incomplete manifest that must not be treated as
success or automatically overwritten. The health output must not collide with
manifest, receipt or body paths (including symlink/hardlink aliases). The caller
must isolate staging from concurrent untrusted writers; filesystem checks do not
provide a hostile-process security boundary.

No publication, delivery or checkpoint is implied by `manifest_bound`. Receipt
claims remain assertions from trusted producers. Hashes are not signatures,
independent source checks or clinical validation. Never fabricate receipt fields
just to make the gate green.

## Remaining release gates

1. Identify the actual producer and receipt schema. Do not convert prose or
   successful API submission into invented evidence of readback or user delivery.
2. Approve and implement private materialization/transport for both units and
   the exact expected window. No such transport is included in this patch.
3. Supply independently trusted expected run/window values.
4. Test a genuine full cycle, missing/stale unit, partial source coverage, failed
   readback/delivery, changed artifacts and duplicate retry.
5. Only then wire the existing scheduled postcheck to the verified manifest.
   Do not add patient artifacts to GitHub or alter clinical source tables.

## Tests

Run the full repository unittest suite and compile check with dependencies
installed. `tests/test_vait_binding.py` uses synthetic bodies/receipts only and
covers both units, identity/window/stage failures, pins, path confinement, safe
local manifest writes and sanitized postcheck output.
