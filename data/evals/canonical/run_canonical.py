"""Reader's canonicalisation gate: URL pairs that must, and must not, collapse.

    python data/evals/canonical/run_canonical.py

Free and deterministic: no model, no network, no database. Exit 0 only if every pair
behaves as labelled. The set is a draft until Varun signs it off (see pairs.json).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
sys.path.insert(0, str(REPO))

from agents import excerpts  # noqa: E402

PAIRS_PATH = HERE / "pairs.json"


def load_pairs(path: Path = PAIRS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(canonicalise=excerpts.canonicalise, pairs: list[dict] | None = None) -> list[str]:
    """Failures, one line each. Empty means the gate is green."""
    pairs = pairs if pairs is not None else load_pairs()["pairs"]
    failures = []
    for p in pairs:
        a, b = canonicalise(p["a"]), canonicalise(p["b"])
        if p["expect"] == "same" and a != b:
            failures.append(f"{p['id']}: must collapse but did not: {a}  !=  {b}")
        elif p["expect"] == "different" and a == b:
            failures.append(f"{p['id']}: MERGED two different articles into {a}")
    return failures


def main() -> int:
    data = load_pairs()
    pairs = data["pairs"]
    failures = evaluate(pairs=pairs)
    same = sum(p["expect"] == "same" for p in pairs)
    planted = sum(bool(p.get("planted")) for p in pairs)
    print(f"canonicalisation gate [{data['status']}]: {same} must-collapse, {len(pairs) - same} must-not "
          f"({planted} planted near-duplicates)")
    for f in failures:
        print("  FAIL", f)
    print("  all pairs behave as labelled" if not failures else f"  {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
