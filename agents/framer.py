"""Framer — turns a raw (verified but unframed) day into the daily brief.

Reads (profile, raw_day) and writes a framed day next to it. Two API calls per run:
one Opus call that frames every item at once, one Haiku call that checks the framing
against the sources. Everything that is arithmetic rather than judgment (reading
time, counts, rank) is computed here in Python.

    python agents/framer.py                          # defaults to the 2026-08-04 sample
    python agents/framer.py data/verified/gatekeeper_2026-08-05.json

See prompts/framer-prompt.md for the prompt and prompts/framer-contract.md for the
output contract, and
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
# The grader now enumerates every claim with a quoted span before it judges, so its
# response is several times longer than the old bare verdict list. Truncation here
# aborts the run (see structured_text), so the cap has room to spare.
CHECK_MAX_TOKENS = 16_000
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
PROMPT_PATH = REPO / "prompts" / "framer-prompt.md"
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

# WHAT: one enumerated claim, with the excerpt span that supports it (or null).
# CONCEPT: harness — make the model show its work before it judges. The grader used
# to jump straight to a verdict and skipped clauses; requiring a quoted span per
# claim forces it to actually look for one. `supported` is checked against the span.
CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "supporting_span": _NULLABLE_STRING,
        "supported": {"type": "boolean"},
    },
    "required": ["claim", "supporting_span", "supported"],
    "additionalProperties": False,
}

_VERDICT_PROPERTIES = {
    "claims": {"type": "array", "items": CLAIM_SCHEMA},
    "confidence": {"type": "number"},
}

# WHAT: the grader returns a verdict per item AND one separate verdict for the thread.
# CONCEPT: eval — grading scope. The thread synthesizes across every item, so it gets
# its own unit graded against the union of excerpts, never against one item's excerpt.
FAITHFULNESS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, **_VERDICT_PROPERTIES},
                "required": ["id", "claims", "confidence"],
                "additionalProperties": False,
            },
        },
        "thread": {
            "type": "object",
            "properties": dict(_VERDICT_PROPERTIES),
            "required": ["claims", "confidence"],
            "additionalProperties": False,
        },
    },
    "required": ["items", "thread"],
    "additionalProperties": False,
}

CHECK_SYSTEM_PROMPT = """You are a faithfulness grader. You are given units of writing \
and, for each, the source excerpt(s) that are the ONLY ground truth for that unit.

The unit under test is `writing_under_test`. The excerpt is NOT under test — it is only \
ever the place you look for support. Never enumerate a claim that the excerpt makes but \
the writing does not; you would be grading the excerpt against itself.

Work through every unit in this order. Do not skip step 1 or 2.

1. Enumerate every factual claim `writing_under_test` makes: numbers, dates, versions, \
model and company names, benchmarks, capability claims. One entry per claim. Phrase each \
`claim` the way the WRITING phrases it, not the way the excerpt does — if the two are \
worded identically you have almost certainly copied from the excerpt by mistake.
2. For each claim, find the span of the excerpt that supports it and quote it verbatim \
in `supporting_span`. It must be a literal substring of the excerpt — copy the \
characters, never paraphrase or reconstruct them. Read the whole excerpt before \
deciding, including the later clauses of a sentence you have already drawn from — a \
claim is often supported by the second half of a sentence. If no span supports it, set \
`supporting_span` to null.
3. Only then set `supported`: true when you quoted a real span, false when you did not.
4. Only then set `confidence` for the unit: 1.0 when every claim traces cleanly, lower \
as unsupported claims accumulate in number and severity.

Scope — grade each unit against its own ground truth and nothing else:
- An item's framing is graded ONLY against that item's `source_excerpt`.
- The `thread` is graded against the UNION of every item's excerpt. It synthesizes \
across the whole day, so a claim supported by any one of those excerpts is supported.
- Headlines are written upstream by a different agent, not by the writer you are \
grading. They are out of scope: never enumerate a claim that appears only in a headline.

What counts as a claim — apply this test to every sentence before you enumerate it:

