"""The weekly capture import: links saved with /AGI-add-news join the week's candidates (Run 6, Step C).

Varun saves links to the project-memory file `news-backlog.md`. A scheduled task in the Claude app reads that file before the Friday run and
posts it to `POST /add-news/batch` (docs/capture-import.md), which calls `import_batch`. This module is **free**: it fetches nothing, calls no
model and books nothing in the spend ledger. Fetching happens in the weekly run, which finds these items like any others, ahead of the feeds'
(BACKLOG_SCORE) and flagged "from your backlog" on the page.

Each link goes through the dedup layers that need no page: the address after safe clean-up, the identity of a paper (arXiv or DOI) and the
sites that refuse fetchers. The layers that read text (fingerprint, model) run later, in the weekly Reader, over what was fetched. Posting the
same batch twice changes nothing.

Outcomes, per link: `added`, `merged` (the same paper as an item already held: kept as another sighting of it), `already-known`, `rejected`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
import ipaddress
import re
from datetime import timedelta
from urllib.parse import urlsplit

try:
    from . import dedupe, excerpts, items, sources
except ImportError:  # pragma: no cover
    import dedupe
    import excerpts
    import items
    import sources

BACKLOG_TERM = "from your backlog"      # in items.relevance_terms; the Framer's input carries it as fromBacklog
BACKLOG_SCORE = 10                      # the top of the scale (2 to 10): Varun chose these, so they are fetched first and rank with the best
MAX_LINKS = 100
MAX_URL_CHARS = 250
WINDOW = timedelta(days=8)              # reader.WINDOW: an item first seen longer ago than this is not in a weekly run's reach
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")


def address_problem(text: str):
    """Why a link is not a public web address worth fetching, or None. No credentials, no odd ports, no local or numeric hosts."""
    try:
        parts = urlsplit(text)
        host, port = (parts.hostname or "").lower().rstrip("."), parts.port
    except ValueError:
        return "not a valid address"
    if parts.scheme not in ("http", "https") or not host:
        return f"not an absolute http(s) address of at most {MAX_URL_CHARS} characters"
    if parts.username is not None or parts.password is not None:
        return "an address with a user name or password is not accepted"
    if port not in (None, 80, 443):
        return "only the standard web ports are accepted"
    try:
        ipaddress.ip_address(host.strip("[]"))
        return "an address given as an IP number is not accepted"
    except ValueError:
        pass
    if not _HOST.fullmatch(host) or host.endswith((".local", ".internal", ".localhost")):
        return "the host name is not a public domain name"
    return None


def story_of(conn, item_id) -> dict | None:
    """Where an item already appears in a published edition: {'week': '2026-W44', 'story': 's03'}, or None."""
    row = conn.execute(
        "SELECT b.id, s ->> 'id' FROM briefs b, jsonb_array_elements(CASE WHEN jsonb_typeof(b.body #> '{edition,stories}') = 'array' "
        "THEN b.body #> '{edition,stories}' ELSE '[]'::jsonb END) s WHERE s ->> 'itemId' = %s ORDER BY b.published_at DESC LIMIT 1",
        (str(item_id),)).fetchone()
    return {"week": row[0], "story": row[1]} if row else None


def _flag_existing(conn, hit, now: datetime) -> str:
    """A saved link an earlier feed already found: flag it and give it the backlog priority, if it can still be read this week."""
    iid, _url, status, found = hit
    reachable = status in ("discovered", "fetched") and now - found <= WINDOW
    if reachable:
        conn.execute("UPDATE items SET relevance_score = GREATEST(relevance_score, %s), "
                     "relevance_terms = CASE WHEN %s = ANY(relevance_terms) THEN relevance_terms ELSE array_append(relevance_terms, %s) END "
                     "WHERE id = %s", (BACKLOG_SCORE, BACKLOG_TERM, BACKLOG_TERM, iid))
        return f"already held, status {status}; flagged from your backlog for this week"
    return f"already held, status {status}; it is not within this week's reach, so it is not read again"


def import_batch(conn, links, now: datetime) -> dict:
    """Add a list of `{url, addedAt, note}` to the week's candidates and report what happened to each. No fetch, no paid call."""
    if not isinstance(links, list):
        raise ValueError("send a JSON list of {url, addedAt, note}")
    if len(links) > MAX_LINKS:
        raise ValueError(f"at most {MAX_LINKS} links in one batch")
    report, seen = [], set()
    blocked = sources.blocked_domains()
    known = conn.execute("SELECT id, canonical_url, status, discovered_at FROM items").fetchall()
    by_url = {r[1]: r for r in known}
    for entry in links:
        raw = entry.get("url") if isinstance(entry, dict) else entry
        row = {"url": str(raw)[:MAX_URL_CHARS]}
        report.append(row)
        text = str(raw).strip() if isinstance(raw, str) else ""
        problem = address_problem(text) if text and len(text) <= MAX_URL_CHARS else f"not an absolute http(s) address of at most {MAX_URL_CHARS} characters"
        if problem:
            row.update(outcome="rejected", detail=problem)
            continue
        try:
            canonical = excerpts.canonicalise(text)
        except excerpts.ExcerptRefused as e:
            row.update(outcome="rejected", detail=str(e)[:120])
            continue
        row["canonical"] = canonical
        if sources.is_blocked(sources.domain_of(canonical), blocked):
            row.update(outcome="rejected", detail="this site refuses fetchers; capture a syndicated copy")
            continue
        if canonical in seen:
            row.update(outcome="already-known", detail="listed twice in this batch")
            continue
        seen.add(canonical)
        hit = by_url.get(canonical)
        if hit:
            row.update(outcome="already-known", item=str(hit[0]), detail=_flag_existing(conn, hit, now))
            continue
        ident = dedupe.identity_of(canonical)
        twin = next((r for r in known if ident and dedupe.identity_of(r[1]) == ident), None)
        if twin:
            fresh = items.add_provenance(conn, twin[0], canonical, now)
            where = story_of(conn, twin[0])
            row.update(outcome="merged" if fresh else "already-known", item=str(twin[0]), into=str(twin[0]),
                       detail=f"the same {ident[0]} id as an item already held; " + ("kept as another sighting of it" if fresh else "already recorded as a sighting"))
            if where:
                row["story"] = where
            continue
        iid, created = items.upsert(conn, canonical, canonical, now, relevance=(BACKLOG_SCORE, [BACKLOG_TERM]))
        if not created:                           # another request got there between the read and the write
            row.update(outcome="already-known", detail="already held", item=str(iid))
            continue
        known.append((iid, canonical, "discovered", now))
        by_url[canonical] = (iid, canonical, "discovered", now)
        row.update(outcome="added", detail="joins this week's candidates, flagged from your backlog; it is fetched in the weekly run", item=str(iid))
    totals = {k: sum(1 for r in report if r["outcome"] == k) for k in ("added", "merged", "already-known", "rejected")}
    return {"received": len(report), **{k.replace("-", "_"): v for k, v in totals.items()}, "links": report}
