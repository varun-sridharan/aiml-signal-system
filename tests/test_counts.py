"""A count across stories names its stories, and code checks the count (Run 4, Step C)."""

import unittest

from agents import composer, counts
from tests.test_composer import raw_day, valid

IDS = {f"s{n:02d}": f"item-{n}" for n in range(1, 10)}      # a nine-story edition


def fields(**kw):
    return {k.replace("_", "."): v for k, v in kw.items()}


def entry(path, phrase, stories):
    return {"path": path, "phrase": phrase, "stories": stories}


def check(path, text, entries, ids=IDS):
    return counts.count_failures({"counts": entries}, ids, {path: text})


class Mentions(unittest.TestCase):
    def test_the_three_forms(self):
        self.assertEqual([m["n"] for m in counts.mentions("thread.body", "Four releases cut the cost of running a model.")], [4])
        self.assertEqual([m["n"] for m in counts.mentions("thread.body", "Two papers disagree about the benchmark.")], [2])
        m = counts.mentions("closing.summary", "Three of this week's nine stories were about pricing.")
        self.assertEqual([(x["n"], x["total"], x["form"]) for x in m], [(3, 9, "of")])

    def test_markers_are_read_as_the_words_they_show(self):
        m = counts.mentions("thread.line.1", "[[hl|Four releases]] cut the cost of running a model.")
        self.assertEqual(m[0]["phrase"], "Four releases")

    def test_digits_and_an_adjective(self):
        self.assertEqual(counts.mentions("thread.body", "3 new releases landed.")[0]["n"], 3)

    def test_a_figure_that_is_not_a_story_count_is_ignored(self):
        self.assertEqual(counts.mentions("thread.body", "It cut merge conflicts across 212 repositories by 31 percent."), [])
        self.assertEqual(counts.mentions("thread.body", "One release, then nothing for a month."), [])

    def test_a_single_story_field_is_only_scanned_for_the_edition_form(self):
        self.assertEqual(counts.mentions("story.s01.body", "The vendor shipped five models in a day."), [])
        self.assertEqual(len(counts.mentions("story.s01.body", "It was one of three of the stories about pricing.")), 1)
        self.assertEqual(len(counts.mentions("story.s01.tie", "Four releases point the same way.")), 1)


class Verdicts(unittest.TestCase):
    def test_four_releases_named_correctly_passes(self):
        e = [entry("thread.body", "Four releases", ["s01", "s02", "s04", "s05"])]
        self.assertEqual(check("thread.body", "Four releases cut the cost.", e), [])

    def test_two_papers_named_correctly_passes_and_matches_case_and_spacing(self):
        e = [entry("thread.body", "two  papers", ["s03", "s06"])]
        self.assertEqual(check("thread.body", "Two papers disagree.", e), [])

    def test_three_of_nine_passes_with_the_right_total(self):
        e = [entry("closing.summary", "three of this week's nine stories", ["s01", "s02", "s03"])]
        self.assertEqual(check("closing.summary", "Three of this week's nine stories were about pricing.", e), [])

    def test_an_unlisted_count_fails(self):
        out = check("thread.body", "Four releases cut the cost.", [])
        self.assertEqual(len(out), 1)
        self.assertIn("no entry", " ".join(out))

    def test_a_miscount_fails(self):
        e = [entry("thread.body", "Four releases", ["s01", "s02", "s04"])]
        self.assertIn("says 4 but `counts` names 3", " ".join(check("thread.body", "Four releases cut the cost.", e)))

    def test_a_story_that_does_not_exist_fails(self):
        e = [entry("thread.body", "Two papers", ["s03", "s42"])]
        self.assertIn("s42", " ".join(check("thread.body", "Two papers disagree.", e)))

    def test_naming_one_story_twice_fails(self):
        e = [entry("thread.body", "Two papers", ["s03", "s03"])]
        self.assertIn("twice", " ".join(check("thread.body", "Two papers disagree.", e)))

    def test_the_wrong_total_fails(self):
        e = [entry("closing.summary", "three of this week's eight stories", ["s01", "s02", "s03"])]
        out = check("closing.summary", "Three of this week's eight stories were about pricing.", e)
        self.assertTrue(any("total of 8 but this edition has 9" in line for line in out))

    def test_more_than_the_edition_has_fails(self):
        e = [entry("closing.summary", "twelve of the stories", [f"s0{n}" for n in range(1, 10)])]
        out = check("closing.summary", "Twelve of the stories were about pricing.", e)
        self.assertTrue(any("more than the 9" in line for line in out))

    def test_an_entry_for_another_field_does_not_count(self):
        e = [entry("closing.summary", "Four releases", ["s01", "s02", "s04", "s05"])]
        self.assertEqual(len(check("thread.body", "Four releases cut the cost.", e)), 1)

    def test_a_stale_entry_with_no_phrase_in_the_text_is_ignored(self):
        e = [entry("thread.body", "Four releases", ["s01", "s02", "s04", "s05"])]
        self.assertEqual(check("thread.body", "Releases cut the cost.", e), [])

    def test_two_counts_in_one_field_each_need_an_entry(self):
        text = "Four releases cut the cost and two papers asked why."
        e = [entry("thread.body", "Four releases", ["s01", "s02", "s04", "s05"])]
        out = check("thread.body", text, e)
        self.assertEqual(len(out), 1)
        self.assertIn("two papers", " ".join(out))


