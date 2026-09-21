"""Framer — turns a raw (verified but unframed) pool into the edition.

Reads (profile, raw_day) and writes a framed edition next to it. Two API calls per run:
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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# WHAT: the shared plumbing now lives in two sibling modules.
# CONCEPT: harness — extracted at M1, when Reader became the second caller of both.
# budget.py owns the ledger and the spend breaker; llm.py owns the client, the pinned
# models and the one guarded call path. This file keeps the Framer's own judgement.
#
# The try/except is not decoration: data/evals/run_evals.py puts agents/ on sys.path
# and does `import framer`, so these modules load as top-level names there and as
# `agents.framer` everywhere else.
try:
    from . import budget, llm
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import budget
    import llm

# WHAT: re-export every name that moved, because run_evals.py reaches all of them
# through `framer.X`.
# CONCEPT: harness — the façade is the compatibility contract. Twelve attributes are
# read off this module by the eval harness and EVERY ONE of its call sites sits on an
# API path, so `--no-api` would stay green while a rename broke the paid run. These
# aliases are what keep that from being possible.
FRAMER_MODEL = llm.FRAMER_MODEL
CHECK_MODEL = llm.CHECK_MODEL
FRAMER_MAX_TOKENS = llm.FRAMER_MAX_TOKENS
CHECK_MAX_TOKENS = llm.CHECK_MAX_TOKENS
FRAMER_EFFORT = llm.FRAMER_EFFORT
structured_text = llm.structured_text

MONTHLY_BUDGET_USD = budget.MONTHLY_BUDGET_USD
PRICING_PER_MTOK = budget.PRICING_PER_MTOK
CACHE_READ_MULTIPLIER = budget.CACHE_READ_MULTIPLIER
CACHE_WRITE_MULTIPLIER = budget.CACHE_WRITE_MULTIPLIER
USAGE_PATH = budget.USAGE_PATH
estimate_cost_usd = budget.estimate_cost_usd
load_usage_log = budget.load_usage_log
month_to_date_spend = budget.month_to_date_spend
record_call = budget.record_call

WORDS_PER_MINUTE = 200

REPO = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO / "config" / "profile.json"
PROMPT_PATH = REPO / "prompts" / "framer-prompt.md"
DEFAULT_RAW_PATH = REPO / "data" / "verified" / "gatekeeper_2026-08-04.json"

# TODO: production scheduled runs should submit these through the Anthropic Message
# Batches API (client.messages.batches.create) — ~50% cheaper and async, which suits
# a brief that only has to be ready by morning. Interactive runs stay synchronous.


# PIVOT NOTE (2026-09-16): the edition became weekly, and Scout + Gatekeeper became Reader.
# NO LOGIC IN THIS FILE CHANGED, on purpose. Moving a component and altering it in the
# same step means a red run afterwards cannot be attributed to either. The golden cases
# stay frozen for the same reason — re-cutting them would discard the baseline measured
# on 2026-09-15, which is the only thing the next change can be compared against.
#
# TWO PROFILE KEYS DID CHANGE, and this file reads them:
#   dailyReadMinutesTarget -> weeklyReadMinutesTarget
#   quietDayAllowed        -> quietWeekAllowed
# The payload keys sent to the model were renamed with them, because
# `daily_read_minutes_target` would instruct a weekly writer to target a daily read.
#
# WORTH RECORDING, because it is the exact failure this file's comments keep warning about:
# the config was renamed first and these call sites were missed, so the Framer raised
# KeyError on its first real run while `run_evals.py --no-api` stayed green. Both call
# sites sit on API paths the offline gate never reaches. "Nothing changed behaviourally"
# was true of the code and false of the system, because the config it reads is part of it.

# ---------------------------------------------------------------- schemas

# WHAT: the exact JSON shape the Framer must return, enforced server-side.
# CONCEPT: harness — structured I/O. Validation happens at the API boundary, so we
# never parse prose or repair half-JSON. Nullable fields are anyOf because the
# schema dialect requires every property to be listed in `required`.
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

# WHAT: the writer's own division of each field into checkable claims and its reading
# of them. Concatenating every run's text in order must reproduce the field exactly.
# CONCEPT: eval — grading scope, enforced by what we put in the payload rather than by
# asking for it. Only `fact` runs reach the grader, so interpretation cannot be flagged
# as unsupported: there is no evidence for "your input costs are being repriced", and
# there never could be. Measured first: two independent labelers agreed on this
# boundary for 94.6% of characters, which is why it is safe to ask one model for it.
# The split says what KIND a clause is, never whether it is true — a false claim is
# still a fact run, and failing its check is the point.
SPLIT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "class": {"type": "string", "enum": ["fact", "interpretation"]},
        },
        "required": ["text", "class"],
        "additionalProperties": False,
    },
}

FRAMING_SCHEMA = {
    "type": "object",
    "properties": {
        "thread": {"type": "string"},
        "threadSplit": SPLIT_SCHEMA,
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
                    "split": {
                        "type": "object",
                        "properties": {
                            "why": SPLIT_SCHEMA,
                            "example": SPLIT_SCHEMA,
                            "connection": SPLIT_SCHEMA,
                            "ninety.method": SPLIT_SCHEMA,
                            "ninety.result": SPLIT_SCHEMA,
                            "ninety.caveat": SPLIT_SCHEMA,
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["id", "why", "example", "connection", "ninety", "split"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["thread", "threadSplit", "items"],
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



# ---------------------------------------------------------------- api calls


def frame_day(client, system_prompt: str, profile: dict, raw_day: dict, timestamp: str, ledger: dict):
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
            "weekly_read_minutes_target": preferences["weeklyReadMinutesTarget"],
            "gatekeeper_bias": profile["tuner"]["gatekeeperBias"],
        },
        "rules_for_this_request": {
            "worked_examples_only_for_categories": preferences["examplesOnlyForCategories"],
            "weave_connections": preferences["weaveConnections"],
            "quiet_week_allowed": preferences["quietWeekAllowed"],
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
    text, usage, cost = llm.call(
        client,
        model=llm.FRAMER_MODEL,
        max_tokens=llm.FRAMER_MAX_TOKENS,
        stream=True,
        timestamp=timestamp,
        ledger=ledger,
        user_id=profile["user_id"],
        phase="framing",
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
            "effort": llm.FRAMER_EFFORT,
        },
    )
    return json.loads(text), usage, cost


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
                # Interpretation is excluded the same way, and for the same reason: it
                # is not the Framer's faithfulness burden either. No excerpt can settle
                # "your input costs are being repriced", so sending it to a grader can
                # only produce a flag nobody can act on.
                "writing_under_test": facts_only(framing_by_id.get(item["id"], {})),
                "ground_truth_excerpt": item["source_excerpt"],
            }
            for item in raw_day["items"]
        ],
        "thread": {
            "writing_under_test": join_runs(framing.get("threadSplit"), framing["thread"]),
            "_note": "Graded against the union below — it synthesizes across all items.",
            "ground_truth_excerpt_union": [
                {"id": item["id"], "source_excerpt": item["source_excerpt"]}
                for item in raw_day["items"]
            ],
        },
    }


def join_runs(runs, fallback: str) -> str:
    """The `fact` half of one field, in order. Falls back to the whole field if unsplit."""
    # WHAT: turn a split into the text the grader is allowed to see.
    # CONCEPT: eval — scope enforced by the payload. The fallback is deliberate: a
    # frozen brief written before the split existed is graded whole, as it was.
    if not runs:
        return fallback
    return "".join(r["text"] for r in runs if r["class"] == "fact").strip()


def facts_only(framing_item: dict) -> dict:
    """One item's writing, reduced to its factual clauses, field by field."""
    # WHAT: apply the item's own split to every field, dropping fields left empty.
    # CONCEPT: eval — an item whose `why` is pure interpretation contributes nothing to
    # grade, and an empty field is better than a field of unanswerable claims.
    split = framing_item.get("split") or {}
    out = {}
    for key, value in framing_item.items():
        if key in ("id", "split") or value is None:
            continue
        if key == "ninety" and isinstance(value, dict):
            kept = {
                sub: join_runs(split.get(f"ninety.{sub}"), text)
                for sub, text in value.items()
            }
            kept = {k: v for k, v in kept.items() if v}
            if kept:
                out[key] = kept
            continue
        text = join_runs(split.get(key), value)
        if text:
            out[key] = text
    return out


