"""A failure stops a sentence or a story, never the week: the engine in agents/repair.py (Run 5, Step A)."""

import json
import unittest
import unittest.mock
from types import SimpleNamespace as NS

from agents import composer, counts as counts_mod, repair
from tests.test_composer import raw_day, valid


class Text(unittest.TestCase):
    def test_removing_a_sentence_keeps_the_other_sentences_and_their_split(self):
        text = "One fact here. Two facts there. Three."
        runs = [{"text": "One fact here. ", "class": "fact"}, {"text": "Two facts there. ", "class": "interpretation"}, {"text": "Three.", "class": "fact"}]
        a, b = repair.find_sentence(text, "Two facts there.")
        new, new_runs = repair.remove_span(text, runs, a, b)
        self.assertEqual(new, "One fact here. Three.")
        self.assertEqual("".join(r["text"] for r in new_runs), new)
        self.assertEqual([r["class"] for r in new_runs], ["fact", "fact"])

    def test_removing_the_last_sentence_takes_the_space_before_it(self):
        text = "First. Second."
        a, b = repair.find_sentence(text, "Second.")
        self.assertEqual(repair.remove_span(text, None, a, b)[0], "First.")

    def test_replacing_a_sentence_splices_the_new_split_between_the_old_ones(self):
        text = "Keep this. Change this. Keep that."
        runs = [{"text": text, "class": "fact"}]
        a, b = repair.find_sentence(text, "Change this.")
        new, new_runs = repair.replace_span(text, runs, a, b, "Changed.", [{"text": "Changed.", "class": "interpretation"}])
        self.assertEqual(new, "Keep this. Changed. Keep that.")
        self.assertEqual("".join(r["text"] for r in new_runs), new)
        self.assertIn("interpretation", [r["class"] for r in new_runs])

    def test_a_split_that_does_not_reconstruct_is_read_as_one_fact(self):
        self.assertEqual(repair.as_runs("Whole text.", [{"text": "Other.", "class": "interpretation"}]), [{"text": "Whole text.", "class": "fact"}])
        self.assertEqual(repair.as_runs("Whole text.", None), [{"text": "Whole text.", "class": "fact"}])

    def test_only_sentences_with_a_fact_run_are_the_ones_the_grader_reads(self):
        text = "A fact stated. An opinion given."
        runs = [{"text": "A fact stated. ", "class": "fact"}, {"text": "An opinion given.", "class": "interpretation"}]
        self.assertEqual([text[a:b] for a, b in repair.fact_sentences(text, runs)], ["A fact stated."])


class Script:
    """Stands in for llm.call: answers the second reader and the rewriter from tables."""

    def __init__(self, agrees=True, rewrite="A supported sentence.", locate=None, unreadable=False, bad_rewrite=False):
        self.agrees, self.rewrite, self.locate, self.unreadable, self.bad_rewrite, self.calls = agrees, rewrite, locate, unreadable, bad_rewrite, []

    def __call__(self, client, **kw):
        body = json.loads(kw["messages"][0]["content"])
        self.calls.append(kw["phase"])
        if kw["phase"] == repair.SECOND_PHASE:
            if self.unreadable:
                return "not json", NS(), 0.0
            ids = [i for i, s in enumerate(body["sentences"]) if self.locate is None or self.locate in s["text"]][:1]
            return json.dumps({"supported": not self.agrees, "sentence_ids": ids if self.agrees else [], "why": "w"}), NS(), 0.0
        if self.bad_rewrite:
            return "not json", NS(), 0.0
        return json.dumps({"text": self.rewrite, "runs": [{"text": self.rewrite, "class": "fact"}] if self.rewrite else []}), NS(), 0.0


class Store:
    """A minimal store: fields by path, one unit, graded by a script."""

    def __init__(self, fields, grades, owner_of=None, bearing=None):
        self.fields, self.grades, self.owner_of, self.bearing, self.graded = fields, list(grades), owner_of or (lambda p: None), bearing or (lambda p, t, r: False), 0

    def paths(self, unit):
        return list(self.fields)

    def get(self, path):
        return self.fields[path], None

    def put(self, path, text, runs):
        self.fields[path] = text

    def truth(self, unit):
        return "the excerpt"

    def grade(self):
        self.graded += 1
        return self.grades.pop(0) if self.grades else {"u": {"unsupported_claims": []}}, 0.0

    def owner(self, path):
        return self.owner_of(path)

    def load_bearing(self, path, text, runs):
        return self.bearing(path, text, runs)


