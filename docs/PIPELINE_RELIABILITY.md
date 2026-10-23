# Pipeline reliability and rollout gate

This draft change does not restore revoked Google credentials or prove live operation.

## What success means

A production exit code of zero requires all four configured sources to succeed,
verified handoff and radar persistence, and successful checkpoint/log persistence.
The local health JSON contains status/counts only, never source records or secrets.
Dry runs are labelled `dry_run_ok`, not persisted success. No live integration is
called by the regression suite.

Unreadable canonical checkpoints, ambiguous Drive filenames, malformed existing
handoff archives, and mismatching full readbacks fail closed. Checkpoints advance
to capture start, not completion time. Workflow concurrency serializes this
pipeline's writes; it cannot protect against unrelated external writers, because
Drive text append is not transactional.

## Coverage boundaries

Gmail capture is read-only and covers recent INBOX messages, including messages
already read. Archived, spam and trash folders are not covered. Message bodies and
attachment metadata are preserved; attachment contents are not analysed. A capped
collection is partial and cannot advance checkpoints. Gmail URLs are emitted only
when Gmail's actual X-GM-MSGID is available; UID fallback identifiers are explicitly
scoped to account, mailbox and UIDVALIDITY.

Calendar collection follows pagination. Changed calendar/research records use
content fingerprints rather than treating a stable object ID as proof of no change.
Legacy archive entries without fingerprints may be emitted once again for upgrade.
Deduplication still uses the existing 14-day archive window.

## Before rollout

1. Review this draft PR, including the preserved evidence-quality gate.
2. Recover only the required credentials through the approved secure route in
   AUTH_RECOVERY.md. Never put tokens or app passwords in chat, commits or logs.
3. Obtain approval for deployment and for a bounded live validation, identifying
   its Drive/registry/notification effects first. No live validation was run while
   implementing this draft.
4. Verify a real run's source coverage, readbacks and final health report before
   calling the system operational. Passing mocked regression tests is not enough.

The workflow no longer commits arbitrary runtime files with `git add -A`.
