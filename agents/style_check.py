"""The five hard style rules of config/style-guide.md, as a deterministic checker.

A failure here blocks publishing (agents/weekly.py gates_stage). Pure and offline: no model, no
database. Each rule is a function over plain text, and each has a test that fails without it
(tests/test_style_check.py).

  1. every essay paragraph opens on a dated, attributed fact carrying a number
  2. a named company or person is the subject of the sentence, never an abstract noun
  3. no sentence restates its subject as its predicate ("X costs what it costs")
  4. a comparison is computed and both endpoints are shown ("from $96 billion to $2.4 trillion")
  5. no stage directions ("Read that twice", "So ask what", "Here is the shape")

These are heuristics over prose, written to be strict in the direction the style guide asks for:
a sentence that merely looks like a violation fails and is rewritten. They check form, never truth;
whether a sentence is true is the faithfulness gate's job.
"""

from __future__ import annotations

import re

# Text inside the slot tokens ([[hl|x]], [[#s01|x]], [[fn:key|x]]) is read as the plain text it displays.
_TOKEN = re.compile(r"\[\[(?:hl|#[A-Za-z0-9_-]+|fn:[A-Za-z0-9_-]+)\|([^\]]*)\]\]")


def plain(text: str) -> str:
    return _TOKEN.sub(r"\1", text or "")


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(\[$])")
# Abbreviations that end in a full stop without ending the sentence.
_ABBREV = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|St|Inc|Corp|Ltd|Co|vs|etc|e\.g|i\.e|U\.S|U\.K|No|approx|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.$", re.I)


def sentences(text: str) -> list[str]:
    out, buf = [], ""
    for piece in _SENTENCE_END.split(plain(text).strip()):
        buf = f"{buf} {piece}".strip() if buf else piece
        if _ABBREV.search(piece):
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return [s for s in (s.strip() for s in out) if s]


# ---------------------------------------------------------------- rule 1: the opening fact

_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
_DATE = re.compile(
    rf"\b{_MONTH}\s+\d{{1,2}}\b|\b\d{{1,2}}\s+{_MONTH}\b|\b{_MONTH}\s+\d{{4}}\b|\b(?:19|20)\d{{2}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b|"
    r"\b(?:on|last|this|since)\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|"
    r"\b(?:this|last|earlier this|late last)\s+(?:week|month|quarter|year)\b|\b(?:today|yesterday)\b|\bQ[1-4]\b", re.I)
_NUMBER = re.compile(r"\d")
# A capitalised word that is not merely the first word of the sentence: a company, a person, a product.
_STOP_CAPITALISED = {"The", "A", "An", "This", "That", "These", "Those", "It", "Its", "He", "She", "They", "We", "Our", "Your", "In", "On", "At",
                     "By", "For", "From", "With", "After", "Before", "During", "When", "While", "As", "If", "But", "And", "So", "Then", "Here",
                     "There", "What", "Why", "How", "Who", "Where", "Which", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
                     "Nine", "Ten", "Some", "Many", "Most", "Every", "Each", "All", "No", "Not", "Now", "Today", "Yesterday", "Last", "Next",
                     "Early", "Late", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Consider", "Imagine",
                     "Notice", "Let", "Think", "Remember", "Look"} | {m for m in
                     "January February March April May June July August September October November December Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec".split()}
# Capitalised common nouns and acronyms that are not the name of a company or a person.
_NOT_A_NAME = {"SDK", "API", "APIs", "AI", "ML", "LLM", "LLMs", "GPU", "GPUs", "CPU", "UI", "CLI", "URL", "JSON", "SQL", "OS", "IT", "PDF", "HTML", "CEO", "CTO", "US", "UK", "EU"}
_ANNOUNCE = re.compile(
    r"^(?:here(?:'s| is| are)|consider|imagine|picture|think (?:about|of)|let(?:'s| us)|what follows|the (?:story|point|question|lesson|idea|"
    r"takeaway|shape|pattern|week|thing|trend|theme|answer|problem|catch)\b.{0,20}\b(?:is|are|was)\b|there(?:'s| is| are) a (?:story|pattern|"
    r"shape|lesson|question)|something (?:shifted|changed|happened|broke)|capability (?:went|became|got))", re.I)


