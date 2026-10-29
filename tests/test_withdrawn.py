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


class _Borrow:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


@needs_db
class FixtureIsGone(unittest.TestCase):
    def test_the_leaked_fixture_week_was_removed_and_nothing_is_withdrawn_now(self):
        self.assertEqual(briefs.WITHDRAWN, {})
        with budget.connect() as conn:
            self.assertEqual(conn.execute("SELECT (SELECT count(*) FROM briefs WHERE id = '1999-W20'), (SELECT count(*) FROM runs WHERE id = '1999-W20')").fetchone(), (0, 0))
            self.assertEqual(conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = 'briefs_append_only'").fetchone()[0], "O")      # the protection is on

    def test_the_mechanism_still_works_for_a_week_that_is_listed(self):
        with budget.connect(autocommit=False) as conn, unittest.mock.patch.object(briefs, "WITHDRAWN", {"1998-W32": "test"}):
            conn.execute("SELECT 1")
            runs.open_run(conn, "1998-W32", NOW)
            briefs.publish(conn, "1998-W32", "1998-W32", composed_body(), NOW)
            self.assertIsNone(briefs.get(conn, "1998-W32"))
            conn.rollback()
