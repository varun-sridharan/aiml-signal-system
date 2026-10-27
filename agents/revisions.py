"""Corrections to a published edition, through an append-only revision table (Run 9, Step A; Varun's decision of 2026-10-26).

A published brief is never edited (`briefs_append_only` in agents/schema.sql). A headline that must change gets a row in `edition_revisions`
instead, and the Weekly Digest page applies the latest row per field over the brief's body, with a note that says when and why, never what
the old text was. A row is written only by `revise`, and only after

  1. the free checks pass: the value has the field's shape, the reason is at most 200 characters, and the headline cap of agents/style_check
     (14 words and 90 characters; the thread two lines of at most 8 words and no figure; at most one figure elsewhere; no number shared
     between the section headlines) holds for the edition with the new value in place; and
  2. the claim gate passes the new value on its own: one grader call (agents/llm.py, booked in the ledger) grades it against every story's
     excerpt, as the week unit of the gate does. The verdict is kept with the row, and the table refuses a row without a passing verdict.

Nothing here updates or deletes a row: the code has no such path and the database has a trigger that refuses both.

    python -m agents.revisions --brief 2026-W44 --field stories.title --value "A headline" --reason "headline shortened" [--dry-run]
    python -m agents.revisions --brief 2026-W44 --field thread.lines --from-recheck evidence/headline-recheck.json --reason "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import psycopg

from . import briefs, budget, composer, framer, llm, runs, style_check, weekly

FIELDS = ("thread.lines", "learning.headline", "stories.title")
REASON_MAX = 200


class RevisionRefused(Exception):
    """The value did not pass the free checks or the claim gate, or is not a revisable field of this brief. Nothing was written.

    `verdict` is the claim gate's verdict when the gate was the reason, so a caller can keep what it said.
    """
    def __init__(self, message: str, verdict: dict | None = None):
        super().__init__(message)
        self.verdict = verdict


# ---------------------------------------------------------------- where a field lives in a brief

def _edition(body: dict) -> dict:
    edition = body.get("edition") if isinstance(body, dict) else None
    if not isinstance(edition, dict):
        raise RevisionRefused("this brief has no composed edition, so it has no section headlines to correct")
    return edition


def current_value(body: dict, field: str):
    edition = _edition(body)
    if field == "thread.lines":
        return list((edition.get("thread") or {}).get("lines") or [])
    if field == "learning.headline":
        return (edition.get("learning") or {}).get("headline", "")
    if field == "stories.title":
        return edition.get("storiesTitle", "")
    raise RevisionRefused(f"{field!r} is not a revisable field (one of {', '.join(FIELDS)})")


def with_value(body: dict, field: str, value) -> dict:
    """A copy of the brief's body with the field's value in place."""
    out = json.loads(json.dumps(body))
    edition = _edition(out)
    if field == "thread.lines":
        edition.setdefault("thread", {})["lines"] = list(value)
    elif field == "learning.headline":
        edition.setdefault("learning", {})["headline"] = value
    elif field == "stories.title":
        edition["storiesTitle"] = value
    else:
        raise RevisionRefused(f"{field!r} is not a revisable field (one of {', '.join(FIELDS)})")
    return out


def shape_problem(field: str, value) -> str | None:
    if field == "thread.lines":
        if not (isinstance(value, list) and len(value) == 2 and all(isinstance(x, str) and x.strip() for x in value)):
            return "thread.lines is a list of exactly two non-empty strings"
    elif field in ("learning.headline", "stories.title"):
        if not (isinstance(value, str) and value.strip()):
            return f"{field} is a non-empty string"
    else:
        return f"{field!r} is not a revisable field"
    return None


def free_findings(body: dict, field: str, value) -> list[str]:
    """What the headline cap says about the edition with `value` in the field, for that field only."""
    trial = _edition(with_value(body, field, value))
    own = {"thread.lines": ("thread.line.",), "learning.headline": ("learning.headline",), "stories.title": ("stories.title",)}[field]
    return [message for path, message in style_check.headline_findings(trial) if path.startswith(own)]


def verdict_text(value) -> str:
    """The text a verdict is about, as the gate sees it: the plain words, the thread's two lines joined."""
    return style_check.plain(" ".join(value) if isinstance(value, list) else value)