def opening_failures(paragraph: str) -> list[str]:
    """Rule 1. The first sentence must name who, say when, and carry a number."""
    first = (sentences(paragraph) or [""])[0]
    if not first:
        return ["the paragraph is empty"]
    problems = []
    if _ANNOUNCE.search(first):
        problems.append("opens by announcing that something will be told")
    if not _NUMBER.search(_DATE.sub(" ", first)):         # the digits of the date do not count as the number
        problems.append("the opening sentence carries no number")
    if not _DATE.search(first):
        problems.append("the opening sentence carries no date")
    names = [w for w in re.findall(r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*)*", first) if w.split()[0] not in _STOP_CAPITALISED
             and not re.fullmatch(_MONTH, w.split()[0]) and w not in _NOT_A_NAME]
    if not names:
        problems.append("the opening sentence names no company or person")
    return problems


# ---------------------------------------------------------------- rule 2: the subject

BANNED_SUBJECTS = (
    # the style guide's own list, with plurals
    "capability", "capabilities", "permission", "permissions", "oversight", "verification", "verifications", "mechanism", "mechanisms",
    "shape", "shapes", "axis", "axes", "thing", "things", "scarce good", "scarce goods",
    # the indefinite pronouns its example uses ("Everything that grants permission did")
    "everything", "something",
)
_DETERMINER = r"(?:the|a|an|this|that|these|those|each|every|all|any|such|its|their|our|no|one)"
_SUBJECT = re.compile(
    rf"^(?:{_DETERMINER}\s+(?:[a-z][a-z-]*\s+){{0,2}}?)?(?P<noun>{'|'.join(re.escape(b) for b in sorted(BANNED_SUBJECTS, key=len, reverse=True))})\b(?!['’]s\s+\w)",
    re.I)


def subject_failures(sentence: str) -> list[str]:
    """Rule 2. A sentence may not open on an abstract noun as its subject."""
    s = sentence.strip().lstrip("\"'(“‘")
    m = _SUBJECT.match(s)
    if not m:
        return []
    noun = m.group("noun").lower()
    # A banned noun that is only the tail of a proper name ("Trust Layer Inc") is not an abstract subject.
    words = s.split()
    if words and words[0][:1].isupper() and len(m.group(0).split()) > 1 and m.group(0).split()[-1][:1].isupper() and not re.match(rf"{_DETERMINER}\b", s, re.I):
        return []
    return [f"the subject is the abstract noun '{noun}', not a named company or person"]


# ---------------------------------------------------------------- rule 3: restating

_RESTATE = re.compile(r"\b(?P<s>\w+)\s+(?P<v>\w+)\s+what\s+(?:it|they|he|she|we|you|\1)\s+(?P=v)\b", re.I)
_IS_WHAT = re.compile(r"\b(?P<s>\w+)\s+(?P<v>is|are|was|were|does|did|means|costs|cost)\s+what\s+(?:it|they|he|she|\1)\s+(?:is|are|was|were|does|did|means|costs|cost)\b", re.I)
_TAUTOLOGY = re.compile(r"\b(?P<w>\w{4,})\s+(?:is|are|was|were)\s+(?:a\s+|an\s+|the\s+)?(?P=w)\b", re.I)


def restating_failures(sentence: str) -> list[str]:
    """Rule 3. 'X costs what it costs', 'X is what it is', and their relatives."""
    if _RESTATE.search(sentence) or _IS_WHAT.search(sentence) or _TAUTOLOGY.search(sentence):
        return ["the sentence restates its subject as its predicate"]
    return []


# ---------------------------------------------------------------- rule 4: comparisons

