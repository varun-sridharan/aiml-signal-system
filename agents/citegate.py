"""The citation-based faithfulness gate (Run 4, Step D), built beside the claim-by-claim gate.

The claim gate asks a model to read the writer's text, list its claims and find each in the page. This gate asks the writer
to say where each factual sentence comes from, and then checks the answer in three layers, cheapest and most certain first:

  1. CODE. The writer answers through Anthropic's citations feature, so the passage behind each citation comes back from the
     document itself (`cited_text` is copied by the API, not typed by the model). Code checks that every sentence is either cited
     or marked as the writer's own interpretation (a leading `~`), that a marked sentence carries no figure, that every cited
     passage is a literal substring of the stored excerpt and sits where the API says it does, and that a sentence cites only
     the pages it may draw on (a story's own fields only its own page; the essay only the stories it lists).
  2. A MODEL, one small question per citation: does this passage, by itself, establish what this sentence claims?
  3. A SECOND, independent check, only for a sentence none of whose citations passed: a differently framed question over all
     its passages together. The rejection stands only if both say no.

A story with a rejected or uncited sentence is HELD ("held: could not be verified") and the rest of the edition publishes,
provided at least MIN_STORIES stories pass. A failure in a field that belongs to the whole week (thread, essay, closing,
section title) stops the edition: no single story can be dropped to remove it.

Everything here that touches the network goes through a `client` and `agents/llm.py`, so it is booked in the ledger and can be
tested with a scripted client.
"""

from __future__ import annotations

import json
import re

try:
    from . import framer, llm, style_check
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import framer
    import llm
    import style_check

MIN_STORIES = 4
INTERPRETATION_MARK = "~"
VERIFY_PHASE = "citation-verify"
BATCH = 24                        # questions per verification call

_FIELD = re.compile(r"^@@ ([A-Za-z0-9_.\-]+)[ \t]*$", re.M)
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[~A-Z0-9\"'(\[$])")
_ABBREV = style_check._ABBREV
_FIGURE = re.compile(r"\d")
_STORY_FIELD = re.compile(r"^(?:story|why|ninety)\.(s\d+)\b")
_WEEK_FIELD = re.compile(r"^(?:thread|learning|closing|stories\.title|divider)")


# ---------------------------------------------------------------- the response, as text with spans

def read_blocks(blocks) -> tuple[str, list[dict]]:
    """The response's text blocks as one string, and for each block its [start, end) and its citations as plain dicts."""
    text, spans = "", []
    for b in blocks:
        if getattr(b, "type", None) != "text":
            continue
        cites = []
        for c in getattr(b, "citations", None) or []:
            cites.append({"type": getattr(c, "type", ""), "document_index": getattr(c, "document_index", None),
                          "cited_text": getattr(c, "cited_text", ""),
                          "start": getattr(c, "start_char_index", None), "end": getattr(c, "end_char_index", None)})
        spans.append({"start": len(text), "end": len(text) + len(b.text), "citations": cites})
        text += b.text
    return text, spans


def split_fields(full_text: str) -> dict[str, tuple[int, int]]:
    """Each `@@ path` section of the answer: path -> (start, end) offsets of its body in the full text. A path may appear once."""
    marks = list(_FIELD.finditer(full_text))
    out = {}
    for n, m in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(full_text)
        out.setdefault(m.group(1), (m.end(), end))
    return out


def sentence_spans(text: str, base: int = 0) -> list[tuple[int, int]]:
    """Offsets of each sentence in `text` (shifted by `base`), keeping abbreviations together. Blank lines also end a sentence."""
    cuts, last = [], 0
    for m in re.finditer(_SENTENCE_END, text):
        piece = text[last:m.start() + 1]
        if _ABBREV.search(piece):
            continue
        cuts.append((last, m.start() + 1))
        last = m.end()
    cuts.append((last, len(text)))
    out = []
    for a, b in cuts:
        for para in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", text[a:b]):
            seg = para.group(0)
            lead = len(seg) - len(seg.lstrip())
            seg_text = seg.strip()
            if seg_text:
                out.append((base + a + para.start() + lead, base + a + para.start() + lead + len(seg_text)))
    return out


