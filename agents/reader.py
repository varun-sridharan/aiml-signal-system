"""Reader: Scout and Gatekeeper merged. Discover candidates, keep the relevant, fetch what is worth reading.

    discover (free) -> canonicalise -> dedup -> relevance filter -> fetch the body -> store

Discovery reads feeds and official APIs directly: free and deterministic. Fetching an
article body is delegated to Claude's web fetch, called through the Anthropic API and
booked in the ledger like any other call (decision 2026-10-17): the same fetcher the
scheduled digest already uses, because a Python process on Railway would have to re-earn
reachability from datacenter addresses that publishers block harder.

Only candidates that pass the relevance filter AND may surface (agents/items.surfaceable)
are fetched, and no more than MAX_FETCHES_PER_RUN of them, so the paid step is a handful
of small Haiku calls rather than one per feed entry.

The Reader writes only pointers into the run record (ids of items and excerpts); the text
lives in the excerpt store.
"""

from __future__ import annotations

import html
import html.parser
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

try:
    from . import budget, dedupe, excerpts, graph, items, llm, runs, sources
except ImportError:  # pragma: no cover
    import budget
    import dedupe
    import excerpts
    import graph
    import items
    import llm
    import runs
    import sources

# WHAT: the fetch driver and its bounds.
# CONCEPT: delegated fetching. Haiku is the cheapest current model and does no reasoning
# here: it is told to fetch one URL and say DONE, and the page comes back in the tool's
# result block, which this module reads. The page is capped by max_content_tokens, which
# is also what the ledger's worst-case estimate adds as extra input.
FETCH_MODEL = "claude-haiku-4-5"
WEB_FETCH_TOOL = "web_fetch_20250910"  # basic fetch; the dynamic-filtering versions need code execution
FETCH_MAX_CONTENT_TOKENS = 8_000
FETCH_MAX_TOKENS = 200
FETCH_PHASE = "reader-fetch"
USER_ID = "varun"

MAX_FETCHES_PER_RUN = 20
# The hard cost guard for one Reader run. The count above already keeps a run near $0.3;
# this is the number the plan promises ("if one run would cost more than $3, lower the
# fetch count"), enforced in run() against the worst-case reservation, not the typical cost.
RUN_COST_CEILING_USD = 3.0
# A failed fetch is retried next run only if the failure could be temporary. A PDF, a
# robots.txt refusal or a page that is not text will fail the same way again, and paying for
# that on every run, twenty at a time, is waste.
TRANSIENT_FETCH_ERRORS = ("too_many_requests", "unavailable", "no_fetch_result", "max_uses_exceeded")
# One domain cannot take the whole fetch budget: arXiv alone would otherwise fill all twenty
# slots and leave the lab blogs, the MCP blog and the changelog unread.
MAX_FETCHES_PER_DOMAIN = 4
# A pause before a second fetch from the same site. The first real run had two of four arXiv
# fetches answered 'too_many_requests'; spacing them is cheaper than retrying them.
FETCH_PAUSE_SECONDS = 3.0
WINDOW = timedelta(days=8)  # a weekly run looks back a week plus a day of slack


def window_label(start: datetime, end: datetime) -> str:
    """'Oct 10 – Oct 18, 2026': the dates an edition covers, the way a reader would say them.

    An edition is named by what its Reader actually looked at, never by the week key of the
    run that made it. A window that crosses a year names both years.
    """
    first, last = f"{start:%b} {start.day}", f"{end:%b} {end.day}, {end.year}"
    return f"{first}, {start.year} \u2013 {last}" if start.year != end.year else f"{first} \u2013 {last}"


def window_of(now: datetime) -> dict:
    """The Reader's window as the stage output records it (ISO times and the label)."""
    start = now - WINDOW
    return {"from": start.isoformat(), "to": now.isoformat(), "label": window_label(start, now)}
