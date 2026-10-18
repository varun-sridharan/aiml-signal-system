"""Runs and their stages, against the real database, without keeping anything.

    python -m unittest tests.test_runs -v

Every test runs inside a transaction that is rolled back, so no run or stage it writes
survives it; tearDown checks the live row counts are unchanged. Tests skip when the
database is unreachable (start the tunnel first), or fail with REQUIRE_DB=1.

To prove the trigger tests can fail, run with a trigger disabled:

    RUNS_TEST_DISABLE_TRIGGER=run_stages_order python -m unittest tests.test_runs

The trigger is disabled inside each test's own transaction (ALTER TABLE is transactional
in Postgres), so the rollback restores it and the live table is never left unguarded.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import psycopg  # noqa: E402

from agents import budget, runs  # noqa: E402
from tests._db import needs_db  # noqa: E402

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
RUN = "1999-W01"  # a week no real run will ever use, so a live row cannot collide
READER_OUT = {"items": ["item-01", "item-02"], "excerpts": ["6f1c0e6e-0b4e-4b0e-9a57-1f1f0c3c9a01"], "fetched": 2}


DISABLED = os.environ.get("RUNS_TEST_DISABLE_TRIGGER", "").strip()


@needs_db
class Runs(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")  # open the outer transaction; everything below is a savepoint
        self.live = self.counts()
        if DISABLED:
            table = "runs" if DISABLED.startswith("runs_") else "run_stages"
            self.conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER {DISABLED}")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        conn = conn or self.conn
        return conn.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM run_stages)").fetchone()

    def attempt(self, stage, attempt, cols="status, error, finished_at"):
        return self.conn.execute(
            f"SELECT {cols} FROM run_stages WHERE run_id = %s AND stage = %s AND attempt = %s",
            (RUN, stage, attempt)).fetchone()

    def complete(self, stage, output=None, at=NOW):
        n = runs.start_stage(self.conn, RUN, stage, at)
        runs.finish_stage(self.conn, RUN, stage, n, output if output is not None else {"stage": stage}, at)
        return n

    def assertRefusedByDatabase(self, sql, args, error=psycopg.errors.RestrictViolation):
        with self.assertRaises(error) as refused:
            with self.conn.transaction():  # a savepoint, so the failure does not end the test
                self.conn.execute(sql, args)
        return str(refused.exception)

    # 1 ----------------------------------------------------------------------------
    def test_1_a_fresh_run_starts_at_the_reader(self):
        run = runs.open_run(self.conn, RUN, NOW)
        self.assertEqual((run.id, run.status, run.finished_at), (RUN, "running", None))
        self.assertEqual(runs.next_stage(self.conn, RUN), "reader")
        # Opening it again (a rerun) returns the same run, with its original start.
        self.assertEqual(runs.open_run(self.conn, RUN, NOW + timedelta(days=1)), run)

    # 2 ----------------------------------------------------------------------------
    def test_2_a_failed_framer_resumes_at_the_framer_with_the_readers_output(self):
        runs.open_run(self.conn, RUN, NOW)
        self.complete("reader", READER_OUT)
        n = runs.start_stage(self.conn, RUN, "framer", NOW)
        runs.fail_stage(self.conn, RUN, "framer", n, "model returned no text", NOW)
        self.assertEqual(runs.next_stage(self.conn, RUN), "framer")
        self.assertEqual(runs.start_stage(self.conn, RUN, "framer", NOW + timedelta(minutes=5)), 2)
        self.assertEqual(runs.stage_output(self.conn, RUN, "reader"), READER_OUT)
        with self.assertRaises(runs.RunRefused):  # a done stage is loaded, never re-run
            runs.start_stage(self.conn, RUN, "reader", NOW)

    # 3 ----------------------------------------------------------------------------
    # The update is also refused by run_stages_transitions (done -> done is not a
    # transition), so 3a checks the message to tell the two apart. 3b has no backstop.
    def test_3a_database_refuses_to_change_a_done_stage(self):
        runs.open_run(self.conn, RUN, NOW)
        self.complete("reader", READER_OUT)
        msg = self.assertRefusedByDatabase(
            "UPDATE run_stages SET output = '{\"items\": []}' WHERE run_id = %s AND stage = 'reader'", (RUN,))
        self.assertIn("checkpoint", msg)
        self.assertEqual(runs.stage_output(self.conn, RUN, "reader"), READER_OUT)

    def test_3b_database_refuses_to_delete_a_done_stage(self):
        runs.open_run(self.conn, RUN, NOW)
        self.complete("reader", READER_OUT)
        self.assertRefusedByDatabase("DELETE FROM run_stages WHERE run_id = %s AND stage = 'reader'", (RUN,))
        self.assertEqual(runs.stage_output(self.conn, RUN, "reader"), READER_OUT)

    # 4 ----------------------------------------------------------------------------
    def test_4_database_refuses_a_stage_before_the_one_ahead_is_done(self):
        runs.open_run(self.conn, RUN, NOW)
        runs.start_stage(self.conn, RUN, "reader", NOW)  # running, not done
        msg = self.assertRefusedByDatabase(
            "INSERT INTO run_stages (run_id, stage, attempt, status, started_at) VALUES (%s, 'framer', 1, 'running', %s)",
            (RUN, NOW))
        self.assertIn("before reader is done", msg)
        with self.assertRaises(psycopg.errors.RestrictViolation):
            with self.conn.transaction():
                runs.start_stage(self.conn, RUN, "publish", NOW)

    # 5 ----------------------------------------------------------------------------
    def test_5_one_owner_per_running_stage(self):
        runs.open_run(self.conn, RUN, NOW)
        runs.start_stage(self.conn, RUN, "reader", NOW)
        almost_stale = NOW + runs.STALE_AFTER  # exactly at the limit is still owned
        with self.assertRaises(runs.StageBusy):
            runs.start_stage(self.conn, RUN, "reader", almost_stale)
        self.assertEqual(self.attempt("reader", 1, "status")[0], "running")
        # The unique index is the backstop for a worker that skips start_stage().
        self.assertRefusedByDatabase(
            "INSERT INTO run_stages (run_id, stage, attempt, status, started_at) VALUES (%s, 'reader', 2, 'running', %s)",
            (RUN, NOW), error=psycopg.errors.UniqueViolation)

    # 6 ----------------------------------------------------------------------------
    def test_6_a_stale_attempt_is_abandoned_and_a_new_one_starts(self):
        runs.open_run(self.conn, RUN, NOW - timedelta(hours=3))
        runs.start_stage(self.conn, RUN, "reader", NOW - timedelta(hours=3))
        self.assertEqual(runs.start_stage(self.conn, RUN, "reader", NOW), 2)
        self.assertEqual(self.attempt("reader", 1), ("failed", "abandoned", NOW))
        self.assertEqual(self.attempt("reader", 2, "status")[0], "running")

    # 7 ----------------------------------------------------------------------------
    def test_7_finishing_publish_finishes_the_run(self):
        runs.open_run(self.conn, RUN, NOW)
        for i, stage in enumerate(runs.STAGES):
            self.assertEqual(runs.next_stage(self.conn, RUN), stage)
            self.complete(stage, at=NOW + timedelta(minutes=i))
        self.assertIsNone(runs.next_stage(self.conn, RUN))
        run = runs.open_run(self.conn, RUN, NOW + timedelta(days=1))
        self.assertEqual((run.status, run.finished_at), ("done", NOW + timedelta(minutes=3)))

    # 8 ----------------------------------------------------------------------------
    def test_8_output_over_64_kb_is_refused(self):
        runs.open_run(self.conn, RUN, NOW)
        n = runs.start_stage(self.conn, RUN, "reader", NOW)
        big = {"items": [f"item-{i:06d}-{'x' * 40}" for i in range(1500)]}  # ~85 KB of pointers
        with self.assertRaises(runs.RunRefused):
            runs.finish_stage(self.conn, RUN, "reader", n, big, NOW)
        with self.assertRaises(runs.RunRefused):  # one long string is text, not a pointer
            runs.finish_stage(self.conn, RUN, "reader", n, {"text": "y" * 2000}, NOW)
        self.assertEqual(self.attempt("reader", n, "status")[0], "running")
        # And the cap is the database's, not only this module's.
        self.assertRefusedByDatabase(
            "UPDATE run_stages SET status = 'done', finished_at = %s, output = %s WHERE run_id = %s AND attempt = %s",
            (NOW, psycopg.types.json.Jsonb(big), RUN, n), error=psycopg.errors.CheckViolation)

    # 9 ----------------------------------------------------------------------------
    def test_9_database_refuses_any_transition_but_running_to_done_or_failed(self):
        runs.open_run(self.conn, RUN, NOW)
        n = runs.start_stage(self.conn, RUN, "reader", NOW)
        runs.fail_stage(self.conn, RUN, "reader", n, "fetch timed out", NOW)
        for status, finished in (("running", None), ("done", NOW)):
            msg = self.assertRefusedByDatabase(
                "UPDATE run_stages SET status = %s, finished_at = %s WHERE run_id = %s AND attempt = %s",
                (status, finished, RUN, n))
            self.assertIn("not allowed", msg)
        msg = self.assertRefusedByDatabase(  # born done, skipping running
            "INSERT INTO run_stages (run_id, stage, attempt, status, started_at, finished_at) "
            "VALUES (%s, 'reader', 2, 'done', %s, %s)", (RUN, NOW, NOW))
        self.assertIn("starts as running", msg)
        with self.assertRaises(runs.RunRefused):  # a failed attempt cannot be finished
            runs.finish_stage(self.conn, RUN, "reader", n, READER_OUT, NOW)

    # 10 ---------------------------------------------------------------------------
    def test_10_errors_are_stored_without_credentials(self):
        runs.open_run(self.conn, RUN, NOW)
        n = runs.start_stage(self.conn, RUN, "reader", NOW)
        runs.fail_stage(self.conn, RUN, "reader", n,
                        "connection to postgresql://app:hunter2@db.internal:5432/x failed; password=hunter2 "
                        "api_key: sk-ant-abc123def456ghi " + "z" * 2000, NOW)
        error = self.attempt("reader", n, "error")[0]
        self.assertNotIn("hunter2", error)
        self.assertNotIn("sk-ant", error)
        self.assertLessEqual(len(error), runs.MAX_ERROR_CHARS)

    def test_14_a_clock_stamps_each_stage_when_it_really_starts_and_ends(self):
        runs.open_run(self.conn, RUN, NOW)
        ticks = iter(NOW + timedelta(minutes=m) for m in range(1, 20))
        runs.checkpointed(self.conn, RUN, "reader", lambda: next(ticks), lambda attempt: {"ok": 1})
        started, finished = self.conn.execute(
            "SELECT started_at, finished_at FROM run_stages WHERE run_id = %s AND stage = 'reader'", (RUN,)).fetchone()
        self.assertEqual((started, finished), (NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)))

    # 11-13: a finished run ------------------------------------------------------------
    def test_11_database_refuses_to_change_a_finished_run(self):
        runs.open_run(self.conn, RUN, NOW)
        for stage in runs.STAGES:
            self.complete(stage)
        self.assertRefusedByDatabase(  # reopened
            "UPDATE runs SET status = 'running', finished_at = NULL WHERE id = %s", (RUN,))
        self.assertRefusedByDatabase(  # re-dated
            "UPDATE runs SET finished_at = %s WHERE id = %s", (NOW + timedelta(days=1), RUN))
        self.assertEqual(runs.open_run(self.conn, RUN, NOW).finished_at, NOW)

    def test_12_database_refuses_to_delete_a_finished_run(self):
        # Marked done by hand, with no stages, so ON DELETE RESTRICT cannot be what refuses.
        runs.open_run(self.conn, RUN, NOW)
        self.conn.execute("UPDATE runs SET status = 'done', finished_at = %s WHERE id = %s", (NOW, RUN))
        self.assertRefusedByDatabase("DELETE FROM runs WHERE id = %s", (RUN,))
        self.assertEqual(runs.open_run(self.conn, RUN, NOW).status, "done")

    def test_13_database_allows_a_run_only_running_to_done(self):
        runs.open_run(self.conn, RUN, NOW)
        self.assertRefusedByDatabase("UPDATE runs SET started_at = %s WHERE id = %s", (NOW - timedelta(days=1), RUN))
        self.assertRefusedByDatabase("UPDATE runs SET id = '1999-W02' WHERE id = %s", (RUN,))
        self.assertRefusedByDatabase("UPDATE runs SET status = 'running' WHERE id = %s", (RUN,))
        self.conn.execute("UPDATE runs SET status = 'done', finished_at = %s WHERE id = %s", (NOW, RUN))  # allowed
        self.assertEqual(runs.open_run(self.conn, RUN, NOW).status, "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
