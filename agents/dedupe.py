"""Reader's deduplication gate: are these two pages the same article?

Duplicates are decided in layers, cheapest and most certain first. A layer either decides
or passes the pair on; nothing here ever *guesses* that two pages are the same.

  1. url          safe clean-up only (agents/excerpts.canonicalise): changes that can never alter
                  which page is meant. Same canonical URL -> same.
  2. identity     an arXiv ID or a DOI names a paper. The same ID is the same paper (versions
                  merge); two different IDs are different papers, decided here and never passed on.
  3. fingerprint  a deterministic comparison of the fetched text. The same only when one text sits
                  word for word inside the other, flush with one edge (a truncated fetch, furniture
                  added around a page, an appended update, a syndicated reprint); different when the
                  two share almost nothing (word 5-gram shingles); everything else, including text
                  that is merely *nearly* identical, goes on to the model. A single changed word can
                  reverse a story ("is not available" / "is now available"), so resemblance alone
                  never merges.
  4. model        a cheap model reads the two openings and says same or different, with its reason,
                  through llm.call() so it is booked in the ledger. Only for what layers 1 to 3 left.

**When in doubt the answer is "different".** A duplicate in the edition is visible and fixable; a
story merged away is not. So every failure to decide (no text, too short, a model error, a spent
budget, a cap on judgements) is "different", and a merge keeps the dropped link as another sighting
of the kept item and can be undone (`undo_merge`).

Only items that passed the relevance filter *and* were fetched are compared: this module is handed
fetched item ids, never raw candidates.
"""

from __future__ import annotations

import json
import re
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

try:
    from . import budget, excerpts, items, llm, sources
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import budget
    import excerpts
    import items
    import llm
    import sources

JUDGE_PHASE = "reader-dedup-judge"
USER_ID = "varun"

# WHAT: the numbers that decide a text pair, written down once.
# CONCEPT: eval — thresholds are fixed by the content pairs in data/evals/canonical/content/ and are
# not tuned to pass them: each pair there carries the layer that is expected to decide it.
SHINGLE = 5                 # words per shingle
MIN_WORDS = 40              # below this a text is too short to fingerprint
CONTAIN_MIN_RATIO = 0.4     # the shorter text is at least this share of the longer, to count as "the same text, cut short"
CONTAIN_FLUSH_WORDS = 40    # ...and sits flush with one edge of it: at most this many extra words before or after
DIFFERENT_RESEMBLANCE = 0.15
DIFFERENT_CONTAINMENT = 0.35
MAX_JUDGEMENTS = 10         # model judgements per pass; pairs beyond it stay "different"
JUDGE_CHARS = 3500          # of each text the model reads: the opening, where the template and facts are


@dataclass(frozen=True)
class Doc:
    url: str
    text: str | None = None
    title: str = ""


@dataclass(frozen=True)
class Decision:
    verdict: str            # 'same' | 'different' | 'unsure' (layer 3 only: hand on to the model)
    layer: str              # 'url' | 'identity' | 'fingerprint' | 'model' | 'default'
    reason: str
    detail: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Layer 2: identity
# --------------------------------------------------------------------------------------

_ARXIV_ID = r"(?P<id>\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})"
_IDENTITY_PATTERNS = (
    ("arxiv", re.compile(rf"^(?:www\.)?arxiv\.org/(?:abs|pdf|html)/{_ARXIV_ID}(?:v\d+)?(?:\.pdf)?/?$")),
    ("arxiv", re.compile(rf"^(?:www\.)?alphaxiv\.org/(?:abs|overview|pdf)/{_ARXIV_ID}(?:v\d+)?/?$")),
    ("arxiv", re.compile(rf"^huggingface\.co/papers/{_ARXIV_ID}(?:v\d+)?/?$")),
    ("arxiv", re.compile(rf"^ar5iv\.(?:labs\.arxiv\.)?org/(?:abs|html)/{_ARXIV_ID}(?:v\d+)?/?$")),
    ("doi", re.compile(r"^(?:dx\.)?doi\.org/(?P<id>10\.\d{4,9}/\S+?)/?$")),
)