FEED_TIMEOUT = 20
FEED_MAX_BYTES = 3_000_000
USER_AGENT = "aiml-signal-system/1.0 (feed reader; personal project)"


@dataclass(frozen=True)
class Feed:
    id: str
    source_id: str      # the sources row this feed belongs to (config/scout-sources.md)
    domain: str         # the address its articles live at, verified by fetching the feed
    url: str
    kind: str           # 'rss' | 'atom' | 'hf-papers' | 'hn' | 'index'
    pattern: str = ""   # 'index' only: the article URLs on the page, as a regex the whole URL must match


# The first nine verified reachable on 2026-10-18 (HTTP 200, expected content type). Still not
# discovered: Qwen, DeepSeek, Moonshot, xAI, Ai2, Z.ai, The Batch, the benchmarks, the agent
# frameworks, the two subreddits, and the enterprise vendors (no address in the catalogue, and an
# address is never guessed); see the debrief.
FEEDS = [
    Feed("arxiv", "arxiv", "arxiv.org",
         "http://export.arxiv.org/api/query?search_query=cat:cs.MA+OR+cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.LG"
         "&sortBy=submittedDate&sortOrder=descending&max_results=200", "atom"),
    Feed("hf-papers", "hugging-face-papers", "huggingface.co", "https://huggingface.co/api/daily_papers?limit=50",
         "hf-papers"),
    Feed("github-changelog", "github-changelog", "github.blog", "https://github.blog/changelog/feed/", "rss"),
    Feed("mcp-blog", "model-context-protocol", "blog.modelcontextprotocol.io",
         "https://blog.modelcontextprotocol.io/index.xml", "rss"),
    Feed("openai", "openai", "openai.com", "https://openai.com/news/rss.xml", "rss"),
    Feed("deepmind", "google-deepmind", "deepmind.google", "https://deepmind.google/blog/rss.xml", "rss"),
    Feed("latent-space", "latent-space", "latent.space", "https://www.latent.space/feed", "rss"),
    Feed("import-ai", "import-ai", "importai.substack.com", "https://importai.substack.com/feed", "rss"),
    Feed("hacker-news", "hacker-news-front-page", "news.ycombinator.com",
         "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60", "hn"),
    # Added 2026-10-20 from config/digest-sources.md. Each address below was fetched once and read: the
    # three feeds are advertised by their own pages; the five indexes are server-rendered pages whose
    # article links the pattern was checked against. Nothing here is a guessed address.
    Feed("mistral", "mistral", "mistral.ai", "https://mistral.ai/news/rss", "rss"),
    Feed("cio-dive", "cio-dive", "ciodive.com", "https://www.ciodive.com/feeds/news/", "rss"),
    Feed("the-new-stack", "the-new-stack", "thenewstack.io", "https://thenewstack.io/blog/feed/", "rss"),
    Feed("anthropic-news", "anthropic", "anthropic.com", "https://www.anthropic.com/news", "index",
         r"https://www\.anthropic\.com/news/[a-z0-9][a-z0-9-]*"),
    Feed("anthropic-research", "anthropic", "anthropic.com", "https://www.anthropic.com/research", "index",
         r"https://www\.anthropic\.com/research/(?!team/)[a-z0-9][a-z0-9-]*"),
    Feed("meta-ai", "meta-ai", "ai.meta.com", "https://ai.meta.com/blog/", "index",
         r"https://ai\.meta\.com/blog/(?!page/)[a-z0-9][a-z0-9-]*/?"),
    Feed("nvidia-developer", "nvidia-developer-blog", "developer.nvidia.com", "https://developer.nvidia.com/blog/", "index",
         r"https://developer\.nvidia\.com/blog/(?!page/)[a-z0-9][a-z0-9-]*/"),
    Feed("microsoft-ai", "microsoft-ai", "microsoft.ai", "https://microsoft.ai/news/", "index",
         r"https://microsoft\.ai/news/[a-z0-9][a-z0-9-]*/"),
]
# Not discovered, and why. OpenAI's second index (openai.com/research) answers fetchers 403 and the catalogue
# calls it curated and often months stale; real research posts appear in the /news feed above. The Gemini API
# changelog is one page of dated entries, and the item store's identity is the canonical URL with the fragment
# dropped (on purpose), so its entries cannot be separate items; it needs a decision (see the debrief).


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str
    summary: str
    published: datetime | None
    feed: Feed
    seen_url: str | None = None   # where the discoverer saw it, when that is not the article itself


