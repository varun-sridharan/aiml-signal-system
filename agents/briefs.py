"""Briefs and feedback: what was published, and what readers said about it.

A published brief is the Framer's output exactly as written, one per run, never edited
(the database refuses). Feedback is anonymous: anyone can vote, and the only thing kept
about the voter is a salted hash of their address. A changed mind is a new row; the
latest vote per (brief, item, client) counts.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

MAX_COMMENT_CHARS = 2000
MIN_SALT_CHARS = 16

# WHAT: how many votes one client may cast in a window.
# CONCEPT: rate limit. Voting has no login, so the only brake on a script is a count per
# client. Generous for a human clicking through a brief (about fifteen items, a few note
# boxes), tight for anything automated.
RATE_LIMIT_VOTES = 30
RATE_LIMIT_WINDOW = timedelta(hours=1)
# A ceiling on all votes, whoever sends them: rotating addresses defeats a per-client limit,
# and this keeps the table from being filled by one script.
GLOBAL_LIMIT_VOTES = 1_000


class RateLimited(RuntimeError):
    """This client has voted too often in the window."""


class FeedbackRefused(ValueError):
    """The vote or comment is not acceptable."""


def client_hash(address: str, salt: str) -> str:
    """A salted, keyed hash of a client address; the address itself is never stored."""
    if len(salt) < MIN_SALT_CHARS:
        raise FeedbackRefused(f"the salt must be at least {MIN_SALT_CHARS} characters: a short one can be "
                              "brute-forced across the address space")
    return hmac.new(salt.encode("utf-8"), address.encode("utf-8"), hashlib.sha256).hexdigest()


def publish(conn, brief_id: str, run_id: str, body: dict, now: datetime) -> None:
    """Store a finished brief. Refuses a second brief for the same week."""
    if now.tzinfo is None:
        raise FeedbackRefused("now must be timezone-aware")
    with conn.transaction():
        conn.execute("INSERT INTO briefs (id, run_id, body, published_at) VALUES (%s, %s, %s, %s)",
                     (brief_id, run_id, Jsonb(body), now))


# Weeks that are not editions. A brief is append-only, so a row written by mistake cannot be removed without disabling the database's own
# protection, which only Varun may decide. The public views leave these out instead, and say why here.
WITHDRAWN = {
    "1999-W20": "a test fixture a Run 9 helper committed to the live database on 2026-10-26 by mistake; not an edition",
}


def withdrawn() -> list:
    return sorted(WITHDRAWN)


def latest(conn):
    """The most recent published brief as (id, body, published_at), or None."""
    return conn.execute("SELECT id, body, published_at FROM briefs WHERE id <> ALL(%s) ORDER BY id DESC LIMIT 1", (withdrawn(),)).fetchone()


def get(conn, brief_id: str):
    if brief_id in WITHDRAWN:
        return None
    return conn.execute("SELECT id, body, published_at FROM briefs WHERE id = %s", (brief_id,)).fetchone()


def vote(conn, brief_id: str, item_ref: str, direction: str, comment: str | None,
         client: str, now: datetime) -> uuid.UUID:
    """Record a vote. `client` is already a client_hash. Comments are for thumbs-down only."""
    if direction not in ("up", "down"):
        raise FeedbackRefused("vote must be 'up' or 'down'")
    comment = (comment or "").replace("\r\n", "\n").strip() or None   # a browser sends CRLF; count it as one
    if comment is not None:
        if "\x00" in comment:
            raise FeedbackRefused("a note cannot contain a NUL character")
        if direction != "down":
            raise FeedbackRefused("a note goes with a thumbs-down only")
        if len(comment) > MAX_COMMENT_CHARS:
            raise FeedbackRefused(f"notes are limited to {MAX_COMMENT_CHARS} characters")
    with conn.transaction():
        # Serialise one client's votes, so two requests cannot both pass the count.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (client,))
        recent = conn.execute("SELECT count(*) FROM feedback WHERE client_hash = %s AND created_at > %s",
                              (client, now - RATE_LIMIT_WINDOW)).fetchone()[0]
        if recent >= RATE_LIMIT_VOTES:
            raise RateLimited(f"more than {RATE_LIMIT_VOTES} votes in {RATE_LIMIT_WINDOW}")
        everyone = conn.execute("SELECT count(*) FROM feedback WHERE created_at > %s", (now - RATE_LIMIT_WINDOW,)).fetchone()[0]
        if everyone >= GLOBAL_LIMIT_VOTES:
            raise RateLimited(f"more than {GLOBAL_LIMIT_VOTES} votes from everyone in {RATE_LIMIT_WINDOW}")
        row_id = uuid.uuid4()
        try:
            with conn.transaction():
                conn.execute("INSERT INTO feedback (id, brief_id, item_ref, vote, comment, client_hash, created_at) "
                             "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                             (row_id, brief_id, item_ref, direction, comment, client, now))
        except psycopg.errors.ForeignKeyViolation:
            raise FeedbackRefused(f"no brief {brief_id!r}") from None
    return row_id


def tally(conn, brief_id: str) -> dict[str, dict[str, int]]:
    """Latest vote per client per item: {item_ref: {'up': n, 'down': m}}."""
    rows = conn.execute(
        "SELECT item_ref, vote, count(*) FROM ("
        "  SELECT DISTINCT ON (item_ref, client_hash) item_ref, vote FROM feedback "
        "  WHERE brief_id = %s ORDER BY item_ref, client_hash, created_at DESC, id DESC"
        ") latest GROUP BY item_ref, vote", (brief_id,)).fetchall()
    out: dict[str, dict[str, int]] = {}
    for item_ref, direction, n in rows:
        out.setdefault(item_ref, {"up": 0, "down": 0})[direction] = n
    return out
