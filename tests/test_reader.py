"""Reader: parsing, filtering, corroboration and fetching, without a network or a paid call.

    python -m unittest tests.test_reader -v

Feeds are fixtures; the model client is a fake that returns a canned web-fetch result.
Database tests run in rolled-back transactions and skip when the database is unreachable
(start the tunnel first), or fail with REQUIRE_DB=1.
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, excerpts, items, llm, reader, runs, sources  # noqa: E402
from tests._db import needs_db  # noqa: E402

# Earlier on 2026-10-18 than the first real run, so that run's items (all discovered later) are outside this window.
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=timezone.utc)
RSS_FEED = reader.Feed("openai", "openai", "openai.com", "https://openai.com/news/rss.xml", "rss")
HN_FEED = next(f for f in reader.FEEDS if f.id == "hacker-news")
HF_FEED = next(f for f in reader.FEEDS if f.id == "hf-papers")
ATOM_FEED = reader.Feed("arxiv", "arxiv", "arxiv.org", "http://export.arxiv.org/api/query", "atom")

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>MCP servers get stateless transport</title><link>https://openai.com/index/mcp-stateless/?utm_source=rss</link>
<description>&lt;p&gt;A multi-agent &lt;b&gt;MCP&lt;/b&gt; change.&lt;/p&gt;</description><pubDate>Thu, 15 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Old post</title><link>https://openai.com/index/old/</link><description>evals</description><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
<item><title>No link here</title><description>x</description></item></channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Agents that coordinate</title>
<link href="http://arxiv.org/abs/2610.00001v1" rel="alternate"/><summary>Multi-agent systems paper.</summary>
<published>2026-10-16T08:00:00Z</published></entry></feed>"""
HF = json.dumps([{"paper": {"id": "2610.00001", "title": "Agents that coordinate", "summary": "Multi-agent paper.",
                            "publishedAt": "2026-10-16T08:00:00.000Z"}}, {"paper": {"id": None, "title": "x"}}]).encode()
HN = json.dumps({"hits": [
    {"title": "Show HN: a multi-agent MCP thing", "url": "https://small-blog.example/post", "objectID": "42",
     "created_at": "2026-10-17T12:00:00Z"},
    {"title": "Ask HN: anything", "url": None, "objectID": "43", "created_at": "2026-10-17T12:00:00Z"}]}).encode()

ANTHROPIC_INDEX = b"""<html><body><nav><a href="/news">News</a><a href="/research">Research</a><a href="/research/team/alignment">Alignment team</a></nav>
<a href="/news/cyber-verification-program" class="card"><div><time>Oct 6, 2026</time><span>Announcements</span></div><span class="x-title">Expanding the Cyber <em>Verification</em> Program</span><p>We are launching a new, expanded version.</p></a>
<a href="/news/public-benefit-fund"><div><span>Announcements</span><time datetime="2026-10-18T00:00:00Z">Oct 18, 2026</time></div><h4>Investing in the public benefit</h4></a>
<a href="/news/public-benefit-fund"><span>Read now</span></a>
<a href="/news/no-date-here"><h3>A post whose card shows no date</h3></a>
<a href="/news/x?utm_source=y#frag"><h3>Query and fragment are dropped from the link</h3></a>
<a href="/news/bad-date"><time>Feb 31, 2026</time><h3>A card with an impossible date</h3></a>
<a href="/research/team/economics"><h3>Economics team page is not a post</h3></a>
<a href="/research/the-missing-map"><div><span>Frontier Red Team</span><time>Oct 8, 2026</time></div><h4>The missing map of the sky</h4></a>
<a href="https://elsewhere.example/news/offsite-post"><h3>An off-site link with a news path</h3></a>
<a href="/news/two-words"><h3>Introducing Claude</h3></a>
</body></html>"""
ANTHROPIC_NEWS = reader.Feed("anthropic-news", "anthropic", "anthropic.com", "https://www.anthropic.com/news", "index",
                             r"https://www\.anthropic\.com/news/[a-z0-9][a-z0-9-]*")
ANTHROPIC_RESEARCH = reader.Feed("anthropic-research", "anthropic", "anthropic.com", "https://www.anthropic.com/research", "index",
                                 r"https://www\.anthropic\.com/research/(?!team/)[a-z0-9][a-z0-9-]*")