@dataclass
class Stats:
    feeds_ok: int = 0
    feed_errors: list[str] = field(default_factory=list)
    candidates: int = 0
    out_of_window: int = 0
    irrelevant: int = 0
    new_items: int = 0
    known_items: int = 0
    corroborated: int = 0
    fetched: int = 0
    fetch_failed: int = 0
    left_for_later: int = 0   # relevant and not fetched this run: over the per-run or per-domain cap
    dated_out: int = 0        # fetched, and the page's own date put it outside the window
    merged: int = 0           # fetched, and the deduplication gate folded it into another item (agents/dedupe.py)
    judged: int = 0           # pairs the gate's model judged (layer 4)
    dedup_cost_usd: float = 0.0
    fetch_cost_usd: float = 0.0

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["feed_errors"] = self.feed_errors[:10]
        return d


# --------------------------------------------------------------------------------------
# Parsing: pure functions, tested on fixtures
# --------------------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _strip_tags(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html or "").split())


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed_xml(xml_bytes: bytes, feed: Feed) -> list[Candidate]:
    """RSS 2.0 and Atom, by local tag name, so namespaces do not matter."""
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        fields = {}
        link = None
        for child in el:
            name = _local(child.tag)
            if name == "link":
                href = child.get("href") or (child.text or "").strip()
                if href and (link is None or child.get("rel") in (None, "alternate")):
                    link = href
            elif name in ("title", "description", "summary", "content", "pubDate", "published", "updated"):
                fields.setdefault(name, _text(child))
        title = fields.get("title", "")
        if not link or not title or not link.startswith(("http://", "https://")):
            continue
        summary = _strip_tags(fields.get("description") or fields.get("summary") or fields.get("content") or "")[:600]
        published = _date(fields.get("pubDate") or fields.get("published") or fields.get("updated"))
        out.append(Candidate(link, title, summary, published, feed))
    return out


def parse_hf_papers(payload: bytes, feed: Feed) -> list[Candidate]:
    """Hugging Face daily papers: each is an arXiv paper, so the article is the arXiv page."""
    out = []
    for row in json.loads(payload):
        paper = row.get("paper") or {}
        pid, title = paper.get("id"), (paper.get("title") or row.get("title") or "").strip()
        if not pid or not title:
            continue
        out.append(Candidate(f"https://arxiv.org/abs/{pid}", title, " ".join((paper.get("summary") or "").split())[:600],
                             _date(paper.get("publishedAt") or row.get("publishedAt")), feed,
                             seen_url=f"https://huggingface.co/papers/{pid}"))
    return out


def parse_hn(payload: bytes, feed: Feed) -> list[Candidate]:
    """The Hacker News front page: external links only; discussion threads are not articles."""
    out = []
    for hit in json.loads(payload).get("hits", []):
        url, title = hit.get("url"), (hit.get("title") or "").strip()
        if not url or not title:
            continue
        out.append(Candidate(url, title, "", _date(hit.get("created_at")), feed,
                             seen_url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}"))
    return out


# --- index pages: sites with no feed. Only links that are in the page, matching the feed's pattern, are
# candidates: a URL is never built from a headline (slugs cannot be derived; the catalogue's rule).
INDEX_MAX_LINKS = 12      # dated cards first, newest first, then undated ones in page order
INDEX_UNDATED_MAX = 3     # an undated card cannot be judged old until its page is paid for, so few are let through
_MONTH_DATE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (20\d\d)\b")
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}


