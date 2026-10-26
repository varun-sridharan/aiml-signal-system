"""The citation gate: reading the answer, the code checks, the two model checks, and holding stories (Run 4, Step D)."""

import json
import unittest
import unittest.mock
from types import SimpleNamespace as NS

from agents import citegate, composer, composer_cited
from tests.test_composer import raw_day, valid

DOCS = [{"id": "s01", "text": "Helio Labs released Orbit 1.0 on October 14, 2026. The SDK cut merge conflicts from 31 percent of runs to 9 percent across 212 repositories."},
        {"id": "s02", "text": "Atlas-3 scores 71.2% on SWE-Verify and 58.4% on Terminal-Ops. It ships with a 400K-token context window."},
        {"id": "s03", "text": "Parcel 2.0 resumes crashed runs from their last completed step."}]


def cite(doc, passage_start, passage_end=None):
    text = DOCS[doc]["text"]
    end = passage_end if passage_end is not None else text.index(".", passage_start) + 1
    return NS(type="char_location", document_index=doc, cited_text=text[passage_start:end], start_char_index=passage_start, end_char_index=end)


def block(text, *cites):
    return NS(type="text", text=text, citations=list(cites))


class Reading(unittest.TestCase):
    def test_sections_sentences_and_their_citations(self):
        blocks = [block("@@ story.s01.body\n"), block("Helio Labs released Orbit 1.0 on October 14, 2026.", cite(0, 0)), block(" "),
                  block("~ That is a useful release for small teams."), block(" "),
                  block("The SDK cut conflicts.", cite(0, 52)), block("\n@@ story.s02.body\n"), block("Atlas-3 ships a 400K-token window.", cite(1, 65))]
        text, spans = citegate.read_blocks(blocks)
        fields = citegate.split_fields(text)
        self.assertEqual(list(fields), ["story.s01.body", "story.s02.body"])
        sents = citegate.field_sentences("story.s01.body", fields["story.s01.body"], text, spans)
        self.assertEqual([s["text"] for s in sents], ["Helio Labs released Orbit 1.0 on October 14, 2026.", "That is a useful release for small teams.", "The SDK cut conflicts."])
        self.assertEqual([bool(s["citations"]) for s in sents], [True, False, True])
        self.assertEqual([s["interpretation"] for s in sents], [False, True, False])

    def test_a_sentence_takes_only_the_citations_of_the_blocks_that_overlap_it(self):
        blocks = [block("@@ a\n"), block("One cited sentence.", cite(0, 0)), block(" Two uncited sentence here.")]
        text, spans = citegate.read_blocks(blocks)
        sents = citegate.field_sentences("a", citegate.split_fields(text)["a"], text, spans)
        self.assertEqual([bool(s["citations"]) for s in sents], [True, False])


def sentence(path, text, *cites, interpretation=False):
    return {"path": path, "text": text, "interpretation": interpretation,
            "citations": [{"type": c.type, "document_index": c.document_index, "cited_text": c.cited_text, "start": c.start_char_index, "end": c.end_char_index} for c in cites]}


class CodeChecks(unittest.TestCase):
    def check(self, s, **kw):
        return citegate.code_failures(s, DOCS, **kw)

    def test_a_cited_sentence_in_its_own_story_passes(self):
        self.assertEqual(self.check(sentence("story.s01.body", "Helio released Orbit.", cite(0, 0))), [])

    def test_an_uncited_factual_sentence_fails(self):
        self.assertIn("uncited", self.check(sentence("story.s01.body", "Helio released Orbit."))[0])

    def test_a_marked_sentence_needs_no_citation_but_may_not_carry_a_figure(self):
        self.assertEqual(self.check(sentence("story.s01.body", "That is useful.", interpretation=True)), [])
        self.assertIn("carries a figure", self.check(sentence("story.s01.body", "That saves 40 hours.", interpretation=True))[0])

    def test_a_passage_the_document_does_not_contain_fails(self):
        bad = NS(type="char_location", document_index=0, cited_text="Helio Labs raised 965 billion dollars.", start_char_index=0, end_char_index=38)
        self.assertIn("not in s01's stored excerpt", self.check(sentence("story.s01.body", "Helio raised money.", bad))[0])

    def test_a_passage_at_the_wrong_offsets_fails(self):
        moved = NS(type="char_location", document_index=0, cited_text=DOCS[0]["text"][:20], start_char_index=5, end_char_index=25)
        self.assertIn("not where the citation says", self.check(sentence("story.s01.body", "Helio.", moved))[0])

    def test_a_story_field_may_cite_only_its_own_document(self):
        self.assertIn("may not draw on", self.check(sentence("story.s01.body", "Atlas ships.", cite(1, 0)))[0])

    def test_week_fields_may_cite_any_document_but_the_essay_only_the_stories_it_lists(self):
        self.assertEqual(self.check(sentence("thread.body", "Atlas ships.", cite(1, 0))), [])
        s = sentence("learning.para.1", "Atlas ships.", cite(1, 0))
        self.assertIn("may not draw on", self.check(s, drawn_from=["s01", "s03"])[0])
        self.assertEqual(self.check(s, drawn_from=["s01", "s02"]), [])

    def test_a_citation_to_a_document_that_was_not_sent_fails(self):
        c = NS(type="char_location", document_index=9, cited_text="x", start_char_index=0, end_char_index=1)
        self.assertIn("document 9", self.check(sentence("thread.body", "Atlas ships.", c))[0])


