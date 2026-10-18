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

from agents import budget, excerpts, items, llm, reader, runs  # noqa: E402
from tests._db import needs_db  # noqa: E402

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
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

    def __init__(self, **message_kwargs):
        self.kwargs, self.requests = message_kwargs, []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        asked = request["messages"][0]["content"].rsplit(": ", 1)[-1] if request.get("messages") else "https://x.test/"
        return fetch_result_message(url=asked, **self.kwargs)   # the tool answers with the page it was asked for


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
        client = FakeClient(text="MCP went stateless. " * 20)
        out = self.run_reader(client)
        stats = out["stats"]
        self.assertEqual(stats["out_of_window"], 1)                # the June post
        self.assertEqual(stats["new_items"], 3)                    # openai post, arxiv paper, the HN-linked blog
        # The openai post (tier A) and the arXiv paper (tier A, also on HF) are fetched. The
        # blog Hacker News linked to is an unrated domain seen only via a low-prior
        # aggregator: discovery only, never fetched alone.
        self.assertEqual((stats["fetched"], stats["not_surfaceable"]), (2, 1))
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
        status, err = self.conn.execute("SELECT status, fetch_error FROM items WHERE status = 'failed'").fetchone()
        self.assertEqual((status, err), ("failed", "url_not_accessible"))

    def test_r5_a_page_is_never_stored_as_text(self):
        out = self.run_reader(FakeClient(text="<html><body>raw page</body></html>"))
        self.assertEqual(out["stats"]["fetched"], 0)
        err = self.conn.execute("SELECT fetch_error FROM items WHERE status = 'failed' LIMIT 1").fetchone()[0]
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
        chosen = reader.select_for_fetch(self.conn, limit=20)
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
        urls = {u.rsplit("/", 1)[1] for _, u in reader.select_for_fetch(self.conn)}
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

    def test_r8_call_message_books_like_call(self):
        before = self.counts()[2]
        message, usage, cost = llm.call_message(FakeClient(), model="claude-haiku-4-5", max_tokens=200, user_id="test",
                                                phase="test-call-message", conn=self.conn, messages=[])
        self.assertEqual(self.counts()[2] - before, 1)
        self.assertGreater(cost, 0)
        self.assertEqual(items.ItemRefused.__name__, "ItemRefused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