def _card_date(text: str, attr: str | None = None) -> datetime | None:
    """A date shown on a card: a <time datetime> value, else a 'Oct 6, 2026' style text. Bad dates are None."""
    for raw in (attr, text):
        if not raw:
            continue
        try:
            if raw is attr:
                d = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            m = _MONTH_DATE.fullmatch(" ".join(raw.split()))
            if m:
                return datetime(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)), tzinfo=timezone.utc)
        except ValueError:      # "Feb 31, 2026" or a malformed attribute: no date, never a failed feed
            continue
    return None


class _IndexParser(html.parser.HTMLParser):
    """Collects each <a href> card: its date, its title and its summary, in one linear pass.

    A title is, in order of trust: heading text (h1 to h6), an element whose class says 'title', the
    anchor's own aria-label ('Read ...'), or all of the anchor's text. Short pieces on the card
    ("Announcements", "Read now") are labels and never a title.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self.card: dict | None = None
        self.stack: list[tuple[str, str]] = []     # (tag, role) inside the open anchor

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            if self.card is not None:
                self.handle_endtag("a")
            self.card = {"href": a["href"], "aria": a.get("aria-label", ""), "title": [], "time": [], "time_attr": None,
                         "p": [], "text": []}
            self.stack = []
            return
        if self.card is None or tag in _VOID:
            return
        role = ("title" if re.fullmatch(r"h[1-6]", tag) or "title" in (a.get("class") or "").lower()
                else "time" if tag == "time" else "p" if tag == "p" else "")
        if tag == "time" and a.get("datetime"):
            self.card["time_attr"] = a["datetime"]
        self.stack.append((tag, role))

    def handle_endtag(self, tag):
        if self.card is None:
            return
        if tag == "a":
            self.cards.append(self.card)
            self.card, self.stack = None, []
        else:
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

    def handle_data(self, data):
        if self.card is None or not data.strip():
            return
        self.card["text"].append(data)
        for _tag, role in self.stack:
            if role:
                self.card[role].append(data)


def parse_index(payload: bytes, feed: Feed) -> list[Candidate]:
    pattern = re.compile(feed.pattern)
    parser = _IndexParser()
    parser.feed(payload.decode("utf-8", "replace"))
    parser.close()
    if parser.card is not None:
        parser.handle_endtag("a")
    best: dict[str, tuple[str, str, datetime | None, bool]] = {}
    for card in parser.cards:
        url = urljoin(feed.url, card["href"]).split("#")[0].split("?")[0]
        if not pattern.fullmatch(url):
            continue
        norm = lambda parts: " ".join(" ".join(parts).split())      # noqa: E731
        published = _card_date(norm(card["time"]), card["time_attr"]) or next(
            (d for d in (_card_date(piece) for piece in (norm([t]) for t in card["text"])) if d), None)
        title = norm(card["title"])
        trusted = bool(title)
        if not title and card["aria"].startswith("Read "):
            title, trusted = " ".join(card["aria"][5:].split()), True
        if not title:
            pieces = [p for p in (norm([t]) for t in card["text"]) if p and not _card_date(p)]
            title = " ".join(pieces) if len(pieces) == 1 else next((p for p in pieces if len(p.split()) >= 3), "")
        if len(title.split()) < 2 or len(title) > 300:
            continue                                                  # "Read now", a label, or a wall of text
        summary = norm(card["p"])[:600]
        old = best.get(url)
        if old is None or (trusted and not old[3]) or (trusted == old[3] and len(title) > len(old[0])):
            best[url] = (title, summary or (old[1] if old else ""), published or (old[2] if old else None), trusted)
        elif published and old[2] is None:
            best[url] = (old[0], old[1], published, old[3])
    cards = [Candidate(url, t, sm, d, feed) for url, (t, sm, d, _) in best.items()]
    dated = sorted((c for c in cards if c.published), key=lambda c: c.published, reverse=True)
    undated = [c for c in cards if not c.published][:INDEX_UNDATED_MAX]
    return (dated + undated)[:INDEX_MAX_LINKS]


PARSERS = {"rss": parse_feed_xml, "atom": parse_feed_xml, "hf-papers": parse_hf_papers, "hn": parse_hn, "index": parse_index}


def parse(payload: bytes, feed: Feed) -> list[Candidate]:
    return PARSERS[feed.kind](payload, feed)


# --------------------------------------------------------------------------------------
# Discovery: free, no model
# --------------------------------------------------------------------------------------

def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as resp:  # noqa: S310 - https/http only, fixed list
        # read(n) may return short on a chunked response, so read to the end (bounded).
        chunks, size = [], 0
        while size < FEED_MAX_BYTES:
            chunk = resp.read(min(65536, FEED_MAX_BYTES - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks)


def discover(stats: Stats, fetch=http_get, feeds=FEEDS) -> list[Candidate]:
    """Every feed's candidates. A feed that fails is recorded and skipped, never fatal."""
    found: list[Candidate] = []
    for feed in feeds:
        for attempt in (1, 2):  # one retry: a feed that answers badly once often answers well the second time
            try:
                found.extend(parse(fetch(feed.url), feed))
                stats.feeds_ok += 1
                break
            except Exception as e:  # a dead feed must not stop the others
                if attempt == 2:
                    stats.feed_errors.append(f"{feed.id}: {type(e).__name__}")
    stats.candidates = len(found)
    return found


