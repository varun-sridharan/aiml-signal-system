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

import json
import os
import re
import sys
import time

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

# The most output a model may be asked for (a request above it is a 400). Read from the API's own error on 2026-11-08; Haiku 4.5's is
# its published limit. A max_tokens retry doubles the cap up to this and no further.
MODEL_MAX_OUTPUT = {"claude-opus-5": 128_000, "claude-haiku-4-5": 64_000}

# Overload, a 5xx or a timeout: at most 3 tries, 5 s then 20 s apart, and none that would start after 2 minutes (Run 10, step 3d).
TRANSIENT_TRIES = 3
TRANSIENT_BACKOFF_S = (5, 20)
TRANSIENT_WINDOW_S = 120
LOG_HEAD_CHARS = 500
STREAM_ABOVE = 20_000                # a request asking for more than this is streamed
_sleep = time.sleep                  # tests replace these two, so a backoff takes no real time
_monotonic = time.monotonic


class ModelCallError(RuntimeError):
    """A model call that failed for a named reason. `reason` is the short name a log line carries; `stop_reason` is the API's;
    `reply` is the first LOG_HEAD_CHARS of what came back (secrets removed); `cost` is what the failed attempts cost."""
    reason = "model_error"

    def __init__(self, message: str, *, stop_reason=None, reply: str = "", request_id=None, cost: float = 0.0):
        super().__init__(message)
        self.stop_reason, self.reply, self.request_id, self.cost = stop_reason, log_head(reply), request_id, cost

    def log_detail(self) -> str:
        parts = [f"reason={self.reason}", f"stop_reason={self.stop_reason}"]
        if self.request_id:
            parts.append(f"request={self.request_id}")
        if self.reply:
            parts.append(f"reply: {self.reply!r}")
        return "; ".join(parts)


class ModelRefusal(ModelCallError):
    reason = "refusal"


class ModelMaxTokens(ModelCallError):
    reason = "max_tokens"


class ModelEmptyReply(ModelCallError):
    reason = "empty_reply"


class ModelMalformedReply(ModelCallError):
    reason = "malformed_reply"


class ModelUnavailable(ModelCallError):
    """Overloaded, rate-limited, a 5xx or a timeout, on every try the policy allows."""
    reason = "unavailable"


class ModelRejected(ModelCallError):
    """The API refused the request itself (a 4xx other than rate limiting): it was not run and not billed, and retrying cannot help."""
    reason = "rejected"


_SECRET = re.compile(r"Bearer\s+\S+|sk-ant-[\w-]+|postgres(?:ql)?://\S+|(?:API_KEY|DATABASE_URL|TOKEN|SECRET|PASSWORD)\s*[=:]\s*\S+", re.I)


def log_head(text, limit: int = LOG_HEAD_CHARS) -> str:
    """The first `limit` characters of a reply for a log line, on one line, with anything that looks like a credential removed."""
    flat = " ".join(str(text or "").split())
    return _SECRET.sub("[removed]", flat)[:limit]


def retry_phase(phase: str) -> str:
    """The ledger phase a retry is booked under: `composing` retries as `composing-retry`; a retry of a retry stays there."""
    return phase if phase.endswith("-retry") else phase + "-retry"


