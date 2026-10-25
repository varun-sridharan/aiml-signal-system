"""Counts across stories are checked by code (Run 4, Step C).

A sentence such as "four releases cut the cost of running a model" is a claim no single excerpt can settle, so the
faithfulness grader never sees it as a fact. It is still a claim, and a wrong count is a wrong sentence. The rule:

  * when the writer states a number of stories or items, it must list that count in `counts` as
    {path, phrase, stories}: the field it is in, the words exactly as written, and the ids of the stories counted;
  * code checks the phrase is in the field, the ids are real and distinct, the number of ids is the number written,
    and, for "three of this week's nine stories", that the total is the number of stories in the edition.

What this does not check is that the named stories are *releases* or *papers* (that is the grader's job for each
story's own text, and the pill for papers is not a reliable test). It makes the count a list kept with the stored
edition (`counts`), and it makes a miscount, an unnamed count and an invented story id fail the edition.

Fields that draw on one story's excerpt only (a story's headline, body, feature paragraphs, try-it) are not scanned
for plain counts, because "five models" there is a fact of that story; they are scanned for the "N of the M stories"
form, which can only refer to the edition.
"""

from __future__ import annotations

import re

try:
    from . import style_check
except ImportError:  # pragma: no cover - top-level import from the eval harness
    import style_check

WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen "
    "nineteen twenty".split())}
WORDS.update({"both": 2, "a pair of": 2, "a couple of": 2, "a dozen": 12})
NUM = r"(?:" + "|".join(sorted((w.replace(" ", r"\s+") for w in WORDS if WORDS[w] >= 2), key=len, reverse=True)) + r"|\d{1,2})"
# Nouns that name what a digest counts across its stories. Others ("labs", "updates", "benchmarks", "models") are far more often one
# story's own fact ("the vendor shipped three updates"), and a check that flags those can only be satisfied by inventing a list.
NOUNS = r"(?:stories|story|items|releases|papers|launches|announcements|decisions)"
STORY_NOUNS = ("stories", "items")                   # only these are the edition itself, so only for these is "of the M" the edition's size
_FILLER = r"(?:(?!(?:of|the|a|an|and|or|in|to|by|than|from|for|on|with|that|at|but|as|is|are|was|were|has|have)\b)[A-Za-z][A-Za-z\-]*\s+){0,2}"
OF_FORM = re.compile(rf"\b({NUM})\s+(?:of|in)\s+(?:(?:this|the)\s+week's\s+|the\s+|these\s+|those\s+|our\s+)?(?:({NUM})\s+)?({NOUNS})\b", re.I)
PLAIN_FORM = re.compile(rf"\b({NUM})\s+{_FILLER}({NOUNS})\b", re.I)

SINGLE_STORY_PATH = re.compile(r"^story\.s\d+\.(?:headline|body|feature\.\d+|tryIt)$")


def number(word: str) -> int:
    word = re.sub(r"\s+", " ", word.lower())
    return WORDS[word] if word in WORDS else int(word)


def mentions(path: str, text: str) -> list[dict]:
    """Every count of stories or items the text states: {phrase, n, total, form}. Tokens are read as the words they show."""
    plain = style_check.plain(text)
    out, taken = [], []
    for m in OF_FORM.finditer(plain):
        out.append({"phrase": m.group(0), "n": number(m.group(1)), "total": number(m.group(2)) if m.group(2) else None, "form": "of",
                    "noun": m.group(3).lower()})
        taken.append(m.span())
    if SINGLE_STORY_PATH.match(path):
        return out
    for m in PLAIN_FORM.finditer(plain):
        if any(a <= m.start() < b for a, b in taken):
            continue
        out.append({"phrase": m.group(0), "n": number(m.group(1)), "total": None, "form": "plain", "noun": m.group(2).lower()})
    return out


def _key(s: str) -> str:
    return re.sub(r"\s+", " ", style_check.plain(s)).strip().lower()


def count_failures(composed: dict, ids: dict, fields: dict) -> list[str]:
    """One line per count that is unnamed, wrongly numbered, names a story that does not exist, or has the wrong total.

    `ids` is {story id: item id} for the edition; `fields` is composer.prose_fields(composed).
    """
    entries: dict = {}
    for e in composed.get("counts") or []:
        entries.setdefault((e.get("path"), _key(e.get("phrase") or "")), []).append(e)
    out = []
    for path, text in fields.items():
        for m in mentions(path, text):
            pool = entries.get((path, _key(m["phrase"])))
            found = [pool.pop(0)] if pool else None            # each mention uses one entry, so a phrase said twice is listed twice
            if not found:
                out.append(f"{path}: count — '{m['phrase']}' states a count of stories with no entry in `counts` naming them: "
                           f"list the {m['n']} stories in `counts`, or say it without a number (a figure inside one story's own page belongs in that story's own field)")
                continue
            named = found[0].get("stories") or []
            bad = [s for s in named if s not in ids]
            if bad:
                out.append(f"{path}: count — '{m['phrase']}' names {', '.join(map(str, bad))}, which {'is' if len(bad) == 1 else 'are'} not in this edition")
            elif len(set(named)) != len(named):
                out.append(f"{path}: count — '{m['phrase']}' names the same story twice")
            elif len(named) != m["n"]:
                out.append(f"{path}: count — '{m['phrase']}' says {m['n']} but `counts` names {len(named)} ({', '.join(named) or 'none'})")
            if m["total"] is not None and m["noun"] in STORY_NOUNS and m["total"] != len(ids):
                out.append(f"{path}: count — '{m['phrase']}' gives a total of {m['total']} but this edition has {len(ids)} stories")
            if m["form"] == "of" and m["noun"] in STORY_NOUNS and m["n"] > len(ids):
                out.append(f"{path}: count — '{m['phrase']}' is more than the {len(ids)} stories in this edition")
    return out
