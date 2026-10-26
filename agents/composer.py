"""The Framer's second pass: it writes the fields the Weekly Digest's NEWS tab needs.

The first pass (agents/framer.py) writes each story's `why`, `example` and `ninety` and a short thread,
and is unchanged: the golden cases, their gates and their baseline depend on it. This pass reads the same
verified excerpts, plus config/style-guide.md in its cached system prompt, and writes what the template's
slot list asks for (application/Weekly-Digest-Slots.html): the week in one thread, the Learning essay with
footnotes, each story's headline, hashtags and body, the top one or two as features, ties, dividers and the
closing thread.

Everything it writes goes through the same discipline as the first pass:

  * every prose field comes back with a fact/interpretation split that must reproduce it exactly (coverage);
  * the factual clauses are graded by the cheap grader against the excerpts they may draw on, in three kinds
    of unit: one per story (its own excerpt), the essay (the excerpts it lists in drawnFrom), and the week
    (thread, closing, ties and the section headline, against every excerpt);
  * the five hard style rules are checked by code (agents/style_check.py);
  * the slot list's counts and limits are checked by code (slot_failures);
  * a count of stories or items in the prose must be listed with the stories it counts, and code checks it (agents/counts.py).

Any failure blocks publishing. Nothing the model decides is trusted where code can decide it: the pill, the
sources and their one primary, the story ids, `checked`, the numbers and the "how this was made" paragraph are
all computed here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from . import budget, counts, framer, llm, style_check
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import budget
    import counts
    import framer
    import llm
    import style_check

REPO = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO / "prompts" / "composer-prompt.md"
STYLE_GUIDE_PATH = REPO / "config" / "style-guide.md"

# Added to every composing grader payload (the shared grader prompt is the one the golden baseline was measured with and is not edited).
# It restates what that prompt already requires, because Haiku sometimes joined two passages or added a quotation mark to a span it
# had found, and the literal span check rightly rejects a span that is not in the text.
GRADER_NOTE = ("Each `supporting_span` must be ONE contiguous run of characters copied exactly from the excerpt: do not join two passages, "
               "do not skip words inside it, and do not add, drop or move quotation marks. If a claim is supported in two places, quote the one place that settles it.")

COMPOSE_PHASE = "composing"
CHECK_PHASE = "composing-faithfulness"
# The cap covers the model's reasoning as well as the JSON. The first real run hit 30,000 at high effort (and cost $0.82 for a
# truncated answer), so the second pass runs at medium effort with room for the prose and a split of every field.
COMPOSE_MAX_TOKENS = 40_000
COMPOSE_EFFORT = "medium"
# What one composing run is expected to cost at most before it is flagged in the debrief (Run 3, Step C).
COST_NOTE_USD = 1.50

PILLS = ("DO THIS", "RADAR", "KNOW")        # one pill per story; the first of these a story's tags contain
MIN_STORIES, MAX_STORIES = 4, 11
MAX_FEATURES = 2

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_STRINGS = {"type": "array", "items": {"type": "string"}}

COMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "thread": {
            "type": "object",
            "properties": {"lines": _STRINGS, "body": {"type": "string"}},
            "required": ["lines", "body"], "additionalProperties": False,
        },
        "learning": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "paras": _STRINGS,
                "notes": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "term": {"type": "string"}, "definition": {"type": "string"}},
                    "required": ["key", "term", "definition"], "additionalProperties": False}},
                "drawnFrom": _STRINGS,
                "doDifferently": {"type": "string"},
            },
            "required": ["headline", "paras", "notes", "drawnFrom", "doDifferently"], "additionalProperties": False,
        },
        "storiesTitle": {"type": "string"},
        "stories": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "headline": {"type": "string"},
                "hashtags": _STRINGS,
                "body": {"type": "string"},
                "featureParas": {"anyOf": [_STRINGS, {"type": "null"}]},
                "tryIt": _NULLABLE_STRING,
                "tie": {"anyOf": [{"type": "object", "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
                                   "required": ["to", "text"], "additionalProperties": False}, {"type": "null"}]},
            },
            "required": ["id", "headline", "hashtags", "body", "featureParas", "tryIt", "tie"], "additionalProperties": False}},
        "dividers": {"type": "array", "items": {
            "type": "object", "properties": {"before": {"type": "string"}, "label": {"type": "string"}},
            "required": ["before", "label"], "additionalProperties": False}},
        "closing": {
            "type": "object",
            "properties": {"lines": _STRINGS, "summary": {"type": "string"}, "oneThing": {"type": "string"}},
            "required": ["lines", "summary", "oneThing"], "additionalProperties": False,
        },
        "splits": {"type": "array", "items": {
            "type": "object", "properties": {"path": {"type": "string"}, "runs": framer.SPLIT_SCHEMA},
            "required": ["path", "runs"], "additionalProperties": False}},
        "counts": {"type": "array", "items": {
            "type": "object", "properties": {"path": {"type": "string"}, "phrase": {"type": "string"}, "stories": _STRINGS},
            "required": ["path", "phrase", "stories"], "additionalProperties": False}},
    },
    "required": ["thread", "learning", "storiesTitle", "stories", "dividers", "closing", "splits", "counts"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- the request

def load_system_prompt() -> str:
    """The composer's brief, then config/style-guide.md: both in the cached system prompt."""
    return (PROMPT_PATH.read_text().strip() + "\n\n---\n\n# Style guide\n\n" + STYLE_GUIDE_PATH.read_text().strip())


