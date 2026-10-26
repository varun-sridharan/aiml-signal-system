"""The weekly capture import: POST /add-news/batch and agents/capture.py (Run 6, Step C). Free: nothing is fetched and no model is called."""

from __future__ import annotations

import json
import os
import sys
import unittest
import unittest.mock
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

import app as site  # noqa: E402
from agents import briefs, budget, capture, digest, llm, reader, sources, weekly  # noqa: E402
from tests._db import needs_db  # noqa: E402
from tests.test_weekly import NOW, WEEK, Pipeline  # noqa: E402

TOKEN = "t" * 24


class Route(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(site.app)

    def post(self, body, token=TOKEN, raw=None):
        headers = {"x-add-news-token": token} if token is not None else {}
        if raw is not None:
            return self.client.post("/add-news/batch", content=raw, headers=headers)
        return self.client.post("/add-news/batch", json=body, headers=headers)

    def test_off_until_a_token_is_configured(self):
        with unittest.mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": ""}):
            self.assertEqual(self.post([]).status_code, 503)

    def test_a_missing_or_wrong_token_is_forbidden_and_touches_no_database(self):
        with unittest.mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), \
                unittest.mock.patch.object(budget, "connect", side_effect=AssertionError("connected")):
            self.assertEqual(self.post([{"url": "https://example.org/a"}], token=None).status_code, 403)
            self.assertEqual(self.post([{"url": "https://example.org/a"}], token="x" * 24).status_code, 403)

    def test_the_batch_must_be_a_json_list_and_not_too_big(self):
        with unittest.mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), \
                unittest.mock.patch.object(budget, "connect", side_effect=AssertionError("connected")):
            self.assertEqual(self.post(None, raw=b"not json").status_code, 400)
            self.assertEqual(self.post({"url": "https://example.org/a"}).status_code, 400)
            self.assertEqual(self.post([{"url": f"https://example.org/{n}"} for n in range(capture.MAX_LINKS + 1)]).status_code, 413)
            self.assertEqual(self.post(None, raw=b" " * (site.MAX_BATCH_BYTES + 1)).status_code, 413)

    def test_it_calls_the_importer_and_never_the_model_or_the_fetcher(self):
        @contextmanager
        def connect():
            yield object()
        answer = {"received": 1, "added": 1, "merged": 0, "already_known": 0, "rejected": 0, "links": [{"url": "u", "outcome": "added"}]}
        with unittest.mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), unittest.mock.patch.object(budget, "connect", connect), \
                unittest.mock.patch.object(capture, "import_batch", return_value=answer) as imp, \
                unittest.mock.patch.object(llm, "build_client", side_effect=AssertionError("built a client")), \
                unittest.mock.patch.object(llm, "call", side_effect=AssertionError("paid call")), \
                unittest.mock.patch.object(reader, "fetch_text", side_effect=AssertionError("fetched")), \
                unittest.mock.patch.object(reader, "http_get", side_effect=AssertionError("fetched")):
            r = self.post([{"url": "https://example.org/a", "addedAt": "2026-10-24T10:00:00Z", "note": "n"}])
        self.assertEqual((r.status_code, r.json()), (200, answer))
        self.assertEqual(imp.call_args.args[1], [{"url": "https://example.org/a", "addedAt": "2026-10-24T10:00:00Z", "note": "n"}])

    def test_a_store_that_fails_is_a_502_that_says_to_post_again(self):
        with unittest.mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), unittest.mock.patch.object(budget, "connect", side_effect=RuntimeError("down")):
            r = self.post([{"url": "https://example.org/a"}])
        self.assertEqual(r.status_code, 502)
        self.assertIn("post it again", r.json()["detail"])

    def test_an_object_with_a_links_list_is_accepted_too(self):
        @contextmanager
        def connect():
            yield object()
        with unittest.mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), unittest.mock.patch.object(budget, "connect", connect), \
                unittest.mock.patch.object(capture, "import_batch", return_value={"received": 0}) as imp:
            self.assertEqual(self.post({"links": [{"url": "https://example.org/a"}]}).status_code, 200)
        self.assertEqual(imp.call_args.args[1], [{"url": "https://example.org/a"}])