def fetch_result_message(text="Article body about MCP.", error=None, media_type="text/plain", input_tokens=2500,
                         url="https://x.test/"):
    if error:
        result = {"type": "web_fetch_tool_result_error", "error_code": error}
    else:
        source = {"type": "text", "media_type": media_type, "data": text} if media_type == "text/plain" else \
                 {"type": "base64", "media_type": media_type, "data": "JVBERi0="}
        result = {"type": "web_fetch_result", "url": url, "content": {"type": "document", "source": source}}
    return SimpleNamespace(
        stop_reason="end_turn", stop_details=None,
        content=[SimpleNamespace(type="text", text="ok"), {"type": "web_fetch_tool_result", "tool_use_id": "t", "content": result},
                 SimpleNamespace(type="text", text="DONE")],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=5, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0))


class FakeClient:
    """Stands in for anthropic.Anthropic: records requests, returns canned fetch results."""

    def __init__(self, unique=False, **message_kwargs):
        # unique: every page gets its own words after the canned text, as real pages do. Without it every
        # address serves the same text, which the deduplication gate (rightly) folds into one story.
        self.unique, self.kwargs, self.requests = unique, message_kwargs, []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        asked = request["messages"][0]["content"].rsplit(": ", 1)[-1] if request.get("messages") else "https://x.test/"
        kwargs = dict(self.kwargs)
        if self.unique:
            import hashlib
            words = " ".join(hashlib.sha256(f"{asked}|{i}".encode()).hexdigest()[:8] for i in range(400))
            kwargs["text"] = kwargs.get("text", "Article body about MCP.") + " " + words
        return fetch_result_message(url=asked, **kwargs)   # the tool answers with the page it was asked for


class Parsing(unittest.TestCase):
    def test_rss_with_html_summary_and_missing_link(self):
        got = reader.parse(RSS, RSS_FEED)
        self.assertEqual([c.title for c in got], ["MCP servers get stateless transport", "Old post"])
        self.assertEqual(got[0].summary, "A multi-agent MCP change.")
        self.assertEqual(got[0].published, datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc))

    def test_atom(self):
        (c,) = reader.parse(ATOM, ATOM_FEED)
        self.assertEqual((c.url, c.title), ("http://arxiv.org/abs/2610.00001v1", "Agents that coordinate"))
        self.assertEqual(c.published.year, 2026)

    def test_hf_papers_point_at_arxiv_and_remember_where_they_were_seen(self):
        (c,) = reader.parse(HF, HF_FEED)
        self.assertEqual((c.url, c.seen_url), ("https://arxiv.org/abs/2610.00001", "https://huggingface.co/papers/2610.00001"))

    def test_hn_keeps_external_links_only(self):
        (c,) = reader.parse(HN, HN_FEED)
        self.assertEqual((c.url, c.seen_url), ("https://small-blog.example/post", "https://news.ycombinator.com/item?id=42"))

    def test_a_dead_feed_is_recorded_not_fatal(self):
        def fetch(url):
            if "openai" in url:
                raise OSError("down")
            return RSS
        stats = reader.Stats()
        got = reader.discover(stats, fetch, feeds=[RSS_FEED, reader.Feed("o2", "openai", "openai.com", "https://x/y", "rss")])
        self.assertEqual((stats.feeds_ok, len(stats.feed_errors), len(got)), (1, 1, 2))
        self.assertIn("OSError", stats.feed_errors[0])

    def test_same_story_is_conservative(self):
        self.assertTrue(same := reader.same_story("OpenAI closes record $122B funding round at $852B valuation",
                                                  "OpenAI closes record funding round at $852B valuation, Reuters"), same)
        self.assertFalse(reader.same_story("MCP spec goes stateless", "Kimi K3 released with agentic benchmarks"))
        self.assertFalse(reader.same_story("New model released", "New model released today"))  # too few distinctive words


