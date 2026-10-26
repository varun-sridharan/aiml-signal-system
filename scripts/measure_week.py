"""One composing run on a stored week under either gate, measured, publishing nothing (Run 4, Step D).

    python scripts/measure_week.py --gate claims|citations --week 2026-W43-r4 --out DIR --label run1

Reads the week's finished Reader output and first-pass framing (read only), writes the edition with the chosen gate's writer,
checks it with that gate's checker, and saves what is needed to compare the two: the gate failures, which stories each flagged,
the sentences or claims flagged (our own words, never a cited or quoted source passage), cost and seconds. Paid; every call is
booked through agents/llm.py. Touches no run, no brief, no item. The first pass is reused, so its cost is not in the numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, citegate, composer, composer_cited, framer, llm, runs, weekly  # noqa: E402


def safe_claims(verdict: dict) -> dict:
    """A grader verdict without its quoted source spans."""
    return {"claims": [{"claim": c["claim"], "supported": c.get("supported")} for c in verdict.get("claims", [])],
            "unsupported_claims": verdict.get("unsupported_claims", []), "grader_errors": len(verdict.get("grader_errors", []))}


def run_claims(client, profile, week, conn):
    art = runs.load_artifact(conn, week, "framer")
    raw_day, framing = art["raw_day"], art["framing"]
    wide_day, published, minutes = weekly.composer_day(conn, raw_day)
    label = (runs.stage_output(conn, week, "reader").get("window") or {}).get("label", "")
    t = time.time()
    composed, revision, cost = composer.compose_with_revision(client, composer.load_system_prompt(), profile, wide_day, framing, label, published, conn=conn)
    verdicts, wk, cusage, payload = composer.check_faithfulness(client, composed, wide_day, published, user_id=profile["user_id"], conn=conn)
    failures = composer.edition_failures(composed, wide_day, payload, verdicts, wk)
    check_cost = float(budget.estimate_cost_usd(llm.CHECK_MODEL, cusage))
    flagged_units = sorted({f.split(":", 1)[0] for f in composer.faithfulness_failures(verdicts, wk)})
    other = [f for f in failures if f not in composer.faithfulness_failures(verdicts, wk)]
    return {"gate": "claims", "cost_usd": round(cost + check_cost, 4), "seconds": round(time.time() - t, 1),
            "failures_total": len(failures), "faithfulness_failures": composer.faithfulness_failures(verdicts, wk), "other_failures": other,
            "units_flagged": flagged_units, "publishes": not failures, "revised_fields": revision["replaced"],
            "units": {u: safe_claims(v) for u, v in verdicts.items()}, "week": safe_claims(wk),
            "story_count": len(raw_day["items"])}


def run_citations(client, profile, week, conn):
    art = runs.load_artifact(conn, week, "framer")
    raw_day, framing = art["raw_day"], art["framing"]
    wide_day, published, minutes = weekly.composer_day(conn, raw_day)
    label = (runs.stage_output(conn, week, "reader").get("window") or {}).get("label", "")
    t = time.time()
    r = composer_cited.compose_and_check(client, composer_cited.load_system_prompt(), profile, wide_day, framing, label, published, conn=conn)
    d = r["decision"]
    flagged = []
    for s in r["sentences"]:
        v = s.get("verdict")
        if s["code_failures"] or (v is not None and not v["supported"]):
            flagged.append({"path": s["path"], "text": s["text"], "code": s["code_failures"][:2],
                            "verdict": None if v is None else {"first": [x["supports"] for x in v["first"]], "second_unsupported": (v["second"] or {}).get("unsupported")}})
    return {"gate": "citations", "cost_usd": round(r["cost_usd"], 4), "writer_cost_usd": round(r["writer_cost_usd"], 4), "verify_cost_usd": round(r["verify_cost_usd"], 4),
            "seconds": round(time.time() - t, 1), "structural_failures": r["structural"], "held": d["held"], "blockers": d["blockers"], "passing": d["passing"],
            "publishes": d["publishable"] and not r["structural"], "flagged": flagged, "sentences": len(r["sentences"]),
            "cited": sum(1 for s in r["sentences"] if s["citations"]), "marked": sum(1 for s in r["sentences"] if s["interpretation"]),
            "second_checks": sum(1 for s in r["sentences"] if (s.get("verdict") or {}).get("second") is not None),
            "revised_fields": r["revision"]["replaced"], "story_count": len(raw_day["items"]),
            "sentences_detail": [{"path": s["path"], "text": s["text"], "n_citations": len(s["citations"]), "marked": s["interpretation"],
                                  "supported": (s.get("verdict") or {}).get("supported")} for s in r["sentences"]]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", choices=["claims", "citations"], required=True)
    ap.add_argument("--week", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    profile = framer.load_json(framer.PROFILE_PATH)
    client = llm.build_client()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with budget.connect() as conn:
        before = float(budget.month_to_date_spend(conn))
        try:
            result = (run_claims if args.gate == "claims" else run_citations)(client, profile, args.week, conn)
            result["error"] = None
        except BaseException as e:              # a run that raises is a measurement too (budget refusals and truncation included)
            result = {"gate": args.gate, "error": f"{type(e).__name__}: {str(e)[:300]}", "publishes": False, "cost_usd": 0.0}
        result["ledger_delta_usd"] = round(float(budget.month_to_date_spend(conn)) - before, 4)
    (out / f"{args.gate}-{args.label}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({k: result.get(k) for k in ("gate", "publishes", "cost_usd", "ledger_delta_usd", "seconds", "units_flagged", "held", "blockers", "error")}, default=str)[:900])
    return 0


if __name__ == "__main__":
    sys.exit(main())
