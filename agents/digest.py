"""The Weekly Digest page's view of a published brief.

A brief is the Framer's output as stored in `briefs.body`. This turns it into what the
page shows, and refuses a malformed one with DigestMalformed so the route can render an
error panel instead of a 500: a bad brief in the database must not take the page down.

The page is the Claude Design template (application/Weekly-Digest-Template.html), server-rendered by
templates/digest.template.html. A brief written by the composing pass (agents/composer.py) carries `edition`
with every field the template's slot list asks for; one written before it (the October 18 edition) carries the
first pass's fields only. Both become the same view (edition_view below): the older one shows its thread and
its stories as they are, with no essay, hashtags or closing, and never an error.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import timezone
from urllib.parse import urlsplit

from . import reader

MAX_ITEMS = 30
MAX_LABEL_CHARS = 60


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


def edition_label(body, published_at=None) -> str | None:
    """The dates an edition covers, like 'Oct 10 \u2013 Oct 18, 2026'. Never the run's week key.

    Editions published since attempts were numbered carry the window their Reader actually read.
    Briefs are append-only, so an older one has none stored: its Reader looked back reader.WINDOW
    from the moment it ran, which is within moments of when it was published, so that is the label.
    """
    window = body.get("window") if isinstance(body, dict) else None
    label = window.get("label") if isinstance(window, dict) else None
    if isinstance(label, str) and 0 < len(label.strip()) <= MAX_LABEL_CHARS:
        return label.strip()
    if published_at is not None:
        published_at = published_at.astimezone(timezone.utc)      # the database hands it back in the session's zone
        return reader.window_label(published_at - reader.WINDOW, published_at)
    return None


def view(brief_id: str, body, published_at=None, number: int | None = None) -> dict:
    """The page's content for one brief. Raises DigestMalformed if it cannot be shown.

    Whatever is wrong with a stored brief, this raises DigestMalformed and nothing else: the
    page and the vote route catch that one exception and show a panel instead of a 500.
    """
    try:
        return _view(brief_id, body, published_at, number)
    except DigestMalformed:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:
        raise DigestMalformed(f"unexpected shape ({type(e).__name__})") from None


def _view(brief_id: str, body, published_at, number=None) -> dict:
    if not isinstance(body, dict):
        raise DigestMalformed("the brief is not an object")
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise DigestMalformed("the brief has no items")
    if len(raw_items) > MAX_ITEMS:
        raise DigestMalformed("the brief has too many items")
    if len({i.get("id") for i in raw_items if isinstance(i, dict)}) != len(raw_items):
        raise DigestMalformed("two items share an id")
    return edition_view(brief_id, body, published_at, number)


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


# ---------------------------------------------------------------- the template's view (Run 3, Step D)
#
# application/Weekly-Digest-Template.html is the design and application/Weekly-Digest-Slots.html its slot
# list. A brief written by the composing pass carries `edition` (agents/composer.py); one written before
# it (the October 18 edition) carries only the first pass's fields. Both become the same view, so the one
# template renders either, showing the fields a brief has and nothing it does not.

import re

TOKEN = re.compile(r"\[\[([^|\]\[]{1,40})\|([^\]\[]*)\]\]")       # any [[kind|text]]; a kind the page does not know shows as its text
SECTION_COLOURS = {"thread": ("var(--sec-thread)", "var(--sec-thread-ink)"), "learning": ("var(--sec-learning)", "var(--sec-learning-ink)"),
                   "stories": ("var(--sec-stories)", "var(--sec-stories-ink)"), "leaders": ("var(--sec-leaders)", "var(--sec-leaders-ink)")}
PILLS = ("DO THIS", "RADAR", "KNOW")
SAMPLE = {"label": "SAMPLE", "range": "Sample edition · week ending 2026-08-04", "l1": "Capability spread out.",
          "l2": "Capital concentrated.", "stories": 7, "href": "/weekly-digest/sample-2026-08-04"}


def segments(text: str, *, ids: set | None = None, notes: dict | None = None, order: list | None = None) -> list[dict]:
    """A prose field as runs for the template: plain text, a link to a story, a footnote marker, or a highlight.

    Tokens that point nowhere (a story that is not in the edition, a footnote with no note) become plain text, so a
    model's stray token never reaches the page as a dead link or as visible brackets.
    """
    text = text or ""
    out, last = [], 0
    for m in TOKEN.finditer(text):
        if m.start() > last:
            out.append({"kind": "plain", "text": text[last:m.start()]})
        kind, shown = m.group(1), m.group(2)
        if kind == "hl":
            out.append({"kind": "hl", "text": shown})
        elif kind.startswith("#") and ids is not None and kind[1:] in ids:
            out.append({"kind": "link", "text": shown, "href": "#" + kind[1:]})
        elif kind.startswith("fn:") and notes and kind[3:] in notes:
            key = kind[3:]
            if order is not None and key not in order:
                order.append(key)
            out.append({"kind": "fn", "text": shown, "key": key, "n": (order.index(key) + 1) if order is not None else "",
                        "term": notes[key][0], "definition": notes[key][1]})
        else:
            out.append({"kind": "plain", "text": shown})
        last = m.end()
    if last < len(text):
        out.append({"kind": "plain", "text": text[last:]})
    return out


def plain_text(text: str) -> str:
    return TOKEN.sub(lambda m: m.group(2), text or "")


def first_sentence(text: str) -> str:
    s = plain_text(text).strip()
    m = re.match(r"^.*?[.!?](\s|$)", s, re.S)
    return (m.group(0) if m else s).strip()


def range_line(window, published_at=None) -> str:
    """'OCT-10-2026 TO OCT-18-2026', from the window the Reader read, else from the publish date and the Reader's look-back."""
    from datetime import datetime
    try:
        if isinstance(window, dict) and window.get("from") and window.get("to"):
            start, end = datetime.fromisoformat(window["from"]), datetime.fromisoformat(window["to"])
        elif published_at is not None:
            end = published_at.astimezone(timezone.utc)
            start = end - reader.WINDOW
        else:
            return ""
    except (TypeError, ValueError):
        return ""
    return f"{start:%b-%d-%Y} TO {end:%b-%d-%Y}".upper()


