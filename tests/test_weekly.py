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

from agents import composer, briefs, budget, excerpts, framer, items, reader, runs, weekly  # noqa: E402
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


REAL_EDITION_FAILURES = composer.edition_failures


def canned_composed(wide_day):
    ids = list(composer.story_ids(wide_day))
    return {
        "thread": {"lines": ["Line one.", "Line two."], "body": "A thread body."},
        "learning": {"headline": "An essay headline", "paras": ["On October 1, 2026, Acme did 1 thing."], "notes": [],
                     "drawnFrom": ids[:2], "doDifferently": "Do it differently."},
        "storiesTitle": "The stories",
        "stories": [{"id": sid, "headline": f"Story {sid}", "hashtags": ["#Agents"], "body": f"Body of {sid}.", "featureParas": None, "tryIt": None, "tie": None}
                    for sid in ids],
        "dividers": [], "closing": {"lines": ["One.", "Two."], "summary": "A summary.", "oneThing": "Try one thing."}, "splits": [],
    }


def clean_verdict():
    claim = {"claim": "A factual claim.", "supported": True, "supporting_span": "a span", "span_verified": True}
    return {"confidence": 0.95, "claims": [claim], "unsupported_claims": [], "grader_errors": [],
            "possible_false_alarms": [], "lifted_claims": []}


