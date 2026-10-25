"""Step C mutation proofs: break agents/counts.py in ways that should fail tests/test_counts.py, then restore it."""
import subprocess, sys
P = "agents/counts.py"
orig = open(P).read()
MUTS = [
 ("no check that the number of ids equals the number written", "elif len(named) != m[\"n\"]:", "elif False:"),
 ("no check of the total in 'N of the M stories'", "if m[\"total\"] is not None and m[\"noun\"] in STORY_NOUNS and m[\"total\"] != len(ids):", "if False:"),
 ("an unlisted count is accepted", "if not found:", "if False:"),
 ("story ids are not checked against the edition", "bad = [s for s in named if s not in ids]", "bad = []"),
 ("duplicate ids are accepted", "elif len(set(named)) != len(named):", "elif False:"),
 ("the plain form ('four releases') is not looked for", "for m in PLAIN_FORM.finditer(plain):", "for m in []:"),
 ("the 'of' form ('three of ... nine stories') is not looked for", "for m in OF_FORM.finditer(plain):", "for m in []:"),
 ("a phrase said twice reuses one entry", "found = [pool.pop(0)] if pool else None", "found = [pool[0]] if pool else None"),
 ("the 'N of the M <noun>' total is compared for every noun", "m[\"noun\"] in STORY_NOUNS and m[\"total\"]", "m[\"total\"]"),
 ("the entry's path is ignored", "pool = entries.get((path, _key(m[\"phrase\"])))", "pool = next((v for (p_, k_), v in entries.items() if k_ == _key(m[\"phrase\"])), None)"),
]
out = []
try:
    for name, old, new in MUTS:
        assert old in orig, name
        open(P, "w").write(orig.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_counts"], capture_output=True, text=True)
        out.append(f"{'KILLED ' if r.returncode else 'SURVIVED'}  {name}  ({r.stderr.strip().splitlines()[-1]})")
finally:
    open(P, "w").write(orig)
r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_counts"], capture_output=True, text=True)
out.append(f"clean code: {r.stderr.strip().splitlines()[-1]}")
print("\n".join(out))