def _str_list(value, field: str, limit: int = 40, clip: bool = False) -> list[str]:
    """A list of text. Past `limit` it is cut when `clip` (a story with five hashtags still shows) and refused otherwise."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or (len(value) > limit and not clip):
        raise DigestMalformed(f"{field} is not a list of text")
    return [v for v in value if v.strip()][:limit]


SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
STORY_ID = re.compile(r"s\d{1,3}")


def _story_sources(raw) -> list[dict]:
    out = []
    for s in raw or []:
        if isinstance(s, dict) and _safe_url(s.get("href") or s.get("url")):
            url = s.get("href") or s.get("url")
            out.append({"label": _text(s.get("label"), "source label") or urlsplit(url).netloc, "href": url,
                        "primary": bool(s.get("primary")) or s.get("tier") == "primary"})
    if out and not any(s["primary"] for s in out):
        out[0]["primary"] = True
    seen_primary = False
    for s in out:
        s["primary"], seen_primary = s["primary"] and not seen_primary, seen_primary or s["primary"]
    out.sort(key=lambda s: not s["primary"])
    for n, s in enumerate(out):
        s["prefix"] = "Primary: " if n == 0 else (" · Also: " if n == 1 else ", ")
        s["aria"] = f"{s['label']} (opens in a new tab)"
    return out


def edition_view(brief_id: str, body: dict, published_at, number: int | None) -> dict:
    """The template's view of one brief, whichever pass wrote it."""
    items = [_item(i) for i in body["items"]]
    if any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", i["id"]) for i in items):
        raise DigestMalformed("an item id has characters a page anchor cannot carry")
    by_item = {i["id"]: i for i in items}
    edition = body.get("edition") if isinstance(body.get("edition"), dict) else None
    window = body.get("window")
    view = {
        "week": brief_id, "label": edition_label(body, published_at), "published_at": published_at,
        "number": f"{number:02d}" if number else "", "range_line": range_line(window, published_at),
        "item_ids": set(by_item), "composed": edition is not None,
    }
    if edition is None:
        view.update(_legacy_sections(body, items))
    else:
        view.update(_composed_sections(edition, by_item))
    view["leaders"] = {"count": 0, "preview": "Nothing quotable surfaced this week.", "empty": True}
    view["sections"] = [k for k in ("thread", "learning", "stories", "leaders") if view.get(k)]
    labels = {"thread": "THE WEEK IN ONE THREAD", "learning": "LEARNING FOR THE WEEK", "stories": "THIS WEEK'S STORIES",
              "leaders": "WHAT ARE INDUSTRY LEADERS TALKING?"}
    counts = {"thread": None, "learning": None, "stories": (view.get("stories") or {}).get("count"), "leaders": 0}
    view["subnav"] = [(k, labels[k], counts[k]) for k in view["sections"]]
    view["colours"] = {k: SECTION_COLOURS[k][0] for k in SECTION_COLOURS}
    return view


