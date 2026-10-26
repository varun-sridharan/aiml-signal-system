"""Measure the claim gate's per-sentence repair on the golden day and the four planted-fabrication cases (Run 5, Step A).

    python data/evals/measure_settle.py --runs 2 --out autopilot/evidence/2026-10-27/stepA-measure

PAID: every call goes through agents/llm.py and is booked. For each case and run it grades the Framer's frozen output, lets
agents/repair.py act on what the grader flags (second reader, one rewrite, regrade, removal or hold), and then asks the one
question that matters: is the planted fabrication still in the text that would be published? It also counts what happened to
CLEAN text, since a repair of a sentence that was never wrong is a false alarm that cost something.

  planted_kept_out   the labelled planted claim is no longer in the final text (removed, rewritten away, or its story held)
  clean_touched      sentences rewritten, removed or held in units the labels say are clean
  dismissed          flags the second reader found supported (the false alarms it caught)

Results hold our own sentences and grader claims, never a quoted source span.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_evals  # noqa: E402
from run_evals import REPO, load_cases, load_json  # noqa: E402

sys.path.insert(0, str(REPO))
from agents import repair  # noqa: E402

PLANTS = ["negative-01", "negative-02", "negative-03", "negative-04"]
CLEAN = "2026-08-04"


def final_text(store, unit: str) -> str:
    return " ".join(store.get(p)[0] for p in store.paths(unit))


def run_case(client, profile, case):
    raw_day = case["input"]
    framing = copy.deepcopy(run_evals.framing_from_framed_day(case["output"]))
    store = repair.FramingStore(framing, raw_day, client, user_id=profile["user_id"], phase="eval-faithfulness")
    labels = case["labels"]["units"]
    # the sentences that carry a planted claim, before anything is done to them
    carriers = {}
    for unit, lab in labels.items():
        for pattern in lab.get("expected_unsupported", []) if not unit.startswith("_") else []:
            for path in store.paths(unit):
                text = store.get(path)[0]
                carriers[(unit, pattern)] = [text[a:b] for a, b in repair.citegate.sentence_spans(text) if pattern.lower() in text[a:b].lower()]
    t = time.time()
    verdicts, cost = store.grade()
    first_flags = {u: list(v.get("unsupported_claims", [])) for u, v in verdicts.items() if v.get("unsupported_claims")}
    verdicts, log = repair.settle(store, verdicts, client, user_id=profile["user_id"])
    planted = []
    for unit, lab in labels.items():
        if unit.startswith("_"):
            continue
        for pattern in lab.get("expected_unsupported", []):
            held = unit in log["held"]
            # kept out: the sentence that carried the planted claim is gone from the final text (removed, rewritten, or its story held).
            # A rewrite may keep a true date or name the plant misused, so the pattern alone is not the test.
            still = [c for c in carriers[(unit, pattern)] if c in final_text(store, unit)]
            planted.append({"unit": unit, "pattern": pattern, "kept_out": held or not still, "held": held,
                            "final_sentences_with_pattern": [t for t in (store.get(p)[0] for p in store.paths(unit)) if pattern.lower() in t.lower()]})
    clean_units = {u for u, lab in labels.items() if not u.startswith("_") and not lab.get("expected_unsupported")}
    touched = [r for r in log["rewritten"] + log["removed"] if (r.get("unit") or str(r.get("path", "")).partition(".")[0]) in clean_units]
    touched += [{"held": u} for u in log["held"] if u in clean_units]
    return {"planted": planted, "flags_first_grade": {u: len(c) for u, c in first_flags.items()}, "dismissed": len(log["dismissed"]),
            "rewritten": len(log["rewritten"]), "removed": len(log["removed"]), "held": sorted(log["held"]), "clean_touched": len(touched),
            "regraded": log["regraded"], "remaining_flags": {u: len(v.get("unsupported_claims", [])) for u, v in verdicts.items() if v.get("unsupported_claims")},
            "removed_claims": [r["claim"] for r in log["removed"]], "rewritten_claims": [r["claim"] for r in log["rewritten"]],
            "cost": round(cost + log["cost_usd"], 5), "seconds": round(time.time() - t, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    import anthropic
    client = anthropic.Anthropic()
    profile = load_json(REPO / "config" / "profile.json")
    cases = {c["name"]: c for c in load_cases(None)}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in PLANTS + [CLEAN]:
        for n in range(1, args.runs + 1):
            try:
                res = run_case(client, profile, cases[name])
                res["error"] = None
            except Exception as e:                       # a failed run is recorded and the measurement goes on
                res = {"error": f"{type(e).__name__}: {str(e)[:200]}", "planted": [], "cost": 0.0}
            res.update(case=name, run=n)
            (out / f"{name}-run{n}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
            kept = sum(p["kept_out"] for p in res["planted"])
            print(f"{name:<12} run {n}: planted kept out {kept}/{len(res['planted'])} dismissed={res.get('dismissed')} rewritten={res.get('rewritten')} "
                  f"removed={res.get('removed')} held={res.get('held')} clean_touched={res.get('clean_touched')} ${res['cost']:.4f} {res['error'] or ''}", flush=True)
            rows.append(res)
    (out / "summary.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