_COMPARATIVE = r"(?:than|higher|lower|faster|slower|cheaper|costlier|more|less|larger|smaller|bigger|longer|shorter|as many|as much|as fast|as large)"
# "5x" is always a ratio; "3 times" only when something is being compared ("3 times faster"), not "ran it 3 times"; "5-fold cross-validation" is not one.
_RATIO = re.compile(r"\b\d+(?:\.\d+)?\s?(?:x|\u00d7)\b|\b\d+(?:\.\d+)?\s?(?:-fold|fold|times)\s+" + _COMPARATIVE + r"\b|\b(?:doubled|tripled|quadrupled|halved)\b|\bby half\b", re.I)
_CHANGE = re.compile(r"\b(?:grew|grow|grows|growing|fell|fall|falls|rose|rise|rises|dropped|drop|declined|decline|increased|increase|decreased|decrease|"
                     r"cut|reduced|jumped|surged|up|down|higher|lower|more|less|faster|slower|cheaper)\b"
                     r"[^.]{0,30}?\b\d+(?:\.\d+)?\s?(?:%|percent|per cent)", re.I)
# Both endpoints are "from X to Y" (or "between X and Y"): a bare "40% to $5 billion" is not an endpoint pair.
_FROM_TO = re.compile(r"\bfrom\s+[^.]*?\d[^.]*?\bto\s+[^.]*?\d|\bbetween\s+[^.]*?\d[^.]*?\band\s+[^.]*?\d", re.I)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12}
_ENDPOINTS = re.compile(r"\bfrom\s+\$?(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?P<ua>k|m|b|t|thousand|million|billion|trillion)?\b(?P<pa>\s*(?:%|percent))?"
                        r"[^.]*?\bto\s+\$?(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<ub>k|m|b|t|thousand|million|billion|trillion)?\b(?P<pb>\s*(?:%|percent))?", re.I)
_RATIO_VALUE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:x|\u00d7|-fold|fold|times)\b", re.I)
_PERCENT_VALUE = re.compile(r"\b(?:grew|grow|grows|fell|fall|falls|rose|rise|rises|dropped|drop|declined|increased|decreased|cut|reduced|jumped|surged|up|down)\b[^.]{0,30}?(\d+(?:\.\d+)?)\s?(?:%|percent|per cent)", re.I)


def _number(text: str, unit: str | None) -> float:
    return float(text.replace(",", "")) * _SCALE.get((unit or "").lower(), 1.0)


def arithmetic_failures(sentence: str) -> list[str]:
    """When a sentence states a ratio or a percentage change AND its endpoints, the computed figure must follow from them."""
    m = _ENDPOINTS.search(sentence)
    if not m or m.group("pa") or m.group("pb"):
        return []                                   # endpoints that are themselves percentages: the change is in points, not checked here
    a, b = _number(m.group("a"), m.group("ua")), _number(m.group("b"), m.group("ub"))
    if a <= 0:
        return []
    out = []
    ratio = _RATIO_VALUE.search(sentence)
    if ratio and float(ratio.group(1)) > 0:
        claimed, actual = float(ratio.group(1)), b / a
        actual = 1 / actual if 0 < actual < 1 else actual          # "dropped 3x" is a fall to a third
        if abs(actual - claimed) / claimed > 0.15:
            out.append(f"the computed ratio {claimed:g}x does not follow from its endpoints ({m.group('a')} to {m.group('b')} is {actual:.3g}x)")
    pct = _PERCENT_VALUE.search(sentence)
    if pct:
        claimed, actual = float(pct.group(1)), abs(b - a) / a * 100
        if abs(actual - claimed) > max(1.5, 0.1 * claimed):
            out.append(f"the computed change {claimed:g}% does not follow from its endpoints ({m.group('a')} to {m.group('b')} is {actual:.3g}%)")
    return out


def comparison_failures(sentence: str) -> list[str]:
    """Rule 4. A ratio or a percentage change needs both endpoints in the same sentence, and the computed figure must follow from them."""
    if (_RATIO.search(sentence) or _CHANGE.search(sentence)) and not _FROM_TO.search(sentence):
        return ["a comparison is given without both endpoints ('from X to Y')"]
    return arithmetic_failures(sentence)


# ---------------------------------------------------------------- rule 5: stage directions

