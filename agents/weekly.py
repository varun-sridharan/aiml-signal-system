"""The weekly run: Reader, then the Framer, then the gates, then publish.

    python -m agents.weekly                    # this ISO week: start it, or resume its latest attempt
    python -m agents.weekly --week 2026-W42
    python -m agents.weekly --stop-after reader   # stop cleanly after a stage (resume demo)
    python -m agents.weekly --week 2026-W43 --new-attempt                       # a fresh attempt, 2026-W43-r2, from the Reader
    python -m agents.weekly --week 2026-W43 --reuse-reader 2026-W43             # ...reframing what that attempt's Reader found
    python -m agents.weekly --week 2026-W43 --status                            # every attempt at the week

Each stage is checkpointed in the `runs` tables (agents/runs.py). A rerun of an attempt loads
every stage that already finished and continues at the first that did not, so a failed
Framer does not re-fetch the sources or pay for the Reader again. A finished stage is never
redone, so a week whose gates failed is reframed as a NEW attempt (-r2, -r3), which may adopt
the earlier attempt's Reader output. A week publishes at most one edition. The Framer and its gates
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
    from . import briefs, budget, composer, excerpts, framer, gates, items, llm, reader, runs, style_check
except ImportError:  # pragma: no cover
    import briefs
    import budget
    import composer
    import excerpts
    import framer
    import gates
    import items
    import llm
    import reader
    import runs
    import style_check

MAX_BRIEF_ITEMS = 7        # the golden week's size: about five minutes of reading
MAX_PER_DOMAIN = 3         # one publisher cannot be the whole edition
MAX_DO_THIS = 2
LEAD_MAX_CHARS = 700       # what the Framer is shown of an article: its opening sentences, not the page
LEAD_MAX_SENTENCES = 4     # the contract says 1-3 factual sentences; the golden excerpts run 248-393 characters
LEAD_MIN_PARAGRAPH_CHARS = 80
# The composing pass writes features of 300 to 500 words, which a 700-character opening cannot support. It is shown more of
# each page, and its faithfulness gate is graded against exactly that text.
COMPOSER_EXCERPT_CHARS = 6000
COMPOSER_EXCERPT_SENTENCES = 60

# Where a practitioner can act on what they read; items from these on a core topic are DO THIS.
PRACTICE_DOMAINS = {"github.blog", "blog.modelcontextprotocol.io"}
# ...and only on topics a practitioner can try. A GitHub post that merely mentions "agents" matches the
# core multi-agent topic through a generic word, and must not take one of the two DO THIS slots.
PRACTICE_TOPICS = {"MCP architecture", "agentic coding", "harness engineering"}
CORE_TOPICS_USER = "varun"


class AlreadyPublished(RuntimeError):
    """The week already has its edition; a second attempt may not publish another."""


class GateFailed(RuntimeError):
    """The brief failed a hard gate and was not published."""


def week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def week_start(week: str) -> date:
    year, number = re.fullmatch(r"(\d{4})-W(\d{2})", runs.base_week(week)).groups()
    return date.fromisocalendar(int(year), int(number), 1)


# --------------------------------------------------------------------------------------
# From a fetched page to the Framer's source_excerpt
# --------------------------------------------------------------------------------------

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _plain(paragraph: str) -> str:
    return " ".join(_LINK.sub(lambda m: m.group(1), paragraph).replace("**", "").replace("`", "").split())


_BOILERPLATE = ("arxivlabs", "cookie", "subscribe", "newsletter", "sign up", "all rights reserved", "privacy policy",
                "skip to content", "opens in a new window", "authors:", "share this", "follow us")


def lead(text: str, max_chars: int = LEAD_MAX_CHARS, prefer_meta: bool = False, max_sentences: int = LEAD_MAX_SENTENCES) -> str:
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
    for sentence in sentences[:max_sentences]:
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


def build_raw_day(conn, week: str, item_ids: list[str], profile: dict, skipped: list | None = None,
                  as_of: date | None = None) -> dict:
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
    # The edition is dated the day it was made: what a run reads is the last days up to now,
    # whatever its week key says, so the key's Monday would date the brief wrongly.
    return {"date": (as_of or week_start(week)).isoformat(), "items": chosen}


# --------------------------------------------------------------------------------------
# The stages
# --------------------------------------------------------------------------------------

# Curly quotes and apostrophes, and the hyphen look-alikes U+2010 to U+2012, shown as the plain marks, and no-break spaces as spaces. The
# graders quote with plain marks (one turned a non-breaking hyphen into a line break), and the span check (rightly) compares literally, so a
# correct quote of "repository’s" or "GPT‑6" failed for the mark alone. The composing pass is shown this rendering of
# the page and is graded against the same text; the stored excerpt is untouched. Decision log, 2026-10-20 (Run 3).
_STRAIGHT = {0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"',
             0x2010: "-", 0x2011: "-", 0x2012: "-", 0x00A0: " "}


def straight_quotes(text: str) -> str:
    return text.translate(_STRAIGHT)


def composer_day(conn, raw_day: dict) -> tuple[dict, dict, int]:
    """The day as the composing pass sees it: each story's fuller excerpt, the dates its pages gave, and the source minutes.

    The first pass keeps its short opening (it is the one that shipped and was calibrated). The composing pass writes
    features and an essay, so it is shown up to COMPOSER_EXCERPT_CHARS of each page, and is graded against that same text.
    """
    wide, published, words = [], {}, 0
    for item in raw_day["items"]:
        eid, domain = conn.execute("SELECT excerpt_id, domain FROM items WHERE id = %s", (item["id"],)).fetchone()
        text = excerpts.get(conn, eid)
        words += len(text.split())
        dated = reader.page_date(text)
        if dated is not None:
            published[item["id"]] = dated.date().isoformat()
        longer = lead(text, COMPOSER_EXCERPT_CHARS, prefer_meta=(domain == "arxiv.org"), max_sentences=COMPOSER_EXCERPT_SENTENCES)
        wide.append({**item, "source_excerpt": straight_quotes(longer if len(longer) >= len(item["source_excerpt"]) else item["source_excerpt"])})
    return {**raw_day, "items": wide}, published, round(words / framer.WORDS_PER_MINUTE)


def framer_stage(conn, client, week: str, now: datetime, profile: dict) -> dict:
    def work(attempt: int) -> dict:
        reader_out = runs.stage_output(conn, week, "reader")
        skipped: list = []
        raw_day = build_raw_day(conn, week, reader_out["items"], profile, skipped, as_of=runs._at(now).date())
        for note in skipped:
            print(f"  not framed — {note}", file=sys.stderr)
        if not raw_day["items"]:
            raise RuntimeError("no verified, readable items this week; nothing to frame")
        framing, _usage, cost = framer.frame_day(client, framer.load_framer_system_prompt(), profile, raw_day)
        # The second pass writes the template's fields (thread, essay, stories, features, closing); see agents/composer.py.
        wide_day, published, minutes = composer_day(conn, raw_day)
        label = (reader_out.get("window") or {}).get("label", "")
        # A failure of the composing pass (a truncated answer, say) must not throw away the first pass that was paid for: the
        # artifact records it, and the gates stage fails closed on it, so no edition publishes without its template fields.
        composed, revision, composer_cost, composer_error = None, None, 0.0, None
        try:
            composed, revision, composer_cost = composer.compose_with_revision(
                client, composer.load_system_prompt(), profile, wide_day, framing, label, published)
        except (budget.BudgetExceeded, budget.LedgerUnavailable):
            raise
        except Exception as e:     # noqa: BLE001
            composer_error = f"{type(e).__name__}: {str(e)[:200]}"
        runs.save_artifact(conn, week, "framer", attempt, {"raw_day": raw_day, "framing": framing, "composing": "on", "composed": composed,
                                                            "composer_error": composer_error, "wide_day": wide_day, "published": published,
                                                            "source_minutes": minutes, "revision": revision}, runs._at(now))
        return {"artifact": "framer", "items": len(raw_day["items"]), "skipped_ungrounded": len(skipped),
                "cost_usd": round(cost + composer_cost, 4), "composer_cost_usd": round(composer_cost, 4),
                "revised_fields": len(revision["replaced"]) if revision else 0, "composer_error": composer_error}
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
        reader_out = runs.stage_output(conn, week, "reader") or {}
        composer_check_cost = 0.0
        if art.get("composing") == "on" and not art.get("composed"):
            failures.append(f"the composing pass produced nothing ({art.get('composer_error') or 'no output'}), so there are no template fields to publish")
        if art.get("composed"):          # attempts framed before the composing pass have no edition fields
            wide_day, published = art["wide_day"], art.get("published") or {}
            cverdicts, cweek, cusage, cpayload = composer.check_faithfulness(
                client, art["composed"], wide_day, published, user_id=profile["user_id"])
            failures += composer.edition_failures(art["composed"], wide_day, cpayload, cverdicts, cweek)
            day["edition"] = composer.assemble_edition(
                art["composed"], wide_day, framing, cverdicts, cweek, stats=reader_out.get("stats") or {},
                source_minutes=art.get("source_minutes", 0), style=style_check.edition_style_failures(art["composed"]), published=published)
            composer_check_cost = budget.estimate_cost_usd(llm.CHECK_MODEL, cusage)
        window = reader_out.get("window")
        if window:
            day["window"] = window       # what the edition covers, by what its Reader read; the page labels it with this
        runs.save_artifact(conn, week, "gates", attempt, {"day": day, "failures": failures}, runs._at(now))
        if failures:
            raise GateFailed(f"{len(failures)} hard-gate failure(s): " + "; ".join(failures)[:350])
        return {"artifact": "gates", "items": len(day["items"]),
                "check_cost_usd": round(budget.estimate_cost_usd(llm.CHECK_MODEL, usage) + composer_check_cost, 4)}
    return runs.checkpointed(conn, week, "gates", now, work)


def refuse_second_edition(conn, run_id: str) -> None:
    """A week publishes at most one edition: another attempt's brief is final."""
    held = conn.execute("SELECT run_id FROM briefs WHERE id = %s", (runs.base_week(run_id),)).fetchone()
    if held is not None and held[0] != run_id:
        raise AlreadyPublished(f"{runs.base_week(run_id)} was already published by {held[0]}; "
                               f"{run_id} may not publish a second edition")