def identity_of(url: str) -> tuple[str, str] | None:
    """('arxiv', '2610.00001') or ('doi', '10.1000/xyz'), read off the address alone; else None.

    The address is all that is read: a paper's text cites other papers, so scanning it for IDs would
    attach the wrong identity. A version suffix is not part of the identity.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    key = (parts.netloc.lower() + parts.path)
    for kind, pattern in _IDENTITY_PATTERNS:
        m = pattern.match(key)
        if m:
            ident = m.group("id")
            return kind, ident.lower() if kind == "doi" else ident
    return None


# --------------------------------------------------------------------------------------
# Layer 3: the text fingerprint
# --------------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+(?:[.,'][a-z0-9]+)*")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("’", "'"))


@dataclass(frozen=True)
class Fingerprint:
    words: int
    norm: str               # the words, lower-cased and single-spaced: what "word for word" is judged on
    shingles: frozenset


def fingerprint(text: str) -> Fingerprint:
    words = _words(text)
    grams = frozenset(zlib.crc32(" ".join(words[i:i + SHINGLE]).encode()) for i in range(max(1, len(words) - SHINGLE + 1)))
    return Fingerprint(words=len(words), norm=" ".join(words), shingles=grams)


def _inside(short: Fingerprint, long_: Fingerprint) -> tuple[int, int] | None:
    """(words before, words after) if `short` sits word for word inside `long_`, else None."""
    at = f" {long_.norm} ".find(f" {short.norm} ")
    if at < 0:
        return None
    before = len(long_.norm[:max(0, at - 1)].split()) if at > 0 else 0
    return before, long_.words - short.words - before


def compare_fingerprints(a: Fingerprint, b: Fingerprint) -> Decision:
    if a.norm == b.norm and a.words >= 1:
        return Decision("same", "fingerprint", "identical text", {"words": a.words})
    if min(a.words, b.words) < MIN_WORDS:
        return Decision("different", "default", f"a text under {MIN_WORDS} words is too short to compare")
    short, long_ = (a, b) if a.words <= b.words else (b, a)
    shared = len(a.shingles & b.shingles)
    resemblance = shared / max(1, len(a.shingles | b.shingles))
    containment = shared / max(1, min(len(a.shingles), len(b.shingles)))
    stats = {"resemblance": round(resemblance, 3), "containment": round(containment, 3), "words": [a.words, b.words]}
    placed = _inside(short, long_)
    if placed is not None and short.words >= CONTAIN_MIN_RATIO * long_.words and min(placed) <= CONTAIN_FLUSH_WORDS:
        return Decision("same", "fingerprint", "the shorter text sits word for word inside the longer one", {**stats, "extra_words": list(placed)})
    if resemblance < DIFFERENT_RESEMBLANCE and containment < DIFFERENT_CONTAINMENT:
        return Decision("different", "fingerprint", "the texts have almost nothing in common", stats)
    why = ("one text sits inside the other but not flush with an edge or not most of it" if placed is not None
           else "the texts overlap but are not the same word for word")
    return Decision("unsure", "fingerprint", why, stats)


# --------------------------------------------------------------------------------------
# Layers 1 to 3 together
# --------------------------------------------------------------------------------------

def decide(a: Doc, b: Doc) -> Decision:
    """Layers 1 to 3, free and deterministic. 'unsure' means only the model can say."""
    if excerpts.canonicalise(a.url) == excerpts.canonicalise(b.url):
        return Decision("same", "url", "the same address after safe clean-up")
    ia, ib = identity_of(a.url), identity_of(b.url)
    if ia and ib and ia[0] == ib[0]:
        if ia == ib:
            return Decision("same", "identity", f"the same {ia[0]} id, {ia[1]}")
        return Decision("different", "identity", f"different {ia[0]} ids, {ia[1]} and {ib[1]}")
    if not a.text or not b.text:
        return Decision("different", "default", "no text to compare")
    return compare_fingerprints(fingerprint(a.text), fingerprint(b.text))


# --------------------------------------------------------------------------------------
# Layer 4: the model
# --------------------------------------------------------------------------------------

JUDGE_SYSTEM = """You decide whether two fetched web pages are the SAME article or story, for a deduplicating news reader. \
You are shown the title, address and opening text of each.

