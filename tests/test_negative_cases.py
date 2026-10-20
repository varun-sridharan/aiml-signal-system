"""The constructed planted-fabrication cases in data/evals/negative/ are well formed. Free; no model.

    python -m unittest tests.test_negative_cases -v

A negative case only tests the grader if the planted claim is built from words the excerpt really
contains (see negative-01's labels.json): a figure, name or date absent from the excerpt is caught
by Python's substring check and the case quietly stops testing the grader. This pins that.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "data" / "evals"))

import run_evals  # noqa: E402
from agents import gates  # noqa: E402

NEGATIVE = REPO / "data" / "evals" / "negative"
CASES = sorted(p.name for p in NEGATIVE.iterdir() if p.is_dir())


class NegativeCases(unittest.TestCase):
    def test_there_are_at_least_three_new_cases_and_the_runner_loads_them(self):
        self.assertGreaterEqual(len(CASES), 3)
        loaded = {c["name"] for c in run_evals.load_cases(None)}
        self.assertTrue(set(CASES) <= loaded)
        self.assertEqual(run_evals.load_cases("negative-02")[0]["name"], "negative-02")

    def test_each_case_has_exactly_one_planted_unit_built_from_the_excerpts_own_words(self):
        profile = run_evals.load_json(REPO / "config" / "profile.json")
        for name in CASES:
            case = run_evals.load_cases(name)[0]
            planted = {u: l for u, l in case["labels"]["units"].items() if not u.startswith("_") and l.get("expected_unsupported")}
            self.assertEqual(len(planted), 1, name)
            (unit, labels), = planted.items()
            item_in = next(i for i in case["input"]["items"] if i["id"] == unit)
            item_out = next(i for i in case["output"]["items"] if i["id"] == unit)
            for pattern in labels["expected_unsupported"]:
                self.assertIn(pattern.lower(), item_out["why"].lower(), f"{name}: the plant is not in the writing")
            # The planted figure/date/name is in the excerpt too (just attached to the wrong thing).
            # Every distinctive word of the plant is in the excerpt (just attached to the wrong thing).
            excerpt = item_in["source_excerpt"].lower()
            for word in labels["expected_unsupported"][0].split():
                self.assertIn(word.lower().rstrip(".,"), excerpt, f"{name}: {word!r} of the plant is absent from the excerpt")
            self.assertNotIn(item_out["why"], item_in["source_excerpt"], f"{name}: the writing is the excerpt")
            self.assertEqual(gates.structural_failures(profile, case["output"]), [], name)

    def test_every_split_reconstructs_its_field(self):
        for name in CASES:
            day = json.loads((NEGATIVE / name / "output.json").read_text())
            for item in day["items"]:
                for key, runs in item["split"].items():
                    self.assertEqual("".join(r["text"] for r in runs), item[key], f"{name}/{item['id']}/{key}")
            self.assertEqual("".join(r["text"] for r in day["threadSplit"]), day["thread"], name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
