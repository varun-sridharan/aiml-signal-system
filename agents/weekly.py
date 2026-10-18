"""The weekly run: Reader, then the Framer, then the gates, then publish.

    python -m agents.weekly                    # this ISO week
    python -m agents.weekly --week 2026-W42
    python -m agents.weekly --stop-after reader   # stop cleanly after a stage (resume demo)

Each stage is checkpointed in the `runs` tables (agents/runs.py). A rerun of a week loads
every stage that already finished and continues at the first that did not, so a failed
Framer does not re-fetch the sources or pay for the Reader again. The Framer and its gates
are the ones that shipped (agents/framer.py), ported without change; this module only
feeds them verified items from the database and stores what they produce.

NOT scheduled. The scheduled Weekly Digest keeps running in parallel (decision 2026-10-17);
see README.md for how to put this on a Railway cron when it has earned the switch.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timezone

try:
    from . import briefs, budget, excerpts, framer, gates, items, llm, reader, runs
except ImportError:  # pragma: no cover
    import briefs
    import budget
    import excerpts
    import framer
    import gates
    import items
    import llm
    import reader
    import runs

MAX_BRIEF_ITEMS = 7        # the golden week's size: about five minutes of reading
MAX_PER_DOMAIN = 3         # one publisher cannot be the whole edition
MAX_DO_THIS = 2
LEAD_MAX_CHARS = 700       # what the Framer is shown of an article: its opening sentences, not the page
LEAD_MAX_SENTENCES = 4     # the contract says 1-3 factual sentences; the golden excerpts run 248-393 characters
LEAD_MIN_PARAGRAPH_CHARS = 80

# Where a practitioner can act on what they read; items from these on a core topic are DO THIS.
PRACTICE_DOMAINS = {"github.blog", "blog.modelcontextprotocol.io"}
# ...and only on topics a practitioner can try. A GitHub post that merely mentions "agents" matches the
# core multi-agent topic through a generic word, and must not take one of the two DO THIS slots.
PRACTICE_TOPICS = {"MCP architecture", "agentic coding", "harness engineering"}
CORE_TOPICS_USER = "varun"


class GateFailed(RuntimeError):
    """The brief failed a hard gate and was not published."""


def week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def week_start(week: str) -> date:
    year, number = re.fullmatch(r"(\d{4})-W(\d{2})", week).groups()
    return date.fromisocalendar(int(year), int(number), 1)


# --------------------------------------------------------------------------------------
# From a fetched page to the Framer's source_excerpt
# --------------------------------------------------------------------------------------

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _plain(paragraph: str) -> str:
    return " ".join(_LINK.sub(lambda m: m.group(1), paragraph).replace("**", "").replace("`", "").split())


_BOILERPLATE = ("arxivlabs", "cookie", "subscribe", "newsletter", "sign up", "all rights reserved", "privacy policy",
                "skip to content", "opens in a new window", "authors:", "share this", "follow us")


def lead(text: str, max_chars: int = LEAD_MAX_CHARS, prefer_meta: bool = False) -> str:
    """The opening of an article as plain sentences: the Framer's `source_excerpt`.

    The fetch returns a whole page (metadata, menus, the article, a footer). The Framer's
    contract is 1-3 factual sentences, and the faithfulness gate grades against exactly
    what it was shown, so this keeps the first running paragraphs of prose and nothing
    else. Deterministic and offline. Returns "" when a page has no prose to show.

    `prefer_meta` is for pages whose body is not the article: an arXiv abstract page's body
    is an author list and site notices, and the abstract itself is in the page metadata.
    """
    body = text
    match = re.match(r"^---\n.*?\n---\n", text, re.S)
    description = ""
    if match:
        body = text[match.end():]
        meta = dict(re.findall(r"^meta-([\w:.-]+):\s*(.+)$", match.group(0), re.M))
        description = (meta.get("citation_abstract") or meta.get("og:description") or meta.get("description") or "").strip()
        if prefer_meta and description:
            body = ""
    paragraphs = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block or block.startswith(("#", "- ", "* ", "!", "|", "```", ">", "[")) or "](" in block[:2]:
            continue
        plain = _plain(block)
        if (len(plain) >= LEAD_MIN_PARAGRAPH_CHARS and len(plain.split()) >= 12 and not plain.endswith(("•", ":"))
                and not any(b in plain.lower() for b in _BOILERPLATE)):
            paragraphs.append(plain)
    chosen = " ".join(paragraphs) if paragraphs else _plain(description)
    # The Framer's contract is a few factual sentences, and the gate grades against exactly
    # what it was shown, so show the opening sentences only: a wider excerpt would widen what
    # counts as "supported" well beyond the golden cases.
    sentences = re.split(r"(?<=[.!?])\s+", chosen)
    kept, size = [], 0
    for sentence in sentences[:LEAD_MAX_SENTENCES]:
        if size + len(sentence) > max_chars and kept:
            break
        kept.append(sentence)
        size += len(sentence) + 1
    chosen = " ".join(kept)
    return chosen[:max_chars].rstrip()


_UNAMBIGUOUS_MONTHS = ("january", "february", "april", "june", "july", "august", "september", "october", "november", "december")
# "May" and "March" are also verbs: they count as dates only when capitalised and next to a number or a date word.
_FACT_TOKEN = re.compile(r"\$?\d[\d,.]*[%BMTKx+]?|\b(?:" + "|".join(_UNAMBIGUOUS_MONTHS) + r")\b", re.I)


def headline_facts_missing(headline: str, excerpt: str) -> list[str]:
    """Checkable facts in a headline (numbers, money, dates) that the excerpt does not contain.

    The Framer writes from the excerpt but sees the headline too, and a headline's figures are
    the first thing it restates. The faithfulness gate then (rightly) finds them unsupported,
    because the only ground truth is the excerpt. So a story whose headline claims something
    its excerpt does not say is not a verified item yet, and is not framed.

    Numbers match as whole numbers ("10" is not found inside "2010"); "May" and "March" count
    only as dates, not as verbs.
    """
    haystack = " ".join(excerpt.lower().replace(",", "").split())
    tokens = _FACT_TOKEN.findall(headline) + [m.group(1) for m in re.finditer(r"\b(?:in|since|until|by|of|early|late|mid|on)[- ](May|March)\b", headline)]
    missing = []
    for token in tokens:
        plain = token.lower().replace(",", "").strip(".")
        if len(plain) < 2 and not plain.isdigit():
            continue
        found = re.search(r"(?<![\w.])" + re.escape(plain) + r"(?![\w])", haystack) if plain[0].isalnum() or plain[0] == "$" else plain in haystack
        if not found and token not in missing:
            missing.append(token)
    return missing


def tags_for(domain: str, topics: list[str], core_topics: set[str], do_this_left: int) -> list[str]:
    """The Gatekeeper's tags, deterministically: DO THIS where it can be acted on, RADAR for papers."""
    if domain == "arxiv.org":
        return ["RADAR"]
    if domain in PRACTICE_DOMAINS and core_topics & PRACTICE_TOPICS & set(topics) and do_this_left > 0:
        return ["DO THIS", "KNOW"]
    return ["KNOW"]


