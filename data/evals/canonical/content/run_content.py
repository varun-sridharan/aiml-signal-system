"""Reader's deduplication gate, content pairs: do layers 1 to 3 behave as labelled?

    python data/evals/canonical/content/run_content.py            # free: layers 1 to 3, no model, no network
    python data/evals/canonical/content/run_content.py --paid     # also asks the model about the pairs only it may decide

Each pair in pairs.json says what it is (`expect`: same | different) and which layer should decide it
(`layer`: url, identity, fingerprint, or model). The free check holds the layers to that:

  * a pair labelled url / identity / fingerprint must be decided by that layer, with the right verdict;
  * a pair labelled model must NOT be merged by layers 1 to 3 (they may pass it on or say different).

The failure that matters is a merge of two articles that are different, so that is asserted first.
`--paid` sends the model-labelled pairs to layer 4 (about a cent in all, booked in the ledger) and holds
its verdict to `expect`. The set is a draft until Varun signs it off (see pairs.json).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent.parent
sys.path.insert(0, str(REPO))

from agents import dedupe  # noqa: E402

PAIRS_PATH = HERE / "pairs.json"


def load_pairs(path: Path = PAIRS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(decide=dedupe.decide, pairs: list[dict] | None = None) -> list[str]:
    """Failures of layers 1 to 3, one line each. Empty means the free gate is green."""
    pairs = pairs if pairs is not None else load_pairs()["pairs"]
    failures = []
    for p in pairs:
        d = decide(dedupe.Doc(**p["a"]), dedupe.Doc(**p["b"]))
        if p["expect"] == "different" and d.verdict == "same":
            failures.append(f"{p['id']}: MERGED two different articles ({d.layer}: {d.reason})")
        elif p["layer"] == "model":
            if d.verdict == "same":
                failures.append(f"{p['id']}: a model-only pair was merged by layer '{d.layer}' ({d.reason})")
            elif d.verdict == "different" and p["expect"] == "same":
                failures.append(f"{p['id']}: a pair that is the same was decided different before the model saw it ({d.layer}: {d.reason})")
        else:
            if (d.verdict, d.layer) != (p["expect"], p["layer"]):
                failures.append(f"{p['id']}: expected {p['expect']} by {p['layer']}, got {d.verdict} by {d.layer} ({d.reason})")
    return failures


def evaluate_paid(pairs: list[dict] | None = None, client=None) -> tuple[list[str], list[dict], float]:
    from agents import llm
    pairs = [p for p in (pairs if pairs is not None else load_pairs()["pairs"]) if p["layer"] == "model"]
    client = client or llm.build_client()
    failures, rows, spent = [], [], 0.0
    for p in pairs:
        d, cost = dedupe.judge(client, dedupe.Doc(**p["a"]), dedupe.Doc(**p["b"]), user_id="dedup-eval", phase="dedup-eval-judge")
        spent += cost
        rows.append({"id": p["id"], "expect": p["expect"], "verdict": d.verdict, "reason": d.reason, "cost_usd": round(cost, 6)})
        if d.verdict != p["expect"]:
            failures.append(f"{p['id']}: the model said {d.verdict} ({d.reason}); the label is {p['expect']}")
    return failures, rows, spent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paid", action="store_true", help="also run layer 4 on the model-labelled pairs")
    parser.add_argument("--json-out", help="write the paid verdicts to this file")
    args = parser.parse_args()
    data = load_pairs()
    pairs = data["pairs"]
    failures = evaluate(pairs=pairs)
    by_layer = {}
    for p in pairs:
        by_layer[p["layer"]] = by_layer.get(p["layer"], 0) + 1
    print(f"content-pairs gate [{data['status']}]: {len(pairs)} pairs ({', '.join(f'{n} by {k}' for k, n in sorted(by_layer.items()))})")
    for f in failures:
        print("  FAIL", f)
    print("  layers 1 to 3 behave as labelled" if not failures else f"  {len(failures)} failure(s)")
    code = 1 if failures else 0
    if args.paid:
        bad, rows, spent = evaluate_paid(pairs)
        for r in rows:
            print(f"  model: {r['id']}: {r['verdict']} (label {r['expect']}): {r['reason']}")
        for f in bad:
            print("  FAIL", f)
        print(f"  layer 4: {len(rows) - len(bad)}/{len(rows)} as labelled · ${spent:.4f}")
        if args.json_out:
            Path(args.json_out).write_text(json.dumps({"rows": rows, "cost_usd": round(spent, 6)}, indent=1))
        code = code or (1 if bad else 0)
    return code


if __name__ == "__main__":
    sys.exit(main())