A claim is a checkable statement about the world: a number, a date, a version, a name, \
a benchmark result, or a statement that some named thing has some named property or \
capability. If a reader could disagree with the sentence without disputing anything in \
the excerpt, it is NOT a claim — it is the writer's reasoning, and reasoning is the \
writer's job, not yours. Enumerating it wastes the flag and buries the real thing.

Never enumerate: implications and consequences ("X means your Y no longer matters"), \
recommendations ("spend the quarter on Z"), evaluations ("that is the harder kind of \
engineering"), comparisons drawn between two items in the day, or anything hedged \
("suggests", "plausibly", "arguably"). A worked example may invent a scenario and its \
quantities; grade only the named facts it leans on, never the scenario itself or the \
conclusion it draws.

Most sentences mix fact with interpretation. Split them and enumerate only the \
checkable part. From "trillion-dollar rounds and the export ban argue the inputs are \
consolidating out of reach", the checkable parts are "there were trillion-dollar rounds" \
and "there is an export ban"; "inputs are consolidating out of reach" is the writer's \
argument and is not enumerated at all. The test: if no single span of the excerpt could \
settle it, it was never a claim — drop it rather than flagging it unsupported. This \
matters most in the `thread`, where nearly every sentence hangs an argument off a fact.

Worked contrast — for an excerpt that says only "Model M scores 71% on Bench B":
    claim, supported     "Model M scores 71% on Bench B"
    claim, unsupported   "Model M has a 400K context window"
    NOT a claim          "71% means the benchmark no longer separates the frontier"
    NOT a claim          "spend the quarter on your harness instead"
    NOT a claim          "if your agent makes 20 calls a task, that cost compounds\""""


# ---------------------------------------------------------------- io helpers


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def load_framer_system_prompt() -> str:
    """Read the Framer's system prompt from its own file."""
    # WHAT: the prompt file contains the prompt and nothing else, so this is a plain read.
    # CONCEPT: harness — one source of truth. The voice is content, not code: editing it
    # never means touching Python, and a prompt-only change shows as a prompt-only diff.
    # (The contract that describes this prompt's I/O lives in prompts/framer-contract.md.)
    return PROMPT_PATH.read_text().strip()



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


def build_check_payload(raw_day: dict, framing: dict) -> dict:
    """Assemble what the grader sees: each item with its own excerpt, the thread with all of them."""
    # WHAT: pair each unit of writing with exactly the ground truth it is allowed
    # to be graded against, and send the headline to neither.
    # CONCEPT: eval — grading scope, enforced by what we put in the payload rather
    # than only by asking for it. The thread can't be graded against a single
    # excerpt if a single excerpt is never what it's shown.
    # The writing under test comes FIRST in every unit and the ground truth second.
    # The grader reads the payload top-down; when the excerpt led, it enumerated the
    # excerpt's claims instead of the writing's and passed fabrications straight through.
    framing_by_id = {f["id"]: f for f in framing["items"]}
    return {
        "items": [
            {
                "id": item["id"],
                # Headlines come from the Gatekeeper upstream; the Framer didn't write
                # them, so they are not its faithfulness burden and never go in here.
                "writing_under_test": {
                    k: v
                    for k, v in framing_by_id.get(item["id"], {}).items()
                    if k != "id" and v is not None
                },
                "ground_truth_excerpt": item["source_excerpt"],
            }
            for item in raw_day["items"]
        ],
        "thread": {
            "writing_under_test": framing["thread"],
            "_note": "Graded against the union below — it synthesizes across all items.",
            "ground_truth_excerpt_union": [
                {"id": item["id"], "source_excerpt": item["source_excerpt"]}
                for item in raw_day["items"]
            ],
        },
    }


def ground_truth_for(raw_day: dict) -> tuple[dict, str]:
    """The text each unit is allowed to be supported by: own excerpt per item, union for the thread."""
    per_item = {item["id"]: item["source_excerpt"] for item in raw_day["items"]}
    return per_item, "\n".join(per_item.values())


def _normalise_text(text: str) -> str:
    return " ".join((text or "").split()).lower()