class Addresses(unittest.TestCase):
    def test_only_public_web_addresses_pass(self):
        for good in ("https://example.com/a", "http://blog.example.co.uk/post?id=3", "https://example.com:443/x", "https://arxiv.org/abs/2610.00001"):
            self.assertIsNone(capture.address_problem(good), good)
        for bad in ("http://exa mple.com", "https://user:pw@host.com/a", "http://localhost/x", "http://169.254.169.254/latest", "https://example.com:0/",
                    "https://a.b:abc", "https://[::1]/x", "http://printer.local/x", "https://intranet/x", "ftp://example.com/a", "mailto:a@b.com", "https:///nohost"):
            self.assertIsNotNone(capture.address_problem(bad), bad)

    def test_deeply_nested_json_is_a_400_not_a_500(self):
        with unittest.mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}):
            r = TestClient(site.app).post("/add-news/batch", content=b"[" * 30000, headers={"x-add-news-token": TOKEN})
        self.assertEqual(r.status_code, 400)


@needs_db
class Import(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.before = self.conn.execute("SELECT count(*) FROM items").fetchone()[0]
        self.spend = self.conn.execute("SELECT count(*) FROM spend_ledger").fetchone()[0]
        reader.prepare(self.conn)
        self.now = datetime(2026, 10, 30, 1, 30, tzinfo=timezone.utc)
        self.blocker = unittest.mock.patch.multiple(llm, call=unittest.mock.Mock(side_effect=AssertionError("paid call")),
                                                    build_client=unittest.mock.Mock(side_effect=AssertionError("client")))
        self.blocker.start()
        self.nofetch = unittest.mock.patch.object(reader, "fetch_text", side_effect=AssertionError("fetched"))
        self.nofetch.start()

    def tearDown(self):
        self.nofetch.stop()
        self.blocker.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT count(*) FROM items").fetchone()[0], self.before, "a test leaked an item")
            self.assertEqual(check.execute("SELECT count(*) FROM spend_ledger").fetchone()[0], self.spend, "the import booked spend")

    def run_batch(self, links):
        return capture.import_batch(self.conn, links, self.now)

    def row(self, url):
        return self.conn.execute("SELECT status, relevance_score, relevance_terms, discovered_at FROM items WHERE canonical_url = %s",
                                 (url,)).fetchone()

    def test_a_new_link_is_added_flagged_and_unfetched(self):
        r = self.run_batch([{"url": "https://www.example.org/backlog-post-1?utm_source=x", "addedAt": "2026-10-24T10:00:00Z", "note": "read this"}])
        self.assertEqual((r["received"], r["added"], r["merged"], r["already_known"], r["rejected"]), (1, 1, 0, 0, 0))
        link = r["links"][0]
        self.assertEqual((link["outcome"], link["canonical"]), ("added", "https://example.org/backlog-post-1"))
        status, score, terms, found = self.row("https://example.org/backlog-post-1")
        self.assertEqual((status, score, list(terms), found), ("discovered", capture.BACKLOG_SCORE, [capture.BACKLOG_TERM], self.now))

    def test_posting_the_same_batch_twice_changes_nothing(self):
        batch = [{"url": "https://example.org/backlog-post-2"}, {"url": "https://example.org/backlog-post-3", "note": "x"}]
        self.run_batch(batch)
        state = self.conn.execute("SELECT count(*), count(DISTINCT canonical_url) FROM items").fetchone()
        sightings = self.conn.execute("SELECT count(*) FROM item_sources").fetchone()[0]
        again = self.run_batch(batch)
        self.assertEqual((again["added"], again["already_known"]), (0, 2))
        self.assertEqual(self.conn.execute("SELECT count(*), count(DISTINCT canonical_url) FROM items").fetchone(), state)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM item_sources").fetchone()[0], sightings)

    def test_a_link_already_held_is_reported_not_re_added(self):
        known = self.conn.execute("SELECT canonical_url FROM items LIMIT 1").fetchone()
        if known is None:
            self.skipTest("no item in the database")
        r = self.run_batch([{"url": known[0]}])
        self.assertEqual((r["links"][0]["outcome"], r["added"]), ("already-known", 0))

    def test_a_link_a_feed_already_found_this_week_is_flagged_and_moved_up(self):
        from agents import items
        iid, _ = items.upsert(self.conn, "https://feed.example.org/post-9", "A post", self.now - timedelta(days=2), relevance=(3, ["evals"]))
        r = self.run_batch([{"url": "https://feed.example.org/post-9"}])
        self.assertEqual(r["links"][0]["outcome"], "already-known")
        self.assertIn("flagged from your backlog", r["links"][0]["detail"])
        _status, score, terms, _found = self.row("https://feed.example.org/post-9")
        self.assertEqual((score, list(terms)), (capture.BACKLOG_SCORE, ["evals", capture.BACKLOG_TERM]))
        self.run_batch([{"url": "https://feed.example.org/post-9"}])               # again: no second term
        self.assertEqual(list(self.row("https://feed.example.org/post-9")[2]), ["evals", capture.BACKLOG_TERM])

    def test_an_old_or_skipped_link_is_reported_not_revived(self):
        from agents import items
        items.upsert(self.conn, "https://feed.example.org/old-10", "Old", self.now - timedelta(days=30), relevance=(3, ["evals"]))
        r = self.run_batch([{"url": "https://feed.example.org/old-10"}])
        self.assertIn("not within this week's reach", r["links"][0]["detail"])
        self.assertEqual(list(self.row("https://feed.example.org/old-10")[2]), ["evals"])

    def test_a_link_listed_twice_in_one_batch_is_added_once(self):
        r = self.run_batch([{"url": "https://example.org/backlog-post-4"}, {"url": "https://example.org/backlog-post-4/#frag"}])
        self.assertEqual([x["outcome"] for x in r["links"]], ["added", "already-known"])

    def test_the_same_paper_at_another_address_is_a_sighting_of_the_item_held(self):
        from agents import items
        iid, _ = items.upsert(self.conn, "https://arxiv.org/abs/2610.99999", "A paper", self.now)
        r = self.run_batch([{"url": "https://huggingface.co/papers/2610.99999"}])
        link = r["links"][0]
        self.assertEqual((link["outcome"], link["into"]), ("merged", str(iid)))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM items WHERE canonical_url LIKE %s", ("%2610.99999%",)).fetchone()[0], 1)
        self.assertEqual(self.run_batch([{"url": "https://huggingface.co/papers/2610.99999"}])["links"][0]["outcome"], "already-known")

    def test_the_report_says_why_a_link_was_rejected(self):
        blocked = next(iter(sources.blocked_domains()))
        r = self.run_batch([{"url": "ftp://example.org/a"}, {"url": "not a url"}, {"url": "https://example.org/" + "x" * 300},
                            {"url": f"https://{blocked}/some-post"}, {"note": "no url"}, "https://example.org/bare-string", {"url": 42}])
        self.assertEqual([x["outcome"] for x in r["links"]], ["rejected"] * 5 + ["added", "rejected"])
        self.assertIn("refuses fetchers", r["links"][3]["detail"])
        self.assertIn("http(s)", r["links"][0]["detail"])
        self.assertEqual((r["added"], r["rejected"]), (1, 6))

    def test_a_batch_that_is_not_a_list_or_is_too_long_is_refused_whole(self):
        with self.assertRaises(ValueError):
            self.run_batch({"url": "https://example.org/a"})
        with self.assertRaises(ValueError):
            self.run_batch([{"url": f"https://example.org/{n}"} for n in range(capture.MAX_LINKS + 1)])

    def test_an_item_in_a_published_edition_reports_its_story(self):
        row = self.conn.execute("SELECT s ->> 'itemId', b.id, s ->> 'id' FROM briefs b, jsonb_array_elements(b.body #> '{edition,stories}') s "
                                "ORDER BY b.published_at DESC LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no published edition with stories")
        self.assertEqual(capture.story_of(self.conn, row[0]), {"week": row[1], "story": row[2]})
        self.assertIsNone(capture.story_of(self.conn, "00000000-0000-0000-0000-000000000000"))


@needs_db
class ReachesThePage(Pipeline, unittest.TestCase):
    """An imported link is fetched by the weekly run, reaches the brief flagged, and the page shows the flag on that story only."""

    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.conn.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items)").fetchone()
        self.start()

    def tearDown(self):
        self.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items)").fetchone(), self.live)

    def test_the_backlog_flag_reaches_the_brief_and_the_template(self):
        reader.prepare(self.conn)
        report = capture.import_batch(self.conn, [{"url": "https://backlog.example.org/a-saved-post"}], NOW - timedelta(hours=2))
        self.assertEqual(report["added"], 1)
        saved = report["links"][0]["item"]
        self.assertEqual(len(self.client.requests), 0)                           # importing fetched nothing
        weekly.run_week(self.conn, self.client, NOW, WEEK)
        self.assertGreater(len(self.client.requests), 0)                         # the weekly run did
        _id, body, at = briefs.get(self.conn, WEEK)
        flagged = [i["id"] for i in body["items"] if i.get("fromBacklog")]
        self.assertEqual(flagged, [saved])
        view = digest.view(WEEK, body, at, 1)
        rows = view["stories"]["rows"]
        self.assertEqual([r["item_id"] for r in rows if r["from_backlog"]], [saved])
        self.assertGreater(len(rows), 1)
        with unittest.mock.patch.object(site, "_connect", lambda: _Conn(self.conn)):
            html = TestClient(site.app).get(f"/weekly-digest?week={WEEK}").text
        self.assertEqual(html.count("FROM YOUR BACKLOG"), 1)


class _Conn:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    unittest.main()