def coverage_failures(framing: dict) -> list[str]:
    """Every split must reproduce its field exactly — nothing dropped, nothing invented."""
    # WHAT: concatenate each split in order and compare to the field it describes.
    # CONCEPT: harness — the deterministic half of the fact/interpretation design. The
    # model decides which clauses are checkable; code decides that it accounted for all
    # of them. Without this, a claim can be hidden from the grader by omitting it from
    # the split entirely, and the omission is invisible.
    failures = []

    def check(label: str, runs, original: str) -> None:
        if runs is None:
            failures.append(f"{label}: no split")
        elif "".join(r["text"] for r in runs) != original:
            failures.append(f"{label}: split does not reconstruct the field")

    for item in framing["items"]:
        split = item.get("split") or {}
        for key in ("why", "example", "connection"):
            if item.get(key):
                check(f"{item['id']}/{key}", split.get(key), item[key])
        if isinstance(item.get("ninety"), dict):
            for sub, text in item["ninety"].items():
                check(f"{item['id']}/ninety.{sub}", split.get(f"ninety.{sub}"), text)
    check("thread", framing.get("threadSplit"), framing["thread"])
    return failures


def lifted_claims(claims: list, writing: str, ground_truth: str) -> list[str]:
    """Claims copied verbatim out of the source that do not appear in the writing at all."""
    # WHAT: the mirror of the span check. `span_found` proves the evidence is real;
    # this asks whether the CLAIM came from the right document.
    # CONCEPT: eval — verify the verifier. A grader that enumerates the excerpt's
    # sentences and checks them against the excerpt passes everything at confidence 1.0,
    # and no label can catch it that was not written to. Narrow on purpose: it fires
    # only on a verbatim lift, so a legitimate paraphrase of the writing never trips it,
    # and neither does writing that quotes its source exactly — that text is in both.
    # Warns; does not gate, until the false-alarm rate is measured.
    truth, written = _normalise_text(ground_truth), _normalise_text(writing)
    lifted = []
    for claim in claims:
        text = _normalise_text(claim.get("claim", ""))
        if text and text in truth and text not in written:
            lifted.append(claim["claim"])
    return lifted


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


