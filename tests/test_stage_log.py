"""What the scheduled job tells Railway's log (2026-11-06: the log ended at the Reader and the real end was visible only in the database).

Every stage writes a line when it starts and one when it ends, with the time and the peak memory; SIGTERM says which stage it hit, marks that stage
failed, and unwinds; the job's output is line-buffered. The database tests run in a rolled-back transaction on a week no real run uses.
"""

from __future__ import annotations

import io
import os
import re
import signal
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, runs, weekly  # noqa: E402
from tests._db import needs_db  # noqa: E402

NOW = datetime(1998, 7, 3, 3, 0, tzinfo=timezone.utc)
RUN = "1998-W30"
WEEK = "2026-W46"


class Borrowed:
    """A connection another piece of code may `with`, without closing the test's own."""
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


@needs_db
class StageLines(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.counts()
        self.lines = []
        runs.emit = self.lines.append
        runs.open_run(self.conn, RUN, NOW)

    def tearDown(self):
        runs.emit = None
        runs._active = None
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        return (conn or self.conn).execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM run_stages)").fetchone()

    def row(self, stage="reader"):
        return self.conn.execute("SELECT status, error FROM run_stages WHERE run_id = %s AND stage = %s ORDER BY attempt DESC LIMIT 1", (RUN, stage)).fetchone()

    def test_a_stage_writes_a_line_at_its_start_and_one_at_its_end(self):
        runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: {"items": []})
        self.assertEqual(len(self.lines), 2)
        start, end = self.lines
        self.assertRegex(start, rf"^stage {RUN} reader attempt 1 started at \d\d:\d\d:\d\dZ, peak RSS \d+ MB$")
        self.assertRegex(end, rf"^stage {RUN} reader attempt 1 done in \d+s at \d\d:\d\d:\d\dZ, peak RSS \d+ MB$")

    def test_the_line_is_stamped_with_the_wall_clock_not_the_runs_fixed_one(self):
        runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: {"items": []})
        stamp = re.search(r"at (\d\d:\d\d:\d\d)Z", self.lines[0]).group(1)
        self.assertNotEqual(stamp, "03:00:00")          # NOW is 03:00:00; the line says when it really ran

    def test_a_failing_stage_writes_a_failed_line_and_is_marked_failed(self):
        def boom(attempt):
            raise ValueError("no")
        with self.assertRaises(ValueError):
            runs.checkpointed(self.conn, RUN, "reader", NOW, boom)
        self.assertRegex(self.lines[-1], rf"^stage {RUN} reader attempt 1 failed after \d+s \(ValueError\) at \d\d:\d\d:\d\dZ, peak RSS \d+ MB$")
        self.assertEqual(self.row(), ("failed", "ValueError: no"))
        self.assertIsNone(runs.active())

    def test_a_finished_stage_is_not_announced_again_on_a_resume(self):
        runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: {"items": []})
        del self.lines[:]
        runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: self.fail("done stages are loaded, not rerun"))
        self.assertEqual(self.lines, [])

    def test_it_is_silent_when_nothing_asked_for_the_lines(self):
        runs.emit = None
        runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: {"items": []})
        self.assertEqual(self.lines, [])

    def test_the_active_stage_is_known_while_it_runs(self):
        seen = []
        runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: seen.append(runs.active()) or {"items": []})
        self.assertEqual(seen, [(RUN, "reader", 1)])
        self.assertIsNone(runs.active())

    def handler(self):
        out = []
        return weekly.sigterm_handler(WEEK, out.append, lambda: Borrowed(self.conn), lambda: NOW), out

    def test_sigterm_names_the_stage_marks_it_failed_and_unwinds(self):
        handle, out = self.handler()

        def work(attempt):
            handle(signal.SIGTERM, None)
        with self.assertRaises(runs.Terminated):
            runs.checkpointed(self.conn, RUN, "reader", NOW, work)
        self.assertEqual(out, [f"scheduled: {WEEK} killed by SIGTERM during reader"])
        self.assertEqual(self.row(), ("failed", "killed by SIGTERM"))        # the handler's mark stands; the unwind did not overwrite it
        self.assertIsNone(runs.active())

    def test_the_next_start_after_a_sigterm_is_a_new_attempt_not_a_busy_stage(self):
        handle, _ = self.handler()
        with self.assertRaises(runs.Terminated):
            runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: handle(signal.SIGTERM, None))
        self.assertEqual(runs.start_stage(self.conn, RUN, "reader", NOW), 2)

    def test_sigterm_between_stages_says_so_and_still_unwinds(self):
        handle, out = self.handler()
        with self.assertRaises(runs.Terminated):
            handle(signal.SIGTERM, None)
        self.assertEqual(out, [f"scheduled: {WEEK} killed by SIGTERM during no stage"])

    def test_a_handler_that_cannot_reach_the_database_still_prints_and_unwinds(self):
        out = []

        def refuse():
            raise budget.LedgerUnavailable("down")
        handle = weekly.sigterm_handler(WEEK, out.append, refuse, lambda: NOW)
        runs._active = (RUN, "gates", 1)
        with self.assertRaises(runs.Terminated):
            handle(signal.SIGTERM, None)
        self.assertEqual(out[0], f"scheduled: {WEEK} killed by SIGTERM during gates")
        self.assertIn("could not mark gates failed", out[1])

    def test_a_real_sigterm_through_the_installed_handler(self):
        out = []
        previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, weekly.sigterm_handler(WEEK, out.append, lambda: Borrowed(self.conn), lambda: NOW))
        try:
            with self.assertRaises(runs.Terminated):
                runs.checkpointed(self.conn, RUN, "reader", NOW, lambda attempt: os.kill(os.getpid(), signal.SIGTERM))
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.assertEqual(out, [f"scheduled: {WEEK} killed by SIGTERM during reader"])
        self.assertEqual(self.row(), ("failed", "killed by SIGTERM"))


class ScheduledJob(unittest.TestCase):
    def test_the_scheduled_job_turns_the_stage_lines_on_and_off_again(self):
        lines = []
        seen = []

        def run(conn, client, now, run_id, clock=None):
            seen.append(runs.emit)
            return {"publish": {}}

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                return mock.Mock(fetchone=lambda: None)
        with mock.patch.object(weekly.briefs, "get", return_value=None), mock.patch.object(weekly, "resolve_run", return_value=RUN):
            weekly.scheduled(datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc), connect=Conn, build_client=lambda: object(), run=run, out=lines.append)
        self.assertIsNotNone(seen[0])
        self.assertIsNone(runs.emit)                    # off again, so nothing else in the process writes stage lines

    def test_prepare_line_buffers_the_output_and_installs_the_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        out, err = io.TextIOWrapper(io.BytesIO(), write_through=False), io.TextIOWrapper(io.BytesIO())
        try:
            with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
                weekly.prepare_scheduled_process(WEEK)
            self.assertTrue(out.line_buffering and err.line_buffering)
            self.assertNotEqual(signal.getsignal(signal.SIGTERM), previous)
            self.assertTrue(callable(signal.getsignal(signal.SIGTERM)))
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_the_peak_memory_is_a_plausible_number_of_megabytes(self):
        self.assertTrue(1 <= runs.peak_rss_mb() < 100_000)


if __name__ == "__main__":
    unittest.main()