def verdict_passes(verdict) -> bool:
    return (isinstance(verdict, dict) and bool(verdict.get("claims")) and not verdict.get("unsupported_claims") and not verdict.get("grader_errors")
            and all(c.get("supported") and c.get("span_verified") for c in verdict["claims"] if isinstance(c, dict)))


# ---------------------------------------------------------------- the claim gate, on one value

def grade(conn, brief_id: str, run_id: str, value, *, client=None, user_id: str | None = None) -> dict:
    """One grader call: `value` alone against every story's excerpt of the brief's own week. Returns the normalised verdict."""
    art = runs.load_artifact(conn, run_id, "framer")
    wide_day, published, _ = weekly.composer_day(conn, art["raw_day"])
    union = [{"id": e["id"], "source_excerpt": e["excerpt"]} for e in composer.composer_items(wide_day, published)]
    profile = framer.load_json(framer.PROFILE_PATH)
    text = style_check.plain(" ".join(value) if isinstance(value, list) else value)
    part = {"items": [], "thread": {"writing_under_test": text, "_note": "Graded against the union below: a section headline synthesises across stories.",
                                    "ground_truth_excerpt_union": union}, "_grading_note": composer.GRADER_NOTE}
    client = client or llm.build_client()
    raw, _used, _cost = llm.call(client, user_id=user_id or profile["user_id"], phase=composer.CHECK_PHASE, conn=conn, **framer.check_request(part))
    verdict = framer.normalise_verdict(json.loads(raw)["thread"], "\n".join(u["source_excerpt"] for u in union), text)
    return {"text": text, "claims": [{"claim": c.get("claim"), "supported": c.get("supported"), "span_verified": c.get("span_verified")} for c in verdict.get("claims", [])],
            "unsupported_claims": verdict.get("unsupported_claims", []), "grader_errors": verdict.get("grader_errors", []),
            "model": llm.CHECK_MODEL, "checked_at": datetime.now(timezone.utc).isoformat(), "how": "the claim gate on the new value alone"}


# ---------------------------------------------------------------- writing a revision

def effective_body(conn, brief_id: str) -> tuple:
    """(brief row, its body with the revisions so far applied); the thing a new revision is a correction of."""
    row = briefs.get(conn, brief_id)
    if row is None:
        raise RevisionRefused(f"there is no brief {brief_id}")
    body = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return row, apply(body, rows_for(conn, brief_id))[0]


def revise(conn, brief_id: str, field: str, new_value, reason: str, *, client=None, verdict: dict | None = None, dry_run: bool = False) -> dict:
    """Correct one field of a published brief. Writes a row only if the free checks and the claim gate pass; raises RevisionRefused otherwise.

    `verdict` may carry a passing verdict already recorded for exactly this value (Run 8's rechecks of Edition 02): it must name the text it was given for
    and match the new value word for word, and the database refuses a verdict with any claim unsupported or any span unverified. Without a verdict the gate runs.
    With dry_run only the free checks run, and nothing is paid or written.
    """
    reason = (reason or "").strip()
    if not 1 <= len(reason) <= REASON_MAX:
        raise RevisionRefused(f"the reason is {len(reason)} characters; it must be 1 to {REASON_MAX}")
    problem = shape_problem(field, new_value)
    if problem:
        raise RevisionRefused(problem)
    row, body = effective_body(conn, brief_id)
    old_value = current_value(body, field)
    if old_value == new_value:
        raise RevisionRefused("the new value is the current value")
    findings = free_findings(body, field, new_value)
    if findings:
        raise RevisionRefused("the headline cap: " + "; ".join(findings))
    if dry_run:
        return {"brief": brief_id, "field": field, "old": old_value, "new": new_value, "free_checks": "passed", "written": False}
    if verdict is not None and verdict.get("text") != verdict_text(new_value):
        raise RevisionRefused("the recorded verdict is not for this text (a verdict carries the exact words it was given for)")
    gate = verdict if verdict is not None else grade(conn, brief_id, _run_id(conn, brief_id), new_value, client=client)
    if not verdict_passes(gate):
        raise RevisionRefused("the claim gate did not pass the new value: " + "; ".join(map(str, gate.get("unsupported_claims") or ["no verified claim"])), verdict=gate)
    with conn.transaction():
        got = conn.execute("INSERT INTO edition_revisions (brief_id, field, old_value, new_value, reason, gate_verdict) "
                           "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb) RETURNING id, created_at",
                           (brief_id, field, json.dumps(old_value, ensure_ascii=False), json.dumps(new_value, ensure_ascii=False), reason,
                            json.dumps(gate, ensure_ascii=False))).fetchone()
    return {"brief": brief_id, "field": field, "old": old_value, "new": new_value, "free_checks": "passed", "written": True, "id": got[0], "created_at": got[1]}


