"""The guarded model call: pinned models, one client, and no way to skip the breaker.

Extracted from framer.py at M1 alongside budget.py, when Reader became the second
caller. The single entry point is call(), which reserves the call's worst case in the
ledger (refusing it if that would cross the ceiling), makes the request, settles the
reservation at the actual cost, and only then refuses a truncated or declined response.
A caller cannot spend without being checked or without being booked, because neither
is a separate step it could forget to take.

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
    user_id: str,
    phase: str,
    stream: bool = False,
    conn=None,
    **request,
):
    """Reserve, call, settle, validate. Returns (text, usage, cost).

    Every call is booked, exactly once, under the caller's `phase` and `user_id`; both
    are required. `conn` is for tests, which pass a connection inside a transaction they
    roll back; everyone else lets this open its own.
    """
    # WHAT: no ledger, no call. budget.connect() raises LedgerUnavailable rather than
    # falling back, and the request below is never reached.
    own = conn is None
    if own:
        conn = budget.connect()
    try:
        # WHAT: reserve the worst case before spending, every time, with no opt-out.
        # CONCEPT: loop — circuit breaker. The check and the booking are one locked
        # transaction, so an unrecorded call can no longer quietly raise the ceiling.
        estimate = budget.estimate_call_ceiling_usd(model, max_tokens, request)
        reservation = budget.reserve(conn, user_id=user_id, phase=phase, model=model, estimate_usd=estimate)

        # If the request raises, the reservation stays at its worst case: the call may
        # still have billed, and over-counting is the safe direction.
        if stream:
            with client.messages.stream(model=model, max_tokens=max_tokens, **request) as s:
                message = s.get_final_message()
        else:
            message = client.messages.create(model=model, max_tokens=max_tokens, **request)

        # WHAT: settle BEFORE validating. A truncated or declined response was still
        # billed; validating first would exit with the spend unrecorded.
        cost = budget.settle(conn, reservation, model, message.usage)
    finally:
        if own:
            conn.close()

    text = structured_text(message)
    return text, message.usage, float(cost)