def build_raw_day(conn, week: str, item_ids: list[str], profile: dict, skipped: list | None = None) -> dict:
    """The Framer's input for a week, from verified items in the database."""
    core = {t for (t,) in conn.execute("SELECT topic FROM interest_nodes WHERE user_id = %s AND ring = 'core'",
                                       (profile["user_id"],)).fetchall()}
    rows = conn.execute(
        "SELECT i.id, i.canonical_url, i.domain, i.title, i.relevance_score, i.relevance_terms, i.excerpt_id "
        "FROM items i WHERE i.id = ANY(%s::uuid[]) AND i.status = 'fetched' "
        "ORDER BY i.relevance_score DESC, i.discovered_at DESC", (item_ids,)).fetchall()
    chosen, per_domain, do_this_left = [], {}, MAX_DO_THIS
    for iid, url, domain, title, _score, topics, eid in rows:
        if per_domain.get(domain, 0) >= MAX_PER_DOMAIN or not items.surfaceable(conn, iid):
            continue
        excerpt = lead(excerpts.get(conn, eid), prefer_meta=(domain == "arxiv.org"))
        if not excerpt:
            continue  # nothing readable on the page: not a story we can ground
        missing = headline_facts_missing(title, excerpt)
        if missing:  # the headline claims what the excerpt does not say: not verified
            if skipped is not None:
                skipped.append(f"{title[:60]}: {', '.join(missing)} not in the excerpt")
            continue
        tags = tags_for(domain, topics, core, do_this_left)
        do_this_left -= "DO THIS" in tags
        per_domain[domain] = per_domain.get(domain, 0) + 1
        sources = [{"label": name, "url": src_url, "tier": "primary" if role == "primary" else "corroboration"}
                   for name, src_url, role in conn.execute(
                       "SELECT s.name, i2.url, i2.role FROM item_sources i2 JOIN sources s ON s.id = i2.source_id "
                       "WHERE i2.item_id = %s ORDER BY (i2.role = 'primary') DESC, s.name", (iid,)).fetchall()]
        chosen.append({"id": str(iid), "category": "AI", "headline": title, "tags": tags,
                       "source_excerpt": excerpt, "sources": sources})
        if len(chosen) >= MAX_BRIEF_ITEMS:
            break
    return {"date": week_start(week).isoformat(), "items": chosen}