def story_ids(raw_day: dict) -> dict:
    """The edition's story ids, s01 upward in rank order, mapped to the item ids."""
    return {f"s{n:02d}": item["id"] for n, item in enumerate(raw_day["items"], start=1)}


def source_label(item: dict) -> str:
    primary = next((s for s in item.get("sources") or [] if s.get("tier") == "primary"), None) or next(iter(item.get("sources") or []), None)
    return (primary or {}).get("label", "")


def composer_items(raw_day: dict, published: dict | None = None) -> list[dict]:
    """What the composer is shown per story, and what its facts are graded against (the same text)."""
    published = published or {}
    out = []
    for sid, iid in story_ids(raw_day).items():
        item = next(i for i in raw_day["items"] if i["id"] == iid)
        date = published.get(iid)
        header = f"Published {date} by {source_label(item)}. " if date else ""
        out.append({"id": sid, "item": iid, "headline": item["headline"], "tags": item["tags"], "source": source_label(item),
                    "published": date, "excerpt": header + item["source_excerpt"]})
    return out


def compose_request(system_prompt: str, profile: dict, raw_day: dict, framing: dict, window_label: str,
                    published: dict | None = None) -> dict:
    first_pass = {f["id"]: f for f in framing["items"]}
    stories = []
    for entry in composer_items(raw_day, published):
        f = first_pass.get(entry["item"], {})
        stories.append({**{k: v for k, v in entry.items() if k != "item"},
                        "why": f.get("why"), "ninety": f.get("ninety")})
    payload = {
        "reader_profile": {"role": profile["role"], "goals": profile["goals"], "interests": profile["interests"]},
        "edition_window": window_label,
        "date": raw_day["date"],
        "stories": stories,
    }
    return dict(
        model=llm.FRAMER_MODEL,
        max_tokens=COMPOSE_MAX_TOKENS,
        stream=True,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": json.dumps(payload, indent=2, ensure_ascii=False)}],
        output_config={"format": {"type": "json_schema", "schema": COMPOSE_SCHEMA}, "effort": COMPOSE_EFFORT},
    )


def compose(client, system_prompt: str, profile: dict, raw_day: dict, framing: dict, window_label: str,
            published: dict | None = None, *, conn=None):
    text, usage, cost = llm.call(client, user_id=profile["user_id"], phase=COMPOSE_PHASE, conn=conn,
                                 **compose_request(system_prompt, profile, raw_day, framing, window_label, published))
    return json.loads(text), usage, cost


# ---------------------------------------------------------------- the prose fields and their splits

def prose_fields(composed: dict) -> dict:
    """Every prose field the composer wrote, by split path."""
    out = {}
    thread, learning, closing = composed["thread"], composed["learning"], composed["closing"]
    for n, line in enumerate(thread["lines"], start=1):
        out[f"thread.line.{n}"] = line
    out["thread.body"] = thread["body"]
    out["learning.headline"] = learning["headline"]
    for n, para in enumerate(learning["paras"], start=1):
        out[f"learning.para.{n}"] = para
    for note in learning["notes"]:
        out[f"learning.note.{note['key']}"] = note["definition"]
    out["learning.doDifferently"] = learning["doDifferently"]
    out["stories.title"] = composed["storiesTitle"]
    for s in composed["stories"]:
        out[f"story.{s['id']}.headline"] = s["headline"]
        out[f"story.{s['id']}.body"] = s["body"]
        for n, para in enumerate(s.get("featureParas") or [], start=1):
            out[f"story.{s['id']}.feature.{n}"] = para
        if s.get("tryIt"):
            out[f"story.{s['id']}.tryIt"] = s["tryIt"]
        if s.get("tie"):
            out[f"story.{s['id']}.tie"] = s["tie"]["text"]
    for d in composed["dividers"]:
        out[f"divider.{d['before']}"] = d["label"]
    for n, line in enumerate(closing["lines"], start=1):
        out[f"closing.line.{n}"] = line
    out["closing.summary"] = closing["summary"]
    out["closing.oneThing"] = closing["oneThing"]
    return out


