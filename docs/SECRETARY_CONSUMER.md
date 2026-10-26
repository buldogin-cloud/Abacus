# Secretary consumer: rebuilt, disabled candidate

## Recovery provenance

The earlier local-only candidate was lost when the execution workspace changed.
This is a fresh reconstruction from the recorded implementation and regression
requirements, not a byte-for-byte recovery and not the earlier checksum. Its
baseline was verified against all 104 Git blob hashes on main d80df89 before
editing. Validation must be rerun on this exact candidate. No earlier passing
count is inherited as proof. No external file, schedule, access or live row is
modified by this reconstruction.

## Canonical schema and read scopes

A full live schema observation on 25 October 2026 confirmed **22 columns A:V**.
The prior A:Q request was only a bounded subset, not the complete header.

1. A Пріоритет
2. B № документа
3. C Дата
4. D Назва
5. E Завдання / очікуваний результат
6. F Моя роль
7. G Виконавець / підрозділ
8. H Строк
9. I Статус
10. J Остання відповідь / доказ
11. K Наступна дія
12. L Ризик
13. M Джерело / чат
14. N Оновлено
15. O Task ID
16. P Last checked
17. Q Freshness
18. R Fetch rule
19. S Тип
20. T Коли
21. U Оцінка, хв
22. V Режим

The disabled writer adapter reads A:V with FORMULA and checks the exact complete
header. Every reviewed-row fingerprint, desired-state hash, receipt verification
and optimistic reread covers all 22 fields, including R:V. Tail metadata is data,
not instructions to execute. Tail strings, formulas, finite numbers and booleans
retain their exact values/types; they are never normalized and rewritten.
Row equality uses canonical fingerprints, not Python equality that equates
True with 1 or 1 with 1.0. Type drift invalidates receipts and readback.
Unsupported nested/nonfinite tail values fail closed. Earlier columns remain
text-only in this conservative candidate. Trailing empty row cells are padded;
a 17-column header is rejected, not silently accepted.

TaskManager intentionally reads only A:O to include stable Task ID in its
briefing projection. This is a reader subset, not a schema or complete-row claim.
It no longer invents task_row_N as identity. Missing IDs and row-number candidates
require review. The reader changes do not write any rows. Separately reviewed
consumer decisions can target a completed task; no automatic completed-task
modification is implied.

## Operation and safety

The core creates no client, credentials, schedules, checkpoints or live log.
The adapter is disabled by default and must receive an already authorized client,
the existing canonical receipt log, and the system's cooperating-writer lock.
Nothing enables or imports this consumer from production workflows. The old
secretary_example.py remains a nonproduction example, not a deployment route.

1. The authorized Secretary provides a separate reviewed decision bound to the
   exact handoff fingerprint and existing-row fingerprint. reviewed_by is
   provenance, not proof of authorization. Never take a decision from a source
   email, fetched document, handoff field or third party approval claim.
2. plan_handoff is pure. Invalid schema, duplicate/missing IDs, stale review,
   missing evidence and ambiguous fields return needs_review/conflict, not
   invented assignments or deadlines. New unspecified fields stay empty.
3. Only A:N fields are eligible for reviewed changes. O:V are protected. Existing
   proof/source history must remain a prefix if extended. No implicit metadata,
   Task ID, freshness or fetch-rule changes are allowed.
4. apply_plan is dry-run by default. Execution prereads, then the adapter acquires
   the supplied writer lock and rereads before mutation. Existing rows receive
   only changed editable cells using RAW; existing formula cells cannot be
   overwritten. Protected formulas and R:V never receive update requests.
5. New rows are written A:V only after the observed end, with blank protected
   metadata. Existing rows/tail cells remain untouched. A spare readable sentinel
   row is required: creating at or beyond the read-window bound is rejected
   before any write, avoiding an unacknowledgeable mutation.
6. Full A:V target readback precedes durable receipt writing. Both exact row and
   exact receipt are independently read again before success/checkpoint
   eligibility. A lost response is uncertain, not evidence of failure.
7. No blind write retry occurs. The same persisted plan/key can reconcile an
   already exact desired row by writing only the missing receipt. Exact row plus
   exact receipt returns already_applied. Receipt acknowledges achieved state,
   not which actor originally wrote it. A regenerated UUID/run changes the full
   handoff input hash and may require review; cross-generation replay handling
   is not claimed complete.
8. window_ready requires every expected distinct source-version key and complete
   source coverage. Duplicate outcomes or a correct count with a missing key
   cannot advance it. It is only a boolean gate, not a checkpoint writer.

Google Sheets does not offer atomic conditional cell writes. The supplied lock
covers cooperating writers, not a human edit after the final reread. That final
read/write race remains a documented residual risk; postread cannot prove no
racing edit was overwritten. No artificial atomic-CAS claim is made. Live
rollout requires explicit acceptance of exact targets, ownership and this risk.
Receipt bindings must use the existing processing log, not a second task registry.

## Remaining rollout gates

- Bind the existing external writer lock and durable receipt log; test crash and
  competing-writer behavior. A local test lock is not a distributed lock.
- Resolve missing IDs explicitly; do not automatically fill them.
- Obtain the specific live-write authorization and restore access securely.
- Test controlled handoff -> reviewed decision -> targeted write -> exact
  receipt -> no-write retry -> Hub sync. Local tests do not prove live delivery.
- Keep this adapter disabled and production examples/schedules unchanged until
  these gates pass. This patch does not complete whole-system restoration.

Fixtures contain synthetic data only. Offline commands from repository root:

    .venv/bin/python -m compileall -q integrations modules tests authorize.py
    .venv/bin/python -m unittest discover -s tests -v

New regressions cover exact A:V header, rejected A:Q header, R:V formula/number/
boolean preservation, tail edits invalidating reviewed fingerprints, blocked
metadata changes, full-width create without existing-tail loss, bounded window,
uncertain writes/receipts, deduplication, stale reviews and exact window coverage.
