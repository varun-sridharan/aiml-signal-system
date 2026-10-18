"""The Weekly Digest page: what it shows, who may vote, and that a bad brief cannot take it down.

    python -m unittest tests.test_digest -v

The view-model tests are offline. The route tests run the real app against the real database
inside one rolled-back transaction (the app's connection is swapped for it), so no brief or
vote survives. Skipped when the database is unreachable, or failing with REQUIRE_DB=1.
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

import app as site  # noqa: E402
from agents import briefs, budget, digest, runs  # noqa: E402
from tests._db import needs_db  # noqa: E402

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
WEEK = "1999-W20"
GOLDEN = json.loads((REPO / "data" / "evals" / "golden" / "2026-08-04" / "output.json").read_text())


class View(unittest.TestCase):
    def test_the_golden_week_splits_into_learning_and_stories(self):
        v = digest.view(WEEK, GOLDEN)
        self.assertEqual([i["id"] for i in v["learning"]], ["2026-08-04-mcp", "2026-08-04-istar"])   # the DO THIS items
        self.assertEqual(len(v["stories"]), 5)
        self.assertTrue(v["thread"].startswith("The frontier stopped"))
        self.assertEqual(v["reading_time"], 5)
        istar = v["learning"][1]
        self.assertEqual([label for label, _ in istar["ninety"]], ["Method", "Result", "Caveat"])
        self.assertTrue(all(i["sources"] for i in v["learning"] + v["stories"]))

    def test_unsafe_links_are_not_links(self):
        body = json.loads(json.dumps(GOLDEN))
        body["items"][0]["sources"] = [{"label": "x", "url": "javascript:alert(1)", "tier": "primary"},
                                       {"label": "ok", "url": "https://example.com/a", "tier": "primary"}]
        self.assertEqual([s["url"] for s in digest.view(WEEK, body)["learning"][0]["sources"]], ["https://example.com/a"])

    def test_malformed_briefs_are_refused_with_one_exception(self):
        bad = [None, [], {}, {"items": "nope"}, {"items": []}, {"items": [1]}, {"items": [{"id": "a"}]},
               {"items": [{"id": "a", "headline": "h"}, {"id": "a", "headline": "h2"}]},
               {"items": [{"id": "a", "headline": 5}]}, {"items": [{"id": "a", "headline": "h", "ninety": "text"}]},
               {"items": [{"id": "x" * 300, "headline": "h"}]}]
        for body in bad:
            with self.assertRaises(digest.DigestMalformed, msg=repr(body)[:60]):
                digest.view(WEEK, body)

    def test_every_kind_of_wrong_shape_raises_only_digest_malformed(self):
        def item(**extra):
            return {"items": [{"id": "a", "headline": "h", **extra}]}
        wrong = [item(sources=[{"label": "x", "url": "http://[x"}]),            # urlsplit itself raises on this
                 item(sources=5), item(sources=True), item(tags=5), item(tags="DO THIS"),
                 item(ninety={"method": 5}), item(sources=[{"url": ["not", "text"]}]),
                 {"items": [{"id": "a", "headline": "h"}, None]}]
        for body in wrong:
            try:
                digest.view(WEEK, body)
            except digest.DigestMalformed:
                pass   # refused cleanly
            # (a body that is merely unusual and still shows is fine; what must not happen is any other exception)
        shown = digest.view(WEEK, item(sources=[{"label": "x", "url": "http://[x"}]))   # the bad link is dropped, the story stays
        self.assertEqual(shown["stories"][0]["sources"], [])
        self.assertIsNone(digest.view(WEEK, {**item(), "readingTimeMin": True})["reading_time"])

    def test_the_voter_address_is_taken_from_the_nearest_proxy_hop(self):
        self.assertEqual(digest.client_address({"x-forwarded-for": "6.6.6.6, 203.0.113.9"}, "10.0.0.1"), "203.0.113.9")
        self.assertEqual(digest.client_address({}, "10.0.0.1"), "10.0.0.1")

    def test_the_salt_is_long_stable_and_secret_derived(self):
        with unittest.mock.patch.dict("os.environ", {"VOTE_SALT": "", "DATABASE_URL": "postgresql://u:pw@h/db"}):
            a = digest.vote_salt()
        with unittest.mock.patch.dict("os.environ", {"VOTE_SALT": "", "DATABASE_URL": "postgresql://u:pw@h/db"}):
            b = digest.vote_salt()
        self.assertEqual((a, len(a)), (b, 64))
        self.assertNotIn("pw", a)
        with unittest.mock.patch.dict("os.environ", {"VOTE_SALT": "", "DATABASE_URL": ""}):
            self.assertEqual(digest.vote_salt(), "")           # no secret, no voting
        with unittest.mock.patch.dict("os.environ", {"VOTE_SALT": "an-explicit-salt-of-16+"}):
            self.assertEqual(digest.vote_salt(), "an-explicit-salt-of-16+")


class Borrowed:
    """Lends the test's rolled-back connection to the app, and never closes or commits it."""

    def __init__(self, conn):
        self.conn = conn

    def __call__(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


@needs_db
class Routes(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.counts()
        self.patch = unittest.mock.patch.object(site, "_connect", Borrowed(self.conn))
        self.patch.start()
        self.client = TestClient(site.app)

    def tearDown(self):
        self.patch.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        conn = conn or self.conn
        return conn.execute("SELECT (SELECT count(*) FROM briefs), (SELECT count(*) FROM feedback), (SELECT count(*) FROM runs)").fetchone()

    def publish(self, body=GOLDEN, week=WEEK):
        runs.open_run(self.conn, week, NOW)
        briefs.publish(self.conn, week, week, body, NOW)

    def vote(self, item="2026-08-04-mcp", vote="up", comment=None, week=WEEK, headers=None, follow=False):
        data = {"brief": week, "item": item, "vote": vote}
        if comment is not None:
            data["comment"] = comment
        return self.client.post("/weekly-digest/vote", data=data, headers=headers or {}, follow_redirects=follow)

    # the page -------------------------------------------------------------------------
    def test_d1_with_no_edition_the_page_says_so(self):
        original = briefs.latest
        with unittest.mock.patch.object(briefs, "latest", lambda conn: None):
            r = self.client.get("/weekly-digest")
        self.assertEqual(r.status_code, 200)
        self.assertIn("first edition has not been published", r.text)
        self.assertIsNotNone(original)

    def test_d2_a_published_brief_shows_both_sections_a_sticky_subnav_and_vote_controls(self):
        self.publish()
        r = self.client.get(f"/weekly-digest?week={WEEK}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("published Oct 18, 2026", r.text)
        for needle in ("Learning for the Week", "This Week's Stories", 'class="subnav"', 'href="#learning"', 'href="#stories"',
                       "The frontier stopped", "Useful</button>", "Not useful</summary>", 'name="comment"', "spec moves the core"):
            self.assertIn(needle, r.text, needle)
        self.assertEqual(r.text.count('action="/weekly-digest/vote"'), 2 * len(GOLDEN["items"]))   # an up form and a down form each
        self.assertLess(r.text.index(">Method<"), r.text.index(">Caveat<"))   # reading order survives jsonb

    def test_d3_the_header_links_to_the_page_and_the_old_url_redirects(self):
        self.assertIn('href="/weekly-digest"', self.client.get("/plan").text)
        old = self.client.get("/application/Weekly-Digest.html", follow_redirects=False)
        self.assertEqual((old.status_code, old.headers["location"]), (301, "/weekly-digest"))

    def test_d4_a_malformed_brief_is_an_error_panel_not_a_500(self):
        for body in ({"items": "nope"}, {"items": [{"id": "a"}]}, {}):
            self.conn.rollback()
            self.conn.execute("SELECT 1")
            self.publish(body)
            r = self.client.get(f"/weekly-digest?week={WEEK}")     # by key: the database also holds a real, newer edition
            self.assertEqual(r.status_code, 200, body)
            self.assertIn("cannot be shown", r.text)
            self.assertIn('role="alert"', r.text)
            self.assertIn("/plan", r.text)   # the site chrome is still there

    def test_d5_brief_text_is_never_trusted_as_html(self):
        body = json.loads(json.dumps(GOLDEN))
        body["items"][1]["headline"] = "<script>alert('x')</script> & <img src=x onerror=alert(1)>"
        body["thread"] = "<b onmouseover=alert(1)>thread</b>"
        self.publish(body)
        r = self.client.get(f"/weekly-digest?week={WEEK}")
        self.assertNotIn("<script>alert('x')", r.text)
        self.assertNotIn("<img src=x", r.text)
        self.assertIn("&lt;script&gt;alert(", r.text)
        self.assertNotIn("<b onmouseover", r.text)

    def test_d6_a_dead_database_is_a_503_panel(self):
        def down():
            raise budget.LedgerUnavailable("no database")
        with unittest.mock.patch.object(site, "_connect", down):
            r = self.client.get("/weekly-digest")
        self.assertEqual(r.status_code, 503)
        self.assertIn("unavailable right now", r.text)
        self.assertNotIn("no database", r.text)                  # internals are not shown

    def test_d7_bad_week_keys_and_unknown_weeks(self):
        self.assertEqual(self.client.get("/weekly-digest?week=../../etc").status_code, 404)
        self.assertEqual(self.client.get("/weekly-digest?week=1999-W44").status_code, 404)

    # voting ---------------------------------------------------------------------------
    def test_v1_anyone_can_vote_and_only_a_hash_is_kept(self):
        self.publish()
        r = self.vote(headers={"x-forwarded-for": "198.51.100.77"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("notice=voted-up", r.headers["location"])
        (row,) = self.conn.execute("SELECT item_ref, vote, client_hash FROM feedback WHERE brief_id = %s", (WEEK,)).fetchall()
        self.assertEqual(row[:2], ("2026-08-04-mcp", "up"))
        self.assertRegex(row[2], r"^[0-9a-f]{64}$")
        self.assertNotIn("198.51.100.77", json.dumps(self.conn.execute("SELECT * FROM feedback").fetchall(), default=str))
        shown = self.client.get(r.headers["location"])
        self.assertIn("Marked useful", shown.text)

    def test_v2_a_thumbs_down_keeps_the_note_and_a_thumbs_up_refuses_one(self):
        self.publish()
        self.assertEqual(self.vote(vote="down", comment="Too vague to act on.").status_code, 303)
        self.assertEqual(self.conn.execute("SELECT comment FROM feedback WHERE vote = 'down'").fetchone()[0], "Too vague to act on.")
        r = self.vote(vote="up", comment="great")
        self.assertIn("notice=bad-vote", r.headers["location"])

    def test_v3_votes_for_things_not_in_the_brief_are_refused(self):
        self.publish()
        before = self.counts()
        for item, vote, week in (("not-an-item", "up", WEEK), ("2026-08-04-mcp", "sideways", WEEK), ("2026-08-04-mcp", "up", "1999-W44"),
                                 ("2026-08-04-mcp", "up", "nonsense")):
            r = self.vote(item=item, vote=vote, week=week)
            self.assertEqual(r.status_code, 303)
            self.assertIn("notice=bad-vote", r.headers["location"], (item, vote, week))
        self.assertEqual(self.counts(), before)

    def test_v4_a_client_cannot_vote_without_limit(self):
        self.publish()
        headers = {"x-forwarded-for": "203.0.113.50"}
        for _ in range(briefs.RATE_LIMIT_VOTES):
            self.assertIn("voted-up", self.vote(headers=headers).headers["location"])
        self.assertIn("notice=slow-down", self.vote(headers=headers).headers["location"])
        self.assertIn("voted-up", self.vote(headers={"x-forwarded-for": "203.0.113.51"}).headers["location"])   # someone else

    def test_v5_oversized_and_broken_submissions(self):
        self.assertEqual(self.client.post("/weekly-digest/vote", content=b"x" * 20_000).status_code, 413)
        r = self.client.post("/weekly-digest/vote", content=b"\xff\xfe garbage", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("bad-vote", r.headers["location"])

    def test_v7_a_vote_with_too_many_fields_is_refused_not_a_500(self):
        self.publish()
        body = "&".join(f"f{i}=x" for i in range(30)) + f"&brief={WEEK}&item=2026-08-04-mcp&vote=up"
        r = self.client.post("/weekly-digest/vote", content=body, headers={"content-type": "application/x-www-form-urlencoded"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("bad-vote", r.headers["location"])

    def test_v8_a_cross_site_post_is_refused_and_a_same_site_one_is_not(self):
        self.publish()
        before = self.counts()
        r = self.vote(headers={"origin": "https://evil.example", "host": "testserver"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.counts(), before)
        ok = self.vote(headers={"origin": "http://testserver", "host": "testserver"})
        self.assertEqual(ok.status_code, 303)

    def test_v9_the_body_is_capped_as_it_is_read(self):
        def chunks():
            for _ in range(10):
                yield b"x" * 4_000       # no Content-Length to check up front
        r = self.client.post("/weekly-digest/vote", content=chunks())
        self.assertEqual(r.status_code, 413)

    def test_v10_a_week_key_with_a_trailing_newline_is_not_a_week(self):
        self.assertEqual(self.client.get("/weekly-digest?week=1999-W20%0A").status_code, 404)

    def test_v11_notes_survive_a_browsers_line_endings_and_refuse_nul(self):
        self.publish()
        long_note = "\r\n".join(["x" * 99] * 15)                          # 1,500 visible characters; 1,515 with CRLF counted as two
        self.assertIn("voted-down", self.vote(vote="down", comment=long_note).headers["location"])
        stored = self.conn.execute("SELECT comment FROM feedback").fetchone()[0]
        self.assertNotIn("\r", stored)
        self.assertIn("bad-vote", self.vote(vote="down", comment="bad \x00 note").headers["location"])

    def test_v12_everyone_together_is_capped_too(self):
        self.publish()
        with unittest.mock.patch.object(briefs, "GLOBAL_LIMIT_VOTES", 3):
            for n in range(3):
                self.assertIn("voted-up", self.vote(headers={"x-forwarded-for": f"198.51.100.{n}"}).headers["location"])
            self.assertIn("slow-down", self.vote(headers={"x-forwarded-for": "198.51.100.99"}).headers["location"])

    def test_v13_the_thanks_appears_on_the_story_that_was_voted_on(self):
        self.publish()
        r = self.vote(item="2026-08-04-kimi-k3", follow=True)
        card = r.text.split('id="item-2026-08-04-kimi-k3"')[1].split("</article>")[0]
        self.assertIn("Marked useful", card)
        self.assertEqual(r.text.count("Marked useful"), 1)                         # once, not also at the top

    def test_v6_voting_needs_a_salt_and_says_nothing_about_it(self):
        self.publish()
        with unittest.mock.patch.object(digest, "vote_salt", lambda: ""):
            r = self.vote()
        self.assertIn("notice=bad-vote", r.headers["location"])
        self.assertEqual(self.counts()[1], self.live[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