def split_map(composed: dict) -> dict:
    return {s["path"]: s["runs"] for s in composed.get("splits", [])}


def coverage_failures(composed: dict) -> list[str]:
    """Every prose field must have a split that reproduces it exactly, with no token cut across two runs."""
    failures, splits = [], split_map(composed)
    seen = [s["path"] for s in composed.get("splits", [])]
    if len(seen) != len(set(seen)):
        failures.append("splits: a path appears more than once")
    fields = prose_fields(composed)
    for path in seen:
        if path not in fields:
            failures.append(f"{path}: a split for a field that does not exist")
    for path, text in fields.items():
        runs = splits.get(path)
        if runs is None:
            failures.append(f"{path}: no split")
        elif "".join(r["text"] for r in runs) != text:
            failures.append(f"{path}: split does not reconstruct the field")
        elif any(r["text"].count("[[") != r["text"].count("]]") for r in runs):
            failures.append(f"{path}: a [[token]] is cut across two runs")
    return failures


_FLOOR_PATH = re.compile(r"^(?:thread\.body|learning\.para\.\d+|story\.s\d+\.body|story\.s\d+\.feature\.\d+|closing\.summary)$")


def fact_floor_failures(composed: dict) -> list[str]:
    """A field classed entirely as interpretation is never graded, so the prose that should state facts must contain some.

    Applies to the thread body, every essay paragraph, every story body and feature paragraph, and the closing summary. An essay
    paragraph must also OPEN on a fact (the style guide's first rule: a dated, attributed fact), so its first run is a fact run.
    """
    out, splits = [], split_map(composed)
    for path in prose_fields(composed):
        runs = splits.get(path)
        if runs is None or not _FLOOR_PATH.match(path):
            continue
        spoken = [r for r in runs if r["text"].strip()]
        if not any(r["class"] == "fact" for r in spoken):
            out.append(f"{path}: no clause is classed as a fact, so nothing in it is checked against the sources")
        elif path.startswith("learning.para.") and spoken and spoken[0]["class"] != "fact":
            out.append(f"{path}: the opening clause is classed as interpretation, but an essay paragraph opens on a dated, attributed fact")
    return out


def facts(composed: dict, path: str) -> str:
    """The factual clauses of one field, as the grader reads them: tokens replaced by the words they display."""
    runs = split_map(composed).get(path)
    text = prose_fields(composed)[path]
    # A split that does not reproduce the field, or one that calls a fact-bearing field wholly interpretation, would hide the field
    # from the grader. Those are style problems for the writer to fix; for the grader the whole field is a fact.
    if runs and "".join(r["text"] for r in runs) != text:
        runs = None
    if runs and _FLOOR_PATH.match(path) and not any(r["class"] == "fact" and r["text"].strip() for r in runs):
        runs = None
    return style_check.plain(framer.join_runs(runs, text))


# ---------------------------------------------------------------- slot rules

HASHTAG = re.compile(r"^#[A-Za-z][A-Za-z0-9]{1,30}$")
_HL = re.compile(r"\[\[hl\|([^\]]*)\]\]")
_LINK = re.compile(r"\[\[#([A-Za-z0-9_-]+)\|([^\]]*)\]\]")
_FN = re.compile(r"\[\[fn:([A-Za-z0-9_-]+)\|([^\]]*)\]\]")
_ANY_TOKEN = re.compile(r"\[\[[^\]]*\]\]")


def words(text: str) -> int:
    return len(style_check.plain(text).split())


