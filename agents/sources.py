"""Sources: who Reader trusts how much, seeded from config/scout-sources.md.

The list is the only authority on tier and reputation prior. This module reads it and
writes it to the `sources` table; it does not add publishers of its own. A domain Reader
meets that the list does not name is recorded as 'unrated', and an unrated source cannot
make an item surface on its own (see items.surfaceable).

The plan speaks of 91 tiered domains. config/scout-sources.md names 21 publishers, most
without an address; those are seeded by name with a NULL domain, and only addresses that
appear in the file are taken from it. The rest of the catalogue is config/digest-sources.md,
the old digest's own list (copied 2026-10-19), transcribed into config/digest-source-rows.json
and seeded the same way by seed_digest(). It is prose, not a table of 91 domains: it names
83 sources, of which 14 carry an address. Nothing is invented; a test pins that.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
SOURCES_PATH = REPO / "config" / "scout-sources.md"
DIGEST_ROWS_PATH = REPO / "config" / "digest-source-rows.json"   # config/digest-sources.md, transcribed; a test checks it

# WHAT: tier -> reputation prior, as the file defines them in its headings.
# CONCEPT: reputation prior. A prior is a starting trust, not a verdict: high may surface
# alone, medium needs a second source for its claims, low is discovery and corroboration
# only. The Tuner adjusts it later from feedback; today it is fixed by the list.
PRIOR = {"A": "high", "B": "high", "C": "medium", "D": "low", "unrated": "unrated"}

_TIER_HEADING = re.compile(r"^##\s+Tier\s+([A-D])\b")
_ENTRY = re.compile(r"^-\s+\*\*(?P<name>[^*]+)\*\*\s*(?:—\s*(?P<rest>.*))?$")
_DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})(?:/[^\s,)]*)?", re.I)

# Entries whose bold name is a heading for a list of publishers, not one publisher.
_NAME_LISTS = {"Lab blogs"}
_PLAIN_ENTRY = re.compile(r"^-\s+(?!\*\*)(?P<names>.+)$")


class Source(NamedTuple):
    id: str
    name: str
    domain: str | None
    tier: str
    reputation_prior: str
    notes: str


def _split_names(text: str) -> list[str]:
    """'Import AI (Jack Clark), The Batch (DeepLearning.AI), Latent Space.' -> three names."""
    text = re.sub(r"\([^)]*\)", "", text).strip().rstrip(".")
    return [n.strip() for n in text.split(",") if n.strip()]


def _find_domain(text: str) -> str | None:
    for m in _DOMAIN.finditer(text):
        host = m.group(1).lower()
        if host.split(".")[0] == "cs":  # arXiv category names (cs.AI), not addresses
            continue
        return host
    return None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def domain_of(url: str) -> str:
    """The host of a URL, lowercased, without a leading www."""
    host = urlsplit(url.strip()).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def parse(text: str) -> list[Source]:
    sources: list[Source] = []
    tier = None
    for line in text.splitlines():
        heading = _TIER_HEADING.match(line)
        if heading:
            tier = heading.group(1)
            continue
        if line.startswith("## "):
            tier = None  # past the tier lists
            continue
        if tier is None:
            continue
        plain = _PLAIN_ENTRY.match(line.strip())
        if plain:  # Tiers C and D list bare names: "Import AI (Jack Clark), The Batch, Latent Space."
            for n in _split_names(plain.group("names")):
                sources.append(Source(slug(n), n, None, tier, PRIOR[tier], "named in the list without an address"))
            continue
        entry = _ENTRY.match(line.strip())
        if entry is None:
            continue
        name, rest = entry.group("name").strip(), (entry.group("rest") or "").strip()
        if name in _NAME_LISTS:
            names = _split_names(rest)
            sources.extend(Source(slug(n), n, None, tier, PRIOR[tier], f"listed under {name!r}") for n in names)
            continue
        # A domain is taken only if the entry literally contains one.
        domain = _find_domain(rest)
        sources.append(Source(slug(name), name, domain, tier, PRIOR[tier], rest))
    return sources


def load_seed(path: Path = SOURCES_PATH) -> list[Source]:
    return parse(path.read_text(encoding="utf-8"))


def seed(conn, path: Path = SOURCES_PATH) -> int:
    """Insert the listed sources that are not already there. Never overwrites a row."""
    added = 0
    with conn.transaction():
        for s in load_seed(path):
            cur = conn.execute(
                "INSERT INTO sources (id, name, domain, tier, reputation_prior, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (s.id, s.name, s.domain, s.tier, s.reputation_prior, s.notes[:500] or None),
            )
            added += cur.rowcount
    return added


def load_digest_rows(path: Path = DIGEST_ROWS_PATH) -> list[Source]:
    """The old digest's catalogue as sources. A source the catalogue names without an address has none."""
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Source(slug(r["name"]), r["name"], r.get("domain"), r["tier"], PRIOR[r["tier"]],
                   f'{r["section"]}: {r.get("note") or "named in config/digest-sources.md"}') for r in data["sources"]]


