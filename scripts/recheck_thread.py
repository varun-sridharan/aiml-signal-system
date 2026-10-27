"""Rewrite one stored edition's thread lines and recheck only them with the claim gate (Run 7, Step A.3).

    python scripts/recheck_thread.py 2026-W44 out_dir --lines "line one" "line two"

Free part: checks the new lines against the thread rule (agents/style_check.thread_findings) and prints what the paid call would send.
Paid part (only without --dry-run): one grader call (agents/llm.py, booked in the ledger) grading the two lines against every story's
excerpt, as the week unit of the claim gate does. Refused when month-to-date spend is above SPEND_LIMIT.
Nothing is written to the database (a published brief is append-only); a passing pair becomes a correction through agents/revisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, composer, framer, llm, runs, style_check, weekly  # noqa: E402

SPEND_LIMIT = 31.50


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("week")
    ap.add_argument("out_dir")
    ap.add_argument("--lines", nargs=2, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    profile = framer.load_json(framer.PROFILE_PATH)
    with budget.connect() as conn:
        body = conn.execute("SELECT body FROM briefs WHERE id = %s", (args.week,)).fetchone()[0]
        body = body if isinstance(body, dict) else json.loads(body)
        edition = body["edition"]
        before_lines = list(edition["thread"]["lines"])
        trial = {"thread": {"lines": args.lines, "body": edition["thread"]["body"]}, "learning": edition["learning"], "storiesTitle": edition.get("storiesTitle", "")}
        findings = style_check.thread_findings(trial)
        art = runs.load_artifact(conn, args.week, "framer")
        wide_day, published, _ = weekly.composer_day(conn, art["raw_day"])
        shown = composer.composer_items(wide_day, published)
        writing = " ".join(args.lines)
        part = {"items": [], "thread": {"writing_under_test": style_check.plain(writing), "_note": "Graded against the union below: the week's thread synthesises across stories.",
                                         "ground_truth_excerpt_union": [{"id": e["id"], "source_excerpt": e["excerpt"]} for e in shown]}, "_grading_note": composer.GRADER_NOTE}
        spend = float(budget.month_to_date_spend(conn))
        summary = {"week": args.week, "old_lines": before_lines, "new_lines": args.lines, "thread_rule_findings": [m for _, m in findings],
                   "ledger_before_usd": round(spend, 4), "payload_chars": len(json.dumps(part))}
        if findings:
            summary["result"] = "not sent: the new lines break the thread rule"
        elif args.dry_run:
            summary["result"] = "dry run: nothing sent"
        elif spend > SPEND_LIMIT:
            summary["result"] = f"blocked: month-to-date spend {spend:.2f} is above {SPEND_LIMIT}"
        else:
            client = llm.build_client()
            text, used, _cost = llm.call(client, user_id=profile["user_id"], phase=composer.CHECK_PHASE, conn=conn, **framer.check_request(part))
            graded = json.loads(text)
            union = "\n".join(e["source_excerpt"] for e in part["thread"]["ground_truth_excerpt_union"])
            verdict = framer.normalise_verdict(graded["thread"], union, part["thread"]["writing_under_test"])
            (out / "thread-verdict.json").write_text(json.dumps(verdict, indent=1, ensure_ascii=False))
            summary["claims"] = [{"claim": c.get("claim"), "supported": c.get("supported"), "span_verified": c.get("span_verified")} for c in verdict.get("claims", [])]
            summary["unsupported_claims"] = verdict.get("unsupported_claims", [])
            summary["ledger_after_usd"] = round(float(budget.month_to_date_spend(conn)), 4)
            ok = bool(verdict.get("claims")) and not verdict.get("unsupported_claims") and not verdict.get("grader_errors")
            summary["result"] = "passed" if ok else "failed: the old lines are kept"
    (out / "thread-recheck.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
