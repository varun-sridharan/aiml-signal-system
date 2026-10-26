"""Run 7, Step D: the Plan table keeps short cells, and what a cell no longer carries lives under '### Row notes'."""

import re
import tempfile
import unittest
from pathlib import Path

from scripts import plan_format as pf

REPO = Path(__file__).resolve().parent.parent
PLAN = REPO / "plan" / "plan.md"


def words(html: str) -> int:
    return len(re.sub(r"<[^>]+>", " ", html).replace("&rarr;", "→").split())


class RealPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = pf.load_plan(PLAN)
        cls.ms = cls.model["milestones"][0]

    def test_no_cell_in_the_table_is_over_45_words(self):
        for row in self.ms["rows"]:
            for cell in ("activity", "outcome", "why"):
                self.assertLessEqual(words(row[cell]), 45, f"{row['activity']}: {cell}")

    def test_the_summaries_keep_to_their_limits(self):
        for row in self.ms["rows"]:
            self.assertLessEqual(words(row["activity"]), 8, row["activity"])
            self.assertLessEqual(words(row["outcome"]), 35, row["activity"])
            self.assertLessEqual(words(row["why"]), 30, row["activity"])

    def test_every_notes_link_has_an_anchor_that_exists_and_every_note_has_a_row(self):
        anchors = [n["anchor"] for n in self.ms["notes"]]
        linked = [m for row in self.ms["rows"] for m in re.findall(r'href="#(note-[a-z0-9-]+)"', row["outcome"])]
        self.assertGreater(len(linked), 5)
        self.assertEqual(sorted(linked), sorted(anchors))
        self.assertEqual(anchors, [pf.note_anchor(r["activity"]) for r in self.ms["rows"] if pf.note_anchor(r["activity"]) in anchors])

    def test_a_note_holds_the_cell_it_replaced_in_full(self):
        note = next(n for n in self.ms["notes"] if "deduplication" in n["title"])
        self.assertIn("Outcome, in full", note["html"])
        self.assertIn("text fingerprint", note["html"])
        self.assertIn("dedup_decisions", note["html"])

    def test_a_row_with_a_long_cell_in_the_page_links_to_its_notes(self):
        from fastapi.testclient import TestClient
        import app as site
        html = TestClient(site.app).get("/plan").text
        self.assertIn('id="m1-notes"', html)
        self.assertIn('data-toc="m1-notes"', html)
        for n in self.ms["notes"]:
            self.assertIn(f'id="{n["anchor"]}"', html)


class PlantedBreaks(unittest.TestCase):
    def errors(self, mutate) -> list:
        text = mutate(PLAN.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plan.md"
            p.write_text(text, encoding="utf-8")
            return [e.rule for e in pf.parse_plan(p)[1]]

    def messages(self, mutate) -> str:
        text = mutate(PLAN.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plan.md"
            p.write_text(text, encoding="utf-8")
            return " || ".join(e.message for e in pf.parse_plan(p)[1])

    def test_each_row_note_rule_fails_on_its_own_message(self):
        link = "(#note-reader-s-deduplication-gate)"
        self.assertIn(link, PLAN.read_text(encoding="utf-8"))
        self.assertIn("which has no '####' entry", self.messages(lambda t: t.replace(link, "(#note-missing)", 1)))
        self.assertIn("which is not its own note", self.messages(lambda t: t.replace(link, "(#note-the-framer-writes-in-varun-s-style)", 1)))
        self.assertIn("belongs to no row's Outcome link", self.messages(lambda t: t.replace("### Row notes\n", "### Row notes\n\n#### An orphan\n\nText.\n", 1)))

    def test_a_why_cell_over_45_words_fails(self):
        long = " ".join(["word"] * 46)
        self.assertIn("the why cell is over 45 words", self.messages(lambda t: t.replace("| M1 ports this unchanged.", f"| {long} M1 ports this unchanged.", 1)))

    def test_an_activity_cell_over_45_words_fails(self):
        long = " ".join(["word"] * 46)
        self.assertIn("the activity cell is over 45 words", self.messages(lambda t: t.replace("| The Framer agent |", f"| {long} |", 1)))

    def test_the_real_file_passes(self):
        self.assertEqual(self.errors(lambda t: t), [])

    def test_a_cell_over_45_words_fails(self):
        long = " ".join(["word"] * 46)
        self.assertIn("plan.cell-length", self.errors(lambda t: t.replace("| The Framer agent | ", f"| The Framer agent | {long} ", 1)))

    def test_a_link_with_no_note_fails(self):
        self.assertIn("plan.row-notes", self.errors(lambda t: t.replace("#### Storage layer, with the excerpt rule", "#### Something else entirely", 1)))

    def test_a_note_with_no_row_link_fails(self):
        self.assertIn("plan.row-notes", self.errors(lambda t: t.replace("### Row notes\n", "### Row notes\n\n#### An orphan\n\nText.\n", 1)))

    def test_notes_out_of_row_order_fail(self):
        def swap(t):
            a, b = "#### Storage layer, with the excerpt rule", "#### Reader — Scout and Gatekeeper merged"
            return t.replace(a, "@@A@@").replace(b, a).replace("@@A@@", b)
        self.assertIn("plan.row-notes", self.errors(swap))


if __name__ == "__main__":
    unittest.main()
