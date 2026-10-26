"""Measure the two faithfulness gates on the same fixed texts (Run 4, Step D).

    python data/evals/measure_gates.py --runs 5 --out data/evals/runs/2026-10-26-gates

PAID: every call goes through agents/llm.py and is booked. For each case (the clean golden day, and the four planted-fabrication
cases) and each run it measures

  GATE=claims     the existing claim-by-claim grader, exactly as run_evals.py runs it
  GATE=citations  a citer (the writer's stand-in, since this text is already written) restates each field with citations, then
                  the citation gate runs on it: code checks, a question per citation, a second check before a rejection stands

and scores both against the same hand labels (`expected_unsupported`, `tolerated`):

  caught         the planted claim was flagged (a failing sentence or claim that contains the labelled figure)
  false alarms   anything else flagged, on clean text and clean units
  units held     units with any flag (what the citation gate would hold, or the claim gate would block)
  cost, seconds  per run

The stand-in matters: in production the writer cites as it writes. Here the text is fixed, so a model is asked to reproduce it
word for word and cite it. A sentence it changes or leaves uncited counts as uncited, so a plant can only pass by being cited
to a passage the verifier then accepts. Results hold our own sentences and the verdicts, never a cited passage.
Nothing is tuned to pass; the numbers are what they are.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_evals  # noqa: E402
from run_evals import REPO, framer, load_cases, load_json, matching_pattern, run_faithfulness  # noqa: E402

sys.path.insert(0, str(REPO))
from agents import citegate, llm, style_check  # noqa: E402

PLANTS = ["negative-01", "negative-02", "negative-03", "negative-04"]
CLEAN = "2026-08-04"

CITER_SYSTEM = (
    "You restate finished writing with citations. You are given documents (the only pages the writing may rest on) and fields of "
    "finished writing, each as a `@@ path` section. Reproduce every field EXACTLY, word for word, in the same `@@ path` sections, "
    "answering through the citations feature: a sentence that states a fact carries a citation to the passage it rests on. "
    "Do not add, drop, fix or reword anything, even if the writing looks wrong: if a sentence is not supported by any document, "
    "reproduce it unchanged and leave it uncited. Start a sentence that is only judgement, with no figure, name or date, with `~ `. "
    "Write nothing outside the sections."
)


def unit_fields(framing: dict) -> tuple[list[dict], dict]:
    """[{unit id, path -> text}] for each item and the thread, from a Framer output (the whole text, not only its fact clauses)."""
    units = []
    for n, item in enumerate(framing["items"], start=1):
        fields = {}
        for key, value in item.items():
            if key in ("id", "split") or not value:
                continue
            if key == "ninety" and isinstance(value, dict):
                for sub, text in value.items():
                    if text:
                        fields[f"story.s{n:02d}.ninety.{sub}"] = text
            elif isinstance(value, str):
                fields[f"story.s{n:02d}.{key}"] = value
        units.append({"id": item["id"], "sid": f"s{n:02d}", "fields": fields})
    units.append({"id": "thread", "sid": None, "fields": {"thread.body": framing["thread"]}})
    return units, {}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", style_check.plain(text).replace("~", "", 1) if text.startswith("~") else style_check.plain(text)).strip().lower()


def cite_fixed(client, profile, raw_day, framing, conn=None):
    """The stand-in citer: returns (sentences with citations matched to the original sentences, cost)."""
    docs = [{"id": f"s{n:02d}", "text": item["source_excerpt"], "title": f"s{n:02d}"} for n, item in enumerate(raw_day["items"], start=1)]
    units, _ = unit_fields(framing)
    wanted = {}
    for u in units:
        wanted.update(u["fields"])
    content = [{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": d["text"]}, "title": d["title"], "citations": {"enabled": True}} for d in docs]
    content.append({"type": "text", "text": "\n".join(f"@@ {p}\n{t}" for p, t in wanted.items())})
    message, _usage, cost = llm.call_message(
        client, model=llm.FRAMER_MODEL, max_tokens=16_000, user_id=profile["user_id"], phase="eval-citer", stream=True, conn=conn,
        system=[{"type": "text", "text": CITER_SYSTEM}], messages=[{"role": "user", "content": content}], output_config={"effort": "low"})
    text, spans = citegate.read_blocks(message.content)
    sections = citegate.split_fields(text)
    theirs = {p: citegate.field_sentences(p, se, text, spans) for p, se in sections.items()}
    out = []
    for path, original in wanted.items():
        by_text = {}
        for s in theirs.get(path, []):
            by_text.setdefault(norm(s["text"]), s)
        for a, b in citegate.sentence_spans(original):
            sent = original[a:b]
            match = by_text.get(norm(sent))
            out.append({"path": path, "text": sent, "interpretation": bool(match and match["interpretation"]),
                        "citations": match["citations"] if match else [], "matched": match is not None})
    return out, docs, cost


def run_citations(client, profile, case, conn=None):
    framing = run_evals.framing_from_framed_day(case["output"])
    sentences, docs, cost = cite_fixed(client, profile, case["input"], framing, conn)
    for s in sentences:
        s["code_failures"] = citegate.code_failures(s, docs)
    sentences, vcost = citegate.verify(client, sentences, user_id=profile["user_id"], conn=conn)
    units, _ = unit_fields(framing)
    by_path = {}
    for s in sentences:
        by_path.setdefault(s["path"], []).append(s)
    labels = case["labels"]["units"]
    per_unit, caught_all, fps = {}, {}, []
    for u in units:
        flagged = []
        for path in u["fields"]:
            for s in by_path.get(path, []):
                v = s.get("verdict")
                if s["code_failures"] or (v is not None and not v["supported"]):
                    flagged.append({"path": path, "text": s["text"], "why": "code: " + s["code_failures"][0][:100] if s["code_failures"] else "both checks said no"})
        lab = labels.get(u["id"], {})
        expected, tolerated = list(lab.get("expected_unsupported", [])), list(lab.get("tolerated", []))
        matched = {matching_pattern(expected, f["text"]) for f in flagged} - {None}
        unit_fps = [f for f in flagged if matching_pattern(expected, f["text"]) is None and matching_pattern(tolerated, f["text"]) is None]
        per_unit[u["id"]] = {"sentences": sum(len(by_path.get(p, [])) for p in u["fields"]), "flagged": flagged, "false_alarms": unit_fps,
                             "missed": [p for p in expected if p not in matched], "expected": expected}
        for p in expected:
            caught_all[f"{u['id']}:{p}"] = p in matched
        fps += unit_fps
    return {"per_unit": per_unit, "caught": caught_all, "false_alarms": len(fps), "units_flagged": sum(1 for v in per_unit.values() if v["flagged"]),
            "sentences": len(sentences), "uncited_unmatched": sum(1 for s in sentences if not s["matched"]),
            "second_checks": sum(1 for s in sentences if (s.get("verdict") or {}).get("second") is not None),
            "cost": cost + vcost}


def run_claims(client, profile, case):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = run_faithfulness(client, profile, case, False)
    labels = case["labels"]["units"]
    per_unit, caught_all, fps = {}, {}, 0
    for uid, verdict in result["verdicts"].items():
        lab = labels.get(uid, {})
        false_pos, false_neg = run_evals.score_unit(lab, verdict) if lab else (verdict["unsupported_claims"], [])
        expected = list(lab.get("expected_unsupported", []))
        per_unit[uid] = {"flagged": [{"text": c} for c in verdict["unsupported_claims"]], "false_alarms": [{"text": c} for c in false_pos],
                         "missed": false_neg, "expected": expected}      # the grader's quoted spans are source text and are not kept
        for p in expected:
            caught_all[f"{uid}:{p}"] = p not in false_neg
        fps += len(false_pos)
    return {"per_unit": per_unit, "caught": caught_all, "false_alarms": fps,
            "units_flagged": sum(1 for v in per_unit.values() if v["flagged"]), "cost": result["cost"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gates", default="claims,citations")
    ap.add_argument("--cases", default=",".join(PLANTS + [CLEAN]))
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    import anthropic
    client = anthropic.Anthropic()
    profile = load_json(REPO / "config" / "profile.json")
    cases = {c["name"]: c for c in load_cases(None)}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for gate in args.gates.split(","):
        for name in args.cases.split(","):
            for n in range(1, args.runs + 1):
                t = time.time()
                try:
                    res = run_claims(client, profile, cases[name]) if gate == "claims" else run_citations(client, profile, cases[name])
                    res["error"] = None
                except Exception as e:                       # one failed run is recorded and the measurement goes on
                    res = {"error": f"{type(e).__name__}: {str(e)[:200]}", "caught": {}, "false_alarms": 0, "units_flagged": 0, "cost": 0.0, "per_unit": {}}
                res.update(gate=gate, case=name, run=n, seconds=round(time.time() - t, 1))
                (out / f"{gate}-{name}-run{n}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n")
                rows.append({k: res[k] for k in ("gate", "case", "run", "caught", "false_alarms", "units_flagged", "cost", "seconds", "error")})
                print(f"{gate:>9} {name:<12} run {n}: caught={sum(res['caught'].values())}/{len(res['caught'])} false_alarms={res['false_alarms']} "
                      f"units_flagged={res['units_flagged']} ${res['cost']:.4f} {res['seconds']}s {res['error'] or ''}", flush=True)
    (out / "summary.json").write_text(json.dumps(rows, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