class Scripted:
    """llm.call stand-in: answers the first-check and second-check calls from a table keyed by the sentence text."""

    def __init__(self, supports, unsupported):
        self.supports, self.unsupported, self.calls = supports, unsupported, []

    def __call__(self, client, **kw):
        qs = json.loads(kw["messages"][0]["content"])["questions"]
        self.calls.append((kw["system"][:20], len(qs)))
        if "second" in kw["system"]:
            body = {"answers": [{"id": q["id"], "unsupported": self.unsupported.get(q["sentence"], [])} for q in qs]}
        else:
            body = {"answers": [{"id": q["id"], "supports": self.supports.get((q["sentence"], q["passage"]), False), "why": "w"} for q in qs]}
        return json.dumps(body), NS(), 0.001


class Verification(unittest.TestCase):
    def run_verify(self, sentences, supports, unsupported):
        script = Scripted(supports, unsupported)
        with unittest.mock.patch.object(citegate.llm, "call", script):
            out, cost = citegate.verify(object(), sentences, user_id="t")
        return out, script

    def test_one_supporting_citation_is_enough_and_no_second_check_runs(self):
        s = sentence("story.s01.body", "Helio released Orbit.", cite(0, 0), cite(0, 52))
        out, script = self.run_verify([s], {("Helio released Orbit.", s["citations"][0]["cited_text"]): True}, {})
        self.assertTrue(out[0]["verdict"]["supported"])
        self.assertEqual(len(script.calls), 1)

    def test_a_rejection_stands_only_if_the_second_check_also_says_no(self):
        a = sentence("story.s01.body", "Helio cut conflicts to 9 percent.", cite(0, 52))
        b = sentence("story.s02.body", "Atlas scores 58.4% on SWE-Verify.", cite(1, 0))
        out, script = self.run_verify([a, b], {}, {"Atlas scores 58.4% on SWE-Verify.": ["58.4% on SWE-Verify"]})
        self.assertTrue(out[0]["verdict"]["supported"])          # first said no, the second reader found nothing unsupported
        self.assertFalse(out[1]["verdict"]["supported"])         # both said no
        self.assertEqual(out[1]["verdict"]["second"]["unsupported"], ["58.4% on SWE-Verify"])
        self.assertEqual(len(script.calls), 2)

    def test_sentences_with_code_failures_and_marked_sentences_are_not_sent_to_a_model(self):
        s = sentence("story.s01.body", "Helio released Orbit.", cite(0, 0))
        s["code_failures"] = ["x"]
        m = sentence("story.s01.body", "Useful.", interpretation=True)
        out, script = self.run_verify([s, m], {}, {})
        self.assertEqual(script.calls, [])
        self.assertNotIn("verdict", out[0])

    def test_a_missing_answer_counts_as_a_no(self):
        s = sentence("story.s01.body", "Helio released Orbit.", cite(0, 0))
        script = Scripted({}, {})
        script.__call__ = None
        with unittest.mock.patch.object(citegate.llm, "call", lambda client, **kw: (json.dumps({"answers": []}), NS(), 0.0)):
            out, _ = citegate.verify(object(), [s], user_id="t")
        self.assertFalse(out[0]["verdict"]["supported"])


