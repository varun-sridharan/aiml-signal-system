"""How many things the Reader read for the latest edition, and how many it published: the measured card in the Product page's stats band.

Run 9 put this number in the hero; Run 9's follow-up (2026-10-28) moved it into the stats band and took the number out of the title. The count
is the Reader's own: the items it discovered, from `items.discovered_at`, inside the dates the latest published edition covers (`body.window`),
the number of distinct sources that produced them, and the number of stories the edition published. One query per edition (a new edition has a
new id), no model call. If the database cannot answer, or the count is under MIN_COUNT, there is no result and the page shows the fixed card
("7 items in the latest edition"): never an invented figure.

Three source counts exist and mean different things, and every page that gives one says which: `feeds` (the feeds and index pages the Reader reads
each run, a constant in agents/reader.py), `listed` (the rows of the `sources` table: every source it knows, which grows as it finds new domains)
and `sources` (the listed sources that produced items inside this window). The Product page's stats note and System Design's Reader row take
all three from here, so they cannot disagree.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from . import briefs, digest, reader

MIN_COUNT = 50
RETRY_AFTER_S = 60                       # a database that did not answer is asked again after this long, not on every request

_cache: dict = {}                        # brief id -> the result (or None when the count is under MIN_COUNT)
_failed_at: list = [None]


def reset() -> None:
    _cache.clear()
    _failed_at[0] = None


def window_of(body: dict, published_at) -> tuple:
    """(start, end) of the dates the edition covers: the window its Reader read, else the publish date and the Reader's look-back."""
    window = body.get("window") if isinstance(body, dict) else None
    if isinstance(window, dict) and window.get("from") and window.get("to"):
        return datetime.fromisoformat(window["from"]), datetime.fromisoformat(window["to"])
    return published_at - reader.WINDOW, published_at


def count_window(conn, start, end) -> tuple:
    """(items discovered in [start, end), distinct sources they came from)."""
    n, sources = conn.execute("SELECT count(*), count(DISTINCT source_id) FROM items WHERE discovered_at >= %s AND discovered_at < %s", (start, end)).fetchone()
    return int(n), int(sources)


def for_latest(conn) -> dict | None:
    """{"count", "sources", "listed", "published", "feeds", "dates"} for the latest published edition, or None when there is nothing honest to say."""
    row = briefs.latest(conn)
    if row is None:
        return None
    brief_id, body, published_at = row[0], row[1], row[2]
    if brief_id in _cache:
        return _cache[brief_id]
    body = body if isinstance(body, dict) else json.loads(body)
    start, end = window_of(body, published_at)
    n, sources = count_window(conn, start, end)
    stories = len(body["items"]) if isinstance(body.get("items"), list) else 0
    listed = int(conn.execute("SELECT count(*) FROM sources").fetchone()[0])
    result = ({"count": n, "sources": sources, "listed": listed, "published": stories, "feeds": len(reader.FEEDS),
               "dates": digest.edition_label(body, published_at) or ""}
              if n >= MIN_COUNT and stories else None)
    _cache.clear()
    _cache[brief_id] = result
    return result


def hero(connect) -> dict | None:
    """The hero's number, from `connect()` (a context manager yielding a connection); None on any failure or a count under MIN_COUNT."""
    now = time.monotonic()
    if _failed_at[0] is not None and now - _failed_at[0] < RETRY_AFTER_S:
        return None
    try:
        with connect() as conn:
            out = for_latest(conn)
        _failed_at[0] = None
        return out
    except Exception:
        _failed_at[0] = now
        return None


def window_clause(result) -> str:
    """The clause System Design's Reader row adds after "17 feeds and index pages", from the same numbers as the Product page's note."""
    if not result:
        return ""
    return f"; of the {result['listed']} sources it lists, {result['sources']} produced items in the latest edition's window"
