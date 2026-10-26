"""Reviewed handoff -> stable-ID plan -> guarded commit -> verified receipt.

No management decision is inferred from source text. No Google client, schedule,
credential, or checkpoint writer is created here. The optional injected adapter
is disabled until its exact targets and existing writer lock/log are approved.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Protocol

HEADERS = (
    "Пріоритет", "№ документа", "Дата", "Назва",
    "Завдання / очікуваний результат", "Моя роль", "Виконавець / підрозділ",
    "Строк", "Статус", "Остання відповідь / доказ", "Наступна дія", "Ризик",
    "Джерело / чат", "Оновлено", "Task ID", "Last checked", "Freshness",
    "Fetch rule", "Тип", "Коли", "Оцінка, хв", "Режим",
)
TASK_ID = "Task ID"
EDITABLE = frozenset(HEADERS[:14])
# Tail metadata can contain native numbers/bools. Preserve its exact type/value.
TAIL = frozenset(HEADERS[17:])
Cell = str | int | float | bool


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _row(raw: dict) -> dict[str, Cell]:
    if not isinstance(raw, dict) or set(raw) != set(HEADERS):
        raise ValueError("registry_schema_mismatch")
    for header, value in raw.items():
        if isinstance(value, str):
            continue
        if header in TAIL and type(value) in (int, float, bool):
            if not isinstance(value, float) or math.isfinite(value):
                continue
        raise ValueError("unsupported_registry_value")
    return dict(raw)


def _index(rows: list[dict]) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError("registry_unavailable")
    indexed = {}
    for raw in rows:
        row = _row(raw)
        task_id = row[TASK_ID]
        if not task_id:  # Unidentified legacy rows are never guessed.
            continue
        if task_id != task_id.strip() or task_id.startswith("task_row_"):
            raise ValueError("unstable_task_id")
        if task_id in indexed:
            raise ValueError("duplicate_task_id")
        indexed[task_id] = row
    return indexed


def source_key(handoff: dict) -> str:
    if not isinstance(handoff, dict):
        raise ValueError("invalid_handoff")
    for name in ("source", "source_id", "title", "factual_summary"):
        if not _text(handoff.get(name)):
            raise ValueError("incomplete_handoff")
    if handoff["source"] == "gmail":
        version = handoff["source_id"]
    else:
        keys = handoff.get("_dedup_keys")
        version = keys.get("content_version") if isinstance(keys, dict) else None
        if not _text(version):
            raise ValueError("missing_source_version")
    return "secretary-v1:" + fingerprint([handoff["source"], handoff["source_id"], version])


@dataclass(frozen=True)
class Plan:
    key: str
    input_hash: str
    decision_hash: str
    action: str
    task_id: str
    before_hash: str | None
    after: tuple[tuple[str, Cell], ...]

    @property
    def row(self) -> dict[str, Cell]:
        return dict(self.after)

    @property
    def receipt(self) -> dict:
        return {"key": self.key, "input_hash": self.input_hash,
                "decision_hash": self.decision_hash, "action": self.action,
                "task_id": self.task_id, "after_hash": fingerprint(self.row),
                "status": "applied_verified"}


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    plan: Plan | None = None
    checkpoint_eligible: bool = False


class ConditionalStore(Protocol):
    """A guarded adapter must use the existing writer lock and receipt log.

    read returns complete A:V data and durable receipt. commit rereads/resolves
    stable ID under a cooperating-writer lock, compares old version, writes only
    reviewed cells, verifies row, then stores receipt. No atomic Sheets CAS is
    claimed: noncooperating/human edits in the final gap remain a residual risk.
    """
    def read(self, key: str) -> tuple[list[dict], dict | None]: ...
    def commit(self, plan: Plan) -> None: ...


def plan_handoff(handoff: dict, decision: dict | None, rows: list[dict]) -> Outcome:
    """Pure planner. Decision is separately trusted caller input, NEVER source text.

    reviewed_by supplies provenance, not authorization. Caller must establish
    permission and review the exact input/row fingerprint before providing it.
    """
    try:
        key, indexed = source_key(handoff), _index(rows)
        if not isinstance(decision, dict) or not _text(decision.get("reviewed_by")):
            return Outcome("needs_review", "missing_reviewed_decision")
        input_hash = fingerprint(handoff)
        if decision.get("input_hash") != input_hash:
            return Outcome("needs_review", "handoff_changed_since_review")
        if decision.get("action") not in ("create", "update"):
            return Outcome("needs_review", "unsupported_decision")
        task_id = decision.get("task_id")
        if not _text(task_id) or task_id != task_id.strip() or task_id.startswith("task_row_"):
            return Outcome("needs_review", "missing_stable_task_id")
        changes = decision.get("changes")
        if not isinstance(changes, dict) or not changes or not set(changes).issubset(EDITABLE):
            return Outcome("needs_review", "invalid_change_fields")
        if any(not isinstance(v, str) for v in changes.values()):
            return Outcome("needs_review", "nontext_change")
        current = indexed.get(task_id)
        if decision["action"] == "create":
            if current is not None:
                return Outcome("needs_review", "task_id_already_exists")
            if decision.get("expected_before") is not None:
                return Outcome("needs_review", "unexpected_create_version")
            after = dict.fromkeys(HEADERS, "")
            after[TASK_ID], before = task_id, None
        else:
            if current is None:
                return Outcome("needs_review", "task_id_not_found")
            before = fingerprint(current)
            if decision.get("expected_before") != before:
                return Outcome("conflict", "registry_changed_since_review")
            after = dict(current)
            for field in ("Остання відповідь / доказ", "Джерело / чат"):
                if field in changes and current[field] and not changes[field].startswith(current[field]):
                    return Outcome("needs_review", "evidence_history_would_be_replaced")
        after.update(changes)
        if not all(_text(after[h]) for h in ("Назва", "Завдання / очікуваний результат",
                                            "Остання відповідь / доказ", "Джерело / чат")):
            return Outcome("needs_review", "missing_task_or_evidence")
        plan = Plan(key, input_hash, fingerprint(decision), decision["action"], task_id,
                    before, tuple((h, after[h]) for h in HEADERS))
        return Outcome("ready", "reviewed_plan", plan)
    except (ValueError, TypeError, OverflowError):
        return Outcome("needs_review", "invalid_input_or_registry")


def same_row(left: dict | None, right: dict | None) -> bool:
    """JSON scalar types matter: bool/int/float equality is not row identity."""
    return left is not None and right is not None and fingerprint(left) == fingerprint(right)


def _check_receipt(plan: Plan, rows: list[dict], receipt: dict | None) -> bool:
    return receipt == plan.receipt and same_row(_index(rows).get(plan.task_id), plan.row)


def apply_plan(plan: Plan, store: ConditionalStore, *, execute: bool = False) -> Outcome:
    """Dry-run by default. No blind retry after ambiguous write. No checkpoint I/O."""
    if execute is not True:
        return Outcome("dry_run", "no_write_requested", plan)
    try:
        rows, receipt = store.read(plan.key)
        if receipt is not None:
            if _check_receipt(plan, rows, receipt):
                return Outcome("already_applied", "exact_receipt_and_row", plan, True)
            return Outcome("conflict", "receipt_or_row_mismatch", plan)
        existing = _index(rows).get(plan.task_id)
        expected = fingerprint(existing) if existing is not None else None
        if expected != plan.before_hash and not same_row(existing, plan.row):
            return Outcome("conflict", "registry_changed_before_commit", plan)
    except Exception:
        return Outcome("failed", "preread_failed", plan)
    try:
        store.commit(plan)
    except Exception:
        try:
            rows, receipt = store.read(plan.key)
            if _check_receipt(plan, rows, receipt):
                return Outcome("applied", "verified_after_uncertain_commit", plan, True)
        except Exception:
            pass
        return Outcome("uncertain", "commit_outcome_unconfirmed", plan)
    try:
        rows, receipt = store.read(plan.key)
        if _check_receipt(plan, rows, receipt):
            return Outcome("applied", "exact_readback", plan, True)
        return Outcome("uncertain", "readback_mismatch", plan)
    except Exception:
        return Outcome("uncertain", "readback_failed", plan)


def window_ready(outcomes: list[Outcome], *, sources_complete: bool,
                 expected_keys: set[str]) -> bool:
    if (sources_complete is not True or not isinstance(expected_keys, set)
            or not expected_keys or any(not _text(k) for k in expected_keys)
            or len(outcomes) != len(expected_keys)):
        return False
    if not all(isinstance(o, Outcome) and o.plan is not None and o.checkpoint_eligible
               and o.status in ("applied", "already_applied") for o in outcomes):
        return False
    return {o.plan.key for o in outcomes} == expected_keys
