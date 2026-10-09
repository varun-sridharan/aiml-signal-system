"""The spend ledger, against the real database, without spending anything.

    python -m unittest tests.test_ledger -v

No test makes an API call: the model client is a fake that returns canned usage. Tests
that need the database skip when it is unreachable (start the tunnel first), except the
fail-closed tests, which are the point of having no database.

Everything written to the live `spend_ledger` table happens inside a transaction that is
rolled back, in a synthetic month (2099-01) so live spend cannot affect the arithmetic.
The concurrency test needs two connections to see each other's commits, which a
rolled-back transaction cannot give it, so it runs in a throwaway schema with its own
copy of the table and drops it afterwards. The live table is never committed to.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "agents"))
sys.path.insert(0, str(REPO / "data" / "evals"))

from agents import budget, llm  # noqa: E402

JAN_2099 = datetime(2099, 1, 15, 12, 0, tzinfo=timezone.utc)
FEB_2099 = datetime(2099, 2, 1, 0, 0, tzinfo=timezone.utc)
HISTORICAL_PHASES = ("framing", "faithfulness", "eval-faithfulness", "eval-usefulness")


def usage(inp=1000, out=500, cache_read=0, cache_write=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write)


class FakeClient:
    """Stands in for anthropic.Anthropic. Counts calls; never touches the network."""

    def __init__(self, stop_reason="end_turn", raises=None, usage_=None):
        self.calls = 0
        self._stop, self._raises, self._usage = stop_reason, raises, usage_ or usage()
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        if self._raises:
            raise self._raises
        return SimpleNamespace(stop_reason=self._stop, stop_details=None, usage=self._usage,
                               content=[SimpleNamespace(type="text", text='{"ok": true}')])


def database_available() -> bool:
    try:
        with budget.connect():
            return True
    except budget.LedgerUnavailable:
        return False


DB = database_available()
needs_db = unittest.skipUnless(DB, f"ledger database unreachable — {budget.TUNNEL_HINT}")


@contextmanager
def env(**values):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------------------
# a) the ledger, in a rolled-back transaction
# --------------------------------------------------------------------------------------

@needs_db
class LedgerInRolledBackTransaction(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        # Open the outer transaction now, so every conn.transaction() inside the code
        # under test becomes a savepoint within it rather than a commit.
        self.conn.execute("SELECT 1")
        self.live_rows = self.count()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            n = check.execute("SELECT count(*) FROM spend_ledger").fetchone()[0]
        self.assertEqual(n, self.live_rows, "a test leaked a row into the live ledger")

    def count(self, where="TRUE", args=()):
        return self.conn.execute(f"SELECT count(*) FROM spend_ledger WHERE {where}", args).fetchone()[0]

    def seed(self, usd, now=JAN_2099, state="settled"):
        self.conn.execute(
            "INSERT INTO spend_ledger (id, ts, user_id, phase, model, estimated_cost_usd, state) "
            "VALUES (%s, %s, 'test', 'test-seed', 'claude-haiku-4-5', %s, %s)",
            (uuid.uuid4(), now, Decimal(usd), state),
        )

    def test_reserve_then_settle_replaces_worst_case_with_actual(self):
        rid = budget.reserve(self.conn, user_id="test", phase="test", model="claude-haiku-4-5",
                             estimate_usd=0.5, now=JAN_2099)
        row = self.conn.execute("SELECT state, estimated_cost_usd FROM spend_ledger WHERE id=%s", (rid,)).fetchone()
        self.assertEqual(row, ("reserved", Decimal("0.500000")))
        self.assertEqual(budget.month_to_date_spend(self.conn, JAN_2099), Decimal("0.5"))

        u = usage(inp=2000, out=300, cache_read=100, cache_write=50)
        cost = budget.settle(self.conn, rid, "claude-haiku-4-5", u)
        expected = Decimal(str(round(budget.estimate_cost_usd("claude-haiku-4-5", u), 6)))
        row = self.conn.execute(
            "SELECT state, estimated_cost_usd, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens FROM spend_ledger WHERE id=%s", (rid,)).fetchone()
        self.assertEqual(row, ("settled", expected, 2000, 300, 100, 50))
        self.assertEqual(cost, expected)
        self.assertEqual(budget.month_to_date_spend(self.conn, JAN_2099), expected)
        with self.assertRaises(RuntimeError):
            budget.settle(self.conn, rid, "claude-haiku-4-5", u)  # a settled row stays settled

    def test_call_that_would_cross_the_ceiling_is_refused_naming_all_three(self):
        self.seed("19.50")
        before = self.count()
        with self.assertRaises(budget.BudgetExceeded) as refused:
            budget.reserve(self.conn, user_id="test", phase="test", model="claude-haiku-4-5",
                           estimate_usd=0.6, now=JAN_2099)
        msg = str(refused.exception)
        for figure in ("$19.50", "$0.60", "$20.00"):
            self.assertIn(figure, msg)
        self.assertEqual(self.count(), before, "a refused call must book nothing")
        # Exactly at the ceiling is allowed: the rule is "would exceed", not "would reach".
        budget.reserve(self.conn, user_id="test", phase="test", model="claude-haiku-4-5",
                       estimate_usd=0.5, now=JAN_2099)

    def test_orphaned_reservation_counts_toward_the_month(self):
        # A process that died mid-call: reserved, never settled.
        budget.reserve(self.conn, user_id="test", phase="test", model="claude-opus-5",
                       estimate_usd=1.25, now=JAN_2099)
        self.assertEqual(budget.month_to_date_spend(self.conn, JAN_2099), Decimal("1.25"))
        self.seed("18.00")
        with self.assertRaises(budget.BudgetExceeded):  # 18.00 + 1.25 + 1.00 > 20
            budget.reserve(self.conn, user_id="test", phase="test", model="claude-haiku-4-5",
                           estimate_usd=1.0, now=JAN_2099)
        # ...and only toward its own month.
        self.assertEqual(budget.month_to_date_spend(self.conn, FEB_2099), Decimal("0"))

    def test_llm_call_books_exactly_once_and_settles_at_actual(self):
        u = usage(inp=1234, out=567)
        client = FakeClient(usage_=u)
        before = self.count("phase = 'test-call'")
        text, got_usage, cost = llm.call(client, model="claude-haiku-4-5", max_tokens=1000,
                                         user_id="test", phase="test-call", conn=self.conn,
                                         messages=[{"role": "user", "content": "x" * 300}])
        self.assertEqual((client.calls, text), (1, '{"ok": true}'))
        rows = self.conn.execute(
            "SELECT state, estimated_cost_usd, user_id FROM spend_ledger WHERE phase='test-call'").fetchall()
        self.assertEqual(len(rows) - before, 1)
        self.assertEqual(rows[-1][0], "settled")
        self.assertEqual(rows[-1][2], "test")
        self.assertAlmostEqual(float(rows[-1][1]), budget.estimate_cost_usd("claude-haiku-4-5", u), places=6)
        self.assertAlmostEqual(cost, budget.estimate_cost_usd("claude-haiku-4-5", u), places=6)

    def test_truncated_response_is_still_booked_before_the_run_exits(self):
        client = FakeClient(stop_reason="max_tokens", usage_=usage(out=1000))
        with self.assertRaises(SystemExit):
            llm.call(client, model="claude-haiku-4-5", max_tokens=1000, user_id="test",
                     phase="test-truncated", conn=self.conn, messages=[])
        state = self.conn.execute("SELECT state FROM spend_ledger WHERE phase='test-truncated'").fetchone()
        self.assertEqual(state, ("settled",), "a billed-but-truncated call went unrecorded")

    def test_request_that_raises_leaves_the_worst_case_reserved(self):
        client = FakeClient(raises=ConnectionError("network dropped mid-call"))
        with self.assertRaises(ConnectionError):
            llm.call(client, model="claude-haiku-4-5", max_tokens=1000, user_id="test",
                     phase="test-raised", conn=self.conn, messages=[])
        row = self.conn.execute(
            "SELECT state, estimated_cost_usd FROM spend_ledger WHERE phase='test-raised'").fetchone()
        self.assertEqual(row[0], "reserved")
        self.assertGreater(row[1], 0)

    def test_refused_call_never_reaches_the_client(self):
        self.seed("19.99", now=datetime.now(timezone.utc))  # llm.call books in the real month
        client = FakeClient()
        with self.assertRaises(budget.BudgetExceeded):
            llm.call(client, model="claude-haiku-4-5", max_tokens=16_000, user_id="test",
                     phase="test-refused", conn=self.conn, messages=[])  # worst case $0.08
        self.assertEqual(client.calls, 0)

    def test_phase_and_user_id_are_required(self):
        with self.assertRaises(TypeError):
            llm.call(FakeClient(), model="claude-haiku-4-5", max_tokens=10, conn=self.conn, messages=[])
        with self.assertRaises(ValueError):
            llm.call(FakeClient(), model="claude-haiku-4-5", max_tokens=10, user_id="",
                     phase="", conn=self.conn, messages=[])


# --------------------------------------------------------------------------------------
# a) concurrency, in a throwaway schema
# --------------------------------------------------------------------------------------

@needs_db
class ConcurrentReservations(unittest.TestCase):
    """Two runs at $19.50 spent, each wanting $0.40: at most one may pass."""

    def setUp(self):
        self.schema = f"ledger_test_{uuid.uuid4().hex[:12]}"
        with budget.connect() as admin:
            admin.execute(f"CREATE SCHEMA {self.schema}")
        self.a, self.b = (self.scoped() for _ in range(2))
        self.a.execute(budget.SCHEMA_PATH.read_text())
        self.a.execute(
            "INSERT INTO spend_ledger (id, ts, user_id, phase, model, estimated_cost_usd, state) "
            "VALUES (%s, %s, 'test', 'seed', 'claude-haiku-4-5', 19.50, 'settled')", (uuid.uuid4(), JAN_2099))
        self.a.commit()

    def tearDown(self):
        for c in (self.a, self.b):
            c.rollback()
            c.close()
        with budget.connect() as admin:
            admin.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def scoped(self):
        c = budget.connect(autocommit=False)
        c.execute(f"SET search_path TO {self.schema}")
        c.commit()
        return c

    def race(self, reserve):
        """A reserves and holds its transaction open; B tries meanwhile. Returns B's outcome."""
        out = {}

        def run_b():
            self.b.execute("SELECT 1")
            t0 = time.monotonic()
            try:
                reserve(self.b)
                out["b"] = "passed"
            except budget.BudgetExceeded:
                out["b"] = "refused"
            out["waited"] = time.monotonic() - t0
            self.b.commit()

        self.a.execute("SELECT 1")
        reserve(self.a)                      # A has checked and inserted, not yet committed
        t = threading.Thread(target=run_b)
        t.start()
        time.sleep(0.6)                      # B is now running while A holds its transaction
        self.a.commit()
        t.join(10)
        total = self.a.execute("SELECT sum(estimated_cost_usd) FROM spend_ledger").fetchone()[0]
        self.a.commit()
        return out, total

    def test_lock_serialises_check_and_insert(self):
        def locked(conn):
            budget.reserve(conn, user_id="test", phase="race", model="claude-haiku-4-5",
                           estimate_usd=0.40, now=JAN_2099)
        out, total = self.race(locked)
        self.assertEqual(out["b"], "refused")
        self.assertGreaterEqual(out["waited"], 0.5, "B should have blocked on A's lock")
        self.assertEqual(total, Decimal("19.90"))

    def test_control_without_the_lock_both_pass(self):
        """The same race with the lock removed: proves the test above can fail."""
        def unlocked(conn):
            with conn.transaction():
                spent = budget.month_to_date_spend(conn, JAN_2099)
                if spent + Decimal("0.40") > budget.MONTHLY_BUDGET_USD:
                    raise budget.BudgetExceeded("over")
                conn.execute(
                    "INSERT INTO spend_ledger (id, ts, user_id, phase, model, estimated_cost_usd, state) "
                    "VALUES (%s, %s, 'test', 'race', 'claude-haiku-4-5', 0.40, 'reserved')",
                    (uuid.uuid4(), JAN_2099))
        out, total = self.race(unlocked)
        self.assertEqual(out["b"], "passed")
        self.assertEqual(total, Decimal("20.30"), "without the lock the ceiling is crossed")


