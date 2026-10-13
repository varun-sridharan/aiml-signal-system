"""The excerpt store: article text, stored once at fetch time and never re-fetched.

The faithfulness gate checks every quoted span as a literal substring of the source. If
the source were re-fetched at grading time, a publisher's later edit would turn the gate
red for a reason that has nothing to do with the writing. So the text the writer saw is
stored once, as plain text, and every grading run afterwards reads that copy.

Write-once is enforced by the database (agents/schema.sql), not by this module: triggers
refuse any change to a stored excerpt except pinning it, and refuse to delete one a brief
cites. This module's own checks are the ones a database cannot make: that what arrives
is extracted text and not a page, and that what comes back still hashes to what went in.

Every function takes an open connection, from budget.connect(); the store lives in the
same Postgres as the spend ledger.
"""

from __future__ import annotations

import hashlib
import uuid
import zlib
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

# WHAT: how long an uncited excerpt is kept.
# CONCEPT: retention. Eight weeks covers what an uncited excerpt is still needed for:
# deduplicating a story that resurfaces, corroborating it against later sources, and
# checking a prediction against the evidence around it. Anything a published brief
# cites is pinned (expires_at = NULL) and kept forever, so a prediction resolved months
# later still has its evidence; only what no brief ever used ages out.
RETENTION_DAYS = 56

# Extraction is the Reader's job, not the store's. A page arriving here means the
# extractor failed, and storing it would put markup into the text the gate quotes from.
MAX_TEXT_BYTES = 200 * 1024
PAGE_MARKERS = ("<html", "<body")

# Write-once, read on every grading run: the best compression is worth its one-off cost.
ZLIB_LEVEL = 9


class ExcerptRefused(ValueError):
    """The text is not an excerpt this store will hold."""


class ExcerptCorrupted(RuntimeError):
    """The stored text no longer hashes to what was stored. The gate must not use it."""


def canonicalise(url: str) -> str:
    """One form per article, the same way AGI-add-news canonicalises.

    Force https, lowercase the host and strip a leading www., drop the whole query
    string and fragment (tracking parameters, #:~:text= and the rest), and strip the
    trailing slash. The path keeps its case: servers are entitled to treat it as
    significant.
    """
    parts = urlsplit(url.strip())
    if not parts.netloc:
        raise ExcerptRefused(f"not an absolute URL: {url!r}")
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store(conn, url: str, text: str, fetched_at: datetime) -> uuid.UUID:
    """Store an article's text once. Returns its id, new or existing.

    The same URL with the same text returns the row already there. The same URL with
    different text is a new row: the publisher changed the article, and the old text is
    still what any brief written before the change was checked against.
    """
    if fetched_at.tzinfo is None:
        raise ExcerptRefused("fetched_at must be timezone-aware")
    if not isinstance(text, str) or not text.strip():
        raise ExcerptRefused("excerpt text is empty")
    lowered = text.lower()
    marker = next((m for m in PAGE_MARKERS if m in lowered), None)
    if marker:
        raise ExcerptRefused(f"text contains {marker!r}: that is a page, not extracted article text")
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise ExcerptRefused(f"text is {len(data)} bytes, over the {MAX_TEXT_BYTES}-byte limit for one article")

    canonical = canonicalise(url)
    digest = _sha256(data)
    blob = zlib.compress(data, ZLIB_LEVEL)
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO excerpts (id, canonical_url, domain, fetched_at, content_sha256, text_z, "
            "text_bytes, stored_bytes, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (canonical_url, content_sha256) DO NOTHING RETURNING id",
            (uuid.uuid4(), canonical, urlsplit(canonical).netloc, fetched_at, digest, blob,
             len(data), len(blob), fetched_at + timedelta(days=RETENTION_DAYS)),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM excerpts WHERE canonical_url = %s AND content_sha256 = %s",
                (canonical, digest),
            ).fetchone()
    return row[0]


def get(conn, excerpt_id: uuid.UUID) -> str:
    """The stored text, verified against its hash on every read."""
    row = conn.execute(
        "SELECT text_z, content_sha256, text_bytes FROM excerpts WHERE id = %s", (excerpt_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no excerpt {excerpt_id}")
    blob, digest, size = row
    # WHAT: decompress, re-hash, compare, every time.
    # CONCEPT: the gate trusts this text absolutely. A silently corrupted excerpt would
    # make it pass a fabrication or fail an honest quote, so a mismatch stops the read.
    try:
        data = zlib.decompress(bytes(blob))
    except zlib.error as e:
        raise ExcerptCorrupted(f"excerpt {excerpt_id} does not decompress: {e}") from None
    if len(data) != size or _sha256(data) != digest:
        raise ExcerptCorrupted(f"excerpt {excerpt_id} does not match its stored hash; refusing to use it")
    return data.decode("utf-8")


def cite(conn, excerpt_id: uuid.UUID, brief_id: str) -> None:
    """Record that a brief cites this excerpt, and pin it: kept forever from now on."""
    if not brief_id:
        raise ValueError("brief_id is required")
    with conn.transaction():
        conn.execute(
            "INSERT INTO excerpt_citations (excerpt_id, brief_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (excerpt_id, brief_id),
        )
        conn.execute("UPDATE excerpts SET expires_at = NULL WHERE id = %s AND expires_at IS NOT NULL", (excerpt_id,))


def expire(conn, now: datetime) -> int:
    """Delete uncited excerpts past their date. Returns how many went.

    Not scheduled yet: the weekly run will call this first. A cited excerpt has no date
    (cite() pinned it), and the database refuses to delete one regardless.
    """
    with conn.transaction():
        cur = conn.execute(
            "DELETE FROM excerpts e WHERE e.expires_at < %s "
            "AND NOT EXISTS (SELECT 1 FROM excerpt_citations c WHERE c.excerpt_id = e.id)",
            (now,),
        )
    return cur.rowcount
