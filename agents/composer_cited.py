"""The composing pass in its citing mode (GATE=citations; Run 4, Step D).

The same pieces as agents/composer.py, written a different way. Anthropic's citations feature cannot be combined with
structured outputs (the API answers 400: "Citations cannot be enabled when output format is set"; autopilot/evidence/2026-10-26/
stepD-citations-probe.json), so the writer answers in plain text sections instead of a JSON object, and each story's excerpt is
sent as a document with citations on. Everything it writes comes back with the passages it rests on, copied by the API from
the document. agents/citegate.py checks the citations; this module asks for the text, reads it into the same `composed` shape
the claim gate's writer produces, and applies the same slot, style and count rules by code.

The first pass's `why` and `ninety` are rewritten here with citations, so that every sentence the page shows is cited and the
first-pass grader is not needed under this gate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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

REPO = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO / "prompts" / "composer-cited-prompt.md"
PHASE = "composing-cited"
MAX_TOKENS = 40_000
MAX_REVISIONS = composer.MAX_REVISIONS

HASHTAG_SPLIT = re.compile(r"[\s,]+")


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text().strip() + "\n\n---\n\n# Style guide\n\n" + composer.STYLE_GUIDE_PATH.read_text().strip()


# ---------------------------------------------------------------- the request

def documents(raw_day: dict, published: dict | None = None) -> list[dict]:
    """One document per story, in rank order (document_index n is story s(n+1)); `text` is exactly what the API is sent."""
    out = []
    for entry in composer.composer_items(raw_day, published):
        out.append({"id": entry["id"], "text": entry["excerpt"], "title": entry["id"]})
    return out


def _doc_block(doc: dict, last: bool) -> dict:
    block = {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": doc["text"]},
             "title": doc["title"], "citations": {"enabled": True}}
    if last:
        block["cache_control"] = {"type": "ephemeral"}      # the stories are the same for the writer, its revisions and a rerun
    return block


def brief(profile: dict, raw_day: dict, framing: dict, window_label: str, published: dict | None) -> dict:
    first_pass = {f["id"]: f for f in framing["items"]}
    stories = []
    for entry in composer.composer_items(raw_day, published):
        f = first_pass.get(entry["item"], {})
        stories.append({"id": entry["id"], "headline": entry["headline"], "tags": entry["tags"], "source": entry["source"],
                        "why_draft": f.get("why"), "ninety_draft": f.get("ninety")})
    return {"reader_profile": {"role": profile["role"], "goals": profile["goals"], "interests": profile["interests"]},
            "edition_window": window_label, "date": raw_day["date"], "stories": stories}


def compose_request(system_prompt: str, profile: dict, raw_day: dict, framing: dict, window_label: str,
                    published: dict | None = None, revise: dict | None = None) -> dict:
    docs = documents(raw_day, published)
    content = [_doc_block(d, n == len(docs) - 1) for n, d in enumerate(docs)]
    payload = brief(profile, raw_day, framing, window_label, published)
    if revise:
        payload["revise"] = [{"path": p, "current_text": revise["texts"][p], "problems": msgs} for p, msgs in revise["problems"].items() if p in revise["texts"]]
        payload["instruction"] = REVISE_NOTE
    content.append({"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)})
    return dict(model=llm.FRAMER_MODEL, max_tokens=MAX_TOKENS, stream=True,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                output_config={"effort": composer.COMPOSE_EFFORT})


REVISE_NOTE = (
    "REVISION. The edition you wrote was checked by code. The fields listed in `revise` broke the rules named under `problems`. Rewrite "
    "ONLY those fields, in the same `@@ path` sections, citing as before: fix every problem named, keep every fact inside the documents, "
    "keep any [[...]] tokens and footnote keys that are still needed, keep the field's job. If a count is named, say it without a number. "
    "Do not return any field not listed, and no `counts` section."
)


def write(client, system_prompt, profile, raw_day, framing, window_label, published, *, revise=None, conn=None):
    """One writer call. Returns (full text, block spans, cost)."""
    message, _usage, cost = llm.call_message(client, user_id=profile["user_id"], phase=PHASE + ("-revise" if revise else ""), conn=conn,
                                             **compose_request(system_prompt, profile, raw_day, framing, window_label, published, revise))
    if message.stop_reason == "max_tokens":
        raise RuntimeError("the cited writer hit max_tokens; the answer is truncated")
    text, spans = citegate.read_blocks(message.content)
    return text, spans, cost


# ---------------------------------------------------------------- reading the answer

def draft_from(full_text: str, spans: list[dict]) -> dict:
    """{path: [sentence dicts]} for every section of the answer except `counts`, plus the counts text."""
    sections = citegate.split_fields(full_text)
    fields = {p: citegate.field_sentences(p, se, full_text, spans) for p, se in sections.items() if p != "counts"}
    counts_text = full_text[slice(*sections["counts"])].strip() if "counts" in sections else ""
    return {"fields": fields, "counts_text": counts_text}


def field_text(sentences: list[dict]) -> str:
    return " ".join(s["text"] for s in sentences)


def parse_counts(counts_text: str) -> list[dict]:
    out = []
    for line in counts_text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 3 and parts[0] and parts[1]:
            out.append({"path": parts[0], "phrase": parts[1], "stories": [t for t in HASHTAG_SPLIT.split(parts[2]) if t]})
    return out


def build_composed(draft: dict, ids: dict) -> tuple[dict, list[str]]:
    """The claim writer's `composed` shape from the sections, and what is missing. Story why/ninety ride along on each story."""
    texts = {p: field_text(s) for p, s in draft["fields"].items()}
    missing = []

    def need(path):
        if not texts.get(path, "").strip():
            missing.append(f"{path}: the section is missing or empty")
        return texts.get(path, "")

    def nums(prefix):
        found = sorted((int(m.group(1)), p) for p in texts for m in [re.fullmatch(re.escape(prefix) + r"\.(\d+)", p)] if m)
        return [texts[p] for _, p in found]

    note_keys = sorted({m.group(1) for p in texts for m in [re.fullmatch(r"learning\.note\.([A-Za-z0-9_-]+?)(?:\.term)?", p)] if m})
    stories = []
    for sid in ids:
        pre = f"story.{sid}"
        feature = nums(f"{pre}.feature")
        tie = texts.get(f"{pre}.tie", "").strip()
        ninety = {k: texts.get(f"{pre}.ninety.{k}", "") for k in ("method", "result", "caveat")}
        stories.append({
            "id": sid, "headline": need(f"{pre}.headline"),
            "hashtags": [h for h in HASHTAG_SPLIT.split(texts.get(f"{pre}.hashtags", "")) if h],
            "body": need(f"{pre}.body"), "why": need(f"{pre}.why"),
            "ninety": ninety if any(ninety.values()) else None,
            "featureParas": feature or None, "tryIt": texts.get(f"{pre}.tryIt", "").strip() or None,
            "tie": {"to": texts.get(f"{pre}.tie.to", "").strip(), "text": tie} if tie and texts.get(f"{pre}.tie.to", "").strip() else None,
        })
    composed = {
        "thread": {"lines": [need("thread.line.1"), need("thread.line.2")], "body": need("thread.body")},
        "learning": {
            "headline": need("learning.headline"), "paras": nums("learning.para"),
            "notes": [{"key": k, "term": texts.get(f"learning.note.{k}.term", "").strip(), "definition": texts.get(f"learning.note.{k}", "").strip()} for k in note_keys],
            "drawnFrom": [d for d in HASHTAG_SPLIT.split(texts.get("learning.drawnFrom", "")) if d],
            "doDifferently": need("learning.doDifferently"),
        },
        "storiesTitle": need("stories.title"),
        "stories": stories,
        "dividers": [{"before": p.split(".", 1)[1], "label": t} for p, t in texts.items() if p.startswith("divider.") and t.strip()],
        "closing": {"lines": [need("closing.line.1"), need("closing.line.2")], "summary": need("closing.summary"), "oneThing": need("closing.oneThing")},
        "counts": parse_counts(draft["counts_text"]),
    }
    # The claim gate's coverage and fact-floor rules read the writer's own fact/interpretation split. Here nothing is split by the
    # writer: a sentence is cited or marked, and citegate decides. One whole-field run keeps those two rules vacuously true.
    composed["splits"] = [{"path": p, "runs": [{"text": t, "class": "fact"}]} for p, t in composer.prose_fields(composed).items()]
    return composed, missing


