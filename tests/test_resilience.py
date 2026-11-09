"""Run 10: an empty, refused or truncated model reply, and an overloaded API, are named and survivable. No paid call anywhere in this file.

    python -m unittest tests.test_resilience -v

The model client is a script of outcomes (a reply, or an error), the ledger is a rolled-back transaction, and the backoff sleeps and the
clock are replaced, so every retry runs for real and takes no time. Skips when the database is unreachable; fails with REQUIRE_DB=1.
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import anthropic
import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import briefs, budget, citegate, composer, dedupe, framer, llm, repair, review, runs, weekly  # noqa: E402
from agents import reader  # noqa: E402
from tests._db import needs_db  # noqa: E402
from tests.test_reader import fetch_result_message  # noqa: E402
from tests.test_weekly import NOW, PAGE, Pipeline, REAL_COMPOSE, SimpleUsage, WEEK, canned_composed  # noqa: E402

REAL_MIN_STORIES = composer.MIN_STORIES
REQUEST = httpx.Request("POST", "https://api.anthropic.test/v1/messages")
SCHEMA = {"type": "object", "properties": {"things": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}},
          "required": ["things"], "additionalProperties": False}
GOOD = json.dumps({"things": ["a"]})


def reply(text=GOOD, stop="end_turn", out=10, rid="req_ok"):
    return NS(stop_reason=stop, stop_details=None, content=[NS(type="text", text=text)], _request_id=rid,
              usage=NS(input_tokens=100, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def status_error(code):
    return anthropic.APIStatusError(f"error {code}", response=httpx.Response(code, request=REQUEST, headers={"request-id": f"req_{code}"}), body=None)


class Script:
    """Stands in for anthropic.Anthropic: answers each request from a list, in order, and keeps every request."""

    def __init__(self, *outcomes):
        self.outcomes, self.requests = list(outcomes), []
        self.messages = NS(create=self._answer, stream=self._stream)

    def _answer(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome(request) if callable(outcome) else outcome

    def _stream(self, **request):
        message = self._answer(**request)

        class Context:
            def __enter__(self_inner):
                return NS(get_final_message=lambda: message, request_id="req_from_the_stream")

            def __exit__(self_inner, *a):
                return False
        return Context()


@needs_db
class LedgerCase(unittest.TestCase):
    """A rolled-back ledger, a recorded sleep, and a clock that only moves when a test moves it."""

    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.conn.execute("SELECT count(*) FROM spend_ledger").fetchone()[0]
        self.slept, self.clock = [], [0.0]
        for p in (unittest.mock.patch.object(llm, "_sleep", self.slept.append), unittest.mock.patch.object(llm, "_monotonic", lambda: self.clock[0])):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT count(*) FROM spend_ledger").fetchone()[0], self.live, "a test leaked a row into the live ledger")

    def ask(self, client, **kw):
        args = dict(model=llm.CHECK_MODEL, max_tokens=1000, user_id="t", phase="probe", conn=self.conn, messages=[{"role": "user", "content": "x"}],
                    output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
        args.update(kw)
        return llm.call(client, **args)

    def booked(self):
        return self.conn.execute("SELECT phase, state, stop_reason, request_id, output_tokens, estimated_cost_usd FROM spend_ledger "
                                 "WHERE user_id = 't' ORDER BY ts, id").fetchall()


class EmptyRepliesAreRejectedBySchema(unittest.TestCase):
    """Every structured-output schema in the repo refuses the empty reply that used to pass it (2026-W46's was 120 tokens of empty fields)."""

    def rejects(self, schema, empty):
        self.assertTrue(llm.schema_problems(empty, schema), f"accepted {empty!r}")

    def test_the_composer_refuses_the_w46_reply_and_every_one_of_its_parts(self):
        w46 = {"thread": {"lines": [], "body": ""}, "learning": {"headline": "", "paras": [], "notes": [], "drawnFrom": [], "doDifferently": ""},
               "storiesTitle": "", "stories": [], "dividers": [], "closing": {"lines": [], "summary": "", "oneThing": ""}, "splits": [], "counts": []}
        problems = llm.schema_problems(w46, composer.COMPOSE_SCHEMA)
        self.assertTrue(any("$.stories: 0 items" in p for p in problems) or len(problems) >= 8, problems)
        full = canned_composed({"items": [{"id": "i1"}, {"id": "i2"}]})
        full["splits"] = [{"path": "thread.body", "runs": [{"text": "x", "class": "fact"}]}]
        full["counts"] = []
        self.assertEqual(llm.schema_problems(full, composer.COMPOSE_SCHEMA), [])
        for part in (("thread", "body"), ("closing", "summary"), ("learning", "headline")):
            broken = json.loads(json.dumps(full))
            broken[part[0]][part[1]] = ""
            self.rejects(composer.COMPOSE_SCHEMA, broken)
        broken = json.loads(json.dumps(full))
        broken["stories"][0]["body"] = ""
        self.rejects(composer.COMPOSE_SCHEMA, broken)

    def test_the_other_schemas_refuse_their_empty_replies(self):
        self.rejects(composer.REVISE_SCHEMA, {"fields": []})
        self.rejects(framer.FRAMING_SCHEMA, {"thread": "", "threadSplit": [], "items": []})
        self.rejects(framer.FAITHFULNESS_SCHEMA, {"items": [{"id": "", "claims": [], "confidence": 0.5}], "thread": {"claims": [], "confidence": 0.5}})
        self.rejects(dedupe.JUDGE_SCHEMA, {"same": True, "reason": ""})
        self.rejects(repair.SECOND_SCHEMA, {"supported": False, "sentence_ids": [], "why": ""})
        self.rejects(citegate.SINGLE_SCHEMA, {"answers": []})
        self.rejects(citegate.SECOND_SCHEMA, {"answers": []})
        self.rejects(citegate.MARKED_SCHEMA, {"answers": []})

    def test_the_one_reply_that_may_be_empty_is_the_rewrite_and_says_so(self):
        self.assertEqual(llm.schema_problems({"text": "", "runs": []}, repair.REWRITE_SCHEMA), [])     # "if nothing true can be said, return an empty text"

    def test_the_checker_names_what_is_wrong(self):
        self.assertEqual(llm.schema_problems({"things": []}, SCHEMA), ["$.things: 0 items, at least 1 required"])
        self.assertEqual(llm.schema_problems({"things": [""]}, SCHEMA), ["$.things[0]: empty string, at least 1 character required"])
        self.assertEqual(llm.schema_problems({}, SCHEMA), ["$.things: required and missing"])
        self.assertEqual(llm.schema_problems({"things": ["a"]}, SCHEMA), [])


class StopReasons(LedgerCase):
    def test_a_refusal_is_asked_again_once_then_named(self):
        client = Script(reply("", "refusal", rid="req_r1"), reply("", "refusal", rid="req_r2"))
        with self.assertRaises(llm.ModelRefusal) as e:
            self.ask(client)
        self.assertEqual((e.exception.reason, e.exception.stop_reason, len(client.requests)), ("refusal", "refusal", 2))
        self.assertEqual([b[0] for b in self.booked()], ["probe", "probe-retry"])
        self.assertEqual([b[2] for b in self.booked()], ["refusal", "refusal"])

    def test_a_refusal_then_an_answer_is_one_retry_and_the_answer(self):
        text, _usage, _cost = self.ask(Script(reply("", "refusal"), reply()))
        self.assertEqual(text, GOOD)

    def test_max_tokens_is_retried_once_with_the_cap_doubled(self):
        client = Script(reply("{", "max_tokens"), reply())
        self.ask(client, max_tokens=8000)
        self.assertEqual([r["max_tokens"] for r in client.requests], [8000, 16000])
        self.assertEqual([b[0] for b in self.booked()], ["probe", "probe-retry"])

    def test_max_tokens_twice_is_named_and_the_retry_stops_at_the_models_limit(self):
        with self.assertRaises(llm.ModelMaxTokens) as e:
            self.ask(Script(reply("{", "max_tokens"), reply("{", "max_tokens")), max_tokens=8000)
        self.assertEqual(e.exception.reason, "max_tokens")
        client = Script(reply("{", "max_tokens"))
        with self.assertRaises(llm.ModelMaxTokens):
            self.ask(client, max_tokens=llm.MODEL_MAX_OUTPUT[llm.CHECK_MODEL])        # no room to double: no second call
        self.assertEqual(len(client.requests), 1)
        client = Script(reply("{", "max_tokens"), reply())
        self.ask(client, model=llm.FRAMER_MODEL, max_tokens=100_000)
        self.assertEqual([r["max_tokens"] for r in client.requests], [100_000, 128_000])     # doubling is capped at the limit

    def test_the_ledger_keeps_stop_reason_request_id_and_tokens(self):
        self.ask(Script(reply(rid="req_abc", out=33)))
        (row,) = self.booked()
        self.assertEqual(row[:5], ("probe", "settled", "end_turn", "req_abc", 33))


class Malformed(LedgerCase):
    def test_an_empty_reply_is_asked_again_once_under_the_retry_phase(self):
        client = Script(reply(json.dumps({"things": []})), reply())
        text, _u, cost = self.ask(client)
        self.assertEqual(text, GOOD)
        self.assertEqual([b[0] for b in self.booked()], ["probe", "probe-retry"])
        self.assertGreater(cost, 0)

    def test_two_empty_replies_raise_with_the_stop_reason_and_the_reply_head(self):
        client = Script(reply(json.dumps({"things": []})), reply(json.dumps({"things": []})))
        with self.assertRaises(llm.ModelMalformedReply) as e:
            self.ask(client)
        self.assertEqual(len(client.requests), 2)
        self.assertIn("things", str(e.exception))
        self.assertIn("at least 1", str(e.exception))
        self.assertIn("stop_reason=end_turn", e.exception.log_detail())

    def test_text_that_is_not_json_and_a_reply_with_no_text_are_named(self):
        with self.assertRaises(llm.ModelMalformedReply) as e:
            self.ask(Script(reply("not json"), reply("still not")))
        self.assertIn("not valid JSON", str(e.exception))
        with self.assertRaises(llm.ModelEmptyReply) as e:
            self.ask(Script(reply(""), reply("  ")))
        self.assertEqual(e.exception.reason, "empty_reply")

    def test_a_validator_can_reject_a_reply_the_schema_accepts(self):
        client = Script(reply(), reply())
        with self.assertRaises(llm.ModelMalformedReply) as e:
            self.ask(client, validate=lambda parsed: "no verdict for unit(s) s03")
        self.assertIn("no verdict for unit(s) s03", str(e.exception))

    def test_the_log_head_is_capped_at_500_characters_and_hides_credentials(self):
        secret = "sk-ant-api03-" + "x" * 40
        long = ("word " * 400) + secret + " DATABASE_URL=postgres://u:pw@host/db"
        client = Script(reply(long), reply(long))
        with self.assertRaises(llm.ModelMalformedReply) as e:
            self.ask(client)
        self.assertLessEqual(len(e.exception.reply), llm.LOG_HEAD_CHARS)
        both = llm.log_head(secret + " DATABASE_URL=postgres://u:pw@host/db")
        self.assertNotIn("sk-ant", both)
        self.assertNotIn("pw@host", both)
        self.assertLessEqual(len(e.exception.log_detail()), llm.LOG_HEAD_CHARS + 120)


class QaFixes(LedgerCase):
    def test_a_large_doubled_retry_is_streamed_because_the_sdk_refuses_it_otherwise(self):
        streams = []
        client = Script(reply("{", "max_tokens"), reply())
        real_create, real_stream = client.messages.create, client.messages.stream
        client.messages.create = lambda **kw: (streams.append(False), real_create(**kw))[1]
        client.messages.stream = lambda **kw: (streams.append(True), real_stream(**kw))[1]
        self.ask(client, max_tokens=16_000)
        self.assertEqual(streams, [False, True])            # 16,000 as asked; the doubled 32,000 is streamed
        self.assertEqual([r["max_tokens"] for r in client.requests], [16_000, 32_000])

    def test_a_structural_retry_after_a_doubling_keeps_the_doubled_cap(self):
        client = Script(reply("{", "max_tokens"), reply(json.dumps({"things": []})), reply())
        self.ask(client, max_tokens=8000)
        self.assertEqual([r["max_tokens"] for r in client.requests], [8000, 16000, 16000])

    def test_any_400_that_names_the_schema_gets_the_unbounded_retry(self):
        err = anthropic.APIStatusError("Invalid request: minLength is not supported here", response=httpx.Response(400, request=REQUEST), body=None)
        client = Script(err, reply())
        self.assertEqual(self.ask(client)[0], GOOD)
        self.assertNotIn("minLength", json.dumps(client.requests[1]["output_config"]))

    def test_a_named_failure_of_the_lift_retry_keeps_the_first_verdict(self):
        verdict = {"claims": [{"claim": "c", "supported": True, "supporting_span": "s", "span_verified": True}], "lifted_claims": ["x"], "unsupported_claims": []}
        raw_day = {"items": [{"id": "a", "headline": "h", "source_excerpt": "text"}]}
        calls = []

        def call(client, **kw):
            calls.append(kw["phase"])
            if kw["phase"].endswith("-retry"):
                raise llm.ModelRefusal("refused twice")
            return json.dumps({"items": [{"id": "a", "claims": [], "confidence": 0.9}], "thread": {"claims": [], "confidence": 0.9}}), NS(), 0.0
        with unittest.mock.patch.object(framer.llm, "call", call), unittest.mock.patch.object(framer, "RETRY_ON_LIFT", True), unittest.mock.patch.object(framer, "build_check_payload", return_value={"items": [], "thread": {"writing_under_test": ""}}), \
                unittest.mock.patch.object(framer, "ground_truth_for", return_value=({"a": "t"}, "t")), \
                unittest.mock.patch.object(framer, "normalise_verdict", lambda v, *a, **k: dict(verdict) if v.get("id") == "a" else {**verdict, "lifted_claims": []}), \
                unittest.mock.patch.object(framer, "_unit_payload", return_value={"items": [{"writing_under_test": "x"}], "thread": {"writing_under_test": ""}}), \
                unittest.mock.patch.object(framer, "_add_usage", lambda a, b: a):
            verdicts, _thread, _usage = framer.check_faithfulness(object(), raw_day, {"items": []}, user_id="t", phase="faithfulness")[:3]
        self.assertEqual(calls, ["faithfulness", "faithfulness-retry"])
        self.assertIn("first kept", verdicts["a"]["retried"]["result"])


class SchemaBoundsRejected(LedgerCase):
    def test_an_api_that_refuses_the_bounds_is_asked_again_without_them_and_the_reply_is_still_checked(self):
        refusal = anthropic.APIStatusError("output_config.format.schema: minItems is not supported", response=httpx.Response(400, request=REQUEST), body=None)
        client = Script(refusal, reply(json.dumps({"things": []})), reply())
        text, _u, _c = self.ask(client)
        self.assertEqual(text, GOOD)
        first, second, third = (r["output_config"]["format"]["schema"] for r in client.requests)
        self.assertIn("minItems", json.dumps(first))
        self.assertNotIn("minItems", json.dumps(second))                 # asked again without the bounds...
        self.assertNotIn("minLength", json.dumps(second))
        self.assertNotIn("minItems", json.dumps(third))                  # ...and the empty reply it returned was refused here and asked for once more

    def test_any_other_400_is_not_retried(self):
        client = Script(status_error(400), reply())
        with self.assertRaises(llm.ModelRejected):
            self.ask(client)
        self.assertEqual(len(client.requests), 1)


class Transient(LedgerCase):
    def test_overload_a_5xx_and_a_timeout_are_each_tried_three_times_with_a_backoff(self):
        for error in (status_error(529), status_error(500), anthropic.APITimeoutError(request=REQUEST)):
            self.slept.clear()
            client = Script(error, error, error)
            with self.assertRaises(llm.ModelUnavailable) as e:
                self.ask(client)
            self.assertEqual(len(client.requests), 3, type(error).__name__)
            self.assertEqual(self.slept, [5, 20])
            self.assertEqual(e.exception.reason, "unavailable")

    def test_an_overload_then_an_answer_is_one_retry_booked_under_the_retry_phase(self):
        text, _u, _c = self.ask(Script(status_error(529), reply()))
        self.assertEqual(text, GOOD)
        self.assertEqual(self.slept, [5])
        rows = self.booked()
        self.assertEqual([r[0] for r in rows], ["probe", "probe-retry"])
        self.assertEqual((rows[0][1], rows[0][5]), ("settled", Decimal("0")))               # the overloaded request was never billed
        self.assertEqual(rows[0][2], "error 529")

    def test_no_try_starts_after_two_minutes(self):
        def slow(request):
            self.clock[0] += 119
            raise status_error(500)
        client = Script(slow, reply())
        with self.assertRaises(llm.ModelUnavailable):
            self.ask(client)
        self.assertEqual(len(client.requests), 1)                                           # 119 s in, a 5 s wait would end past 120 s
        self.assertEqual(self.slept, [])

    def test_a_5xx_is_settled_at_zero(self):
        before = budget.month_to_date_spend(self.conn)
        with self.assertRaises(llm.ModelUnavailable):
            self.ask(Script(status_error(500), status_error(502), status_error(503)))
        self.assertEqual(budget.month_to_date_spend(self.conn), before)
        self.assertEqual({r[1] for r in self.booked()}, {"settled"})

    def test_a_rejected_request_is_not_retried_and_leaves_nothing_in_the_month(self):
        client = Script(status_error(400))
        before = budget.month_to_date_spend(self.conn)
        with self.assertRaises(llm.ModelRejected) as e:
            self.ask(client, model=llm.FRAMER_MODEL, max_tokens=100_000)
        self.assertEqual((e.exception.reason, len(client.requests), self.slept), ("rejected", 1, []))
        self.assertEqual(budget.month_to_date_spend(self.conn), before)                    # 2026-11-08: a rejected probe had left $5.00 behind
        self.assertEqual(self.booked()[0][1:3], ("settled", "rejected 400"))


class EveryOutcomeSettles(LedgerCase):
    """2026-11-09: whatever a request ends in, its reservation is closed. Nothing stays 'reserved' to inflate the month."""

    def assert_closed(self, expected_state_reason, cost_at_most=None, user="t"):
        rows = self.conn.execute("SELECT phase, state, stop_reason, request_id, output_tokens, estimated_cost_usd FROM spend_ledger "
                                 "WHERE user_id = %s ORDER BY ts, id", (user,)).fetchall()
        self.assertEqual({r[1] for r in rows}, {"settled"}, rows)
        self.assertNotIn("reserved", {r[1] for r in rows})
        if expected_state_reason:
            self.assertTrue(any(expected_state_reason in (r[2] or "") for r in rows), rows)
        if cost_at_most is not None:
            self.assertLessEqual(sum(r[5] for r in rows), Decimal(str(cost_at_most)))

    def test_a_4xx_rejection(self):
        with self.assertRaises(llm.ModelRejected):
            self.ask(Script(status_error(400)), model=llm.FRAMER_MODEL, max_tokens=100_000)
        self.assert_closed("rejected 400", 0)

    def test_a_429_and_a_529_and_a_5xx(self):
        for code in (429, 529, 500):
            with self.assertRaises(llm.ModelUnavailable):
                self.ask(Script(*[status_error(code)] * 3), user_id=f"t{code}")
            self.assert_closed(f"error {code}", 0, user=f"t{code}")

    def test_a_timeout_and_a_dropped_connection_are_closed_at_their_input_cost_not_their_worst_case(self):
        for error, word in ((anthropic.APITimeoutError(request=REQUEST), "timed out"), (anthropic.APIConnectionError(request=REQUEST), "connection failed")):
            user = "t-" + word.split()[0]
            with self.assertRaises(llm.ModelUnavailable):
                self.ask(Script(error, error, error), model=llm.FRAMER_MODEL, max_tokens=40_000, user_id=user)
            self.assert_closed(word + "; output unknown", 0.01, user=user)       # the worst case of 40,000 Opus output tokens would be $1.00 each
            rows = self.conn.execute("SELECT estimated_cost_usd FROM spend_ledger WHERE user_id = %s", (user,)).fetchall()
            self.assertTrue(all(r[0] > 0 for r in rows))                         # but not zero: the input was sent

    def test_an_error_event_inside_a_stream(self):
        event = anthropic.APIStatusError("overloaded", response=httpx.Response(200, request=REQUEST), body=None)
        with self.assertRaises(llm.ModelUnavailable):
            self.ask(Script(event, event, event))
        self.assert_closed("server error in the stream; output unknown", 0.01)

    def test_a_refusal_is_settled_with_its_real_usage(self):
        client = Script(reply("", stop="refusal", out=0), reply("", stop="refusal", out=0))
        with self.assertRaises(llm.ModelRefusal):
            self.ask(client)
        self.assert_closed("refusal", 0.01)

    def test_a_streamed_reply_books_the_streams_request_id(self):
        self.ask(Script(reply(rid=None)), stream=True, user_id="t-stream")
        rows = self.conn.execute("SELECT request_id, stop_reason FROM spend_ledger WHERE user_id = 't-stream'").fetchall()
        self.assertEqual(rows, [("req_from_the_stream", "end_turn")])        # 2026-11-09: it was NULL for every streamed call

    def test_the_input_estimate_covers_dense_json_at_two_and_a_half_characters_a_token(self):
        # measured 2026-11-09 on the W46-r2 framing call: 6,000 input tokens for 15,033 characters
        request = {"messages": [{"role": "user", "content": "x" * 15_033}]}
        self.assertGreaterEqual(budget.estimate_input_tokens(request), 6_000)

    def test_a_client_side_error_before_send(self):
        def broken(request):
            raise ValueError("max_tokens is too large for a non-streamed request")
        with self.assertRaises(ValueError):
            self.ask(Script(broken))
        self.assert_closed("client error before send", 0)


class NoRevisionOfNothing(unittest.TestCase):
    def test_a_structurally_empty_draft_is_never_revised(self):
        empty = {"thread": {"lines": [], "body": ""}, "learning": {"paras": []}, "closing": {"summary": ""}, "stories": []}
        with unittest.mock.patch.object(composer.llm, "call", side_effect=AssertionError("a revise call was made")):
            with self.assertRaises(composer.EmptyComposerReply) as e:
                composer.revise(object(), "S", {"user_id": "t", "role": "r", "goals": [], "interests": []}, {"items": []}, {"items": []}, "w", None,
                                empty, {"thread.body": ["x"]})
        self.assertIn("structurally empty", str(e.exception))

    def test_a_stored_empty_edition_fails_the_gates_before_any_grading(self):
        empty = {"thread": {"lines": [], "body": ""}, "learning": {"paras": []}, "closing": {"summary": ""}, "stories": []}
        art = {"raw_day": {"items": []}, "framing": {"items": []}, "composed": empty, "composing": "on"}
        with unittest.mock.patch.object(runs, "stage_output", return_value={}), \
                unittest.mock.patch.object(framer, "check_faithfulness", side_effect=AssertionError("graded")):
            with self.assertRaises(weekly.GateFailed) as e:
                weekly.claims_gates(object(), object(), WEEK, art, 1, NOW, {"user_id": "t"})
        self.assertIn("no grading or repair was run", str(e.exception))


class StageLine(unittest.TestCase):
    def test_a_named_failure_puts_its_stop_reason_and_the_reply_head_on_the_stage_line(self):
        lines = []
        error = llm.ModelMalformedReply("the reply does not conform: $.stories: 0 items", stop_reason="end_turn", reply="x" * 900, request_id="req_1")
        conn = unittest.mock.MagicMock()
        with unittest.mock.patch.object(runs, "emit", lines.append), unittest.mock.patch.object(runs, "_done_stages", return_value=[]), \
                unittest.mock.patch.object(runs, "start_stage", return_value=1), unittest.mock.patch.object(runs, "fail_stage"):
            with self.assertRaises(llm.ModelMalformedReply):
                runs.checkpointed(conn, "2026-W99", "framer", NOW, lambda attempt: (_ for _ in ()).throw(error))
        failed = [l for l in lines if "failed after" in l][0]
        self.assertIn("reason=malformed_reply; stop_reason=end_turn; request=req_1", failed)
        self.assertEqual(failed.count("x"), llm.LOG_HEAD_CHARS)                             # the reply is cut at 500 characters


class RefusedPageDropsOnlyThatStory(Pipeline, unittest.TestCase):
    """A page the model refuses to read, twice, drops that story and the week goes on; the review note says which and why."""

    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.conn.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items), "
                                      "(SELECT count(*) FROM spend_ledger)").fetchone()
        self.start()
        self.refuse = None
        client = self.client
        real = client.create

        def create(**request):
            message = real(**request)
            if self.refuse and self.refuse in request["messages"][0]["content"]:
                message.stop_reason = "refusal"
            return message
        client.create = create
        client.messages = client

    def tearDown(self):
        self.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items), "
                                           "(SELECT count(*) FROM spend_ledger)").fetchone(), self.live)

    def test_fetch_text_returns_the_refusal_instead_of_raising(self):
        client = Script(reply("", "refusal"), reply("", "refusal"))
        got = reader.fetch_text(client, "https://openai.com/index/x/", conn=self.conn)
        self.assertEqual((got.text, got.error), (None, reader.REFUSED))
        self.assertEqual([b[0] for b in self.conn.execute("SELECT phase FROM spend_ledger WHERE phase LIKE 'reader-fetch%' ORDER BY ts, id").fetchall()][-2:],
                         ["reader-fetch", "reader-fetch-retry"])

    def test_one_refused_page_is_dropped_and_the_rest_publish_with_the_reason_in_the_review_note(self):
        # The fixture week has two stories, so the floor is lowered to 1 here to let the survivor publish; the real floor of 4 is the constant
        # composer.MIN_STORIES (REAL_MIN_STORIES) and is tested with the gates (tests/test_weekly.py). What this proves is the drop: only the
        # refused story leaves, nothing else changes, and the note says which and why.
        weekly.run_week(self.conn, self.client, NOW, WEEK, stop_after="reader")
        baseline = runs.stage_output(self.conn, WEEK, "reader")["items"]
        victim = self.client.requests[0]["messages"][0]["content"].rsplit(": ", 1)[-1]
        self.assertEqual(len(baseline), 2)
        self.conn.rollback()
        self.conn.execute("SELECT 1")                       # the week again, from nothing, with one page the model will not read
        self.refuse = victim
        with unittest.mock.patch.object(composer, "MIN_STORIES", 1):
            weekly.run_week(self.conn, self.client, NOW, WEEK)
        stats = runs.stage_output(self.conn, WEEK, "reader")["stats"]
        self.assertEqual([r["why"] for r in stats["refused"]], ["the model refused to read this page, twice"])
        _id, body, _at = briefs.get(self.conn, WEEK)
        self.assertEqual(len(body["items"]), len(baseline) - 1)                     # only that story went
        self.assertEqual(self.conn.execute("SELECT count(*) FROM spend_ledger WHERE phase = 'reader-fetch-retry' AND stop_reason = 'refusal'").fetchone()[0], 1)
        note = review.edition_note(WEEK, 1, NOW, runs.load_artifact(self.conn, WEEK, "gates"))
        held = [e for e in note["entries"] if e["stage"] == "reading the sources"]
        self.assertEqual((len(held), held[0]["kind"], held[0]["claim"]), (1, "held", "the model refused to read this page, twice"))
        self.assertEqual(REAL_MIN_STORIES, 4)

    def test_with_the_real_floor_a_drop_that_leaves_fewer_than_four_stops_the_edition(self):
        weekly.run_week(self.conn, self.client, NOW, WEEK, stop_after="reader")
        victim = self.client.requests[0]["messages"][0]["content"].rsplit(": ", 1)[-1]
        self.conn.rollback()
        self.conn.execute("SELECT 1")
        self.refuse = victim
        with unittest.mock.patch.object(composer, "MIN_STORIES", REAL_MIN_STORIES), self.assertRaises(weekly.GateFailed) as e:
            weekly.run_week(self.conn, self.client, NOW, WEEK)
        self.assertIn("4 are needed to publish", str(e.exception))