def blocked_domains(path: Path = DIGEST_ROWS_PATH) -> dict:
    """Domains that refuse fetchers, with why. A fetch of one is never attempted (use a syndicated copy)."""
    import json
    return {b["domain"]: b["why"] for b in json.loads(path.read_text(encoding="utf-8")).get("blocked_domains", [])}


def is_blocked(domain: str, blocked: dict | None = None) -> bool:
    blocked = blocked if blocked is not None else blocked_domains()
    domain = domain.lower().rsplit("@", 1)[-1].split(":")[0].rstrip(".")      # no userinfo, port or trailing dot
    return any(domain == b or domain.endswith("." + b) for b in blocked)


def seed_digest(conn, path: Path = DIGEST_ROWS_PATH) -> int:
    """Insert the old digest's sources that are not already there. Never overwrites a row, and a
    row whose id or address a source already holds is left to that source."""
    added = 0
    with conn.transaction():
        for s in load_digest_rows(path):
            cur = conn.execute(
                "INSERT INTO sources (id, name, domain, tier, reputation_prior, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (s.id, s.name, s.domain, s.tier, s.reputation_prior, s.notes[:500] or None))
            added += cur.rowcount
    return added


def for_domain(conn, domain: str) -> str:
    """The source id for a domain, creating an 'unrated' one if the list does not know it.

    Matched on the domain alone. A met domain whose slug happens to equal a listed
    source's id (meta.ai -> meta-ai) must not inherit that source's tier, so a clash
    gets a suffix instead.
    """
    domain = domain.lower()
    row = conn.execute("SELECT id FROM sources WHERE domain = %s", (domain,)).fetchone()
    if row:
        return row[0]
    sid = slug(domain)
    if conn.execute("SELECT 1 FROM sources WHERE id = %s", (sid,)).fetchone():
        sid = f"{sid}-{hashlib.sha256(domain.encode()).hexdigest()[:6]}"
    conn.execute(
        "INSERT INTO sources (id, name, domain, tier, reputation_prior, notes) "
        "VALUES (%s, %s, %s, 'unrated', 'unrated', 'met by Reader; not in config/scout-sources.md') "
        "ON CONFLICT DO NOTHING", (sid, domain, domain))
    return conn.execute("SELECT id FROM sources WHERE domain = %s", (domain,)).fetchone()[0]


def set_feed(conn, source_id: str, domain: str, feed_url: str) -> None:
    """Attach a verified feed to a listed source, and its address if the list gave none.

    Must run before anything meets the domain: once an item points at an 'unrated' row
    for that address, the address cannot move (an item's source never changes), so this
    refuses with a clear error instead of a constraint violation. Reader.prepare() calls
    it for every feed before it discovers anything.
    """
    holder = conn.execute("SELECT id FROM sources WHERE domain = %s", (domain,)).fetchone()
    if holder is not None and holder[0] != source_id:
        raise ValueError(f"{domain} is already held by source {holder[0]!r}; attach {source_id!r}'s address before "
                         "any item meets it")
    conn.execute("UPDATE sources SET feed_url = %s, domain = coalesce(domain, %s) WHERE id = %s",
                 (feed_url, domain, source_id))