# --------------------------------------------------------------------------------------
# The stages
# --------------------------------------------------------------------------------------

def framer_stage(conn, client, week: str, now: datetime, profile: dict) -> dict:
    def work(attempt: int) -> dict:
        reader_out = runs.stage_output(conn, week, "reader")
        skipped: list = []
        raw_day = build_raw_day(conn, week, reader_out["items"], profile, skipped)
        for note in skipped:
            print(f"  not framed — {note}", file=sys.stderr)
        if not raw_day["items"]:
            raise RuntimeError("no verified, readable items this week; nothing to frame")
        framing, _usage, cost = framer.frame_day(client, framer.load_framer_system_prompt(), profile, raw_day)
        runs.save_artifact(conn, week, "framer", attempt, {"raw_day": raw_day, "framing": framing}, now)
        return {"artifact": "framer", "items": len(raw_day["items"]), "skipped_ungrounded": len(skipped), "cost_usd": round(cost, 4)}
    return runs.checkpointed(conn, week, "framer", now, work)


def gates_stage(conn, client, week: str, now: datetime, profile: dict) -> dict:
    def work(attempt: int) -> dict:
        art = runs.load_artifact(conn, week, "framer")
        raw_day, framing = art["raw_day"], art["framing"]
        verdicts, thread_verdict, usage = framer.check_faithfulness(
            client, raw_day, framing, user_id=profile["user_id"], phase="faithfulness")
        day = framer.assemble_day(profile, raw_day, framing, verdicts, thread_verdict)
        failures = (gates.completeness_failures(raw_day, framing, verdicts, thread_verdict)
                    + gates.structural_failures(profile, day, framing) + gates.faithfulness_failures(day))
        runs.save_artifact(conn, week, "gates", attempt, {"day": day, "failures": failures}, now)
        if failures:
            raise GateFailed(f"{len(failures)} hard-gate failure(s): " + "; ".join(failures)[:350])
        return {"artifact": "gates", "items": len(day["items"]),
                "check_cost_usd": round(budget.estimate_cost_usd(llm.CHECK_MODEL, usage), 4)}
    return runs.checkpointed(conn, week, "gates", now, work)