def slot_failures(composed: dict, ids: dict) -> list[str]:
    """The slot list's counts and limits (application/Weekly-Digest-Slots.html), as code."""
    f = []
    thread, learning, closing = composed["thread"], composed["learning"], composed["closing"]
    stories = composed["stories"]
    story_ids_ = [s["id"] for s in stories]

    if story_ids_ != list(ids):
        f.append(f"stories: ids {story_ids_} are not the edition's {list(ids)} in order")
    if not MIN_STORIES <= len(stories) <= MAX_STORIES:
        f.append(f"stories: {len(stories)} stories, the template holds {MIN_STORIES} to {MAX_STORIES}")

    if len(thread["lines"]) != 2:
        f.append(f"thread.lines: {len(thread['lines'])} lines, exactly 2 are required")
    hl = [m for line in thread["lines"] for m in _HL.findall(line)]
    if len(hl) > 1:
        f.append("thread.lines: more than one [[hl|...]] across the pair")
    for phrase in hl + [m for m in _HL.findall(learning["headline"])] + _HL.findall(composed["storiesTitle"]):
        if not 1 <= len(phrase.split()) <= 5:
            f.append(f"[[hl|{phrase}]]: a highlight is one to five words")
    n = words(thread["body"])
    if not 80 <= n <= 120:
        f.append(f"thread.body: {n} words, 80 to 120 are required")
    links = _LINK.findall(thread["body"])
    if not 2 <= len(links) <= 3:
        f.append(f"thread.body: {len(links)} story links, 2 or 3 are required")
    for target, _ in links:
        if target not in ids:
            f.append(f"thread.body: a link to {target}, which is not a story in this edition")

    if not 6 <= len(learning["paras"]) <= 12:
        f.append(f"learning.paras: {len(learning['paras'])} paragraphs, 6 to 12 are required")
    keys = {note["key"] for note in learning["notes"]}
    used = [k for p in learning["paras"] for k, _ in _FN.findall(p)]
    for k in used:
        if k not in keys:
            f.append(f"learning: footnote [[fn:{k}|...]] has no note")
    for k in keys - set(used):
        f.append(f"learning.notes: note '{k}' is never used in a paragraph")
    if len(used) != len(set(used)):
        f.append("learning: a footnote key is used more than once")
    if len(keys) != len(learning["notes"]):
        f.append("learning.notes: a key appears twice")
    if not 2 <= len(learning["drawnFrom"]) <= 6 or any(d not in ids for d in learning["drawnFrom"]):
        f.append(f"learning.drawnFrom: {learning['drawnFrom']} must be 2 to 6 ids of stories in this edition")
    if not learning["doDifferently"].strip():
        f.append("learning.doDifferently is empty")

    features = [s for s in stories if s.get("featureParas")]
    if len(features) > MAX_FEATURES or any(s["id"] not in list(ids)[:MAX_FEATURES] for s in features):
        f.append(f"features: only the top {MAX_FEATURES} stories may be features")
    for s in stories:
        sid = s["id"]
        if not 1 <= len(s["hashtags"]) <= 4 or any(not HASHTAG.match(h) for h in s["hashtags"]):
            f.append(f"{sid}.hashtags: one to four labels like #Agents, got {s['hashtags']}")
        if not s["headline"].strip() or not s["body"].strip():
            f.append(f"{sid}: headline or body is empty")
        sentences = len(style_check.sentences(s["body"]))
        if not 3 <= sentences <= 5:
            f.append(f"{sid}.body: {sentences} sentences, 3 to 5 are required")
        if s.get("featureParas"):
            paras, total = len(s["featureParas"]), sum(words(p) for p in s["featureParas"])
            if not 3 <= paras <= 5 or not 300 <= total <= 500:
                f.append(f"{sid}.featureParas: {paras} paragraphs and {total} words, 3 to 5 paragraphs and 300 to 500 words are required")
        if s.get("tryIt"):
            t = len(style_check.sentences(s["tryIt"]))
            if not 2 <= t <= 4:
                f.append(f"{sid}.tryIt: {t} sentences, 2 to 4 are required")
        if s.get("tie") and s["tie"]["to"] == sid:
            f.append(f"{sid}.tie points at itself")
    for d in composed["dividers"]:
        if d["before"] not in ids:
            f.append(f"divider before {d['before']}: not a story in this edition")

    if len(closing["lines"]) != 2:
        f.append(f"closing.lines: {len(closing['lines'])} lines, exactly 2 are required")
    if not closing["summary"].strip() or not closing["oneThing"].strip():
        f.append("closing: summary or oneThing is empty")

    for headline_path, text in (("learning.headline", learning["headline"]), ("stories.title", composed["storiesTitle"])):
        if len(_HL.findall(text)) > 1:
            f.append(f"{headline_path}: more than one [[hl|...]]")
    if len(style_check.sentences(learning["doDifferently"])) > 2:
        f.append("learning.doDifferently: one instruction of one or two sentences is required")
    if len(style_check.sentences(closing["summary"])) != 2:
        f.append(f"closing.summary: {len(style_check.sentences(closing['summary']))} sentences, exactly 2 are required")
    for s in stories:
        if len(set(h.lower() for h in s["hashtags"])) != len(s["hashtags"]):
            f.append(f"{s['id']}.hashtags: a label is repeated")
    befores = [d["before"] for d in composed["dividers"]]
    if len(set(befores)) != len(befores):
        f.append("dividers: two dividers before the same story")
    # Tokens that are not part of the slot syntax would reach the page as visible brackets.
    for path, text in prose_fields(composed).items():
        for tok in _ANY_TOKEN.findall(text):
            headline_field = path in ("thread.line.1", "thread.line.2", "learning.headline", "stories.title")
            ok = ((_HL.fullmatch(tok) and headline_field) or (_LINK.fullmatch(tok) and path == "thread.body")
                  or (_FN.fullmatch(tok) and path.startswith("learning.para.")))
            if not ok:
                f.append(f"{path}: token {tok} is not allowed here (a highlight belongs in a headline, a story link in the thread body, a footnote in an essay paragraph)")
    return f


