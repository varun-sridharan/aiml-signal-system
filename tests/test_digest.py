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
WEEK = "1998-W20"
GOLDEN = json.loads((REPO / "data" / "evals" / "golden" / "2026-08-04" / "output.json").read_text())


def composed_body():
    """A brief written by the composing pass: GOLDEN's items plus the `edition` the template's slot list asks for."""
    from agents import composer
    from tests.test_composer import raw_day, valid
    day = raw_day()
    items = [{"id": i["id"], "category": "AI", "headline": i["headline"], "tags": i["tags"], "why": f"Why {i['id']}.",
              "sources": [{"label": s["label"], "url": s["url"], "tier": s["tier"]} for s in i["sources"]],
              "faithfulness": {"confidence": 0.9, "unsupported_claims": []}} for i in day["items"]]
    verdict = {"confidence": 0.9, "claims": [{"claim": "c"}], "unsupported_claims": []}
    composed = valid()
    edition = composer.assemble_edition(composed, day, {"items": [{"id": i["id"], "why": f"Why {i['id']}."} for i in day["items"]]},
                                        {f"s{n:02d}": verdict for n in range(1, 6)}, verdict, stats={"candidates": 1700, "fetched": 12, "feeds_ok": 17},
                                        source_minutes=34, style=[])
    return {"date": "2026-10-20", "thread": "a first-pass thread", "items": items, "edition": edition, "window": {
        "from": "2026-10-12T05:00:00+00:00", "to": "2026-10-20T05:00:00+00:00", "label": "Oct 12 \u2013 Oct 20, 2026"}}


