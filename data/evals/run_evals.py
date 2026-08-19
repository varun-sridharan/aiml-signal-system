"""Offline eval harness for the Framer.

Runs three checks over every golden case in data/evals/golden/<date>/:

  FAITHFULNESS  hard gate, objective. Re-runs the Framer's own grader over the frozen
                framing and compares its verdicts to labels.json. The grader is the
                thing under test here — a false positive on a seeded regression fails
                the run. Costs one cheap API call per case.
  STRUCTURAL    hard gate, deterministic, no API. Asserts in code the rules the
                Framer's prompt only asks for.
  USEFULNESS    soft, reported not gated. An LLM judge scores the framing against a
                rubric. Not a gate until the scores are calibrated against real
                ratings. Costs one cheap API call per case.

    python data/evals/run_evals.py                  # everything, 2 API calls per case
    python data/evals/run_evals.py --no-api         # structural only, free
    python data/evals/run_evals.py --case 2026-08-04
    python data/evals/run_evals.py --show-claims    # print the grader's full ledger

Exits non-zero if any hard gate fails, so this can gate a future Framer prompt change.
See prompts/CONVENTIONS.md for why each block carries a WHAT/CONCEPT comment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = REPO / "data" / "evals" / "golden"

# WHAT: import the Framer module itself rather than reimplementing any of it.
# CONCEPT: eval — test the artifact, not a copy of it. If the eval had its own
# grader or its own reading-time formula, a passing run would prove nothing about
# what actually ships.
sys.path.insert(0, str(REPO / "agents"))
import framer  # noqa: E402

RUBRIC_DIMENSIONS = ["relevance", "insight", "concision", "tradeoff_named"]

# WHAT: the judge's output shape — one scored row per unit of writing.
# CONCEPT: harness — structured I/O. Scores come back as numbers we can average,
# not as prose we would have to parse.
USEFULNESS_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    **{d: {"type": "integer"} for d in RUBRIC_DIMENSIONS},
                    "comment": {"type": "string"},
                },
                "required": ["id", *RUBRIC_DIMENSIONS, "comment"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["units"],
    "additionalProperties": False,
}

JUDGE_SYSTEM_PROMPT = """You are judging the usefulness of a daily AI/ML brief written \
for one specific reader, whose profile is given to you. Usefulness is relative to that \
reader — not to a general audience.

Score every unit on each dimension, 1 (bad) to 5 (excellent):

- relevance: does this matter to THIS reader's role, goals and interests? A true but \
generic item scores low.
- insight: does it say something the reader would not have concluded from the headline \
alone? Restating the headline scores 1.
- concision: is every sentence load-bearing? Padding, hedging and throat-clearing \
score low. Short and dense scores high.
- tradeoff_named: does it name the cost, the catch, or what the reader gives up? An \
unqualified upside scores 1.

Judge the writing you are given and nothing else. Do not check facts — a separate \
grader does that, and an unsupported claim is not your problem here. Add a one-sentence \
comment per unit saying what would most improve it."""


# ---------------------------------------------------------------- helpers


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def framing_from_framed_day(day: dict) -> dict:
    """Recover the Framer's raw framing output from a committed brief."""
    # WHAT: strip the assembled brief back to just what the model wrote, so the
    # grader sees the same payload it would see mid-run.
    # CONCEPT: eval — evaluate the committed artifact. We never re-run the Framer to
    # regenerate a brief; the golden output is frozen and this reconstitutes its input
    # to the grader. Headline, rank, sources and counts are deliberately dropped.
    return {
        "thread": day["thread"],
        "items": [
            {"id": item["id"], **{k: item.get(k) for k in ("why", "example", "connection", "ninety")}}
            for item in day["items"]
        ],
    }


def matching_pattern(patterns: list, text: str) -> str | None:
    low = text.lower()
    return next((p for p in patterns if p.lower() in low), None)


# ---------------------------------------------------------------- faithfulness


def score_unit(labels: dict, verdict: dict) -> tuple[list, list]:
    """Compare one unit's flags to its labels; return (false positives, false negatives)."""
    # WHAT: anything the grader flagged that the labels don't expect is a false
    # positive; anything the labels expect that it missed is a false negative.
    # CONCEPT: eval — who checks the checker. The grader's output is the prediction
    # and the hand labels are the ground truth, exactly as for any other classifier.
    expected = list(labels.get("expected_unsupported", []))
    tolerated = list(labels.get("tolerated", []))

    false_positives, matched = [], set()
    for claim in verdict["unsupported_claims"]:
        hit = matching_pattern(expected, claim)
        if hit:
            matched.add(hit)
        elif matching_pattern(tolerated, claim) is None:
            false_positives.append(claim)

    false_negatives = [p for p in expected if p not in matched]
    return false_positives, false_negatives


