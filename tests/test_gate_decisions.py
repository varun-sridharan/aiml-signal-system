"""The three gate decisions of 2026-10-20 ("Three gate decisions after the closeout run").

    python -m unittest tests.test_gate_decisions -v

Free. Each test is written to fail when its change is undone: the RADAR rule, the dash folding, and
the retry switch (whose default is covered in tests/test_retry.py).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import framer, gates  # noqa: E402

NINETY = {"method": "A sparse probe over 4,000 traces.", "result": "It beats the baseline by 6 points.", "caveat": "Tested on one model family."}
VERDICT = {"claims": [{"claim": "c"}]}


def completeness(tags, entry):
    raw = {"items": [{"id": "a", "tags": tags}]}
    return gates.completeness_failures(raw, {"items": [{"id": "a", **entry}]}, {"a": VERDICT}, VERDICT)


class RadarPaperWithoutWhy(unittest.TestCase):
    def test_a_radar_story_with_a_full_summary_and_no_why_is_written(self):
        self.assertEqual(completeness(["RADAR"], {"why": None, "ninety": NINETY}), [])
        self.assertEqual(completeness(["DO THIS", "RADAR"], {"why": "", "ninety": NINETY}), [])

    def test_every_other_story_still_needs_its_why(self):
        for tags in (["KNOW"], ["DO THIS"], ["DO THIS", "KNOW"], []):
            self.assertEqual(len(completeness(tags, {"why": None, "ninety": NINETY})), 1, tags)

    def test_a_radar_summary_with_a_part_missing_or_blank_is_not_a_story(self):
        for broken in ({"method": "m", "result": "r"}, {**NINETY, "caveat": "  "}, {**NINETY, "result": ""}, {**NINETY, "method": ["a list"]}, {}, None, "text"):
            self.assertEqual(len(completeness(["RADAR"], {"why": None, "ninety": broken})), 1, broken)

    def test_a_radar_story_with_nothing_written_still_fails(self):
        self.assertEqual(len(completeness(["RADAR"], {"why": None, "ninety": None, "example": None, "connection": "x"})), 1)


class DashVariants(unittest.TestCase):
    def verdict(self, excerpt, span, claim="The model is sub-linear."):
        raw = {"claims": [{"claim": claim, "supported": True, "supporting_span": span}], "confidence": 0.9}
        return framer.normalise_verdict(raw, excerpt, "")

    def test_a_non_breaking_hyphen_in_the_excerpt_matches_a_plain_hyphen_in_the_quote(self):
        got = self.verdict("The scaling is sub‑linear in depth.", "sub-linear in depth")
        self.assertEqual(got["unsupported_claims"], [])
        self.assertTrue(got["claims"][0]["span_verified"])

    def test_it_works_the_other_way_round_and_for_every_dash(self):
        for dash in "‐‑‒–—―−﹣－":
            self.assertEqual(self.verdict("a sub-linear fit", f"sub{dash}linear fit")["unsupported_claims"], [], hex(ord(dash)))
            self.assertEqual(self.verdict(f"a sub{dash}linear fit", "sub-linear fit")["unsupported_claims"], [], hex(ord(dash)))

    def test_nothing_else_is_folded(self):
        for excerpt, span in (("sub linear fit", "sub-linear fit"),          # a space is not a hyphen
                              ("sublinear fit", "sub-linear fit"),             # a missing hyphen is a different word
                              ("a fit of 3 point", "a fit of 3 points"),       # a different word
                              ("the cafe", "the café"),                         # accents are not folded
                              ("it’s", "it's")):                           # nor quotes
            self.assertNotEqual(self.verdict(excerpt, span)["unsupported_claims"], [], (excerpt, span))

    def test_a_hyphenated_claim_is_not_made_up_by_the_folding(self):
        got = self.verdict("The scaling is linear.", "sub‑linear", claim="x")
        self.assertEqual(got["unsupported_claims"], ["x"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