class View(unittest.TestCase):
    def test_an_older_brief_shows_the_fields_it_has_and_no_others(self):
        v = digest.view(WEEK, GOLDEN, None, 1)
        self.assertEqual((v["number"], v["composed"], v["sections"]), ("01", False, ["thread", "stories", "leaders"]))   # no essay: it was never written
        self.assertIsNone(v["learning"])
        self.assertIsNone(v["thread"]["lines"])
        self.assertTrue(v["thread"]["body"][0]["text"].startswith("The frontier stopped"))
        rows = v["stories"]["rows"]
        self.assertEqual((len(rows), v["stories"]["closing"]), (7, None))
        self.assertEqual([r["num"] for r in rows], [f"{n:02d}" for n in range(1, 8)])
        self.assertEqual(rows[2]["tag"], "DO THIS")                                            # the pill: DO THIS first, then RADAR, then KNOW
        self.assertEqual([k for k, _ in rows[2]["ninety"]], ["METHOD", "RESULT", "CAVEAT"])
        self.assertTrue(all(len(r["sources"]) >= 1 and sum(s["primary"] for s in r["sources"]) == 1 for r in rows))
        self.assertTrue(all(r["hashtags"] == [] and not r["feature"] for r in rows))
        self.assertEqual(v["leaders"]["count"], 0)

    def test_a_composed_edition_carries_every_slot(self):
        body = composed_body()
        v = digest.view(WEEK, body, None, 2)
        self.assertEqual((v["number"], v["composed"], v["range_line"]), ("02", True, "OCT-12-2026 TO OCT-20-2026"))
        self.assertEqual(v["sections"], ["thread", "learning", "stories", "leaders"])
        self.assertEqual([(k, c) for k, _, c in v["subnav"]], [("thread", None), ("learning", None), ("stories", 5), ("leaders", 0)])
        lines = v["thread"]["lines"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(v["thread"]["count"], "5 signals")
        links = [r for r in v["thread"]["body"] if r["kind"] == "link"]
        self.assertEqual([r["href"] for r in links], ["#s01", "#s02"])
        learning = v["learning"]
        self.assertEqual([n["key"] for n in learning["notes"]], ["queue"])                        # numbered by first appearance
        fn = [r for p in learning["paras"] for r in p["segs"] if r["kind"] == "fn"]
        self.assertEqual((fn[0]["n"], fn[0]["term"]), (1, "review queue"))
        self.assertEqual([d["num"] for d in learning["drawn"]], ["01", "02"])
        self.assertEqual(len(learning["numbers"]), 3)
        self.assertEqual(learning["numbers"][0], {"label": "CANDIDATE LINKS CONSIDERED", "value": "1700"})
        rows = v["stories"]["rows"]
        self.assertEqual([r["feature"] for r in rows], [True, True, False, False, False])
        self.assertEqual(rows[0]["tie"], {"num": "02", "href": "#s02", "text": "Helio Labs shipped product 2 the following day."})
        self.assertEqual([r["divider"] for r in rows], [None, None, "Smaller releases", None, None])
        self.assertTrue(all(r["hashtags"] and r["tag"] in ("DO THIS", "KNOW", "RADAR") for r in rows))
        self.assertEqual(sum(s["primary"] for s in rows[0]["sources"]), 1)
        self.assertEqual(rows[0]["sources"][0]["prefix"], "Primary: ")
        self.assertEqual(v["stories"]["closing"]["l1"], "One lab shipped a queue.")

    def test_held_stories_are_listed_with_their_page_and_nothing_else_of_theirs(self):
        body = composed_body()
        body["edition"]["held"] = [{"id": "s03", "headline": "Parcel 2.0 adds durable execution", "why": "could not be verified",
                                    "sources": [{"label": "Parcel", "href": "https://parcel.example/2", "primary": True}], "body": "never shown"},
                                   {"id": "../x", "headline": "bad id"}, "not a dict"]
        v = digest.view(WEEK, body, None, 2)
        held = v["stories"]["held"]
        self.assertEqual([h["id"] for h in held], ["s03"])
        self.assertEqual(held[0]["sources"][0]["href"], "https://parcel.example/2")
        self.assertNotIn("body", held[0])
        self.assertEqual(digest.view(WEEK, composed_body(), None, 2)["stories"]["held"], [])

    def test_tokens_that_point_nowhere_become_plain_text(self):
        body = composed_body()
        body["edition"]["thread"]["body"] = "See [[#s02|story two]] and [[#s99|story ninety-nine]] and [[fn:nope|a term]] and [[bogus|x]]."
        v = digest.view(WEEK, body)
        kinds = [(r["kind"], r["text"]) for r in v["thread"]["body"]]
        self.assertIn(("link", "story two"), kinds)
        self.assertIn(("plain", "story ninety-nine"), kinds)
        self.assertIn(("plain", "a term"), kinds)
        self.assertIn(("plain", "x"), kinds)
        self.assertNotIn("[[", "".join(t for _, t in kinds))

    def test_a_tie_to_a_story_not_in_the_edition_is_hidden_and_the_one_highlight_is_painted_once(self):
        body = composed_body()
        body["edition"]["stories"][0]["tie"] = {"to": "s77", "text": "Nowhere."}
        self.assertIsNone(digest.view(WEEK, body)["stories"]["rows"][0]["tie"])
        body["edition"]["thread"]["lines"] = ["Four [[hl|releases]] cut cost.", "Three [[hl|decisions]] restricted who may look."]
        first, second = digest.view(WEEK, body)["thread"]["lines"]
        self.assertEqual([r["kind"] for r in first], ["plain", "hl", "plain"])
        self.assertTrue(all(r["kind"] == "plain" for r in second))

    def test_a_malformed_edition_is_refused_cleanly(self):
        for mutate in (lambda e: e.pop("closing"), lambda e: e.update(stories=[]), lambda e: e["stories"][0].update(itemId="no-such-item"),
                       lambda e: e["learning"].update(paras="text"), lambda e: e.update(thread=[]), lambda e: e["stories"][0].pop("headline")):
            body = composed_body()
            mutate(body["edition"])
            with self.assertRaises(digest.DigestMalformed):
                digest.view(WEEK, body)

    def test_brittle_limits_clip_instead_of_refusing_the_edition(self):
        body = composed_body()
        body["edition"]["stories"][2]["hashtags"] = ["#a", "#b", "#c", "#d", "#e", "#f"]
        body["edition"]["stories"][0]["featureParas"] = ["one", "two", "three", "four", "five", "six", "seven"]
        body["edition"]["thread"]["lines"] = ["one", "two", "three"]
        v = digest.view(WEEK, body)
        self.assertEqual(len(v["stories"]["rows"][2]["hashtags"]), 4)
        self.assertEqual(len(v["stories"]["rows"][0]["feature_paras"]), 5)
        self.assertIsNone(v["thread"]["lines"])

    def test_a_feature_with_no_readable_paragraphs_is_not_a_feature_and_keeps_its_body(self):
        body = composed_body()
        body["edition"]["stories"][0]["featureParas"] = [" ", ""]
        row = digest.view(WEEK, body)["stories"]["rows"][0]
        self.assertFalse(row["feature"])
        self.assertTrue(row["body"])

    def test_ids_that_would_break_the_pages_anchors_are_refused_or_dropped(self):
        for bad in ("sec-thread", "s01 s02", "S1", "s1\"onclick=x", "../x"):
            body = composed_body()
            body["edition"]["stories"][0]["id"] = bad
            with self.assertRaises(digest.DigestMalformed, msg=bad):
                digest.view(WEEK, body)
        body = composed_body()
        body["edition"]["learning"]["notes"]["bad key"] = ["T", "D"]
        body["edition"]["learning"]["paras"][0] += " [[fn:bad key|x]]"
        self.assertEqual([n["key"] for n in digest.view(WEEK, body)["learning"]["notes"]], ["queue"])         # a key with a space is plain text
        body = composed_body()
        body["items"][0]["id"] = 'item "x" <y>'
        body["edition"]["stories"][0]["itemId"] = 'item "x" <y>'
        with self.assertRaises(digest.DigestMalformed):
            digest.view(WEEK, body)

    def test_an_empty_tie_is_hidden_and_a_second_highlight_is_plain(self):
        body = composed_body()
        body["edition"]["stories"][0]["tie"] = {"to": "s02", "text": "   "}
        self.assertIsNone(digest.view(WEEK, body)["stories"]["rows"][0]["tie"])
        body["edition"]["thread"]["lines"] = ["No highlight here.", "[[hl|One]] and [[hl|two]] highlights."]
        first, second = digest.view(WEEK, body)["thread"]["lines"]
        self.assertEqual([r["kind"] for r in second], ["hl", "plain", "plain", "plain"])
        self.assertEqual("".join(r["text"] for r in second), "One and two highlights.")

    def test_the_archive_entry_and_the_edition_line(self):
        e = digest.archive_entry("2026-W43", 2, ["Four [[hl|releases]] cut cost.", "Three decisions restricted."], "x", 7,
                                 {"from": "2026-10-12T05:00:00+00:00", "to": "2026-10-20T05:00:00+00:00"}, None)
        self.assertEqual((e["label"], e["l1"], e["l2"], e["stories"], e["range"], e["href"]),
                         ("ED 02", "Four releases cut cost.", "Three decisions restricted.", 7, "OCT-12-2026 TO OCT-20-2026", "/weekly-digest?week=2026-W43"))
        old = digest.archive_entry("2026-W40", 1, None, "First sentence here. And a second.", 7, None, datetime(2026, 10, 18, 5, 56, tzinfo=timezone.utc))
        self.assertEqual((old["l1"], old["l2"], old["range"]), ("First sentence here.", "", "OCT-10-2026 TO OCT-18-2026"))
        self.assertEqual(digest.range_line({"from": "junk", "to": "junk"}), "")

    def test_the_edition_is_labelled_by_its_window_never_by_the_run_key(self):
        body = json.loads(json.dumps(GOLDEN))
        body["window"] = {"from": "2026-10-10T05:00:00+00:00", "to": "2026-10-18T05:00:00+00:00", "label": "Oct 10 \u2013 Oct 18, 2026"}
        self.assertEqual(digest.view(WEEK, body)["label"], "Oct 10 \u2013 Oct 18, 2026")
        for bad in ({"label": ""}, {"label": 5}, {"label": "x" * 100}, "text", None):      # a bad stored window falls back
            body["window"] = bad
            self.assertEqual(digest.view(WEEK, body, datetime(2026, 10, 18, 5, 56, tzinfo=timezone.utc))["label"],
                             "Oct 10 \u2013 Oct 18, 2026", msg=repr(bad))
        old = {k: v for k, v in GOLDEN.items() if k != "window"}                              # an older brief carries none: derived from publish date
        self.assertEqual(digest.view("2026-W40", old, datetime(2026, 10, 18, 5, 56, tzinfo=timezone.utc))["label"],
                         "Oct 10 \u2013 Oct 18, 2026")
        self.assertIsNone(digest.view(WEEK, old)["label"])                                     # nothing to derive from

    def test_unsafe_links_are_not_links(self):
        body = json.loads(json.dumps(GOLDEN))
        body["items"][0]["sources"] = [{"label": "x", "url": "javascript:alert(1)", "tier": "primary"},
                                       {"label": "ok", "url": "https://example.com/a", "tier": "primary"}]
        self.assertEqual([s["href"] for s in digest.view(WEEK, body)["stories"]["rows"][0]["sources"]], ["https://example.com/a"])

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
        self.assertEqual(shown["stories"]["rows"][0]["sources"], [])

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

    def test_d2_an_older_brief_renders_in_the_template_with_the_fields_it_has(self):
        self.publish()
        r = self.client.get(f"/weekly-digest?week={WEEK}")
        self.assertEqual(r.status_code, 200)
        for needle in ("THE WEEK IN ONE THREAD", "THIS WEEK&#39;S STORIES", 'class="subnav"', 'href="#sec-thread"', 'href="#sec-stories"',
                       "The frontier stopped", 'aria-label="Useful: ', 'aria-label="Not useful: ', 'name="comment"', "spec moves the core", 'role="tablist"',
                       "EDITION 0"):
            self.assertIn(needle, r.text, needle)
        self.assertNotIn("LEARNING FOR THE WEEK", r.text)                                        # an older brief has no essay, so no empty section either
        self.assertEqual(r.text.count('action="/weekly-digest/vote"'), 2 * len(GOLDEN["items"]))   # an up form and a down form each
        self.assertLess(r.text.index(">METHOD<"), r.text.index(">CAVEAT<"))                       # reading order survives jsonb

    def test_d2a_a_composed_edition_renders_every_slot_and_only_news_and_archive(self):
        self.publish(composed_body())
        r = self.client.get(f"/weekly-digest?week={WEEK}")
        self.assertEqual(r.status_code, 200)
        for needle in ("LEARNING FOR THE WEEK", "WHAT TO DO DIFFERENTLY THIS WEEK", "WHAT IT WAS DRAWN FROM", "HOW THIS EDITION WAS MADE", "THE THREAD THROUGH IT ALL",
                       "TRY THIS WEEK /", "WHY IT MATTERS /", "TRY IT", "FEATURE", "Ties to 02", "Smaller releases", "#Agents", "checked against source",
                       'id="note-queue"', 'role="tooltip"', "Nothing quotable surfaced this week.", "WHAT ARE INDUSTRY LEADERS TALKING?"):
            self.assertIn(needle, r.text, needle)
        self.assertEqual(r.text.count('role="tab"'), 2)                                         # NEWS and ARCHIVE; nothing else is built
        for absent in ("PREDICTION", "HOMEWORK", "RETIRED PREDICTIONS", "React", "__bundler"):
            self.assertNotIn(absent, r.text, absent)
        self.assertEqual(r.text.count("FEATURE</span>"), 2)
        self.assertEqual(r.text.count('aria-expanded="false"'), 4)                              # every section starts collapsed
        self.assertEqual(r.text.count("Primary: "), 5)                                          # exactly one primary per story
        for key in ("thread", "learning", "stories", "leaders"):
            self.assertIn(f'aria-controls="body-{key}"', r.text)
        self.assertIn("/static/digest.js", r.text)
        self.assertNotIn("<script>alert", r.text)

    def test_d2j_a_clean_edition_has_no_held_block(self):
        self.publish(composed_body())
        self.assertNotIn("COULD NOT BE VERIFIED", self.client.get(f"/weekly-digest?week={WEEK}").text)

    def test_d2k_held_stories_are_listed_under_the_stories(self):
        body = composed_body()
        body["edition"]["held"] = [{"id": "s03", "headline": "<b>Parcel</b> 2.0", "why": "could not be verified",
                                    "sources": [{"label": "Parcel", "href": "https://parcel.example/2"}, {"label": "evil", "href": "javascript:alert(1)"}]}]
        self.publish(body)
        text = self.client.get(f"/weekly-digest?week={WEEK}").text
        self.assertIn("HELD &middot; COULD NOT BE VERIFIED", text)
        self.assertIn("1 story was read this week and left out", text)
        self.assertIn("&lt;b&gt;Parcel&lt;/b&gt; 2.0", text)
        self.assertIn('href="https://parcel.example/2"', text)
        self.assertNotIn("javascript:", text)

    def test_d2b_the_page_shows_the_window_not_the_week_key(self):
        self.publish({**GOLDEN, "window": {"from": "2026-10-10T05:00:00+00:00", "to": "2026-10-18T05:00:00+00:00", "label": "Oct 10 \u2013 Oct 18, 2026"}})
        text = self.client.get(f"/weekly-digest?week={WEEK}").text
        line = text[text.index('class="editionline"'):][:200]
        self.assertIn("OCT-10-2026 TO OCT-18-2026", line)
        self.assertNotIn(WEEK, line)
        self.assertIn("<title>Weekly Digest &middot; Oct 10 \u2013 Oct 18, 2026</title>", text)

    def test_d2c_the_archive_lists_every_edition_newest_first_with_the_sample_and_every_link_opens(self):
        self.publish(composed_body(), WEEK)
        self.publish(GOLDEN, "1998-W21")
        text = self.client.get("/weekly-digest?tab=archive").text
        self.assertIn('id="tab-archive" aria-selected="true"', text)
        panel = text[text.index('id="panel-archive"'):]
        self.assertIn("PAST EDITIONS", panel)
        import re
        hrefs = [h.replace("&amp;", "&") for h in re.findall(r'class="rd" href="([^"]+)"', panel)]
        mine = [hrefs.index(f"/weekly-digest?week={w}") for w in ("1998-W21", "1998-W20")]
        self.assertLess(mine[0], mine[1])                                                         # published later, listed first
        self.assertEqual(hrefs[-1], site.SAMPLE_EDITION_URL)                                      # the sample edition is the last row
        numbers = [int(n) for n in re.findall(r'class="el">ED (\d+)<', panel)]
        self.assertEqual(numbers, sorted(numbers, reverse=True))                                  # ED numbers fall down the page
        self.assertEqual(len(numbers), len(hrefs) - 1)                                            # every edition has a number; the sample has SAMPLE
        self.assertIn("SAMPLE", panel)
        for href in re.findall(r'class="rd" href="([^"]+)"', panel):
            r = self.client.get(href.replace("&amp;", "&"))
            self.assertEqual(r.status_code, 200, href)
        self.assertIn(site.SAMPLE_EDITION_URL, panel)

    def test_d2e_keyboard_and_screen_reader_structure(self):
        import re
        self.publish(composed_body())
        text = self.client.get(f"/weekly-digest?week={WEEK}").text
        ids = set(re.findall(r'\bid="([^"]+)"', text))
        for attr in ("aria-controls", "aria-labelledby", "aria-describedby"):
            for ref in re.findall(rf'{attr}="([^"]+)"', text):
                for one in ref.split():
                    if one.startswith(("body-", "h-", "tab-", "panel-", "tip-")):
                        self.assertIn(one, ids, f"{attr} points at a missing id: {one}")
        self.assertEqual(len(re.findall(r'<button type="button" class="sec-toggle" aria-expanded="(?:true|false)"', text)), 4)   # a real button, with its state
        self.assertEqual(re.findall(r'role="tab" id="(tab-[a-z]+)" aria-selected="(true|false)"', text), [("tab-news", "true"), ("tab-archive", "false")])
        self.assertEqual(len(re.findall(r'tabindex="-1"', text)), 1)                                # roving tabindex: only the selected tab is in the tab order
        self.assertEqual(len(re.findall(r'<nav class="subnav" aria-label="In this edition">', text)), 1)
        self.assertIn('<html lang="en">', text)
        self.assertEqual(len(re.findall(r"<h1", text)), 1)
        for link in re.findall(r'<a href="(https?://[^"]+)" target="_blank" rel="([^"]+)"', text):
            self.assertEqual(link[1], "noopener noreferrer")
        for svg in re.findall(r"<svg[^>]*>", text):
            self.assertIn('aria-hidden="true"', svg)
        script = (REPO / "static" / "digest.js").read_text()
        for key in ("ArrowRight", "ArrowLeft", "Home", "End", "aria-expanded", "aria-selected", "prefers-reduced-motion"):
            self.assertIn(key, script)

    def test_d2d_the_sample_edition_is_the_old_snapshot_served_as_it_is_and_the_old_url_still_redirects(self):
        r = self.client.get(site.SAMPLE_EDITION_URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, (REPO / "application" / "Weekly-Digest.html").read_text(encoding="utf-8"))
        old = self.client.get("/application/Weekly-Digest.html", follow_redirects=False)
        self.assertEqual((old.status_code, old.headers["location"]), (301, "/weekly-digest"))

    def test_d2f_hostile_text_in_every_field_of_a_composed_edition_is_inert(self):
        evil = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'"
        body = composed_body()
        ed = body["edition"]
        ed["thread"]["lines"] = [evil, evil]
        ed["thread"]["body"] = f"{evil} [[#s02|{evil}]]"
        ed["learning"]["headline"] = evil
        ed["learning"]["paras"][0] = f"{evil} [[fn:queue|{evil}]]"
        ed["learning"]["notes"]["queue"] = [evil, evil]
        ed["learning"]["howMade"] = ed["learning"]["doDifferently"] = evil
        ed["storiesTitle"] = evil
        for story in ed["stories"]:
            story.update(headline=evil, body=evil, why=evil, tryIt=evil, hashtags=["#ok"], featureParas=[evil] if story.get("feature") else story.get("featureParas"))
            story["sources"] = [{"label": evil, "href": "javascript:alert(3)", "primary": True}, {"label": evil, "href": "https://example.com/a?x=\"onmouseover=alert(4)", "primary": False}]
            story["tie"] = {"to": "s02", "text": evil}
        ed["dividers"] = [{"before": "s03", "label": evil}]
        ed["closing"] = {"lines": [evil, evil], "summary": evil, "oneThing": evil}
        for item in body["items"]:
            item["headline"] = evil
        self.publish(body)
        text = self.client.get(f"/weekly-digest?week={WEEK}").text
        self.assertNotIn("<script>alert", text)
        self.assertNotIn("<img src=x", text)
        self.assertNotIn("javascript:", text)                                                  # an unsafe link is not rendered as a link
        from html.parser import HTMLParser

        class Seen(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags, self.attrs = [], []

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                self.attrs += [(tag, name, value) for name, value in attrs]
        parsed = Seen()
        parsed.feed(text)
        self.assertEqual([t for t in parsed.tags if t == "script"], ["script", "script"])           # only the two files the template loads
        self.assertNotIn("img", parsed.tags)
        self.assertEqual([a for a in parsed.attrs if a[1].startswith("on")], [])                    # no event handler attribute anywhere
        for tag, name, value in parsed.attrs:
            if name == "href":
                self.assertTrue(value.startswith(("#", "/", "https://", "http://")), value)         # no javascript: link
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)

    def test_d2g_a_notice_about_a_story_the_page_does_not_show_is_not_lost(self):
        self.publish()
        text = self.client.get(f"/weekly-digest?week={WEEK}&notice=voted-up&item=not-a-story").text
        self.assertEqual(text.count("Thanks. Marked useful."), 1)

    def test_d2h_a_malformed_latest_edition_still_offers_the_archive(self):
        self.publish({"items": [{"id": "a"}]}, "1999-W22")
        text = self.client.get("/weekly-digest?week=1999-W22").text
        self.assertIn("cannot be shown", text)
        self.assertIn("OTHER EDITIONS", text)
        self.assertIn(site.SAMPLE_EDITION_URL, text)

    def test_d2i_with_no_edition_the_sample_is_still_reachable(self):
        with unittest.mock.patch.object(briefs, "latest", lambda conn: None):
            text = self.client.get("/weekly-digest").text
        self.assertIn("first edition has not been published", text)
        self.assertIn(site.SAMPLE_EDITION_URL, text)

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
        self.assertEqual(self.client.get("/weekly-digest?week=1998-W20%0A").status_code, 404)

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