# ---------------------------------------------------------------- grading

def _story_paths(sid: str, fields: dict) -> list[str]:
    return [p for p in fields if p.startswith(f"story.{sid}.") and not p.endswith(".tie")]


def grading_units(composed: dict, raw_day: dict, published: dict | None = None) -> dict:
    """The grader's payload for everything the composer wrote, and the ground truth of each unit.

    A story unit is graded against that story's own excerpt; the essay against the excerpts it lists; the
    week (thread, closing, ties, section headline, dividers) against every excerpt. Writing under test comes
    first in each unit, as in the first pass.
    """
    ids = story_ids(raw_day)
    shown = {e["id"]: e for e in composer_items(raw_day, published)}
    fields = prose_fields(composed)
    items = []
    for sid in ids:
        writing = {p.split(".", 2)[2]: facts(composed, p) for p in _story_paths(sid, fields) if facts(composed, p)}
        if writing:
            items.append({"id": sid, "writing_under_test": writing, "ground_truth_excerpt": shown[sid]["excerpt"]})
    essay_paths = [p for p in fields if p.startswith("learning.")]
    essay = {p.split(".", 1)[1]: facts(composed, p) for p in essay_paths if facts(composed, p)}
    if essay:
        drawn = [d for d in composed["learning"]["drawnFrom"] if d in shown]
        items.append({"id": "essay", "writing_under_test": essay,
                      "ground_truth_excerpt": "\n\n".join(shown[d]["excerpt"] for d in drawn)})
    week_paths = [p for p in fields if p.startswith(("thread.", "closing.", "divider.", "stories.title")) or p.endswith(".tie")]
    week = " ".join(facts(composed, p) for p in week_paths if facts(composed, p)).strip()
    return {
        "items": items,
        "thread": {"writing_under_test": week, "_note": "Graded against the union below: the week's thread, closing and ties synthesise across stories.",
                   "ground_truth_excerpt_union": [{"id": sid, "source_excerpt": e["excerpt"]} for sid, e in shown.items()]},
    }


def check_faithfulness(client, composed: dict, raw_day: dict, published: dict | None, *, user_id: str, conn=None):
    """Grade the composed prose. Returns (verdicts by unit id, week verdict, usage, payload).

    Two grader calls: the stories and the week together, then the essay alone. The first real run sent the essay in the
    same payload as the rest and the grader returned no verdict for it, which the completeness gate (rightly) failed.
    """
    payload = grading_units(composed, raw_day, published)
    essay = [u for u in payload["items"] if u["id"] == "essay"]
    first = {**payload, "items": [u for u in payload["items"] if u["id"] != "essay"], "_grading_note": GRADER_NOTE}
    second = ({"items": essay, "thread": {"writing_under_test": "", "_note": "Nothing here is graded as the week.", "ground_truth_excerpt_union": []},
               "_grading_note": GRADER_NOTE} if essay else None)
    verdicts, week, usage = {}, None, None
    for number, part in enumerate(p for p in (first, second) if p):
        text, used, _cost = llm.call(client, user_id=user_id, phase=CHECK_PHASE, conn=conn, **framer.check_request(part))
        usage = used if usage is None else framer._add_usage(usage, used)
        graded = json.loads(text)
        truth = {u["id"]: u["ground_truth_excerpt"] for u in part["items"]}
        seen = {u["id"]: json.dumps(u["writing_under_test"], ensure_ascii=False) for u in part["items"]}
        for v in graded["items"]:
            if v["id"] in truth:
                verdicts[v["id"]] = framer.normalise_verdict(v, truth[v["id"]], seen[v["id"]])
        if number == 0:
            union = "\n".join(e["source_excerpt"] for e in part["thread"]["ground_truth_excerpt_union"])
            week = framer.normalise_verdict(graded["thread"], union, part["thread"]["writing_under_test"])
    return verdicts, week, usage, payload


def completeness_failures(payload: dict, verdicts: dict, week: dict) -> list[str]:
    """Anything with facts in it that came back ungraded: fail closed."""
    out = []
    for unit in payload["items"]:
        v = verdicts.get(unit["id"])
        if not v:
            out.append(f"{unit['id']}: the grader returned no verdict for the composed prose")
        elif not v.get("claims"):
            out.append(f"{unit['id']}: the grader enumerated no claims for the composed prose, so nothing was checked")
    if payload["thread"]["writing_under_test"] and not week.get("claims"):
        out.append("week: the grader enumerated no claims for the thread and closing, so nothing was checked")
    return out


