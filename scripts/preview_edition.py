"""Serve the app locally with a stored edition's headlines swapped for candidate texts, to measure how they set (Run 8, Steps B and G).

    python scripts/preview_edition.py autopilot/evidence/2026-10-30/stepB-headlines/headline-recheck.json 8766

A published brief is append-only in the database, so a headline that passed the claim gate cannot be written back to it. This serves a
READ-ONLY preview instead: the stored brief is read, the passing texts from the recheck log replace the headlines in memory, and
nothing is written anywhere. Stop it with Ctrl-C (or kill the process).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import uvicorn  # noqa: E402

from agents import briefs  # noqa: E402


def winners(log_path: str) -> dict:
    log = json.loads(Path(log_path).read_text(encoding="utf-8"))
    out = {}
    for field, rec in log["fields"].items():
        for step in rec["attempts"]:
            if step.get("verdict") == "passed":
                out[field] = step["text"]
    return out


def patch(body: dict, won: dict) -> dict:
    body = json.loads(json.dumps(body))
    e = body.get("edition") or {}
    if "thread" in won and e.get("thread"):
        e["thread"]["lines"] = list(won["thread"])
    if "learning.headline" in won and e.get("learning"):
        e["learning"]["headline"] = won["learning.headline"]
    if "stories.title" in won:
        e["storiesTitle"] = won["stories.title"]
    return body


def main() -> int:
    won = winners(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8766
    real_latest, real_get = briefs.latest, briefs.get

    def latest(conn):
        row = real_latest(conn)
        return (row[0], patch(row[1], won), row[2]) if row else row

    def get(conn, brief_id):
        row = real_get(conn, brief_id)
        return (row[0], patch(row[1], won), row[2]) if row else row

    briefs.latest, briefs.get = latest, get
    import app as site
    uvicorn.run(site.app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