def publish_stage(conn, week: str, now: datetime) -> dict:
    def work(attempt: int) -> dict:
        day = runs.load_artifact(conn, week, "gates")["day"]
        with conn.transaction():
            existing = briefs.get(conn, week)
            if existing is None:
                briefs.publish(conn, week, week, day, now)
            elif existing[1] != day:
                # A different brief is already live for this week: never overwrite, never guess.
                raise RuntimeError(f"a different brief is already published for {week}")
            # else: a previous attempt published this exact brief and died before recording
            # the stage as done. Publishing is idempotent, so carry on to the citations.
            for item in day["items"]:
                # The excerpt behind every cited story is evidence: pinned, kept forever.
                row = conn.execute("SELECT excerpt_id FROM items WHERE id = %s", (item["id"],)).fetchone()
                if row and row[0]:
                    excerpts.cite(conn, row[0], week)
        return {"brief": week, "items": len(day["items"])}
    return runs.checkpointed(conn, week, "publish", now, work)


def run_week(conn, client, now: datetime, week: str | None = None, *, stop_after: str | None = None) -> dict:
    """Run, or resume, one week. Returns each stage's output; stops early only when asked."""
    week = week or week_key(now)
    profile = framer.load_json(framer.PROFILE_PATH)
    runs.open_run(conn, week, now)
    outputs = {}
    plan = [
        ("reader", lambda: reader.run_stage(conn, client, now, week, profile=profile)),
        ("framer", lambda: framer_stage(conn, client, week, now, profile)),
        ("gates", lambda: gates_stage(conn, client, week, now, profile)),
        ("publish", lambda: publish_stage(conn, week, now)),
    ]
    for stage, go in plan:
        outputs[stage] = go()
        if stop_after == stage:
            break
    return outputs


def status(conn, week: str) -> list[str]:
    """One line per attempt at each stage of a week, for a person to read."""
    rows = conn.execute(
        "SELECT stage, attempt, status, started_at, finished_at, coalesce(error, '') FROM run_stages WHERE run_id = %s "
        "ORDER BY array_position(ARRAY['reader', 'framer', 'gates', 'publish'], stage), attempt", (week,)).fetchall()
    if not rows:
        return [f"{week}: no run yet"]
    run = conn.execute("SELECT status FROM runs WHERE id = %s", (week,)).fetchone()[0]
    lines = [f"{week}: run {run}"]
    for stage, attempt, state, started, finished, error in rows:
        took = f"{(finished - started).total_seconds():.0f}s" if finished else "-"
        lines.append(f"  {stage:<8} attempt {attempt}  {state:<8} {took:>5}  {error[:90]}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--week", help="ISO week key like 2026-W42 (default: this week)")
    parser.add_argument("--status", action="store_true", help="print the week's stages and exit; no model call")
    parser.add_argument("--stop-after", choices=runs.STAGES, help="stop cleanly after this stage")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.status:
        with budget.connect() as conn:
            print("\n".join(status(conn, args.week or week_key(now))))
        return 0
    try:
        client = llm.build_client()
        with budget.connect() as conn:
            week = args.week or week_key(now)
            before = runs.next_stage(conn, week) if conn.execute("SELECT 1 FROM runs WHERE id = %s", (week,)).fetchone() else "reader"
            print(f"week {week}: starting at {before or 'nothing (already complete)'}")
            out = run_week(conn, client, now, week, stop_after=args.stop_after)
    except (budget.BudgetExceeded, budget.LedgerUnavailable, GateFailed, runs.StageBusy) as stop:
        print(f"stopped: {stop}", file=sys.stderr)
        return 1
    for stage, o in out.items():
        brief = {k: v for k, v in o.items() if k != "items" or not isinstance(v, list)}
        print(f"  {stage}: {brief if stage != 'reader' else o['stats']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
