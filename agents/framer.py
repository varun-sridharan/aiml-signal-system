"""Framer — turns a raw (verified but unframed) day into the daily brief.

Reads (profile, raw_day) and writes a framed day next to it. Two API calls per run:
one Opus call that frames every item at once, one Haiku call that checks the framing
against the sources. Everything that is arithmetic rather than judgment (reading
time, counts, rank) is computed here in Python.

    python agents/framer.py                          # defaults to the 2026-08-04 sample
    python agents/framer.py data/verified/gatekeeper_2026-08-05.json

See prompts/Framer-Prompt-and-Sources.md for the prompt and the output contract, and
prompts/CONVENTIONS.md for why each block carries a WHAT/CONCEPT comment.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# WHAT: pin every model and cost knob in one place at the top of the file.
# CONCEPT: harness — model routing. The expensive model frames; a cheap one checks.
# Pinning (not "latest") is what makes a prompt file a reproducible recipe.
FRAMER_MODEL = "claude-opus-5"
CHECK_MODEL = "claude-haiku-4-5"
FRAMER_MAX_TOKENS = 24_000
CHECK_MAX_TOKENS = 4_000
FRAMER_EFFORT = "high"

# WHAT: hard monthly ceiling; the run aborts before spending past it.
# CONCEPT: loop — cost circuit-breaker guardrail.
MONTHLY_BUDGET_USD = 20.00

# WHAT: USD per million tokens, per model. Cache reads bill at ~0.1x input, cache
# writes at ~1.25x, so they are priced separately rather than folded into input.
# CONCEPT: eval — cost telemetry the Control Room can chart later.
PRICING_PER_MTOK = {
    FRAMER_MODEL: {"input": 5.00, "output": 25.00},
    CHECK_MODEL: {"input": 1.00, "output": 5.00},
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

WORDS_PER_MINUTE = 200

REPO = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO / "config" / "profile.json"
PROMPT_PATH = REPO / "prompts" / "Framer-Prompt-and-Sources.md"
USAGE_PATH = REPO / "data" / "state" / "usage.json"
DEFAULT_RAW_PATH = REPO / "data" / "verified" / "gatekeeper_2026-08-04.json"

# TODO: production scheduled runs should submit these through the Anthropic Message
# Batches API (client.messages.batches.create) — ~50% cheaper and async, which suits
# a brief that only has to be ready by morning. Interactive runs stay synchronous.


# ---------------------------------------------------------------- schemas

# WHAT: the exact JSON shape the Framer must return, enforced server-side.
# CONCEPT: harness — structured I/O. Validation happens at the API boundary, so we
# never parse prose or repair half-JSON. Nullable fields are anyOf because the
# schema dialect requires every property to be listed in `required`.
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

FRAMING_SCHEMA = {
    "type": "object",
    "properties": {
        "thread": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "why": _NULLABLE_STRING,
                    "example": _NULLABLE_STRING,
                    "connection": _NULLABLE_STRING,
                    "ninety": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "method": {"type": "string"},
                                    "result": {"type": "string"},
                                    "caveat": {"type": "string"},
                                },
                                "required": ["method", "result", "caveat"],
                                "additionalProperties": False,
                            },
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["id", "why", "example", "connection", "ninety"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["thread", "items"],
    "additionalProperties": False,
}

FAITHFULNESS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "confidence", "unsupported_claims"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

CHECK_SYSTEM_PROMPT = """You are a faithfulness grader. For each item you are given a \
source_excerpt (the only ground truth) and the framing written from it.

Flag every factual claim in the framing that does not trace to that item's excerpt: \
numbers, dates, versions, company names, benchmarks, capability claims. Do not flag \
reasoning, implications, comparisons between items, or the hypothetical scenario in a \
worked example — those are the writer's job. Flag only the facts a scenario leans on \
when those facts are absent from the excerpt.