def field_sentences(path: str, span: tuple[int, int], full_text: str, spans: list[dict]) -> list[dict]:
    """The sentences of one field: text (without any leading mark), whether the writer marked it interpretation, its citations."""
    start, end = span
    out = []
    for a, b in sentence_spans(full_text[start:end], start):
        raw = full_text[a:b]
        marked = raw.startswith(INTERPRETATION_MARK)
        cites = []
        for s in spans:
            if s["citations"] and s["start"] < b and s["end"] > a and full_text[max(a, s["start"]):min(b, s["end"])].strip():
                cites.extend(s["citations"])
        out.append({"path": path, "text": raw[len(INTERPRETATION_MARK):].strip() if marked else raw, "interpretation": marked, "citations": cites})
    return out


# ---------------------------------------------------------------- layer 1: code

def story_of(path: str) -> str | None:
    m = _STORY_FIELD.match(path)
    if m:
        return m.group(1)
    m = re.match(r"^story\.(s\d+)", path)
    return m.group(1) if m else None


def code_failures(sent: dict, docs: list[dict], drawn_from: list[str] | None = None) -> list[str]:
    """What is wrong with one sentence before any model is asked. `docs` is [{id, text}], in the order sent to the API."""
    path, text = sent["path"], sent["text"]
    out = []
    plain = style_check.plain(text)
    if sent["interpretation"]:
        if _FIGURE.search(plain):
            out.append(f"{path}: a sentence marked as interpretation carries a figure or date: {plain[:90]!r}")
        if sent["citations"]:
            return out                               # a marked sentence that cites anyway is judged on its citations below
    elif not sent["citations"]:
        out.append(f"{path}: uncited factual sentence: {plain[:90]!r}")
    allowed = None
    own = story_of(path)
    if own:
        allowed = {own}
    elif path.startswith("learning.para") or path.startswith("learning.note") or path in ("learning.headline", "learning.doDifferently"):
        allowed = set(drawn_from) if drawn_from else None
    for c in sent["citations"]:
        i = c.get("document_index")
        if not isinstance(i, int) or not 0 <= i < len(docs):
            out.append(f"{path}: a citation points at document {i!r}, which was not sent")
            continue
        doc = docs[i]
        passage = c.get("cited_text") or ""
        if not passage or passage not in doc["text"]:
            out.append(f"{path}: cited passage is not in {doc['id']}'s stored excerpt: {passage[:60]!r}")
        elif c.get("start") is not None and doc["text"][c["start"]:c["end"]] != passage:
            out.append(f"{path}: cited passage is not where the citation says it is in {doc['id']}")
        if allowed is not None and doc["id"] not in allowed:
            out.append(f"{path}: cites {doc['id']}, which this field may not draw on")
    return out


# ---------------------------------------------------------------- layers 2 and 3: the models

SINGLE_SYSTEM = (
    "You check one thing at a time. You are given a SENTENCE written for a news digest and a PASSAGE copied from a source page. "
    "Answer whether the passage, read by itself, establishes every factual statement the sentence makes: each figure, name, date, "
    "version, comparison and who-did-what. Answer true only if all of them are in the passage. Answer false if any is missing, if "
    "the passage attaches a figure to something else, or if the sentence says more than the passage does. Opinions and advice in the "
    "sentence need no support; judge only its factual statements. Return one answer per question id."
)
SECOND_SYSTEM = (
    "You are the second, independent reader. A SENTENCE written for a news digest was rejected against its cited passages. "
    "Read the PASSAGES together and list every factual statement in the sentence (figure, name, date, version, comparison, "
    "who-did-what) that the passages do NOT establish, quoting the statement. If every factual statement is established, "
    "return an empty list. Ignore opinions and advice. Do not excuse a statement because it is plausible."
)
SINGLE_SCHEMA = {"type": "object", "properties": {"answers": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "supports": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["id", "supports", "why"], "additionalProperties": False}}}, "required": ["answers"], "additionalProperties": False}
SECOND_SCHEMA = {"type": "object", "properties": {"answers": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "unsupported": {"type": "array", "items": {"type": "string"}}},
    "required": ["id", "unsupported"], "additionalProperties": False}}}, "required": ["answers"], "additionalProperties": False}