def build_client() -> anthropic.Anthropic:
    """Load the key and construct the client, in one place rather than per agent."""
    # WHAT: .env loading and the missing-key exit live here, not in each agent.
    # CONCEPT: harness — one seam. Reader is the second caller; without this it would
    # have been the second copy of the same four lines, free to drift from the first.
    load_dotenv(budget.REPO / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env (see prompts/p2-build-framer.md).")
    # max_retries=0: the SDK would retry on its own, unbooked and unseen. The retry policy below is the only one, so every retry is booked.
    return anthropic.Anthropic(max_retries=0)


def reply_text(message) -> str:
    """The reply's text. A reply with no text block, or only blank text, is an empty reply with its own name, never a StopIteration."""
    text = "".join(getattr(block, "text", "") or "" for block in message.content if getattr(block, "type", None) == "text")
    if not text.strip():
        raise ModelEmptyReply("the model's reply has no text", stop_reason=message.stop_reason, request_id=getattr(message, "_request_id", None))
    return text


def _head_of(message) -> str:
    return "".join(getattr(block, "text", "") or "" for block in message.content if getattr(block, "type", None) == "text")


def _stop_error(cls, message, text: str, cost: float):
    return cls(text, stop_reason=message.stop_reason, reply=_head_of(message), request_id=getattr(message, "_request_id", None), cost=cost)


def structured_text(message) -> str:
    """Return the response text, refusing to guess when the model didn't finish cleanly."""
    # CONCEPT: harness — fail loudly, with a name. A refusal has empty content and a truncated response has invalid JSON; both abort
    # (as ModelRefusal and ModelMaxTokens) rather than emit a partial brief.
    if message.stop_reason == "refusal":
        raise _stop_error(ModelRefusal, message, f"Model declined the request ({getattr(message, 'stop_details', None)}). Nothing written.", 0.0)
    if message.stop_reason == "max_tokens":
        raise _stop_error(ModelMaxTokens, message, "Response hit max_tokens and the JSON is truncated.", 0.0)
    return reply_text(message)


def schema_problems(value, schema: dict, path: str = "$") -> list:
    """What is wrong with a reply against the JSON schema it was requested under, as short sentences. Empty means it conforms.
    Covers the subset the repo's schemas use: type, enum, required, properties, items, anyOf, minItems, minLength. The API's own
    structured output guarantees the keys; this is what makes `minItems` and `minLength` bind, whatever the API does with them."""
    if "anyOf" in schema:
        trials = [schema_problems(value, option, path) for option in schema["anyOf"]]
        return [] if any(not t for t in trials) else min(trials, key=len)
    kind = schema.get("type")
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None), "integer": int, "number": (int, float)}
    if kind in kinds and not (isinstance(value, kinds[kind]) and not (kind in ("integer", "number") and isinstance(value, bool))):
        return [f"{path}: expected {kind}, got {type(value).__name__}"]
    out = []
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if kind == "string" and len(value) < schema.get("minLength", 0):
        out.append(f"{path}: empty string, at least {schema['minLength']} character required")
    if kind == "array":
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path}: {len(value)} items, at least {schema['minItems']} required")
        for n, item in enumerate(value[:200]):
            out += schema_problems(item, schema.get("items", {}), f"{path}[{n}]")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}.{key}: required and missing")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                out += schema_problems(value[key], sub, f"{path}.{key}")
    return out[:8]


def _status_of(error) -> int:
    return getattr(error, "status_code", 0) or 0


def _close_unknown(conn, reservation, model, request, extra_input_tokens, kind):
    """A request that may or may not have billed: its reservation is closed at the input cost, never left open at the worst case."""
    budget.settle_estimated(conn, reservation, budget.estimate_input_only_usd(model, request, extra_input_tokens), f"{kind}; output unknown")