class ScheduledResume(Pipeline, unittest.TestCase):
    """The Friday job's one in-window resume after a stage fails, and the final failure line."""

    IN_WINDOW = datetime(1999, 3, 6, 3, 0, tzinfo=timezone.utc)          # Fri 1999-03-05 19:00 PST: the fixture week, 1999-W10

    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.conn.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items), "
                                      "(SELECT count(*) FROM spend_ledger)").fetchone()
        self.start()
        self.lines, self.slept, self.now = [], [], self.IN_WINDOW

    def tearDown(self):
        self.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(check.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items), "
                                           "(SELECT count(*) FROM spend_ledger)").fetchone(), self.live)

    def go(self, *, spent=Decimal("0"), month=Decimal("2")):
        @contextmanager
        def connect():
            yield self.conn

        def run(conn, client, now, run_id, **kw):
            return weekly.run_week(conn, client, NOW, run_id, **kw)
        with unittest.mock.patch.object(budget, "spend_since", lambda conn, since: spent), \
                unittest.mock.patch.object(budget, "month_to_date_spend", lambda conn, now=None: month):
            return weekly.scheduled(self.IN_WINDOW, connect=connect, build_client=lambda: self.client, run=run, out=self.lines.append,
                                    sleep=self.slept.append, clock=lambda: self.now)

    def test_a_failed_stage_is_resumed_once_five_minutes_later_inside_the_window(self):
        self.fail_framer = 1
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.slept, [300])
        self.assertTrue(any("will resume once in 5 minutes" in l for l in self.lines))
        self.assertRegex(self.lines[-1], rf"scheduled: {WEEK} published \({WEEK}, from framer\)")
        self.assertEqual(len(self.framer_calls), 2)
        self.assertEqual([r[2] for r in self.conn.execute("SELECT stage, attempt, status FROM run_stages WHERE run_id = %s ORDER BY started_at", (WEEK,)).fetchall()],
                         ["done", "failed", "done", "done", "done"])

    def test_it_resumes_only_once(self):
        self.fail_framer = 5
        self.assertEqual(self.go(), 1)
        self.assertEqual(self.slept, [300])
        self.assertEqual(len(self.framer_calls), 2)
        self.assertIn(f"scheduled: {WEEK} FAILED at stage framer: RuntimeError", self.lines[-1])

    def test_no_resume_when_the_wait_would_end_outside_the_window(self):
        self.fail_framer = 1
        self.now = self.IN_WINDOW + timedelta(minutes=58)                    # 19:58: five minutes later is 20:03
        self.assertEqual(self.go(), 1)
        self.assertEqual((self.slept, len(self.framer_calls)), ([], 1))
        self.assertTrue(any("not resumed: the 5-minute wait would end outside Friday" in l for l in self.lines))

    def test_no_resume_over_the_runs_own_cap_or_the_ledger(self):
        for kwargs, why in (({"spent": Decimal("4.01")}, "already spent $4.01"), ({"month": Decimal("37")}, "leaves no room")):
            self.lines.clear()
            self.fail_framer = 1
            self.go(**kwargs)
            self.assertTrue(any("not resumed" in l and why in l for l in self.lines), self.lines)
            self.assertEqual(self.slept, [])
            self.fail_framer = 0
            self.conn.rollback()
            self.conn.execute("SELECT 1")
            self.framer_calls.clear()

    def test_a_gate_failure_is_not_resumed_and_names_the_new_attempt_command(self):
        def compose(*a, **k):
            raise weekly.GateFailed("2 hard-gate failure(s): essay: the grader enumerated no claims")
        self.edition_problems = ["essay: x"]
        with unittest.mock.patch.object(weekly, "run_week", side_effect=weekly.GateFailed("2 hard-gate failure(s)")):
            self.go()
        self.assertEqual(self.slept, [])
        self.assertIn("--new-attempt --reuse-reader", self.lines[-1])

    def test_the_final_failure_line_has_the_week_stage_reason_and_command(self):
        line = weekly.failure_line("2026-W46", "2026-W46", "framer", llm.ModelRefusal("the model refused twice"))
        for part in ("2026-W46", "stage framer", "refusal", "--week 2026-W46", "Nobody was emailed"):
            self.assertIn(part, line)