STAGE_DIRECTIONS = (
    r"read (?:that|this|it) (?:twice|again|slowly|carefully)", r"so ask (?:what|why|how|whether|yourself)", r"ask yourself",
    r"here(?:'s| is| are) the (?:shape|point|catch|thing|pattern|twist|question|story)", r"notice (?:that|how|what|the)\b",
    r"the point is\b", r"the takeaway is\b", r"the lesson is\b", r"let(?:'s| us) (?:look|think|be clear|start|pause|zoom|step)",
    r"think about (?:that|this|it)\b", r"consider (?:this|that|the following)\b", r"keep (?:that|this) in mind", r"hold (?:that|this) thought",
    r"remember (?:this|that)\b", r"mark (?:that|this)\b", r"look at (?:that|this)\b", r"(?:^|[.!?]\s+)note (?:that|how)\b", r"watch (?:what|how)\b",
    r"here(?:'s| is) why (?:that|this) matters", r"sit with (?:that|this)", r"think of it (?:this way|like this)", r"let that sink in",
    r"(?:now )?(?:read|see) (?:that|this) (?:again|once more)", r"stop and (?:think|read)", r"pause (?:on|for) (?:that|this)", r"in other words, (?:the|this)\b",
    r"what (?:this|that) (?:really )?means is\b", r"the (?:real|big|important) (?:story|question|point) (?:is|here)\b", r"to put it (?:simply|plainly|another way)",
    r"here is what (?:to|you)", r"you(?:'ll| will) want to (?:read|note|remember)", r"spoiler\b", r"mind the\b",
)
_STAGE = re.compile(r"\b(?:" + "|".join(STAGE_DIRECTIONS) + r")", re.I)


def stage_direction_failures(sentence: str) -> list[str]:
    # Spacing and dash tricks ("Read  that\u2014twice") are folded before matching.
    m = _STAGE.search(re.sub(r"[\s\u2010-\u2015]+", " ", sentence))
    return [f"a stage direction tells the reader how to read the next sentence: '{m.group(0)}'"] if m else []


# ---------------------------------------------------------------- the checker

def prose_failures(text: str, label: str, *, opening: bool = False) -> list[str]:
    """All five rules over one field. `opening` applies rule 1 (essay paragraphs), which also flags a paragraph with no digit."""
    out = []
    if opening:
        out += [f"{label}: rule 1: {p}" for p in opening_failures(text)]
        if not _NUMBER.search(plain(text)):
            out.append(f"{label}: rule 1: a paragraph of argument with no number in it")
    for n, sentence in enumerate(sentences(text), start=1):
        out += [f"{label} sentence {n}: rule 2: {p}" for p in subject_failures(sentence)]
        out += [f"{label} sentence {n}: rule 3: {p}" for p in restating_failures(sentence)]
        out += [f"{label} sentence {n}: rule 4: {p}" for p in comparison_failures(sentence)]
        out += [f"{label} sentence {n}: rule 5: {p}" for p in stage_direction_failures(sentence)]
    return out


THREAD_LINE_WORDS = 8
HEADLINE_WORDS = 14
HEADLINE_CHARS = 90
HEADLINE_FIGURES = 1
_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def headline_findings(edition: dict) -> list[tuple[str, str]]:
    """(path, failure) for the four section headlines (Run 8, widening Run 7's thread rule): the week's thread (two lines), the essay
    headline, the stories headline and the leaders headline.

    Each is at most 14 words and 90 characters (the thread: its two lines together, each line at most 8 words); the thread carries no
    figure and the others at most one; and no number appears in two of them. A finding sends the field back for the one rewrite; it never
    holds an edition.
    """
    out: list[tuple[str, str]] = []
    lines = [plain(x) for x in ((edition.get("thread") or {}).get("lines") or [])]
    for n, line in enumerate(lines, start=1):
        words = len(line.split())
        if words > THREAD_LINE_WORDS:
            out.append((f"thread.line.{n}", f"thread line {n}: {words} words, at most {THREAD_LINE_WORDS}"))
        if _NUMBER.search(line):
            out.append((f"thread.line.{n}", f"thread line {n}: carries a figure; the thread names one shift in words, and numbers belong in the body, the essay and the stories"))
    if lines:
        joined = " ".join(lines)
        if len(joined.split()) > HEADLINE_WORDS:
            out.append(("thread.line.2", f"thread lines: {len(joined.split())} words in all, at most {HEADLINE_WORDS}"))
        if len(joined) > HEADLINE_CHARS:
            out.append(("thread.line.2", f"thread lines: {len(joined)} characters in all, at most {HEADLINE_CHARS}"))
    singles = [("learning.headline", "essay headline", plain((edition.get("learning") or {}).get("headline", ""))),
               ("stories.title", "stories headline", plain(edition.get("storiesTitle", ""))),
               ("leaders.headline", "leaders headline", plain((edition.get("leaders") or {}).get("headline", "")) if isinstance(edition.get("leaders"), dict) else "")]
    for path, label, text in singles:
        if not text:
            continue
        if len(text.split()) > HEADLINE_WORDS:
            out.append((path, f"{label}: {len(text.split())} words, at most {HEADLINE_WORDS}"))
        if len(text) > HEADLINE_CHARS:
            out.append((path, f"{label}: {len(text)} characters, at most {HEADLINE_CHARS}"))
        figures = {f.rstrip(",") for f in _FIGURE.findall(text)}
        if len(figures) > HEADLINE_FIGURES:
            out.append((path, f"{label}: {len(figures)} figures, at most {HEADLINE_FIGURES}"))
    fields = [(f"thread.line.{n}", "thread", x) for n, x in enumerate(lines, start=1)] + [(p, lab.split()[0], t) for p, lab, t in singles]
    seen: dict = {}
    for path, label, text in fields:
        for fig in {f.rstrip(",") for f in _FIGURE.findall(text)}:
            if fig in seen and seen[fig] != path:
                out.append((path, f"{label} headline: the figure {fig} already appears in {seen[fig]}; a number is stated once across the four section headlines"))
            seen.setdefault(fig, path)
    return out