def flagged(*claims):
    return {"u": {"unsupported_claims": list(claims)}}


class Settle(unittest.TestCase):
    def run_settle(self, store, verdicts, script):
        with unittest.mock.patch.object(repair.llm, "call", script):
            return repair.settle(store, verdicts, object(), user_id="t")

    def test_nothing_flagged_costs_nothing_and_changes_nothing(self):
        store = Store({"p": "A fact. Another."}, [])
        script = Script()
        _v, log = self.run_settle(store, {"u": {"unsupported_claims": []}}, script)
        self.assertEqual((script.calls, store.graded, log["flags"]), ([], 0, 0))

    def test_a_flag_the_second_reader_does_not_confirm_is_dismissed_and_the_text_untouched(self):
        store = Store({"p": "Revenue doubled. Another."}, [])
        script = Script(agrees=False)
        _v, log = self.run_settle(store, flagged("Revenue doubled"), script)
        self.assertEqual(store.fields["p"], "Revenue doubled. Another.")
        self.assertEqual(script.calls, [repair.SECOND_PHASE])
        self.assertEqual(len(log["dismissed"]), 1)

    def test_an_unreadable_second_reader_does_not_dismiss_the_flag(self):
        store = Store({"p": "Revenue doubled. Another."}, [flagged()])
        _v, log = self.run_settle(store, flagged("Revenue doubled"), Script(unreadable=True, rewrite="Revenue rose."))
        self.assertEqual(log["dismissed"], [])
        self.assertEqual(store.fields["p"], "Revenue rose. Another.")

    def test_one_rewrite_then_a_regrade_and_a_clean_regrade_keeps_the_rewrite(self):
        store = Store({"p": "Revenue doubled. Another."}, [flagged()])
        _v, log = self.run_settle(store, flagged("Revenue doubled"), Script(rewrite="Revenue rose."))
        self.assertEqual(store.fields["p"], "Revenue rose. Another.")
        self.assertEqual((store.graded, len(log["rewritten"]), log["removed"]), (1, 1, []))

    def test_a_rewrite_that_still_fails_is_removed_and_never_rewritten_again(self):
        store = Store({"p": "Revenue doubled. Another."}, [flagged("Revenue rose")])
        script = Script(rewrite="Revenue rose.")
        _v, log = self.run_settle(store, flagged("Revenue doubled"), script)
        self.assertEqual(store.fields["p"], "Another.")
        self.assertEqual(script.calls.count(repair.REWRITE_PHASE), 1)
        self.assertEqual(len(log["removed"]), 1)

    def test_two_flags_on_one_sentence_get_one_rewrite_not_two(self):
        store = Store({"p": "Revenue doubled and costs fell. Another."}, [flagged()])
        script = Script(bad_rewrite=True, locate="Revenue")                          # an unusable answer leaves the sentence as it was
        self.run_settle(store, flagged("Revenue doubled", "Costs fell"), script)
        self.assertEqual(script.calls.count(repair.REWRITE_PHASE), 1)

    def test_a_writer_with_nothing_true_to_say_removes_the_sentence(self):
        store = Store({"p": "Revenue doubled. Another."}, [flagged()])
        _v, log = self.run_settle(store, flagged("Revenue doubled"), Script(rewrite=""))
        self.assertEqual(store.fields["p"], "Another.")

    def test_a_load_bearing_sentence_holds_the_story_instead_of_being_removed(self):
        store = Store({"story.s01.headline": "Revenue doubled."}, [flagged("Revenue rose")], owner_of=lambda p: "s01", bearing=lambda p, t, r: True)
        _v, log = self.run_settle(store, flagged("Revenue doubled"), Script(rewrite="Revenue rose."))
        self.assertEqual(list(log["held"]), ["s01"])
        self.assertEqual(log["removed"], [])

    def test_only_the_failing_sentence_goes_from_a_paragraph_of_other_facts(self):
        store = Store({"p": "Acme released a thing in 2026. Revenue doubled. Beta shipped 4 models."}, [flagged("Revenue rose")])
        self.run_settle(store, flagged("Revenue doubled"), Script(rewrite="Revenue rose.", locate="Revenue"))
        self.assertEqual(store.fields["p"], "Acme released a thing in 2026. Beta shipped 4 models.")