class Holding(unittest.TestCase):
    IDS = ["s01", "s02", "s03", "s04", "s05"]

    def sents(self, bad=()):
        out = []
        for path in ["thread.body", "closing.summary"] + [f"story.{sid}.body" for sid in self.IDS]:
            s = {"path": path, "text": "t", "interpretation": False, "citations": [1], "code_failures": [], "verdict": {"supported": True, "first": [], "second": None}}
            if path in bad:
                s["verdict"] = {"supported": False, "first": [], "second": {"unsupported": ["claim"]}}
            out.append(s)
        return out

    def test_a_clean_edition_publishes_every_story(self):
        d = citegate.evaluate(self.sents(), self.IDS)
        self.assertEqual((d["passing"], d["held"], d["blockers"], d["publishable"]), (self.IDS, {}, [], True))

    def test_a_failing_story_is_held_and_the_rest_publish_with_at_least_four(self):
        d = citegate.evaluate(self.sents(bad=("story.s03.body",)), self.IDS)
        self.assertEqual(list(d["held"]), ["s03"])
        self.assertEqual(d["passing"], ["s01", "s02", "s04", "s05"])
        self.assertTrue(d["publishable"])

    def test_below_four_stories_the_week_does_not_publish(self):
        d = citegate.evaluate(self.sents(bad=("story.s03.body", "story.s04.body")), self.IDS)
        self.assertEqual(len(d["passing"]), 3)
        self.assertFalse(d["publishable"])

    def test_a_failure_in_a_week_field_blocks_the_edition(self):
        d = citegate.evaluate(self.sents(bad=("thread.body",)), self.IDS)
        self.assertEqual(d["held"], {})
        self.assertEqual(len(d["blockers"]), 1)
        self.assertFalse(d["publishable"])

    def test_a_code_failure_holds_its_story_too(self):
        s = self.sents()
        s[3]["code_failures"] = ["story.s02.body: uncited factual sentence: 'x'"]
        d = citegate.evaluate(s, self.IDS)
        self.assertEqual(list(d["held"]), ["s02"])


def sections(composed, why="Why this matters."):
    """The writer's answer for a claim-gate `composed`: each prose field as a cited section (the first sentence of document 0 or its story's)."""
    blocks = []
    docs = {f"s0{i}": i - 1 for i in range(1, 6)}

    def put(path, text, doc):
        blocks.append(block(f"@@ {path}\n"))
        d = DOC5[doc]
        blocks.append(block(text, NS(type="char_location", document_index=doc, cited_text=d[:30], start_char_index=0, end_char_index=30)))
        blocks.append(block("\n"))

    for path, text in composer.prose_fields(composed).items():
        sid = path.split(".")[1] if path.startswith("story.") else None
        put(path, text, docs.get(sid, 0))
    for s in composed["stories"]:
        put(f"story.{s['id']}.hashtags", " ".join(s["hashtags"]), docs[s["id"]])
        put(f"story.{s['id']}.why", why, docs[s["id"]])
        if s.get("tie"):
            put(f"story.{s['id']}.tie.to", s["tie"]["to"], docs[s["id"]])
    put("learning.drawnFrom", " ".join(composed["learning"]["drawnFrom"]), 0)
    for n in composed["learning"]["notes"]:
        put(f"learning.note.{n['key']}.term", n["term"], 0)
    blocks.append(block("@@ counts\n" + "\n".join(f"{c['path']} | {c['phrase']} | {' '.join(c['stories'])}" for c in composed["counts"])))
    return blocks


DOC5 = [d["excerpt"] for d in composer.composer_items(raw_day(5))]