def normalise_verdict(raw: dict, ground_truth: str, writing: str = "") -> dict:
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
        # Claims the grader copied verbatim out of the SOURCE that appear nowhere in the
        # writing. Warns; does not gate, until the false-alarm rate is measured.
        "lifted_claims": lifted_claims(raw.get("claims", []), writing, ground_truth),
    }


def check_faithfulness(client, raw_day: dict, framing: dict, timestamp: str | None = None,
                       ledger: dict | None = None, user_id: str | None = None):
    """Grade each item against its own excerpt and the thread against all of them, in one cheap call.

    `timestamp` and `ledger` are optional because data/evals/run_evals.py calls this
    with three arguments and books the call itself, under its own phase label and
    against its own log. Left unset, the call is still budget-checked; it is only the
    recording that the caller takes responsibility for.
    """
    # WHAT: re-read our own output against the sources before emitting it, on a
    # cheaper model than the one that wrote it. Items and thread in a single request.
    # CONCEPT: loop — reflection / self-verification before emit; eval — grounding.
    # A second pair of eyes is worth more than a bigger model marking its own work.
    # One call keeps the run at two API calls total, thread check included.
    payload = build_check_payload(raw_day, framing)

    # No cache_control here: Haiku 4.5 needs a ~4096-token prefix before anything
    # caches, and this system prompt is far shorter than that.
    stamp = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    text, usage, _cost = llm.call(
        client,
        model=llm.CHECK_MODEL,
        max_tokens=llm.CHECK_MAX_TOKENS,
        timestamp=stamp,
        ledger=ledger,
        user_id=user_id,
        phase="faithfulness",
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
    graded = json.loads(text)
    per_item_truth, union_truth = ground_truth_for(raw_day)
    # The grader saw only the fact clauses, so that is what "the writing" means when we
    # ask whether a claim came from the right document.
    seen = {u["id"]: json.dumps(u["writing_under_test"], ensure_ascii=False) for u in payload["items"]}
    verdicts = {
        v["id"]: normalise_verdict(v, per_item_truth.get(v["id"], ""), seen.get(v["id"], ""))
        for v in graded["items"]
    }
    thread_seen = payload["thread"]["writing_under_test"]
    return verdicts, normalise_verdict(graded["thread"], union_truth, thread_seen), usage


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
            "lifted_claims": verdict.get("lifted_claims", []),
            # The full ledger, not just the failures. Without it a later run cannot ask
            # what the grader enumerated — only what it rejected — and cross-run analysis
            # of a drifting grader becomes impossible after the fact.
            "claims": verdict.get("claims", []),
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
            "lifted_claims": thread_verdict.get("lifted_claims", []),
            # The full ledger, not just the failures. Without it a later run cannot ask
            # what the grader enumerated — only what it rejected — and cross-run analysis
            # of a drifting grader becomes impossible after the fact.
            "claims": thread_verdict.get("claims", []),
        },
        "items": items,
        "_enforcements": enforcements,
    }


