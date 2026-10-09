"""Cost ledger and the spend circuit-breaker.

Extracted from framer.py at M1, when Reader became the second caller. Two callers is
the point at which the shape is knowable; one caller would have been a guess. Nothing
here is generalised past what the Framer and Reader both actually need — there is no
per-agent budget, no rate limiting and no async, because neither caller asks for them.

The ledger is the `spend_ledger` table in Railway Postgres (agents/schema.sql). It used
to be data/state/usage.json, a file in the repository, and Railway rebuilds the repository
on every deploy: on the live site the breaker forgot the month's spend after each deploy
and kept approving calls. A ledger has to outlive the process that writes it.

Every call is booked in two steps. reserve() inserts a 'reserved' row at the call's
worst-case cost, inside the same transaction as the ceiling check and under an advisory
lock, so two runs cannot both pass on the same headroom. settle() rewrites that row to
the actual cost once the response is back.

The ledger fails closed. With no DATABASE_URL, or no reachable database, no paid call is
made. There is deliberately no fallback to a local file: two ledgers is the bug.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# WHAT: hard monthly ceiling; a call is refused before it can spend past it.
# CONCEPT: loop — cost circuit-breaker guardrail.
MONTHLY_BUDGET_USD = Decimal("20.00")

# WHAT: USD per million tokens, per model. Cache reads bill at ~0.1x input, cache
# writes at ~1.25x, so they are priced separately rather than folded into input.
# CONCEPT: eval — cost telemetry the Control Room can chart later.
PRICING_PER_MTOK = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

# WHAT: the pre-flight estimate's assumption about how densely text tokenises.
# CONCEPT: loop — conservative by construction. English prose runs nearer four
# characters a token; assuming three over-counts the input, which is the safe side.
CHARS_PER_TOKEN_ESTIMATE = 3

# Advisory lock keys. Arbitrary but fixed: every process that reserves spend takes the
# same lock, so the check-then-insert is serial across runs, machines and deploys.
RESERVE_LOCK_KEY = 0x5E1D_1ED6  # "spend ledger"
SCHEMA_LOCK_KEY = 0x5E1D_5C4E

TUNNEL_HINT = "start the tunnel: railway connect Postgres --tunnel-only"

MICRO = Decimal("0.000001")


class LedgerUnavailable(RuntimeError):
    """No ledger, so no spend. Raised instead of falling back to anything local."""


class BudgetExceeded(RuntimeError):
    """The call's worst case would push month-to-date past MONTHLY_BUDGET_USD."""


# --------------------------------------------------------------------------------------
# Estimates
# --------------------------------------------------------------------------------------

def _usd(value) -> Decimal:
    return Decimal(str(value)).quantize(MICRO, rounding=ROUND_HALF_UP)


def estimate_cost_usd(model: str, usage) -> float:
    """The actual cost of a finished call, from the usage the API reported."""
    price = PRICING_PER_MTOK[model]
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * price["input"]
        + cache_read * price["input"] * CACHE_READ_MULTIPLIER
        + cache_write * price["input"] * CACHE_WRITE_MULTIPLIER
        + usage.output_tokens * price["output"]
    ) / 1_000_000


def estimate_input_tokens(request: dict) -> int:
    """Upper-bound the input tokens of a request from its size, offline."""
    # WHAT: serialise everything that is sent as input and divide by three.
    # CONCEPT: loop — no token-counting API call, because counting would itself be a
    # request. JSON escaping only adds characters, so serialising errs high too.
    chars = len(json.dumps(request, ensure_ascii=False, default=str))
    return math.ceil(chars / CHARS_PER_TOKEN_ESTIMATE)


def estimate_call_ceiling_usd(model: str, max_tokens: int, request: dict) -> float:
    """Worst-case cost of one call, before it is made."""
    # WHAT: estimated input priced at the cache-WRITE rate, plus max_tokens of output.
    # CONCEPT: loop — the reservation must never come in under the real cost, or the
    # ceiling is soft. Output (thinking included) is capped by max_tokens. Input is
    # priced at 1.25x because a cache write is the dearest way an input token can bill;
    # the old estimate charged max_tokens as input, which a large payload could exceed.
    price = PRICING_PER_MTOK[model]
    input_tokens = estimate_input_tokens(request)
    return (
        input_tokens * price["input"] * CACHE_WRITE_MULTIPLIER
        + max_tokens * price["output"]
    ) / 1_000_000


# --------------------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------------------

def _is_loopback(host: str | None) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


_schema_ready = False


