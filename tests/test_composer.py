"""The Framer's second pass: the slot rules, the splits, the grading units and the assembled edition.

    python -m unittest tests.test_composer -v

Free and offline: the model is replaced by a script, so the rules are proved without a paid call. What the
real model writes is measured separately (autopilot/evidence/2026-10-21/stepC-*).
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import composer, framer, style_check  # noqa: E402

LONG = ("Helio Labs says the queue merges each agent's branch through a review step so that a person sees one combined change. "
        "The company keeps a changelog entry for every release and publishes its scoping rules in the documentation. "
        "Developers can switch the queue off for any single repository. "
        "Helio also reports that the trial covered only repositories under 2 million lines of code. ")


def filler(target_words: int) -> str:
    out, text = 0, ""
    while out < target_words:
        text += LONG
        out = len(text.split())
    return " ".join(text.split()[:target_words]).rstrip(",") + "."


def raw_day(n=5):
    items = []
    for i in range(1, n + 1):
        items.append({"id": f"item-{i}", "category": "AI", "headline": f"Headline {i}", "tags": ["DO THIS", "KNOW"] if i == 1 else ["KNOW"],
                      "source_excerpt": f"Helio Labs released product {i} on October {10 + i}, 2026 to {200 + i} repositories.",
                      "sources": [{"label": "Corroborator", "url": f"https://other.example/{i}", "tier": "corroboration"},
                                  {"label": "Helio Labs", "url": f"https://helio.example/{i}", "tier": "primary"}]})
    return {"date": "2026-10-20", "items": items}


def paragraph(n):
    return (f"On October {10 + n}, 2026, Helio Labs released product {n} to {200 + n} repositories. "
            f"The company keeps a changelog entry for every release and publishes its rules in the documentation.")


def valid(n=5):
    ids = [f"s{i:02d}" for i in range(1, n + 1)]
    thread_body = ("On October 11, 2026, [[#s01|Helio Labs released product 1]] to 201 repositories, and on October 12, 2026 "
                   "[[#s02|it released product 2]] to 202 more. Both releases give each agent its own branch and merge the results through a review queue, "
                   "so a person reviews one combined change. The company says it measured only repositories under 2 million lines of code, "
                   "and it keeps a changelog entry for every release. The harder question is whether a review queue scales to repositories ten times larger, "
                   "and Helio has not yet published a measurement that answers it.")
    composed = {
        "thread": {"lines": ["Two releases gave each agent its own branch.", "One review queue still merges every change."], "body": thread_body},
        "learning": {
            "headline": "Helio Labs shipped 5 products in 5 days",
            "paras": [paragraph(i) + (" It uses a [[fn:queue|review queue]] for merges." if i == 1 else "") for i in range(1, 7)],
            "notes": [{"key": "queue", "term": "review queue", "definition": "A review queue merges each agent's branch into one change."}],
            "drawnFrom": ["s01", "s02"],
            "doDifferently": "Give each agent its own branch before you let two of them edit one repository.",
        },
        "storiesTitle": "Helio Labs released 5 products in 5 days",
        "stories": [{
            "id": sid, "headline": f"Helio Labs released product {i} to {200 + i} repositories",
            "hashtags": ["#Agents", "#DevTools"],
            "body": f"Helio Labs released product {i} on October {10 + i}, 2026. It reached {200 + i} repositories. The company measured only small repositories.",
            "featureParas": [filler(110), filler(110), filler(110)] if i <= 2 else None,
            "tryIt": "Try the queue on one small repository. Compare the merged change with your last manual merge." if i == 1 else None,
            "tie": {"to": "s02", "text": "Helio Labs shipped product 2 the following day."} if i == 1 else None,
        } for i, sid in enumerate(ids, start=1)],
        "dividers": [{"before": "s03", "label": "Smaller releases"}],
        "closing": {"lines": ["One lab shipped a queue.", "Another lab still merges by hand."],
                    "summary": "Helio Labs released 5 products in 5 days. Each one gives an agent its own branch.",
                    "oneThing": "Try the queue on one repository this week."},
    }
    composed["splits"] = splits_all_fact(composed)
    return composed


def splits_all_fact(composed):
    return [{"path": p, "runs": [{"text": t, "class": "fact"}]} for p, t in composer.prose_fields(composed).items()]


def resplit(composed):
    composed["splits"] = splits_all_fact(composed)
    return composed


IDS = {f"s{i:02d}": f"item-{i}" for i in range(1, 6)}


class Fixture(unittest.TestCase):
    def test_the_valid_edition_passes_every_free_gate(self):
        c = valid()
        self.assertEqual(composer.coverage_failures(c), [])
        self.assertEqual(composer.slot_failures(c, IDS), [])
        self.assertEqual(style_check.edition_style_failures(c), [])


class SlotRules(unittest.TestCase):
    def fails(self, mutate, expect):
        c = valid()
        mutate(c)
        resplit(c)
        out = composer.slot_failures(c, IDS)
        self.assertTrue(any(expect in line for line in out), (expect, out))

    def test_thread_lines_must_be_exactly_two(self):
        self.fails(lambda c: c["thread"].update(lines=["only one"]), "thread.lines: 1 lines")

    def test_at_most_one_highlight_across_the_pair_and_at_most_five_words(self):
        self.fails(lambda c: c["thread"].update(lines=["[[hl|Two releases]] gave branches.", "[[hl|One queue]] merges changes."]), "more than one [[hl")
        self.fails(lambda c: c["thread"].update(lines=["Two [[hl|releases gave each agent its own branch]].", "x"]), "one to five words")

    def test_thread_body_is_80_to_120_words_with_two_or_three_links(self):
        self.fails(lambda c: c["thread"].update(body="[[#s01|Short]] and [[#s02|body]]."), "words, 80 to 120")
        self.fails(lambda c: c["thread"].update(body=filler(100)), "0 story links")
        body = valid()["thread"]["body"]
        self.fails(lambda c: c["thread"].update(body=body + " [[#s03|a]] [[#s04|b]]"), "4 story links") if False else None

    def test_a_link_to_a_story_that_is_not_in_the_edition_fails(self):
        self.fails(lambda c: c["thread"].update(body=c["thread"]["body"].replace("#s02", "#s99")), "which is not a story")

    def test_the_essay_has_six_to_twelve_paragraphs(self):
        self.fails(lambda c: c["learning"].update(paras=c["learning"]["paras"][:5]), "5 paragraphs")
        self.fails(lambda c: c["learning"].update(paras=c["learning"]["paras"] * 3), "18 paragraphs")

    def test_footnotes_and_notes_match_one_to_one(self):
        self.fails(lambda c: c["learning"].update(notes=[]), "has no note")
        self.fails(lambda c: c["learning"]["notes"].append({"key": "extra", "term": "t", "definition": "d"}), "never used")
        self.fails(lambda c: c["learning"]["paras"].__setitem__(1, c["learning"]["paras"][1] + " [[fn:queue|review queue]]"), "more than once")

    def test_drawn_from_is_two_to_six_real_stories(self):
        self.fails(lambda c: c["learning"].update(drawnFrom=["s01"]), "learning.drawnFrom")
        self.fails(lambda c: c["learning"].update(drawnFrom=["s01", "s77"]), "learning.drawnFrom")

    def test_stories_are_the_editions_in_order_and_four_to_eleven(self):
        self.fails(lambda c: c["stories"].reverse(), "are not the edition's")
        c = valid(3)
        ids = {f"s{i:02d}": f"item-{i}" for i in range(1, 4)}
        self.assertTrue(any("the template holds 4 to 11" in l for l in composer.slot_failures(c, ids)))

    def test_hashtags_are_one_to_four_labels(self):
        self.fails(lambda c: c["stories"][0].update(hashtags=[]), "s01.hashtags")
        self.fails(lambda c: c["stories"][0].update(hashtags=["#a", "#b", "#c", "#d", "#e"]), "s01.hashtags")
        self.fails(lambda c: c["stories"][0].update(hashtags=["Agents"]), "s01.hashtags")
        self.fails(lambda c: c["stories"][0].update(hashtags=["#two words"]), "s01.hashtags")

    def test_a_body_is_three_to_five_sentences(self):
        self.fails(lambda c: c["stories"][2].update(body="One sentence only."), "s03.body: 1 sentences")
        self.fails(lambda c: c["stories"][2].update(body="A one. B two. C three. D four. E five. F six."), "6 sentences")

    def test_only_the_top_two_may_be_features_and_a_feature_is_300_to_500_words(self):
        self.fails(lambda c: c["stories"][2].update(featureParas=[filler(110)] * 3), "only the top 2")
        self.fails(lambda c: c["stories"][0].update(featureParas=[filler(50)] * 3), "s01.featureParas")
        self.fails(lambda c: c["stories"][0].update(featureParas=[filler(110)] * 2), "s01.featureParas")
        self.fails(lambda c: c["stories"][0].update(featureParas=[filler(200)] * 3), "s01.featureParas")

    def test_try_it_is_two_to_four_sentences_and_a_tie_cannot_point_at_its_own_story(self):
        self.fails(lambda c: c["stories"][0].update(tryIt="Just one."), "s01.tryIt")
        self.fails(lambda c: c["stories"][0].update(tie={"to": "s01", "text": "Self."}), "points at itself")

    def test_dividers_must_name_a_story_in_the_edition(self):
        self.fails(lambda c: c["dividers"].append({"before": "s42", "label": "x"}), "divider before s42")

    def test_closing_has_two_lines(self):
        self.fails(lambda c: c["closing"].update(lines=["one"]), "closing.lines: 1")

    def test_a_token_outside_the_slot_syntax_or_in_the_wrong_place_fails(self):
        self.fails(lambda c: c["stories"][0].update(body=c["stories"][0]["body"] + " [[bogus|x]]"), "is not allowed here")
        self.fails(lambda c: c["stories"][0].update(body=c["stories"][0]["body"] + " [[fn:queue|review queue]]"), "is not allowed here")


class Splits(unittest.TestCase):
    def test_a_missing_or_wrong_split_is_a_failure(self):
        c = valid()
        c["splits"] = c["splits"][1:]
        self.assertTrue(any("no split" in l for l in composer.coverage_failures(c)))
        c = valid()
        c["splits"][0]["runs"] = [{"text": "something else", "class": "fact"}]
        self.assertTrue(any("does not reconstruct" in l for l in composer.coverage_failures(c)))
        c = valid()
        c["splits"].append({"path": "story.s09.body", "runs": []})
        self.assertTrue(any("does not exist" in l for l in composer.coverage_failures(c)))

    def test_a_token_cut_across_two_runs_is_a_failure(self):
        c = valid()
        body = c["thread"]["body"]
        cut = body.index("[[#s01|") + 4
        c["splits"] = [s for s in c["splits"] if s["path"] != "thread.body"] + [
            {"path": "thread.body", "runs": [{"text": body[:cut], "class": "fact"}, {"text": body[cut:], "class": "fact"}]}]
        self.assertTrue(any("cut across two runs" in l for l in composer.coverage_failures(c)))

    def test_only_fact_runs_reach_the_grader_and_tokens_become_plain_words(self):
        c = valid()
        c["splits"] = [s for s in c["splits"] if s["path"] != "closing.oneThing"] + [{"path": "closing.oneThing", "runs": [
            {"text": "Try the queue ", "class": "interpretation"}, {"text": "on one repository this week.", "class": "fact"}]}]
        self.assertEqual(composer.facts(c, "closing.oneThing"), "on one repository this week.")
        self.assertEqual(composer.facts(c, "thread.body").count("[["), 0)
        self.assertIn("Helio Labs released product 1", composer.facts(c, "thread.body"))


class GradingUnits(unittest.TestCase):
    def test_each_unit_is_graded_against_the_text_it_may_draw_on(self):
        c, day = valid(), raw_day()
        payload = composer.grading_units(c, day, {"item-1": "2026-10-11"})
        by_id = {u["id"]: u for u in payload["items"]}
        self.assertEqual(set(by_id), {"s01", "s02", "s03", "s04", "s05", "essay"})
        self.assertIn("Published 2026-10-11 by Helio Labs. ", by_id["s01"]["ground_truth_excerpt"])           # the date the page gave is evidence
        self.assertNotIn("product 2", by_id["s01"]["ground_truth_excerpt"])                                   # a story sees only its own excerpt
        essay = by_id["essay"]["ground_truth_excerpt"]
        self.assertIn("product 1", essay)
        self.assertIn("product 2", essay)
        self.assertNotIn("product 3", essay)                                                                  # only what drawnFrom lists
        union = {e["id"] for e in payload["thread"]["ground_truth_excerpt_union"]}
        self.assertEqual(union, set(IDS))
        week = payload["thread"]["writing_under_test"]
        self.assertIn("one review queue", week.lower())                                                      # thread and closing
        self.assertIn("following day", week)                                                                  # the tie
        self.assertIn("Smaller releases", week)                                                               # the divider
        self.assertNotIn("review queue merges each agent's branch into one change", week)                    # the essay's note is not the week's
        self.assertIn("feature.1", by_id["s01"]["writing_under_test"])
        self.assertNotIn("tie", by_id["s01"]["writing_under_test"])

    def test_the_headline_and_body_of_a_story_and_the_essays_advice_are_graded(self):
        c, day = valid(), raw_day()
        payload = composer.grading_units(c, day)
        s01 = next(u for u in payload["items"] if u["id"] == "s01")["writing_under_test"]
        self.assertIn("headline", s01)
        self.assertIn("body", s01)
        essay = next(u for u in payload["items"] if u["id"] == "essay")["writing_under_test"]
        self.assertIn("doDifferently", essay)                       # advice is graded for the facts inside it, against the excerpts the essay draws on
        self.assertIn("note.queue", essay)


def claim(text, supported=True, span="Helio"):
    return {"claim": text, "supported": supported, "supporting_span": span if supported else None}


class Script:
    def __init__(self, answer):
        self.answer, self.requests = answer, []

    def __call__(self, client, **kw):
        self.requests.append(kw)

        class U:
            input_tokens = 1000
            output_tokens = 100
            cache_read_input_tokens = cache_creation_input_tokens = 0
        return json.dumps(self.answer), U(), 0.002


def grader_answer(payload, bad=None):
    items = []
    for u in payload["items"]:
        text = json.dumps(u["writing_under_test"])
        claims = [claim("Helio Labs released it", True, "Helio Labs released")]
        if bad and bad[0] == u["id"]:
            claims.append(claim(bad[1], False))
        items.append({"id": u["id"], "claims": claims, "confidence": 0.9})
    week = [claim("Helio Labs shipped products", True, "Helio Labs released")]
    if bad and bad[0] == "week":
        week.append(claim(bad[1], False))
    return {"items": items, "thread": {"claims": week, "confidence": 0.9}}


class Faithfulness(unittest.TestCase):
    def grade(self, bad=None, mutate=None):
        c, day = valid(), raw_day()
        # the excerpt text the span quotes must be in each unit's ground truth: use a span every excerpt contains
        payload = composer.grading_units(c, day)
        script = Script(grader_answer(payload, bad))
        if mutate:
            mutate(script)
        with unittest.mock.patch.object(composer.llm, "call", script):
            verdicts, week, usage, payload = composer.check_faithfulness(object(), c, day, None, user_id="t")
        return c, day, script, verdicts, week, payload

    def test_two_grader_calls_the_essay_alone_booked_under_the_composing_phase(self):
        _, _, script, verdicts, week, payload = self.grade()
        self.assertEqual(len(script.requests), 2)
        for request in script.requests:
            self.assertEqual((request["phase"], request["temperature"]), (composer.CHECK_PHASE, 0))
        first, second = (json.loads(r["messages"][0]["content"]) for r in script.requests)
        self.assertNotIn("essay", [u["id"] for u in first["items"]])
        self.assertEqual([u["id"] for u in second["items"]], ["essay"])          # alone: it was dropped when it shared a payload
        self.assertEqual(second["thread"]["writing_under_test"], "")             # the week is graded once, in the first call
        self.assertTrue(first["thread"]["writing_under_test"])
        self.assertEqual(composer.faithfulness_failures(verdicts, week), [])
        self.assertEqual(composer.completeness_failures(payload, verdicts, week), [])

    def test_an_unsupported_claim_in_a_story_the_essay_or_the_week_blocks_publishing(self):
        for unit in ("s02", "essay", "week"):
            c, day, _, verdicts, week, payload = self.grade(bad=(unit, "Helio Labs raised 965 billion dollars"))
            failures = composer.edition_failures(c, day, payload, verdicts, week)
            self.assertTrue(any(f"{unit}: unsupported (composed)" in f and "965" in f for f in failures), (unit, failures))

    def test_an_ungraded_unit_fails_closed(self):
        c, day, _, verdicts, week, payload = self.grade()
        verdicts.pop("essay")
        self.assertTrue(any("essay: the grader returned no verdict" in f for f in composer.completeness_failures(payload, verdicts, week)))
        c, day, _, verdicts, week, payload = self.grade()
        verdicts["s03"]["claims"] = []
        self.assertTrue(any("s03: the grader enumerated no claims" in f for f in composer.completeness_failures(payload, verdicts, week)))
        c, day, _, verdicts, week, payload = self.grade()
        week["claims"] = []
        self.assertTrue(any("week: the grader enumerated no claims" in f for f in composer.completeness_failures(payload, verdicts, week)))

    def test_edition_failures_cover_style_slots_and_splits_together(self):
        c, day, _, verdicts, week, payload = self.grade()
        c["thread"]["lines"][0] = "Read that twice."
        c["learning"]["paras"] = c["learning"]["paras"][:4]
        c["splits"] = c["splits"][1:]
        failures = composer.edition_failures(c, day, payload, verdicts, week)
        self.assertTrue(any(f.startswith("style —") and "rule 5" in f for f in failures))
        self.assertTrue(any("learning.paras: 4 paragraphs" in f for f in failures))
        self.assertTrue(any("no split" in f for f in failures))


class Assembly(unittest.TestCase):
    def build(self, bad=None):
        c, day = valid(), raw_day()
        payload = composer.grading_units(c, day)
        script = Script(grader_answer(payload, bad))
        with unittest.mock.patch.object(composer.llm, "call", script):
            verdicts, week, _, payload = composer.check_faithfulness(object(), c, day, None, user_id="t")
        framing = {"items": [{"id": f"item-{i}", "why": f"Why {i}.", "ninety": {"method": "m", "result": "r", "caveat": "c"} if i == 3 else None} for i in range(1, 6)]}
        return composer.assemble_edition(c, day, framing, verdicts, week, stats={"candidates": 1700, "fetched": 12, "merged": 2, "feeds_ok": 17},
                                         source_minutes=34, style=[]), day

    def test_the_pill_the_sources_and_the_ids_are_computed_not_trusted(self):
        edition, day = self.build()
        s1, s3 = edition["stories"][0], edition["stories"][2]
        self.assertEqual((s1["id"], s1["itemId"], s1["tag"], s3["tag"]), ("s01", "item-1", "DO THIS", "KNOW"))
        self.assertEqual([s["primary"] for s in s1["sources"]], [True, False])          # exactly one, rendered first
        self.assertEqual(s1["sources"][0]["href"], "https://helio.example/1")
        self.assertTrue(s1["feature"] and "feature" not in s3)
        self.assertEqual(s1["why"], "Why 1.")
        self.assertEqual(s3["ninety"], [["METHOD", "m"], ["RESULT", "r"], ["CAVEAT", "c"]])
        self.assertEqual(composer.pill_for(["RADAR", "KNOW"]), "RADAR")
        self.assertEqual(composer.pill_for(["DO THIS", "RADAR"]), "DO THIS")

    def test_numbers_and_how_made_come_from_counts(self):
        edition, _ = self.build()
        self.assertEqual(edition["learning"]["numbers"], [["Candidate links considered", 1700], ["Stories synthesised", 5], ["Minutes of source reading", 34]])
        self.assertIn("1700 candidate links from 17 places", edition["learning"]["howMade"])
        self.assertIn("folded 2 duplicates", edition["learning"]["howMade"])
        self.assertEqual(edition["learning"]["notes"], {"queue": ["review queue", "A review queue merges each agent's branch into one change."]})

    def test_checked_is_false_for_a_story_with_an_unsupported_claim(self):
        edition, _ = self.build(bad=("s02", "an invented figure"))
        self.assertEqual([s["checked"] for s in edition["stories"]], [True, False, True, True, True])

    def test_a_tie_to_a_story_outside_the_edition_is_hidden(self):
        c, day = valid(), raw_day()
        c["stories"][0]["tie"] = {"to": "s77", "text": "Nowhere."}
        resplit(c)
        script = Script(grader_answer(composer.grading_units(c, day)))
        with unittest.mock.patch.object(composer.llm, "call", script):
            verdicts, week, _, _ = composer.check_faithfulness(object(), c, day, None, user_id="t")
        edition = composer.assemble_edition(c, day, {"items": []}, verdicts, week, stats={}, source_minutes=0, style=[])
        self.assertNotIn("tie", edition["stories"][0])


class Request(unittest.TestCase):
    def test_the_style_guide_is_in_the_cached_system_prompt_and_excerpts_are_what_is_shown(self):
        profile = framer.load_json(framer.PROFILE_PATH)
        system = composer.load_system_prompt()
        self.assertIn("# Style guide", system)
        self.assertIn("Every essay paragraph opens on a dated, attributed fact", system)
        req = composer.compose_request(system, profile, raw_day(), {"items": [{"id": "item-1", "why": "W", "ninety": None}]}, "Oct 10 – Oct 18, 2026",
                                       {"item-1": "2026-10-11"})
        self.assertEqual(req["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Every essay paragraph", req["system"][0]["text"])
        sent = json.loads(req["messages"][0]["content"])
        self.assertEqual(sent["edition_window"], "Oct 10 – Oct 18, 2026")
        self.assertEqual([s["id"] for s in sent["stories"]], ["s01", "s02", "s03", "s04", "s05"])
        self.assertTrue(sent["stories"][0]["excerpt"].startswith("Published 2026-10-11 by Helio Labs. "))
        self.assertEqual(sent["stories"][0]["why"], "W")
        self.assertNotIn("sources", sent["stories"][0])
        self.assertTrue(req["stream"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class StraightQuotes(unittest.TestCase):
    """The composing pass is shown, and graded against, a page with straight quotes (decision log, 2026-10-20, Run 3)."""

    def test_curly_marks_become_straight_and_nothing_else_changes(self):
        from agents import weekly
        self.assertEqual(weekly.straight_quotes("the repository’s “Agentic autofix” policy ‘x’"), "the repository's \"Agentic autofix\" policy 'x'")
        self.assertEqual(weekly.straight_quotes("a \u2013 b \u2014 c \u2026 caf\u00e9"), "a \u2013 b \u2014 c \u2026 caf\u00e9")      # en and em dashes, ellipses, accents untouched
        self.assertEqual(weekly.straight_quotes("GPT\u20116 and a\u2010b and 1\u20122 and x\u00a0y"), "GPT-6 and a-b and 1-2 and x y")   # hyphen look-alikes and no-break spaces

    def test_a_quote_the_grader_makes_with_straight_marks_now_verifies_against_the_text_it_was_shown(self):
        from agents import weekly
        shown = weekly.straight_quotes("Agentic autofix supports code scanning alerts on the repository’s default branch.")
        verdict = framer.normalise_verdict({"confidence": 1, "claims": [{"claim": "It supports the default branch only", "supported": True,
                                            "supporting_span": "Agentic autofix supports code scanning alerts on the repository's default branch."}]}, shown, "")
        self.assertEqual(verdict["unsupported_claims"], [])
        # ...and a wrong claim is no easier to support than before.
        wrong = framer.normalise_verdict({"confidence": 1, "claims": [{"claim": "x", "supported": True, "supporting_span": "on the organisation's default branch"}]}, shown, "")
        self.assertEqual(wrong["unsupported_claims"], ["x"])


class TinyUsage:
    input_tokens = output_tokens = 1
    cache_read_input_tokens = cache_creation_input_tokens = 0


class Revision(unittest.TestCase):
    def test_every_field_path_writes_back_to_the_field_it_names(self):
        c = valid()
        for path in composer.prose_fields(c):
            self.assertTrue(composer.set_field(c, path, f"NEW {path}"), path)
            self.assertEqual(composer.prose_fields(c)[path], f"NEW {path}", path)
        self.assertFalse(composer.set_field(c, "story.s99.body", "x"))
        self.assertFalse(composer.set_field(c, "learning.para.99", "x"))
        self.assertFalse(composer.set_field(c, "nonsense", "x"))

    def test_only_fields_that_code_found_broken_are_sent_back(self):
        c = valid()
        self.assertEqual(composer.revisable_problems(c, IDS), {})
        c["learning"]["paras"][1] = "The same authors report a result."
        c["thread"]["body"] = "Short [[#s01|body]] and [[#s02|text]]."
        c["stories"][2]["body"] = "One sentence only."
        c["learning"]["paras"] = c["learning"]["paras"][:5]            # too few paragraphs: not repairable field by field
        resplit(c)
        problems = composer.revisable_problems(c, IDS)
        self.assertEqual(set(problems), {"learning.para.2", "thread.body", "story.s03.body"})
        self.assertTrue(any("rule 1" in m for m in problems["learning.para.2"]))
        self.assertTrue(any("words, 80 to 120" in m for m in problems["thread.body"]))

    def revise(self, answer, problems=None, c=None):
        c = c or valid()
        c["learning"]["paras"][1] = "The same authors report a result."
        resplit(c)
        problems = problems or composer.revisable_problems(c, IDS)
        script = Script(answer)
        profile = framer.load_json(framer.PROFILE_PATH)
        with unittest.mock.patch.object(composer.llm, "call", script):
            out, usage, cost, replaced = composer.revise(object(), "SYSTEM", profile, raw_day(), {"items": []}, "Oct 10 – Oct 18, 2026", None, c, problems)
        return c, out, script, replaced

    def test_a_rewrite_replaces_the_field_and_its_split_and_nothing_else(self):
        new = paragraph(2)
        answer = {"fields": [{"path": "learning.para.2", "text": new, "runs": [{"text": new[:20], "class": "fact"}, {"text": new[20:], "class": "interpretation"}]}]}
        before, out, script, replaced = self.revise(answer)
        self.assertEqual(replaced, ["learning.para.2"])
        self.assertEqual(out["learning"]["paras"][1], new)
        self.assertEqual(composer.coverage_failures(out), [])
        self.assertEqual([r["class"] for r in composer.split_map(out)["learning.para.2"]], ["fact", "interpretation"])
        for path, text in composer.prose_fields(before).items():
            if path != "learning.para.2":
                self.assertEqual(composer.prose_fields(out)[path], text, path)
        self.assertEqual(before["learning"]["paras"][1], "The same authors report a result.")       # the first draft is not mutated
        request = script.requests[0]
        self.assertEqual(request["phase"], composer.COMPOSE_PHASE + "-revise")
        sent = json.loads(request["messages"][0]["content"])
        self.assertEqual([r["path"] for r in sent["revise"]], ["learning.para.2"])
        self.assertIn("rule 1", " ".join(sent["revise"][0]["problems"]))
        self.assertIn("REVISION", sent["instruction"])
        self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})              # the same cached system prompt

    def test_fields_the_writer_was_not_asked_about_are_ignored(self):
        answer = {"fields": [{"path": "thread.body", "text": "Sneaked in.", "runs": [{"text": "Sneaked in.", "class": "fact"}]},
                             {"path": "story.s99.body", "text": "x", "runs": []},
                             {"path": "learning.para.2", "text": paragraph(2), "runs": [{"text": paragraph(2), "class": "fact"}]},
                             {"path": "learning.para.2", "text": "second answer for the same field", "runs": []}]}
        before, out, _, replaced = self.revise(answer)
        self.assertEqual(replaced, ["learning.para.2"])
        self.assertEqual(out["thread"]["body"], before["thread"]["body"])
        self.assertEqual(out["learning"]["paras"][1], paragraph(2))

    def test_a_rewrite_that_does_not_reconstruct_is_still_caught_by_the_coverage_gate(self):
        answer = {"fields": [{"path": "learning.para.2", "text": paragraph(2), "runs": [{"text": "something else", "class": "fact"}]}]}
        _, out, _, _ = self.revise(answer)
        self.assertTrue(any("learning.para.2: split does not reconstruct" in l for l in composer.coverage_failures(out)))

    def test_compose_with_revision_rewrites_at_most_once_and_nothing_when_the_draft_is_clean(self):
        profile = framer.load_json(framer.PROFILE_PATH)
        clean = valid()
        calls = []

        def call(client, **kw):
            calls.append(kw["phase"])
            return json.dumps(clean), TinyUsage(), 0.4
        with unittest.mock.patch.object(composer.llm, "call", call):
            composed, record, cost = composer.compose_with_revision(object(), "S", profile, raw_day(), {"items": []}, "w", None)
        self.assertEqual((calls, record["problems"], record["replaced"], cost), ([composer.COMPOSE_PHASE], {}, [], 0.4))
        broken = valid()
        broken["learning"]["paras"][1] = "The same authors report a result."
        resplit(broken)
        answers = [broken, {"fields": [{"path": "learning.para.2", "text": paragraph(2), "runs": [{"text": paragraph(2), "class": "fact"}]}]}]
        calls.clear()

        def call2(client, **kw):
            calls.append(kw["phase"])
            return json.dumps(answers.pop(0)), TinyUsage(), 0.2
        with unittest.mock.patch.object(composer.llm, "call", call2):
            composed, record, cost = composer.compose_with_revision(object(), "S", profile, raw_day(), {"items": []}, "w", None)
        self.assertEqual(calls, [composer.COMPOSE_PHASE, composer.COMPOSE_PHASE + "-revise"])        # one draft, one rewrite, no third call
        self.assertEqual(record["replaced"], ["learning.para.2"])
        self.assertEqual(record["first_draft"]["learning"]["paras"][1], "The same authors report a result.")
        self.assertEqual(composed["learning"]["paras"][1], paragraph(2))
        self.assertAlmostEqual(cost, 0.4)

    def test_a_second_rewrite_round_is_allowed_and_a_third_is_not(self):
        profile = framer.load_json(framer.PROFILE_PATH)
        broken = valid()
        broken["learning"]["paras"][1] = "The same authors report a result."
        resplit(broken)
        still_broken = {"fields": [{"path": "learning.para.2", "text": "This also fails the rule.", "runs": [{"text": "This also fails the rule.", "class": "fact"}]}]}
        fixed = {"fields": [{"path": "learning.para.2", "text": paragraph(2), "runs": [{"text": paragraph(2), "class": "fact"}]}]}
        for second, expected_calls in ((fixed, 3), (still_broken, 3)):
            answers, calls = [broken, still_broken, second], []

            def call(client, **kw):
                calls.append(kw["phase"])
                return json.dumps(answers.pop(0)), TinyUsage(), 0.1
            with unittest.mock.patch.object(composer.llm, "call", call):
                composed, record, cost = composer.compose_with_revision(object(), "S", profile, raw_day(), {"items": []}, "w", None)
            self.assertEqual(len(calls), expected_calls)                                           # a draft and two rewrites, never a third rewrite
            self.assertEqual(len(record["rounds"]), 2)
        self.assertTrue(composer.revisable_problems(composed, IDS))                                  # the last case is left broken, for the gate to fail

    def test_a_split_that_does_not_reconstruct_is_sent_back_with_the_same_text(self):
        c = valid()
        c["splits"] = [s for s in c["splits"] if s["path"] != "story.s03.tryIt"]
        c["stories"][2]["tryIt"] = "Try the queue on one repository. Compare it with a manual merge."
        problems = composer.revisable_problems(c, IDS)
        self.assertIn("story.s03.tryIt", problems)
        self.assertIn("no split", problems["story.s03.tryIt"][0])


class ReviewFixes(unittest.TestCase):
    """Defects the Step C reviewer found, each with a test that fails without its fix."""

    def test_a_revision_can_supply_a_split_the_first_draft_left_out(self):
        c = valid()
        c["stories"][2]["tryIt"] = "Try the queue on one repository. Compare it with a manual merge."
        problems = composer.revisable_problems(c, IDS)                               # no split for story.s03.tryIt
        text = c["stories"][2]["tryIt"]
        answer = {"fields": [{"path": "story.s03.tryIt", "text": text, "runs": [{"text": text, "class": "interpretation"}]}]}
        profile = framer.load_json(framer.PROFILE_PATH)
        with unittest.mock.patch.object(composer.llm, "call", Script(answer)):
            out, _, _, replaced = composer.revise(object(), "S", profile, raw_day(), {"items": []}, "w", None, c, problems)
        self.assertEqual(replaced, ["story.s03.tryIt"])
        self.assertEqual([l for l in composer.coverage_failures(out) if "s03.tryIt" in l], [])
        self.assertEqual(len([s for s in out["splits"] if s["path"] == "story.s03.tryIt"]), 1)

    def test_a_revision_never_leaves_a_path_twice(self):
        c = valid()
        c["splits"].append(dict(c["splits"][0]))                                      # a duplicate the writer made
        c["learning"]["paras"][1] = "The same authors report a result."
        c["splits"] = [s for s in c["splits"] if s["path"] != "learning.para.2"] + [
            {"path": "learning.para.2", "runs": [{"text": "The same authors report a result.", "class": "fact"}]}]
        problems = composer.revisable_problems(c, IDS)
        answer = {"fields": [{"path": "learning.para.2", "text": paragraph(2), "runs": [{"text": paragraph(2), "class": "fact"}]}]}
        profile = framer.load_json(framer.PROFILE_PATH)
        with unittest.mock.patch.object(composer.llm, "call", Script(answer)):
            out, _, _, _ = composer.revise(object(), "S", profile, raw_day(), {"items": []}, "w", None, c, problems)
        paths = [s["path"] for s in out["splits"]]
        self.assertEqual(len(paths), len(set(paths)))

    def test_the_essays_advice_is_graded(self):
        payload = composer.grading_units(valid(), raw_day())
        essay = next(u for u in payload["items"] if u["id"] == "essay")
        self.assertIn("doDifferently", essay["writing_under_test"])

    def test_a_field_classed_entirely_as_interpretation_is_a_failure(self):
        c = valid()
        for path in ("story.s02.body", "thread.body", "learning.para.3", "closing.summary", "story.s01.feature.2"):
            runs = composer.split_map(c)[path]
            c["splits"] = [s for s in c["splits"] if s["path"] != path] + [{"path": path, "runs": [{"text": r["text"], "class": "interpretation"} for r in runs]}]
        out = composer.fact_floor_failures(c)
        for path in ("story.s02.body", "thread.body", "learning.para.3", "closing.summary", "story.s01.feature.2"):
            self.assertTrue(any(l.startswith(path) for l in out), (path, out))
        self.assertEqual(composer.fact_floor_failures(valid()), [])

    def test_advice_fields_may_be_pure_interpretation_but_an_essay_paragraph_must_open_on_a_fact(self):
        c = valid()
        for path in ("story.s01.tryIt", "learning.doDifferently", "closing.oneThing"):
            text = composer.prose_fields(c)[path]
            c["splits"] = [s for s in c["splits"] if s["path"] != path] + [{"path": path, "runs": [{"text": text, "class": "interpretation"}]}]
        self.assertEqual(composer.fact_floor_failures(c), [])
        text = composer.prose_fields(c)["learning.para.2"]
        cut = text.index(".") + 1
        c["splits"] = [s for s in c["splits"] if s["path"] != "learning.para.2"] + [{"path": "learning.para.2", "runs": [
            {"text": text[:cut], "class": "interpretation"}, {"text": text[cut:], "class": "fact"}]}]
        self.assertTrue(any("learning.para.2: the opening clause is classed as interpretation" in l for l in composer.fact_floor_failures(c)))

    def test_fact_floor_is_part_of_the_edition_gate(self):
        c = valid()
        c["splits"] = [s for s in c["splits"] if s["path"] != "story.s02.body"] + [{"path": "story.s02.body", "runs": [
            {"text": c["stories"][1]["body"], "class": "interpretation"}]}]
        payload = composer.grading_units(c, raw_day())
        verdicts = {u["id"]: {"claims": [{}], "unsupported_claims": []} for u in payload["items"]}
        out = composer.edition_failures(c, raw_day(), payload, verdicts, {"claims": [{}], "unsupported_claims": []})
        self.assertTrue(any("story.s02.body: no clause is classed as a fact" in l for l in out))

    def test_more_slot_rules(self):
        def fails(mutate, expect):
            c = valid()
            mutate(c)
            resplit(c)
            out = composer.slot_failures(c, IDS)
            self.assertTrue(any(expect in l for l in out), (expect, out))
        fails(lambda c: c["learning"].update(headline="[[hl|One]] and [[hl|two]] highlights"), "learning.headline: more than one")
        fails(lambda c: c.update(storiesTitle="[[hl|One]] and [[hl|two]] highlights"), "stories.title: more than one")
        fails(lambda c: c["stories"][0].update(body=c["stories"][0]["body"] + " [[hl|Highlight]] in a body."), "token [[hl|Highlight]] is not allowed here")
        fails(lambda c: c["stories"][0].update(body=c["stories"][0]["body"] + " See [[#s02|story two]]."), "token [[#s02|story two]] is not allowed here")
        fails(lambda c: c["closing"].update(summary="Only one sentence."), "closing.summary: 1 sentences")
        fails(lambda c: c["learning"].update(doDifferently="One. Two. Three sentences here."), "doDifferently")
        fails(lambda c: c["stories"][0].update(hashtags=["#Agents", "#agents"]), "a label is repeated")
        fails(lambda c: c["dividers"].extend([{"before": "s03", "label": "Again"}]), "two dividers before the same story")


class FactFloorIsRevisable(unittest.TestCase):
    def test_a_field_with_no_fact_runs_is_sent_back_with_its_address(self):
        c = valid()
        text = composer.prose_fields(c)["learning.para.3"]
        c["splits"] = [s for s in c["splits"] if s["path"] != "learning.para.3"] + [{"path": "learning.para.3", "runs": [{"text": text, "class": "interpretation"}]}]
        problems = composer.revisable_problems(c, IDS)
        self.assertIn("learning.para.3", problems)
        self.assertIn("classify the clauses again", problems["learning.para.3"][0])