EMPTY_W46 = {"thread": {"lines": [], "body": ""}, "learning": {"headline": "", "paras": [], "notes": [], "drawnFrom": [], "doDifferently": ""},
             "storiesTitle": "", "stories": [], "dividers": [], "closing": {"lines": [], "summary": "", "oneThing": ""}, "splits": [], "counts": []}


class KeepOpen:
    """The test's ledger connection, which a call that opens its own may close without ending the test."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


class Faulty:
    """The reader's fake client for fetches; composing calls (`stream`) answer from a script that injects the failures."""

    def __init__(self, base, outcomes):
        self.base, self.outcomes, self.composing_requests, self.messages = base, outcomes, [], self

    def create(self, **request):
        return self.base.create(**request)

    def requests(self):
        return self.base.requests

    def stream(self, **request):
        self.composing_requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        message = outcome() if callable(outcome) else outcome

        class Context:
            def __enter__(self_inner):
                return NS(get_final_message=lambda: message, request_id="req_from_the_stream")

            def __exit__(self_inner, *a):
                return False
        return Context()


class ConfidenceWeek(Pipeline, unittest.TestCase):
    """Run 10, step 7: a fixture week through the scheduled path with the real llm layer under the composing call, and a failure injected
    at every point the policy covers. The stage lines, every retry and the final-failure line are checked as they will read on Friday."""

    IN_WINDOW = datetime(1999, 3, 6, 3, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.conn.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items), "
                                      "(SELECT count(*) FROM spend_ledger)").fetchone()
        self.start()
        self.lines, self.job_slept, self.backoffs, self.wide_day = [], [], [], None
        self.real_connect, self.began = budget.connect, datetime.now(timezone.utc)
        real_request = composer.compose_request

        def request(system_prompt, profile, raw_day, *a, **k):
            self.wide_day = raw_day
            return real_request(system_prompt, profile, raw_day, *a, **k)
        for p in (unittest.mock.patch.object(composer, "compose_with_revision", REAL_COMPOSE), unittest.mock.patch.object(composer, "compose_request", request),
                  unittest.mock.patch.object(composer, "revisable_problems", lambda *a, **k: {}),
                  unittest.mock.patch.object(llm, "_sleep", self.backoffs.append),
                  unittest.mock.patch.object(budget, "connect", lambda **kw: KeepOpen(self.conn))):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.stop()
        self.conn.rollback()
        self.conn.close()
        with self.real_connect() as check:
            self.assertEqual(check.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items), "
                                           "(SELECT count(*) FROM spend_ledger)").fetchone(), self.live)

    def good(self):
        edition = canned_composed(self.wide_day)
        edition["splits"] = [{"path": "thread.body", "runs": [{"text": edition["thread"]["body"], "class": "fact"}]}]
        edition["counts"] = []
        return reply(json.dumps(edition), out=1500, rid="req_good")

    def week(self, outcomes):
        client = Faulty(self.client, outcomes)

        @contextmanager
        def connect():
            yield self.conn

        def run(conn, client_, now, run_id, **kw):
            return weekly.run_week(conn, client_, NOW, run_id, **kw)
        with unittest.mock.patch.object(budget, "spend_since", lambda conn, since: Decimal("0.5")), \
                unittest.mock.patch.object(budget, "month_to_date_spend", lambda conn, now=None: Decimal("3")):
            code = weekly.scheduled(self.IN_WINDOW, connect=connect, build_client=lambda: client, run=run, out=self.lines.append,
                                    sleep=self.job_slept.append, clock=lambda: self.IN_WINDOW)
        return code, client

    def composing_rows(self):
        return [(r[0], r[1]) for r in self.conn.execute("SELECT phase, stop_reason FROM spend_ledger WHERE ts >= %s AND phase LIKE 'composing%%' AND phase NOT LIKE '%%faithfulness' ORDER BY ts, id", (self.began,)).fetchall()]

    def test_every_injected_failure_is_survived_and_the_week_publishes(self):
        self.fail_framer = 1                                              # a stage failure: the framer dies once, and the job resumes it
        outcomes = [status_error(529),                                    # overloaded: backoff, tried again
                    lambda: reply("", "refusal", rid="req_refused"),      # refused: asked again
                    lambda: reply("{", "max_tokens", out=40000),          # truncated: asked again with the cap doubled
                    lambda: reply(json.dumps(EMPTY_W46), out=120),        # the 2026-W46 reply: empty but valid JSON: asked again
                    self.good]
        code, client = self.week(outcomes)
        text = "\n".join(self.lines)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.job_slept, [300])                           # the job's one resume, five minutes after the framer failed
        self.assertEqual(self.backoffs, [5])                              # the one overload backoff
        self.assertEqual([r["max_tokens"] for r in client.composing_requests], [composer.COMPOSE_MAX_TOKENS] * 3 + [composer.COMPOSE_MAX_TOKENS * 2] * 2)      # the empty-reply retry keeps the doubled cap
        self.assertEqual(client.outcomes, [])                             # every injected outcome was used, in order
        self.assertEqual(self.composing_rows(), [("composing", "error 529"), ("composing-retry", "refusal"), ("composing-retry", "max_tokens"),
                                                 ("composing-retry", "end_turn"), ("composing-retry", "end_turn")])
        for line in (f"stage {WEEK} framer attempt 1 started", f"stage {WEEK} framer attempt 1 failed after", f"scheduled: {WEEK} will resume once in 5 minutes",
                     f"scheduled: {WEEK} resuming the same run", f"stage {WEEK} framer attempt 2 done in", f"stage {WEEK} gates attempt 1 done in",
                     f"stage {WEEK} publish attempt 1 done in", f"scheduled: {WEEK} published"):
            self.assertIn(line, text)
        self.assertTrue(all("peak RSS" in l for l in self.lines if l.startswith("stage ")))
        _id, body, _at = briefs.get(self.conn, WEEK)
        self.assertGreaterEqual(len(body["items"]), 2)

    def test_a_week_that_keeps_failing_ends_with_the_failure_line_after_one_resume(self):
        self.fail_framer = 9
        code, _client = self.week([])
        self.assertEqual((code, self.job_slept, len(self.framer_calls)), (1, [300], 2))
        last = self.lines[-1]
        for part in (f"scheduled: {WEEK} FAILED at stage framer: RuntimeError", f"--week {WEEK}", "Nobody was emailed"):
            self.assertIn(part, last)
        self.assertEqual(sum(1 for l in self.lines if "resuming the same run" in l), 1)

    def test_two_empty_composer_replies_fail_the_stage_with_the_reply_on_the_stage_line_and_the_resume_publishes(self):
        empty = lambda: reply(json.dumps(EMPTY_W46), out=120)         # noqa: E731
        outcomes = [empty, empty, self.good]
        code, client = self.week(outcomes)
        text = "\n".join(self.lines)
        self.assertEqual(code, 0, text)
        failed = [l for l in self.lines if "framer attempt 1 failed" in l][0]
        self.assertIn("reason=malformed_reply; stop_reason=end_turn", failed)
        self.assertIn('{"thread": {"lines": []', failed)                # the first of the reply, on Railway's log
        self.assertEqual(self.job_slept, [300])
        self.assertEqual([r[0] for r in self.composing_rows()], ["composing", "composing-retry", "composing"])     # the resume's first call is a fresh composing call


if __name__ == "__main__":
    unittest.main()