def connect(*, autocommit: bool = True, connect_timeout: int = 5):
    """Open a connection to the ledger, or raise LedgerUnavailable. Never falls back."""
    import psycopg

    load_dotenv(REPO / ".env")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise LedgerUnavailable(
            "DATABASE_URL is not set, so there is no spend ledger and no paid call is "
            "made. Locally, put the tunnel's URL in .env (see README); on Railway the "
            "app service references the Postgres service's DATABASE_URL."
        )

    parts = urlsplit(url)
    kwargs = {"connect_timeout": connect_timeout, "autocommit": autocommit}
    # WHAT: over the local tunnel, require TLS but do not verify the certificate.
    # CONCEPT: the tunnel already rides SSH; the server's certificate is self-signed and
    # issued for its own name, not 127.0.0.1, so verify-full cannot pass locally. Only a
    # loopback URL is touched, so Railway's own private connection keeps its defaults.
    if _is_loopback(parts.hostname) and "sslmode=" not in (parts.query or ""):
        kwargs["sslmode"] = "require"

    try:
        conn = psycopg.connect(url, **kwargs)
    except psycopg.OperationalError:
        # The raw error is not shown: it can quote the connection target, and it does
        # not tell anyone what to do. Neither message includes the URL.
        if _is_loopback(parts.hostname):
            raise LedgerUnavailable(
                f"Cannot reach the spend ledger on 127.0.0.1:{parts.port} — {TUNNEL_HINT}. "
                "No paid call is made without the ledger."
            ) from None
        raise LedgerUnavailable(
            "Cannot reach the spend ledger database. No paid call is made without it."
        ) from None

    global _schema_ready
    if not _schema_ready:
        ensure_schema(conn)
        _schema_ready = True
    return conn


def ensure_schema(conn) -> None:
    """Create the ledger table if it does not exist. Safe to run any number of times."""
    # The lock keeps two processes starting together (an overlapping deploy) from
    # racing on CREATE TABLE IF NOT EXISTS, which is not itself concurrency-safe.
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------------------

def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def month_to_date_spend(conn, now: datetime | None = None) -> Decimal:
    """Everything booked this UTC month: settled calls and open reservations alike."""
    # WHAT: reservations count. A process that died mid-call left its worst case here,
    # and that is the figure the month must carry until someone settles it by hand.
    start, end = _month_bounds(now or datetime.now(timezone.utc))
    row = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM spend_ledger WHERE ts >= %s AND ts < %s",
        (start, end),
    ).fetchone()
    return Decimal(row[0])


def reserve(conn, *, user_id: str, phase: str, model: str, estimate_usd, now: datetime | None = None) -> uuid.UUID:
    """Book the call's worst case, or refuse it. Returns the reservation id."""
    if not user_id or not phase:
        raise ValueError("every call is booked: user_id and phase are required")
    estimate = _usd(estimate_usd)
    now = now or datetime.now(timezone.utc)
    rid = uuid.uuid4()
    # WHAT: lock, read, decide and insert in one transaction.
    # CONCEPT: loop — without the lock two runs read the same month-to-date, both see
    # headroom, and both spend it. The lock is transaction-scoped, so it is released by
    # the commit that makes this reservation visible to the next reader.
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (RESERVE_LOCK_KEY,))
        spent = month_to_date_spend(conn, now)
        if spent + estimate > MONTHLY_BUDGET_USD:
            raise BudgetExceeded(
                f"Refusing to call {model}: month-to-date spend ${spent:.2f} plus this "
                f"call's worst case ${estimate:.2f} would exceed MONTHLY_BUDGET_USD "
                f"(${MONTHLY_BUDGET_USD:.2f}). Raise the ceiling in agents/budget.py or "
                "wait for the month to roll over. No API call made."
            )
        conn.execute(
            "INSERT INTO spend_ledger (id, ts, user_id, phase, model, estimated_cost_usd, state) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'reserved')",
            (rid, now, user_id, phase, model, estimate),
        )
    return rid


def settle(conn, reservation_id: uuid.UUID, model: str, usage) -> Decimal:
    """Replace a reservation's worst case with the call's actual tokens and cost."""
    cost = _usd(estimate_cost_usd(model, usage))
    with conn.transaction():
        cur = conn.execute(
            "UPDATE spend_ledger SET input_tokens = %s, output_tokens = %s, "
            "cache_read_tokens = %s, cache_write_tokens = %s, estimated_cost_usd = %s, "
            "state = 'settled' WHERE id = %s AND state = 'reserved'",
            (
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cost,
                reservation_id,
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"reservation {reservation_id} is not open; nothing settled")
    return cost


def ledger_reachable(connect_timeout: int = 3) -> bool:
    """True when the ledger table answers. For /health; reveals no figures."""
    try:
        with connect(connect_timeout=connect_timeout) as conn:
            conn.execute("SELECT 1 FROM spend_ledger LIMIT 1")
        return True
    except Exception:
        return False
