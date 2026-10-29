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


FRIDAY = utc(1999, 3, 6, 3, 0)             # Friday 1999-03-05 19:00 PST: the Monday after is 03-08, 1999-W10 = the fixture week


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

    def test_the_week_is_the_iso_week_of_the_monday_after_the_pacific_friday(self):
        self.assertEqual(weekly.scheduled_week(utc(2026, 10, 24, 2, 0)), "2026-W44")      # Fri Oct 23 19:00 PDT: the Monday after is Oct 26
        self.assertEqual(weekly.scheduled_week(utc(2026, 10, 31, 2, 30)), "2026-W45")     # Fri Oct 30 19:30 PDT, the first scheduled edition
        self.assertEqual(weekly.scheduled_week(utc(2026, 11, 7, 3, 0)), "2026-W46")       # Fri Nov 6 19:00 PST, the Saturday after the clocks went back
        self.assertEqual(weekly.scheduled_week(utc(2026, 12, 26, 3, 0)), "2026-W53")      # Fri Dec 25: the Monday after is Dec 28, still ISO week 53 of 2026
        self.assertEqual(weekly.scheduled_week(utc(2027, 1, 2, 3, 0)), "2027-W01")        # Fri Jan 1 2027 19:00 PST: the Monday after is Jan 4, week 1 of 2027
        self.assertEqual(weekly.scheduled_week(FRIDAY), WEEK)

    def test_every_fridays_key_is_the_week_after_its_own_and_none_repeats_in_two_years(self):
        friday = datetime(2026, 1, 2, 12, 0, tzinfo=weekly.PACIFIC)
        keys = []
        for n in range(104):
            day = friday + timedelta(days=7 * n)
            saturday = utc(day.year, day.month, day.day, 3, 0) + timedelta(days=1)       # 03:00 UTC is inside the window in winter, and an hour past it in summer
            for hour in (2, 3):
                moment = utc(saturday.year, saturday.month, saturday.day, hour, 30)
                if weekly.in_friday_window(moment):
                    own = weekly.week_key(day)
                    nxt = weekly.week_key(day + timedelta(days=3))                       # the Monday after
                    self.assertEqual(weekly.scheduled_week(moment), nxt)
                    self.assertNotEqual(nxt, own, f"Friday {day.date()}")
                    keys.append(nxt)
        self.assertEqual(len(keys), 104)
        self.assertEqual(len(set(keys)), 104)

    def test_the_window_and_the_briefs_date_come_from_the_run_time_not_from_the_keys_monday(self):
        from agents import reader, runs as runs_module
        moment = utc(2026, 10, 31, 2, 30)                                           # Fri 2026-10-30 19:30 PDT
        self.assertEqual(weekly.scheduled_week(moment), "2026-W45")                  # whose Monday is Nov 2, three days after the run
        window = reader.window_of(moment)
        self.assertEqual(window["to"], moment.isoformat())                           # the window ends at the run time, not at the Monday
        self.assertEqual(window["from"], (moment - reader.WINDOW).isoformat())
        self.assertEqual(runs_module._at(moment).date().isoformat(), "2026-10-31")  # the brief's date (as_of) is the run's own date
        self.assertNotEqual(weekly.week_start("2026-W45").isoformat(), "2026-10-31")  # which the key's Monday (Nov 2) would not give


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
        with unittest.mock.patch.object(briefs, "get", return_value=("2026-W44", {}, utc(2026, 10, 24, 2, 5))):
            code = weekly.scheduled(utc(2026, 10, 24, 2, 0), connect=connect, build_client=self.client, run=self.run, out=self.lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(self.lines, ["scheduled: 2026-W44 is already published (2026-10-24 02:05 UTC); nothing done"])

    FIRST_FRIDAY = utc(2026, 10, 31, 2, 30)          # Fri 2026-10-30 19:30 PDT

    @contextmanager
    def ok(self):
        yield unittest.mock.Mock(execute=lambda *a: unittest.mock.Mock(fetchone=lambda: None))      # a connection that finds no run row

    def test_the_first_scheduled_friday_is_week_45_is_not_already_published_and_is_run_at_its_own_time(self):
        seen = {}

        def run(conn, client, now, run_id, **kw):
            seen.update(now=now, run_id=run_id)
            return {"reader": {}, "framer": {}, "gates": {}, "publish": {}}
        asked = []

        def get(conn, week):
            asked.append(week)
            return None if len(asked) == 1 else ("2026-W45", {}, self.FIRST_FRIDAY)        # absent before the run, published after it
        with unittest.mock.patch.object(briefs, "get", get), unittest.mock.patch.object(weekly, "resolve_run", return_value="2026-W45"), \
                unittest.mock.patch.object(runs, "next_stage", return_value=None):
            code = weekly.scheduled(self.FIRST_FRIDAY, connect=self.ok, build_client=lambda: object(), run=run, out=self.lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(asked[0], "2026-W45")
        self.assertEqual(seen["run_id"], "2026-W45")
        self.assertEqual(seen["now"], self.FIRST_FRIDAY)                                    # the run is handed its own time, which sets the Reader's window
        self.assertNotIn("already published", self.lines[-1])
        self.assertIn("2026-W45 published", self.lines[-1])

    def test_a_second_run_the_same_night_says_already_published(self):
        again = utc(2026, 10, 31, 3, 30)                                                    # the other cron entry: Fri 20:30 PDT, outside the window
        self.assertEqual(weekly.scheduled(again, connect=self.ok, build_client=lambda: self.fail("no client"), out=self.lines.append), 0)
        self.assertIn("outside Friday", self.lines[-1])
        with unittest.mock.patch.object(briefs, "get", return_value=("2026-W45", {}, utc(2026, 10, 31, 2, 41))):
            self.assertEqual(weekly.scheduled(utc(2026, 10, 31, 2, 50), connect=self.ok, build_client=lambda: self.fail("no client"),
                                              run=lambda *a, **k: self.fail("no run"), out=self.lines.append), 0)
        self.assertEqual(self.lines[-1], "scheduled: 2026-W45 is already published (2026-10-31 02:41 UTC); nothing done")

    def test_the_first_friday_after_the_clock_change_is_week_46(self):
        saturday_03 = utc(2026, 11, 7, 3, 0)                                                # Fri 2026-11-06 19:00 PST
        self.assertTrue(weekly.in_friday_window(saturday_03))
        with unittest.mock.patch.object(briefs, "get", return_value=("2026-W46", {}, saturday_03)):
            weekly.scheduled(saturday_03, connect=self.ok, build_client=lambda: self.fail("no client"), out=self.lines.append)
        self.assertIn("2026-W46 is already published", self.lines[-1])

    def test_a_stopped_run_writes_one_line_and_exits_nonzero(self):
        @contextmanager
        def connect():
            yield unittest.mock.Mock()
        client = lambda: object()          # noqa: E731
        with unittest.mock.patch.object(briefs, "get", return_value=None), unittest.mock.patch.object(weekly, "resolve_run", return_value="2026-W44"), \
                unittest.mock.patch.object(runs, "next_stage", return_value="reader"):
            for boom in (budget.BudgetExceeded("month ceiling"), weekly.GateFailed("2 hard failures"), RuntimeError("model returned no text")):
                self.lines.clear()
                code = weekly.scheduled(utc(2026, 10, 24, 2, 0), connect=connect, build_client=client, run=unittest.mock.Mock(side_effect=boom),
                                        out=self.lines.append)
                self.assertEqual(code, 1)
                self.assertEqual(len(self.lines), 1)
                self.assertIn("scheduled: 2026-W44 stopped:", self.lines[0])

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
