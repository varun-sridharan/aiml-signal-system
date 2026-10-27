"""The review note: what the claim gate changed in the first editions, shown on an unlinked noindex page (Run 6, Step A3)."""

import re
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from agents import article_text as A, review
from tests._db import needs_db

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import app as site  # noqa: E402

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo "
         "sierra tango uniform victor whiskey xray yankee zulu").split()
ARTICLE = " ".join(WORDS[(i * 7) % len(WORDS)] + str(i) for i in range(80))

REPAIRS = {
    "first_pass": {"flags": 2, "dismissed": [{"unit": "item-1", "claim": "It says the model is 3x faster.", "reader": "found the claim in the excerpt"}],
                   "rewritten": [], "removed": [], "held": {}, "unlocatable": []},
    "composed": {"flags": 3, "dismissed": [],
                 "rewritten": [{"path": "story.s02.body", "unit": "s02", "claim": "The release came in March.", "reader": "did not find the claim in the excerpt",
                                "before": "The lab shipped it in March.", "after": "The lab shipped it."}],
                 "removed": [{"path": "story.s03.why", "claim": "It cut cost by 40%.", "why": "still unsupported after one rewrite",
                              "sentence": "It cut cost by 40% [[hl|overnight]]."}],
                 "held": {"s04": ["story.s04.headline: still unsupported after one rewrite: it won"]}, "unlocatable": [],
                 "excerpt": ARTICLE, "second_why": ARTICLE},          # fields the note must never pick up
}


class Entries(unittest.TestCase):
    def test_every_kind_of_change_becomes_an_entry_with_its_text(self):
        got = {e["kind"]: e for e in review.entries_from_repairs(REPAIRS)}
        self.assertEqual(set(got), {"dismissed", "rewritten", "removed", "held"})
        self.assertEqual((got["rewritten"]["before"], got["rewritten"]["after"]), ("The lab shipped it in March.", "The lab shipped it."))
        self.assertEqual(got["removed"]["before"], "It cut cost by 40% overnight.")        # the [[hl|..]] marker is made plain
        self.assertIn("still unsupported", got["removed"]["reader"])
        self.assertEqual(got["held"]["where"], "s04")
        self.assertIn("did not find", got["rewritten"]["reader"])

    def test_nothing_but_our_own_fields_is_picked_up_and_every_field_is_cut(self):
        text = " ".join(str(v) for e in review.entries_from_repairs(REPAIRS) for v in e.values())
        self.assertNotIn(ARTICLE[:60], text)
        long_claim = {"composed": {"dismissed": [{"unit": "s01", "claim": "x" * 5000}]}}
        self.assertLessEqual(len(review.entries_from_repairs(long_claim)[0]["claim"]), review.MAX_FIELD)

    def test_an_older_log_without_sentence_text_says_so(self):
        old = {"composed": {"flags": 1, "dismissed": [], "rewritten": [{"path": "p", "unit": "s01", "claim": "c"}], "removed": [], "held": {}}}
        note = review.edition_note("2026-W43", 1, datetime(2026, 10, 24, tzinfo=timezone.utc), {"repairs": old})
        self.assertFalse(note["detailed"])
        self.assertTrue(review.edition_note("2026-W44", 2, None, {"repairs": REPAIRS})["detailed"])

    def test_a_missing_log_is_an_empty_note_not_an_error(self):
        note = review.edition_note("2026-W42", 1, None, {})
        self.assertEqual((note["entries"], note["flags"]), ([], 0))


class FakeConn:
    def __init__(self, rows):
        self.rows, self.sql, self.args = rows, None, None

    def execute(self, sql, args=()):
        self.sql, self.args = sql, args
        return mock.Mock(fetchall=lambda: self.rows[: args[-1]])


