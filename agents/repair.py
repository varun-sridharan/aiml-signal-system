"""A failed check stops a sentence or a story, never the week (Run 5, Step A; plan decision log 2026-10-25).

The claim gate used to be all or nothing: one unsupported clause anywhere stopped the edition, and most of those verdicts
were false alarms. This module gives a failure a size, and keeps the one rule that never bends: nothing unsupported is
published.

  1. An "unsupported" verdict acts only after a second, independent reader agrees (a different model, asked a different
     question: is this claim in the excerpt, and which numbered sentence states it?). A second reader that finds the claim
     supported dismisses the verdict.
  2. The sentence the second reader names gets ONE rewrite, alone, by the writer, and everything is graded again.
  3. A sentence that still fails (and a second reader agrees again) is REMOVED. If it was load-bearing (a story's headline,
     or the only fact left in its body, or part of a paper's 90-second summary) the STORY is held instead. In the essay or
     the thread only the sentence goes, never the paragraph's other facts.

A "store" is a set of editable prose fields with their fact/interpretation splits. Two exist: the composing pass's edition
(ComposedStore) and the first pass's items (FramingStore). The engine here knows neither; it edits text and runs.
"""

from __future__ import annotations

import copy
import json
import re

try:
    from . import budget, citegate, composer, counts, framer, llm, style_check
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import budget
    import citegate
    import composer
    import counts
    import framer
    import llm
    import style_check

SECOND_PHASE = "repair-second"
REWRITE_PHASE = "repair-rewrite"


# ---------------------------------------------------------------- text and runs

def as_runs(text: str, runs) -> list:
    """The field's split, or, when it is missing or does not reproduce the text, the whole field as one fact: graded in full."""
    if runs and "".join(r["text"] for r in runs) == text:
        return [dict(r) for r in runs]
    return [{"text": text, "class": "fact"}]


def graded_runs(text: str, runs) -> list:
    """The split the grader should be shown: the writer's own when it reproduces the field and keeps some fact in it, else the whole field."""
    full = as_runs(text, runs)
    return full if any(r["class"] == "fact" and r["text"].strip() for r in full) else [{"text": text, "class": "fact"}]


def slice_runs(runs: list, a: int, b: int) -> list:
    """The runs covering characters [a, b) of the field, cut at the edges."""
    out, pos = [], 0
    for r in runs:
        lo, hi = pos, pos + len(r["text"])
        pos = hi
        if hi <= a or lo >= b:
            continue
        piece = r["text"][max(a, lo) - lo:min(b, hi) - lo]
        if piece:
            out.append({"text": piece, "class": r["class"]})
    return out


def replace_span(text: str, runs, a: int, b: int, new_text: str, new_runs: list) -> tuple:
    full = as_runs(text, runs)
    merged = slice_runs(full, 0, a) + [r for r in new_runs if r["text"]] + slice_runs(full, b, len(text))
    return text[:a] + new_text + text[b:], merged


def remove_span(text: str, runs, a: int, b: int) -> tuple:
    """Drop [a, b) and one neighbouring space, so no double space or stray leading space is left behind."""
    if b < len(text) and text[b] == " ":
        b += 1
    elif a > 0 and text[a - 1] == " ":
        a -= 1
    return replace_span(text, runs, a, b, "", [])


def fact_sentences(text: str, runs) -> list:
    """(start, end) of every sentence of the field that holds a fact run: the sentences the grader reads."""
    pos, facts = 0, []
    for r in as_runs(text, runs):
        if r["class"] == "fact" and r["text"].strip():
            facts.append((pos, pos + len(r["text"])))
        pos += len(r["text"])
    return [(a, b) for a, b in citegate.sentence_spans(text) if any(fa < b and fb > a for fa, fb in facts)]


def find_sentence(text: str, sentence: str):
    for a, b in citegate.sentence_spans(text):
        if text[a:b] == sentence:
            return a, b
    return None


# ---------------------------------------------------------------- the second reader and the rewrite

SECOND_SYSTEM = (
    "You are the second, independent reader of a fact-check. Another reader flagged a CLAIM as not supported by the EXCERPT. "
    "Read the excerpt yourself and decide: is the claim established by the excerpt (a paraphrase is fine; a figure, name, date, "
    "version and who-did-what must all be there)? Answer supported=false only if something the claim states is missing from the "
    "excerpt or contradicted by it. If it is not supported, list the ids of the numbered SENTENCES that state the unsupported "
    "part. Judge the excerpt, not how plausible the claim is."
)
SECOND_SCHEMA = {"type": "object", "properties": {
    "supported": {"type": "boolean"}, "sentence_ids": {"type": "array", "items": {"type": "integer"}}, "why": {"type": "string"}},
    "required": ["supported", "sentence_ids", "why"], "additionalProperties": False}

