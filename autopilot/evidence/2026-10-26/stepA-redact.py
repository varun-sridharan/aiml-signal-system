"""Step A: replace copied article text in committed JSON evidence with its URL, SHA-256 and length.

Run once, from the repo root. A string is replaced when it shares 200+ characters with a stored excerpt,
or when it is the value of a `supporting_span` key (the grader's quotation of the source). Our own
outputs (verdicts, scores, claims, briefs we wrote) are left as they are. Nothing in this script or its
output contains article text.
"""
import hashlib, json, sys, uuid
from pathlib import Path
sys.path.insert(0, ".")
from agents import article_text as A, budget

with budget.connect() as conn:
    corpus = A.db_corpus(conn) + A.golden_corpus()
    meta = {str(r[0]): (r[1], r[2]) for r in conn.execute("SELECT id, canonical_url, content_sha256 FROM excerpts").fetchall()}
idx = A.Index(corpus)


def marker(s, label, why):
    sha = hashlib.sha256(s.encode("utf-8")).hexdigest()
    src = ""
    if label.startswith("excerpt:"):
        url, esha = meta.get(label.split(":", 1)[1], ("", ""))
        src = f"; source {url}; excerpt sha256 {esha}"
    elif label:
        src = f"; source {label}"
    return f"[source text withheld ({why}): sha256 {sha}; {len(s)} characters{src}]"


def walk(o, key=""):
    if isinstance(o, str):
        label = idx.find(o)
        if label or key == "supporting_span":
            if o.startswith("[source text withheld"):
                return o, 0
            return marker(o, label or idx_label_loose(o), "copied span" if key == "supporting_span" else "copied text"), 1
        return o, 0
    n = 0
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            out[k], c = walk(v, k)
            n += c
        return out, n
    if isinstance(o, list):
        out = []
        for v in o:
            r, c = walk(v, key)
            out.append(r)
            n += c
        return out, n
    return o, 0


def idx_label_loose(s):
    return ""


changed = {}
for tree in A.TREES:
    for path in sorted(Path(tree).rglob("*.json")):
        raw = path.read_text(encoding="utf-8")
        d = json.loads(raw)
        new, n = walk(d)
        if not n:
            continue
        for indent in (1, 2, None):
            if json.dumps(d, indent=indent, ensure_ascii=False) == raw.rstrip("\n"):
                break
        else:
            indent = 1
        out = json.dumps(new, indent=indent, ensure_ascii=False) + ("\n" if raw.endswith("\n") else "")
        path.write_text(out, encoding="utf-8")
        changed[str(path)] = (n, indent)
for p, (n, i) in changed.items():
    print(n, "string(s) replaced in", p, "(indent", i, ")")
print(len(changed), "files changed")