def faithfulness_failures(verdicts: dict, week: dict) -> list[str]:
    out = [f"{uid}: unsupported (composed) — {c}" for uid, v in verdicts.items() for c in v["unsupported_claims"]]
    out += [f"week: unsupported (composed) — {c}" for c in week["unsupported_claims"]]
    return out


# ---------------------------------------------------------------- assembly

def pill_for(tags: list) -> str:
    for pill in PILLS:
        if pill in tags:
            return pill
    return "KNOW"


def story_sources(item: dict) -> list[dict]:
    """The story's links with exactly one primary, rendered first. Duplicates by address are dropped."""
    seen, out = set(), []
    for s in item.get("sources") or []:
        url = s.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append({"label": s.get("label") or url, "href": url, "primary": s.get("tier") == "primary"})
    if out and not any(s["primary"] for s in out):
        out[0]["primary"] = True
    chosen = False
    for s in out:                                     # exactly one primary, whatever the data held
        s["primary"], chosen = s["primary"] and not chosen, chosen or s["primary"]
    out.sort(key=lambda s: not s["primary"])
    return out


def how_made(considered: int, fetched: int, stories: int, merged: int, minutes: int, hosts: int) -> str:
    """The 'how this edition was made' paragraph, from counts alone: no model writes about its own process."""
    dup = f", folded {merged} duplicate{'s' if merged != 1 else ''} into the story they repeated" if merged else ""
    return (f"The Reader looked at {considered} candidate links from {hosts} places, fetched {fetched} pages in full{dup}, and kept {stories} stories. "
            f"About {minutes} minutes of reading stand behind them. Every clause the writer marked as a fact was checked by a second model against the page "
            f"it came from; a count across stories and a figure the writer computed are its own and are not. A story marked checked had no unsupported claim.")


def assemble_edition(composed: dict, raw_day: dict, framing: dict, verdicts: dict, week: dict, *, stats: dict, source_minutes: int,
                     style: list[str], published: dict | None = None) -> dict:
    ids = story_ids(raw_day)
    by_item = {i["id"]: i for i in raw_day["items"]}
    first_pass = {f["id"]: f for f in framing["items"]}
    flagged = {u.split(":", 1)[0] for u in faithfulness_failures(verdicts, week)}
    ties = {}
    for s in composed["stories"]:
        if s.get("tie") and s["tie"]["to"] in ids and s["tie"]["to"] != s["id"]:
            ties[s["id"]] = s["tie"]
    stories = []
    for n, s in enumerate(composed["stories"]):
        item = by_item[ids[s["id"]]]
        fp = first_pass.get(item["id"], {})
        entry = {
            "id": s["id"], "itemId": item["id"], "tag": pill_for(item["tags"]), "hashtags": s["hashtags"],
            "headline": s["headline"], "body": s["body"], "sources": story_sources(item),
            "checked": s["id"] not in flagged and bool((verdicts.get(s["id"]) or {}).get("claims")),
        }
        if s.get("featureParas") and n < MAX_FEATURES:
            entry["feature"] = True
            entry["featureParas"] = s["featureParas"]
        if fp.get("why"):
            entry["why"] = fp["why"]
        if s.get("tryIt"):
            entry["tryIt"] = s["tryIt"]
        if isinstance(fp.get("ninety"), dict):
            entry["ninety"] = [["METHOD", fp["ninety"].get("method", "")], ["RESULT", fp["ninety"].get("result", "")], ["CAVEAT", fp["ninety"].get("caveat", "")]]
        if s["id"] in ties:
            entry["tie"] = ties[s["id"]]
        stories.append(entry)
    learning = composed["learning"]
    considered = int(stats.get("candidates", 0))
    fetched = int(stats.get("fetched", 0))
    merged = int(stats.get("merged", 0))
    hosts = int(stats.get("feeds_ok", 0))
    drawn = [d for d in learning["drawnFrom"] if d in ids]
    return {
        "version": 1,
        "thread": {"lines": composed["thread"]["lines"], "body": composed["thread"]["body"]},
        "learning": {
            "headline": learning["headline"], "paras": learning["paras"],
            "notes": {n["key"]: [n["term"], n["definition"]] for n in learning["notes"]},
            "drawnFrom": drawn,
            "numbers": [["Candidate links considered", considered], ["Stories synthesised", len(stories)], ["Minutes of source reading", source_minutes]],
            "howMade": how_made(considered, fetched, len(stories), merged, source_minutes, hosts),
            "doDifferently": learning["doDifferently"],
        },
        "storiesTitle": composed["storiesTitle"],
        "counts": composed.get("counts") or [],         # the audit trail for every count across stories the prose states
        "stories": stories,
        "dividers": [d for d in composed["dividers"] if d["before"] in ids],
        "closing": composed["closing"],
        "faithfulness": {
            "units": {uid: {k: v.get(k) for k in ("confidence", "claims", "unsupported_claims", "possible_false_alarms", "lifted_claims", "grader_errors")}
                      for uid, v in verdicts.items()},
            "week": {k: week.get(k) for k in ("confidence", "claims", "unsupported_claims", "possible_false_alarms", "lifted_claims", "grader_errors")},
        },
        "style": {"failures": style},
        "composedBy": {"agent": "Framer (second pass)", "model": llm.FRAMER_MODEL, "checkModel": llm.CHECK_MODEL},
    }