def _request(client, conn, *, model, max_tokens, user_id, phase, stream, extra_input_tokens, request):
    """One booked request. Reserves the worst case, calls, settles with the API's stop_reason and request id. Overload (429, 529), a 5xx,
    a dropped connection and a timeout are tried up to TRANSIENT_TRIES times with a backoff, each try booked (the second and third under
    the retry phase). A 4xx is the API refusing the request: settled at zero (it ran nothing) and raised as ModelRejected.
    Returns (message, cost)."""
    started, kind, last = _monotonic(), "unavailable", None
    for attempt in range(TRANSIENT_TRIES):
        booked_as = phase if attempt == 0 else retry_phase(phase)
        # WHAT: reserve the worst case before spending, every time, with no opt-out.
        # CONCEPT: loop — circuit breaker. The check and the booking are one locked transaction.
        estimate = budget.estimate_call_ceiling_usd(model, max_tokens, request, extra_input_tokens)
        reservation = budget.reserve(conn, user_id=user_id, phase=booked_as, model=model, estimate_usd=estimate)
        try:
            if stream:
                with client.messages.stream(model=model, max_tokens=max_tokens, **request) as s:
                    message = s.get_final_message()
                    # a streamed reply does not carry its request id on the message; the stream does (2026-11-09: the ledger column was empty for the composer and framer)
                    if getattr(s, "request_id", None) and not getattr(message, "_request_id", None):
                        message._request_id = s.request_id
            else:
                message = client.messages.create(model=model, max_tokens=max_tokens, **request)
        except anthropic.APIStatusError as e:
            code, rid = _status_of(e), getattr(e, "request_id", None)
            if code in (429, 529):
                budget.settle_unbilled(conn, reservation, f"error {code}", request_id=rid)
                kind, last = ("rate limited" if code == 429 else "overloaded"), e
            elif 400 <= code < 500:
                budget.settle_unbilled(conn, reservation, f"rejected {code}", request_id=rid)
                raise ModelRejected(f"the API rejected the request ({code}): {str(e)[:300]}", stop_reason=f"rejected {code}", request_id=rid) from e
            elif code >= 500:        # a 5xx is an error the API did not bill: settled at zero, so it leaves nothing in the month
                budget.settle_unbilled(conn, reservation, f"error {code}", request_id=rid)
                kind, last = f"server error {code}", e
            else:      # an error event inside a stream (status 200): it may have billed; closed at its input cost, output unknown
                kind, last = "server error in the stream", e
                _close_unknown(conn, reservation, model, request, extra_input_tokens, kind)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            kind, last = ("timed out" if isinstance(e, anthropic.APITimeoutError) else "connection failed"), e
            _close_unknown(conn, reservation, model, request, extra_input_tokens, kind)
        except (ValueError, TypeError):       # the SDK refusing a parameter or a bad argument: raised before the request leaves, so nothing was billed
            budget.settle_unbilled(conn, reservation, "client error before send")
            raise
        except Exception:       # anything else may have come after the API billed (a reply that would not parse): closed at the input cost, output unknown
            _close_unknown(conn, reservation, model, request, extra_input_tokens, "unexpected error")
            raise
        else:
            cost = budget.settle(conn, reservation, model, message.usage, stop_reason=message.stop_reason, request_id=getattr(message, "_request_id", None))
            return message, float(cost)
        if attempt + 1 >= TRANSIENT_TRIES:
            break
        delay = TRANSIENT_BACKOFF_S[min(attempt, len(TRANSIENT_BACKOFF_S) - 1)]
        if _monotonic() - started + delay > TRANSIENT_WINDOW_S:
            break
        _sleep(delay)
    raise ModelUnavailable(f"the model call failed after {attempt + 1} tries: {kind} ({type(last).__name__})", stop_reason=kind) from last


def _stop_policy(client, conn, message, cost, *, model, max_tokens, user_id, phase, stream, extra_input_tokens, request):
    """`refusal`: one retry, and a second refusal is ModelRefusal. `max_tokens`: one retry with the cap doubled up to the model's limit,
    and a second truncation (or no room to double) is ModelMaxTokens. Each retry is booked under the retry phase."""
    again = dict(client=client, conn=conn, model=model, user_id=user_id, phase=retry_phase(phase), stream=stream,
                 extra_input_tokens=extra_input_tokens, request=request)
    again_big = {**again, "stream": True}      # the SDK refuses a non-streamed request above ~21,000 max_tokens (a bare ValueError)
    if message.stop_reason == "refusal":
        message, extra = _request(max_tokens=max_tokens, **again)
        cost += extra
        if message.stop_reason == "refusal":
            raise _stop_error(ModelRefusal, message, f"the model refused twice ({getattr(message, 'stop_details', None)})", cost)
    if message.stop_reason == "max_tokens":
        room = MODEL_MAX_OUTPUT.get(model, max_tokens)
        doubled = min(max_tokens * 2, room)
        if doubled <= max_tokens:
            raise _stop_error(ModelMaxTokens, message, f"the reply hit max_tokens ({max_tokens}), which is the most {model} allows", cost)
        message, extra = _request(max_tokens=doubled, **(again_big if doubled > STREAM_ABOVE else again))
        message._max_tokens_used = doubled      # a later structural retry keeps this cap
        cost += extra
        if message.stop_reason == "max_tokens":
            raise _stop_error(ModelMaxTokens, message, f"the reply hit max_tokens again at {doubled}", cost)
        if message.stop_reason == "refusal":
            raise _stop_error(ModelRefusal, message, "the model refused the retry", cost)
    return message, cost