# --------------------------------------------------------------------------------------
# Dedup, relevance, corroboration
# --------------------------------------------------------------------------------------

_STOP = set("the a an of to in on for and or with is are was be as at by from this that it its new how why what "
            "who you your we our can will has have not more than into about over after".split())


def title_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.-]*", title.lower()) if len(w) > 2 and w not in _STOP}


def same_story(a: str, b: str) -> bool:
    """Conservative: two titles from different pages report one story only if they share
    at least four distinctive words and at least half of the smaller title's words."""
    ta, tb = title_tokens(a), title_tokens(b)
    shared = ta & tb
    return len(shared) >= 4 and len(shared) / max(1, min(len(ta), len(tb))) >= 0.5


def build_items(conn, candidates: list[Candidate], now: datetime, stats: Stats, user_id: str = USER_ID):
    """Turn candidates into items: window, relevance, upsert, provenance, corroboration."""
    kept: list[tuple[object, Candidate]] = []
    for c in candidates:
        if c.published is not None and now - c.published > WINDOW:
            stats.out_of_window += 1
            continue
        score, topics = graph.score(conn, user_id, f"{c.title}. {c.summary}")
        if score == 0:
            stats.irrelevant += 1
            continue
        iid, created = items.upsert(conn, c.url, c.title, now, relevance=(score, topics))
        stats.new_items += created
        stats.known_items += not created
        # The publisher's own page is the item's primary provenance (upsert recorded it). An
        # aggregator that pointed at the page is a second source, never the item's author.
        if c.seen_url and items.add_provenance(conn, iid, c.seen_url, now,
                                               source_id=sources.for_domain(conn, sources.domain_of(c.seen_url))):
            stats.corroborated += 1
        kept.append((iid, c))
    # The same story reported at different addresses: attach each as corroboration.
    for i, (iid_a, a) in enumerate(kept):
        for iid_b, b in kept[i + 1:]:
            if iid_a != iid_b and sources.domain_of(a.url) != sources.domain_of(b.url) and same_story(a.title, b.title):
                stats.corroborated += items.add_provenance(conn, iid_a, b.url, now)
                stats.corroborated += items.add_provenance(conn, iid_b, a.url, now)
    return kept


# --------------------------------------------------------------------------------------
# Fetching: Claude's web fetch, through llm.call_message
# --------------------------------------------------------------------------------------

