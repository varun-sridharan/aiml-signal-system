"""Weeks that are not editions stay out of every public view (Run 9: a fixture brief was committed to the live database by mistake).

A brief is append-only, so it cannot be removed without disabling the database's own protection; the views leave it out instead.
The tests run inside one rolled-back transaction with a patched list, so no row of theirs survives.
"""

from __future__ import annotations

import unittest
import unittest.mock
from datetime import datetime, timezone

import app as site
from agents import briefs, budget, review, runs
from tests._db import needs_db
from tests.test_digest import composed_body

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
GHOST = "1998-W30"
REAL = "1998-W31"


@needs_db
class Withdrawn(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")                       # hold a transaction open: the helpers' own transaction blocks become savepoints
        self.before = self.conn.execute("SELECT (SELECT count(*) FROM briefs), (SELECT count(*) FROM runs)").fetchone()
        for week in (GHOST, REAL):
            runs.open_run(self.conn, week, NOW)
            briefs.publish(self.conn, week, week, composed_body(), NOW)
        self.patch = unittest.mock.patch.object(briefs, "WITHDRAWN", {GHOST: "test"})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT (SELECT count(*) FROM briefs), (SELECT count(*) FROM runs)").fetchone(), self.before,
                             "a test leaked into the live tables")

    def test_get_and_latest_leave_a_withdrawn_week_out(self):
        self.assertIsNone(briefs.get(self.conn, GHOST))
        self.assertIsNotNone(briefs.get(self.conn, REAL))
        self.assertNotEqual(briefs.latest(self.conn)[0], GHOST)

    def test_the_archive_and_its_numbering_leave_it_out(self):
        entries, numbers = site._archive(self.conn)
        self.assertNotIn(GHOST, numbers)
        self.assertIn(REAL, numbers)
        self.assertEqual(sorted(numbers.values()), list(range(1, len(numbers) + 1)))
        self.assertFalse([e for e in entries if GHOST in str(e.get("href", ""))])

    def test_the_review_note_leaves_it_out(self):
        with unittest.mock.patch.object(review, "REVIEW_EDITIONS", 50):
            weeks = [n["week"] for n in review.entries(self.conn)]
        self.assertNotIn(GHOST, weeks)

    def test_the_last_edition_in_health_leaves_it_out(self):
        site._edition_cache.update(at=None, value=None)
        with unittest.mock.patch.object(site, "_connect", lambda: _Borrow(self.conn)):
            value = site._last_edition()
        self.assertNotEqual(value and value["week"], GHOST)
        site._edition_cache.update(at=None, value=None)

    def test_the_mistaken_fixture_week_is_on_the_list_and_not_visible(self):
        with unittest.mock.patch.object(briefs, "WITHDRAWN", {"1999-W20": "x"}):
            self.assertIsNone(briefs.get(self.conn, "1999-W20"))


class _Borrow:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


class ListIsKept(unittest.TestCase):
    def test_the_real_list_names_the_leaked_week_and_says_why(self):
        self.assertIn("1999-W20", briefs.WITHDRAWN)
        self.assertIn("by mistake", briefs.WITHDRAWN["1999-W20"])