thread_findings = headline_findings


def edition_style_findings(edition: dict) -> list[tuple[str, str]]:
    """(split path, failure) for the rules over every prose field of a composed edition. Essay paragraphs also get rule 1.

    The path is the field's address in agents/composer.py, so a failure can be sent back to the writer with the field it belongs to.
    """
    out: list[tuple[str, str]] = []

    def check(path: str, text: str, label: str, opening: bool = False) -> None:
        out.extend((path, line) for line in prose_failures(text, label, opening=opening))

    thread = edition.get("thread") or {}
    for n, line in enumerate(thread.get("lines") or [], start=1):
        check(f"thread.line.{n}", line, f"thread line {n}")
    check("thread.body", thread.get("body", ""), "thread body")
    out.extend(headline_findings(edition))
    learning = edition.get("learning") or {}
    check("learning.headline", learning.get("headline", ""), "essay headline")
    for n, para in enumerate(learning.get("paras") or [], start=1):
        check(f"learning.para.{n}", para, f"essay paragraph {n}", opening=True)
    check("learning.doDifferently", learning.get("doDifferently", ""), "what to do differently")
    notes = learning.get("notes")
    for note in (notes if isinstance(notes, list) else []):
        if isinstance(note, dict) and note.get("key"):
            check(f"learning.note.{note['key']}", note.get("definition", ""), f"footnote {note['key']}")
    check("stories.title", edition.get("storiesTitle", ""), "stories headline")
    for divider in edition.get("dividers") or []:
        check(f"divider.{divider.get('before')}", divider.get("label", ""), f"divider before {divider.get('before')}")
    for story in edition.get("stories") or []:
        check(f"story.{story['id']}.headline", story.get("headline", ""), f"{story['id']} headline")
        if isinstance(story.get("tie"), dict):
            check(f"story.{story['id']}.tie", story["tie"].get("text", ""), f"{story['id']} tie")
        for field in ("body", "tryIt"):
            if story.get(field):
                check(f"story.{story['id']}.{field}", story[field], f"{story['id']} {field}")
        for n, para in enumerate(story.get("featureParas") or [], start=1):
            check(f"story.{story['id']}.feature.{n}", para, f"{story['id']} feature paragraph {n}")
    closing = edition.get("closing") or {}
    for n, line in enumerate(closing.get("lines") or [], start=1):
        check(f"closing.line.{n}", line, f"closing line {n}")
    check("closing.summary", closing.get("summary", ""), "closing summary")
    check("closing.oneThing", closing.get("oneThing", ""), "closing: one thing to try")
    return out


def edition_style_failures(edition: dict) -> list[str]:
    """The same findings as plain lines."""
    return [line for _, line in edition_style_findings(edition)]