class ReviewFixes(unittest.TestCase):
    def test_an_of_form_total_is_the_edition_only_for_stories_and_items(self):
        e = [entry("thread.body", "Two of the three papers", ["s01", "s02"])]
        self.assertEqual(check("thread.body", "Two of the three papers use RL.", e), [])
        e = [entry("thread.body", "Two of the three stories", ["s01", "s02"])]
        self.assertIn("total of 3", " ".join(check("thread.body", "Two of the three stories use RL.", e)))

    def test_four_in_five_stories_is_read_as_a_fraction(self):
        m = counts.mentions("thread.body", "Four in five stories mention cost.")
        self.assertEqual([(x["n"], x["total"]) for x in m], [(4, 5)])

    def test_intervening_words_and_the_other_ways_to_say_a_number(self):
        for text, n in (("Four model releases cut cost.", 4), ("Four open-source releases cut cost.", 4), ("Both releases cut cost.", 2),
                        ("A pair of papers disagree.", 2), ("A dozen announcements landed.", 12), ("Four  releases cut cost.", 4)):
            self.assertEqual([m["n"] for m in counts.mentions("thread.body", text)], [n], text)

    def test_nouns_that_are_usually_one_storys_own_fact_are_not_counts(self):
        for text in ("The vendor shipped three updates.", "The report names 30 vendors.", "Five benchmarks were rerun.", "Four labs said no."):
            self.assertEqual(counts.mentions("thread.body", text), [], text)

    def test_a_phrase_said_twice_needs_two_entries(self):
        text = "Four releases cut cost. Four releases also raised risk."
        e = [entry("thread.body", "Four releases", ["s01", "s02", "s03", "s04"])]
        self.assertEqual(len(check("thread.body", text, e)), 1)
        self.assertEqual(check("thread.body", text, e + [entry("thread.body", "Four releases", ["s02", "s03", "s04", "s05"])]), [])


class InTheEdition(unittest.TestCase):
    def test_edition_gate_and_revision_see_a_miscount(self):
        c = valid(5)
        c["thread"]["body"] = c["thread"]["body"] + " Four releases cut the cost."
        c["counts"] = []
        day = raw_day(5)
        ids = composer.story_ids(day)
        self.assertTrue(any("count —" in line for line in counts.count_failures(c, ids, composer.prose_fields(c))))
        problems = composer.revisable_problems(c, ids)
        self.assertIn("thread.body", problems)
        self.assertTrue(any("without a number of stories" in m for m in problems["thread.body"]))

    def test_the_schema_requires_counts(self):
        self.assertIn("counts", composer.COMPOSE_SCHEMA["required"])


if __name__ == "__main__":
    unittest.main()
