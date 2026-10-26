"""The review note for the first editions (Run 6, Step A3): which sentences the claim gate held, removed or rewrote, and why.

For each of the first REVIEW_EDITIONS published editions the gates stage already keeps a log of what it did (agents/repair.py: the
grader's claim, the second reader's answer, the rewrite, the sentence removed, the reason a story was held). This module turns those
logs into a page's worth of plain entries for /weekly-digest/review. It shows our own text only: the writer's sentences, the grader's
description of the claim, and the gate's reason. The grader's and the second reader's free-text reasons, which can quote the article,
are not kept by the log and are not shown; and every string is cut to a length that cannot carry a page. Pure functions apart from
`entries`, which reads the stored artifacts.
"""

from __future__ import annotations

from . import runs, style_check

REVIEW_EDITIONS = 4              # the note stops growing after this many published editions (decision log, 2026-10-26)
MAX_FIELD = 190                  # under the 200 characters the evidence scanner treats as copied text: no field can carry a copied passage, whatever the log held

STAGE_NAMES = {"first_pass": "the first pass (each story's why and 90-second summary)", "composed": "the composed edition"}


def clean(text) -> str:
    flat = " ".join(style_check.plain(str(text or "")).split())
    return flat if len(flat) <= MAX_FIELD else flat[:MAX_FIELD - 1].rstrip() + "…"


def _where(path) -> str:
    return clean(path) if path else ""


def entries_from_repairs(repairs: dict) -> list:
    """Flat entries, one per thing the gate did, from one edition's `repairs` log. Older logs (no sentence text) give shorter entries."""
    out = []
    for stage, log in (repairs or {}).items():
        if not isinstance(log, dict):
            continue
        stage_name = STAGE_NAMES.get(stage, stage)

        def where(value) -> str:        # the first pass keys its stories by item id, which means nothing to a reader
            return clean(value) if stage != "first_pass" or str(value) in ("thread", "") else "a story"
        for d in log.get("dismissed") or []:
            out.append({"kind": "dismissed", "stage": stage_name, "where": where(d.get("unit")), "claim": clean(d.get("claim")),
                        "reader": clean(d.get("reader") or "found the claim in the excerpt"), "before": "", "after": ""})
        for r in log.get("rewritten") or []:
            out.append({"kind": "rewritten", "stage": stage_name, "where": _where(r.get("path")), "claim": clean(r.get("claim")),
                        "reader": clean(r.get("reader") or "did not find the claim in the excerpt"),
                        "before": clean(r.get("before")), "after": clean(r.get("after"))})
        for r in log.get("removed") or []:
            out.append({"kind": "removed", "stage": stage_name, "where": _where(r.get("path")), "claim": clean(r.get("claim")),
                        "reader": clean(r.get("why")), "before": clean(r.get("sentence")), "after": ""})
        for owner, reasons in (log.get("held") or {}).items():
            for reason in reasons if isinstance(reasons, list) else [reasons]:
                out.append({"kind": "held", "stage": stage_name, "where": where(owner),
                            "claim": clean(reason), "reader": "", "before": "", "after": ""})
        for u in log.get("unlocatable") or []:
            out.append({"kind": "unlocated", "stage": stage_name, "where": where(u.get("unit") or u.get("path")), "claim": clean(u.get("claim")),
                        "reader": "", "before": "", "after": ""})
    return out


def edition_note(week: str, number: int, published_at, gates: dict) -> dict:
    repairs = (gates or {}).get("repairs") or {}
    items = entries_from_repairs(repairs)
    return {"week": week, "number": number, "published_at": published_at, "entries": items,
            "flags": sum(int(r.get("flags", 0)) for r in repairs.values() if isinstance(r, dict)),
            "warnings": len((gates or {}).get("warnings") or []),
            "detailed": all("sentence" in r or "before" in r for r in (x for log in repairs.values() if isinstance(log, dict)
                                                                      for x in (log.get("removed") or []) + (log.get("rewritten") or []))),
            "counts": {k: sum(1 for e in items if e["kind"] == k) for k in ("dismissed", "rewritten", "removed", "held", "unlocated")}}


def entries(conn, limit: int | None = None) -> list:
    """The note for the first `limit` published editions (REVIEW_EDITIONS by default), newest first."""
    rows = conn.execute("SELECT id, run_id, published_at FROM briefs ORDER BY published_at ASC, id ASC LIMIT %s",
                        (REVIEW_EDITIONS if limit is None else limit,)).fetchall()
    notes = []
    for number, (brief_id, run_id, published_at) in enumerate(rows, start=1):
        try:
            gates = runs.load_artifact(conn, run_id, "gates")
        except KeyError:
            gates = {}
        notes.append(edition_note(brief_id, number, published_at, gates))
    return list(reversed(notes))
