"""The Friday job: `python -m agents.weekly --scheduled` (Run 6, Step B). No paid call anywhere in this file."""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import briefs, budget, runs, weekly  # noqa: E402
from tests._db import needs_db  # noqa: E402
from tests.test_weekly import NOW, Pipeline, WEEK  # noqa: E402


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Window(unittest.TestCase):
    def test_summer_friday_19_pacific_is_saturday_02_utc_and_winter_is_03_utc(self):
        self.assertTrue(weekly.in_friday_window(utc(2026, 7, 11, 2, 0)))        # Fri 19:00 PDT
        self.assertFalse(weekly.in_friday_window(utc(2026, 7, 11, 3, 0)))       # Fri 20:00 PDT
        self.assertTrue(weekly.in_friday_window(utc(2026, 12, 12, 3, 0)))       # Fri 19:00 PST
        self.assertFalse(weekly.in_friday_window(utc(2026, 12, 12, 2, 0)))      # Fri 18:00 PST

    def test_the_window_is_the_one_hour(self):
        self.assertTrue(weekly.in_friday_window(utc(2026, 7, 11, 2, 59, 59)))
        self.assertFalse(weekly.in_friday_window(utc(2026, 7, 11, 1, 59, 59)))
        self.assertFalse(weekly.in_friday_window(utc(2026, 7, 11, 3, 0, 0)))

    def test_exactly_one_of_the_two_cron_entries_is_in_the_window_every_friday_of_two_years(self):
        friday = datetime(2026, 1, 2, 12, 0, tzinfo=weekly.PACIFIC)             # a Friday
        self.assertEqual(friday.weekday(), 4)
        for n in range(104):
            day = (friday + timedelta(days=7 * n)).date()
            saturday = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
            hits = [h for h in (2, 3) if weekly.in_friday_window(saturday + timedelta(hours=h))]
            self.assertEqual(len(hits), 1, f"Friday {day}")

    def test_the_days_around_a_clock_change_are_still_right(self):
        # Spring forward Sunday 2026-03-08, fall back Sunday 2026-11-01: the Fridays either side
        for sat in ((2026, 3, 7, 3), (2026, 3, 14, 2), (2026, 10, 31, 2), (2026, 11, 7, 3)):
            self.assertTrue(weekly.in_friday_window(utc(*sat)), sat)

    def test_no_other_day_or_hour_is_in_the_window(self):
        start = utc(2026, 10, 25, 0, 0)
        hits = [start + timedelta(hours=h) for h in range(24 * 14) if weekly.in_friday_window(start + timedelta(hours=h))]
        self.assertEqual(len(hits), 2)                                          # one Friday hour per week
        self.assertTrue(all(weekly.pacific(h).weekday() == 4 and weekly.pacific(h).hour == 19 for h in hits))

    def test_the_week_is_the_iso_week_of_the_pacific_friday(self):
        self.assertEqual(weekly.scheduled_week(utc(2026, 10, 24, 2, 0)), "2026-W43")      # Fri Oct 23 19:00 PDT
        self.assertEqual(weekly.scheduled_week(utc(2027, 1, 2, 3, 0)), "2026-W53")        # Fri Jan 1 2027 19:00 PST: still last year's week 53
        self.assertEqual(weekly.scheduled_week(utc(1999, 3, 13, 3, 0)), WEEK)


