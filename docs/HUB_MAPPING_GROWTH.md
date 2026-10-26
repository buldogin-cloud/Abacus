# Hub mapping growth (bounded, no new writer)

The configured minimum_source_count remains a lower-bound completeness guard,
not an exact row target. Its default and unchanged production contract remain 26.
Only positive integer configuration values are accepted. The same minimum is
forwarded to mapping persistence; included and explicitly excluded rows both count.

The existing private mapping grid has 200 rows: one header and at most 199 source
mappings. Counts 26, 29 and growth through 199 are supported, not unlimited growth.
Capacity overflow, duplicate physical rows/stable IDs/Task IDs/anchors, missing
anchors and missing exclusion reasons fail before any history mutation. Exact
anchor fallback for rows without a Task ID remains supported. Exclusions such as
source row policy 707 are retained, not converted to tasks.

Source snapshots read all rows in the same configured columns and source sheet,
normalizing a bounded A1:V200-style range to A1:V. This prevents an overflow from
being silently truncated into a passing 199-row snapshot. Headers and identity
resolution are still validated by the existing adapter. Repeated prewrite and
postwrite snapshots use this same full-row range. This can read more cells and
consume more read bandwidth than a bounded range; it does not read archives,
other worksheets or new sources. No source cells are written.

Mapping writes remain A:L only, preserving N:O settings and Q:Z checkpoints.
History bounds (configured separately), the 200-row checkpoint log, formulas,
formatting, credentials, schedules and all live configuration remain unchanged.
The separate UI formula repair covers mapping rows 2:200; finite panel display
limits remain. Increasing physical capacity or enabling a recurring writer needs
its own reviewed change. This patch neither enables adapters nor runs live sync.