def fetch_domains(url: str) -> list[str]:
    """The domains the tool may fetch for this URL: its host, with and without www.

    A host is `.hostname` (no port, no userinfo). The www variant is allowed because the
    canonical form strips it and a site may only answer on one of the two.
    """
    host = (urlsplit(url).hostname or "").lower()
    bare = host[4:] if host.startswith("www.") else host
    return [bare, "www." + bare]


@dataclass(frozen=True)
class Fetched:
    text: str | None
    error: str | None
    cost_usd: float


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def extract_fetch_result(message, expect_domain: str | None = None) -> tuple[str | None, str | None]:
    """The page text, or the tool's error code, from a response's web_fetch_tool_result block.

    With `expect_domain`, a result whose own url is on a different domain is refused: a
    redirect to somewhere else must not be stored as the article we asked for.
    """
    error = None
    for block in message.content:
        if _get(block, "type") != "web_fetch_tool_result":
            continue
        result = _get(block, "content")
        kind = _get(result, "type")
        if kind == "web_fetch_tool_result_error":
            error = _get(result, "error_code") or "error"
        elif kind == "web_fetch_result":
            got_url = _get(result, "url") or ""
            if expect_domain and got_url and sources.domain_of(got_url) != expect_domain:
                error = f"redirected_to:{sources.domain_of(got_url)}"[:100]
                continue
            source = _get(_get(result, "content"), "source")
            if _get(source, "type") == "text":
                return _get(source, "data"), None
            error = f"unsupported_content_type:{_get(source, 'media_type')}"
    return None, error or "no_fetch_result"


def fetch_text(client, url: str, *, conn=None, phase: str = FETCH_PHASE, user_id: str = USER_ID) -> Fetched:
    """Fetch one article through Claude's web fetch. Booked in the ledger like any call."""
    tool = {"type": WEB_FETCH_TOOL, "name": "web_fetch", "max_uses": 1,
            "max_content_tokens": FETCH_MAX_CONTENT_TOKENS, "allowed_domains": fetch_domains(url)}
    message, _usage, cost = llm.call_message(
        client, model=FETCH_MODEL, max_tokens=FETCH_MAX_TOKENS, user_id=user_id, phase=phase, conn=conn,
        # The fetched page counts as input the request does not contain: bound it by its cap.
        extra_input_tokens=int(FETCH_MAX_CONTENT_TOKENS * 1.25),
        tools=[tool],
        messages=[{"role": "user", "content": f"Fetch this page and reply with only the word DONE: {url}"}],
    )
    text, error = extract_fetch_result(message, sources.domain_of(url))
    if text is not None and not text.strip():
        text, error = None, "empty_page"
    return Fetched(text, error, cost)


# Publication-date fields a fetched page's own metadata may carry, best first. `article:modified_time` is
# deliberately absent: a page edited today is not a story published today.
PUBLISHED_FIELDS = ("meta-article:published_time", "meta-og:article:published_time", "meta-citation_date",
                    "meta-citation_online_date", "meta-datepublished", "meta-date", "meta-pubdate")