Answer same=true only if both pages are the same publication: the identical article, a later version of one article \
(corrections, an added paragraph), a shortened or truncated copy of it, or a syndicated or reposted copy of it.

Answer same=false for everything else, including: two different announcements that share a template or boilerplate \
(for example a preview and the general-availability post of the same product, or two releases from one lab); two papers \
or posts that cover the same topic; a follow-up, a rebuttal or a commentary on the other; and any pair where you are not \
sure. A wrong "same" hides a story from the reader for good; a wrong "different" only shows one story twice. When in doubt, \
answer false.

Give a one-sentence reason that names what is the same or what differs."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"same": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["same", "reason"],
    "additionalProperties": False,
}


def judge_request(a: Doc, b: Doc) -> dict:
    def view(d: Doc) -> dict:
        return {"title": d.title, "address": d.url, "opening": (d.text or "")[:JUDGE_CHARS]}
    return dict(
        model=llm.CHECK_MODEL,
        max_tokens=600,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": json.dumps({"page_a": view(a), "page_b": view(b)}, indent=2)}],
        temperature=0,
        output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
    )


def judge(client, a: Doc, b: Doc, *, conn=None, user_id: str = USER_ID, phase: str = JUDGE_PHASE) -> tuple[Decision, float]:
    """Layer 4. Returns (decision, cost). Any answer that is not a clean 'same' is 'different'."""
    text, _, cost = llm.call(client, user_id=user_id, phase=phase, conn=conn, **judge_request(a, b))
    try:
        answer = json.loads(text)
        same, reason = answer["same"], str(answer["reason"]).strip()
    except (ValueError, KeyError, TypeError):
        return Decision("different", "default", "the model's answer could not be read"), cost
    if same is not True or not reason:
        return Decision("different", "model", reason or "the model gave no reason"), cost
    return Decision("same", "model", reason), cost


# --------------------------------------------------------------------------------------
# The database: decisions, merges and undo
# --------------------------------------------------------------------------------------

def _pair(conn, x, y):
    return conn.execute(
        "SELECT id, kept_id, verdict, undone_at FROM dedup_decisions WHERE (kept_id = %s AND other_id = %s) "
        "OR (kept_id = %s AND other_id = %s)", (x, y, y, x)).fetchone()


def merged_away(conn, item_ids) -> set:
    """The items among `item_ids` that an active merge folded into another item."""
    rows = conn.execute(
        "SELECT other_id FROM dedup_decisions WHERE verdict = 'same' AND undone_at IS NULL AND other_id = ANY(%s::uuid[])",
        ([str(i) for i in item_ids],)).fetchall()
    return {r[0] for r in rows}


