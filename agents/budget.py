"""Cost ledger and the spend circuit-breaker.

Extracted from framer.py at M1, when Reader became the second caller. Two callers is
the point at which the shape is knowable; one caller would have been a guess. Nothing
here is generalised past what the Framer and Reader both actually need — there is no
per-agent budget, no rate limiting and no async, because neither caller asks for them.

The ledger is append-only and lives in data/state/usage.json. Every call records its
tokens and estimated cost, and the breaker reads that file back on the next run.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# WHAT: hard monthly ceiling; a run aborts before spending past it.
# CONCEPT: loop — cost circuit-breaker guardrail.
MONTHLY_BUDGET_USD = 20.00

# WHAT: USD per million tokens, per model. Cache reads bill at ~0.1x input, cache
# writes at ~1.25x, so they are priced separately rather than folded into input.
# CONCEPT: eval — cost telemetry the Control Room can chart later.
PRICING_PER_MTOK = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

USAGE_PATH = REPO / "data" / "state" / "usage.json"


def estimate_cost_usd(model: str, usage) -> float:
    price = PRICING_PER_MTOK[model]
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * price["input"]
        + cache_read * price["input"] * CACHE_READ_MULTIPLIER
        + cache_write * price["input"] * CACHE_WRITE_MULTIPLIER
        + usage.output_tokens * price["output"]
    ) / 1_000_000


def load_usage_log() -> dict:
    if USAGE_PATH.exists():
        return json.loads(USAGE_PATH.read_text())
    return {
        "note": "Append-only API spend log. Every call records tokens + estimated cost so "
        "the Control Room can chart cost per brief against MONTHLY_BUDGET_USD.",
        "calls": [],
    }


def month_to_date_spend(log: dict, month: str) -> float:
    return sum(c["estimated_cost_usd"] for c in log["calls"] if c["timestamp"].startswith(month))


def estimate_call_ceiling_usd(model: str, max_tokens: int) -> float:
    """Upper bound on what one call can cost, before it is made."""
    # WHAT: price max_tokens as though every one of them comes back as output, and
    # charge the same count again at the input rate.
    # CONCEPT: loop — a pre-flight estimate has to be an over-estimate to be safe. The
    # real input size is not known here and output is capped by max_tokens, so pricing
    # a full cap of output plus an input allowance of the same size is deliberately
    # pessimistic. A breaker that under-estimates is a breaker that lets the run start.
    price = PRICING_PER_MTOK[model]
    return (max_tokens * price["output"] + max_tokens * price["input"]) / 1_000_000


def assert_within_budget(model: str, max_tokens: int, log: dict, month: str) -> float:
    """Refuse the call if its worst case would push month-to-date past the ceiling.

    Returns month-to-date spend so the caller can report it. Raises RuntimeError when
    the call would breach the ceiling.
    """
    # WHAT: stop BEFORE the spend, not after it.
    # CONCEPT: loop — the old check refused only once spend had already passed the
    # ceiling, which means the call that crossed the line was always allowed through.
    # A breaker that trips after the fact is a report, not a breaker.
    spent = month_to_date_spend(log, month)
    estimate = estimate_call_ceiling_usd(model, max_tokens)
    if spent + estimate > MONTHLY_BUDGET_USD:
        raise RuntimeError(
            f"Refusing to call {model}: month-to-date spend ${spent:.2f} plus this "
            f"call's worst case ${estimate:.2f} would exceed MONTHLY_BUDGET_USD "
            f"(${MONTHLY_BUDGET_USD:.2f}). Raise the ceiling in agents/budget.py or "
            "wait for the month to roll over. No API call made."
        )
    return spent


def record_call(log: dict, user_id: str, phase: str, model: str, usage, timestamp: str) -> float:
    """Append one call's tokens and estimated cost to the usage log."""
    # WHAT: log every call's tokens and cost, keyed by user.
    # CONCEPT: eval — the sensor the cost circuit-breaker reads on the next run.
    cost = estimate_cost_usd(model, usage)
    log["calls"].append(
        {
            "user_id": user_id,
            "timestamp": timestamp,
            "phase": phase,
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "estimated_cost_usd": round(cost, 6),
        }
    )
    return cost


def save_usage_log(log: dict) -> None:
    USAGE_PATH.write_text(json.dumps(log, indent=2) + "\n")
