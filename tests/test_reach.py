"""The measured number in the Product hero (Run 9, Step C): the Reader's count for the latest edition's window, never an invented figure."""

from __future__ import annotations

import json
import unittest
import unittest.mock
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app as site
from agents import briefs, budget, reach
from tests import _page
from tests._db import needs_db

REPO = Path(__file__).resolve().parent.parent
START = datetime(1997, 3, 2, 5, 0, tzinfo=timezone.utc)
END = datetime(1997, 3, 10, 5, 0, tzinfo=timezone.utc)
WINDOW = {"from": START.isoformat(), "to": END.isoformat(), "label": "Mar 2 – Mar 10, 1997"}


def seed(conn, n: int, sources: int = 3, inside: bool = True):
    """n items spread over `sources` sources, discovered inside (or just outside) the 1997 window, in the caller's transaction."""
    for s in range(sources):
        conn.execute("INSERT INTO sources (id, name, domain, tier, reputation_prior) VALUES (%s, %s, %s, 'unrated', 'unrated')",
                     (f"reach-test-{s}", f"Reach test {s}", f"reach-test-{s}.example"))
    for i in range(n):
        when = datetime(1997, 3, 4 + (i % 5), 12, 0, tzinfo=timezone.utc) if inside else datetime(1997, 3, 10, 5, 0, tzinfo=timezone.utc)
        conn.execute("INSERT INTO items (id, canonical_url, domain, title, source_id, discovered_at, status) VALUES (%s, %s, %s, %s, %s, %s, 'discovered')",
                     (uuid.uuid4(), f"https://reach-test-{i % sources}.example/{i}", f"reach-test-{i % sources}.example", f"Item {i}", f"reach-test-{i % sources}", when))


@needs_db
class Count(unittest.TestCase):
    def setUp(self):
        reach.reset()
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.items = self.conn.execute("SELECT count(*) FROM items").fetchone()[0]

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        reach.reset()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT count(*) FROM items").fetchone()[0], self.items, "a test leaked into the live tables")

    def latest(self, brief_id="1997-W10"):
        body = {"window": WINDOW}
        return unittest.mock.patch.object(briefs, "latest", lambda conn: (brief_id, body, END))

    def test_the_count_is_the_items_discovered_inside_the_window_and_their_distinct_sources(self):
        seed(self.conn, 60, sources=3)
        self.assertEqual(reach.count_window(self.conn, START, END), (60, 3))

    def test_an_item_found_at_the_window_end_or_after_it_is_not_counted(self):
        seed(self.conn, 60, sources=3, inside=False)             # discovered exactly at the end of the window: the window is half open
        self.assertEqual(reach.count_window(self.conn, START, END), (0, 0))

    def test_for_latest_gives_the_count_the_sources_and_the_editions_own_dates(self):
        seed(self.conn, 60, sources=3)
        with self.latest():
            got = reach.for_latest(self.conn)
        self.assertEqual(got, {"count": 60, "sources": 3, "dates": "Mar 2 – Mar 10, 1997"})

    def test_under_the_minimum_there_is_no_number(self):
        seed(self.conn, reach.MIN_COUNT - 1)
        with self.latest():
            self.assertIsNone(reach.for_latest(self.conn))

    def test_the_minimum_itself_counts(self):
        seed(self.conn, reach.MIN_COUNT)
        with self.latest():
            self.assertEqual(reach.for_latest(self.conn)["count"], reach.MIN_COUNT)

    def test_it_is_cached_for_the_editions_lifetime_and_a_new_edition_recounts(self):
        seed(self.conn, 60)
        with self.latest("1997-W10"):
            first = reach.for_latest(self.conn)
            with unittest.mock.patch.object(reach, "count_window", side_effect=AssertionError("asked the database again")):
                self.assertEqual(reach.for_latest(self.conn), first)          # the same edition: no second query
        with self.latest("1997-W11"), unittest.mock.patch.object(reach, "count_window", return_value=(75, 4)) as q:
            self.assertEqual(reach.for_latest(self.conn)["count"], 75)
            q.assert_called_once()

    def test_the_real_count_matches_a_direct_query_for_the_latest_editions_window(self):
        row = briefs.latest(self.conn)
        body = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        start, end = body["window"]["from"], body["window"]["to"]
        direct = self.conn.execute("SELECT count(*) FROM items WHERE discovered_at >= %s AND discovered_at < %s", (start, end)).fetchone()[0]
        got = reach.for_latest(self.conn)
        self.assertEqual(got["count"] if got else None, direct if direct >= reach.MIN_COUNT else None)


class Fallback(unittest.TestCase):
    def setUp(self):
        reach.reset()

    tearDown = setUp

    def test_a_database_that_does_not_answer_gives_no_number_and_is_not_asked_again_at_once(self):
        calls = []

        def connect():
            calls.append(1)
            raise RuntimeError("down")
        self.assertIsNone(reach.hero(connect))
        self.assertIsNone(reach.hero(connect))
        self.assertEqual(len(calls), 1)

    def test_the_window_falls_back_to_the_publish_date_and_the_readers_look_back(self):
        end = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)
        start, stop = reach.window_of({}, end)
        self.assertEqual(stop, end)
        self.assertLess(start, end)


class Page(unittest.TestCase):
    def get(self, hero):
        with unittest.mock.patch.object(reach, "hero", lambda connect: hero):
            return TestClient(site.app).get("/application/Product.html").text

    def test_with_a_count_the_title_has_the_number_with_a_separator_and_the_note_names_sources_and_dates(self):
        html = self.get({"count": 1234, "sources": 7, "dates": "Oct 1 – Oct 8, 2026"})
        self.assertIn("1,234 things happened in AI last week.", html)
        self.assertIn('<p class="hero-note">Counted by the Reader across 7 sources, Oct 1 – Oct 8, 2026.</p>', html)
        self.assertNotIn("Hundreds of things", html)

    def test_without_one_the_title_says_hundreds_and_there_is_no_note(self):
        html = self.get(None)
        self.assertIn("Hundreds of things happened in AI last week.", html)
        self.assertNotIn("hero-note", html)

    def test_the_content_file_holds_no_number_of_its_own(self):
        text = (REPO / "application" / "Product.json").read_text(encoding="utf-8")
        self.assertNotIn("Four hundred", text)
        self.assertNotIn("four hundred", text.lower())
        h = json.loads(text)["hero"]
        self.assertEqual(h["title"], "{count} things happened in AI last week.")
        self.assertEqual(h["countNote"], "Counted by the Reader across {sources} sources, {dates}.")

    def test_no_sentence_calls_the_headline_illustrative(self):
        html = self.get({"count": 1234, "sources": 7, "dates": "x"})
        self.assertIn("The only illustrative numbers on the page are in the video, and they are labelled.", html)
        self.assertNotIn("the headline above it", html)


if __name__ == "__main__":
    unittest.main()