# --------------------------------------------------------------------------------------
# The worst-case estimate is never under the real cost
# --------------------------------------------------------------------------------------

@needs_db
class EstimateIsAnUpperBound(unittest.TestCase):
    """Every historical call, priced by the new pre-flight estimate, from its rebuilt request.

    The requests are rebuilt from today's prompts and the golden inputs, since the ledger
    keeps token counts, not payloads. For each phase the SMALLEST rebuilt request is used,
    so the estimate being compared is the least generous one available.
    """

    @classmethod
    def setUpClass(cls):
        import framer
        import run_evals  # noqa: F401  (data/evals is put on the path below)

        profile = framer.load_json(framer.PROFILE_PATH)
        cases = run_evals.load_cases(None)
        system_prompt = framer.load_framer_system_prompt()
        raw = framer.load_json(framer.DEFAULT_RAW_PATH)
        checks = [framer.check_request(framer.build_check_payload(c["input"], run_evals.framing_from_framed_day(c["output"])))
                  for c in cases]
        cls.requests = {
            "framing": [framer.framing_request(system_prompt, profile, raw)],
            "faithfulness": checks,
            "eval-faithfulness": checks,
            "eval-usefulness": [run_evals.usefulness_request(profile, c) for c in cases],
        }
        with budget.connect() as conn:
            cls.rows = conn.execute(
                "SELECT phase, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
                "estimated_cost_usd FROM spend_ledger WHERE state = 'settled' AND phase = ANY(%s)",
                (list(HISTORICAL_PHASES),)).fetchall()

    def least(self, phase):
        def priced(req):
            req = dict(req)
            model, max_tokens = req.pop("model"), req.pop("max_tokens")
            req.pop("stream", None)
            return (budget.estimate_call_ceiling_usd(model, max_tokens, req), budget.estimate_input_tokens(req))
        return min(priced(r) for r in self.requests[phase])

    def test_history_is_present(self):
        self.assertGreaterEqual(len(self.rows), 36, "expected at least the 36 calls imported from usage.json")

    def test_estimate_covers_actual_cost_of_every_recorded_call(self):
        for phase, model, inp, out, cr, cw, recorded in self.rows:
            ceiling, _ = self.least(phase)
            actual = budget.estimate_cost_usd(model, usage(inp, out, cr, cw))
            # The file ledger rounded with float round(); allow its half-micro-dollar.
            self.assertLessEqual(abs(actual - float(recorded)), 0.000001)
            self.assertGreaterEqual(ceiling, actual, f"{phase}: ${ceiling:.6f} < actual ${actual:.6f}")

    def test_estimated_input_covers_recorded_input_tokens(self):
        """The stricter half: the output allowance alone would hide a low input guess.

        Only for calls whose request can be rebuilt as it was sent. The eval-usefulness
        judge reads the frozen golden output with an unchanged prompt (its recorded
        input held at 2499-2526 tokens from August to September), so today's rebuild IS
        that request. The grader's payload shrank by two thirds in the Sep-15 split, and
        the Framer's prompt has moved, so rebuilding those calls today would compare a
        different request against the old count. Each call is matched to the golden case
        whose rebuilt size is nearest its recorded size.
        """
        rebuilt = []
        for req in self.requests["eval-usefulness"]:
            req = dict(req)
            [req.pop(k, None) for k in ("model", "max_tokens", "stream")]
            rebuilt.append(budget.estimate_input_tokens(req))
        checked = 0
        for phase, _model, inp, _out, cr, cw, _cost in self.rows:
            if phase != "eval-usefulness":
                continue
            recorded = inp + cr + cw
            est = min(rebuilt, key=lambda e: abs(e - recorded))
            self.assertGreaterEqual(est, recorded, f"{phase}: {est} < {recorded} input tokens")
            checked += 1
        self.assertGreaterEqual(checked, 7)  # the seven imported; later runs add more