class Composed(unittest.TestCase):
    def store(self, grades):
        day = raw_day(5)
        c = valid(5)
        self.grades = list(grades)
        s = repair.ComposedStore(c, day, {}, object(), user_id="t")
        s.grade = lambda: (self.grades.pop(0) if self.grades else {}, 0.0)
        return s, c

    def test_a_failing_essay_sentence_goes_and_the_paragraphs_other_facts_stay(self):
        s, c = self.store([{"essay": {"unsupported_claims": []}}])
        para = c["learning"]["paras"][0]
        first = para.split(". ")[0] + "."
        with unittest.mock.patch.object(repair.llm, "call", Script(rewrite="", locate="released product 1 to 201")):
            _v, log = repair.settle(s, {"essay": {"unsupported_claims": ["a claim in the first sentence"]}}, object(), user_id="t")
        self.assertNotIn(first, c["learning"]["paras"][0])
        self.assertIn("The company keeps a changelog entry", c["learning"]["paras"][0])
        self.assertEqual(log["held"], {})
        self.assertEqual(len(c["learning"]["paras"]), 6)                                  # the paragraph itself stays

    def test_a_failing_headline_holds_its_story_and_a_failing_feature_paragraph_sentence_does_not(self):
        s, c = self.store([{}])
        head = c["stories"][2]["headline"]
        with unittest.mock.patch.object(repair.llm, "call", Script(rewrite="", locate=head)):
            _v, log = repair.settle(s, {"s03": {"unsupported_claims": ["headline claim"]}}, object(), user_id="t")
        self.assertEqual(list(log["held"]), ["s03"])
        self.assertEqual(c["stories"][2]["headline"], head)                               # held, not edited

    def test_a_story_body_whose_only_fact_sentence_fails_is_held(self):
        s, c = self.store([{}])
        c["stories"][3]["body"] = "Helio Labs released product 4."
        c["splits"] = [x for x in c["splits"] if x["path"] != "story.s04.body"] + [{"path": "story.s04.body", "runs": [{"text": "Helio Labs released product 4.", "class": "fact"}]}]
        with unittest.mock.patch.object(repair.llm, "call", Script(rewrite="", locate="Helio Labs released product 4")):
            _v, log = repair.settle(s, {"s04": {"unsupported_claims": ["x"]}}, object(), user_id="t")
        self.assertIn("s04", log["held"])

    def test_a_count_naming_a_held_story_is_removed_not_published(self):
        s, c = self.store([])
        c["thread"]["lines"][0] = "Two releases gave each agent its own branch."
        kept_ids = {sid: item for sid, item in composer.story_ids(raw_day(5)).items() if sid != "s01"}
        log = {"removed": []}
        repair.drop_failing_counts(s, kept_ids, log)
        self.assertEqual(c["thread"]["lines"][0], "")
        self.assertGreaterEqual(len(log["removed"]), 1)
        self.assertEqual(counts_mod.count_failures(c, kept_ids, composer.prose_fields(c)), [])