REWRITE_SYSTEM = (
    "You rewrite ONE sentence of a news digest so that every factual statement in it is established by the EXCERPT. A fact-check found "
    "the PROBLEM claim unsupported. Keep what the excerpt supports, drop or soften what it does not, keep the sentence's job and voice, "
    "keep any [[...]] tokens that still apply, and add no figure, name, date or capability the excerpt does not contain. Return the new "
    "sentence as `text` and its fact/interpretation split as `runs` (runs concatenate to text exactly; a clause one span of the excerpt "
    "could settle is a fact). If nothing true can be said, return an empty text and no runs."
)
REWRITE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}, "runs": framer.SPLIT_SCHEMA},
                  "required": ["text", "runs"], "additionalProperties": False}


def _overlap(a: str, b: str) -> int:
    wa, wb = set(re.findall(r"[a-z0-9]+", a.lower())), set(re.findall(r"[a-z0-9]+", b.lower()))
    return len(wa & wb)


def second_check(client, claim: str, candidates: list, excerpt: str, *, user_id: str, conn=None) -> tuple:
    """(agrees_unsupported, [(path, sentence)], cost). `candidates` is [(path, sentence text)] for the unit's fact sentences."""
    payload = {"claim": claim, "excerpt": excerpt, "sentences": [{"id": n, "text": style_check.plain(s)} for n, (_, s) in enumerate(candidates)]}
    text, _usage, cost = llm.call(
        client, model=llm.FRAMER_MODEL, max_tokens=4000, user_id=user_id, phase=SECOND_PHASE, conn=conn, system=SECOND_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, indent=1, ensure_ascii=False)}],
        output_config={"format": {"type": "json_schema", "schema": SECOND_SCHEMA}, "effort": "low"})
    try:
        answer = json.loads(text)
        supported = answer["supported"] is True
        ids = [i for i in answer["sentence_ids"] if isinstance(i, int) and 0 <= i < len(candidates)]
    except (ValueError, KeyError, TypeError):
        supported, ids = False, []           # an unreadable second reader cannot dismiss a verdict: it stands, and the sentence is located by words
    if supported:
        return False, [], cost
    if not ids and candidates:               # it agreed but did not say where: the sentence that shares most words with the claim
        ids = [max(range(len(candidates)), key=lambda n: _overlap(claim, candidates[n][1]))]
    return True, [candidates[i] for i in ids], cost


def rewrite_sentence(client, sentence: str, field_text: str, claim: str, excerpt: str, *, user_id: str, conn=None) -> tuple:
    """(new text, new runs, cost) or (None, None, cost) when the answer is unusable."""
    payload = {"sentence": sentence, "problem": claim, "field": field_text, "excerpt": excerpt}
    text, _usage, cost = llm.call(
        client, model=llm.FRAMER_MODEL, max_tokens=4000, user_id=user_id, phase=REWRITE_PHASE, conn=conn, system=REWRITE_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, indent=1, ensure_ascii=False)}],
        output_config={"format": {"type": "json_schema", "schema": REWRITE_SCHEMA}, "effort": "low"})
    try:
        new = json.loads(text)["text"].strip()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, None, cost
    # The writer's own split of its rewrite is not trusted: a sentence it called interpretation would escape the regrade. The
    # whole rewritten sentence is a fact, and is graded as one.
    return new, ([{"text": new, "class": "fact"}] if new else []), cost


# ---------------------------------------------------------------- the engine