class IndexPages(unittest.TestCase):
    def test_links_titles_and_dates_come_from_the_page_and_only_from_the_page(self):
        with unittest.mock.patch.object(reader, "INDEX_UNDATED_MAX", 10):      # the cap has its own test
            got = {c.url: c for c in reader.parse(ANTHROPIC_INDEX, ANTHROPIC_NEWS)}
        self.assertEqual(set(got), {"https://www.anthropic.com/news/cyber-verification-program",
                                    "https://www.anthropic.com/news/public-benefit-fund",
                                    "https://www.anthropic.com/news/no-date-here",
                                    "https://www.anthropic.com/news/x", "https://www.anthropic.com/news/bad-date",
                                    "https://www.anthropic.com/news/two-words"})   # nav, team pages, off-site links, 'Read now' are not posts
        first = got["https://www.anthropic.com/news/cyber-verification-program"]
        self.assertEqual((first.title, first.published), ("Expanding the Cyber Verification Program", datetime(2026, 10, 6, tzinfo=timezone.utc)))   # inline <em> does not split a title
        self.assertIn("expanded version", first.summary)
        fund = got["https://www.anthropic.com/news/public-benefit-fund"]
        self.assertEqual((fund.title, fund.published), ("Investing in the public benefit", datetime(2026, 10, 18, tzinfo=timezone.utc)))
        self.assertIsNone(got["https://www.anthropic.com/news/no-date-here"].published)      # undated stays undated
        self.assertIsNone(got["https://www.anthropic.com/news/bad-date"].published)          # an impossible date is no date, not a failed feed
        self.assertEqual(got["https://www.anthropic.com/news/two-words"].title, "Introducing Claude")   # a two-word title is a title
        self.assertEqual(got["https://www.anthropic.com/news/x"].url, "https://www.anthropic.com/news/x")   # query and fragment dropped

    def test_a_category_label_is_never_the_title(self):
        research = {c.url: c for c in reader.parse(ANTHROPIC_INDEX, ANTHROPIC_RESEARCH)}
        self.assertEqual(set(research), {"https://www.anthropic.com/research/the-missing-map"})   # /research/team/... is a team page
        self.assertEqual(research["https://www.anthropic.com/research/the-missing-map"].title, "The missing map of the sky")   # not 'Frontier Red Team'

    def test_titles_from_an_aria_label_or_the_anchors_own_text(self):
        page = (b'<a aria-label="Read How Meta&#x2019;s models help" href="https://ai.meta.com/blog/one/">FEATURED</a>'
                b'<a href="https://ai.meta.com/blog/two/"> A card that is only text in the anchor </a>')
        feed = reader.Feed("meta-ai", "meta-ai", "ai.meta.com", "https://ai.meta.com/blog/", "index", r"https://ai\.meta\.com/blog/(?!page/)[a-z0-9][a-z0-9-]*/?")
        got = {c.url: c.title for c in reader.parse(page, feed)}
        self.assertEqual(got, {"https://ai.meta.com/blog/one/": "How Meta\u2019s models help", "https://ai.meta.com/blog/two/": "A card that is only text in the anchor"})

    def test_no_url_is_ever_built_from_a_headline(self):
        html_text = ANTHROPIC_INDEX.decode()
        for feed in (ANTHROPIC_NEWS, ANTHROPIC_RESEARCH):
            for c in reader.parse(ANTHROPIC_INDEX, feed):
                path = c.url.split("anthropic.com", 1)[1]
                self.assertIn(f'href="{path}', html_text, c.url)       # the address is literally in the page

    def test_an_index_yields_a_bounded_number_of_candidates_dated_first(self):
        many = "".join(f'<a href="/news/post-{i}"><time>Oct {i + 1}, 2026</time><h3>A long enough post title number {i}</h3></a>'
                       for i in range(20)).encode()
        got = reader.parse(many, ANTHROPIC_NEWS)
        self.assertEqual(len(got), reader.INDEX_MAX_LINKS)
        self.assertEqual(got[0].url, "https://www.anthropic.com/news/post-19")              # newest first, whatever the page order
        undated = "".join(f'<a href="/news/u-{i}"><h3>An undated post number {i}</h3></a>' for i in range(10)).encode()
        self.assertEqual(len(reader.parse(undated, ANTHROPIC_NEWS)), reader.INDEX_UNDATED_MAX)   # few unknowns let through

    def test_a_hostile_or_truncated_page_is_parsed_in_linear_time(self):
        import time
        started = time.monotonic()
        reader.parse(b"<a href='/news/x'>" * 60_000, ANTHROPIC_NEWS)                         # no closing tags at all
        reader.parse(b"<a href='/news/y'><div>" * 5_000, ANTHROPIC_NEWS)
        self.assertLess(time.monotonic() - started, 5)

    def test_an_unreadable_index_is_a_recorded_feed_error_not_a_crash(self):
        def refuse(url):
            raise OSError("403")
        stats = reader.Stats()
        self.assertEqual(reader.discover(stats, refuse, [ANTHROPIC_NEWS]), [])
        self.assertEqual(stats.feed_errors, ["anthropic-news: OSError"])


