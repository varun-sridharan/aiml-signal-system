"""The Weekly Digest page's view of a published brief.

A brief is the Framer's output as stored in `briefs.body`. This turns it into what the
page shows, and refuses a malformed one with DigestMalformed so the route can render an
error panel instead of a 500: a bad brief in the database must not take the page down.

Mapping, decided here because the Framer (ported unchanged) does not know the page:
  Learning for the Week   the items tagged DO THIS: something to try, with its worked
                          example and 90-second summary when the Framer wrote them
  This Week's Stories     every other item, under the week's thread
"""

from __future__ import annotations

import hashlib
import os
import sys
from urllib.parse import urlsplit

MAX_ITEMS = 30


class DigestMalformed(ValueError):
    """The stored brief cannot be shown."""


def _text(value, field: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise DigestMalformed(f"{field} is missing")
        return ""
    if not isinstance(value, str):
        raise DigestMalformed(f"{field} is not text")
    if required and not value.strip():
        raise DigestMalformed(f"{field} is empty")
    return value.strip()


def _safe_url(url) -> str | None:
    """Only http(s) links are shown as links: a stored `javascript:` URL must not become one."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:  # e.g. "http://[x": a stored URL must never be able to raise here
        return None
    return url if parts.scheme in ("http", "https") and parts.netloc else None


def _item(raw) -> dict:
    if not isinstance(raw, dict):
        raise DigestMalformed("an item is not an object")
    ident = _text(raw.get("id"), "item id", required=True)
    if len(ident) > 200:
        raise DigestMalformed("an item id is too long")
    ninety = raw.get("ninety")
    ninety_rows = []
    if isinstance(ninety, dict):
        # jsonb does not keep key order (it sorts them), so the 90-second summary is put back in
        # reading order: what it does, what it found, what to doubt. Unknown keys follow.
        order = {"method": 0, "result": 1, "caveat": 2}
        ninety_rows = [(str(k).replace("_", " ").capitalize(), _text(v, f"{ident}/ninety.{k}"))
                       for k, v in sorted(ninety.items(), key=lambda kv: (order.get(kv[0], 9), kv[0])) if v]
    elif ninety:
        raise DigestMalformed(f"{ident}/ninety is not an object")
    sources = []
    raw_sources, raw_tags = raw.get("sources") or [], raw.get("tags") or []
    if not isinstance(raw_sources, list) or not isinstance(raw_tags, list):
        raise DigestMalformed(f"{ident}: sources and tags must be lists")
    for s in raw_sources:
        if isinstance(s, dict) and _safe_url(s.get("url")):
            sources.append({"label": _text(s.get("label"), "source label") or urlsplit(s["url"]).netloc,
                            "url": s["url"], "tier": s.get("tier") if s.get("tier") in ("primary", "corroboration") else "corroboration"})
    tags = [t for t in raw_tags if isinstance(t, str)]
    faith = raw.get("faithfulness") if isinstance(raw.get("faithfulness"), dict) else {}
    return {
        "id": ident,
        "headline": _text(raw.get("headline"), f"{ident}/headline", required=True),
        "tags": tags,
        "why": _text(raw.get("why"), f"{ident}/why"),
        "example": _text(raw.get("example"), f"{ident}/example"),
        "connection": _text(raw.get("connection"), f"{ident}/connection"),
        "ninety": ninety_rows,
        "sources": sources,
        "checked": not faith.get("unsupported_claims") and faith.get("confidence") is not None,
    }


def view(brief_id: str, body, published_at=None) -> dict:
    """The page's content for one brief. Raises DigestMalformed if it cannot be shown.

    Whatever is wrong with a stored brief, this raises DigestMalformed and nothing else: the
    page and the vote route catch that one exception and show a panel instead of a 500.
    """
    try:
        return _view(brief_id, body, published_at)
    except DigestMalformed:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:
        raise DigestMalformed(f"unexpected shape ({type(e).__name__})") from None


def _view(brief_id: str, body, published_at) -> dict:
    if not isinstance(body, dict):
        raise DigestMalformed("the brief is not an object")
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise DigestMalformed("the brief has no items")
    if len(raw_items) > MAX_ITEMS:
        raise DigestMalformed("the brief has too many items")
    items = [_item(i) for i in raw_items]
    if len({i["id"] for i in items}) != len(items):
        raise DigestMalformed("two items share an id")
    reading = body.get("readingTimeMin")
    return {
        "week": brief_id,
        "published_at": published_at,
        "thread": _text(body.get("thread"), "thread"),
        "reading_time": reading if isinstance(reading, int) and not isinstance(reading, bool) and 0 < reading < 120 else None,
        "learning": [i for i in items if "DO THIS" in i["tags"]],
        "stories": [i for i in items if "DO THIS" not in i["tags"]],
        "item_ids": {i["id"] for i in items},
    }


def vote_salt() -> str:
    """The secret mixed into every stored voter hash.

    VOTE_SALT if the deploy sets one; otherwise derived from DATABASE_URL, which is already
    a long, stable secret the app holds, so voting works without a new setting. The salt
    never leaves the process and never appears in a response or a log.
    """
    explicit = os.environ.get("VOTE_SALT", "").strip()
    if len(explicit) >= 16:
        return explicit
    if explicit:
        print("VOTE_SALT is set but shorter than 16 characters; ignoring it", file=sys.stderr)
    secret = os.environ.get("DATABASE_URL", "")
    return hashlib.sha256(("vote-salt:" + secret).encode("utf-8")).hexdigest() if secret else ""


def client_address(headers, peer: str | None) -> str:
    """The address to hash: the last X-Forwarded-For entry (added by the proxy nearest us),
    else the socket peer. Hashed immediately by the caller; never stored or logged."""
    forwarded = headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    return parts[-1] if parts else (peer or "unknown")
