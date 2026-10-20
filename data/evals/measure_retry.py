"""Measure the faithfulness grader on every planted-fabrication case, with and without bk-retry.

    python data/evals/measure_retry.py --runs 5 --out data/evals/runs/2026-10-20-bk-retry

PAID: each run is one grader call per case (plus one per retried unit), booked in the ledger
through llm.call. For every case and arm it saves each run's full result (claim ledgers
included) and writes summary.json and summary.md. The metrics:

  caught         the planted claim was flagged (no false negative on the labelled plant)
  clean-day FPs  claims flagged unsupported on 2026-08-04, which has no fault in it
  control FPs    flagged claims on the clean units of the negative cases (reported, not the criterion)

Done means: every planted fabrication caught in at least 4 of 5 runs, and 0 false positives on the
clean day. Nothing here fits a threshold to pass; the numbers are what they are.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_evals  # noqa: E402
from run_evals import REPO, framer, load_cases, load_json, run_faithfulness  # noqa: E402

PLANTS = ["negative-01", "negative-02", "negative-03", "negative-04"]
CLEAN = "2026-08-04"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--arms", default="off,on", help="retry arms to measure, in order")
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
    with framer.budget.connect() as conn:
        start = float(framer.month_to_date_spend(conn))
    summary = {}
    for arm in args.arms.split(","):
        framer.RETRY_ON_LIFT = arm == "on"
        for name in PLANTS + [CLEAN]:
            for n in range(1, args.runs + 1):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    result = run_faithfulness(client, profile, cases[name], False)
                plant_unit = None
                if name in PLANTS:
                    plant_unit = next(u for u, l in cases[name]["labels"]["units"].items() if not u.startswith("_") and l.get("expected_unsupported"))
                caught = None if plant_unit is None else not any(f.startswith(f"{plant_unit}: false negative") for f in result["failures"])
                fps = [f for f in result["failures"] if "false positive" in f]
                row = {"arm": arm, "case": name, "run": n, "plant_unit": plant_unit, "caught": caught,
                       "false_positives": fps, "retried_units": result["retried_units"], "lifted": result["lifted"],
                       "failures": result["failures"], "cost_usd": round(result["cost"], 5), "verdicts": result["verdicts"]}
                (out / f"{arm}-{name}-run{n}.json").write_text(json.dumps(row, indent=1, ensure_ascii=False, default=str) + "\n")
                summary.setdefault(arm, {}).setdefault(name, []).append({k: row[k] for k in ("run", "caught", "false_positives", "retried_units", "cost_usd")})
                print(f"{arm:>3} {name:<12} run {n}: caught={caught} fps={len(fps)} retried={row['retried_units']} ${row['cost_usd']:.4f}", flush=True)
    with framer.budget.connect() as conn:
        spent = float(framer.month_to_date_spend(conn)) - start
    (out / "summary.json").write_text(json.dumps({"runs": args.runs, "spent_usd": round(spent, 4), "results": summary}, indent=1) + "\n")
    lines = [f"# bk-retry measurement\n\n{args.runs} runs per case and arm; spent ${spent:.4f}.\n",
             "| arm | case | plant caught | false positives (all runs) | runs with a retry | cost |", "|---|---|---|---|---|---|"]
    for arm, per_case in summary.items():
        for name, rows in per_case.items():
            caught = "-" if rows[0]["caught"] is None else f"{sum(1 for r in rows if r['caught'])}/{len(rows)}"
            lines.append(f"| {arm} | {name} | {caught} | {sum(len(r['false_positives']) for r in rows)} | "
                         f"{sum(1 for r in rows if r['retried_units'])} | ${sum(r['cost_usd'] for r in rows):.4f} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
