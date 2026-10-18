"""The Framer's hard gates, as functions: the same rules the eval harness asserts.

run_evals.py and the weekly run both call these, so what the eval proves is what the
hosted run enforces. Pure and offline: no model, no database.

  structural    contract rules the prompt only asks for, asserted in code
  faithfulness  no claim the grader could not support from the item's own excerpt
"""

from __future__ import annotations

try:
    from . import framer
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import framer


def framing_from_day(day: dict) -> dict:
    """Recover the Framer's own output from an assembled brief, so the coverage check sees what the model wrote."""
    return {
        "thread": day["thread"],
        "threadSplit": day.get("threadSplit"),
        "items": [
            {"id": item["id"], **{k: item.get(k) for k in ("why", "example", "connection", "ninety")},
             "split": item.get("split")}
            for item in day["items"]
        ],
    }


def structural_failures(profile: dict, day: dict, framing: dict | None = None) -> list[str]:
    """Contract violations in an assembled brief. Empty means the structural gate is green.

    `framing` is the Framer's raw output when the caller has it; without it the splits are
    recovered from the brief (the eval's frozen golden days carry them).
    """
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

    # Every fact/interpretation split must reproduce the field it describes, or a claim can
    # be hidden from the grader by leaving it out of the split.
    failures += [f"coverage — {f}" for f in framer.coverage_failures(framing or framing_from_day(day))]

    expected_reading_time = framer.compute_reading_time(day["thread"], day["items"])
    if day["readingTimeMin"] != expected_reading_time:
        failures.append(f"readingTimeMin is {day['readingTimeMin']}, recomputes to {expected_reading_time}")
    expected_counts = framer.compute_counts(profile, day["items"])
    if day["counts"] != expected_counts:
        failures.append(f"counts are {day['counts']}, recompute to {expected_counts}")
    return failures


def completeness_failures(raw_day: dict, framing: dict, verdicts: dict, thread_verdict: dict | None) -> list[str]:
    """Anything that would reach publication UNGRADED. Empty means every story was graded.

    The faithfulness gate reads the claims the grader flagged; a story the grader skipped,
    or one the Framer left out, has no flags and would pass as if it were clean. So each
    story must have been written, must have a verdict, and the verdict must have enumerated
    at least one claim; the thread must have been graded too. Fail closed.
    """
    failures = []
    written = {f.get("id"): f for f in framing.get("items", [])}
    for item in raw_day["items"]:
        ident = item["id"]
        if ident not in written or not (written[ident].get("why") or "").strip():
            failures.append(f"{ident}: the Framer wrote nothing for this story")
        verdict = verdicts.get(ident)
        if not verdict:
            failures.append(f"{ident}: the grader returned no verdict")
        elif not verdict.get("claims"):
            failures.append(f"{ident}: the grader enumerated no claims, so nothing was checked")
    if not thread_verdict or not thread_verdict.get("claims"):
        failures.append("thread: the grader enumerated no claims, so nothing was checked")
    return failures


def faithfulness_failures(day: dict) -> list[str]:
    """Every claim the grader left unsupported, per item and in the thread. Empty means green.

    Zero tolerance: this is the hard gate the whole milestone is written around. Lifted
    claims and possible false alarms are reported by the grader but do not gate (they are
    warnings until their false-alarm rate is measured).
    """
    failures = []
    for item in day["items"]:
        for claim in item["faithfulness"]["unsupported_claims"]:
            failures.append(f"{item['id']}: unsupported — {claim}")
    for claim in day["threadFaithfulness"]["unsupported_claims"]:
        failures.append(f"thread: unsupported — {claim}")
    return failures