class WholeRun(unittest.TestCase):
    """compose_and_check with a scripted writer and a scripted verifier: the plumbing from sections to a publishable decision."""

    def run_it(self, composed, supports_everything=True, verify_no=()):
        day = raw_day(5)
        profile = {"user_id": "t", "role": "r", "goals": [], "interests": [], "preferences": {}}
        writes = []

        def call_message(client, **kw):
            writes.append(kw)
            return NS(content=sections(composed), stop_reason="end_turn", usage=NS()), NS(), 0.5

        def call(client, **kw):
            qs = json.loads(kw["messages"][0]["content"])["questions"]
            if "second" in kw["system"]:
                return json.dumps({"answers": [{"id": q["id"], "unsupported": ["claim"]} for q in qs]}), NS(), 0.001
            return json.dumps({"answers": [{"id": q["id"], "supports": not any(v in q["sentence"] for v in verify_no), "why": "w"} for q in qs]}), NS(), 0.001

        with unittest.mock.patch.object(composer_cited.llm, "call_message", call_message), unittest.mock.patch.object(citegate.llm, "call", call):
            result = composer_cited.compose_and_check(object(), "SYSTEM", profile, day, {"items": [{"id": f"item-{i}", "why": "W", "ninety": None} for i in range(1, 6)]},
                                                      "Oct 10 – Oct 18, 2026", None)
        return result, writes

    def test_a_clean_answer_is_publishable_and_every_story_passes(self):
        result, writes = self.run_it(valid(5))
        self.assertEqual(result["structural"], [])
        self.assertTrue(result["decision"]["publishable"], result["decision"]["blockers"][:3])
        self.assertEqual(result["decision"]["passing"], [f"s0{i}" for i in range(1, 6)])
        self.assertEqual(len(writes), 1)
        docs = [b for b in writes[0]["messages"][0]["content"] if b["type"] == "document"]
        self.assertEqual(len(docs), 5)
        self.assertTrue(all(d["citations"] == {"enabled": True} for d in docs))
        self.assertNotIn("output_config", {k for k in writes[0] if k == "output_config" and "format" in writes[0][k]})

    def test_a_story_whose_sentence_the_verifier_rejects_is_held(self):
        c = valid(5)
        c["stories"][2]["body"] = "Helio Labs released product 3 on October 13, 2026. It reached 203 repositories. The company measured NOBLE only small repositories."
        result, _ = self.run_it(c, verify_no=("NOBLE",))
        self.assertEqual(list(result["decision"]["held"]), ["s03"])
        self.assertTrue(result["decision"]["publishable"])
        edition = composer_cited.assemble_edition(result, raw_day(5), {"items": [{"id": f"item-{i}", "why": "W", "ninety": None} for i in range(1, 6)]},
                                                  stats={}, source_minutes=3)
        self.assertEqual([s["id"] for s in edition["stories"]], ["s01", "s02", "s04", "s05"])
        self.assertEqual([h["id"] for h in edition["held"]], ["s03"])
        self.assertEqual(edition["held"][0]["why"], "could not be verified")
        self.assertEqual(edition["faithfulness"]["gate"], "citations")
        self.assertIn("1 story was held", edition["learning"]["howMade"])
        self.assertTrue(all(s["checked"] for s in edition["stories"]))

    def test_links_ties_and_dividers_to_a_held_story_go_with_it(self):
        c = valid(5)
        held = composer_cited.without_held(c, {"s02"})
        self.assertNotIn("[[#s02|", held["thread"]["body"])
        self.assertIn("it released product 2", held["thread"]["body"])
        self.assertIsNone(held["stories"][0]["tie"])                       # s01's tie pointed at s02
        c["dividers"] = [{"before": "s03", "label": "Smaller releases"}]
        self.assertEqual(composer_cited.without_held(c, {"s03"})["dividers"], [])

    def test_a_missing_section_is_reported_not_guessed(self):
        c = valid(5)
        draft = composer_cited.draft_from(*citegate.read_blocks(sections(c)))
        del draft["fields"]["closing.summary"]
        _, missing = composer_cited.build_composed(draft, composer.story_ids(raw_day(5)))
        self.assertEqual(missing, ["closing.summary: the section is missing or empty"])


