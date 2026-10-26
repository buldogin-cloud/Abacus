# Configurable research feed repair (local, not deployed)

Base: d80df89ea606e75fa327e523d7c30ca0ce6543ab.

This patch wires explicitly configured RSS feeds through Researcher.collect,
the normal collector and digest, preserving a distinct source code and clinical
article type. Clinical entries are review candidates, never automatic task
updates. Feed parsing distinguishes valid empty results from blocked, malformed,
HTML or failed responses. Caps/invalid entries mark partial coverage.

The JPAIC endpoint is from the existing October 4 monitoring plan:
(private monitoring plan; no private file link is published here).
The endpoint is not newly verified: direct HTTP to the site returned 403 here,
and a web RSS open did not return content. Configuration therefore remains
disabled in the example configuration. The live config.yaml is deliberately
unchanged; no production feed configuration is applied. No claim of live
operation or scheduled execution is made.

Offline checks use synthetic XML only. This is feed metadata, not PDF collection,
full-text verification, clinical appraisal, archive coverage, DOI/PDF revision
tracking, or guideline-currentness verification. Existing Google News collection
quality limits are unchanged. A feed snapshot cannot justify a historical catch-up
checkpoint. Do not turn enabled on until source accessibility, appropriate cap,
readback, receipt/delivery and existing-flow deduplication are validated.

Before rollout, independently review the diff, run the whole offline suite and
get publication/rollout approval. No credentials, rights, schedules, Drive files,
patient sheets, protected memos or MIS code are changed by this patch.