# WHAT: distinctive, checkable tokens inside a claim — numbers, dates, versions, names.
# CONCEPT: harness — a cheap deterministic signal where a semantic check is impossible.
# Numbers, money, percentages and dates only. Proper nouns are too weak a signal —
# every claim about an item names that item, so they fire on fabrications too.
_TOKEN_RE = re.compile(r"\$?\d[\d,.]*[%BMTK]?|\b\d{4}-\d{2}-\d{2}\b")


def suspicious_unsupported(claim: str, ground_truth: str) -> list[str]:
    """Tokens the grader called unsupported that are sitting in the excerpt verbatim."""
    # WHAT: when the grader says "no evidence", look for its own distinctive tokens in
    # the source. A hit does NOT prove the claim — "$122B" appearing says nothing about
    # who raised it — so this warns rather than overruling the verdict.
    # CONCEPT: eval — the one false-alarm signal code can produce. Verifying that
    # evidence is ABSENT is a semantic judgement Python cannot make; spotting that the
    # grader's own numbers are present is mechanical, and it is how $965B slipped past.
    truth = _normalise_text(ground_truth)
    hits = []
    for tok in _TOKEN_RE.findall(claim or ""):
        t = _normalise_text(tok)
        if len(t) >= 3 and t in truth and t not in hits:
            hits.append(tok)
    return hits


def normalise_verdict(raw: dict, ground_truth: str) -> dict:
    """Verify the grader's evidence, then derive the flag list from the claim ledger."""
    # WHAT: check that every span the grader quoted is literally in the excerpt, and
    # flag exactly those claims left without a real span.
    # CONCEPT: guardrail — verify the verifier's evidence in code. A grader can claim
    # a span it never found; a substring check cannot. Anything that fails this is
    # unsupported regardless of what the grader said, and is recorded as a grader
    # error so the eval can see the checker itself misbehaving.
    truth = _normalise_text(ground_truth)
    claims, unsupported, grader_errors, possible_false_alarms = [], [], [], []

    for claim in raw.get("claims", []):
        span = claim.get("supporting_span")
        span_found = bool(span) and _normalise_text(span) in truth
        if claim["supported"] and not span_found:
            reason = "quoted a span that is not in the excerpt" if span else "supported with no span"
            grader_errors.append(f"{claim['claim']!r}: {reason}")
        supported = claim["supported"] and span_found
        if not supported:
            hits = suspicious_unsupported(claim["claim"], ground_truth)
            if hits:
                possible_false_alarms.append(
                    f"{claim['claim']!r} — but {', '.join(hits)} appears in the excerpt")
        claims.append({**claim, "supported": supported, "span_verified": span_found})
        if not supported:
            unsupported.append(claim["claim"])

    return {
        "confidence": raw.get("confidence"),
        "claims": claims,
        "unsupported_claims": unsupported,
        "grader_errors": grader_errors,
        "possible_false_alarms": possible_false_alarms,
    }


def check_faithfulness(client, raw_day: dict, framing: dict):
    """Grade each item against its own excerpt and the thread against all of them, in one cheap call."""
    # WHAT: re-read our own output against the sources before emitting it, on a
    # cheaper model than the one that wrote it. Items and thread in a single request.
    # CONCEPT: loop — reflection / self-verification before emit; eval — grounding.
    # A second pair of eyes is worth more than a bigger model marking its own work.
    # One call keeps the run at two API calls total, thread check included.
    payload = build_check_payload(raw_day, framing)

    # No cache_control here: Haiku 4.5 needs a ~4096-token prefix before anything
    # caches, and this system prompt is far shorter than that.
    message = client.messages.create(
        model=CHECK_MODEL,
        max_tokens=CHECK_MAX_TOKENS,
        system=CHECK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        # WHAT: sample at zero so the same brief grades the same way twice.
        # CONCEPT: eval — a gate that flickers is not a gate. At default sampling this
        # grader disagreed with itself on borderline sentences roughly one run in three,
        # which would have failed builds at random. (Haiku 4.5 still accepts
        # temperature; it is rejected on Opus 5 and the other 4.6+ models.)
        temperature=0,
        output_config={"format": {"type": "json_schema", "schema": FAITHFULNESS_SCHEMA}},
    )
    graded = json.loads(structured_text(message))
    per_item_truth, union_truth = ground_truth_for(raw_day)
    verdicts = {
        v["id"]: normalise_verdict(v, per_item_truth.get(v["id"], "")) for v in graded["items"]
    }
    return verdicts, normalise_verdict(graded["thread"], union_truth), message.usage