def edition_failures(composed: dict, raw_day: dict, payload: dict, verdicts: dict, week: dict) -> list[str]:
    """Every hard-gate failure of the composed prose. Empty means the edition may publish."""
    ids = story_ids(raw_day)
    out = coverage_failures(composed) + fact_floor_failures(composed) + slot_failures(composed, ids)
    out += counts.count_failures(composed, ids, prose_fields(composed))
    out += [f"style — {line}" for line in style_check.edition_style_failures(composed)]
    out += completeness_failures(payload, verdicts, week) + faithfulness_failures(verdicts, week)
    return out


# ---------------------------------------------------------------- one revision pass

REVISE_SCHEMA = {
    "type": "object",
    "properties": {"fields": {"type": "array", "items": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "text": {"type": "string"}, "runs": framer.SPLIT_SCHEMA},
        "required": ["path", "text", "runs"], "additionalProperties": False}}},
    "required": ["fields"], "additionalProperties": False,
}

_REVISABLE_SLOT = (
    (re.compile(r"^thread\.body: (.*)$"), lambda m: "thread.body"),
    (re.compile(r"^(s\d\d)\.body: (.*)$"), lambda m: f"story.{m.group(1)}.body"),
    (re.compile(r"^(s\d\d)\.tryIt: (.*)$"), lambda m: f"story.{m.group(1)}.tryIt"),
)


def revisable_problems(composed: dict, ids: dict) -> dict:
    """The fields the writer can be asked to rewrite, with what is wrong with each: style findings and per-field slot failures.

    Everything else (a skipped story, a footnote without a note, too few paragraphs) is not repairable one field at a time and is
    left to fail the gate.
    """
    problems: dict = {}
    for line in coverage_failures(composed):
        path, _, message = line.partition(": ")
        if path in prose_fields(composed) and message in ("no split", "split does not reconstruct the field") or "cut across two runs" in message:
            problems.setdefault(path, []).append(f"{path}: {message}: return the same text with a split that reproduces it exactly, character for character")
    for line in fact_floor_failures(composed):
        path, _, message = line.partition(": ")
        problems.setdefault(path, []).append(f"{message}: classify the clauses again (a clause one span of the excerpt could settle is a fact), keeping the text")
    for path, line in style_check.edition_style_findings(composed):
        problems.setdefault(path, []).append(line)
    for line in counts.count_failures(composed, ids, prose_fields(composed)):
        path, _, message = line.partition(": ")
        problems.setdefault(path, []).append(f"{message}. In this rewrite state it without a number of stories (you cannot add `counts` entries here)")
    for line in slot_failures(composed, ids):
        for pattern, to_path in _REVISABLE_SLOT:
            m = pattern.match(line)
            if m:
                problems.setdefault(to_path(m), []).append(line)
    return problems


def set_field(composed: dict, path: str, text: str) -> bool:
    """Write `text` into the field a split path names. False if the path names nothing."""
    parts = path.split(".")
    try:
        if parts[0] == "thread" and parts[1] == "line":
            composed["thread"]["lines"][int(parts[2]) - 1] = text
        elif path == "thread.body":
            composed["thread"]["body"] = text
        elif path == "learning.headline":
            composed["learning"]["headline"] = text
        elif parts[:2] == ["learning", "para"]:
            composed["learning"]["paras"][int(parts[2]) - 1] = text
        elif parts[:2] == ["learning", "note"]:
            note = next(n for n in composed["learning"]["notes"] if n["key"] == parts[2])
            note["definition"] = text
        elif path == "learning.doDifferently":
            composed["learning"]["doDifferently"] = text
        elif path == "stories.title":
            composed["storiesTitle"] = text
        elif parts[0] == "story":
            story = next(s for s in composed["stories"] if s["id"] == parts[1])
            if parts[2] in ("headline", "body", "tryIt"):
                story[parts[2]] = text
            elif parts[2] == "tie":
                story["tie"]["text"] = text
            elif parts[2] == "feature":
                story["featureParas"][int(parts[3]) - 1] = text
            else:
                return False
        elif parts[0] == "divider":
            next(d for d in composed["dividers"] if d["before"] == parts[1])["label"] = text
        elif parts[:2] == ["closing", "line"]:
            composed["closing"]["lines"][int(parts[2]) - 1] = text
        elif path in ("closing.summary", "closing.oneThing"):
            composed["closing"][path.split(".")[1]] = text
        else:
            return False
    except (IndexError, KeyError, StopIteration, ValueError, TypeError):
        return False
    return True


