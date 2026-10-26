"""Step D: the citing writer sometimes reproduced stretches of a page word for word. Replace each such string in the Step D result
files (80+ characters that sit verbatim inside a stored excerpt) with its SHA-256 and length. Our own sentences are untouched."""
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, ".")
from agents import article_text as A, budget
with budget.connect() as conn:
    corpus = [A.normalise(t) for _, t in A.db_corpus(conn) + A.golden_corpus()]
with budget.connect() as _c:
    INDEX = A.Index(A.db_corpus(_c) + A.golden_corpus())
MINLEN = int(sys.argv[1]) if len(sys.argv) > 1 else 80
def verbatim(s):
    n = A.normalise(s)
    return (len(n) >= MINLEN and any(n in c for c in corpus)) or bool(INDEX.find(s))
def walk(o):
    if isinstance(o, str):
        if verbatim(o) and not o.startswith("[source text withheld"):
            return f"[source text withheld (the writer reproduced the page): sha256 {hashlib.sha256(o.encode()).hexdigest()}; {len(o)} characters]", 1
        return o, 0
    n = 0
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            out[k], c = walk(v); n += c
        return out, n
    if isinstance(o, list):
        out = []
        for v in o:
            r, c = walk(v); out.append(r); n += c
        return out, n
    return o, 0
for path in sorted(Path("autopilot/evidence/2026-10-26/stepD-gates").rglob("*.json")):
    raw = path.read_text(encoding="utf-8")
    d = json.loads(raw)
    new, n = walk(d)
    if n:
        path.write_text(json.dumps(new, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print(n, "string(s) replaced in", path)