def record(conn, kept, other, decision: Decision, now: datetime) -> uuid.UUID | None:
    """Store a decision. A merge also keeps the dropped link as another sighting of the kept item."""
    did = uuid.uuid4()
    with conn.transaction():
        cur = conn.execute(
            "INSERT INTO dedup_decisions (id, kept_id, other_id, verdict, layer, reason, decided_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (did, kept, other, decision.verdict, decision.layer, decision.reason[:1000] or "-", now))
        if cur.rowcount != 1:
            return None                           # the pair was decided by someone else a moment ago
        if decision.verdict == "same":
            url = conn.execute("SELECT canonical_url FROM items WHERE id = %s", (other,)).fetchone()[0]
            items.add_provenance(conn, kept, url, now)
    return did


def undo_merge(conn, decision_id, reason: str, now: datetime) -> None:
    """Take a merge back: the dropped item is eligible again, and the pair is never re-merged.

    The sighting recorded when it merged stays (provenance is append-only), which is true: the kept
    story was seen at that address.
    """
    if not reason.strip():
        raise ValueError("an undo needs a reason")
    cur = conn.execute("UPDATE dedup_decisions SET undone_at = %s, undone_reason = %s "
                       "WHERE id = %s AND verdict = 'same' AND undone_at IS NULL", (now, reason.strip()[:1000], decision_id))
    if cur.rowcount != 1:
        raise ValueError("no active merge with that id")


def _load_docs(conn, item_ids) -> list[tuple]:
    rows = conn.execute(
        "SELECT i.id, i.canonical_url, i.title, i.discovered_at, i.excerpt_id FROM items i "
        "WHERE i.id = ANY(%s::uuid[]) AND i.status = 'fetched' ORDER BY i.discovered_at, i.id::text",
        ([str(i) for i in item_ids],)).fetchall()
    return [(r[0], Doc(r[1], excerpts.get(conn, r[4]), r[2])) for r in rows]


def dedupe_fetched(conn, client, item_ids, now: datetime, *, max_judgements: int = MAX_JUDGEMENTS,
                   judge_fn=judge) -> dict:
    """Compare fetched items with each other. Returns a report of what happened.

    The earlier-discovered item of a pair is kept. A pair already decided (including a merge a person
    undid) is not decided again. The model sees only pairs layers 1 to 3 left 'unsure', most alike
    first, up to `max_judgements`; the rest stay different.
    """
    folded = merged_away(conn, item_ids)
    docs = [d for d in _load_docs(conn, item_ids) if d[0] not in folded]     # an active merge stays in force
    report = {"compared": 0, "merged": [], "judged": 0, "left_different": 0, "judge_failed": 0,
              "skipped_known": 0, "cost_usd": 0.0}
    dropped: set = set()
    queue = []
    for i, (ida, a) in enumerate(docs):
        if ida in dropped:
            continue
        for idb, b in docs[i + 1:]:
            if idb in dropped:
                continue
            if _pair(conn, ida, idb):
                report["skipped_known"] += 1
                continue
            report["compared"] += 1
            d = decide(a, b)
            if d.verdict == "same":
                if record(conn, ida, idb, d, now) is None:
                    report["skipped_known"] += 1
                    continue
                dropped.add(idb)
                report["merged"].append({"kept": str(ida), "dropped": str(idb), "layer": d.layer, "reason": d.reason})
            elif d.verdict == "unsure":
                queue.append((d.detail.get("resemblance", 0), ida, idb, a, b))
            else:
                report["left_different"] += 1
    queue.sort(key=lambda q: -q[0])
    for n, (_, ida, idb, a, b) in enumerate(queue):
        if ida in dropped or idb in dropped:
            report["left_different"] += 1
            continue
        if n >= max_judgements:
            report["left_different"] += 1       # over the cap: doubt means different
            continue
        try:
            d, cost = judge_fn(client, a, b, conn=conn)
        except (budget.BudgetExceeded, budget.LedgerUnavailable):
            report["judge_failed"] += 1
            report["left_different"] += 1
            max_judgements = 0                  # the budget is spent: stop judging, keep going
            continue
        except Exception:                       # noqa: BLE001 - a model error must not lose a story
            report["judge_failed"] += 1
            report["left_different"] += 1
            continue
        report["cost_usd"] = round(report["cost_usd"] + cost, 6)
        if d.verdict not in ("same", "different") or d.layer == "default":
            # The model gave no usable answer: doubt means different now, and the pair is asked again next run.
            report["judge_failed"] += 1
            report["left_different"] += 1
            continue
        report["judged"] += 1
        if record(conn, ida, idb, d, now) is None:
            report["skipped_known"] += 1         # another run decided this pair first
            continue
        if d.verdict == "same":
            dropped.add(idb)
            report["merged"].append({"kept": str(ida), "dropped": str(idb), "layer": d.layer, "reason": d.reason})
        else:
            report["left_different"] += 1
    return report


# --------------------------------------------------------------------------------------
# Captured links: /AGI-add-news and /add-news
# --------------------------------------------------------------------------------------

def ingest_captured(conn, client, urls, now: datetime, *, add, window_ids) -> list[dict]:
    """Run a list of captured URLs through the layers and report what happened to each.

    `add(conn, client, url, now)` fetches and stores one URL (reader.add_url; it is the paid step and
    is injected so this stays testable). reader.add_url runs the layers itself and says so in
    `merged_into`; an `add` that does not leaves the comparison to this function. `window_ids(conn,
    now)` lists the fetched items to compare against, and is required: a caller that forgets it would
    compare nothing and report every capture as added. Captures made with /AGI-add-news live in project memory, which the hosted app cannot read
    until go-live; go-live passes the list in here.

    Outcomes: rejected (not an absolute http(s) address), blocked (a site that refuses fetchers),
    already-known (same address after safe clean-up), same-paper (same arXiv/DOI identity as an item
    already held: recorded as a sighting, not fetched), merged (the fetched text is the same article
    as another item), added (kept as its own story), fetch-failed.
    """
    report, seen = [], set()
    known = conn.execute("SELECT id, canonical_url, status FROM items").fetchall()
    by_url = {r[1]: r for r in known}
    for raw in urls:
        row = {"url": raw}
        report.append(row)
        try:
            parts = urlsplit(str(raw).strip())
            ok = parts.scheme in ("http", "https") and bool(parts.hostname)
        except ValueError:
            ok = False
        if not ok:
            row.update(outcome="rejected", detail="not an absolute http(s) address")
            continue
        canonical = excerpts.canonicalise(raw)
        row["canonical"] = canonical
        if sources.is_blocked(sources.domain_of(canonical)):
            row.update(outcome="blocked", detail="this site refuses fetchers; capture a syndicated copy")
            continue
        if canonical in seen or canonical in by_url:
            hit = by_url.get(canonical)
            row.update(outcome="already-known", detail=f"already held, status {hit[2]}" if hit else "listed twice in this batch")
            continue
        seen.add(canonical)
        ident = identity_of(canonical)
        twin = next((r for r in known if ident and identity_of(r[1]) == ident), None)
        if twin:
            items.add_provenance(conn, twin[0], canonical, now)
            row.update(outcome="same-paper", detail=f"the same {ident[0]} id as an item already held; kept as another sighting", item=str(twin[0]))
            continue
        try:
            result = add(conn, client, raw, now)
        except (budget.BudgetExceeded, budget.LedgerUnavailable):
            raise                                  # the ceiling stops the whole batch, as everywhere else
        except Exception as e:                     # noqa: BLE001 - one bad URL must not lose the rest of the batch
            row.update(outcome="error", detail=f"{type(e).__name__} while fetching; not stored")
            continue
        iid = uuid.UUID(result["item"])
        row["item"] = str(iid)
        if not result.get("fetched"):
            row.update(outcome="fetch-failed", detail="the page could not be fetched")
            continue
        if "merged_into" in result:                # add() already ran the layers
            into = result["merged_into"]
        else:
            rep = dedupe_fetched(conn, client, [iid, *window_ids(conn, now)], now)
            mine = next((m for m in rep["merged"] if m["dropped"] == str(iid)), None)
            into = mine["kept"] if mine else None
        if into:
            decision = conn.execute("SELECT layer, reason FROM dedup_decisions WHERE other_id = %s AND verdict = 'same' "
                                    "AND undone_at IS NULL", (iid,)).fetchone()
            row.update(outcome="merged", detail=f"{decision[1]} ({decision[0]})" if decision else "folded into another story", into=str(into))
        else:
            row.update(outcome="added", detail="kept as its own story")
        known.append((iid, canonical, "fetched"))
    return report
