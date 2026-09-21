"""The guarded model call: pinned models, one client, and no way to skip the breaker.

Extracted from framer.py at M1 alongside budget.py, when Reader became the second
caller. The single entry point is call(), which does the pre-flight budget check,
makes the request, refuses a truncated or declined response, and appends to the
ledger. A caller that uses this module cannot spend without checking first, because
the check is not a separate step it could forget to take.

Deliberately not generalised: two call shapes exist because the two real callers need
two — one streamed with a cache breakpoint and a thinking budget, one plain create at
temperature zero — and `stream` is the only axis between them. Everything else is
passed straight through to the SDK rather than wrapped in parameters nobody asked for.
"""

from __future__ import annotations

import os
import sys

import anthropic
from dotenv import load_dotenv

# WHAT: resolve the sibling module under both import styles.
# CONCEPT: harness — data/evals/run_evals.py puts agents/ on sys.path and does
# `import framer`, so these modules load as top-level names there and as
# `agents.llm` everywhere else. A bare relative import works only in the second
# case, and the failure would surface on an API path the offline gate never runs.
try:
    from . import budget
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import budget

# WHAT: pin every model and cost knob in one place.
# CONCEPT: harness — model routing. The expensive model frames; a cheap one checks.
# Pinning (not "latest") is what makes a prompt file a reproducible recipe.
FRAMER_MODEL = "claude-opus-5"
CHECK_MODEL = "claude-haiku-4-5"
FRAMER_MAX_TOKENS = 24_000
# The grader enumerates every claim with a quoted span before it judges, so its
# response is several times longer than the old bare verdict list. Truncation here
# aborts the run (see structured_text), so the cap has room to spare.
CHECK_MAX_TOKENS = 16_000
FRAMER_EFFORT = "high"


def build_client() -> anthropic.Anthropic:
    """Load the key and construct the client, in one place rather than per agent."""
    # WHAT: .env loading and the missing-key exit live here, not in each agent.
    # CONCEPT: harness — one seam. Reader is the second caller; without this it would
    # have been the second copy of the same four lines, free to drift from the first.
    load_dotenv(budget.REPO / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env (see prompts/p2-build-framer.md).")
    return anthropic.Anthropic()


def structured_text(message) -> str:
    """Return the response text, refusing to guess when the model didn't finish cleanly."""
    # WHAT: check stop_reason before touching content.
    # CONCEPT: harness — fail loudly. A refusal has empty content and a truncated
    # response has invalid JSON; both must abort rather than emit a partial brief.
    if message.stop_reason == "refusal":
        sys.exit(f"Model declined the request ({message.stop_details}). Nothing written.")
    if message.stop_reason == "max_tokens":
        sys.exit("Response hit max_tokens and the JSON is truncated. Raise the cap and rerun.")
    return next(block.text for block in message.content if block.type == "text")


def call(
    client,
    *,
    model: str,
    max_tokens: int,
    timestamp: str,
    stream: bool = False,
    ledger: dict | None = None,
    user_id: str | None = None,
    phase: str | None = None,
    **request,
):
    """Budget-check, call, validate, and record. Returns (text, usage, cost).

    The pre-flight check always runs. Recording happens when `ledger` is supplied; the
    eval harness passes none because it books the same call under its own phase label
    against its own log, and double-booking one call would corrupt the ledger it reads
    back next run. `cost` is None when nothing was recorded.
    """
    month = timestamp[:7]
    # WHAT: check before spending, every time, with no opt-out.
    # CONCEPT: loop — circuit breaker. Reading the log here rather than taking it as a
    # required argument is what lets a caller with its own bookkeeping still be gated.
    log_for_check = ledger if ledger is not None else budget.load_usage_log()
    budget.assert_within_budget(model, max_tokens, log_for_check, month)

    if stream:
        with client.messages.stream(model=model, max_tokens=max_tokens, **request) as s:
            message = s.get_final_message()
    else:
        message = client.messages.create(model=model, max_tokens=max_tokens, **request)

    text = structured_text(message)
    cost = None
    if ledger is not None:
        cost = budget.record_call(ledger, user_id, phase, model, message.usage, timestamp)
    return text, message.usage, cost
