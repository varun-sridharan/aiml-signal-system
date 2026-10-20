"""The five hard style rules of config/style-guide.md, as a deterministic checker.

    python -m unittest tests.test_style_check -v

Free and offline. Each rule has its own tests: sentences that must fail it (several shapes, including
the style guide's own examples) and sentences that must pass. autopilot/evidence/2026-10-21/stepC-mutate.py
neutralises one rule at a time and checks its tests go red.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import style_check as sc  # noqa: E402

GOOD_OPENING = ("On October 14, 2026, Helio Labs released Orbit 1.0, an open-source SDK that cut merge conflicts "
                "from 31 percent of runs to 9 percent across 212 repositories. It is free to self-host.")


class Sentences(unittest.TestCase):
    def test_splitting_keeps_abbreviations_and_decimals_together(self):
        got = sc.sentences("Dr. Rao said the model scored 0.93 on the test. Inc. filings followed. It rose.")
        self.assertEqual(len(got), 3)
        self.assertTrue(got[0].startswith("Dr. Rao"))

    def test_slot_tokens_are_read_as_the_text_they_display(self):
        self.assertEqual(sc.plain("[[hl|Four releases]] cut cost; see [[#s02|the second story]] and [[fn:kv|KV cache]]."),
                         "Four releases cut cost; see the second story and KV cache.")


class Rule1Opening(unittest.TestCase):
    def test_a_dated_attributed_fact_with_a_number_passes(self):
        self.assertEqual(sc.opening_failures(GOOD_OPENING), [])
        self.assertEqual(sc.opening_failures("Anthropic said on Tuesday that it had shipped 3 updates."), [])
        self.assertEqual(sc.opening_failures("Last week OpenAI told developers that 4 endpoints would close."), [])

    def test_an_opening_that_announces_something_will_be_told_fails(self):
        for text in ("Capability went ambient. Anthropic shipped 3 things on October 14, 2026.",
                     "Here is the shape of the week: 4 releases on October 14, 2026.",
                     "Consider what Helio Labs did on October 14, 2026 with 212 repositories.",
                     "The story of the week is Helio Labs and its 212 repositories on October 14, 2026.",
                     "Something shifted on October 14, 2026, when Helio Labs shipped 1 SDK."):
            self.assertIn("opens by announcing that something will be told", sc.opening_failures(text), text)

    def test_an_opening_with_no_number_fails(self):
        self.assertIn("the opening sentence carries no number", sc.opening_failures("On October 14, Helio Labs released Orbit."))

    def test_an_opening_with_no_date_fails(self):
        self.assertIn("the opening sentence carries no date", sc.opening_failures("Helio Labs released Orbit to 212 repositories."))

    def test_an_opening_with_no_named_company_or_person_fails(self):
        self.assertIn("the opening sentence names no company or person",
                      sc.opening_failures("On October 14, 2026, an open-source SDK cut merge conflicts by 22 points."))

    def test_a_paragraph_of_argument_with_no_digit_is_flagged(self):
        out = sc.prose_failures("Helio Labs shipped Orbit last week. It cut conflicts a lot.", "essay paragraph 1", opening=True)
        self.assertTrue(any("rule 1" in line for line in out))
        self.assertTrue(any("no number in it" in line for line in out), out)

    def test_abbreviated_months_do_not_split_the_opening(self):
        self.assertEqual(sc.opening_failures("Anthropic cut prices to 6 dollars on Oct. 5, 2026. It also did more."), [])
        self.assertEqual(len(sc.sentences("Helio shipped it on Sept. 30, 2026. Then more.")), 2)

    def test_only_the_first_sentence_has_to_open_the_fact(self):
        self.assertEqual([l for l in sc.prose_failures(GOOD_OPENING, "p", opening=True) if "rule 1" in l], [])

    def test_rule_1_applies_to_essay_paragraphs_and_not_to_story_bodies(self):
        story = {"learning": {"paras": []}, "stories": [{"id": "s01", "body": "Helio shipped it. Conflicts fell."}]}
        self.assertFalse(any("rule 1" in line for line in sc.edition_style_failures(story)))
        essay = {"learning": {"paras": ["Helio shipped it. Conflicts fell."]}}
        self.assertTrue(any("rule 1" in line for line in sc.edition_style_failures(essay)))


class Rule2Subject(unittest.TestCase):
    def test_the_style_guides_own_bad_example_fails(self):
        self.assertTrue(sc.subject_failures("Everything that grants permission did."))
        self.assertTrue(sc.subject_failures("Capability went ambient."))

    def test_abstract_nouns_as_subjects_fail_whatever_the_determiner(self):
        for s in ("The mechanism is unclear.", "Permissions are checked by Anthropic.", "A thing changed.", "Oversight shrank.",
                  "The scarce good is attention.", "This shape repeats.", "Verification costs more.", "The new capability arrived.",
                  "The axis flipped."):
            self.assertTrue(sc.subject_failures(s), s)

    def test_a_named_company_or_person_passes_even_with_an_abstract_noun_later_in_the_sentence(self):
        for s in ("Anthropic scoped its models' reasoning.", "Anthropic's permission model changed.", "Helio Labs added oversight to Orbit.",
                  "Maria Ostrowski told staff that verification would move in-house.", "Helio added oversight to Orbit.", "Anthropic permission changes shipped.", "The bank cut 1,800 jobs."):
            self.assertEqual(sc.subject_failures(s), [], s)

    def test_words_outside_the_style_guides_list_are_not_banned(self):
        for s in ("The question is open.", "Trust moved to the vendor.", "The pattern repeated.", "The harder question is whether the queue scales.",
                  "Nothing in the paper says otherwise.", "Nothing changed for Anthropic's customers."):
            self.assertEqual(sc.subject_failures(s), [], s)

    def test_the_rule_is_applied_to_every_sentence_of_a_field(self):
        out = sc.prose_failures("Helio shipped Orbit on October 14, 2026. The mechanism is unclear.", "thread body")
        self.assertTrue(any("sentence 2" in line and "rule 2" in line for line in out), out)


class Rule3Restating(unittest.TestCase):
    def test_the_varun_example_fails(self):
        self.assertTrue(sc.restating_failures("Permission costs what it costs because verification costs what it costs."))

    def test_its_relatives_fail(self):
        for s in ("The model is what it is.", "Latency matters what it matters.", "Pricing is what it is.", "Compute costs what it costs.",
                  "A bubble is a bubble.", "Risk is risk."):
            self.assertTrue(sc.restating_failures(s), s)

    def test_a_sentence_that_says_something_passes(self):
        for s in ("The model is what Helio shipped on October 14.", "Compute costs 40 dollars an hour.", "Anthropic is a lab that trains models.",
                  "It costs what the contract says it costs: 9 dollars a seat."):
            self.assertEqual(sc.restating_failures(s), [], s)


class Rule4Comparisons(unittest.TestCase):
    def test_a_bare_ratio_or_change_fails(self):
        for s in ("Revenue grew about 25x.", "Costs fell 40 percent.", "Latency dropped 3x.", "Usage rose 12 percent last quarter.",
                  "Throughput was 1.4 times higher.", "The index is up 8%."):
            self.assertTrue(sc.comparison_failures(s), s)

    def test_a_computed_comparison_with_both_endpoints_passes(self):
        for s in ("Revenue grew about 25x, from $96 billion to $2.4 trillion.", "Costs fell 40 percent, from $10 to $6 a month.",
                  "Latency dropped 3x, from 105 milliseconds to 35 milliseconds.", "Usage rose 12 percent, from 800 to 896 sessions."):
            self.assertEqual(sc.comparison_failures(s), [], s)

    def test_the_computed_figure_must_follow_from_its_endpoints(self):
        for s in ("Revenue grew about 25x, from $96 billion to $240 billion.", "Costs fell 40 percent, from $10 to $9 a month.",
                  "Latency dropped 3x, from 105 milliseconds to 90 milliseconds."):
            self.assertTrue(any("does not follow" in m for m in sc.comparison_failures(s)), s)
        for s in ("Revenue grew about 25x, from $96 billion to $2.4 trillion.", "Costs fell 40 percent, from $10 to $6 a month.",
                  "Latency dropped 3x, from 105 milliseconds to 35 milliseconds.", "Usage fell from 31 percent of runs to 9 percent."):
            self.assertEqual(sc.comparison_failures(s), [], s)

    def test_vaguer_changes_and_evasions_are_caught_and_honest_counts_are_not(self):
        for s in ("Revenue doubled in a year.", "Prices fell by half.", "Revenue grew 40% to $5 billion.", "Prices fell 40 percent to $6."):
            self.assertTrue(sc.comparison_failures(s), s)
        for s in ("The team ran 5-fold cross-validation.", "We ran it 3 times.", "The paper reports 3 times as many calls from 10 to 30."):
            self.assertEqual([m for m in sc.comparison_failures(s) if "endpoints" in m and "without" in m], [], s)

    def test_plain_figures_are_not_comparisons(self):
        for s in ("The trial covered 31 percent of runs.", "It ran 20 calls per task.", "Pricing starts at 40 dollars a month."):
            self.assertEqual(sc.comparison_failures(s), [], s)


class Rule5StageDirections(unittest.TestCase):
    def test_the_style_guides_examples_fail(self):
        for s in ("Read that twice.", "So ask what the bank did.", "Here is the shape of it.", "Notice that costs fell.", "The point is cost."):
            self.assertTrue(sc.stage_direction_failures(s), s)

    def test_more_shapes_fail(self):
        for s in ("Let's look at the numbers.", "Keep that in mind.", "Ask yourself who pays.", "Think about that for a second.",
                  "Hold that thought.", "Consider this: Helio cut conflicts."):
            self.assertTrue(sc.stage_direction_failures(s), s)

    def test_spacing_and_dash_tricks_are_folded(self):
        for s in ("Read  that   twice.", "Read that\u2014twice.", "Here's why that matters.", "Sit with that.", "Think of it this way."):
            self.assertTrue(sc.stage_direction_failures(s), s)

    def test_ordinary_sentences_pass(self):
        for s in ("Helio released Orbit.", "The team noted that costs fell.", "The authors note that costs fell.", "Analysts read the filing on Friday.", "Anthropic asked customers to migrate."):
            self.assertEqual(sc.stage_direction_failures(s), [], s)


class WholeEdition(unittest.TestCase):
    def edition(self, **over):
        base = {
            "thread": {"lines": ["Four releases cut the cost of running a model.", "Three decisions restricted who may inspect one."],
                       "body": "Helio Labs released Orbit on October 14, 2026."},
            "learning": {"headline": "Helio cut conflicts from 31 percent to 9 percent", "paras": [GOOD_OPENING], "doDifferently": "Run your agents on separate branches."},
            "stories": [{"id": "s01", "body": "Helio released Orbit 1.0.", "tryIt": "Try the SDK on a small repository.", "featureParas": ["Helio released Orbit 1.0."]}],
            "closing": {"lines": ["One lab shipped a queue.", "Another shipped a fix."], "summary": "Helio shipped a queue.", "oneThing": "Try Orbit on one repository."},
        }
        base.update(over)
        return base

    def test_a_clean_edition_has_no_failures(self):
        self.assertEqual(sc.edition_style_failures(self.edition()), [])

    def test_headlines_ties_dividers_and_notes_are_checked_too(self):
        bad = "Capability went ambient."
        ed = self.edition()
        ed["storiesTitle"] = bad
        ed["dividers"] = [{"before": "s01", "label": bad}]
        ed["learning"]["notes"] = [{"key": "queue", "term": "q", "definition": bad}]
        ed["stories"][0].update(headline=bad, tie={"to": "s02", "text": bad})
        paths = {p for p, _ in sc.edition_style_findings(ed)}
        self.assertEqual(paths, {"stories.title", "divider.s01", "learning.note.queue", "story.s01.headline", "story.s01.tie"})

    def test_findings_carry_the_address_of_the_field(self):
        ed = self.edition()
        ed["learning"]["paras"][0] = "The same authors report a result."
        self.assertEqual({p for p, _ in sc.edition_style_findings(ed)}, {"learning.para.1"})

    def test_every_prose_field_is_checked_and_named(self):
        bad = "Read that twice."
        ed = self.edition()
        ed["thread"]["lines"][0] = bad
        ed["thread"]["body"] = bad
        ed["learning"]["headline"] = bad
        ed["learning"]["doDifferently"] = bad
        ed["stories"][0].update(body=bad, tryIt=bad, featureParas=[bad])
        ed["closing"].update(lines=[bad, bad], summary=bad, oneThing=bad)
        out = "\n".join(sc.edition_style_failures(ed))
        for where in ("thread line 1", "thread body", "essay headline", "what to do differently", "s01 body", "s01 tryIt", "s01 feature paragraph 1",
                      "closing line 1", "closing line 2", "closing summary", "closing: one thing to try"):
            self.assertIn(where, out, where)


if __name__ == "__main__":
    unittest.main(verbosity=2)