class PageDates(unittest.TestCase):
    def test_the_pages_own_publication_date_not_its_modification_date(self):
        page = "---\nmeta-article:published_time: 2026-09-01T10:00:00+00:00\nmeta-article:modified_time: 2026-10-17T10:00:00+00:00\n---\n\nBody."
        self.assertEqual(reader.page_date(page), datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc))
        self.assertIsNone(reader.page_date("---\nmeta-article:modified_time: 2026-10-17T10:00:00+00:00\n---\n\nBody."))   # modified is not published
        self.assertEqual(reader.page_date("---\nmeta-citation_date: 2026/10/15\n---\nBody."), datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.assertEqual(reader.page_date("---\nmeta-article:published_time: 2026-09-01T10:00:00Z\n---\n"), datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc))

    def test_a_date_in_the_prose_is_not_a_publication_date(self):
        self.assertIsNone(reader.page_date("---\ncanonical: x\n---\n\nOn July 28, 2026 the specification changed."))
        self.assertIsNone(reader.page_date("No front matter. Published Oct 6, 2026."))
        self.assertIsNone(reader.page_date("---\nmeta-article:published_time: not a date\n---\nBody."))


class WindowLabel(unittest.TestCase):
    def test_the_label_names_the_dates_the_reader_read(self):
        end = datetime(2026, 10, 18, 5, 56, tzinfo=timezone.utc)
        self.assertEqual(reader.window_label(end - reader.WINDOW, end), "Oct 10 \u2013 Oct 18, 2026")
        self.assertEqual(reader.window_label(datetime(2026, 12, 28, tzinfo=timezone.utc), datetime(2027, 1, 5, tzinfo=timezone.utc)),
                         "Dec 28, 2026 \u2013 Jan 5, 2027")
        got = reader.window_of(end)
        self.assertEqual((got["from"], got["to"]), ((end - reader.WINDOW).isoformat(), end.isoformat()))
        self.assertEqual(got["label"], "Oct 10 \u2013 Oct 18, 2026")


class FetchResult(unittest.TestCase):
    def test_text_error_and_pdf(self):
        self.assertEqual(reader.extract_fetch_result(fetch_result_message("hello")), ("hello", None))
        self.assertEqual(reader.extract_fetch_result(fetch_result_message(error="url_not_allowed")), (None, "url_not_allowed"))
        text, err = reader.extract_fetch_result(fetch_result_message(media_type="application/pdf"))
        self.assertIsNone(text)
        self.assertIn("application/pdf", err)
        self.assertEqual(reader.extract_fetch_result(SimpleNamespace(content=[SimpleNamespace(type="text", text="x")])),
                         (None, "no_fetch_result"))

    def test_the_worst_case_estimate_covers_the_fetched_page(self):
        request = {"tools": [{}], "messages": [{"role": "user", "content": "Fetch this page: https://x.test/a"}]}
        plain = budget.estimate_call_ceiling_usd(reader.FETCH_MODEL, reader.FETCH_MAX_TOKENS, request)
        withpage = budget.estimate_call_ceiling_usd(reader.FETCH_MODEL, reader.FETCH_MAX_TOKENS, request,
                                                    int(reader.FETCH_MAX_CONTENT_TOKENS * 1.25))
        # A full 8,000-token page at the cache-write rate: the part the old estimate missed.
        self.assertAlmostEqual(withpage - plain, 10_000 * 1.00 * 1.25 / 1_000_000, places=6)