# --------------------------------------------------------------------------------------
# b) fail closed
# --------------------------------------------------------------------------------------

class FailsClosed(unittest.TestCase):
    def test_no_database_url_refuses_the_call(self):
        client = FakeClient()
        with env(DATABASE_URL=""):  # load_dotenv never overrides a set variable
            with self.assertRaises(budget.LedgerUnavailable) as refused:
                llm.call(client, model="claude-haiku-4-5", max_tokens=10, user_id="t", phase="t", messages=[])
        self.assertIn("DATABASE_URL is not set", str(refused.exception))
        self.assertEqual(client.calls, 0)

    def test_tunnel_down_names_the_fix_and_hides_the_url(self):
        with env(DATABASE_URL="postgresql://someone:pw-NOT-SHOWN@127.0.0.1:1/railway"):
            with self.assertRaises(budget.LedgerUnavailable) as refused:
                budget.connect(connect_timeout=2)
        msg = str(refused.exception)
        self.assertIn(budget.TUNNEL_HINT, msg)
        self.assertNotIn("pw-NOT-SHOWN", msg)
        self.assertNotIn("someone", msg)
        self.assertIsNone(refused.exception.__cause__)
        self.assertTrue(refused.exception.__suppress_context__)

    def _run(self, *args):
        # A dummy key as well as no database: even a regression that got past the
        # ledger would be refused by the API with a 401 rather than spend anything.
        child = dict(os.environ, DATABASE_URL="", ANTHROPIC_API_KEY="sk-ant-invalid-test-key")
        return subprocess.run([sys.executable, *args], cwd=REPO, env=child, capture_output=True, text=True, timeout=120)

    def test_no_api_evals_pass_without_a_database(self):
        r = self._run("data/evals/run_evals.py", "--no-api")
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertIn("all hard gates passed", r.stdout)

    def test_paid_eval_run_is_refused_before_any_call(self):
        r = self._run("data/evals/run_evals.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DATABASE_URL is not set", r.stderr)
        self.assertNotIn("FAITHFULNESS", r.stdout)  # no case got as far as a call

    def test_framer_run_is_refused_before_any_call(self):
        r = self._run("agents/framer.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DATABASE_URL is not set", r.stderr)
        self.assertNotIn("Framing", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