def publish_stage(conn, run_id: str, now: datetime) -> dict:
    brief_id = runs.base_week(run_id)       # the edition is stored under the bare week, whichever attempt made it

    def work(attempt: int) -> dict:
        day = runs.load_artifact(conn, run_id, "gates")["day"]
        with conn.transaction():
            refuse_second_edition(conn, run_id)
            existing = briefs.get(conn, brief_id)
            if existing is None:
                briefs.publish(conn, brief_id, run_id, day, runs._at(now))
            elif existing[1] != day:
                # A different brief is already live for this week: never overwrite, never guess.
                raise RuntimeError(f"a different brief is already published for {brief_id}")
            # else: a previous try of this same attempt published this exact brief and died before
            # recording the stage as done. Publishing is idempotent, so carry on to the citations.
            for item in day["items"]:
                # The excerpt behind every cited story is evidence: pinned, kept forever.
                row = conn.execute("SELECT excerpt_id FROM items WHERE id = %s", (item["id"],)).fetchone()
                if row and row[0]:
                    excerpts.cite(conn, row[0], brief_id)
        return {"brief": brief_id, "items": len(day["items"])}
    return runs.checkpointed(conn, run_id, "publish", now, work)


def resolve_run(conn, week: str, *, new_attempt: bool = False) -> str:
    """Which run a command about `week` means. `week` may be a week key or one attempt's id.

    A bare week key means "carry on with this week": the latest attempt, if it is still
    unfinished, else the first attempt of a week with none. A finished week is never reopened
    by accident; a fresh attempt is asked for (new_attempt), and takes the next free number.
    """
    base, number = runs.parse_run_id(week)
    existing = runs.attempts(conn, base)
    if number > 1:                                       # an attempt named outright: it exists, or it is exactly the next
        if week not in existing and week != runs.next_attempt_id(conn, base):
            raise runs.RunRefused(f"{week} would leave a gap: the next attempt at {base} is {runs.next_attempt_id(conn, base)}")
        return week
    if not existing:
        return base
    if new_attempt:
        return runs.next_attempt_id(conn, base)
    return existing[-1]