class Quiet(unittest.TestCase):
    """Outside the window, and for a week already published, the job touches nothing: no connection, no client, no run."""

    def setUp(self):
        self.lines = []
        self.connect = unittest.mock.Mock(side_effect=AssertionError("connected"))
        self.client = unittest.mock.Mock(side_effect=AssertionError("built a client"))
        self.run = unittest.mock.Mock(side_effect=AssertionError("ran"))

    def go(self, now):
        return weekly.scheduled(now, connect=self.connect, build_client=self.client, run=self.run, out=self.lines.append)

    def test_outside_the_window_it_does_nothing_and_says_so(self):
        for now in (utc(2026, 10, 24, 3, 0), utc(2026, 10, 23, 2, 0), utc(2026, 10, 25, 2, 0), utc(2026, 10, 21, 3, 0)):
            self.lines.clear()
            self.assertEqual(self.go(now), 0)
            self.assertEqual(len(self.lines), 1)
            self.assertIn("outside Friday 19:00 to 19:59 Pacific; nothing done", self.lines[0])
        self.connect.assert_not_called()

    def test_a_week_already_published_exits_cleanly_without_a_client_or_a_run(self):
        @contextmanager
        def connect():
            yield object()
        with unittest.mock.patch.object(briefs, "get", return_value=("2026-W43", {}, utc(2026, 10, 24, 2, 5))):
            code = weekly.scheduled(utc(2026, 10, 24, 2, 0), connect=connect, build_client=self.client, run=self.run, out=self.lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(self.lines, ["scheduled: 2026-W43 is already published (2026-10-24 02:05 UTC); nothing done"])

    def test_a_stopped_run_writes_one_line_and_exits_nonzero(self):
        @contextmanager
        def connect():
            yield unittest.mock.Mock()
        client = lambda: object()          # noqa: E731
        with unittest.mock.patch.object(briefs, "get", return_value=None), unittest.mock.patch.object(weekly, "resolve_run", return_value="2026-W43"), \
                unittest.mock.patch.object(runs, "next_stage", return_value="reader"):
            for boom in (budget.BudgetExceeded("month ceiling"), weekly.GateFailed("2 hard failures"), RuntimeError("model returned no text")):
                self.lines.clear()
                code = weekly.scheduled(utc(2026, 10, 24, 2, 0), connect=connect, build_client=client, run=unittest.mock.Mock(side_effect=boom),
                                        out=self.lines.append)
                self.assertEqual(code, 1)
                self.assertEqual(len(self.lines), 1)
                self.assertIn("scheduled: 2026-W43 stopped:", self.lines[0])

    def test_scheduled_takes_no_other_option(self):
        with unittest.mock.patch.object(sys, "argv", ["weekly", "--scheduled", "--week", "2026-W43"]), self.assertRaises(SystemExit) as e:
            weekly.main()
        self.assertEqual(e.exception.code, 2)


class Health(unittest.TestCase):
    """/health says which edition was published last and when."""

    def get(self, connect):
        import app as site
        from fastapi.testclient import TestClient
        site._edition_cache.update(at=None, value=None)
        with unittest.mock.patch.object(site, "_connect", connect), unittest.mock.patch.object(site, "_ledger_ok", return_value=True):
            return TestClient(site.app).get("/health")

    def test_last_edition_is_the_newest_week_and_the_time(self):
        @contextmanager
        def connect():
            yield unittest.mock.Mock(execute=lambda *a: unittest.mock.Mock(fetchone=lambda: ("2026-W44", utc(2026, 10, 24, 2, 41, 7))))
        r = self.get(connect)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["last_edition"], {"week": "2026-W44", "published_at": "2026-10-24T02:41:07Z"})
        self.assertEqual(r.json()["status"], "ok")

    def test_no_edition_or_no_database_is_null_and_still_a_200(self):
        @contextmanager
        def empty():
            yield unittest.mock.Mock(execute=lambda *a: unittest.mock.Mock(fetchone=lambda: None))
        self.assertIsNone(self.get(empty).json()["last_edition"])
        r = self.get(unittest.mock.Mock(side_effect=RuntimeError("down")))
        self.assertEqual((r.status_code, r.json()["last_edition"]), (200, None))


FRIDAY = utc(1999, 3, 13, 3, 0)            # Friday 1999-03-12 19:00 PST, which is 1999-W10 = the fixture week


@needs_db
class ResumeAndIdempotence(Pipeline, unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.conn.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items)").fetchone()
        self.start()
        self.lines = []

    def tearDown(self):
        self.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items)").fetchone(),
                             self.live)

    def go(self):
        @contextmanager
        def connect():
            yield self.conn

        def run(conn, client, now, run_id, **kw):          # the fixture feeds are dated NOW, so the Reader's window is NOW's
            return weekly.run_week(conn, client, NOW, run_id, **kw)
        return weekly.scheduled(FRIDAY, connect=connect, build_client=lambda: self.client, run=run, out=self.lines.append)

    def test_a_run_that_dies_resumes_at_its_checkpoint_then_publishes_then_is_a_no_op(self):
        self.fail_framer = 1
        self.assertEqual(self.go(), 1)
        self.assertIn(f"scheduled: {WEEK} stopped: RuntimeError", self.lines[-1])
        fetches = len(self.client.requests)
        self.assertGreater(fetches, 0)
        self.assertEqual(runs.next_stage(self.conn, WEEK), "framer")

        self.assertEqual(self.go(), 0)                                           # the next run in the window resumes at the framer
        self.assertRegex(self.lines[-1], rf"scheduled: {WEEK} published \({WEEK}, from framer\)")
        self.assertEqual(len(self.client.requests), fetches)                     # the Reader was not paid for again
        self.assertIsNotNone(briefs.get(self.conn, WEEK))

        calls = (len(self.framer_calls), len(self.client.requests))
        self.assertEqual(self.go(), 0)                                           # a third run: the week is done
        self.assertIn("already published", self.lines[-1])
        self.assertEqual((len(self.framer_calls), len(self.client.requests)), calls)

    def test_a_first_run_starts_at_the_reader(self):
        self.assertEqual(self.go(), 0)
        self.assertRegex(self.lines[-1], rf"scheduled: {WEEK} published \({WEEK}, from reader\)")


if __name__ == "__main__":
    unittest.main()