@needs_db
class ReaderAgainstDatabase(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.counts()
        pause = unittest.mock.patch.object(reader, "FETCH_PAUSE_SECONDS", 0)   # the real pause is for real sites
        pause.start()
        self.addCleanup(pause.stop)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        conn = conn or self.conn
        return conn.execute("SELECT (SELECT count(*) FROM items), (SELECT count(*) FROM excerpts), "
                            "(SELECT count(*) FROM spend_ledger), (SELECT count(*) FROM item_sources), "
                            "(SELECT count(*) FROM runs), (SELECT count(*) FROM run_stages)").fetchone()

    def feeds(self, *pairs):
        table = dict(pairs)
        return lambda url: table[next(u for u in table if url.startswith(u[:30]))]

    def run_reader(self, client, **kw):
        fetch = self.feeds((RSS_FEED.url, RSS), (ATOM_FEED.url, ATOM), (HF_FEED.url, HF), (HN_FEED.url, HN))
        feeds = [RSS_FEED, ATOM_FEED, HF_FEED, HN_FEED]
        original = reader.FEEDS
        reader.FEEDS = feeds
        try:
            return reader.run(self.conn, client, NOW, fetch=fetch, **kw)
        finally:
            reader.FEEDS = original

    def test_r1_filters_by_window_and_relevance_then_fetches_what_may_surface(self):
        client = FakeClient(unique=True, text="MCP went stateless. " * 20)
        out = self.run_reader(client)
        stats = out["stats"]
        self.assertEqual(stats["out_of_window"], 1)                # the June post
        self.assertEqual(stats["new_items"], 3)                    # openai post, arxiv paper, the HN-linked blog
        # The openai post (tier A) and the arXiv paper (tier A, also on HF) are fetched. The
        # blog Hacker News linked to is an unrated domain seen only via a low-prior
        # aggregator: discovery only, never fetched alone.
        self.assertEqual((stats["fetched"], stats["left_for_later"]), (2, 1))
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(len(out["excerpts"]), 2)
        for k in ("items", "excerpts"):                            # pointers only, never text
            self.assertTrue(all(len(v) == 36 for v in out[k]))
        row = self.conn.execute("SELECT status FROM items WHERE canonical_url = 'https://small-blog.example/post'").fetchone()
        self.assertEqual(row, ("discovered",))
        # Provenance: arXiv paper has its own page and the Hugging Face listing.
        roles = self.conn.execute(
            "SELECT i.role, s.id FROM item_sources i JOIN items it ON it.id = i.item_id "
            "JOIN sources s ON s.id = i.source_id WHERE it.canonical_url = 'https://arxiv.org/abs/2610.00001' ORDER BY 1, 2").fetchall()
        self.assertIn(("corroboration", "hugging-face-papers"), roles)
        self.assertIn(("primary", "arxiv"), roles)

    def test_r2_a_rerun_fetches_nothing_it_already_has(self):
        self.run_reader(FakeClient(text="MCP went stateless. " * 20))
        client = FakeClient(text="should never be asked for")
        out = self.run_reader(client)
        self.assertEqual(client.requests, [])
        self.assertEqual(out["stats"]["fetched"], 0)
        self.assertEqual(out["stats"]["new_items"], 0)

    def test_r3_every_fetch_goes_through_the_ledger_with_a_bounded_page(self):
        client = FakeClient(text="MCP went stateless. " * 20)
        before = self.counts()[2]
        self.run_reader(client)
        self.assertEqual(self.counts()[2] - before, 2)             # one booked row per fetch
        tool = client.requests[0]["tools"][0]
        self.assertEqual((tool["type"], tool["max_uses"], tool["max_content_tokens"]),
                         (reader.WEB_FETCH_TOOL, 1, reader.FETCH_MAX_CONTENT_TOKENS))
        self.assertEqual(tool["allowed_domains"], ["openai.com", "www.openai.com"])
        self.assertEqual(client.requests[0]["model"], "claude-haiku-4-5")
        phases = {r[0] for r in self.conn.execute(
            "SELECT phase FROM spend_ledger ORDER BY ts DESC LIMIT 2").fetchall()}
        self.assertEqual(phases, {"reader-fetch"})

    def test_r4_the_fetch_cap_and_failures(self):
        out = self.run_reader(FakeClient(text="MCP went stateless. " * 20), limit=1)
        self.assertEqual(out["stats"]["fetched"], 1)
        failing = FakeClient(error="url_not_accessible")
        out = self.run_reader(failing, limit=5)
        self.assertEqual((out["stats"]["fetched"], out["stats"]["fetch_failed"]), (0, 1))
        status, err = self.conn.execute("SELECT status, fetch_error FROM items WHERE status = 'failed' AND discovered_at = %s", (NOW,)).fetchone()
        self.assertEqual((status, err), ("failed", "url_not_accessible"))

    def test_r5_a_page_is_never_stored_as_text(self):
        out = self.run_reader(FakeClient(text="<html><body>raw page</body></html>"))
        self.assertEqual(out["stats"]["fetched"], 0)
        err = self.conn.execute("SELECT fetch_error FROM items WHERE status = 'failed' AND discovered_at = %s LIMIT 1", (NOW,)).fetchone()[0]
        self.assertIn("excerpt refused", err)
        self.assertEqual(self.counts()[1], self.live[1])  # nothing stored

    def test_r6_the_ceiling_stops_the_reader_before_it_spends(self):
        original = budget.MONTHLY_BUDGET_USD
        budget.MONTHLY_BUDGET_USD = budget.month_to_date_spend(self.conn, NOW) + budget.Decimal("0.0001")
        try:
            client = FakeClient(text="x" * 50)
            with self.assertRaises(budget.BudgetExceeded):
                self.run_reader(client)
            self.assertEqual(client.requests, [])                  # refused before the request
        finally:
            budget.MONTHLY_BUDGET_USD = original

    def test_r7_add_news_fetches_on_arrival_through_the_same_path(self):
        client = FakeClient(text="A hand-added article body.")
        out = reader.add_url(self.conn, client, "https://www.Example.org/post/One/?utm_source=x", NOW)
        self.assertEqual((out["created"], out["fetched"]), (True, True))
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.requests[0]["tools"][0]["allowed_domains"], ["example.org", "www.example.org"])
        eid = self.conn.execute("SELECT excerpt_id FROM items WHERE id = %s", (out["item"],)).fetchone()[0]
        self.assertEqual(excerpts.get(self.conn, eid), "A hand-added article body.")
        again_client = FakeClient(text="A hand-added article body, edited.")
        again = reader.add_url(self.conn, again_client, "https://example.org/post/One", NOW)
        self.assertEqual((again["created"], again["already_fetched"], again_client.requests), (False, True, []))  # free

    def test_r9_one_domain_cannot_take_the_whole_fetch_budget(self):
        reader.prepare(self.conn)
        for i in range(7):
            items.upsert(self.conn, f"https://arxiv.org/abs/2610.0010{i}", f"Paper {i} on agents", NOW, relevance=(9, ["x"]))
        for i in range(2):
            items.upsert(self.conn, f"https://openai.com/index/post-{i}", f"Post {i} on MCP", NOW, relevance=(3, ["x"]))
        chosen = reader.select_for_fetch(self.conn, NOW, limit=20)
        domains = [reader.sources.domain_of(u) for _, u in chosen]
        self.assertEqual(domains.count("arxiv.org"), reader.MAX_FETCHES_PER_DOMAIN)
        self.assertEqual(domains.count("openai.com"), 2)   # lower score, but not crowded out

    def test_r10_a_week_resumes_at_the_framer_without_refetching(self):
        client = FakeClient(text="MCP went stateless. " * 20)
        original, reader.FEEDS = reader.FEEDS, [RSS_FEED, ATOM_FEED, HF_FEED, HN_FEED]
        fetch = self.feeds((RSS_FEED.url, RSS), (ATOM_FEED.url, ATOM), (HF_FEED.url, HF), (HN_FEED.url, HN))
        try:
            first = reader.run_stage(self.conn, client, NOW, "1999-W03", fetch=fetch)
            asked = len(client.requests)
            again = reader.run_stage(self.conn, client, NOW + timedelta(hours=1), "1999-W03", fetch=fetch)
        finally:
            reader.FEEDS = original
        self.assertEqual(first, again)                       # the checkpoint, loaded
        self.assertEqual(len(client.requests), asked)        # nothing fetched the second time
        self.assertEqual(runs.next_stage(self.conn, "1999-W03"), "framer")
        out = runs.stage_output(self.conn, "1999-W03", "reader")
        self.assertTrue(all(len(v) == 36 for v in out["items"] + out["excerpts"]))   # pointers only

    def test_r11_a_failed_reader_marks_its_attempt_failed_so_a_retry_can_start_at_once(self):
        class Boom(FakeClient):
            def create(self, **request):
                raise RuntimeError("network down")
        original, reader.FEEDS = reader.FEEDS, [RSS_FEED]
        try:
            with self.assertRaises(RuntimeError):
                reader.run_stage(self.conn, Boom(), NOW, "1999-W04", fetch=lambda url: RSS)
            status, error = self.conn.execute(
                "SELECT status, error FROM run_stages WHERE run_id = '1999-W04' AND stage = 'reader'").fetchone()
            self.assertEqual(status, "failed")
            self.assertIn("network down", error)
            self.assertEqual(runs.start_stage(self.conn, "1999-W04", "reader", NOW + timedelta(minutes=1)), 2)
        finally:
            reader.FEEDS = original

    def test_r12_only_failures_that_could_be_temporary_are_retried(self):
        reader.prepare(self.conn)
        for name, error in (("a", "url_not_allowed"), ("b", "too_many_requests"), ("c", "unsupported_content_type:application/pdf"),
                            ("d", "unavailable")):
            iid, _ = items.upsert(self.conn, f"https://openai.com/index/{name}", name, NOW, relevance=(5, ["x"]))
            items.mark(self.conn, iid, "failed", error=error)
        urls = {u.rsplit("/", 1)[1] for _, u in reader.select_for_fetch(self.conn, NOW)}
        self.assertEqual(urls, {"b", "d"})

    def test_r13_the_fetch_reserves_for_the_page_it_will_pull_in(self):
        seen = []
        real = budget.estimate_call_ceiling_usd

        def spy(model, max_tokens, request, extra_input_tokens=0):
            seen.append(extra_input_tokens)
            return real(model, max_tokens, request, extra_input_tokens)
        with unittest.mock.patch.object(budget, "estimate_call_ceiling_usd", spy):
            reader.fetch_text(FakeClient(text="x" * 50), "https://openai.com/index/a", conn=self.conn)
        self.assertEqual(seen, [int(reader.FETCH_MAX_CONTENT_TOKENS * 1.25)])

    def test_r14_a_redirect_to_another_site_is_not_stored_as_the_article(self):
        message = fetch_result_message("Some other site's page.")
        message.content[1]["content"]["url"] = "https://elsewhere.example/landing"
        self.assertEqual(reader.extract_fetch_result(message, "openai.com"), (None, "redirected_to:elsewhere.example"))
        message.content[1]["content"]["url"] = "https://www.openai.com/index/a"   # www is the same site
        self.assertEqual(reader.extract_fetch_result(message, "openai.com")[1], None)

    def test_r15_one_run_cannot_be_planned_past_its_cost_ceiling(self):
        worst = budget.estimate_call_ceiling_usd(
            reader.FETCH_MODEL, reader.FETCH_MAX_TOKENS, {"tools": [{}], "messages": [{"role": "user", "content": "x" * 400}]},
            int(reader.FETCH_MAX_CONTENT_TOKENS * 1.25))
        self.assertLessEqual(reader.MAX_FETCHES_PER_RUN * worst, reader.RUN_COST_CEILING_USD)   # today's cap fits with room
        original = reader.RUN_COST_CEILING_USD
        reader.RUN_COST_CEILING_USD = worst * 1.5        # a ceiling that fits only one fetch
        try:
            client = FakeClient(text="MCP went stateless. " * 20)
            out = self.run_reader(client)
        finally:
            reader.RUN_COST_CEILING_USD = original
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(out["stats"]["fetched"], 1)

    def test_r16_a_second_fetch_from_the_same_site_waits(self):
        reader.prepare(self.conn)
        for i in range(2):
            items.upsert(self.conn, f"https://openai.com/index/pause-{i}", f"Post {i} on agents", NOW, relevance=(5, ["x"]))
        slept = []
        with unittest.mock.patch.object(reader, "FETCH_PAUSE_SECONDS", 7), \
                unittest.mock.patch.object(reader.time, "sleep", lambda s: slept.append(s)):
            original, reader.FEEDS = reader.FEEDS, []
            try:
                reader.run(self.conn, FakeClient(text="Some page text about agents."), NOW, fetch=lambda url: b"<rss/>")
            finally:
                reader.FEEDS = original
        self.assertEqual(slept, [7])        # two fetches from one site: one pause between them, none before the first

    def test_r17_the_pages_own_date_outranks_the_feed_that_surfaced_it(self):
        old = "---\nmeta-article:published_time: 2026-07-01T10:00:00+00:00\n---\n\nMCP went stateless. " * 3
        client = FakeClient(text=old)
        out = self.run_reader(client)
        self.assertEqual(out["stats"]["dated_out"], 2)                       # both fetched pages say July
        self.assertEqual(out["stats"]["fetched"], 0)
        row = self.conn.execute("SELECT status, fetch_error FROM items WHERE canonical_url = 'https://openai.com/index/mcp-stateless'").fetchone()
        self.assertEqual(row[0], "skipped")
        self.assertIn("2026-07-01", row[1])
        again = FakeClient(text=old)
        self.run_reader(again)
        self.assertEqual(again.requests, [])                                  # a skipped item is not paid for twice

    def test_r18_an_undated_page_is_not_a_dry_source(self):
        out = self.run_reader(FakeClient(text="MCP went stateless. " * 20))
        self.assertEqual((out["stats"]["fetched"], out["stats"]["dated_out"]), (2, 0))

    def test_r19_a_retried_item_found_to_be_old_can_still_be_skipped(self):
        reader.prepare(self.conn)
        iid, _ = items.upsert(self.conn, "https://openai.com/index/was-busy", "Was busy", NOW, relevance=(3, ["x"]))
        items.mark(self.conn, iid, "failed", error="too_many_requests")
        old = "---\nmeta-article:published_time: 2026-07-01T10:00:00+00:00\n---\n\nBody text."
        stats = reader.Stats()
        self.assertFalse(reader.fetch_and_store(self.conn, FakeClient(text=old), iid, "https://openai.com/index/was-busy", NOW, stats))
        self.assertEqual(self.conn.execute("SELECT status FROM items WHERE id = %s", (iid,)).fetchone()[0], "skipped")

    def test_r20_a_url_a_person_adds_is_not_held_to_the_window(self):
        old = "---\nmeta-article:published_time: 2026-07-01T10:00:00+00:00\n---\n\nAn old post the owner wants anyway."
        out = reader.add_url(self.conn, FakeClient(text=old), "https://example.org/old-post", NOW)
        self.assertTrue(out["fetched"])

    def test_r21_a_blocked_domain_is_never_fetched(self):
        reader.prepare(self.conn)
        iid, _ = items.upsert(self.conn, "https://www.reuters.com/technology/ai-story", "Agents raise money", NOW, relevance=(9, ["agents"]))
        items.add_provenance(self.conn, iid, "https://openai.com/index/x", NOW, role="corroboration")   # even surfaceable...
        self.assertNotIn(iid, [i for i, _ in reader.select_for_fetch(self.conn, NOW)])               # ...it refuses fetchers
        self.assertTrue(sources.is_blocked("www.reuters.com") and sources.is_blocked("arstechnica.com"))
        self.assertFalse(sources.is_blocked("notreuters.com"))
        for sneaky in ("reuters.com:443", "user@reuters.com", "reuters.com.", "WWW.Reuters.com"):
            self.assertTrue(sources.is_blocked(sources.domain_of("https://" + sneaky + "/x") if "@" not in sneaky and ":" not in sneaky else sneaky), sneaky)

    def test_r23_the_same_article_at_two_addresses_is_one_story_in_the_output_and_can_be_undone(self):
        from agents import dedupe
        client = FakeClient(text="MCP went stateless. " * 20)            # every address serves the same text
        out = self.run_reader(client)
        self.assertEqual(out["stats"]["fetched"], 2)
        self.assertEqual((out["stats"]["merged"], len(out["items"]), len(out["dedup"]["merged"])), (1, 1, 1))
        merged = out["dedup"]["merged"][0]
        self.assertEqual(merged["layer"], "fingerprint")
        self.assertNotIn(merged["dropped"], out["items"])
        self.assertIn(merged["kept"], out["items"])
        self.assertEqual(self.conn.execute("SELECT status FROM items WHERE id = %s", (merged["dropped"],)).fetchone()[0], "fetched")   # kept, not deleted
        did = self.conn.execute("SELECT id FROM dedup_decisions WHERE other_id = %s", (merged["dropped"],)).fetchone()[0]
        dedupe.undo_merge(self.conn, did, "test", NOW)
        again = self.run_reader(client)
        self.assertEqual((len(again["items"]), again["stats"]["merged"]), (2, 0))      # the undo holds across runs

    def test_r22_even_a_person_added_url_on_a_blocked_domain_is_not_fetched(self):
        client = FakeClient(text="should never be asked for")
        out = reader.add_url(self.conn, client, "https://www.reuters.com/technology/some-story", NOW)
        self.assertFalse(out["fetched"])
        self.assertEqual(client.requests, [])                                                 # no paid attempt that is known to fail

    def test_r8_call_message_books_like_call(self):
        before = self.counts()[2]
        message, usage, cost = llm.call_message(FakeClient(), model="claude-haiku-4-5", max_tokens=200, user_id="test",
                                                phase="test-call-message", conn=self.conn, messages=[])
        self.assertEqual(self.counts()[2] - before, 1)
        self.assertGreater(cost, 0)
        self.assertEqual(items.ItemRefused.__name__, "ItemRefused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
