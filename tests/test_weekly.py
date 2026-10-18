"""The weekly run: stages, resume, gates and publish, with the model calls replaced.

    python -m unittest tests.test_weekly -v

The Framer and its grader are the shipped ones (agents/framer.py); here they return canned
output so the plumbing around them can be proved without a paid call. Their real behaviour
is covered by the eval harness (data/evals/run_evals.py) and by the first real run.
Database tests run in rolled-back transactions and skip when the database is unreachable
(start the tunnel first), or fail with REQUIRE_DB=1.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import briefs, budget, excerpts, framer, items, reader, runs, weekly  # noqa: E402
from tests._db import needs_db  # noqa: E402
from tests.test_reader import ATOM, ATOM_FEED, HF, HF_FEED, HN, HN_FEED, RSS, RSS_FEED, FakeClient  # noqa: E402

# Earlier on 2026-10-18 than the first real run, so that run's items (all discovered later) are outside this window.
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=timezone.utc)
WEEK = "1999-W10"
PAGE = """---
canonical: https://openai.com/index/mcp-stateless/
meta-og:description: A short description.
---

[Skip to content](#start)

- [Home](/)
- [News](/news)

# MCP servers get stateless transport

![diagram](https://openai.com/x.png)

The 2026-07-28 MCP specification changes the core transport from stateful sessions to stateless request and response. Servers can now scale horizontally behind an ordinary load balancer.

Existing clients keep working for twelve months, after which the old handshake is removed from the specification entirely.

[Subscribe](https://openai.com/subscribe) [Share](https://openai.com/share)
"""


class GoldenDayIsComplete(unittest.TestCase):
    """The completeness gate must pass the day the whole system was calibrated on."""

    def test_the_golden_framing_has_no_completeness_failures(self):
        import json
        from agents import gates
        golden = REPO / "data" / "evals" / "golden" / "2026-08-04"
        day = json.loads((golden / "output.json").read_text())
        raw = json.loads((golden / "input.json").read_text())
        graded = {"claims": [{"claim": "c"}]}      # the frozen day predates the claim ledger; the framing is what is checked
        verdicts = {i["id"]: graded for i in day["items"]}
        self.assertEqual(gates.completeness_failures(raw, gates.framing_from_day(day), verdicts, graded), [])

    def test_a_story_with_only_an_example_and_a_summary_counts_as_written(self):
        from agents import gates
        raw = {"items": [{"id": "a"}]}
        framing = {"items": [{"id": "a", "why": "", "example": "Try this.", "ninety": {"method": "m"}}]}
        verdict = {"claims": [{"claim": "c"}]}
        self.assertEqual(gates.completeness_failures(raw, framing, {"a": verdict}, verdict), [])
        framing["items"][0].update(example="", ninety={}, connection="Linked to something else.")
        self.assertEqual(len(gates.completeness_failures(raw, framing, {"a": verdict}, verdict)), 1)   # a connection alone is not a story
        framing["items"][0].update(example="Try this.", ninety={})
        self.assertEqual(len(gates.completeness_failures(raw, framing, {"a": verdict}, verdict)), 1)   # nor an example alone


class Lead(unittest.TestCase):
    def test_keeps_running_prose_and_drops_the_page_furniture(self):
        got = weekly.lead(PAGE)
        self.assertTrue(got.startswith("The 2026-07-28 MCP specification"))
        self.assertIn("twelve months", got)
        for junk in ("Skip to content", "meta-og", "Subscribe", "diagram", "News", "http", "]("):
            self.assertNotIn(junk, got)

    def test_bounded_and_cut_at_a_sentence(self):
        long = "\n\n".join(f"Sentence number {i} says something quite specific about agents and tools in production." for i in range(80))
        got = weekly.lead(long, max_chars=500)
        self.assertLessEqual(len(got), 500)
        self.assertTrue(got.endswith("."))
        self.assertLessEqual(got.count("Sentence number"), weekly.LEAD_MAX_SENTENCES)   # a few sentences, not a page

    def test_falls_back_to_the_description_and_then_to_nothing(self):
        only_menus = "---\nmeta-og:description: The page's own summary of what it covers, in a sentence.\n---\n\n- [a](b)\n- [c](d)\n"
        self.assertEqual(weekly.lead(only_menus), "The page's own summary of what it covers, in a sentence.")
        self.assertEqual(weekly.lead("---\ncanonical: x\n---\n\n- [a](b)\n"), "")

    def test_an_arxiv_page_is_read_from_its_abstract_not_its_author_list(self):
        page = ("---\ncanonical: https://arxiv.org/abs/2610.20939\nmeta-citation_abstract: We introduce a platform that unifies "
                "agent evaluation and training. It scales to twenty thousand parallel environments and reaches state of the art. "
                "Importantly, training in a mocked app transfers to the real one.\n---\n\nAuthors:Panrong Tong, Quyu Kong, Pengxiang Li, "
                "Tianyu Xia, Hanzhang Zhou, Lei Wang, Chenglin Cai, Long Li, Long Chen, Jianan Zhang arXivLabs is a framework that "
                "allows collaborators to develop and share new arXiv features directly on our website, with partners who share its values.\n")
        got = weekly.lead(page, prefer_meta=True)
        self.assertTrue(got.startswith("We introduce a platform"))
        self.assertNotIn("arXivLabs", got)
        self.assertNotIn("Authors", got)
        self.assertEqual(weekly.lead(page), "We introduce a platform that unifies agent evaluation and training. "
                         "It scales to twenty thousand parallel environments and reaches state of the art. "
                         "Importantly, training in a mocked app transfers to the real one.")   # boilerplate body rejected, abstract is the fallback

    def test_a_headline_may_not_claim_what_its_excerpt_does_not_say(self):
        facts = weekly.headline_facts_missing
        self.assertEqual(facts("Upcoming deprecation of selected GitHub Copilot models in mid-November",
                               "Please update your workflows before this date."), ["November"])
        self.assertEqual(facts("Upcoming deprecation in mid-November", "Models are retired on November 15."), [])
        self.assertEqual(facts("How Third Moment runs 10,000 agents for 36+ hours", "This lab builds agents that focus for days."),
                         ["10,000", "36+"])
        self.assertEqual(facts("OpenAI raises $122B", "OpenAI closed a round of $122B at a high valuation."), [])
        self.assertEqual(facts("GPT-5.6 beats Opus 5 at 89.5%", "Scores: GPT-5.6 reached 89.5% and Opus 5 reached 89.1%."), [])
        self.assertEqual(facts("A headline with no figures at all", "Any text."), [])
        self.assertEqual(facts("Version 10 ships", "It first shipped in 2010 and again in 2015."), ["10"])   # not found inside 2010
        self.assertEqual(facts("Why this may matter to you", "Nothing here."), [])                           # "may" is a verb
        self.assertEqual(facts("Why this may matter in March", "Nothing here."), ["March"])                  # "March" is a date here
        self.assertEqual(facts("Models retire in mid-November", "They retire on November 15."), [])

    def test_boilerplate_paragraphs_are_not_prose(self):
        page = ("---\ncanonical: x\n---\n\nSubscribe to our newsletter to get every post in your inbox every single week, for free, forever.\n\n"
                "The real article begins here and says something specific about how the new release changes deployment for everyone.\n")
        self.assertTrue(weekly.lead(page).startswith("The real article begins"))

    def test_tags_are_deterministic(self):
        core = {"MCP architecture"}
        self.assertEqual(weekly.tags_for("arxiv.org", ["MCP architecture"], core, 2), ["RADAR"])
        self.assertEqual(weekly.tags_for("github.blog", ["MCP architecture"], core, 2), ["DO THIS", "KNOW"])
        self.assertEqual(weekly.tags_for("github.blog", ["MCP architecture"], core, 0), ["KNOW"])   # the cap
        self.assertEqual(weekly.tags_for("github.blog", ["multi-agent systems"], {"multi-agent systems"}, 2), ["KNOW"])   # a generic 'agents' mention
        self.assertEqual(weekly.tags_for("github.blog", ["evals"], core, 2), ["KNOW"])              # not a core topic
        self.assertEqual(weekly.tags_for("openai.com", ["MCP architecture"], core, 2), ["KNOW"])

    def test_week_keys(self):
        self.assertEqual(weekly.week_key(datetime(2026, 10, 18, tzinfo=timezone.utc)), "2026-W42")
        self.assertEqual(weekly.week_start("2026-W42").isoformat(), "2026-10-12")


def canned_framing(raw_day):
    def field(text):
        return text, [{"text": text, "class": "fact"}]
    out_items = []
    for it in raw_day["items"]:
        why, why_split = field(f"{it['headline']} matters to practitioners.")
        out_items.append({"id": it["id"], "why": why, "split": {"why": why_split}})
    thread, thread_split = field("The week's stories share one idea.")
    return {"thread": thread, "threadSplit": thread_split, "items": out_items}


def clean_verdict():
    claim = {"claim": "A factual claim.", "supported": True, "supporting_span": "a span", "span_verified": True}
    return {"confidence": 0.95, "claims": [claim], "unsupported_claims": [], "grader_errors": [],
            "possible_false_alarms": [], "lifted_claims": []}


class Pipeline:
    """Mixin: the Framer and its grader replaced, the feeds replaced, the model client faked."""

    def start(self):
        self.client = FakeClient(text=PAGE)
        self.framer_calls, self.check_calls = [], []
        self.fail_framer = 0

        def frame_day(client, system_prompt, profile, raw_day):
            self.framer_calls.append(raw_day)
            if self.fail_framer:
                self.fail_framer -= 1
                raise RuntimeError("model returned no text")
            return canned_framing(raw_day), None, 0.05

        def check(client, raw_day, framing, *, user_id, phase):
            self.check_calls.append(raw_day)
            verdicts = {i["id"]: clean_verdict() for i in raw_day["items"]}
            return verdicts, clean_verdict(), SimpleUsage()

        self.patches = [
            unittest.mock.patch.object(framer, "frame_day", frame_day),
            unittest.mock.patch.object(framer, "check_faithfulness", check),
            unittest.mock.patch.object(reader, "FEEDS", [RSS_FEED, ATOM_FEED, HF_FEED, HN_FEED]),
            unittest.mock.patch.object(reader, "http_get", lambda url: {RSS_FEED.url: RSS, ATOM_FEED.url: ATOM,
                                                                       HF_FEED.url: HF, HN_FEED.url: HN}[url]),
        ]
        for p in self.patches:
            p.start()

    def stop(self):
        for p in self.patches:
            p.stop()


class SimpleUsage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 1000, 200, 0, 0


@needs_db
class WeeklyRun(Pipeline, unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.counts()
        self.start()

    def tearDown(self):
        self.stop()
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        conn = conn or self.conn
        return conn.execute("SELECT (SELECT count(*) FROM runs), (SELECT count(*) FROM run_stages), "
                            "(SELECT count(*) FROM run_artifacts), (SELECT count(*) FROM briefs), (SELECT count(*) FROM items), "
                            "(SELECT count(*) FROM excerpts), (SELECT count(*) FROM excerpt_citations)").fetchone()

    def run_week(self, **kw):
        return weekly.run_week(self.conn, self.client, NOW, WEEK, **kw)

    def stages(self):
        return self.conn.execute("SELECT stage, attempt, status FROM run_stages WHERE run_id = %s ORDER BY array_position(ARRAY['reader', 'framer', 'gates', 'publish'], stage), attempt",
                                 (WEEK,)).fetchall()

    def test_w1_a_whole_week_publishes_and_pins_its_evidence(self):
        out = self.run_week()
        self.assertEqual(list(out), ["reader", "framer", "gates", "publish"])
        self.assertEqual([r[2] for r in self.stages()], ["done"] * 4)
        self.assertEqual(self.conn.execute("SELECT status FROM runs WHERE id = %s", (WEEK,)).fetchone()[0], "done")
        _id, body, _at = briefs.get(self.conn, WEEK)
        self.assertEqual(body["counts"]["items"], len(body["items"]))
        self.assertGreaterEqual(len(body["items"]), 2)
        # Everything the brief cites is evidence now: pinned, with a citation row, kept forever.
        pinned = self.conn.execute("SELECT count(*) FROM excerpt_citations WHERE brief_id = %s", (WEEK,)).fetchone()[0]
        self.assertEqual(pinned, len(body["items"]))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM excerpts e JOIN excerpt_citations c ON c.excerpt_id = e.id WHERE c.brief_id = %s "
            "AND e.expires_at IS NOT NULL", (WEEK,)).fetchone()[0], 0)
        # The Framer was shown leads, not pages.
        shown = self.framer_calls[0]["items"][0]
        self.assertNotIn("meta-og", shown["source_excerpt"])
        self.assertLessEqual(len(shown["source_excerpt"]), weekly.LEAD_MAX_CHARS)
        self.assertEqual(set(shown), {"id", "category", "headline", "tags", "source_excerpt", "sources"})   # the golden shape
        # Run outputs hold pointers; the writing is in artifacts.
        self.assertEqual(runs.stage_output(self.conn, WEEK, "framer")["artifact"], "framer")

    def test_w2_a_failed_framer_resumes_without_refetching_or_repaying_the_reader(self):
        self.fail_framer = 1
        with self.assertRaises(RuntimeError):
            self.run_week()
        fetches = len(self.client.requests)
        self.assertGreater(fetches, 0)
        self.assertEqual(self.stages(), [("reader", 1, "done"), ("framer", 1, "failed")])
        self.assertEqual(runs.next_stage(self.conn, WEEK), "framer")
        self.run_week()
        self.assertEqual(len(self.client.requests), fetches)                      # no new fetch
        self.assertEqual(len(self.framer_calls), 2)                               # the Framer ran again
        self.assertEqual(self.stages(), [("reader", 1, "done"), ("framer", 1, "failed"), ("framer", 2, "done"),
                                         ("gates", 1, "done"), ("publish", 1, "done")])
        self.assertEqual(self.framer_calls[0]["items"], self.framer_calls[1]["items"])   # the same evidence both times

    def test_w3_stop_after_a_stage_then_resume(self):
        self.run_week(stop_after="reader")
        self.assertEqual(runs.next_stage(self.conn, WEEK), "framer")
        asked = len(self.client.requests)
        self.run_week()
        self.assertEqual(len(self.client.requests), asked)
        self.assertIsNone(runs.next_stage(self.conn, WEEK))

    def test_w4_a_failed_hard_gate_publishes_nothing(self):
        bad = clean_verdict()
        bad["unsupported_claims"] = ["Revenue doubled in the quarter."]

        def check(client, raw_day, framing, *, user_id, phase):
            verdicts = {i["id"]: (bad if n == 0 else clean_verdict()) for n, i in enumerate(raw_day["items"])}
            return verdicts, clean_verdict(), SimpleUsage()
        with unittest.mock.patch.object(framer, "check_faithfulness", check):
            with self.assertRaises(weekly.GateFailed) as failed:
                self.run_week()
        self.assertIn("unsupported", str(failed.exception))
        self.assertIsNone(briefs.get(self.conn, WEEK))
        self.assertEqual(self.stages()[-1], ("gates", 1, "failed"))
        art = self.conn.execute("SELECT body FROM run_artifacts WHERE run_id = %s AND stage = 'gates'", (WEEK,)).fetchone()[0]
        self.assertEqual(len(art["failures"]), 1)                                  # the failure is kept for the debrief
        self.assertEqual(self.conn.execute("SELECT count(*) FROM excerpt_citations WHERE brief_id = %s", (WEEK,)).fetchone()[0], 0)

    def test_w5_a_structural_failure_also_gates(self):
        def frame_day(client, system_prompt, profile, raw_day):
            f = canned_framing(raw_day)
            f["items"][0]["why"] += " (edited after the split)"                    # the split no longer reconstructs the field
            return f, None, 0.05
        with unittest.mock.patch.object(framer, "frame_day", frame_day):
            with self.assertRaises(weekly.GateFailed) as failed:
                self.run_week()
        self.assertIn("coverage", str(failed.exception))
        self.assertIsNone(briefs.get(self.conn, WEEK))

    def test_w10_a_story_the_grader_skipped_is_not_published_as_clean(self):
        def check(client, raw_day, framing, *, user_id, phase):
            verdicts = {i["id"]: clean_verdict() for i in raw_day["items"][1:]}      # the first story was never graded
            return verdicts, clean_verdict(), SimpleUsage()
        with unittest.mock.patch.object(framer, "check_faithfulness", check):
            with self.assertRaises(weekly.GateFailed) as failed:
                self.run_week()
        self.assertIn("no verdict", str(failed.exception))
        self.assertIsNone(briefs.get(self.conn, WEEK))

    def test_w11_a_grader_that_checked_nothing_is_not_a_clean_bill(self):
        empty = clean_verdict()
        empty["claims"] = []

        def check(client, raw_day, framing, *, user_id, phase):
            return {i["id"]: empty for i in raw_day["items"]}, empty, SimpleUsage()
        with unittest.mock.patch.object(framer, "check_faithfulness", check):
            with self.assertRaises(weekly.GateFailed) as failed:
                self.run_week()
        self.assertIn("nothing was checked", str(failed.exception))

    def test_w12_a_story_the_framer_left_out_is_not_published(self):
        def frame_day(client, system_prompt, profile, raw_day):
            f = canned_framing(raw_day)
            f["items"] = f["items"][1:]
            return f, None, 0.05
        with unittest.mock.patch.object(framer, "frame_day", frame_day):
            with self.assertRaises(weekly.GateFailed) as failed:
                self.run_week()
        self.assertIn("wrote nothing", str(failed.exception))

    def test_w13_publish_survives_dying_after_the_brief_was_written(self):
        self.run_week(stop_after="gates")
        day = runs.load_artifact(self.conn, WEEK, "gates")["day"]
        briefs.publish(self.conn, WEEK, WEEK, day, NOW)          # the previous attempt got this far, then died
        self.run_week()                                          # the rerun completes instead of colliding
        self.assertIsNone(runs.next_stage(self.conn, WEEK))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM excerpt_citations WHERE brief_id = %s", (WEEK,)).fetchone()[0],
                         len(day["items"]))

    def test_w14_a_different_brief_already_live_is_never_overwritten(self):
        self.run_week(stop_after="gates")
        briefs.publish(self.conn, WEEK, WEEK, {"items": [], "other": True}, NOW)
        with self.assertRaises(RuntimeError):
            self.run_week()

    def test_w16_a_story_whose_headline_outruns_its_excerpt_is_not_framed(self):
        reader.prepare(self.conn)
        text = "The release notes explain how the new default works for every customer on the platform today and tomorrow."
        ids = []
        for n, title in enumerate(("Grounded story with no figures", "Deprecation of models in mid-November")):
            url = f"https://openai.com/index/ground-{n}"
            iid, _ = items.upsert(self.conn, url, title, NOW, relevance=(5, ["x"]))
            items.attach_excerpt(self.conn, iid, excerpts.store(self.conn, url, text, NOW))
            ids.append(str(iid))
        raw = weekly.build_raw_day(self.conn, WEEK, ids, framer.load_json(framer.PROFILE_PATH))
        self.assertEqual([i["headline"] for i in raw["items"]], ["Grounded story with no figures"])

    def test_w15_status_shows_each_attempt(self):
        self.assertEqual(weekly.status(self.conn, WEEK), [f"{WEEK}: no run yet"])
        self.fail_framer = 1
        with self.assertRaises(RuntimeError):
            self.run_week()
        lines = weekly.status(self.conn, WEEK)
        self.assertEqual(lines[0], f"{WEEK}: run running")
        self.assertRegex(lines[1], r"reader\s+attempt 1\s+done")
        self.assertRegex(lines[2], r"framer\s+attempt 1\s+failed.*model returned no text")

    def test_w6_a_finished_week_does_nothing_when_run_again(self):
        self.run_week()
        asked, framed, checked = len(self.client.requests), len(self.framer_calls), len(self.check_calls)
        again = self.run_week()
        self.assertEqual((len(self.client.requests), len(self.framer_calls), len(self.check_calls)), (asked, framed, checked))
        self.assertEqual(again["publish"]["brief"], WEEK)

    def test_w7_an_empty_week_fails_before_spending_on_the_framer(self):
        with unittest.mock.patch.object(reader, "http_get", lambda url: b"<rss><channel></channel></rss>"):
            with self.assertRaises(RuntimeError) as failed:
                self.run_week()
        self.assertIn("nothing to frame", str(failed.exception))
        self.assertEqual(self.framer_calls, [])

    def test_w8_only_surfaceable_fetched_items_reach_the_framer(self):
        self.run_week(stop_after="reader")
        domains = {i["sources"][0]["url"].split("/")[2] for i in weekly.build_raw_day(
            self.conn, WEEK, runs.stage_output(self.conn, WEEK, "reader")["items"], framer.load_json(framer.PROFILE_PATH))["items"]}
        self.assertNotIn("small-blog.example", domains)                            # the Hacker News link: discovery only
        self.assertTrue(domains <= {"openai.com", "arxiv.org"})

    def test_w9_artifacts_are_append_only_and_tied_to_an_attempt(self):
        self.run_week(stop_after="framer")
        with self.assertRaises(Exception) as refused:
            with self.conn.transaction():
                self.conn.execute("UPDATE run_artifacts SET body = '{}' WHERE run_id = %s", (WEEK,))
        self.assertIn("append-only", str(refused.exception))
        with self.assertRaises(Exception):
            with self.conn.transaction():   # an artifact for an attempt that does not exist
                self.conn.execute("INSERT INTO run_artifacts (run_id, stage, attempt, body, created_at) "
                                  "VALUES (%s, 'framer', 9, '{}', %s)", (WEEK, NOW))
        self.assertEqual(items.ItemRefused.__name__, "ItemRefused")
        self.assertEqual(excerpts.RETENTION_DAYS, 56)
        self.assertEqual(timedelta(hours=2), runs.STALE_AFTER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
