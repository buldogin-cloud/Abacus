"""Privacy-safe AI usage accounting. No prompts or user content are logged.

Use record_usage only for a real provider response with known token usage.
Unknown token counts and prices stay null; never record invented zeros.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def estimated_usd(input_tokens, output_tokens, input_usd_per_m, output_usd_per_m):
    """Return estimated API charge, or None if any quantity/rate is unknown."""
    vals = (input_tokens, output_tokens, input_usd_per_m, output_usd_per_m)
    if any(x is None for x in vals):
        return None
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("tokens must be non-negative")
    a, b = Decimal(str(input_usd_per_m)), Decimal(str(output_usd_per_m))
    if a < 0 or b < 0:
        raise ValueError("rates must be non-negative")
    return str((Decimal(input_tokens) * a + Decimal(output_tokens) * b) / 1000000)

def record_usage(path, *, run_id, task_id, provider, model, status,
                 input_tokens=None, output_tokens=None, price_input_per_m=None,
                 price_output_per_m=None, price_source=None, currency="USD",
                 request_id=None, duration_ms=None):
    """Append a minimal one-request JSONL event. Caller controls path.

    Do not use this for ChatGPT subscription / Tasks billing: those are
    separate products unless measured by their own authoritative usage API.
    """
    if not all((run_id, task_id, provider, model, status)):
        raise ValueError("required run/task/provider/model/status")
    if (input_tokens is None) != (output_tokens is None):
        raise ValueError("both token quantities required together")
    event = {
        "schema_version": 1, "event_type": "model_usage",
        "occurred_at": _utc(), "run_id": run_id, "task_id": task_id,
        "provider": provider, "model": model, "status": status,
        "request_id": request_id,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "api_cost_estimate": estimated_usd(input_tokens, output_tokens,
                                             price_input_per_m, price_output_per_m),
        "currency": currency, "price_source": price_source,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Protect local log from other local users. Do not write to public git.
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        out.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event

def summarize(path):
    """Aggregate only counted model usage. Missing usage stays visibly unknown."""
    events = []
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    estimated = sum((Decimal(e["api_cost_estimate"]) for e in events
                     if e.get("api_cost_estimate") is not None), Decimal("0"))
    return {"events": len(events),
            "priced_events": sum(e.get("api_cost_estimate") is not None for e in events),
            "unpriced_events": sum(e.get("api_cost_estimate") is None for e in events),
            "priced_cost_usd": str(estimated),
            "total_cost_usd": str(estimated) if all(
                e.get("api_cost_estimate") is not None for e in events) else None}