def _composed_sections(edition: dict, by_item: dict) -> dict:
    thread, learning, closing = edition.get("thread"), edition.get("learning"), edition.get("closing")
    stories_raw = edition.get("stories")
    if not isinstance(thread, dict) or not isinstance(learning, dict) or not isinstance(closing, dict) or not isinstance(stories_raw, list) or not stories_raw:
        raise DigestMalformed("the edition is missing a section")
    if len(stories_raw) > MAX_ITEMS:
        raise DigestMalformed("the edition has too many stories")
    if any(not isinstance(s, dict) or not isinstance(s.get("id"), str) or not STORY_ID.fullmatch(s["id"]) for s in stories_raw):
        raise DigestMalformed("a story id is not of the form s01")
    ids = {s["id"] for s in stories_raw}
    numbers = {s["id"]: f"{n:02d}" for n, s in enumerate(stories_raw, start=1)}
    lines = _str_list(thread.get("lines"), "thread.lines", 3, clip=True)      # more than two lines: no headline, never a silently cut one
    notes_raw = learning.get("notes") if isinstance(learning.get("notes"), dict) else {}
    notes = {k: (str(v[0]), str(v[1])) for k, v in notes_raw.items()
             if isinstance(k, str) and SAFE_ID.fullmatch(k) and isinstance(v, list) and len(v) == 2}
    order: list = []
    paras = [{"segs": segments(p, ids=ids, notes=notes, order=order)} for p in _str_list(learning.get("paras"), "learning.paras")]
    note_list = [{"n": n, "key": k, "term": notes[k][0], "definition": notes[k][1]} for n, k in enumerate(order, start=1)]
    headlines = {s["id"]: _text(s.get("headline"), f"{s['id']}/headline") for s in stories_raw if isinstance(s, dict) and "id" in s}
    drawn = [{"num": numbers[d], "href": "#" + d, "headline": headlines.get(d, "")} for d in (learning.get("drawnFrom") or []) if d in numbers]
    dividers = {d["before"]: _text(d.get("label"), "divider") for d in edition.get("dividers") or [] if isinstance(d, dict) and d.get("before") in ids}
    rows = []
    for s in stories_raw:
        if not isinstance(s, dict) or s.get("id") not in numbers:
            raise DigestMalformed("a story has no id")
        item = by_item.get(s.get("itemId"))
        if item is None:
            raise DigestMalformed(f"{s['id']} points at an item the brief does not have")
        tie = s.get("tie") if (isinstance(s.get("tie"), dict) and s["tie"].get("to") in numbers and s["tie"].get("to") != s["id"]
                               and isinstance(s["tie"].get("text"), str) and s["tie"]["text"].strip()) else None
        feature_paras = _str_list(s.get("featureParas"), f"{s['id']}/featureParas", 5, clip=True)
        ninety = s.get("ninety") if isinstance(s.get("ninety"), list) else []
        rows.append({
            "divider": dividers.get(s["id"]),
            "id": s["id"], "item_id": item["id"], "num": numbers[s["id"]], "tag": s.get("tag") if s.get("tag") in PILLS else "KNOW",
            "feature": bool(s.get("feature")) and bool(feature_paras), "from_backlog": bool(s.get("fromBacklog")),
            "headline": _text(s.get("headline"), f"{s['id']}/headline", required=True),
            "hashtags": [h for h in _str_list(s.get("hashtags"), f"{s['id']}/hashtags", 4, clip=True) if re.fullmatch(r"#[A-Za-z][A-Za-z0-9]*", h)],
            "feature_paras": feature_paras, "body": _text(s.get("body"), f"{s['id']}/body"),
            "why": _text(s.get("why"), f"{s['id']}/why"), "try_it": _text(s.get("tryIt"), f"{s['id']}/tryIt"),
            "ninety": [(str(k), str(v)) for k, v in ninety if isinstance(k, str) and isinstance(v, str)] if all(isinstance(r, list) and len(r) == 2 for r in ninety) else [],
            "tie": {"num": numbers[tie["to"]], "href": "#" + tie["to"], "text": _text(tie.get("text"), "tie")} if tie else None,
            "sources": _story_sources(s.get("sources")), "checked": bool(s.get("checked")), "connection": "",
        })
    count = len(rows)
    held = [{"id": h["id"], "headline": _text(h.get("headline"), "held headline"), "sources": _story_sources(h.get("sources"))[:1],
             "why": _text(h.get("why"), "held reason") or "could not be verified"}
            for h in (edition.get("held") if isinstance(edition.get("held"), list) else [])[:MAX_ITEMS]
            if isinstance(h, dict) and isinstance(h.get("id"), str) and STORY_ID.fullmatch(h["id"])]
    return {
        "thread": {
            "lines": _thread_lines(lines), "body": segments(_text(thread.get("body"), "thread.body"), ids=ids), "signals": f"{count} SIGNALS WORTH YOUR ATTENTION",
            "count": f"{count} signals", "preview": first_sentence(thread.get("body", "")),
        },
        "learning": {
            "headline": segments(_text(learning.get("headline"), "learning.headline"), ids=ids), "paras": paras, "notes": note_list, "drawn": drawn,
            "numbers": [{"label": str(n[0]).upper(), "value": str(n[1]).upper()} for n in (learning.get("numbers") or []) if isinstance(n, list) and len(n) == 2],
            "how_made": _text(learning.get("howMade"), "howMade"), "do_differently": _text(learning.get("doDifferently"), "doDifferently"),
            "count": f"{len(drawn)} sources", "preview": first_sentence((learning.get("paras") or [""])[0]),
        },
        "stories": {
            "title": segments(_text(edition.get("storiesTitle"), "storiesTitle"), ids=ids), "rows": rows, "count": count, "held": held,
            "preview": "Lead: " + rows[0]["headline"],
            "closing": {"l1": _text((closing.get("lines") or ["", ""])[0], "closing"), "l2": _text((closing.get("lines") or ["", ""])[-1], "closing"),
                        "summary": _text(closing.get("summary"), "closing.summary"), "one_thing": _text(closing.get("oneThing"), "closing.oneThing")},
        },
    }