def run_week(conn, client, now: datetime, week: str | None = None, *, stop_after: str | None = None, clock=None,
             reuse_reader: str | None = None) -> dict:
    """Run, or resume, one attempt at a week. Returns each stage's output; stops early only when asked.

    `week` is the run id: a week key, or one attempt's id (2026-W43-r2). `reuse_reader` names an
    earlier attempt at the same week whose finished Reader output this one adopts (no fetch,
    no cost), so a reframe sees exactly the evidence the failed attempt saw. `now` fixes the
    Reader's window; `clock` (a zero-argument callable) stamps each stage when it actually
    starts and ends. Without a clock every stamp is `now`, which is what tests want.
    """
    week = week or week_key(now)
    stamp = clock or now
    profile = framer.load_json(framer.PROFILE_PATH)
    refuse_second_edition(conn, week)                    # before any paid call, not after
    if reuse_reader:                                     # validated before a run row exists: a typo must not leave an empty attempt
        runs.adoptable_output(conn, week, reuse_reader, "reader")
    runs.open_run(conn, week, runs._at(stamp))
    if reuse_reader:
        if runs.next_stage(conn, week) != "reader":
            raise runs.RunRefused(f"{week} has already finished its Reader; --reuse-reader is for a fresh attempt")
        runs.adopt_stage(conn, week, "reader", reuse_reader, stamp)
    outputs = {}
    plan = [
        ("reader", lambda: reader.run_stage(conn, client, now, week, stamp=stamp, profile=profile)),
        ("framer", lambda: framer_stage(conn, client, week, stamp, profile)),
        ("gates", lambda: gates_stage(conn, client, week, stamp, profile)),
        ("publish", lambda: publish_stage(conn, week, stamp)),
    ]
    for stage, go in plan:
        outputs[stage] = go()
        if stop_after == stage:
            break
    return outputs


