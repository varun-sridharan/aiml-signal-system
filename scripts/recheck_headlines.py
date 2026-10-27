"""Recheck candidate section headlines for a stored edition, each alone, with the claim gate (Run 8, Step B; Run 7's recheck_thread.py, widened).

    python scripts/recheck_headlines.py 2026-W44 candidates.json out_dir [--dry-run]

candidates.json maps a field to its attempts, best first: {"thread": [["line 1", "line 2"], ...], "learning.headline": ["text", ...],
"stories.title": ["text", ...]}. For each field, in that order: an attempt that breaks the headline rule (agents/style_check.headline_findings, with
the fields accepted so far in place) is skipped for free; otherwise one grader call (agents/llm.py, booked in the ledger) grades the text
against every story's excerpt, as the week unit of the claim gate does. The first attempt with every claim supported and a verified span
wins; a field is tried at most MAX_ATTEMPTS times; total spend is capped (SPEND_CAP_USD above the ledger at the start). Every attempt and
verdict is written to out_dir/headline-recheck.json. Nothing is written to the database: a published brief is append-only, and a winning text
becomes a correction through `python -m agents.revisions ... --from-recheck out_dir/headline-recheck.json` (Run 9).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, composer, framer, llm, runs, style_check, weekly  # noqa: E402

MAX_ATTEMPTS = 3
SPEND_CAP_USD = 0.15


def get_field(edition: dict, field: str):
    if field == "thread":
        return list(edition["thread"]["lines"])
    if field == "learning.headline":
        return edition["learning"]["headline"]
    return edition.get("storiesTitle", "")


def set_field(edition: dict, field: str, value) -> None:
    if field == "thread":
        edition["thread"]["lines"] = list(value)
    elif field == "learning.headline":
        edition["learning"]["headline"] = value
    else:
        edition["storiesTitle"] = value


def text_of(value) -> str:
    return " ".join(value) if isinstance(value, list) else value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("week")
    ap.add_argument("candidates")
    ap.add_argument("out_dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cands = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    profile = framer.load_json(framer.PROFILE_PATH)
    log: dict = {"week": args.week, "fields": {}}
    with budget.connect() as conn:
        body = conn.execute("SELECT body FROM briefs WHERE id = %s", (args.week,)).fetchone()[0]
        body = body if isinstance(body, dict) else json.loads(body)
        edition = body["edition"]
        trial = json.loads(json.dumps(edition))
        art = runs.load_artifact(conn, args.week, "framer")
        wide_day, published, _ = weekly.composer_day(conn, art["raw_day"])
        union = [{"id": e["id"], "source_excerpt": e["excerpt"]} for e in composer.composer_items(wide_day, published)]
        start = float(budget.month_to_date_spend(conn))
        log["ledger_before_usd"] = round(start, 4)
        won: dict = {}
        client = None
        for field, attempts in cands.items():
            rec = {"old": get_field(edition, field), "attempts": [], "result": "kept the old text"}
            log["fields"][field] = rec
            for n, cand in enumerate(attempts[:MAX_ATTEMPTS], start=1):
                step = {"attempt": n, "text": cand}
                check = json.loads(json.dumps(trial))
                set_field(check, field, cand)
                bad = [m for p, m in style_check.headline_findings(check) if p == ("thread.line.1" if field == "thread" else field) or (field == "thread" and p.startswith("thread."))]
                if bad:
                    step["verdict"] = "not sent: breaks the headline rule: " + "; ".join(bad)
                elif args.dry_run:
                    step["verdict"] = "dry run: nothing sent"
                elif float(budget.month_to_date_spend(conn)) - start >= SPEND_CAP_USD:
                    step["verdict"] = f"not sent: the ${SPEND_CAP_USD} cap for this run is reached"
                else:
                    client = client or llm.build_client()
                    part = {"items": [], "thread": {"writing_under_test": style_check.plain(text_of(cand)), "_note": "Graded against the union below: a section headline synthesises across stories.",
                                                    "ground_truth_excerpt_union": union}, "_grading_note": composer.GRADER_NOTE}
                    text, _used, _cost = llm.call(client, user_id=profile["user_id"], phase=composer.CHECK_PHASE, conn=conn, **framer.check_request(part))
                    graded = json.loads(text)
                    verdict = framer.normalise_verdict(graded["thread"], "\n".join(u["source_excerpt"] for u in union), part["thread"]["writing_under_test"])
                    ok = bool(verdict.get("claims")) and not verdict.get("unsupported_claims") and not verdict.get("grader_errors")
                    step["claims"] = [{"claim": c.get("claim"), "supported": c.get("supported"), "span_verified": c.get("span_verified")} for c in verdict.get("claims", [])]
                    step["unsupported_claims"] = verdict.get("unsupported_claims", [])
                    step["verdict"] = "passed" if ok else "failed"
                    if ok:
                        won[field] = cand
                        set_field(trial, field, cand)
                        rec["result"] = f"passed on attempt {n}"
                rec["attempts"].append(step)
                if field in won:
                    break
        log["ledger_after_usd"] = round(float(budget.month_to_date_spend(conn)), 4)
        log["spent_usd"] = round(log["ledger_after_usd"] - log["ledger_before_usd"], 4)
        final = json.loads(json.dumps(trial))
        log["rule_findings_after"] = [m for _, m in style_check.headline_findings(final)]
    (out / "headline-recheck.json").write_text(json.dumps(log, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in log.items() if k != "fields"}, indent=1, ensure_ascii=False))
    for field, rec in log["fields"].items():
        print(f"{field}: {rec['result']}")
        for s in rec["attempts"]:
            print(f"   attempt {s['attempt']}: {s['verdict']}  {text_of(s['text'])[:100]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