class ReviewFixes(unittest.TestCase):
    def spans(self, text):
        return [text[a:b] for a, b in citegate.sentence_spans(text)]

    def test_sentence_splitting_keeps_abbreviations_decimals_and_versions_together(self):
        self.assertEqual(self.spans("Dr. Smith said so. Acme bought it."), ["Dr. Smith said so.", "Acme bought it."])
        self.assertEqual(self.spans("U.S. labs shipped v1.5 today. It ran 3.5x faster."), ["U.S. labs shipped v1.5 today.", "It ran 3.5x faster."])
        self.assertEqual(self.spans("One thing.\n\nAnother thing."), ["One thing.", "Another thing."])

    def test_a_marked_sentence_that_cites_is_still_held_to_its_citations(self):
        bad = NS(type="char_location", document_index=0, cited_text="Helio Labs raised 965 billion dollars.", start_char_index=0, end_char_index=38)
        out = citegate.code_failures(sentence("story.s01.body", "That is useful.", bad, interpretation=True), DOCS)
        self.assertTrue(any("not in s01's stored excerpt" in f for f in out), out)

    def test_a_spelled_out_count_in_a_marked_sentence_fails(self):
        out = citegate.code_failures(sentence("thread.body", "Nine labs agreed.", interpretation=True), DOCS)
        self.assertTrue(any("carries a figure" in f for f in out))
        self.assertEqual(citegate.code_failures(sentence("thread.body", "Teams should try the first step.", interpretation=True), DOCS), [])

    def test_a_tie_may_cite_the_other_story_but_a_story_field_may_not(self):
        self.assertEqual(citegate.code_failures(sentence("story.s01.tie", "Atlas ships too.", cite(1, 0)), DOCS), [])
        self.assertTrue(citegate.code_failures(sentence("story.s01.body", "Atlas ships too.", cite(1, 0)), DOCS))

    def test_an_essay_with_no_drawn_from_may_cite_nothing(self):
        self.assertTrue(citegate.code_failures(sentence("learning.para.1", "Atlas ships.", cite(1, 0)), DOCS, drawn_from=[]))

    def test_a_one_character_passage_supports_nothing(self):
        c = NS(type="char_location", document_index=0, cited_text="H", start_char_index=0, end_char_index=1)
        self.assertTrue(citegate.code_failures(sentence("story.s01.body", "Helio released Orbit.", c), DOCS))

    def marked(self, answers, sentences):
        def call(client, **kw):
            qs = json.loads(kw["messages"][0]["content"])["questions"]
            return json.dumps({"answers": [{"id": q["id"], "states_fact": answers.get(q["line"], True)} for q in qs]}), NS(), 0.0
        with unittest.mock.patch.object(citegate.llm, "call", call):
            citegate.check_marked(object(), sentences, user_id="t")
        return sentences

    def test_a_marked_sentence_that_states_a_fact_is_an_uncited_fact(self):
        a = sentence("story.s01.body", "Acme was acquired by Helio.", interpretation=True)
        b = sentence("story.s01.body", "Teams should try this.", interpretation=True)
        self.marked({"Acme was acquired by Helio.": True, "Teams should try this.": False}, [a, b])
        self.assertTrue(a.get("code_failures"))
        self.assertFalse(b.get("code_failures"))

    def test_a_judgement_line_needs_both_readers_to_say_it_states_no_fact(self):
        a = sentence("story.s01.body", "Teams should try this.", interpretation=True)
        seen = []

        def call(client, **kw):
            qs = json.loads(kw["messages"][0]["content"])["questions"]
            seen.append(kw["model"])
            return json.dumps({"answers": [{"id": q["id"], "states_fact": kw["model"] == citegate.llm.FRAMER_MODEL} for q in qs]}), NS(), 0.0
        with unittest.mock.patch.object(citegate.llm, "call", call):
            citegate.check_marked(object(), [a], user_id="t")
        self.assertEqual(seen, [citegate.llm.CHECK_MODEL, citegate.llm.FRAMER_MODEL])
        self.assertTrue(a.get("code_failures"))

    def test_an_unreadable_judgement_check_fails_closed(self):
        a = sentence("story.s01.body", "Teams should try this.", interpretation=True)
        with unittest.mock.patch.object(citegate.llm, "call", lambda client, **kw: ("not json", NS(), 0.0)):
            citegate.check_marked(object(), [a], user_id="t")
        self.assertTrue(a.get("code_failures"))

    def test_the_second_check_is_a_different_model_from_the_first(self):
        models = []

        def call(client, **kw):
            models.append(kw["model"])
            qs = json.loads(kw["messages"][0]["content"])["questions"]
            if "second" in kw["system"]:
                return json.dumps({"answers": [{"id": q["id"], "unsupported": []} for q in qs]}), NS(), 0.0
            return json.dumps({"answers": [{"id": q["id"], "supports": False, "why": "w"} for q in qs]}), NS(), 0.0
        s = sentence("story.s01.body", "Helio released Orbit.", cite(0, 0))
        with unittest.mock.patch.object(citegate.llm, "call", call):
            citegate.verify(object(), [s], user_id="t")
        self.assertEqual(models, [citegate.llm.CHECK_MODEL, citegate.llm.FRAMER_MODEL])

    def test_a_garbled_verifier_answer_holds_the_story_and_does_not_crash(self):
        s = sentence("story.s01.body", "Helio released Orbit.", cite(0, 0))
        with unittest.mock.patch.object(citegate.llm, "call", lambda client, **kw: (json.dumps({"answers": [{"nope": 1}]}), NS(), 0.0)):
            out, _ = citegate.verify(object(), [s], user_id="t")
        self.assertFalse(out[0]["verdict"]["supported"])

    def test_structural_failures_block_the_edition_even_when_every_story_passes(self):
        sents = Holding().sents()
        d = citegate.evaluate(sents, Holding.IDS, structural=["thread.body: 70 words, 80 to 120 are required"])
        self.assertFalse(d["publishable"])
        self.assertEqual(len(d["passing"]), 5)