REVISE_NOTE = (
    "REVISION. The edition you wrote was checked by code. The fields listed in `revise` broke the rules named under `problems`. Rewrite "
    "ONLY those fields: fix every problem named, keep every factual clause inside the excerpts, keep any [[...]] tokens and footnote keys "
    "that are still needed, and keep the field's job (an essay paragraph stays an essay paragraph). Return each as {path, text, runs}: `runs` "
    "is the fact/interpretation split of the new text and must reproduce it exactly. Do not return any field not listed."
)


def revise_request(system_prompt: str, profile: dict, raw_day: dict, framing: dict, window_label: str, published: dict | None,
                   composed: dict, problems: dict) -> dict:
    base = compose_request(system_prompt, profile, raw_day, framing, window_label, published)
    payload = json.loads(base["messages"][0]["content"])
    current = prose_fields(composed)
    payload["revise"] = [{"path": p, "current_text": current[p], "problems": msgs} for p, msgs in problems.items() if p in current]
    payload["instruction"] = REVISE_NOTE
    return {**base, "messages": [{"role": "user", "content": json.dumps(payload, indent=2, ensure_ascii=False)}],
            "output_config": {"format": {"type": "json_schema", "schema": REVISE_SCHEMA}, "effort": COMPOSE_EFFORT}}


def revise(client, system_prompt: str, profile: dict, raw_day: dict, framing: dict, window_label: str, published: dict | None,
           composed: dict, problems: dict, *, conn=None):
    """One rewrite of the listed fields. Returns (composed with those fields replaced, usage, cost, the paths replaced).

    Only listed paths are taken from the answer; a field that does not exist, or one the writer was not asked about, is ignored.
    The gates then run on the result exactly as they would have on the first draft.
    """
    text, usage, cost = llm.call(client, user_id=profile["user_id"], phase=COMPOSE_PHASE + "-revise", conn=conn,
                                 **revise_request(system_prompt, profile, raw_day, framing, window_label, published, composed, problems))
    answer = json.loads(text)
    out = json.loads(json.dumps(composed))
    replaced, splits = [], {s["path"]: s for s in out["splits"]}
    for field in answer.get("fields", []):
        path = field["path"]
        if path in problems and path not in replaced and set_field(out, path, field["text"]):
            splits[path] = {"path": path, "runs": field["runs"]}
            replaced.append(path)
    merged, seen = [], set()
    for entry in [*composed["splits"], *({"path": p} for p in replaced)]:    # existing order first, then splits for fields that had none
        path = entry["path"]
        if path not in seen:
            seen.add(path)
            merged.append(splits[path] if path in splits else entry)
    out["splits"] = merged
    return out, usage, cost, replaced


MAX_REVISIONS = 1     # one automatic repair attempt (Run 5): a style, length or split problem that survives it is a warning, not a block


def compose_with_revision(client, system_prompt: str, profile: dict, raw_day: dict, framing: dict, window_label: str,
                          published: dict | None = None, *, conn=None):
    """The first draft, then up to MAX_REVISIONS rewrites of the fields that code found broken.

    Returns (composed, record, cost). `record` keeps the first draft, what was wrong with which field in each round and which
    fields were rewritten, so the run's evidence shows the draft as well as the result. Nothing is loosened: the gates run on the result.
    """
    composed, _usage, cost = compose(client, system_prompt, profile, raw_day, framing, window_label, published, conn=conn)
    record = {"first_draft": composed, "problems": {}, "replaced": [], "rounds": []}
    for number in range(MAX_REVISIONS):
        problems = revisable_problems(composed, story_ids(raw_day))
        if number == 0:
            record["problems"] = problems
        if not problems:
            break
        composed, _usage, extra, replaced = revise(client, system_prompt, profile, raw_day, framing, window_label, published,
                                                   composed, problems, conn=conn)
        cost += extra
        record["replaced"] += [p for p in replaced if p not in record["replaced"]]
        record["rounds"].append({"problems": problems, "replaced": replaced})
    return composed, record, cost
