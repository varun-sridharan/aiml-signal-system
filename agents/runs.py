"""Runs: where a weekly run got to, so a failed one resumes instead of starting over.

A weekly run is the Reader, then the Framer, then the gates, then publish. If the Framer
fails and the run starts again from the Reader, it re-fetches sources that may have
changed overnight, pays again for steps that already finished, and the retried brief may
rest on different evidence from the failed one. So each attempt at a stage is a row in
run_stages, and a 'done' attempt holds pointers to its output: the checkpoint a rerun
loads before carrying on from the next stage.

The rules a resume depends on are enforced by the database (agents/schema.sql), not by
this module: a done stage is never changed or deleted, an attempt only goes running ->
done or running -> failed, and a stage cannot start before the one ahead of it is done.
This module's own checks are the ones a database cannot make: that output is pointers
and not text, that an error message carries no credential, and whether a running attempt
has been abandoned.

Every function takes an open connection, from budget.connect(), and the time as `now`:
there is no hidden clock, so a test can put an attempt three hours in the past.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import NamedTuple

import psycopg
from psycopg.types.json import Jsonb

# WHAT: the stages of a weekly run, in the order they must finish.
# CONCEPT: checkpointed pipeline. Each stage reads only what the stage before it wrote,
# so the last done stage is a complete account of where the run got to; a rerun skips
# everything up to it and repeats nothing that already succeeded. The database holds
# the same list (run_stages_order in schema.sql); change both together.
STAGES = ("reader", "framer", "gates", "publish")

# WHAT: how long a running attempt can go without finishing before it counts as dead.
# CONCEPT: a lease. A worker that crashes cannot mark its own attempt failed, so the
# next worker decides: older than this, the attempt is abandoned and a new one starts;
# younger, another worker may still be on it, and starting a second would run the
# stage twice. Two hours is well beyond the longest stage expected (the Reader's fetch).
STALE_AFTER = timedelta(hours=2)

# Output is pointers: ids of items, excerpts and the brief. A string this long is text
# that belongs in the excerpt store, not an id.
MAX_POINTER_CHARS = 512
MAX_ERROR_CHARS = 500

_SECRETS = (
    (re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@]*@\S*", re.I), "<redacted-url>"),  # scheme://user:pass@host
    (re.compile(r"\b(password|passwd|pwd|token|secret|api[_-]?key)\s*[=:]\s*\S+", re.I), r"\1=<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "<redacted-key>"),
)


class RunRefused(ValueError):
    """The request breaks a rule of the run: wrong stage, bad output, no such attempt."""


class StageBusy(RuntimeError):
    """Another worker is running this stage, and its attempt is not yet stale."""


class Run(NamedTuple):
    id: str
    started_at: datetime
    finished_at: datetime | None
    status: str


def _aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise RunRefused("now must be timezone-aware")


def _stage(stage: str) -> None:
    if stage not in STAGES:
        raise RunRefused(f"unknown stage {stage!r}; stages are {', '.join(STAGES)}")


def _check_pointers(value, path="output") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise RunRefused(f"{path} has a non-string key {k!r}")
            _check_pointers(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_pointers(v, f"{path}[{i}]")
    elif isinstance(value, str):
        if len(value) > MAX_POINTER_CHARS:
            raise RunRefused(f"{path} is a {len(value)}-character string: output holds pointers, not text")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise RunRefused(f"{path} is a {type(value).__name__}, not JSON")


def scrub(error: str) -> str:
    """A short error message with anything that looks like a credential removed."""
    text = " ".join(str(error).split())
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text[:MAX_ERROR_CHARS] or "failed"


def open_run(conn, run_id: str, now: datetime) -> Run:
    """Create the run for this week, or return it if a previous attempt already did."""
    _aware(now)
    with conn.transaction():
        conn.execute(
            "INSERT INTO runs (id, started_at, status) VALUES (%s, %s, 'running') ON CONFLICT (id) DO NOTHING",
            (run_id, now),
        )
        row = conn.execute("SELECT id, started_at, finished_at, status FROM runs WHERE id = %s", (run_id,)).fetchone()
    return Run(*row)


def _done_stages(conn, run_id: str) -> set[str]:
    rows = conn.execute("SELECT stage FROM run_stages WHERE run_id = %s AND status = 'done'", (run_id,)).fetchall()
    return {r[0] for r in rows}


def next_stage(conn, run_id: str) -> str | None:
    """The first stage with no done attempt, or None when the run is complete."""
    done = _done_stages(conn, run_id)
    return next((s for s in STAGES if s not in done), None)


def start_stage(conn, run_id: str, stage: str, now: datetime) -> int:
    """Start a new attempt at a stage and return its attempt number.

    A running attempt older than STALE_AFTER is marked failed ('abandoned') first; a
    younger one belongs to another worker, and this raises StageBusy. Starting a stage
    before the one ahead of it is done is refused by the database.
    """
    _aware(now)
    _stage(stage)
    with conn.transaction():
        # Lock the run, so two workers starting together queue here instead of racing.
        if conn.execute("SELECT 1 FROM runs WHERE id = %s FOR UPDATE", (run_id,)).fetchone() is None:
            raise RunRefused(f"no run {run_id}; open_run() first")
        if stage in _done_stages(conn, run_id):
            raise RunRefused(f"run {run_id} stage {stage} is already done; load stage_output() instead")
        running = conn.execute(
            "SELECT attempt, started_at FROM run_stages WHERE run_id = %s AND stage = %s AND status = 'running'",
            (run_id, stage),
        ).fetchone()
        if running is not None:
            attempt, started_at = running
            if now - started_at <= STALE_AFTER:
                raise StageBusy(f"run {run_id} stage {stage} attempt {attempt} started at {started_at:%Y-%m-%d %H:%M} "
                                f"and is not stale yet; another worker owns it")
            conn.execute(
                "UPDATE run_stages SET status = 'failed', finished_at = %s, error = 'abandoned' "
                "WHERE run_id = %s AND stage = %s AND attempt = %s",
                (now, run_id, stage, attempt),
            )
        attempt = conn.execute(
            "SELECT coalesce(max(attempt), 0) + 1 FROM run_stages WHERE run_id = %s AND stage = %s", (run_id, stage),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO run_stages (run_id, stage, attempt, status, started_at) VALUES (%s, %s, %s, 'running', %s)",
            (run_id, stage, attempt, now),
        )
    return attempt


def _end(conn, run_id, stage, attempt, status, now, *, output=None, error=None) -> None:
    row = conn.execute(
        "UPDATE run_stages SET status = %s, finished_at = %s, output = %s, error = %s "
        "WHERE run_id = %s AND stage = %s AND attempt = %s AND status = 'running' RETURNING 1",
        (status, now, output, error, run_id, stage, attempt),
    ).fetchone()
    if row is None:
        raise RunRefused(f"run {run_id} stage {stage} attempt {attempt} is not running")


def finish_stage(conn, run_id: str, stage: str, attempt: int, output, now: datetime) -> None:
    """Mark an attempt done with pointers to its output. Finishing publish finishes the run."""
    _aware(now)
    _stage(stage)
    _check_pointers(output)
    try:
        with conn.transaction():
            _end(conn, run_id, stage, attempt, "done", now, output=None if output is None else Jsonb(output))
            if stage == STAGES[-1]:
                conn.execute("UPDATE runs SET status = 'done', finished_at = %s WHERE id = %s", (now, run_id))
    except psycopg.errors.CheckViolation as e:
        raise RunRefused(f"run {run_id} stage {stage}: output refused by the database "
                         f"({e.diag.constraint_name}); it is capped at 64 KB of pointers") from None


def fail_stage(conn, run_id: str, stage: str, attempt: int, error: str, now: datetime) -> None:
    """Mark an attempt failed. The next start_stage() for this stage is a new attempt."""
    _aware(now)
    _stage(stage)
    with conn.transaction():
        _end(conn, run_id, stage, attempt, "failed", now, error=scrub(error))


def stage_output(conn, run_id: str, stage: str):
    """The output of a stage's done attempt: what a resumed run loads instead of re-running it."""
    _stage(stage)
    row = conn.execute(
        "SELECT output FROM run_stages WHERE run_id = %s AND stage = %s AND status = 'done'", (run_id, stage),
    ).fetchone()
    if row is None:
        raise KeyError(f"run {run_id} stage {stage} has no done attempt")
    return row[0]