class AfterAHold(unittest.TestCase):
    def test_a_count_that_names_a_held_story_or_totals_the_old_edition_blocks_the_edition(self):
        c = valid(5)
        c["thread"]["lines"][0] = "Three of this week's five stories shipped products."
        c["counts"] = [{"path": "thread.line.1", "phrase": "Three of this week's five stories", "stories": ["s01", "s02", "s03"]}]
        ids = composer.story_ids(raw_day(5))
        out = composer_cited.hold_checks(c, ids, {"held": {"s01": ["x"]}})
        # the count's entry naming the held story is dropped with it, so the sentence now states a count nobody listed: still a failure
        self.assertTrue(any("no entry in `counts`" in line for line in out), out)
        self.assertEqual(composer_cited.hold_checks(c, ids, {"held": {}}), [])
        # a count whose stories all survive, but whose total is the old edition's, is a failure of its own
        c["counts"] = [{"path": "thread.line.1", "phrase": "Three of this week's five stories", "stories": ["s02", "s03", "s04"]}]
        out = composer_cited.hold_checks(c, ids, {"held": {"s01": ["x"]}})
        self.assertTrue(any("total of 5 but this edition has 4" in line for line in out), out)

    def test_a_held_label_state_fact_blocks_through_the_judgement_check(self):
        c = valid(5)
        c["dividers"] = [{"before": "s03", "label": "Acme fraud scandal"}]
        day = raw_day(5)
        blocks = sections(c)
        profile = {"user_id": "t"}

        def call(client, **kw):
            qs = json.loads(kw["messages"][0]["content"])["questions"]
            if "judgement" in kw["system"]:
                return json.dumps({"answers": [{"id": q["id"], "states_fact": "scandal" in q["line"]} for q in qs]}), NS(), 0.0
            return json.dumps({"answers": [{"id": q["id"], "supports": True, "why": "w"} for q in qs]}), NS(), 0.0
        blocks.insert(0, block("@@ divider.s03\nAcme fraud scandal\n"))
        draft = composer_cited.draft_from(*citegate.read_blocks(blocks))
        docs = composer_cited.documents(day)
        composed, missing = composer_cited.build_composed(draft, composer.story_ids(day))
        with unittest.mock.patch.object(citegate.llm, "call", call):
            result = composer_cited.check_draft(object(), profile, draft, composed, missing, composer.story_ids(day), docs)
        self.assertFalse(result["decision"]["publishable"])
        self.assertTrue(any("divider.s03" in b and "marked as judgement but states a checkable fact" in b for b in result["decision"]["blockers"]), result["decision"]["blockers"])


class NoCitations(unittest.TestCase):
    def test_an_answer_that_hardly_cites_is_asked_for_once_more(self):
        day, c = raw_day(5), valid(5)
        calls = []

        def call_message(client, **kw):
            calls.append(kw)
            blocks = sections(c)
            if len(calls) == 1:
                blocks = [block(b.text) for b in blocks]          # the same text, no citations at all
            return NS(content=blocks, stop_reason="end_turn", usage=NS()), NS(), 0.5

        def call(client, **kw):
            qs = json.loads(kw["messages"][0]["content"])["questions"]
            return json.dumps({"answers": [{"id": q["id"], "supports": True, "why": "w"} for q in qs]}), NS(), 0.0
        with unittest.mock.patch.object(composer_cited.llm, "call_message", call_message), unittest.mock.patch.object(citegate.llm, "call", call):
            r = composer_cited.compose_and_check(object(), "SYSTEM", {"user_id": "t", "role": "r", "goals": [], "interests": [], "preferences": {}}, day,
                                                 {"items": [{"id": f"item-{i}", "why": "W", "ninety": None} for i in range(1, 6)]}, "Oct 10 – Oct 18, 2026", None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(r["revision"]["rewrites_for_no_citations"], 1)
        self.assertTrue(r["decision"]["publishable"])

    def test_the_request_asks_for_citations_and_sends_documents(self):
        req = composer_cited.compose_request("SYSTEM", {"role": "r", "goals": [], "interests": []}, raw_day(5), {"items": []}, "L", None)
        self.assertIn("answer_through_citations", req["messages"][0]["content"][-1]["text"])
        self.assertNotIn("format", req["output_config"])
        self.assertEqual(sum(1 for b in req["messages"][0]["content"] if b["type"] == "document"), 5)


if __name__ == "__main__":
    unittest.main()