def call_message(
    client,
    *,
    model: str,
    max_tokens: int,
    user_id: str,
    phase: str,
    stream: bool = False,
    conn=None,
    extra_input_tokens: int = 0,
    stop_policy: bool = False,
    **request,
):
    """Reserve, call, settle. Returns (message, usage, cost), without reading the content.

    For callers that need the whole response rather than its text: Reader's fetch driver reads the page out of a server tool's result
    block, and the cited writer reads citation blocks. Everything call() guarantees about booking holds here. `stop_policy=True` adds
    the refusal and max_tokens handling call() always applies; the cost is the sum of every attempt.
    """
    # WHAT: no ledger, no call. budget.connect() raises LedgerUnavailable rather than falling back.
    own = conn is None
    if own:
        conn = budget.connect()
    try:
        message, cost = _request(client, conn, model=model, max_tokens=max_tokens, user_id=user_id, phase=phase, stream=stream,
                                 extra_input_tokens=extra_input_tokens, request=request)
        if stop_policy and message.stop_reason in ("refusal", "max_tokens"):
            message, cost = _stop_policy(client, conn, message, cost, model=model, max_tokens=max_tokens, user_id=user_id, phase=phase,
                                         stream=stream, extra_input_tokens=extra_input_tokens, request=request)
    finally:
        if own:
            conn.close()
    return message, message.usage, cost


def _structural_problem(text: str, request: dict, validate) -> str:
    """Why a reply is not usable, or ''. A reply asked for under a JSON schema must be JSON that conforms to it; `validate(parsed)` may add more."""
    schema = ((request.get("output_config") or {}).get("format") or {}).get("schema")
    if schema is None and validate is None:
        return ""
    try:
        parsed = json.loads(text) if schema is not None else text
    except ValueError as e:
        return f"not valid JSON ({e})"
    problems = schema_problems(parsed, schema) if schema is not None else []
    if not problems and validate is not None:
        found = validate(parsed)
        problems = [found] if found else []
    return "; ".join(problems[:3])


_BOUNDS = ("minItems", "minLength")


def _without_bounds(schema):
    """The schema with its minItems and minLength removed, for an API that refuses them. The reply is still checked against the original."""
    if isinstance(schema, dict):
        return {k: _without_bounds(v) for k, v in schema.items() if k not in _BOUNDS}
    if isinstance(schema, list):
        return [_without_bounds(v) for v in schema]
    return schema


def _relaxed(request: dict):
    """The request with its output schema's bounds removed, or None when it has no bounds to remove."""
    fmt = (request.get("output_config") or {}).get("format") or {}
    schema = fmt.get("schema")
    if schema is None or _without_bounds(schema) == schema:
        return None
    return {**request, "output_config": {**request["output_config"], "format": {**fmt, "schema": _without_bounds(schema)}}}


def call(client, *, model: str, max_tokens: int, user_id: str, phase: str, stream: bool = False, conn=None, validate=None, **request):
    """Reserve, call, settle, validate. Returns (text, usage, cost).

    Every call is booked, exactly once per attempt, under the caller's `phase` and `user_id`; both are required. `conn` is for tests, which
    pass a connection inside a transaction they roll back; everyone else lets this open its own.

    A reply that is empty, not valid JSON, or does not conform to its own schema (including its minItems and minLength) is asked for once
    more, booked under the retry phase; a second one raises ModelEmptyReply or ModelMalformedReply with the API's stop_reason and the first
    500 characters of what came back. Refusal and max_tokens follow `_stop_policy`. `validate(parsed)` returns a reason or ''.
    If the API itself rejects the schema's bounds (a 400 that names the schema, output_config, minItems or minLength; not billed), the call is repeated once without
    them and the reply is still checked against the full schema here.
    """
    args = dict(model=model, max_tokens=max_tokens, user_id=user_id, stream=stream, conn=conn, stop_policy=True)
    sent = [request]

    def send(booked_as: str):
        try:
            return call_message(client, phase=booked_as, **args, **sent[0])
        except ModelRejected as e:
            relaxed = _relaxed(sent[0])
            if relaxed is None or not any(w in str(e) for w in ("output_config", "schema", "minItems", "minLength")):
                raise
            sent[0] = relaxed
            return call_message(client, phase=retry_phase(booked_as), **args, **relaxed)

    message, usage, cost = send(phase)
    for attempt in (1, 2):
        try:
            text = reply_text(message)
            problem = _structural_problem(text, request, validate)
            if not problem:
                return text, usage, cost
            error = ModelMalformedReply(f"the reply does not conform: {problem}", stop_reason=message.stop_reason, reply=text,
                                        request_id=getattr(message, "_request_id", None))
        except ModelEmptyReply as e:
            error = e
        if attempt == 2:
            error.cost = cost
            raise error
        args["max_tokens"] = getattr(message, "_max_tokens_used", args["max_tokens"])
        message, usage, extra = send(retry_phase(phase))
        cost += extra
