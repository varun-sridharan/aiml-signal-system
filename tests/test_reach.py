"""The Reader's measured count for the latest edition: the Product page's stats card (Run 9 put it in the hero; 2026-10-28 moved it), never an invented figure."""

from __future__ import annotations

import json
import unittest
import unittest.mock
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app as site
from agents import briefs, budget, reach, reader
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
        body = {"window": WINDOW, "items": [{"id": f"item-{n}"} for n in range(7)]}
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
        listed = self.conn.execute("SELECT count(*) FROM sources").fetchone()[0]             # every source it lists, the three seeded here included
        self.assertEqual(got, {"count": 60, "sources": 3, "listed": listed, "published": 7, "feeds": len(reader.FEEDS), "dates": "Mar 2 \u2013 Mar 10, 1997"})

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
    """The hero has no number and no date; the measured count is the fourth card of the stats band, with a fixed card as the fallback."""
    RESULT = {"count": 1234, "sources": 7, "listed": 99, "published": 6, "feeds": 17, "dates": "Oct 1 \u2013 Oct 8, 2026"}

    def get(self, hero):
        with unittest.mock.patch.object(reach, "hero", lambda connect: hero):
            return TestClient(site.app).get("/application/Product.html").text

    def hero_of(self, html):
        return html.split('<section class="band hero">', 1)[1].split("</section>", 1)[0]

    def test_the_hero_has_the_question_for_a_title_and_no_number_date_or_note(self):
        for hero in (self.RESULT, None):
            hero_html = self.hero_of(self.get(hero))
            self.assertIn('<h1 class="hero-title">Which AI news matters to your business?</h1>', hero_html)
            self.assertNotIn("hero-note", hero_html)
            title = hero_html.split('<h1 class="hero-title">', 1)[1].split("</h1>", 1)[0]
            self.assertNotRegex(title, r"\d|Oct|<span")
            self.assertIn("From AI news to a change your team can ship", hero_html)
            self.assertIn("Today it is one weekly brief of up to seven items", hero_html)

    def test_with_a_count_the_fourth_card_reads_read_to_published_with_its_label_and_note(self):
        html = self.get(self.RESULT)
        self.assertIn('<div class="value">1,234 &rarr; 6</div>', html)
        self.assertIn('<div class="label">read by the Reader last week \u2192 published</div>', html)
        self.assertIn('<div class="source">Latest edition \u00b7 updates every Friday</div>', html)
        self.assertIn("the Reader's own count for the latest edition: it read 1,234 items from 7 of the 99 sources it lists, and 6 were published.", html)
        self.assertNotIn('<div class="value">7</div>', html)

    def test_without_one_the_card_shows_the_fixed_7_as_before_and_the_note_does_not_name_a_count(self):
        html = self.get(None)
        self.assertIn('<div class="value">7</div>', html)
        self.assertIn("items in the latest edition (an edition carries at most seven)", html)
        self.assertNotIn("read by the Reader last week", html)
        self.assertIn("The fourth is the size of the latest edition, which carries at most seven items.", html)
        self.assertNotRegex(html.split('class="stats-note"', 1)[1].split("</p>", 1)[0], r"\{read\}|\{sources\}|\{published\}")

    def test_the_content_file_holds_no_number_of_its_own_in_the_hero(self):
        text = (REPO / "application" / "Product.json").read_text(encoding="utf-8")
        self.assertNotIn("Four hundred", text)
        h = json.loads(text)["hero"]
        self.assertEqual(h["title"], "Which AI news matters to your business?")
        self.assertFalse({"titleAccent", "titleFallback", "countNote"} & set(h))

    def test_the_stats_note_says_the_only_illustrative_numbers_are_in_the_video_in_both_cases(self):
        for hero in (self.RESULT, None):
            html = self.get(hero)
            self.assertIn("Every figure in this section is measured. The only illustrative numbers on the page are in the video, and they are labelled.", html)

    def system_design(self, hero):
        with unittest.mock.patch.object(reach, "hero", lambda connect: hero):
            return TestClient(site.app).get("/design/System-Design.html").text

    def test_the_source_counts_are_labelled_and_the_two_pages_use_the_same_numbers(self):
        note = self.get(self.RESULT).split('class="stats-note"', 1)[1].split("</p>", 1)[0]
        self.assertIn("from 7 of the 99 sources it lists", note)                  # 7 produced items in the window; 99 are listed in the sources table
        sd = self.system_design(self.RESULT)
        self.assertIn("Reads 17 feeds and index pages; of the 99 sources it lists, 7 produced items in the latest edition's window", sd)
        from agents import reader
        self.assertEqual(len(reader.FEEDS), 17)                                    # the constant behind "17 feeds and index pages"

    def test_system_design_without_a_count_drops_the_clause_and_leaves_no_placeholder(self):
        sd = self.system_design(None)
        self.assertIn("Reads 17 feeds and index pages, seeded from", sd)
        self.assertNotIn("{window_sources}", sd)
        self.assertNotIn("sources it lists", sd)

    def test_the_window_clause_is_empty_without_a_result(self):
        self.assertEqual(reach.window_clause(None), "")
        self.assertEqual(reach.window_clause(self.RESULT), "; of the 99 sources it lists, 7 produced items in the latest edition's window")


if __name__ == "__main__":
    unittest.main()
