"""Finds copied article text in the files we publish as evidence.

The repository is public and the writing in it is not ours. Evidence files (autopilot/evidence/ and
data/evals/runs/) may name a page by its URL, its SHA-256 and its length, and may hold our own outputs
(verdicts, scores, briefs we wrote). They may not hold text copied from a source page.

The test is mechanical: a file fails if it contains LIMIT (200) or more consecutive characters of any
stored excerpt, after whitespace is collapsed and the common typographic variants are made plain, so a
re-wrapped, re-quoted or JSON-escaped copy is found as well as a verbatim one. A second, folded pass (letters and digits only, lower case, markup and hidden characters removed) catches
a copy dressed as a blockquote, a list, an HTML fragment or a hyphenated wrap. The corpus is every
excerpt in the database plus every long string in the golden fixtures (read only).

    python -m agents.article_text            # scan the two evidence trees, exit 1 on a hit
"""

from __future__ import annotations

import html
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

REPO = Path(__file__).resolve().parent.parent
LIMIT = 200
TREES = ("autopilot/evidence", "data/evals/runs")
GOLDEN = "data/evals/golden"

_WS = re.compile(r"\s+")
_PLAIN = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", " ": " ",
})
_DIFF_PREFIX = re.compile(r"^[+\- ]", re.M)


_TAGS = re.compile(r"<[^>]*>")
_HIDDEN = re.compile("[\u00ad\u200b\u200c\u200d\u2060\ufeff]")
_HYPHEN_WRAP = re.compile(r"(\w)-\s*\n\s*(\w)")
_NON_ALNUM = re.compile(r"[\W_]+")


def normalise(text: str) -> str:
    return _WS.sub(" ", text.translate(_PLAIN)).strip()


def fold(text: str) -> str:
    """Letters and digits only, lower case, markup and hidden characters gone: a copy dressed up as a quote,
    a list item, an HTML fragment or a hyphenated wrap folds to the same string as the source."""
    text = _HYPHEN_WRAP.sub(r"\1\2", text)
    text = _HIDDEN.sub("", _TAGS.sub("", html.unescape(text)))
    return _NON_ALNUM.sub("", text.lower())


def _strings(obj) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def golden_corpus(root: Path = REPO) -> List[Tuple[str, str]]:
    """The long strings of the golden cases' INPUT files: the source excerpts. Their output and label files hold our own writing and
    hand labels, which evidence may quote; the pages they were written from it may not."""
    out: List[Tuple[str, str]] = []
    for path in sorted((root / GOLDEN).rglob("input.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for i, s in enumerate(_strings(data)):
            if len(normalise(s)) >= LIMIT:
                out.append((f"golden:{path.relative_to(root / GOLDEN)}#{i}", s))
    return out


def db_corpus(conn) -> List[Tuple[str, str]]:
    from agents import excerpts
    out: List[Tuple[str, str]] = []
    for (eid,) in conn.execute("SELECT id FROM excerpts ORDER BY id").fetchall():
        out.append((f"excerpt:{eid}", excerpts.get(conn, eid if isinstance(eid, uuid.UUID) else uuid.UUID(str(eid)))))
    return out


class Index:
    """Every LIMIT-character window of every corpus text, by hash, so a file is scanned in one pass."""

    def __init__(self, corpus: Iterable[Tuple[str, str]]):
        self.windows: Dict[int, str] = {}
        self.folded: Dict[int, str] = {}
        self.texts = 0
        for label, text in corpus:
            n, f = normalise(text), fold(text)
            self.texts += 1
            for i in range(0, max(0, len(n) - LIMIT + 1)):
                self.windows.setdefault(hash(n[i:i + LIMIT]), label)
            for i in range(0, max(0, len(f) - LIMIT + 1)):
                self.folded.setdefault(hash(f[i:i + LIMIT]), label)

    def find(self, text: str) -> str:
        """The label of the first corpus text that shares LIMIT consecutive characters with `text`, or ''."""
        n = normalise(text)
        for i in range(0, max(0, len(n) - LIMIT + 1)):
            label = self.windows.get(hash(n[i:i + LIMIT]))
            if label is not None:
                return label
        f = fold(text)
        for i in range(0, max(0, len(f) - LIMIT + 1)):
            label = self.folded.get(hash(f[i:i + LIMIT]))
            if label is not None:
                return label
        return ""


def _unwrap(s: str, depth: int = 0) -> Iterable[str]:
    """A string, and the strings inside it when it is itself JSON (a copy can sit two encodings deep)."""
    yield s
    if depth < 3 and s.lstrip()[:1] in ("{", "["):
        try:
            for inner in _strings(json.loads(s)):
                yield from _unwrap(inner, depth + 1)
        except ValueError:
            pass


def _variants(path: Path, raw: str) -> Iterable[str]:
    """The ways a copy can sit in a file: as is, with JSON escapes resolved, with diff prefixes removed."""
    yield raw
    if path.suffix in (".json", ".jsonl") or raw.lstrip()[:1] in ("{", "["):
        try:
            for s in _strings(json.loads(raw)):
                yield from _unwrap(s)
        except ValueError:
            for line in raw.splitlines():
                try:
                    for s in _strings(json.loads(line)):
                        yield from _unwrap(s)
                except ValueError:
                    continue
    if path.suffix in (".diff", ".patch"):
        yield _DIFF_PREFIX.sub("", raw)
    if "\\" in raw:
        yield raw.replace("\\n", " ").replace('\\"', '"').replace("\\'", "'")


def scan_text(index: Index, path: Path, raw: str) -> str:
    for v in _variants(path, raw):
        hit = index.find(v)
        if hit:
            return hit
    return ""


def scan_tree(index: Index, root: Path = REPO, trees: Iterable[str] = TREES) -> List[Tuple[str, str]]:
    """[(relative path, label of the corpus text it copies)] for every file that holds LIMIT+ characters of one."""
    hits: List[Tuple[str, str]] = []
    for tree in trees:
        base = root / tree
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            try:
                blob = path.read_bytes()
                raw = blob.decode("utf-16", errors="ignore") if blob[:2] in (b"\xff\xfe", b"\xfe\xff") else blob.decode("utf-8", errors="ignore")
            except OSError:
                continue
            hit = scan_text(index, path, raw)
            if hit:
                hits.append((str(path.relative_to(root)), hit))
    return hits


def main() -> int:
    from agents import budget
    with budget.connect() as conn:
        corpus = db_corpus(conn) + golden_corpus()
    index = Index(corpus)
    hits = scan_tree(index)
    print(f"corpus: {index.texts} texts, {len(index.windows)} windows; files holding {LIMIT}+ copied characters: {len(hits)}")
    for path, label in hits:
        print(f"  {path}  <- {label}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