def check_regression(case: dict, claims: list) -> tuple[bool, str]:
    """Assert a seeded case against the grader's claim ledger, not just its flag list."""
    # WHAT: find the enumerated claim this regression is about and check the grader
    # both examined it and reached the labelled verdict on it.
    # CONCEPT: eval — regression case. Absence of a flag is not proof; the ledger
    # lets us demand the grader looked at the $965B clause and found the span.
    expected_supported = case.get("expected_supported", True)
    claim = next((c for c in claims if case["claim_pattern"].lower() in c["claim"].lower()), None)

    if claim is None:
        return False, f"grader never enumerated a claim matching {case['claim_pattern']!r}"
    if claim["supported"] != expected_supported:
        verb = "flagged as unsupported" if not claim["supported"] else "accepted as supported"
        return False, f"{verb}, expected the opposite — {claim['claim']!r}"

    span = claim.get("supporting_span") or ""
    wanted_span = case.get("expected_span_contains")
    if wanted_span and wanted_span.lower() not in span.lower():
        return False, f"supported, but the quoted span misses {wanted_span!r} — got {span!r}"
    return True, f"{claim['claim']!r} → {span[:70]!r}" if span else f"{claim['claim']!r}"


def run_faithfulness(client, usage_log, timestamp, profile, case, show_claims: bool) -> dict:
    """Regrade the golden framing and compare every verdict to the labels."""
    labels = case["labels"]["units"]
    framing = framing_from_framed_day(case["output"])

    verdicts, thread_verdict, usage = framer.check_faithfulness(client, case["input"], framing)
    cost = framer.record_call(
        usage_log, profile["user_id"], "eval-faithfulness", framer.CHECK_MODEL, usage, timestamp
    )
    graded = {**verdicts, "thread": thread_verdict}

    print("\n  FAITHFULNESS (hard gate)")
    failures, total_fp, total_fn = [], 0, 0

    for unit_id, unit_labels in labels.items():
        if unit_id.startswith("_"):
            continue
        verdict = graded.get(unit_id)
        if verdict is None:
            failures.append(f"{unit_id}: grader returned no verdict")
            print(f"    FAIL  {unit_id} — no verdict returned")
            continue

        false_positives, false_negatives = score_unit(unit_labels, verdict)
        total_fp += len(false_positives)
        total_fn += len(false_negatives)
        # Labels cover every unit of this day, so any unlabelled flag is the grader
        # being wrong — every FP and FN gates, not only the seeded regressions.
        failures += [f"{unit_id}: false positive — {c}" for c in false_positives]
        failures += [f"{unit_id}: false negative — expected a flag matching {p!r}" for p in false_negatives]

        # A span the grader quoted that isn't in the excerpt is the checker itself
        # misbehaving — gated separately from FP/FN so the two never get confused.
        grader_errors = verdict.get("grader_errors", [])
        failures += [f"{unit_id}: grader error — {e}" for e in grader_errors]

        marks = []
        for case_def in unit_labels.get("regression_cases", []):
            ok, detail = check_regression(case_def, verdict["claims"])
            marks.append((case_def["id"], ok, detail))
            if not ok:
                failures.append(f"{unit_id} / {case_def['id']}: {detail}")

        broken = false_positives or false_negatives or grader_errors or any(not ok for _, ok, _ in marks)
        status = "FAIL" if broken else "ok"
        confidence = verdict["confidence"]
        print(f"    {status:<4}  {unit_id}  ({len(verdict['claims'])} claims"
              + (f", confidence {confidence:.2f})" if confidence is not None else ")"))
        for claim in false_positives:
            print(f"            FALSE POSITIVE  {claim}")
        for pattern in false_negatives:
            print(f"            FALSE NEGATIVE  expected a flag matching {pattern!r}")
        for error in grader_errors:
            print(f"            GRADER ERROR    {error}")
        for name, ok, detail in marks:
            print(f"            regression {name}: {'PASS' if ok else 'FAIL'} — {detail}")
        if show_claims:
            for claim in verdict["claims"]:
                mark = "+" if claim["supported"] else "-"
                print(f"              {mark} {claim['claim']}")
                print(f"                span: {claim.get('supporting_span')!r}")

    if not failures:
        print("    all units match their labels")
    print(f"    false positives: {total_fp} · false negatives: {total_fn}")

    return {
        "passed": not failures,
        "failures": failures,
        "false_positives": total_fp,
        "false_negatives": total_fn,
        "cost": cost,
    }


# ---------------------------------------------------------------- structural


