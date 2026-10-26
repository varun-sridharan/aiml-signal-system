"""Step A mutation proofs: break agents/repair.py, run tests/test_repair.py, restore."""
import os, subprocess, sys
P = "agents/repair.py"
M = [
 ("a flag is acted on without the second reader", "                if not ok:\n                    log[\"dismissed\"]", "                if False:\n                    log[\"dismissed\"]"),
 ("the unreadable second reader dismisses the flag", "supported, ids = False, []           # an unreadable", "supported, ids = True, []           # an unreadable"),
 ("no regrade and no removal after the one rewrite", "    for unit, claim, locs in agreed(verdicts):\n        for path, sentence in locs:\n            _drop(", "    for unit, claim, locs in []:\n        for path, sentence in locs:\n            _drop("),
 ("a load-bearing sentence is removed instead of holding the story", "    if owner and store.load_bearing(path, new_text, new_runs):", "    if False and owner and store.load_bearing(path, new_text, new_runs):"),
 ("removing a sentence leaves its space", "    if b < len(text) and text[b] == \" \":\n        b += 1\n    elif a > 0 and text[a - 1] == \" \":\n        a -= 1\n", ""),
 ("an invalid split is trusted", "    if runs and \"\".join(r[\"text\"] for r in runs) == text:", "    if runs:"),
 ("a rewrite is applied without being kept as a record", "                log[\"rewritten\"].append({\"path\": path, \"unit\": unit, \"claim\": claim})", "                pass"),
 ("sentences without a fact run are graded and edited too", "    return [(a, b) for a, b in citegate.sentence_spans(text) if any(fa < b and fb > a for fa, fb in facts)]", "    return list(citegate.sentence_spans(text))"),
 ("counts naming a held story stay", "    for _ in range(4):\n        lines = counts.count_failures", "    for _ in range(0):\n        lines = counts.count_failures"),
]
orig = open(P).read()
out = []
try:
    for name, old, new in M:
        assert old in orig, name
        open(P, "w").write(orig.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-B", "-m", "unittest", "tests.test_repair"], capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        open(P, "w").write(orig)
        out.append(f"{'KILLED  ' if r.returncode else 'SURVIVED'} {name}  ({r.stderr.strip().splitlines()[-1]})")
finally:
    open(P, "w").write(orig)
r = subprocess.run([sys.executable, "-B", "-m", "unittest", "tests.test_repair"], capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
out.append(f"clean code: {r.stderr.strip().splitlines()[-1]}")
print("\n".join(out))