def status(conn, week: str) -> list[str]:
    """Every attempt at a week, one line per attempt at each stage, for a person to read.

    An attempt that did not publish while a later one exists is marked superseded. Failed
    attempts are kept as evidence; nothing here edits them.
    """
    base = runs.base_week(week)
    ids = runs.attempts(conn, base)
    if not ids:
        return [f"{base}: no run yet"]
    brief = conn.execute("SELECT run_id FROM briefs WHERE id = %s", (base,)).fetchone()
    lines = []
    for n, run_id in enumerate(ids):
        state = conn.execute("SELECT status FROM runs WHERE id = %s", (run_id,)).fetchone()[0]
        note = ("published the edition" if brief and brief[0] == run_id
                else f"superseded by {ids[n + 1]}" if n + 1 < len(ids) else "")
        lines.append(f"{run_id}: run {state}" + (f" ({note})" if note else ""))
        rows = conn.execute(
            "SELECT stage, attempt, status, started_at, finished_at, coalesce(error, ''), coalesce(output ->> 'reused_from', '') "
            "FROM run_stages WHERE run_id = %s "
            "ORDER BY array_position(ARRAY['reader', 'framer', 'gates', 'publish'], stage), attempt", (run_id,)).fetchall()
        for stage, attempt, st, started, finished, error, reused in rows:
            took = f"{(finished - started).total_seconds():.0f}s" if finished else "-"
            detail = f"reused from {reused}" if reused else error[:90]
            lines.append(f"  {stage:<8} attempt {attempt}  {st:<8} {took:>5}  {detail}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--week", help="ISO week key like 2026-W42, or one attempt's id like 2026-W42-r2 (default: this week)")
    parser.add_argument("--status", action="store_true", help="print every attempt at the week and exit; no model call")
    parser.add_argument("--stop-after", choices=runs.STAGES, help="stop cleanly after this stage")
    parser.add_argument("--new-attempt", action="store_true", help="start the week's next attempt (-r2, -r3...) from the Reader")
    parser.add_argument("--reuse-reader", metavar="RUN_ID", help="start a new attempt from this attempt's finished Reader output (no re-fetch)")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.status:
        with budget.connect() as conn:
            print("\n".join(status(conn, args.week or week_key(now))))
        return 0
    try:
        client = llm.build_client()
        with budget.connect() as conn:
            run_id = resolve_run(conn, args.week or week_key(now), new_attempt=args.new_attempt or bool(args.reuse_reader))
            exists = conn.execute("SELECT 1 FROM runs WHERE id = %s", (run_id,)).fetchone()
            before = "reader" if not exists or args.reuse_reader else runs.next_stage(conn, run_id)
            print(f"run {run_id}: starting at {'reader (adopting ' + args.reuse_reader + chr(39) + 's)' if args.reuse_reader else before or 'nothing (already complete)'}")
            out = run_week(conn, client, now, run_id, stop_after=args.stop_after, reuse_reader=args.reuse_reader,
                           clock=lambda: datetime.now(timezone.utc))
    except (budget.BudgetExceeded, budget.LedgerUnavailable, GateFailed, AlreadyPublished, runs.StageBusy, runs.RunRefused) as stop:
        print(f"stopped: {stop}", file=sys.stderr)
        return 1
    for stage, o in out.items():
        brief = {k: v for k, v in o.items() if k != "items" or not isinstance(v, list)}
        print(f"  {stage}: {brief if stage != 'reader' else o['stats']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