def structural_failures(composed: dict, ids: dict, missing: list[str]) -> list[str]:
    """The rules that do not depend on who checks the facts: presence, slot counts and limits, the five style rules, counts."""
    out = list(missing)
    if not missing:
        out += composer.slot_failures(composed, ids)
    out += counts.count_failures(composed, ids, composer.prose_fields(composed))
    out += [f"style — {line}" for line in style_check.edition_style_failures(composed)]
    return out


# ---------------------------------------------------------------- revising, then checking

def revise_problems(composed: dict, ids: dict) -> dict:
    """The fields the writer can be asked to rewrite and what is wrong with each (style, slot and count problems, field by field)."""
    return composer.revisable_problems(composed, ids)


def apply_revision(draft: dict, new_draft: dict, paths) -> list[str]:
    replaced = []
    for p in paths:
        if p in new_draft["fields"] and new_draft["fields"][p]:
            draft["fields"][p] = new_draft["fields"][p]
            replaced.append(p)
    return replaced


def all_sentences(draft: dict, docs: list[dict]) -> list[dict]:
    """Every sentence of every section with its code failures. Essay sentences may cite only the stories the essay lists."""
    drawn = [d for d in HASHTAG_SPLIT.split(field_text(draft["fields"].get("learning.drawnFrom", []))) if d]
    out = []
    for path, sents in draft["fields"].items():
        if path == "learning.drawnFrom" or path.endswith((".hashtags", ".tie.to", ".term")) or path.startswith("divider."):
            continue                        # labels, ids and lists: not sentences that state facts
        for s in sents:
            s["code_failures"] = citegate.code_failures(s, docs, drawn_from=drawn)
            out.append(s)
    return out