class Pipeline:
    """Mixin: the Framer and its grader replaced, the feeds replaced, the model client faked."""

    def start(self):
        self.client = FakeClient(unique=True, text=PAGE)
        self.framer_calls, self.check_calls, self.compose_calls = [], [], []
        self.fail_framer = 0
        self.edition_problems = []

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

        def compose(client, system_prompt, profile, wide_day, framing, label, published=None, *, conn=None):
            self.compose_calls.append((wide_day, label, published))
            composed = canned_composed(wide_day)
            return composed, {"first_draft": composed, "problems": {}, "replaced": []}, 0.2

        def check_composed(client, composed, wide_day, published, *, user_id, conn=None):
            payload = composer.grading_units(composed, wide_day, published)
            return {u["id"]: clean_verdict() for u in payload["items"]}, clean_verdict(), SimpleUsage(), payload

        self.patches = [
            unittest.mock.patch.object(composer, "compose_with_revision", compose),
            unittest.mock.patch.object(composer, "check_faithfulness", check_composed),
            # The composer's own gates are proved in tests/test_composer.py; here only the wiring is under test.
            unittest.mock.patch.object(composer, "edition_failures",
                                       lambda *a, **k: list(self.edition_problems) if self.edition_problems is not None else REAL_EDITION_FAILURES(*a, **k)),
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

    def test_w1b_the_composed_edition_is_stored_with_the_brief_and_was_shown_the_fuller_excerpts(self):
        self.run_week()
        _id, body, _at = briefs.get(self.conn, WEEK)
        edition = body["edition"]
        self.assertEqual([s["itemId"] for s in edition["stories"]], [i["id"] for i in body["items"]])
        self.assertEqual([s["id"] for s in edition["stories"]], [f"s{n:02d}" for n in range(1, len(body["items"]) + 1)])
        for story in edition["stories"]:
            self.assertEqual(sum(s["primary"] for s in story["sources"]), 1)
        self.assertEqual(len(edition["learning"]["numbers"]), 3)
        self.assertIn("units", edition["faithfulness"])
        wide_day, label, _published = self.compose_calls[0]
        self.assertTrue(label)                                                           # the Reader's window label reached the composer
        short = self.framer_calls[0]["items"][0]["source_excerpt"]
        self.assertGreaterEqual(len(wide_day["items"][0]["source_excerpt"]), len(short))   # never less than the first pass saw
        self.assertEqual(self.framer_calls[0]["items"][0]["source_excerpt"], short)         # the first pass is untouched

    def test_w1c_a_composer_failure_blocks_publishing(self):
        self.edition_problems = ["style — thread line 1 sentence 1: rule 5: a stage direction"]
        with self.assertRaises(weekly.GateFailed):
            self.run_week()
        self.assertIsNone(briefs.get(self.conn, WEEK))
        self.assertEqual([r[2] for r in self.stages()], ["done", "done", "failed"])

    def test_w1d_the_real_composer_gates_run_in_the_gates_stage(self):
        self.edition_problems = None                      # no stand-in: composer.edition_failures runs on the canned prose, which has no splits
        with self.assertRaises(weekly.GateFailed) as failed:
            self.run_week()
        self.assertIn("no split", str(failed.exception))
        self.assertIsNone(briefs.get(self.conn, WEEK))

    def test_w1e_a_failed_composing_pass_keeps_the_paid_first_pass_and_blocks_the_edition(self):
        def boom(*a, **k):
            raise RuntimeError("Response hit max_tokens and the JSON is truncated.")
        with unittest.mock.patch.object(composer, "compose_with_revision", boom):
            with self.assertRaises(weekly.GateFailed) as failed:
                self.run_week()
        self.assertIn("composing pass produced nothing", str(failed.exception))
        self.assertIn("max_tokens", str(failed.exception))
        self.assertEqual(len(self.framer_calls), 1)                                   # the first pass ran once and its artifact exists
        self.assertEqual([r[2] for r in self.stages()], ["done", "done", "failed"])
        self.assertIsNone(briefs.get(self.conn, WEEK))

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

    def test_w17_stages_are_stamped_when_they_run_and_the_edition_is_dated_when_it_was_made(self):
        ticks = iter(NOW + timedelta(minutes=m) for m in range(1, 200))
        self.run_week(clock=lambda: next(ticks))
        rows = self.conn.execute(
            "SELECT stage, started_at, finished_at FROM run_stages WHERE run_id = %s "
            "ORDER BY array_position(ARRAY['reader', 'framer', 'gates', 'publish'], stage)", (WEEK,)).fetchall()
        for (_, _, done_a), (_, start_b, _) in zip(rows, rows[1:]):
            self.assertLessEqual(done_a, start_b)                # each stage starts after the one before ended
        self.assertTrue(all(started < finished for _, started, finished in rows))
        _id, body, published = briefs.get(self.conn, WEEK)
        self.assertGreaterEqual(published, rows[-1][1])         # published during the publish stage, not at process start
        self.assertEqual(self.framer_calls[0]["date"], NOW.date().isoformat())   # not the key's Monday (1999-03-08)

    def test_w15_status_shows_each_attempt(self):
        self.assertEqual(weekly.status(self.conn, WEEK), [f"{WEEK}: no run yet"])
        self.fail_framer = 1
        with self.assertRaises(RuntimeError):
            self.run_week()
        lines = weekly.status(self.conn, WEEK)
        self.assertEqual(lines[0], f"{WEEK}: run running")
        self.assertRegex(lines[1], r"reader\s+attempt 1\s+done")
        self.assertRegex(lines[2], r"framer\s+attempt 1\s+failed.*model returned no text")

    def fail_gates_once(self):
        bad = clean_verdict()
        bad["unsupported_claims"] = ["Revenue doubled in the quarter."]

        def check(client, raw_day, framing, *, user_id, phase):
            return {i["id"]: bad for i in raw_day["items"]}, clean_verdict(), SimpleUsage()
        with unittest.mock.patch.object(framer, "check_faithfulness", check):
            with self.assertRaises(weekly.GateFailed):
                self.run_week()

    def test_w18_a_failed_week_is_reframed_as_a_new_attempt_that_reuses_the_reader(self):
        self.fail_gates_once()
        fetched, framed = len(self.client.requests), len(self.framer_calls)
        second = f"{WEEK}-r2"
        self.assertEqual(weekly.resolve_run(self.conn, WEEK, new_attempt=True), second)
        out = weekly.run_week(self.conn, self.client, NOW, second, reuse_reader=WEEK)
        self.assertEqual(len(self.client.requests), fetched)                         # nothing fetched again
        self.assertEqual(out["reader"]["reused_from"], WEEK)
        self.assertEqual(len(self.framer_calls), framed + 1)                         # the Framer ran again
        self.assertEqual(self.framer_calls[0]["items"], self.framer_calls[-1]["items"])   # on the same evidence
        brief_id, body, _ = briefs.get(self.conn, WEEK)                               # stored under the bare week...
        self.assertEqual(brief_id, WEEK)
        self.assertEqual(self.conn.execute("SELECT run_id FROM briefs WHERE id = %s", (WEEK,)).fetchone()[0], second)   # ...made by attempt 2
        self.assertEqual(self.conn.execute("SELECT status FROM runs WHERE id = %s", (WEEK,)).fetchone()[0], "running")   # attempt 1 stays as evidence
        self.assertEqual(self.conn.execute("SELECT count(*) FROM excerpt_citations WHERE brief_id = %s", (WEEK,)).fetchone()[0], len(body["items"]))
        lines = weekly.status(self.conn, second)                                      # asking about either attempt shows both
        self.assertEqual(lines, weekly.status(self.conn, WEEK))
        self.assertIn(f"{WEEK}: run running (superseded by {second})", lines)
        self.assertIn(f"{second}: run done (published the edition)", lines)
        self.assertTrue(any("gates" in l and "failed" in l for l in lines))
        self.assertTrue(any("reader" in l and f"reused from {WEEK}" in l for l in lines))

    def test_w19_a_week_publishes_one_edition_and_a_second_attempt_is_refused_before_it_spends(self):
        self.run_week()
        framed, checked, fetched = len(self.framer_calls), len(self.check_calls), len(self.client.requests)
        second = f"{WEEK}-r2"
        with self.assertRaises(weekly.AlreadyPublished):
            weekly.run_week(self.conn, self.client, NOW, second)
        self.assertEqual((len(self.framer_calls), len(self.check_calls), len(self.client.requests)), (framed, checked, fetched))
        self.assertEqual(runs.attempts(self.conn, WEEK), [WEEK])                      # not even a run row was opened
        # ...and if it got that far, the database refuses: one edition per week, made by that week's own attempt.
        day = briefs.get(self.conn, WEEK)[1]
        runs.open_run(self.conn, second, NOW)
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute("INSERT INTO briefs (id, run_id, body, published_at) VALUES (%s, %s, %s, %s)",
                                  (WEEK, second, '{}', NOW))
        runs.open_run(self.conn, "1999-W11", NOW)
        with self.assertRaises(Exception) as wrong_week:
            with self.conn.transaction():
                self.conn.execute("INSERT INTO briefs (id, run_id, body, published_at) VALUES (%s, %s, %s, %s)",
                                  ("1999-W12", "1999-W11", '{}', NOW))
        self.assertIn("briefs_run_is_this_week", str(wrong_week.exception))
        self.assertIsNotNone(day)

    def test_w20_the_edition_is_labelled_by_the_window_its_reader_read(self):
        self.run_week()
        window = runs.stage_output(self.conn, WEEK, "reader")["window"]
        self.assertEqual(window["label"], "Oct 10 \u2013 Oct 18, 2026")            # NOW minus the Reader's eight days
        body = briefs.get(self.conn, WEEK)[1]
        self.assertEqual(body["window"], window)
        from agents import digest
        self.assertEqual(digest.view(WEEK, body)["label"], "Oct 10 \u2013 Oct 18, 2026")

    def test_w21_a_bare_week_resumes_and_a_new_attempt_takes_the_next_number(self):
        self.assertEqual(weekly.resolve_run(self.conn, WEEK), WEEK)
        self.assertEqual(weekly.resolve_run(self.conn, WEEK, new_attempt=True), WEEK)    # nothing to supersede yet
        self.run_week(stop_after="reader")
        self.assertEqual(weekly.resolve_run(self.conn, WEEK), WEEK)
        self.assertEqual(weekly.resolve_run(self.conn, WEEK, new_attempt=True), f"{WEEK}-r2")
        runs.open_run(self.conn, f"{WEEK}-r2", NOW)
        self.assertEqual(weekly.resolve_run(self.conn, WEEK), f"{WEEK}-r2")             # resume the latest
        self.assertEqual(weekly.resolve_run(self.conn, WEEK, new_attempt=True), f"{WEEK}-r3")
        self.assertEqual(weekly.resolve_run(self.conn, WEEK), f"{WEEK}-r2")
        self.assertEqual(weekly.resolve_run(self.conn, WEEK), f"{WEEK}-r2")
        self.assertEqual(weekly.resolve_run(self.conn, f"{WEEK}-r2"), f"{WEEK}-r2")     # an attempt named outright
        with self.assertRaises(runs.RunRefused):
            weekly.resolve_run(self.conn, "not-a-week")

    def test_w22_reuse_reader_is_only_for_a_fresh_attempt(self):
        self.run_week(stop_after="reader")
        with self.assertRaises(runs.RunRefused):                                      # this attempt's Reader is already done
            weekly.run_week(self.conn, self.client, NOW, WEEK, reuse_reader=WEEK)
        with self.assertRaises(runs.RunRefused):                                      # nothing finished to reuse
            weekly.run_week(self.conn, self.client, NOW, f"{WEEK}-r2", reuse_reader="1999-W10-r9")
        self.assertEqual(runs.attempts(self.conn, WEEK), [WEEK])                      # the refused attempt left no empty run behind

    def test_w23_a_named_attempt_must_exist_or_be_exactly_next(self):
        runs.open_run(self.conn, WEEK, NOW)
        with self.assertRaisesRegex(runs.RunRefused, "gap"):
            weekly.resolve_run(self.conn, f"{WEEK}-r5")
        self.assertEqual(weekly.resolve_run(self.conn, f"{WEEK}-r2"), f"{WEEK}-r2")       # exactly the next

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
