"""bk-retry: a unit whose verdict lifted a claim from its source is graded once more, alone.

    python -m unittest tests.test_retry -v

Free: the grader is replaced by a script, so the rule is proved without a paid call. Its effect
on the real grader is measured in data/evals/runs/ (see data/evals/measure_retry.py).
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import framer  # noqa: E402

RAW = {"date": "2026-10-20", "items": [
    {"id": "a", "category": "AI", "headline": "H", "tags": ["KNOW"], "sources": [],
     "source_excerpt": "Anthropic is valued at $965B. OpenAI closed a $122B round."},
    {"id": "b", "category": "AI", "headline": "H2", "tags": ["KNOW"], "sources": [],
     "source_excerpt": "The spec changes the transport to stateless."},
]}
FRAMING = {"thread": "Thread text.", "threadSplit": [{"text": "Thread text.", "class": "fact"}], "items": [
    {"id": "a", "why": "Anthropic raised $965B.", "split": {"why": [{"text": "Anthropic raised $965B.", "class": "fact"}]}},
    {"id": "b", "why": "The spec is stateless.", "split": {"why": [{"text": "The spec is stateless.", "class": "fact"}]}},
]}


def claim(text, supported, span=None):
    return {"claim": text, "supported": supported, "supporting_span": span}


class Usage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_input_tokens = self.cache_creation_input_tokens = 0


class Script:
    """Stands in for llm.call: hands back the next scripted grader answer and records the request."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, client, **kw):
        self.requests.append(kw)
        answer = self.answers.pop(0)
        return json.dumps(answer), Usage(1000, 100), 0.001


def verdict_json(a_claims, b_claims=None, thread_claims=None):
    return {"items": [{"id": "a", "claims": a_claims, "confidence": 0.9}, {"id": "b", "claims": b_claims or [claim("The spec is stateless.", True, "stateless")], "confidence": 0.9}],
            "thread": {"claims": thread_claims or [claim("Thread text.", True, "Anthropic")], "confidence": 0.9}}


LIFTED = claim("Anthropic is valued at $965B", True, "Anthropic is valued at $965B")      # read off the excerpt, not the writing
HONEST = claim("Anthropic raised $965B", False, None)


class RetryIsOffByDefault(unittest.TestCase):
    def test_a_lift_does_not_retry_unless_the_switch_is_on(self):
        self.assertFalse(framer.RETRY_ON_LIFT)                                       # the 2026-10-20 decision
        script = Script(verdict_json([LIFTED]))
        with unittest.mock.patch.object(framer.llm, "call", script):
            verdicts, _, _ = framer.check_faithfulness(object(), RAW, FRAMING, user_id="t", phase="p")
        self.assertEqual(len(script.requests), 1)                                    # one call: no second pass
        self.assertEqual(verdicts["a"]["lifted_claims"], ["Anthropic is valued at $965B"])   # still reported
        self.assertNotIn("retried", verdicts["a"])