def settle(store, verdicts: dict, client, *, user_id: str, conn=None) -> tuple:
    """Act on the grader's unsupported verdicts. Returns (the final verdicts, a log of everything done).

    `verdicts` is {unit: verdict} as the store graded it. The store's text is edited in place; stories to hold are recorded in
    log['held'] ({owner: [reasons]}); log['unlocatable'] lists flags that could not be tied to a sentence.
    """
    log = {"flags": 0, "dismissed": [], "second_unreadable": 0, "rewritten": [], "removed": [], "held": {}, "unlocatable": [],
           "regraded": False, "cost_usd": 0.0}

    def agreed(vs: dict) -> list:
        out = []
        for unit, verdict in vs.items():
            for claim in verdict.get("unsupported_claims", []):
                log["flags"] += 1
                cands = [(p, store.get(p)[0][a:b]) for p in store.paths(unit) for a, b in fact_sentences(*store.get(p))]
                ok, locs, cost = second_check(client, claim, cands, store.truth(unit), user_id=user_id, conn=conn)
                log["cost_usd"] += cost
                if not ok:
                    log["dismissed"].append({"unit": unit, "claim": claim})
                elif not locs:
                    log["unlocatable"].append({"unit": unit, "claim": claim})
                else:
                    out.append((unit, claim, locs))
        return out

    first = agreed(verdicts)
    if not first and not log["unlocatable"]:
        return verdicts, log
    for unit, claim, locs in first:
        for path, sentence in locs:
            text, runs = store.get(path)               # a sentence already rewritten or removed by an earlier flag is not found again
            span = find_sentence(text, sentence)
            if span is None:
                continue
            new, new_runs, cost = rewrite_sentence(client, sentence, text, claim, store.truth(unit), user_id=user_id, conn=conn)
            log["cost_usd"] += cost
            if new is None:                     # an unusable rewrite is a rewrite that failed: the sentence goes now
                _drop(store, path, sentence, claim, log, why="the one rewrite came back unusable")
            elif new:
                t2, r2 = replace_span(text, runs, span[0], span[1], new, new_runs)
                store.put(path, t2, r2)
                log["rewritten"].append({"path": path, "unit": unit, "claim": claim})
            else:
                _drop(store, path, sentence, claim, log, why="the writer could find nothing true to say")
    verdicts, cost = store.grade()
    log["cost_usd"] += cost
    log["regraded"] = True
    dropped = False
    for unit, claim, locs in agreed(verdicts):
        for path, sentence in locs:
            _drop(store, path, sentence, claim, log, why="still unsupported after one rewrite")
            dropped = True
    if dropped:
        # The second reader named the sentences; nothing yet proves it named the right ones. One last grading says whether any claim
        # survived. A claim that survives with a second reader agreeing holds its story, or stops the week if it is in the essay or
        # the thread: it is never published.
        verdicts, cost = store.grade()
        log["cost_usd"] += cost
        for unit, claim, locs in agreed(verdicts):
            if unit in store.units() and store.owner(next(iter(store.paths(unit)), "")) == unit:
                log["held"].setdefault(unit, []).append(f"{unit}: a claim survived its sentence being removed: {claim[:80]}")
            else:
                log["unlocatable"].append({"unit": unit, "claim": claim})
    return verdicts, log


def _drop(store, path: str, sentence: str, claim: str, log: dict, *, why: str) -> None:
    """Remove the sentence, or hold its story when the sentence carried the story."""
    text, runs = store.get(path)
    span = find_sentence(text, sentence)
    owner = store.owner(path)
    if span is None:
        if owner:
            log["held"].setdefault(owner, []).append(f"{path}: the failing sentence could not be located: {claim[:80]}")
        else:
            log["unlocatable"].append({"path": path, "claim": claim})
        return
    new_text, new_runs = remove_span(text, runs, span[0], span[1])
    if owner and store.load_bearing(path, new_text, new_runs):
        log["held"].setdefault(owner, []).append(f"{path}: {why}: {claim[:80]}")
        return
    store.put(path, new_text, new_runs)
    log["removed"].append({"path": path, "claim": claim, "why": why})


# ---------------------------------------------------------------- the two stores

STORY_PATH = re.compile(r"^story\.(s\d+)\.")


class ComposedStore:
    """The composing pass's edition: prose fields by split path, graded in units (a story, the essay, the week)."""

    def __init__(self, composed: dict, wide_day: dict, published, client, *, user_id: str, conn=None):
        self.composed, self.wide_day, self.published = composed, wide_day, published
        self.client, self.user_id, self.conn = client, user_id, conn

    def units(self):
        return list(composer.story_ids(self.wide_day)) + ["essay", "week"]

    def paths(self, unit: str) -> list:
        fields = composer.prose_fields(self.composed)
        if unit == "essay":
            return [p for p in fields if p.startswith("learning.")]
        if unit == "week":
            return [p for p in fields if p.startswith(("thread.", "closing.", "divider.", "stories.title")) or p.endswith(".tie")]
        return composer._story_paths(unit, fields)

    def get(self, path: str) -> tuple:
        return composer.prose_fields(self.composed)[path], composer.split_map(self.composed).get(path)

    def put(self, path: str, text: str, runs) -> None:
        composer.set_field(self.composed, path, text)
        splits = [s for s in self.composed.get("splits", []) if s["path"] != path]
        splits.append({"path": path, "runs": as_runs(text, runs)})
        self.composed["splits"] = splits

    def truth(self, unit: str) -> str:
        payload = composer.grading_units(self.composed, self.wide_day, self.published)
        if unit == "week":
            return "\n\n".join(e["source_excerpt"] for e in payload["thread"]["ground_truth_excerpt_union"])
        return next((u["ground_truth_excerpt"] for u in payload["items"] if u["id"] == unit), "")

    def grade(self) -> tuple:
        verdicts, week, usage, _payload = composer.check_faithfulness(self.client, self.composed, self.wide_day, self.published,
                                                                       user_id=self.user_id, conn=self.conn)
        return {**verdicts, "week": week}, float(budget.estimate_cost_usd(llm.CHECK_MODEL, usage))

    def owner(self, path: str):
        m = STORY_PATH.match(path)
        return m.group(1) if m else None

    def load_bearing(self, path: str, new_text: str, new_runs) -> bool:
        if path.endswith(".headline"):
            return True
        return path.endswith(".body") and not fact_sentences(new_text, new_runs)