Return confidence from 0.0 to 1.0: 1.0 when every claim traces cleanly, lower as \
unsupported claims accumulate in number and severity. Quote each unsupported claim \
verbatim and briefly say what is missing from the excerpt."""


# ---------------------------------------------------------------- io helpers


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def load_framer_system_prompt() -> str:
    """Pull the system prompt out of the markdown spec so prose and prompt can't drift."""
    # WHAT: read the Framer's system prompt verbatim from docs/, not from a string here.
    # CONCEPT: harness — one source of truth. The doc is the canonical recipe; this
    # file is the runner. Editing the prompt never means editing Python.
    text = PROMPT_PATH.read_text()
    match = re.search(
        r"<!--\s*FRAMER_SYSTEM_PROMPT:START\s*-->(.*?)<!--\s*FRAMER_SYSTEM_PROMPT:END\s*-->",
        text,
        re.DOTALL,
    )
    if not match:
        sys.exit(f"No FRAMER_SYSTEM_PROMPT markers found in {PROMPT_PATH}")
    return match.group(1).strip()


def estimate_cost_usd(model: str, usage) -> float:
    price = PRICING_PER_MTOK[model]
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * price["input"]
        + cache_read * price["input"] * CACHE_READ_MULTIPLIER
        + cache_write * price["input"] * CACHE_WRITE_MULTIPLIER
        + usage.output_tokens * price["output"]
    ) / 1_000_000


def load_usage_log() -> dict:
    if USAGE_PATH.exists():
        return load_json(USAGE_PATH)
    return {
        "note": "Append-only API spend log. Every call records tokens + estimated cost so "
        "the Control Room can chart cost per brief against MONTHLY_BUDGET_USD.",
        "calls": [],
    }


def month_to_date_spend(log: dict, month: str) -> float:
    return sum(c["estimated_cost_usd"] for c in log["calls"] if c["timestamp"].startswith(month))


def record_call(log: dict, user_id: str, phase: str, model: str, usage, timestamp: str) -> float:
    """Append one call's tokens and estimated cost to the usage log."""
    # WHAT: log every call's tokens and cost, keyed by user.
    # CONCEPT: eval — the sensor the cost circuit-breaker reads on the next run.
    cost = estimate_cost_usd(model, usage)
    log["calls"].append(
        {
            "user_id": user_id,
            "timestamp": timestamp,
            "phase": phase,
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "estimated_cost_usd": round(cost, 6),
        }
    )
    return cost


def structured_text(message) -> str:
    """Return the response text, refusing to guess when the model didn't finish cleanly."""
    # WHAT: check stop_reason before touching content.
    # CONCEPT: harness — fail loudly. A refusal has empty content and a truncated
    # response has invalid JSON; both must abort rather than emit a partial brief.
    if message.stop_reason == "refusal":
        sys.exit(f"Model declined the request ({message.stop_details}). Nothing written.")
    if message.stop_reason == "max_tokens":
        sys.exit("Response hit max_tokens and the JSON is truncated. Raise the cap and rerun.")
    return next(block.text for block in message.content if block.type == "text")


# ---------------------------------------------------------------- api calls


def frame_day(client, system_prompt: str, profile: dict, raw_day: dict):
    """Frame every item plus the day-level thread in a single structured call."""
    # WHAT: send the profile and all raw items in ONE request, not one per item.
    # CONCEPT: harness — batching. N items cost one round trip instead of N, and the
    # model can only write the cross-item "thread" if it sees every item at once.
    preferences = profile["preferences"]
    payload = {
        "reader_profile": {
            "role": profile["role"],
            "goals": profile["goals"],
            "interests": profile["interests"],
            "categories": profile["categories"],
            "daily_read_minutes_target": preferences["dailyReadMinutesTarget"],
            "gatekeeper_bias": profile["tuner"]["gatekeeperBias"],
        },
        "rules_for_this_request": {
            "worked_examples_only_for_categories": preferences["examplesOnlyForCategories"],
            "weave_connections": preferences["weaveConnections"],
            "quiet_day_allowed": preferences["quietDayAllowed"],
        },
        "date": raw_day["date"],
        "items": [
            {k: v for k, v in item.items() if k != "sources"} for item in raw_day["items"]
        ],
    }

    # WHAT: stream this call rather than waiting on one long response.
    # CONCEPT: harness — the SDK refuses non-streaming requests whose max_tokens
    # could outlast the HTTP timeout, and thinking is on by default on Opus 5, so
    # the budget has to cover reasoning as well as the JSON. We don't render the
    # tokens; get_final_message() just gives us timeout safety for free.
    with client.messages.stream(
        model=FRAMER_MODEL,
        max_tokens=FRAMER_MAX_TOKENS,
        # WHAT: the static prompt goes in `system` with a cache breakpoint; the
        # per-day profile and items go in the user turn, after it.
        # CONCEPT: harness — prompt caching. Caching is a prefix match, so stable
        # content must physically precede volatile content or nothing ever hits.
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        output_config={
            "format": {"type": "json_schema", "schema": FRAMING_SCHEMA},
            "effort": FRAMER_EFFORT,
        },
    ) as stream:
        message = stream.get_final_message()
    return json.loads(structured_text(message)), message.usage