# ---------------------------------------------------------------- local compute


def compute_reading_time(thread: str, items: list) -> int:
    """Reading time from the framed prose, in minutes."""
    # WHAT: count the words the reader actually reads and divide.
    # CONCEPT: harness — don't pay a model to do arithmetic it can get wrong.
    # run_evals.py imports this rather than reimplementing it, so a committed brief
    # whose header disagrees with its own body is a drift failure, not a formula fork.
    prose = [thread]
    for item in items:
        prose.append(item["headline"])
        prose.extend(str(item[k]) for k in ("why", "example", "connection") if k in item)
        if "ninety" in item:
            prose.extend(item["ninety"].values())
    return max(1, round(len(" ".join(prose).split()) / WORDS_PER_MINUTE))


def compute_counts(profile: dict, items: list) -> dict:
    """The counts block: items, DO THIS, and per-category totals in profile order."""
    by_category = {}
    for category in profile["categories"]:
        count = sum(1 for i in items if i["category"] == category)
        if count:
            by_category[category] = count
    return {
        "items": len(items),
        "doThis": sum(1 for i in items if "DO THIS" in i["tags"]),
        "byCategory": by_category,
    }


def assemble_day(
    profile: dict, raw_day: dict, framing: dict, verdicts: dict, thread_verdict: dict
) -> dict:
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
            "possible_false_alarms": verdict.get("possible_false_alarms", []),
        }
        items.append(item)

    return {
        "user_id": profile["user_id"],
        "date": raw_day["date"],
        "generatedBy": {"agent": "Framer", "model": FRAMER_MODEL, "checkModel": CHECK_MODEL},
        "tunerStateVersion": profile["tuner"].get("lastRecalibrated") or "v0-seed",
        # Reading time and counts are computed here, not asked of the model.
        "readingTimeMin": compute_reading_time(framing["thread"], items),
        "counts": compute_counts(profile, items),
        "thread": framing["thread"],
        # The thread's own verdict, graded against the union of every excerpt.
        "threadFaithfulness": {
            "confidence": thread_verdict.get("confidence"),
            "unsupported_claims": thread_verdict.get("unsupported_claims", []),
            "possible_false_alarms": thread_verdict.get("possible_false_alarms", []),
        },
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

    verdicts, thread_verdict, check_usage = check_faithfulness(client, raw_day, framing)
    cost += record_call(usage_log, profile["user_id"], "faithfulness", CHECK_MODEL, check_usage, timestamp)

    day = assemble_day(profile, raw_day, framing, verdicts, thread_verdict)

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
    thread_flags = day["threadFaithfulness"]["unsupported_claims"]
    thread_conf = day["threadFaithfulness"]["confidence"]
    print(f"  thread (vs union of excerpts): {len(thread_flags)} claim(s) flagged"
          + (f", confidence {thread_conf:.2f}" if thread_conf is not None else ""))
    for claim in thread_flags:
        print(f"        {claim}")
    alarms = [(i["id"], a) for i in day["items"]
              for a in i["faithfulness"].get("possible_false_alarms", [])]
    alarms += [("thread", a) for a in day["threadFaithfulness"].get("possible_false_alarms", [])]
    for unit, note in alarms:
        print(f"  ? possible false alarm — {unit}: {note}")

    for note in day["_enforcements"]:
        print(f"  enforced: {note}")
    print(f"  cost: ${cost:.4f} this run · ${spent + cost:.2f} month-to-date "
          f"of ${MONTHLY_BUDGET_USD:.2f}")


if __name__ == "__main__":
    main()