def run_structural(profile: dict, case: dict) -> dict:
    """Assert in code the rules the Framer's prompt only asks for. No API call."""
    # WHAT: the deterministic half of the eval — contract violations a grader should
    # never have been asked to judge in the first place.
    # CONCEPT: harness — assert in code what the prompt only asks for. These cost
    # nothing, never flake, and catch the failures a stochastic grader would miss.
    day = case["output"]
    preferences = profile["preferences"]
    do_this_category = preferences["doThisCategory"]
    example_categories = preferences["examplesOnlyForCategories"]
    failures = []

    for item in day["items"]:
        if "DO THIS" in item["tags"] and item["category"] != do_this_category:
            failures.append(f"{item['id']}: DO THIS on a {item['category']} item (allowed: {do_this_category})")
        if item.get("example") and item["category"] not in example_categories:
            failures.append(f"{item['id']}: worked example on a {item['category']} item (allowed: {example_categories})")
        if not item.get("sources"):
            failures.append(f"{item['id']}: no sources")

    if not day.get("thread", "").strip():
        failures.append("thread is missing or empty")

    # Recomputed with the Framer's own functions: this catches a brief whose header
    # disagrees with its own body — a hand-edit, or a formula change without a rerun.
    expected_reading_time = framer.compute_reading_time(day["thread"], day["items"])
    if day["readingTimeMin"] != expected_reading_time:
        failures.append(f"readingTimeMin is {day['readingTimeMin']}, recomputes to {expected_reading_time}")

    expected_counts = framer.compute_counts(profile, day["items"])
    if day["counts"] != expected_counts:
        failures.append(f"counts are {day['counts']}, recompute to {expected_counts}")

    print("\n  STRUCTURAL (hard gate, no API)")
    checks = [
        f"DO THIS only on {do_this_category} items",
        f"worked examples only on {example_categories}",
        "every item has sources",
        "thread present and non-empty",
        "reading time and counts match the items",
    ]
    if failures:
        for failure in failures:
            print(f"    FAIL  {failure}")
    else:
        for check in checks:
            print(f"    ok    {check}")

    return {"passed": not failures, "failures": failures}


# ---------------------------------------------------------------- usefulness


def run_usefulness(client, usage_log, timestamp, profile, case) -> dict:
    """Score the framing against a rubric with an LLM judge. Reported, never gated."""
    # WHAT: the subjective half — is the writing actually worth reading?
    # CONCEPT: eval — subjective grader, deliberately kept out of the gate. Faithfulness
    # is objective and can fail a build; usefulness is a judge's opinion and stays
    # advisory until it is calibrated against real ratings from the reader.
    day = case["output"]
    payload = {
        "reader_profile": {
            "role": profile["role"],
            "goals": profile["goals"],
            "interests": profile["interests"],
            "daily_read_minutes_target": profile["preferences"]["dailyReadMinutesTarget"],
        },
        "units": [{"id": "thread", "text": day["thread"]}]
        + [
            {
                "id": item["id"],
                "category": item["category"],
                **{k: item[k] for k in ("why", "example", "connection", "ninety") if k in item},
            }
            for item in day["items"]
        ],
    }

    message = client.messages.create(
        model=framer.CHECK_MODEL,
        max_tokens=framer.CHECK_MAX_TOKENS,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        output_config={"format": {"type": "json_schema", "schema": USEFULNESS_SCHEMA}},
    )
    cost = framer.record_call(
        usage_log, profile["user_id"], "eval-usefulness", framer.CHECK_MODEL, message.usage, timestamp
    )
    units = json.loads(framer.structured_text(message))["units"]

    print("\n  USEFULNESS (reported, not gated — needs your ratings to calibrate)")
    header = "  ".join(f"{d[:9]:>9}" for d in RUBRIC_DIMENSIONS)
    print(f"    {'unit':<28}{header}")
    averages = {d: [] for d in RUBRIC_DIMENSIONS}
    for unit in units:
        scores = "  ".join(f"{unit[d]:>9}" for d in RUBRIC_DIMENSIONS)
        print(f"    {unit['id']:<28}{scores}")
        for dimension in RUBRIC_DIMENSIONS:
            averages[dimension].append(unit[dimension])
    means = {d: sum(v) / len(v) for d, v in averages.items() if v}
    print(f"    {'mean':<28}" + "  ".join(f"{means[d]:>9.2f}" for d in RUBRIC_DIMENSIONS))
    for unit in units:
        print(f"      {unit['id']}: {unit['comment']}")

    return {"means": means, "units": units, "cost": cost}


# ---------------------------------------------------------------- main


