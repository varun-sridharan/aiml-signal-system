"""Neutralise one style rule (or composer rule) at a time and count the failures in its own tests.

    python autopilot/evidence/2026-10-21/stepC-mutate.py <name>
"""
import sys, unittest
sys.path.insert(0, ".")
from agents import composer, style_check as sc

which = sys.argv[1]
target = "tests.test_style_check"
if which == "rule1": sc.opening_failures = lambda p: []; sc._NUMBER = type("N", (), {"search": staticmethod(lambda t: True)})(); names = [f"{target}.Rule1Opening"]
elif which == "rule2": sc.subject_failures = lambda s: []; names = [f"{target}.Rule2Subject"]
elif which == "rule3": sc.restating_failures = lambda s: []; names = [f"{target}.Rule3Restating"]
elif which == "rule4": sc.comparison_failures = lambda s: []; names = [f"{target}.Rule4Comparisons"]
elif which == "rule5": sc.stage_direction_failures = lambda s: []; names = [f"{target}.Rule5StageDirections"]
elif which == "edition_skips_closing": 
    orig = sc.edition_style_failures
    sc.edition_style_failures = lambda ed: orig({**ed, "closing": {}}); names = [f"{target}.WholeEdition"]
elif which == "slots_off": composer.slot_failures = lambda c, ids: []; names = ["tests.test_composer.SlotRules"]
elif which == "coverage_off": composer.coverage_failures = lambda c: []; names = ["tests.test_composer.Splits"]
elif which == "faithfulness_off": composer.faithfulness_failures = lambda v, w: []; names = ["tests.test_composer.Faithfulness"]
elif which == "completeness_off": composer.completeness_failures = lambda p, v, w: []; names = ["tests.test_composer.Faithfulness"]
elif which == "essay_sees_everything":
    orig_units = composer.grading_units
    def wide(c, raw, published=None):
        p = orig_units(c, raw, published)
        for u in p["items"]:
            if u["id"] == "essay": u["ground_truth_excerpt"] = "\n\n".join(e["source_excerpt"] for e in p["thread"]["ground_truth_excerpt_union"])
        return p
    composer.grading_units = wide; names = ["tests.test_composer.GradingUnits"]
elif which == "revision_off": composer.revisable_problems = lambda c, ids: {}; names = ["tests.test_composer.Revision"]
elif which == "revision_unbounded": composer.MAX_REVISIONS = 5; names = ["tests.test_composer.Revision"]
elif which == "revision_accepts_unlisted":
    orig_set = composer.set_field
    import json as _j
    _real_revise = composer.revise
    def revise_any(client, system_prompt, profile, raw_day, framing, window_label, published, composed, problems, *, conn=None):
        return _real_revise(client, system_prompt, profile, raw_day, framing, window_label, published, composed, {**{"thread.body": []}, **problems}, conn=conn)
    composer.revise = revise_any; names = ["tests.test_composer.Revision"]
elif which == "fact_floor_off": composer.fact_floor_failures = lambda c: []; names = ["tests.test_composer.ReviewFixes", "tests.test_composer.FactFloorIsRevisable"]
elif which == "arithmetic_off": sc.arithmetic_failures = lambda s: []; names = ["tests.test_style_check.Rule4Comparisons"]
elif which == "headline_fields_unchecked":
    orig_find = sc.edition_style_findings
    sc.edition_style_findings = lambda ed: [(p, l) for p, l in orig_find(ed) if not (p.endswith(".headline") or p.endswith(".tie") or p.startswith(("divider.", "stories.title", "learning.note.")))]; names = ["tests.test_style_check.WholeEdition"]
elif which == "grader_note_off": composer.GRADER_NOTE = ""; names = ["tests.test_composer.Faithfulness"]
r = unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromNames(names))
print(which, "FAILURES:", len(r.failures) + len(r.errors))