def compose_and_check(client, system_prompt, profile, raw_day, framing, window_label, published, *, conn=None):
    """Write, repair what code can repair, then check every sentence. Returns a result dict (see keys below); publishes nothing."""
    ids = composer.story_ids(raw_day)
    docs = documents(raw_day, published)
    text, spans, cost = write(client, system_prompt, profile, raw_day, framing, window_label, published, conn=conn)
    draft = draft_from(text, spans)
    record = {"rounds": [], "replaced": [], "first_text_chars": len(text)}
    composed, missing = build_composed(draft, ids)
    for number in range(MAX_REVISIONS):
        problems = revise_problems(composed, ids) if not missing else {}
        if not problems:
            break
        texts = {p: field_text(s) for p, s in draft["fields"].items()}
        rtext, rspans, extra = write(client, system_prompt, profile, raw_day, framing, window_label, published,
                                     revise={"texts": texts, "problems": problems}, conn=conn)
        cost += extra
        replaced = apply_revision(draft, draft_from(rtext, rspans), problems)
        record["rounds"].append({"problems": problems, "replaced": replaced})
        record["replaced"] += [p for p in replaced if p not in record["replaced"]]
        composed, missing = build_composed(draft, ids)
    structural = structural_failures(composed, ids, missing)
    sentences = all_sentences(draft, docs)
    sentences, vcost = citegate.verify(client, sentences, user_id=profile["user_id"], conn=conn)
    decision = citegate.evaluate(sentences, list(ids))
    return {"composed": composed, "draft": draft, "sentences": sentences, "structural": structural, "decision": decision,
            "cost_usd": cost + vcost, "writer_cost_usd": cost, "verify_cost_usd": vcost, "revision": record, "docs": docs, "ids": ids}


# ---------------------------------------------------------------- the edition

