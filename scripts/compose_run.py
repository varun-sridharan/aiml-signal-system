"""One composing run on a stored week, measured and gated, without publishing anything.

    python scripts/compose_run.py 2026-W43-r3 autopilot/evidence/2026-10-21/stepC-compose-run

Reads the week's finished Reader output and first-pass framing (read only), then runs the second pass
(agents/composer.py) and its grader for real, and runs every gate over the result. Paid: two calls, booked
in the ledger through agents/llm.py. Writes only files: the composed prose, the grader's verdicts, the
gate failures and the cost, so a number quoted from it can be opened. Touches no run, no brief, no item.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, composer, framer, llm, runs, style_check, weekly  # noqa: E402


def main() -> int:
    run_id, out_dir = sys.argv[1], Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = framer.load_json(framer.PROFILE_PATH)
    client = llm.build_client()
    with budget.connect() as conn:
        art = runs.load_artifact(conn, run_id, "framer")
        reader_out = runs.stage_output(conn, run_id, "reader")
        raw_day, framing = art["raw_day"], art["framing"]
        wide_day, published, minutes = weekly.composer_day(conn, raw_day)
        before = float(budget.month_to_date_spend(conn))
        label = (reader_out.get("window") or {}).get("label", "")
        composed, revision, cost = composer.compose_with_revision(client, composer.load_system_prompt(), profile, wide_day, framing, label, published, conn=conn)
        (out_dir / "first_draft.json").write_text(json.dumps(revision, indent=1, ensure_ascii=False))
        (out_dir / "composed.json").write_text(json.dumps(composed, indent=1, ensure_ascii=False))
        verdicts, week, cusage, payload = composer.check_faithfulness(client, composed, wide_day, published, user_id=profile["user_id"], conn=conn)
        failures = composer.edition_failures(composed, wide_day, payload, verdicts, week)
        after = float(budget.month_to_date_spend(conn))
    edition = composer.assemble_edition(composed, wide_day, framing, verdicts, week, stats=reader_out.get("stats") or {}, source_minutes=minutes,
                                        style=style_check.edition_style_failures(composed), published=published)
    check_cost = budget.estimate_cost_usd(llm.CHECK_MODEL, cusage)
    summary = {
        "run": run_id, "stories": len(raw_day["items"]), "composer_model": llm.FRAMER_MODEL,
        "composer_cost_usd": round(cost, 4), "grader_cost_usd": round(float(check_cost), 4),
        "revised_fields": revision["replaced"], "problems_before_revision": {k: len(v) for k, v in revision["problems"].items()},
        "ledger_before_after_usd": [round(before, 4), round(after, 4)],
        "over_cost_note": cost + float(check_cost) > composer.COST_NOTE_USD,
        "failures": failures,
        "words": {"thread_body": composer.words(composed["thread"]["body"]), "essay_paragraphs": len(composed["learning"]["paras"]),
                  "feature_words": {s["id"]: sum(composer.words(p) for p in s["featureParas"]) for s in composed["stories"] if s.get("featureParas")}},
    }
    (out_dir / "verdicts.json").write_text(json.dumps({"units": edition["faithfulness"], "payload_units": [u["id"] for u in payload["items"]]}, indent=1, ensure_ascii=False))
    (out_dir / "edition.json").write_text(json.dumps(edition, indent=1, ensure_ascii=False))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps({k: summary[k] for k in ("stories", "composer_cost_usd", "grader_cost_usd", "revised_fields", "problems_before_revision", "words")}, indent=1))
    print(f"{len(failures)} gate failure(s)")
    for line in failures:
        print("  -", line[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