def check_faithfulness(client, raw_day: dict, framing_by_id: dict):
    """Grade every item's framing against its own source excerpt, in one cheap call."""
    # WHAT: re-read our own output against the sources before emitting it, on a
    # cheaper model than the one that wrote it.
    # CONCEPT: loop — reflection / self-verification before emit; eval — grounding.
    # A second pair of eyes is worth more than a bigger model marking its own work.
    payload = [
        {
            "id": item["id"],
            "source_excerpt": item["source_excerpt"],
            "framing": framing_by_id.get(item["id"], {}),
        }
        for item in raw_day["items"]
    ]

    # No cache_control here: Haiku 4.5 needs a ~4096-token prefix before anything
    # caches, and this system prompt is far shorter than that.
    message = client.messages.create(
        model=CHECK_MODEL,
        max_tokens=CHECK_MAX_TOKENS,
        system=CHECK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        output_config={"format": {"type": "json_schema", "schema": FAITHFULNESS_SCHEMA}},
    )
    verdicts = json.loads(structured_text(message))["items"]
    return {v["id"]: v for v in verdicts}, message.usage


# ---------------------------------------------------------------- local compute


def assemble_day(profile: dict, raw_day: dict, framing: dict, verdicts: dict) -> dict:
    """Merge framing back onto the raw items and compute everything that isn't judgment."""
    framing_by_id = {f["id"]: f for f in framing["items"]}
    do_this_category = profile["preferences"]["doThisCategory"]
    example_categories = profile["preferences"]["examplesOnlyForCategories"]

    items, enforcements = [], []
    for rank, raw_item in enumerate(raw_day["items"], start=1):
        framed = framing_by_id.get(raw_item["id"], {})
        category = raw_item["category"]

        # WHAT: strip DO THIS and worked examples from categories the profile
        # doesn't allow them on, whatever the model returned.
        # CONCEPT: guardrail — enforce in code what the prompt only asks for.
        tags = list(raw_item["tags"])
        if category != do_this_category and "DO THIS" in tags:
            tags.remove("DO THIS")
            enforcements.append(f"{raw_item['id']}: dropped DO THIS ({category} is not {do_this_category})")

        example = framed.get("example")
        if example and category not in example_categories:
            example = None
            enforcements.append(f"{raw_item['id']}: dropped worked example ({category} not in {example_categories})")

        verdict = verdicts.get(raw_item["id"], {})
        item = {
            "id": raw_item["id"],
            # Rank is the Gatekeeper's ordering, carried through — not the Framer's call.
            "rank": rank,
            "category": category,
            "tags": tags,
            "headline": raw_item["headline"],
        }
        if framed.get("why"):
            item["why"] = framed["why"]
        if framed.get("ninety"):
            item["ninety"] = framed["ninety"]
        if example:
            item["example"] = example
        if framed.get("connection"):
            item["connection"] = framed["connection"]
        item["sources"] = raw_item["sources"]
        item["faithfulness"] = {
            "confidence": verdict.get("confidence"),
            "unsupported_claims": verdict.get("unsupported_claims", []),
        }
        items.append(item)

    # WHAT: reading time and the counts block, in plain Python — no API call.
    # CONCEPT: harness — don't pay a model to do arithmetic it can get wrong.
    prose = [framing["thread"]]
    for item in items:
        prose.append(item["headline"])
        prose.extend(str(item[k]) for k in ("why", "example", "connection") if k in item)
        if "ninety" in item:
            prose.extend(item["ninety"].values())
    reading_time = max(1, round(len(" ".join(prose).split()) / WORDS_PER_MINUTE))

    by_category = {}
    for category in profile["categories"]:
        count = sum(1 for i in items if i["category"] == category)
        if count:
            by_category[category] = count

    return {
        "user_id": profile["user_id"],
        "date": raw_day["date"],
        "generatedBy": {"agent": "Framer", "model": FRAMER_MODEL, "checkModel": CHECK_MODEL},
        "tunerStateVersion": profile["tuner"].get("lastRecalibrated") or "v0-seed",
        "readingTimeMin": reading_time,
        "counts": {
            "items": len(items),
            "doThis": sum(1 for i in items if "DO THIS" in i["tags"]),
            "byCategory": by_category,
        },
        "thread": framing["thread"],
        "items": items,
        "_enforcements": enforcements,
    }