class ReviewFixes(unittest.TestCase):
    def test_a_first_pass_split_that_hides_a_sentence_is_graded_whole(self):
        framing = {"thread": "T.", "threadSplit": None, "items": [{"id": "a", "why": "X launched Y. X also raised $9B from Z.",
                                                                  "split": {"why": [{"text": "X launched Y.", "class": "fact"}]}}]}
        store = repair.FramingStore(framing, {"items": [{"id": "a", "source_excerpt": "e"}]}, object(), user_id="t")
        graded = store._graded_framing()
        self.assertEqual("".join(r["text"] for r in graded["items"][0]["split"]["why"]), "X launched Y. X also raised $9B from Z.")
        self.assertEqual(framing["items"][0]["split"]["why"], [{"text": "X launched Y.", "class": "fact"}])      # the stored framing is untouched

    def test_a_field_called_wholly_interpretation_is_graded_as_a_fact_too(self):
        self.assertEqual(repair.graded_runs("A claim.", [{"text": "A claim.", "class": "interpretation"}]), [{"text": "A claim.", "class": "fact"}])

    def test_a_rewritten_sentence_is_always_a_fact_whatever_the_writer_called_it(self):
        def call(client, **kw):
            return json.dumps({"text": "Now 9,000 repositories.", "runs": [{"text": "Now 9,000 repositories.", "class": "interpretation"}]}), NS(), 0.0
        with unittest.mock.patch.object(repair.llm, "call", call):
            text, runs, _ = repair.rewrite_sentence(object(), "s", "f", "c", "e", user_id="t")
        self.assertEqual(runs, [{"text": "Now 9,000 repositories.", "class": "fact"}])

    def test_an_unusable_rewrite_removes_the_sentence_at_once(self):
        store = Store({"p": "Revenue doubled. Another."}, [flagged()])
        with unittest.mock.patch.object(repair.llm, "call", Script(bad_rewrite=True, locate="Revenue")):
            _v, log = repair.settle(store, flagged("Revenue doubled"), object(), user_id="t")
        self.assertEqual(store.fields["p"], "Another.")
        self.assertEqual(len(log["removed"]), 1)

    def test_a_claim_that_survives_because_the_wrong_sentence_was_removed_holds_the_story(self):
        store = Store({"s01.why": "Revenue doubled. Another.", "s01.x": "Other sentence."}, [flagged("Revenue doubled"), flagged("Revenue doubled")],
                      owner_of=lambda p: "u", bearing=lambda p, t, r: False)
        store.units = lambda: ["u"]
        script = Script(rewrite="", locate="Another")                                # the second reader names the wrong sentence every time
        with unittest.mock.patch.object(repair.llm, "call", script):
            _v, log = repair.settle(store, flagged("Revenue doubled"), object(), user_id="t")
        self.assertIn("u", log["held"])                                              # the claim is still there: the story is held, not published

    def test_a_surviving_claim_in_the_essay_or_week_is_unlocatable_and_stops_the_week(self):
        store = Store({"p": "Revenue doubled. Another."}, [flagged("Revenue doubled"), flagged("Revenue doubled")])
        store.units = lambda: ["u"]
        with unittest.mock.patch.object(repair.llm, "call", Script(rewrite="", locate="Another")):
            _v, log = repair.settle(store, flagged("Revenue doubled"), object(), user_id="t")
        self.assertTrue(log["unlocatable"])

    def test_an_agreed_flag_with_no_sentence_to_name_is_recorded_not_dropped(self):
        store = Store({}, [])
        _v, log = None, None
        with unittest.mock.patch.object(repair.llm, "call", Script(agrees=True)):
            _v, log = repair.settle(store, flagged("Revenue doubled"), object(), user_id="t")
        self.assertEqual(len(log["unlocatable"]), 1)


class Stores(unittest.TestCase):
    def test_putting_a_composed_field_updates_its_split_and_the_text(self):
        c = valid(5)
        s = repair.ComposedStore(c, raw_day(5), {}, object(), user_id="t")
        s.put("closing.summary", "New one. New two.", [{"text": "New one. New two.", "class": "fact"}])
        self.assertEqual(c["closing"]["summary"], "New one. New two.")
        self.assertEqual(composer.split_map(c)["closing.summary"], [{"text": "New one. New two.", "class": "fact"}])

    def test_the_week_unit_covers_thread_closing_ties_titles_and_dividers(self):
        s = repair.ComposedStore(valid(5), raw_day(5), {}, object(), user_id="t")
        week = s.paths("week")
        for needed in ("thread.body", "thread.line.1", "closing.summary", "closing.oneThing", "stories.title", "divider.s03", "story.s01.tie"):
            self.assertIn(needed, week)

    def test_putting_the_first_pass_thread_updates_its_split(self):
        framing = {"thread": "Old.", "threadSplit": None, "items": []}
        s = repair.FramingStore(framing, {"items": []}, object(), user_id="t")
        s.put("thread", "New.", [{"text": "New.", "class": "fact"}])
        self.assertEqual((framing["thread"], framing["threadSplit"]), ("New.", [{"text": "New.", "class": "fact"}]))

    def test_a_story_body_whose_only_fact_sentence_fails_is_held_by_the_body_rule_not_the_headline_rule(self):
        c = valid(5)
        c["stories"][3]["body"] = "Zorblax unique body statement."
        c["splits"] = [x for x in c["splits"] if x["path"] != "story.s04.body"] + [{"path": "story.s04.body", "runs": [{"text": "Zorblax unique body statement.", "class": "fact"}]}]
        s = repair.ComposedStore(c, raw_day(5), {}, object(), user_id="t")
        s.grade = lambda: ({"s04": {"unsupported_claims": []}}, 0.0)
        with unittest.mock.patch.object(repair.llm, "call", Script(rewrite="", locate="Zorblax")):
            _v, log = repair.settle(s, {"s04": {"unsupported_claims": ["x"]}}, object(), user_id="t")
        self.assertEqual(list(log["held"]), ["s04"])
        self.assertIn("story.s04.body", log["held"]["s04"][0])


if __name__ == "__main__":
    unittest.main()