def _ask(client, system: str, schema: dict, questions: list[dict], *, user_id: str, conn=None):
    """One verification call over a list of questions. Returns ({id: answer}, usage cost)."""
    cost, answers = 0.0, {}
    for i in range(0, len(questions), BATCH):
        chunk = questions[i:i + BATCH]
        text, usage, c = llm.call(
            client, model=llm.CHECK_MODEL, max_tokens=8000, user_id=user_id, phase=VERIFY_PHASE, conn=conn, system=system,
            messages=[{"role": "user", "content": json.dumps({"questions": chunk}, indent=1, ensure_ascii=False)}],
            temperature=0, output_config={"format": {"type": "json_schema", "schema": schema}})
        cost += c
        for a in json.loads(text)["answers"]:
            answers[a["id"]] = a
    return answers, cost


def verify(client, sentences: list[dict], *, user_id: str, conn=None) -> tuple[list[dict], float]:
    """Add `verdict` to each cited, non-interpretation sentence: {supported, by: 'citation n' | 'both-no', checks: [...]}. Returns (sentences, cost)."""
    todo = [s for s in sentences if s["citations"] and not s.get("code_failures")]
    questions, owners = [], []
    for si, s in enumerate(todo):
        for ci, c in enumerate(s["citations"]):
            questions.append({"id": len(questions), "sentence": style_check.plain(s["text"]), "passage": c["cited_text"]})
            owners.append((si, ci))
    answers, cost = _ask(client, SINGLE_SYSTEM, SINGLE_SCHEMA, questions, user_id=user_id, conn=conn) if questions else ({}, 0.0)
    for s in todo:
        s["verdict"] = {"supported": False, "first": [], "second": None}
    for qid, (si, ci) in enumerate(owners):
        a = answers.get(qid)
        ok = bool(a and a["supports"])
        todo[si]["verdict"]["first"].append({"citation": ci, "supports": ok, "why": (a or {}).get("why", "no answer")})
    rejected = []
    for s in todo:
        if any(f["supports"] for f in s["verdict"]["first"]):
            s["verdict"]["supported"] = True
        else:
            rejected.append(s)
    if rejected:
        second_q = [{"id": n, "sentence": style_check.plain(s["text"]), "passages": [c["cited_text"] for c in s["citations"]]} for n, s in enumerate(rejected)]
        second, c2 = _ask(client, SECOND_SYSTEM, SECOND_SCHEMA, second_q, user_id=user_id, conn=conn)
        cost += c2
        for n, s in enumerate(rejected):
            a = second.get(n)
            s["verdict"]["second"] = {"unsupported": (a or {}).get("unsupported", ["no answer"])}
            s["verdict"]["supported"] = bool(a and not a["unsupported"])
    return sentences, cost


# ---------------------------------------------------------------- holding stories

def evaluate(sentences: list[dict], story_ids: list[str]) -> dict:
    """From checked sentences to the decision: which stories pass, which are held (and why), what blocks the week.

    Returns {passing, held: {sid: [reasons]}, blockers: [reasons], publishable}.
    """
    held: dict[str, list[str]] = {}
    blockers: list[str] = []
    for s in sentences:
        reasons = list(s.get("code_failures") or [])
        v = s.get("verdict")
        if v is not None and not v["supported"]:
            bad = (v["second"] or {}).get("unsupported") or []
            reasons.append(f"{s['path']}: rejected by both checks: {style_check.plain(s['text'])[:90]!r}" + (f" (not established: {bad[0][:80]!r})" if bad else ""))
        if not reasons:
            continue
        owner = story_of(s["path"])
        if owner in story_ids:
            held.setdefault(owner, []).extend(reasons)
        else:
            blockers.extend(reasons)
    passing = [sid for sid in story_ids if sid not in held]
    return {"passing": passing, "held": held, "blockers": blockers,
            "publishable": not blockers and len(passing) >= MIN_STORIES}