# ---------------------------------------------------------------- main


def main() -> None:
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RAW_PATH
    if not raw_path.exists():
        sys.exit(f"No such raw day: {raw_path}")

    client = llm.build_client()

    profile = load_json(PROFILE_PATH)
    raw_day = load_json(raw_path)
    system_prompt = load_framer_system_prompt()

    # WHAT: read the ledger; llm.call() runs the breaker before each request.
    # CONCEPT: loop — circuit breaker, now stop-BEFORE rather than stop-after. The old
    # check here refused only once spend had already passed the ceiling, which let the
    # call that crossed the line through every time. budget.assert_within_budget adds
    # this call's worst case to month-to-date first, so the crossing never happens.
    now = datetime.now(timezone.utc)
    timestamp, month = now.isoformat(timespec="seconds"), now.strftime("%Y-%m")
    usage_log = budget.load_usage_log()
    spent = budget.month_to_date_spend(usage_log, month)

    print(f"Framing {raw_day['date']} — {len(raw_day['items'])} items "
          f"({llm.FRAMER_MODEL}, ${spent:.2f} spent this month)")

    try:
        framing, _framing_usage, _cost = frame_day(
            client, system_prompt, profile, raw_day, timestamp, usage_log
        )
        verdicts, thread_verdict, _check_usage = check_faithfulness(
            client, raw_day, framing, timestamp, usage_log, profile["user_id"]
        )
    except RuntimeError as breaker:
        # The ledger is written back on the way out so a partial run still books what
        # it spent; a call refused before it was made has nothing to book.
        budget.save_usage_log(usage_log)
        sys.exit(str(breaker))

    # WHAT: this run's cost is the ledger's movement, not a running total kept by hand.
    # CONCEPT: one source of truth. llm.call() books each call as it happens, so
    # re-reading the log is the only figure that cannot drift from what was recorded.
    cost = budget.month_to_date_spend(usage_log, month) - spent

    day = assemble_day(profile, raw_day, framing, verdicts, thread_verdict)

    out_path = REPO / "data" / "briefs" / f"framer_{raw_day['date']}.json"
    out_path.write_text(json.dumps(day, indent=2, ensure_ascii=False) + "\n")
    budget.save_usage_log(usage_log)

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