# ---------------------------------------------------------------- main


def main() -> None:
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RAW_PATH
    if not raw_path.exists():
        sys.exit(f"No such raw day: {raw_path}")

    load_dotenv(REPO / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env (see prompts/p2-build-framer.md).")

    profile = load_json(PROFILE_PATH)
    raw_day = load_json(raw_path)
    system_prompt = load_framer_system_prompt()

    # WHAT: refuse to start if this month's spend is already over budget.
    # CONCEPT: loop — circuit breaker. Checked before the call, not after.
    now = datetime.now(timezone.utc)
    timestamp, month = now.isoformat(timespec="seconds"), now.strftime("%Y-%m")
    usage_log = load_usage_log()
    spent = month_to_date_spend(usage_log, month)
    if spent >= MONTHLY_BUDGET_USD:
        sys.exit(
            f"Month-to-date spend ${spent:.2f} has reached MONTHLY_BUDGET_USD "
            f"(${MONTHLY_BUDGET_USD:.2f}). Raise the budget in agents/framer.py or wait "
            "for the month to roll over. No API call made."
        )

    print(f"Framing {raw_day['date']} — {len(raw_day['items'])} items "
          f"({FRAMER_MODEL}, ${spent:.2f} spent this month)")

    client = anthropic.Anthropic()
    framing, framing_usage = frame_day(client, system_prompt, profile, raw_day)
    cost = record_call(usage_log, profile["user_id"], "framing", FRAMER_MODEL, framing_usage, timestamp)

    framing_by_id = {f["id"]: f for f in framing["items"]}
    verdicts, check_usage = check_faithfulness(client, raw_day, framing_by_id)
    cost += record_call(usage_log, profile["user_id"], "faithfulness", CHECK_MODEL, check_usage, timestamp)

    day = assemble_day(profile, raw_day, framing, verdicts)

    out_path = REPO / "data" / "briefs" / f"framer_{raw_day['date']}.json"
    out_path.write_text(json.dumps(day, indent=2, ensure_ascii=False) + "\n")
    USAGE_PATH.write_text(json.dumps(usage_log, indent=2) + "\n")

    flagged = [i for i in day["items"] if i["faithfulness"]["unsupported_claims"]]
    confidences = [i["faithfulness"]["confidence"] for i in day["items"]
                   if i["faithfulness"]["confidence"] is not None]

    print(f"\nWrote {out_path.relative_to(REPO)}")
    print(f"  {day['counts']['items']} items · {day['counts']['doThis']} DO THIS · "
          f"~{day['readingTimeMin']} min read")
    print(f"  faithfulness: {len(flagged)} item(s) flagged"
          + (f", lowest confidence {min(confidences):.2f}" if confidences else ""))
    for item in flagged:
        print(f"    ⚠ {item['id']} ({item['faithfulness']['confidence']:.2f})")
        for claim in item["faithfulness"]["unsupported_claims"]:
            print(f"        {claim}")
    for note in day["_enforcements"]:
        print(f"  enforced: {note}")
    print(f"  cost: ${cost:.4f} this run · ${spent + cost:.2f} month-to-date "
          f"of ${MONTHLY_BUDGET_USD:.2f}")


if __name__ == "__main__":
    main()
