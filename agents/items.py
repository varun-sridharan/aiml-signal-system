"""Items: one record per canonical URL, with where it was seen and the text it rests on.

Reader creates an item when it discovers a URL, appends a provenance row for every place
that URL (or a report of it) was seen, and attaches the excerpt once the body is fetched.
The database holds the rules that must not depend on a caller remembering them (identity
is fixed, the excerpt is attached once, provenance is append-only, nothing is deleted);
this module holds the ones that need judgement: what counts as corroboration, and whether
an item may reach a reader on its own.
"""

from __future__ import annotations

import uuid
from datetime import datetime

try:
    from . import excerpts, sources
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import excerpts
    import sources


class ItemRefused(ValueError):
    """The request breaks a rule of the item store."""


def upsert(conn, url: str, title: str, now: datetime, *, source_id: str | None = None,
           relevance: tuple[int, list[str]] = (0, []), category: str | None = None) -> tuple[uuid.UUID, bool]:
    """Create the item for a URL, or return the one that exists. Returns (id, created).

    The first source to surface a URL is recorded as its source and as its 'primary'
    provenance; the same URL arriving again adds nothing here (use add_provenance).
    """
    if now.tzinfo is None:
        raise ItemRefused("now must be timezone-aware")
    if not title.strip():
        raise ItemRefused("an item needs a title")
    canonical = excerpts.canonicalise(url)
    domain = sources.domain_of(canonical)
    with conn.transaction():
        sid = source_id or sources.for_domain(conn, domain)
        row = conn.execute(
            "INSERT INTO items (id, canonical_url, domain, title, source_id, discovered_at, status, "
            "relevance_score, relevance_terms, category) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'discovered', %s, %s, %s) "
            "ON CONFLICT (canonical_url) DO NOTHING RETURNING id",
            (uuid.uuid4(), canonical, domain, title.strip()[:500], sid, now, relevance[0], relevance[1], category),
        ).fetchone()
        if row is not None:
            conn.execute("INSERT INTO item_sources (item_id, url, source_id, role, seen_at) "
                         "VALUES (%s, %s, %s, 'primary', %s)", (row[0], canonical, sid, now))
            return row[0], True
        return conn.execute("SELECT id FROM items WHERE canonical_url = %s", (canonical,)).fetchone()[0], False


def add_provenance(conn, item_id: uuid.UUID, url: str, now: datetime, *, source_id: str | None = None,
                   role: str = "corroboration") -> bool:
    """Record that another page reports this item. Returns False if already recorded."""
    canonical = excerpts.canonicalise(url)
    with conn.transaction():
        sid = source_id or sources.for_domain(conn, sources.domain_of(canonical))
        cur = conn.execute(
            "INSERT INTO item_sources (item_id, url, source_id, role, seen_at) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING", (item_id, canonical, sid, role, now))
    return cur.rowcount == 1


def mark(conn, item_id: uuid.UUID, status: str, *, error: str | None = None) -> None:
    """Move an item to 'skipped' or 'failed' (fetched goes through attach_excerpt)."""
    if status not in ("discovered", "skipped", "failed"):
        raise ItemRefused(f"mark() does not set {status!r}")
    conn.execute("UPDATE items SET status = %s, fetch_error = %s WHERE id = %s",
                 (status, (error or None) and " ".join(error.split())[:500], item_id))


def attach_excerpt(conn, item_id: uuid.UUID, excerpt_id: uuid.UUID) -> None:
    """Attach the item's text, once, and mark it fetched.

    The excerpt must be the text of THIS item's page: an excerpt stored under another
    URL would put one article's words under another's headline.
    """
    with conn.transaction():
        row = conn.execute(
            "SELECT i.canonical_url, e.canonical_url FROM items i, excerpts e WHERE i.id = %s AND e.id = %s",
            (item_id, excerpt_id)).fetchone()
        if row is None:
            raise ItemRefused("no such item or excerpt")
        if row[0] != row[1]:
            raise ItemRefused(f"excerpt is for {row[1]}, not {row[0]}")
        conn.execute("UPDATE items SET excerpt_id = %s, status = 'fetched', fetch_error = NULL WHERE id = %s",
                     (excerpt_id, item_id))


def corroboration(conn, item_id: uuid.UUID) -> dict:
    """How well an item is attested: the distinct domains that report it, by tier."""
    rows = conn.execute(
        "SELECT s.id, s.tier, s.domain, i.role FROM item_sources i JOIN sources s ON s.id = i.source_id "
        "WHERE i.item_id = %s", (item_id,)).fetchall()
    ids = {r[0] for r in rows}
    return {
        "sources": len(ids),
        "has_high_prior": any(r[1] in ("A", "B") for r in rows),
        "tiers": sorted({r[1] for r in rows}),
    }


def surfaceable(conn, item_id: uuid.UUID) -> bool:
    """May this item reach a reader?

    Per config/scout-sources.md: high-prior sources (tier A and B) can surface on their
    own; a medium-prior newsletter needs a second source; low-prior aggregators and
    unrated domains are discovery and corroboration only, never surfaced alone. So:
    a high-prior source reports it, or at least two distinct sources do and one of
    them is not low-prior or unrated.
    """
    rows = conn.execute(
        "SELECT DISTINCT s.id, s.tier FROM item_sources i JOIN sources s ON s.id = i.source_id "
        "WHERE i.item_id = %s", (item_id,)).fetchall()
    tiers = [r[1] for r in rows]
    if any(t in ("A", "B") for t in tiers):
        return True
    return len(rows) >= 2 and any(t == "C" for t in tiers)