def strip_links_to(text: str, held: set[str]) -> str:
    return re.sub(r"\[\[#([A-Za-z0-9_-]+)\|([^\]]*)\]\]", lambda m: m.group(2) if m.group(1) in held else m.group(0), text)


def without_held(composed: dict, held: set[str]) -> dict:
    """The composed edition with the held stories removed: their ties, dividers and thread links go with them."""
    out = json.loads(json.dumps(composed))
    out["stories"] = [s for s in out["stories"] if s["id"] not in held]
    for s in out["stories"]:
        if s.get("tie") and s["tie"]["to"] in held:
            s["tie"] = None
    out["dividers"] = [d for d in out["dividers"] if d["before"] not in held]
    out["learning"]["drawnFrom"] = [d for d in out["learning"]["drawnFrom"] if d not in held]
    out["thread"]["body"] = strip_links_to(out["thread"]["body"], held)
    return out


HOW_MADE = ("The Reader looked at {considered} candidate links from {hosts} places, fetched {fetched} pages in full{dup}, and kept {stories} stories. "
            "About {minutes} minutes of reading stand behind them. Every sentence that states a fact carries a citation to the passage of the page it "
            "rests on; code checked that each passage is in the page, and a second model checked that each passage supports its sentence. "
            "A count across stories is listed with the stories it counts and checked by code; a sentence marked as the writer's judgement carries no figure.{held}")


def assemble_edition(result: dict, raw_day: dict, framing: dict, *, stats: dict, source_minutes: int, published: dict | None = None) -> dict:
    """The stored edition for a result whose decision is publishable: held stories out, a `held` list in, citation counts recorded."""
    decision, composed, ids = result["decision"], result["composed"], result["ids"]
    held = set(decision["held"])
    kept = without_held(composed, held)
    # the story's cited why/ninety replace the first pass's, through the framing the assembler reads
    why = {ids[s["id"]]: s for s in composed["stories"]}
    reframed = {"items": [dict(f, why=why[f["id"]]["why"], ninety=why[f["id"]]["ninety"]) if f["id"] in why else f for f in framing["items"]]}
    edition = composer.assemble_edition(kept, raw_day, reframed, {}, {"unsupported_claims": []}, stats=stats, source_minutes=source_minutes,
                                        style=style_check.edition_style_failures(kept), published=published)
    for story in edition["stories"]:
        story["checked"] = story["id"] in decision["passing"]
    edition["learning"]["drawnFrom"] = [d for d in edition["learning"]["drawnFrom"] if d not in held]
    by_item = {i["id"]: i for i in raw_day["items"]}
    edition["held"] = [{"id": sid, "headline": by_item[ids[sid]]["headline"], "sources": composer.story_sources(by_item[ids[sid]])[:1],
                        "why": "could not be verified"} for sid in decision["held"]]
    sents = [s for s in result["sentences"]]
    cited = [s for s in sents if s["citations"]]
    edition["faithfulness"] = {"gate": "citations", "sentences": len(sents), "cited": len(cited),
                               "marked_interpretation": sum(1 for s in sents if s["interpretation"]),
                               "citations": sum(len(s["citations"]) for s in sents),
                               "second_checks": sum(1 for s in sents if (s.get("verdict") or {}).get("second") is not None),
                               "held": {k: v[:3] for k, v in decision["held"].items()}}
    considered, fetched, merged, hosts = (int(stats.get(k, 0)) for k in ("candidates", "fetched", "merged", "feeds_ok"))
    dup = f", folded {merged} duplicate{'s' if merged != 1 else ''} into the story they repeated" if merged else ""
    held_text = f" {len(held)} {'story was' if len(held) == 1 else 'stories were'} held because a sentence could not be verified, and the rest were published." if held else ""
    edition["learning"]["howMade"] = HOW_MADE.format(considered=considered, hosts=hosts, fetched=fetched, dup=dup, stories=len(edition["stories"]),
                                                     minutes=source_minutes, held=held_text)
    edition["composedBy"] = {"agent": "Framer (second pass, citing)", "model": llm.FRAMER_MODEL, "checkModel": llm.CHECK_MODEL}
    return edition