class FramingStore:
    """The first pass's items: why, the 90-second summary, example and connection, and the first pass's thread."""

    FIELDS = ("why", "example", "connection")
    NINETY = ("method", "result", "caveat")

    def __init__(self, framing: dict, raw_day: dict, client, *, user_id: str, phase: str = "faithfulness", conn=None):
        self.framing, self.raw_day, self.client, self.user_id, self.phase, self.conn = framing, raw_day, client, user_id, phase, conn

    def _item(self, item_id: str) -> dict:
        return next(f for f in self.framing["items"] if f["id"] == item_id)

    def units(self):
        return [i["id"] for i in self.raw_day["items"]] + ["thread"]

    def paths(self, unit: str) -> list:
        if unit == "thread":
            return ["thread"]
        item = next((f for f in self.framing["items"] if f["id"] == unit), None)
        if item is None:
            return []
        out = [f"{unit}.{k}" for k in self.FIELDS if item.get(k)]
        if isinstance(item.get("ninety"), dict):
            out += [f"{unit}.ninety.{k}" for k in self.NINETY if item["ninety"].get(k)]
        return out

    def get(self, path: str) -> tuple:
        if path == "thread":
            return self.framing["thread"], self.framing.get("threadSplit")
        unit, _, rest = path.partition(".")
        item = self._item(unit)
        split = (item.get("split") or {}).get(rest)
        if rest.startswith("ninety."):
            return item["ninety"][rest.split(".", 1)[1]], split
        return item[rest], split

    def put(self, path: str, text: str, runs) -> None:
        runs = as_runs(text, runs)
        if path == "thread":
            self.framing["thread"], self.framing["threadSplit"] = text, runs
            return
        unit, _, rest = path.partition(".")
        item = self._item(unit)
        if rest.startswith("ninety."):
            item["ninety"][rest.split(".", 1)[1]] = text
        else:
            item[rest] = text if text else None
        item.setdefault("split", {})[rest] = runs

    def truth(self, unit: str) -> str:
        if unit == "thread":
            return "\n".join(i["source_excerpt"] for i in self.raw_day["items"])
        return next(i["source_excerpt"] for i in self.raw_day["items"] if i["id"] == unit)

    def _graded_framing(self) -> dict:
        """The framing as the grader must read it: a split that is missing, does not reproduce its field, or calls it all interpretation
        would hide text from the grader, so for grading such a field is one fact. The stored framing is not touched."""
        f = copy.deepcopy(self.framing)
        if f.get("thread"):
            f["threadSplit"] = graded_runs(f["thread"], f.get("threadSplit"))
        for item in f["items"]:
            split = item.setdefault("split", {})
            for k in self.FIELDS:
                if item.get(k):
                    split[k] = graded_runs(item[k], split.get(k))
            if isinstance(item.get("ninety"), dict):
                for k in self.NINETY:
                    if item["ninety"].get(k):
                        split["ninety." + k] = graded_runs(item["ninety"][k], split.get("ninety." + k))
        return f

    def grade(self) -> tuple:
        verdicts, thread_verdict, usage = framer.check_faithfulness(self.client, self.raw_day, self._graded_framing(), user_id=self.user_id, phase=self.phase)
        return {**verdicts, "thread": thread_verdict}, float(budget.estimate_cost_usd(llm.CHECK_MODEL, usage))

    def owner(self, path: str):
        return None if path == "thread" else path.partition(".")[0]

    def load_bearing(self, path: str, new_text: str, new_runs) -> bool:
        return ".ninety." in path          # a paper's summary with a part missing is not a summary


_COUNT_LINE = re.compile(r"^(.*?): count — '(.*?)'")


def drop_failing_counts(store: "ComposedStore", ids: dict, log: dict) -> None:
    """A count across stories that is unlisted, wrong, or names a story that was held is an unverified claim: its sentence goes."""
    for _ in range(4):
        lines = counts.count_failures(store.composed, ids, composer.prose_fields(store.composed))
        if not lines:
            return
        changed = False
        for line in lines:
            m = _COUNT_LINE.match(line)
            if not m:
                continue
            path, phrase = m.group(1), m.group(2).lower()
            text, runs = store.get(path)
            for a, b in citegate.sentence_spans(text):
                if phrase in re.sub(r"\s+", " ", style_check.plain(text[a:b]).lower()):
                    new_text, new_runs = remove_span(text, runs, a, b)
                    store.put(path, new_text, new_runs)
                    log["removed"].append({"path": path, "claim": f"a count across stories: {m.group(2)}", "why": "the count could not be checked"})
                    changed = True
                    break
        if not changed:
            return