def page_date(text: str) -> datetime | None:
    """The date the page itself says it was published, from its metadata, or None.

    Only the front-matter fields above are trusted. A date found in the prose is not: a post written
    today may be about a spec dated in July. A page that renders no date is not a dry source; it is
    dated by whatever else is known.
    """
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return None
    fields = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(": ")
        if sep:
            fields.setdefault(k.strip(), v.strip())
    for key in PUBLISHED_FIELDS:
        if key in fields:
            raw = fields[key].replace("/", "-")
            try:
                d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def fetch_and_store(conn, client, item_id, url: str, now: datetime, stats: Stats, *, enforce_window: bool = True) -> bool:
    """Fetch an item's body and attach it as its excerpt. Returns whether it worked.

    The primary source's own date outranks the date of whoever pointed at it: a page that says it was
    published before the window is skipped (after it was paid for, since only the page can say), even
    though a feed or an aggregator surfaced it today. `enforce_window` is off for a URL a person added.
    """
    if sources.is_blocked(sources.domain_of(url)):
        items.mark(conn, item_id, "skipped", error="this site refuses fetchers; use a syndicated copy and anchor to a primary document")
        stats.fetch_failed += 1
        return False
    got = fetch_text(client, url, conn=conn)  # BudgetExceeded and LedgerUnavailable propagate
    stats.fetch_cost_usd += got.cost_usd
    if got.text is None:
        items.mark(conn, item_id, "failed", error=got.error)
        stats.fetch_failed += 1
        return False
    dated = page_date(got.text) if enforce_window else None
    if dated is not None and now - dated > WINDOW:
        items.mark(conn, item_id, "discovered")    # a retried item is 'failed', which may not go straight to 'skipped'
        items.mark(conn, item_id, "skipped", error=f"its own page is dated {dated:%Y-%m-%d}: outside the window")
        stats.dated_out += 1
        return False
    try:
        eid = excerpts.store(conn, url, got.text.replace("\x00", ""), now)
    except excerpts.ExcerptRefused as e:
        items.mark(conn, item_id, "failed", error=f"excerpt refused: {e}"[:300])
        stats.fetch_failed += 1
        return False
    items.attach_excerpt(conn, item_id, eid)
    stats.fetched += 1
    return True


def select_for_fetch(conn, now: datetime, limit: int = MAX_FETCHES_PER_RUN) -> list[tuple]:
    """Items waiting for a body that may surface, best first. Anything else costs nothing.

    Only items first seen inside this run's window (the last WINDOW up to `now`) are
    considered: last month's news is not this week's story, and a run is judged by what it
    could see at its own time, not by whatever else the database holds.
    """
    rows = conn.execute(
        "SELECT i.id, i.canonical_url FROM items i WHERE (i.status = 'discovered' OR (i.status = 'failed' "
        "AND split_part(i.fetch_error, ':', 1) = ANY(%s))) AND i.relevance_score > 0 "
        "AND i.discovered_at > %s AND i.discovered_at <= %s ORDER BY i.relevance_score DESC, i.discovered_at DESC LIMIT 200",
        (list(TRANSIENT_FETCH_ERRORS), now - WINDOW, now)).fetchall()
    chosen, per_domain = [], {}
    blocked = sources.blocked_domains()
    for iid, url in rows:
        domain = sources.domain_of(url)
        if sources.is_blocked(domain, blocked):
            continue                       # refuses fetchers: discovery and corroboration only
        if per_domain.get(domain, 0) >= MAX_FETCHES_PER_DOMAIN:
            continue
        if items.surfaceable(conn, iid):
            per_domain[domain] = per_domain.get(domain, 0) + 1
            chosen.append((iid, url))
            if len(chosen) >= limit:
                break
    return chosen


def prepare(conn, profile: dict | None = None) -> None:
    """Seed the sources, attach the verified feeds' addresses, and load the interest graph."""
    sources.seed(conn)
    sources.seed_digest(conn)
    for feed in FEEDS:
        sources.set_feed(conn, feed.source_id, feed.domain, feed.url)
    graph.sync(conn, profile)


def window_fetched(conn, now: datetime) -> list[tuple]:
    """(item id, excerpt id) of every fetched item first seen inside the window, best first."""
    return conn.execute(
        "SELECT i.id, i.excerpt_id FROM items i WHERE i.status = 'fetched' AND i.discovered_at > %s AND i.discovered_at <= %s "
        "ORDER BY i.relevance_score DESC, i.discovered_at DESC", (now - WINDOW, now)).fetchall()


