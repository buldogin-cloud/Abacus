# Evidence quality gate (migration from Abacus)

The collector is **not equivalent** to ChatGPT's research and analysis. This gate makes gaps visible rather than fabricating conclusions.

- Every handoff carries `evidence_quality` with explicit missing-context and source-verification warnings.
- A matching official domain alone **never** proves that the document was checked.
- Normative updates always require human/ChatGPT review against the official text, date, applicability and changes.
- Email summaries shorter than 40 characters are marked incomplete; the collector must later fetch full authorized content and attachments.
- Never send patient-identifiable information to external AI services. Use minimum necessary data, access controls and an approved processing basis.
- Do not silently mark missing sources as empty. Preserve failures and retry windows.
- No automatic clinical recommendations or task/deadline changes based on unverified evidence.

## Next integration milestones
1. Backfill missing Gmail windows and attachment metadata with bounded pagination.
2. Capture exact source URL, publication date, change detection and content hash for each normative document.
3. Add per-source coverage metrics and alert on stale checkpoints.
4. Compare a sample of automated reports against a human-reviewed ChatGPT daily briefing before enabling delivery.
5. Only then replace the Abacus runtime; do not claim this PR removes every Abacus dependency.