class BoundedRetry(unittest.TestCase):
    """With the switch on (it is off by default), a lifted claim retries that unit once."""

    def setUp(self):
        patch = unittest.mock.patch.object(framer, "RETRY_ON_LIFT", True)
        patch.start()
        self.addCleanup(patch.stop)

    def grade(self, *answers):
        script = Script(*answers)
        with unittest.mock.patch.object(framer.llm, "call", script):
            out = framer.check_faithfulness(object(), RAW, FRAMING, user_id="t", phase="eval-faithfulness")
        return script, out

    def test_no_lift_means_exactly_one_call(self):
        script, (verdicts, thread, usage) = self.grade(verdict_json([HONEST]))
        self.assertEqual(len(script.requests), 1)
        self.assertEqual(verdicts["a"]["unsupported_claims"], ["Anthropic raised $965B"])
        self.assertNotIn("retried", verdicts["a"])

    def test_a_lift_on_an_item_retries_that_item_alone_and_the_retry_replaces_the_verdict(self):
        script, (verdicts, thread, usage) = self.grade(verdict_json([LIFTED]), verdict_json([HONEST]))
        self.assertEqual(len(script.requests), 2)
        first, second = (json.loads(r["messages"][0]["content"]) for r in script.requests)
        self.assertNotIn("_retry", first)
        self.assertEqual([i["id"] for i in second["items"]], ["a"])                       # the one unit, not the day
        self.assertIn("RETRY", second["_retry"])
        self.assertIn("Anthropic is valued at $965B", second["_retry"])                    # names what gave it away
        self.assertEqual(second["thread"]["writing_under_test"], "")                       # the thread is not regraded
        self.assertEqual(script.requests[1]["phase"], "eval-faithfulness-retry")           # booked under its own phase
        self.assertEqual(verdicts["a"]["unsupported_claims"], ["Anthropic raised $965B"])  # the retry's verdict
        self.assertEqual(verdicts["a"]["retried"]["lifted_before"], ["Anthropic is valued at $965B"])
        self.assertNotIn("retried", verdicts["b"])                                          # untouched
        self.assertEqual(usage.input_tokens, 2000)                                           # the retry is in the cost

    def test_a_unit_is_retried_once_however_the_retry_turns_out(self):
        script, (verdicts, _, _) = self.grade(verdict_json([LIFTED]), verdict_json([LIFTED]))
        self.assertEqual(len(script.requests), 2)                                           # no third call
        self.assertEqual(verdicts["a"]["lifted_claims"], ["Anthropic is valued at $965B"])
        self.assertIn("retried", verdicts["a"])

    def test_a_lift_on_the_thread_retries_the_thread_against_every_excerpt(self):
        lifted_thread = claim("Anthropic is valued at $965B", True, "Anthropic is valued at $965B")
        script, (verdicts, thread, _) = self.grade(verdict_json([HONEST], thread_claims=[lifted_thread]),
                                                   {"items": [], "thread": {"claims": [claim("Thread text.", True, "Anthropic")], "confidence": 0.9}})
        self.assertEqual(len(script.requests), 2)
        second = json.loads(script.requests[1]["messages"][0]["content"])
        self.assertEqual({e["id"] for e in second["thread"]["ground_truth_excerpt_union"]}, {"a", "b"})
        self.assertTrue(all(i["writing_under_test"] == {} for i in second["items"]))      # no item writing regraded
        self.assertEqual(thread["retried"]["lifted_before"], ["Anthropic is valued at $965B"])

    def test_a_retry_that_returns_no_verdict_for_the_unit_keeps_the_first(self):
        script, (verdicts, _, _) = self.grade(verdict_json([LIFTED]), {"items": [], "thread": {"claims": [], "confidence": 1.0}})
        self.assertEqual(len(script.requests), 2)
        self.assertEqual(verdicts["a"]["lifted_claims"], ["Anthropic is valued at $965B"])
        self.assertIn("first kept", verdicts["a"]["retried"]["result"])

    def test_an_unusable_retry_keeps_the_first_verdict(self):
        class Raw(Script):
            def __call__(self, client, **kw):
                self.requests.append(kw)
                a = self.answers.pop(0)
                return (a if isinstance(a, str) else json.dumps(a)), Usage(1000, 100), 0.001
        for bad in ("not json at all", {"thread": {}}, {"items": [{"id": "a"}], "thread": {"claims": [], "confidence": 1}}):
            script = Raw(verdict_json([LIFTED, HONEST]), bad)
            with unittest.mock.patch.object(framer.llm, "call", script):
                verdicts, thread, usage = framer.check_faithfulness(object(), RAW, FRAMING, user_id="t", phase="p")
            self.assertEqual(len(script.requests), 2, bad)
            self.assertEqual(verdicts["a"]["unsupported_claims"], ["Anthropic raised $965B"], bad)   # the first pass survives
            self.assertIn("first kept", verdicts["a"]["retried"]["result"], bad)
            self.assertEqual(usage.input_tokens, 2000)

    def test_the_retry_is_recorded_in_the_stored_brief(self):
        profile = framer.load_json(framer.PROFILE_PATH)
        script, (verdicts, thread, _) = self.grade(verdict_json([LIFTED]), verdict_json([HONEST]))
        day = framer.assemble_day(profile, RAW, FRAMING, verdicts, thread)
        self.assertEqual(day["items"][0]["faithfulness"]["retried"]["lifted_before"], ["Anthropic is valued at $965B"])
        self.assertNotIn("retried", day["items"][1]["faithfulness"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