def load_cases(selected: str | None) -> list:
    """Every golden case is a directory; adding a day needs no change here."""
    # WHAT: discover cases by globbing directories that contain labels.json.
    # CONCEPT: harness — the runner is data-driven. A new golden day is a new folder,
    # never a code edit, which is what keeps the set cheap to grow.
    cases = []
    for case_dir in sorted(p for p in GOLDEN_DIR.iterdir() if p.is_dir()):
        if selected and case_dir.name != selected:
            continue
        labels_path = case_dir / "labels.json"
        if not labels_path.exists():
            continue
        labels = load_json(labels_path)
        cases.append(
            {
                "name": case_dir.name,
                "labels": labels,
                "input": load_json(case_dir / labels["input"]),
                "output": load_json(case_dir / labels["output"]),
            }
        )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline eval harness for the Framer.")
    parser.add_argument("--case", help="run one golden case by directory name, e.g. 2026-08-04")
    parser.add_argument("--no-api", action="store_true", help="structural checks only; makes no API call")
    parser.add_argument("--show-claims", action="store_true", help="print the grader's full claim ledger")
    parser.add_argument("--skip-usefulness", action="store_true",
                        help="skip the soft judge; halves the spend while iterating on the grader")
    args = parser.parse_args()

    profile = load_json(REPO / "config" / "profile.json")
    cases = load_cases(args.case)
    if not cases:
        sys.exit(f"No golden cases found in {GOLDEN_DIR.relative_to(REPO)}"
                 + (f" matching {args.case!r}" if args.case else ""))

    client, usage_log, timestamp = None, None, None
    spent = 0.0
    if not args.no_api:
        load_dotenv(REPO / ".env")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env, or run with --no-api.")

        # WHAT: refuse to start if this month's spend is already over budget.
        # CONCEPT: loop — the same circuit breaker the Framer uses, reading the same
        # ledger. An eval that can run unbounded is a way to spend the budget twice.
        now = datetime.now(timezone.utc)
        timestamp, month = now.isoformat(timespec="seconds"), now.strftime("%Y-%m")
        usage_log = framer.load_usage_log()
        spent = framer.month_to_date_spend(usage_log, month)
        if spent >= framer.MONTHLY_BUDGET_USD:
            sys.exit(
                f"Month-to-date spend ${spent:.2f} has reached MONTHLY_BUDGET_USD "
                f"(${framer.MONTHLY_BUDGET_USD:.2f}). No API call made."
            )
        import anthropic

        client = anthropic.Anthropic()

    calls_per_case = 1 if args.skip_usefulness else 2
    mode = ("structural only" if args.no_api
            else f"{calls_per_case * len(cases)} API call(s) ({framer.CHECK_MODEL})")
    print(f"Framer evals — {len(cases)} golden case(s), {mode}")

    hard_failures, cost, summaries = [], 0.0, []
    for case in cases:
        print(f"\n{'=' * 72}\nCASE {case['name']}  ({len(case['output']['items'])} items)")

        structural = run_structural(profile, case)
        hard_failures += [f"{case['name']} structural: {f}" for f in structural["failures"]]

        faithfulness = usefulness = None
        if not args.no_api:
            faithfulness = run_faithfulness(client, usage_log, timestamp, profile, case, args.show_claims)
            hard_failures += [f"{case['name']} faithfulness: {f}" for f in faithfulness["failures"]]
            cost += faithfulness["cost"]

            if not args.skip_usefulness:
                usefulness = run_usefulness(client, usage_log, timestamp, profile, case)
                cost += usefulness["cost"]

        summaries.append({"case": case["name"], "structural": structural,
                          "faithfulness": faithfulness, "usefulness": usefulness})

    if not args.no_api:
        framer.USAGE_PATH.write_text(json.dumps(usage_log, indent=2) + "\n")

    print(f"\n{'=' * 72}\nSUMMARY")
    for summary in summaries:
        line = [f"  {summary['case']}:", f"structural {'PASS' if summary['structural']['passed'] else 'FAIL'}"]
        if summary["faithfulness"]:
            faithfulness = summary["faithfulness"]
            line.append(f"· faithfulness {'PASS' if faithfulness['passed'] else 'FAIL'} "
                        f"(FP {faithfulness['false_positives']}, FN {faithfulness['false_negatives']})")
        if summary["usefulness"]:
            means = summary["usefulness"]["means"]
            line.append("· usefulness " + " ".join(f"{d[:4]} {means[d]:.1f}" for d in RUBRIC_DIMENSIONS))
        print(" ".join(line))

    if not args.no_api:
        print(f"\n  cost: ${cost:.4f} this run · ${spent + cost:.2f} month-to-date "
              f"of ${framer.MONTHLY_BUDGET_USD:.2f}")

    # WHAT: a failing hard gate exits non-zero.
    # CONCEPT: eval — the gate only means something if it can stop something. This is
    # what makes a future Framer prompt edit regression-tested rather than hoped over.
    if hard_failures:
        print(f"\n  {len(hard_failures)} hard-gate failure(s):")
        for failure in hard_failures:
            print(f"    ✗ {failure}")
        sys.exit(1)
    print("\n  all hard gates passed")


if __name__ == "__main__":
    main()