def _thread_lines(lines: list[str]) -> list | None:
    """The two-line headline as runs: the one highlight is painted once, in whichever line carries it."""
    if len(lines) != 2:
        return None
    first, second = segments(lines[0]), segments(lines[1])
    seen = False
    for run in first + second:                       # at most one highlight across the pair: the first one; any later one is plain text
        if run["kind"] == "hl":
            if seen:
                run["kind"] = "plain"
            seen = True
    return [first, second]


def _legacy_sections(body: dict, items: list[dict]) -> dict:
    """A brief written before the composing pass: its thread, and its stories as they are, with no essay and no closing."""
    thread = _text(body.get("thread"), "thread")
    rows = []
    for n, item in enumerate(items, start=1):
        rows.append({
            "divider": None, "id": f"s{n:02d}", "item_id": item["id"], "num": f"{n:02d}", "tag": next((p for p in PILLS if p in item["tags"]), "KNOW"),
            "feature": False, "from_backlog": False, "headline": item["headline"], "hashtags": [], "feature_paras": [], "body": "",
            "why": item["why"], "try_it": item["example"], "ninety": [(k.upper(), v) for k, v in item["ninety"]], "tie": None,
            "sources": _story_sources([{"label": s["label"], "href": s["url"], "primary": s["tier"] == "primary"} for s in item["sources"]]),
            "checked": item["checked"], "connection": item["connection"],
        })
    return {
        "thread": {"lines": None, "body": [{"kind": "plain", "text": thread}] if thread else [], "signals": f"{len(rows)} SIGNALS WORTH YOUR ATTENTION",
                   "count": f"{len(rows)} signals", "preview": first_sentence(thread)},
        "learning": None,
        "stories": {"title": [{"kind": "plain", "text": "What happened this week"}], "rows": rows, "count": len(rows),
                    "preview": "Lead: " + rows[0]["headline"], "closing": None},
    }


def archive_entry(brief_id: str, number: int, body_lines, body_thread, item_count: int, window, published_at) -> dict:
    """One archive row: 'ED 02', the dates, the two-line headline (or the first sentence of an older thread), the story count."""
    lines = [plain_text(x) for x in body_lines if isinstance(x, str)] if isinstance(body_lines, list) else []
    if len(lines) == 2:
        l1, l2 = lines
    else:
        l1, l2 = first_sentence(body_thread if isinstance(body_thread, str) else "")[:140], ""
    return {"label": f"ED {number:02d}", "range": range_line(window, published_at), "l1": l1, "l2": l2, "stories": item_count,
            "href": f"/weekly-digest?week={brief_id}", "id": brief_id}