class Selection(unittest.TestCase):
    ROWS = [(f"2026-W4{n}", f"2026-W4{n}", datetime(2026, 10, 20 + n, tzinfo=timezone.utc)) for n in range(6)]

    def test_the_note_covers_only_the_first_editions_and_shows_the_newest_first(self):
        conn = FakeConn(self.ROWS)
        with mock.patch("agents.review.runs.load_artifact", return_value={"repairs": REPAIRS}):
            notes = review.entries(conn)
        self.assertEqual(review.REVIEW_EDITIONS, 4)
        self.assertEqual([n["number"] for n in notes], [4, 3, 2, 1])
        self.assertEqual(conn.args[-1], 4)
        self.assertEqual(conn.args[0], ["1999-W20"])
        self.assertIn("ORDER BY published_at ASC", conn.sql)       # the first editions published, not the latest

    def test_the_setting_controls_how_many(self):
        with mock.patch.object(review, "REVIEW_EDITIONS", 2), mock.patch("agents.review.runs.load_artifact", side_effect=KeyError("x")):
            notes = review.entries(FakeConn(self.ROWS))
        self.assertEqual([n["number"] for n in notes], [2, 1])


@contextmanager
def fake_connect():
    yield object()


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(site.app)

    def get(self):
        note = review.edition_note("2026-W44", 2, datetime(2026, 10, 24, tzinfo=timezone.utc), {"repairs": REPAIRS, "warnings": ["a", "b"]})
        with mock.patch.object(site, "_connect", fake_connect), mock.patch.object(review, "entries", return_value=[note]):
            return self.client.get("/weekly-digest/review")

    def test_it_is_noindex_in_the_header_and_the_page_and_out_of_the_nav(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        self.assertIn("noindex", r.headers["x-robots-tag"])
        self.assertRegex(r.text, r'<meta name="robots" content="noindex')
        nav = re.search(r"<nav.*?</nav>", r.text, re.S).group(0)
        self.assertNotIn("/weekly-digest/review", nav)
        self.assertNotIn("/weekly-digest/review", self.client.get("/weekly-digest").text)
        self.assertNotIn("/weekly-digest/review", self.client.get("/roadmap").text)

    def test_it_shows_each_change_with_its_text_and_no_article_text(self):
        html = self.get().text
        for text in ("The lab shipped it in March.", "The lab shipped it.", "It cut cost by 40% overnight.", "Edition 02 · 2026-W44",
                     "1 dismissed · 1 rewritten · 1 removed · 1 story holds · 2 warnings"):
            self.assertIn(text.replace("&", "&amp;"), html.replace("&amp;", "&") if "&" not in text else html, text)
        self.assertIsNone(re.search(re.escape(ARTICLE[:100]), html))
        self.assertEqual(A.scan_text(A.Index([("excerpt:test", ARTICLE)]), Path("review.html"), html), "")

    def test_a_database_that_does_not_answer_is_a_page_not_a_500(self):
        with mock.patch.object(site, "_connect", side_effect=RuntimeError("down")):
            r = self.client.get("/weekly-digest/review")
        self.assertEqual(r.status_code, 503)
        self.assertIn("noindex", r.headers["x-robots-tag"])
        self.assertIn("did not answer", r.text)

    def test_it_loads_the_one_header_and_the_two_stylesheets(self):
        html = self.get().text
        self.assertEqual(html.count('<header class="site-header">'), 1)
        for sheet in ("/static/tokens.css", "/static/site.css"):
            self.assertIn(f'href="{sheet}"', html)


@needs_db
class AgainstTheRealEditions(unittest.TestCase):
    def test_no_stored_excerpt_appears_in_the_rendered_note(self):
        from agents import budget
        with budget.connect() as conn:
            notes = review.entries(conn)
            index = A.Index(A.db_corpus(conn))
        with mock.patch.object(site, "_connect", fake_connect), mock.patch.object(review, "entries", return_value=notes):
            html = TestClient(site.app).get("/weekly-digest/review").text
        self.assertEqual(A.scan_text(index, Path("review.html"), html), "")
        self.assertLessEqual(len(notes), review.REVIEW_EDITIONS)


if __name__ == "__main__":
    unittest.main()