def run(conn, client, now: datetime, *, fetch=None, limit: int = MAX_FETCHES_PER_RUN,
        profile: dict | None = None, user_id: str = USER_ID, judge_fn=dedupe.judge) -> dict:
    """One Reader pass. Returns the pointers-only output the run record keeps."""
    stats = Stats()
    prepare(conn, profile)
    kept = build_items(conn, discover(stats, fetch or http_get), now, stats, user_id)
    # The most one fetch can be reserved at, times the count, must fit the run's ceiling.
    worst_case = budget.estimate_call_ceiling_usd(
        FETCH_MODEL, FETCH_MAX_TOKENS, {"tools": [{}], "messages": [{"role": "user", "content": "x" * 400}]},
        int(FETCH_MAX_CONTENT_TOKENS * 1.25))
    chosen = select_for_fetch(conn, now, min(limit, int(RUN_COST_CEILING_USD // worst_case)))
    stats.left_for_later = len({iid for iid, _ in kept}) - len({iid for iid, _ in chosen})
    last_domain = None
    for iid, url in chosen:
        domain = sources.domain_of(url)
        if domain == last_domain:
            time.sleep(FETCH_PAUSE_SECONDS)
        last_domain = domain
        fetch_and_store(conn, client, iid, url, now, stats)
    # Only what passed the relevance filter and was fetched is compared; a duplicate is folded into the
    # earlier item and left out of the output, and the merge is recorded so it can be undone.
    report = dedupe.dedupe_fetched(conn, client, [i for i, _ in window_fetched(conn, now)], now, judge_fn=judge_fn)
    stats.merged, stats.judged, stats.dedup_cost_usd = len(report["merged"]), report["judged"], report["cost_usd"]
    folded = dedupe.merged_away(conn, [i for i, _ in window_fetched(conn, now)])
    fetched = [(i, e) for i, e in window_fetched(conn, now) if i not in folded]
    return {
        "items": [str(i) for i, _ in fetched],
        "excerpts": [str(e) for _, e in fetched],
        "stats": stats.as_dict(),
        "dedup": report,
        "window": window_of(now),
    }


def add_url(conn, client, url: str, now: datetime, user_id: str = USER_ID) -> dict:
    """/add-news: a URL handed to the app is fetched on arrival through the same path.

    A person asked for this URL, so it skips the relevance filter and the surfaceable rule
    (the owner decides what is worth reading; the filters exist for what feeds throw at us).
    It still goes through the ledger, and an already-fetched URL costs nothing.
    """
    prepare(conn)
    canonical = excerpts.canonicalise(url)
    stats = Stats()
    iid, created = items.upsert(conn, canonical, canonical, now, relevance=(1, ["added by hand"]))
    status = conn.execute("SELECT status FROM items WHERE id = %s", (iid,)).fetchone()[0]
    if status == "fetched":
        return {"item": str(iid), "created": False, "fetched": True, "already_fetched": True, "cost_usd": 0.0}
    ok = fetch_and_store(conn, client, iid, url, now, stats, enforce_window=False)
    out = {"item": str(iid), "created": created, "fetched": ok, "cost_usd": round(stats.fetch_cost_usd, 6)}
    if ok:
        # The same layers as the weekly run: a capture of an article already held is a sighting, not a second story.
        report = dedupe.dedupe_fetched(conn, client, [i for i, _ in window_fetched(conn, now)] + [iid], now)
        mine = next((m for m in report["merged"] if m["dropped"] == str(iid)), None)
        out["merged_into"] = mine["kept"] if mine else None
        out["dedup_cost_usd"] = report["cost_usd"]
    return out


def run_stage(conn, client, now: datetime, week: str, stamp=None, **kw) -> dict:
    """Reader as the `reader` stage of a weekly run.

    A rerun of a week whose Reader already finished loads that output and fetches nothing
    (the checkpoint is the point of the `runs` table). A failure marks the attempt failed
    with its message, so the retry can start at once rather than wait out the lease.
    """
    stamp = stamp or now      # when the stage began and ended; `now` fixes the window the Reader looks at
    runs.open_run(conn, week, runs._at(stamp))
    return runs.checkpointed(conn, week, "reader", stamp, lambda attempt: run(conn, client, now, **kw))
