"""Reader's canonicalisation gate, and proof that it can fail.

    python -m unittest tests.test_canonical_gate -v

A gate that has never been seen red proves nothing. Each test here breaks
canonicalisation in one specific way and checks the gate notices, naming the pair that
should catch it. No database, no network, no model.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import excerpts  # noqa: E402

_spec = importlib.util.spec_from_file_location("run_canonical", REPO / "data" / "evals" / "canonical" / "run_canonical.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def failing_ids(canonicalise) -> set[str]:
    return {line.split(":", 1)[0] for line in gate.evaluate(canonicalise)}


class CanonicalGate(unittest.TestCase):
    def test_the_real_canonicaliser_passes(self):
        self.assertEqual(gate.evaluate(), [])

    def test_the_set_has_planted_near_duplicates_and_is_marked_draft(self):
        data = gate.load_pairs()
        self.assertIn("draft, awaiting Varun's review", data["status"])
        planted = [p for p in data["pairs"] if p.get("planted")]
        self.assertGreaterEqual(len(planted), 1)
        self.assertTrue(all(p["expect"] == "different" for p in planted))
        self.assertTrue(any(p["expect"] == "same" for p in data["pairs"]))

    def test_dropping_the_whole_query_merges_articles_named_by_query(self):
        def drops_query(url):
            return excerpts.canonicalise(url).split("?")[0]
        self.assertTrue({"PLANTED-near-duplicate-query-id", "query-meaning"} <= failing_ids(drops_query))

    def test_keeping_tracking_parameters_leaves_duplicates(self):
        def keeps_tracking(url):
            base = excerpts.canonicalise(url)
            return base + (("?" + urlsplit(url.strip()).query) if urlsplit(url.strip()).query else "")
        self.assertTrue({"utm", "click-ids", "everything-at-once"} <= failing_ids(keeps_tracking))

    def test_folding_path_case_merges_different_paths(self):
        def folds_case(url):
            return excerpts.canonicalise(url).lower()
        self.assertIn("path-case", failing_ids(folds_case))

    def test_collapsing_to_the_host_merges_everything_on_a_site(self):
        def host_only(url):
            return "https://" + urlsplit(excerpts.canonicalise(url)).netloc
        ids = failing_ids(host_only)
        self.assertTrue({"PLANTED-near-duplicate-ids", "PLANTED-near-duplicate-slugs", "month"} <= ids)

    def test_a_canonicaliser_that_changes_nothing_fails_the_collapse_pairs(self):
        ids = failing_ids(lambda url: url)
        self.assertTrue({"scheme", "www", "utm", "trailing-slash", "arxiv-version"} <= ids)

    def test_losing_the_arxiv_rule_leaves_one_paper_in_several_places(self):
        def no_arxiv_rule(url):
            return excerpts.canonicalise(url.replace("arxiv.org", "arxiv-rule-off.test"))
        self.assertTrue({"arxiv-version", "arxiv-pdf", "arxiv-html"} <= failing_ids(no_arxiv_rule))


if __name__ == "__main__":
    unittest.main(verbosity=2)