def _run_id(conn, brief_id: str) -> str:
    return conn.execute("SELECT run_id FROM briefs WHERE id = %s", (brief_id,)).fetchone()[0]


# ---------------------------------------------------------------- reading them

def rows_for(conn, brief_id: str) -> list:
    """Every revision of a brief, oldest first, as dicts."""
    try:
        cur = conn.execute("SELECT id, brief_id, field, old_value, new_value, reason, gate_verdict, created_at FROM edition_revisions "
                           "WHERE brief_id = %s ORDER BY id ASC", (brief_id,))
    except psycopg.errors.UndefinedTable:       # a database whose schema has not been applied yet has no corrections
        return []
    return [dict(zip(("id", "brief_id", "field", "old_value", "new_value", "reason", "gate_verdict", "created_at"), r)) for r in cur.fetchall()]


def when(created_at) -> str:
    """The date of a correction as a reader in Pacific time sees it ("Oct 26"); the site's weeks and its Friday job run on Pacific time too."""
    local = created_at.astimezone(weekly.PACIFIC)
    return f"{local:%b} {local.day}"


def note(created_at, reason: str) -> str:
    return f"Corrected {when(created_at)}: {reason}"


def apply(body: dict, rows: list) -> tuple:
    """(the body with the latest revision per field in place, the notes by field). With no rows the body is returned as it is, untouched."""
    if not rows:
        return body, {}
    latest: dict = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        latest[r["field"]] = r
    out = json.loads(json.dumps(body))
    notes = {}
    for field, r in latest.items():
        if not isinstance(out.get("edition"), dict):
            continue
        out = with_value(out, field, r["new_value"])
        notes[field] = note(r["created_at"], r["reason"])
    if notes:
        out["_corrections"] = notes
    return out, notes


def corrected(conn, row):
    """A (id, body, published_at) row with its revisions applied; the row itself when there are none."""
    if row is None:
        return row
    rows = rows_for(conn, row[0])
    if not rows:
        return row
    body = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return (row[0], apply(body, rows)[0]) + tuple(row[2:])


def latest_thread_lines(conn) -> dict:
    """{brief id: the latest corrected thread lines}, for the archive's two-line entries."""
    try:
        cur = conn.execute("SELECT DISTINCT ON (brief_id) brief_id, new_value FROM edition_revisions WHERE field = 'thread.lines' ORDER BY brief_id, id DESC")
    except psycopg.errors.UndefinedTable:
        return {}
    return {b: v for b, v in cur.fetchall()}


# ---------------------------------------------------------------- the command line

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--brief", required=True)
    ap.add_argument("--field", required=True, choices=FIELDS)
    ap.add_argument("--value", help="the new value as text, or as JSON for thread.lines")
    ap.add_argument("--from-recheck", help="a headline-recheck.json whose passing attempt for this field is the value and its recorded claims the verdict")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    verdict = None
    if args.from_recheck:
        log = json.load(open(args.from_recheck, encoding="utf-8"))
        key = "thread" if args.field == "thread.lines" else args.field          # scripts/recheck_headlines.py names the thread's field "thread"
        passed = [s for s in (log["fields"].get(key) or {}).get("attempts", []) if s.get("verdict") == "passed"]
        if not passed:
            print("that recheck has no passing attempt for this field", file=sys.stderr)
            return 1
        value = passed[-1]["text"]
        verdict = {"text": verdict_text(value), "claims": passed[-1]["claims"], "unsupported_claims": passed[-1].get("unsupported_claims", []), "grader_errors": [],
                   "model": llm.CHECK_MODEL, "how": f"recorded by the claim gate in {args.from_recheck} before this revision, for exactly this text"}
    elif args.value is None:
        print("give --value or --from-recheck", file=sys.stderr)
        return 1
    elif args.field == "thread.lines":
        value = json.loads(args.value)
    else:
        value = args.value
    with budget.connect() as conn:
        try:
            out = revise(conn, args.brief, args.field, value, args.reason, verdict=verdict, dry_run=args.dry_run)
        except RevisionRefused as why:
            print(f"refused, nothing written: {why}", file=sys.stderr)
            return 2
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
