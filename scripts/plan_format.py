"""Parse plan/plan.md and plan/roadmap.md into the models their templates render.

plan/FORMAT.md is the contract. This module is the only reader of these two files: the
/plan and /roadmap routes call it to build their pages, and scripts/check_plan_format.py
calls it to validate them. One parser for both means a file the check passes is exactly a
file the renderer understands — two readers would be two opinions about the format.

Every rule violation is collected rather than raised on the first one, so a single run
names everything wrong in a file. Each error carries the file, the 1-based line in that
file, and the name of the rule it breaks.

Markdown is parsed by markdown-it-py (CommonMark plus GFM tables) and front matter by
PyYAML. Rules are checked against the parsed token stream, never by pattern-matching the
markdown source. The two places the raw source is consulted are deliberate and narrow:
splitting off the front matter, and counting a table row's cells, because the table parser
silently drops a cell an unescaped `|` pushes past the header's width.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from markdown_it import MarkdownIt

# WHAT: CommonMark with inline HTML on, plus GFM tables.
# CONCEPT: html stays on because FORMAT.md allows inline HTML where markdown cannot
# express something, and because HTML comments must parse as comments (so they can be
# dropped) rather than as escaped text that would reach the page.
MD = MarkdownIt("commonmark", {"html": True}).enable("table")

FRONT_MATTER_KEYS = ("title", "kicker", "lede", "footer", "updated")

PLAN_COLUMNS = ["Activity", "Outcome", "Why it matters", "Status"]

MILESTONE_RE = re.compile(r"^M(\d+) · (\S.*)$")
DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) · (\S.*)$")
BACKLOG_ITEM_RE = re.compile(r"^(\S+) · (\S.*)$")
BACKLOG_ID_RE = re.compile(r"^bk-[a-z0-9]+(?:-[a-z0-9]+)*$")
ACTIVITY_BK_RE = re.compile(r"bk-[a-z0-9]+(?:-[a-z0-9]+)*")
STATUS_RE = re.compile(r"^(DONE|IN PROGRESS|TO DO)(?: · (\S.*))?$")

ROADMAP_STATUSES = ("NOW", "NEXT", "LATER")
MILESTONE_FIELDS = ("Status", "One line", "Why", "Depends on", "Also includes", "Plan", "Live plan")
MILESTONE_REQUIRED = ("Status", "One line", "Why")
MILESTONE_LISTS = ("Scope", "Exit", "Not in")
BACKLOG_FIELDS = ("Size", "Origin")
SIZES = ("S", "M", "L", "—")

# Tags markdown already has a spelling for. FORMAT.md: inline HTML only where markdown
# cannot express it, so these are refused and the error names the markdown to use.
MARKDOWN_EXPRESSIBLE = {"b": "**", "strong": "**", "i": "*", "em": "*", "code": "`", "a": "[text](url)"}
HTML_TAG_RE = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")

# A sentence ends at . ! or ? (plus any closing quote or bracket), followed by space and
# something that can start a sentence. "$0.10" and "framer.X" have no space, so they
# do not end one.
SENTENCE_END_RE = re.compile(r"[.!?][\"”’)\]]*\s+(?=[A-Z0-9“\"‘(])")


@dataclass
class FormatError:
    file: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: [{self.rule}] {self.message}"


class PlanFormatError(Exception):
    def __init__(self, errors: list[FormatError]):
        self.errors = errors
        super().__init__("\n".join(str(e) for e in errors))


@dataclass
class _Ctx:
    """One file's parse state: the tokens, where its body starts, and its errors."""

    file: str
    offset: int
    tokens: list = field(default_factory=list)
    errors: list[FormatError] = field(default_factory=list)

    def err(self, token_or_line, rule: str, message: str) -> None:
        if isinstance(token_or_line, int):
            line = token_or_line
        else:
            line = self.line(token_or_line)
        self.errors.append(FormatError(self.file, line, rule, message))

    def line(self, token) -> int:
        if token is not None and token.map:
            return token.map[0] + self.offset + 1
        return self.offset + 1


# --------------------------------------------------------------------------------------
# Token helpers
# --------------------------------------------------------------------------------------

def _is_comment(token) -> bool:
    return token.type in ("html_block", "html_inline") and token.content.lstrip().startswith("<!--")


def _blocks(tokens: list, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """Top-level blocks in tokens[start:end], as (first, past-last) index pairs.

    A block is an *_open token and everything up to its matching *_close, or a single
    self-contained token (inline at this level never occurs; fence, hr, html_block do).
    """
    end = len(tokens) if end is None else end
    out, i = [], start
    base = tokens[start].level if start < end else 0
    while i < end:
        t = tokens[i]
        if t.nesting == 1:
            depth, j = 0, i
            while j < end:
                depth += tokens[j].nesting
                j += 1
                if depth == 0:
                    break
            out.append((i, j))
            i = j
        else:
            if t.level == base:
                out.append((i, i + 1))
            i += 1
    return out


def _clean_children(children: list) -> list:
    """Children minus comments and the empty text tokens markdown-it emits around markup."""
    return [c for c in (children or []) if not _is_comment(c) and not (c.type == "text" and c.content == "")]


def _render_inline(children: list) -> str:
    return MD.renderer.renderInline(_clean_children(children), MD.options, {})


def _render_blocks(tokens: list) -> str:
    kept = []
    for t in tokens:
        if _is_comment(t):
            continue
        if t.type == "inline" and t.children:
            t = t.copy()
            t.children = _clean_children(t.children)
        kept.append(t)
    return MD.renderer.render(kept, MD.options, {})


def _plain(children: list) -> str:
    """The visible text of an inline token's children, for counting and matching."""
    parts = []
    for c in _clean_children(children):
        if c.type in ("text", "code_inline"):
            parts.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _strip_prefix(children: list, n: int) -> list:
    """Children with the first n visible characters removed from the leading text.

    Headings like `### bk-x · Title with *markup*` are validated on their source text;
    to render the title, the `bk-x · ` prefix is cut from the first text child, which is
    always plain text because the prefix carries no markup.
    """
    children = _clean_children(children)
    if not children or children[0].type != "text":
        return children
    first = children[0].copy()
    first.content = first.content[n:].lstrip()
    return [first] + children[1:] if first.content else children[1:]


def _heading_level(token) -> int:
    return int(token.tag[1])


def _check_inline_html(ctx: _Ctx) -> None:
    def check(token, line_token):
        if _is_comment(token):
            return
        for tag in HTML_TAG_RE.findall(token.content):
            md = MARKDOWN_EXPRESSIBLE.get(tag.lower())
            if md:
                ctx.err(line_token, "inline-html",
                        f"<{tag.lower()}> has a markdown spelling; use {md} instead")

    for t in ctx.tokens:
        if t.type == "html_block":
            check(t, t)
        elif t.type == "inline":
            for c in t.children or []:
                if c.type == "html_inline":
                    check(c, t)


def _sections(ctx: _Ctx, start: int, end: int, level: int) -> tuple[list, list]:
    """Split blocks in [start, end) at headings of `level`.

    Returns (preamble blocks, [(heading_open index, [blocks under it])]). Comment
    blocks are dropped here, so no caller ever has to step around one.
    """
    pre, secs = [], []
    for b in _blocks(ctx.tokens, start, end):
        t = ctx.tokens[b[0]]
        if _is_comment(t):
            continue
        if t.type == "heading_open" and _heading_level(t) == level:
            secs.append((b[0], []))
        elif secs:
            secs[-1][1].append(b)
        else:
            pre.append(b)
    return pre, secs


def _heading_inline(ctx: _Ctx, idx: int):
    return ctx.tokens[idx + 1]


def _labelled(inline) -> tuple[str, list] | None:
    """`**Label:** value` -> (Label, value children). None if not of that shape."""
    ch = _clean_children(inline.children)
    if len(ch) >= 3 and ch[0].type == "strong_open" and ch[1].type == "text" and ch[2].type == "strong_close":
        label = ch[1].content
        if label.endswith(":"):
            rest = ch[3:]
            if rest and rest[0].type == "text":
                lead = rest[0].copy()
                lead.content = lead.content.lstrip()
                rest = ([lead] if lead.content else []) + rest[1:]
            return label[:-1], rest
    return None


def _list_items(ctx: _Ctx, block: tuple[int, int], rule: str) -> list:
    """The inline token of each item in a bullet list; each item must be one paragraph."""
    toks = ctx.tokens
    out = []
    for b in _blocks(toks, block[0] + 1, block[1] - 1):
        item = toks[b[0]]
        inner = [x for x in _blocks(toks, b[0] + 1, b[1] - 1) if not _is_comment(toks[x[0]])]
        if len(inner) != 1 or toks[inner[0][0]].type != "paragraph_open":
            ctx.err(item, rule, "each list item must be a single line of text, with no nested blocks")
            continue
        out.append(toks[inner[0][0] + 1])
    return out


def _field_list(ctx: _Ctx, block, allowed, required, rule: str, where: str) -> dict:
    """Parse a `- **Label:** value` list into {label: (inline token, value children)}."""
    toks = ctx.tokens
    fields: dict = {}
    if toks[block[0]].type != "bullet_list_open":
        ctx.err(toks[block[0]], rule, f"{where} must start with its field list (- **Label:** value)")
        return fields
    for inline in _list_items(ctx, block, rule):
        lv = _labelled(inline)
        if lv is None:
            ctx.err(inline, rule, f"field list item must be '- **Label:** value', got: {inline.content!r}")
            continue
        label, value = lv
        if label not in allowed:
            ctx.err(inline, rule, f"unknown field '{label}' in {where}; allowed: {', '.join(allowed)}")
        elif label in fields:
            ctx.err(inline, rule, f"field '{label}' appears twice in {where}")
        elif not _plain(value):
            ctx.err(inline, rule, f"field '{label}' in {where} is empty")
        else:
            fields[label] = (inline, value)
    for label in required:
        if label not in fields:
            ctx.err(toks[block[0]], rule, f"{where} is missing required field '{label}'")
    return fields


# --------------------------------------------------------------------------------------
# Front matter
# --------------------------------------------------------------------------------------

def _front_matter(file: str, text: str) -> tuple[dict, str, int, list[FormatError]]:
    errors: list[FormatError] = []
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}, text, 0, [FormatError(file, 1, "front-matter", "file must start with YAML front matter ('---' on line 1)")]
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        return {}, text, 0, [FormatError(file, 1, "front-matter", "front matter is never closed with '---'")]
    raw = "\n".join(lines[1:close])
    body = "\n".join(lines[close + 1:])
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 2 if mark else 1
        return {}, body, close + 1, [FormatError(file, line, "front-matter", f"front matter is not valid YAML: {e}")]
    if not isinstance(data, dict):
        return {}, body, close + 1, [FormatError(file, 2, "front-matter", "front matter must be a mapping of key: value")]

    meta = {}
    for key in FRONT_MATTER_KEYS:
        line = next((i + 1 for i in range(1, close) if lines[i].startswith(key + ":")), 1)
        val = data.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            errors.append(FormatError(file, line, "front-matter", f"required key '{key}' is missing or empty"))
            continue
        if key == "updated":
            # YAML reads an unquoted 2026-10-02 as a date; a quoted one as a string.
            if isinstance(val, dt.date):
                meta[key] = val.isoformat()
            else:
                try:
                    meta[key] = dt.date.fromisoformat(str(val)).isoformat()
                    if meta[key] != str(val):
                        raise ValueError
                except ValueError:
                    errors.append(FormatError(file, line, "front-matter.updated", f"'updated' must be YYYY-MM-DD, got {val!r}"))
            continue
        if not isinstance(val, str):
            errors.append(FormatError(file, line, "front-matter", f"'{key}' must be text, got {type(val).__name__}"))
            continue
        meta[key] = val.strip()

    # Front matter values may carry inline markdown (the plan footer has a code span).
    for key in ("lede", "footer"):
        if key in meta:
            meta[key + "_html"] = MD.renderInline(meta[key])
    return meta, body, close + 1, errors


def _load(path: Path) -> tuple[_Ctx, dict]:
    file = path.as_posix()
    try:
        rel = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
        file = rel
    except ValueError:
        pass
    text = path.read_text(encoding="utf-8")
    meta, body, offset, errors = _front_matter(file, text)
    ctx = _Ctx(file=file, offset=offset, tokens=MD.parse(body), errors=errors)
    _check_inline_html(ctx)
    return ctx, meta


# --------------------------------------------------------------------------------------
# plan.md
# --------------------------------------------------------------------------------------

def _table_row_cells(ctx: _Ctx, tr_index: int) -> list:
    """Inline tokens of a table row's cells."""
    toks, cells, i = ctx.tokens, [], tr_index + 1
    while toks[i].type != "tr_close":
        if toks[i].type == "inline":
            cells.append(toks[i])
        i += 1
    return cells


def _raw_cell_count(source_line: str) -> int:
    """Cells on a table source line, splitting on `|` not preceded by a backslash."""
    s = source_line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return len(re.split(r"(?<!\\)\|", s))


def _parse_plan_table(ctx: _Ctx, block, body_lines: list[str], where: str) -> list[dict]:
    toks = ctx.tokens
    rows: list[dict] = []
    head_cells, body_trs = None, []
    for i in range(block[0], block[1]):
        t = toks[i]
        if t.type == "tr_open":
            if head_cells is None:
                head_cells = _table_row_cells(ctx, i)
            else:
                body_trs.append(i)
    names = [c.content.strip() for c in head_cells or []]
    if names != PLAN_COLUMNS:
        ctx.err(toks[block[0]], "plan.table-columns",
                f"{where} Plan table columns must be exactly {PLAN_COLUMNS}, got {names}")
        return rows
    if not body_trs:
        ctx.err(toks[block[0]], "plan.table-columns", f"{where} Plan table has no rows")
    for tr in body_trs:
        tr_tok = toks[tr]
        line_no = tr_tok.map[0] if tr_tok.map else None
        if line_no is not None and line_no < len(body_lines):
            n = _raw_cell_count(body_lines[line_no])
            if n != len(PLAN_COLUMNS):
                ctx.err(tr_tok, "plan.pipe-escape",
                        f"row has {n} cells, not {len(PLAN_COLUMNS)}; a '|' inside a cell must be written '\\|'")
                continue
        cells = _table_row_cells(ctx, tr)
        activity, outcome, why, status = cells
        status_text = status.content.strip()
        m = STATUS_RE.match(status_text)
        if not m or (m.group(2) and m.group(1) != "TO DO"):
            ctx.err(tr_tok, "plan.status-cell",
                    f"Status must be exactly DONE, IN PROGRESS, TO DO or 'TO DO · <blocker>', got {status_text!r}")
            continue
        kind = {"DONE": "done", "IN PROGRESS": "progress", "TO DO": "todo"}[m.group(1)]
        if m.group(2):
            kind = "blocked"

        act_text = activity.content.strip()
        ids = ACTIVITY_BK_RE.findall(act_text)
        if ids:
            trailing = re.search(r"\((bk-[a-z0-9]+(?:-[a-z0-9]+)*)\)$", act_text)
            if len(ids) != 1 or not trailing:
                ctx.err(tr_tok, "plan.backlog-id",
                        f"a backlog id in an Activity must end it, in parentheses, e.g. '(bk-agent-hooks)': {act_text!r}")

        rows.append({
            "activity": _render_inline(activity.children),
            "outcome": _render_inline(outcome.children),
            "why": _render_inline(why.children),
            "status": kind,
            "label": m.group(1),
            "blocker": m.group(2),
        })
    return rows


def _count_sentences(text: str) -> int:
    text = text.strip()
    return len(SENTENCE_END_RE.findall(text)) + 1 if text else 0


def parse_plan(path: Path) -> tuple[dict, list[FormatError]]:
    ctx, meta = _load(path)
    toks = ctx.tokens
    body_lines = path.read_text(encoding="utf-8").split("\n")[ctx.offset:]
    pre, secs = _sections(ctx, 0, len(toks), 1)
    for b in pre:
        ctx.err(toks[b[0]], "plan.headings", "content before the first '#' heading; only comments may sit there")

    milestones, decisions = [], []
    seen_log = False
    for k, (h_idx, blocks) in enumerate(secs):
        h = toks[h_idx]
        title = _heading_inline(ctx, h_idx).content.strip()
        if title == "Decision log":
            if seen_log:
                ctx.err(h, "plan.headings", "there must be exactly one '# Decision log'")
            seen_log = True
            if k != len(secs) - 1:
                ctx.err(h, "plan.headings", "'# Decision log' must be the last top-level heading")
            decisions = _parse_decision_log(ctx, blocks)
            continue
        m = MILESTONE_RE.match(title)
        if not m:
            ctx.err(h, "plan.headings",
                    f"top-level heading must be '# M<n> · <title>' or '# Decision log', got {title!r}")
            continue
        if seen_log:
            continue  # already reported: the log must come last
        milestones.append(_parse_plan_milestone(ctx, h_idx, blocks, m, body_lines))

    if not milestones:
        ctx.err(ctx.offset + 1, "plan.headings", "plan.md needs at least one '# M<n> · <title>' milestone")
    if not seen_log:
        ctx.err(ctx.offset + 1, "plan.headings", "plan.md needs a '# Decision log' section, last")

    return {"meta": meta, "milestones": milestones, "decisions": decisions}, ctx.errors


def _parse_plan_milestone(ctx: _Ctx, h_idx: int, blocks, m, body_lines) -> dict:
    toks = ctx.tokens
    code, where = f"M{m.group(1)}", f"M{m.group(1)}"
    ms = {"code": code, "id": code.lower(), "title": _render_inline(_strip_prefix(_heading_inline(ctx, h_idx).children, len(code) + 3)),
          "end_state": "", "plan_intro": "", "rows": [], "status_html": ""}

    # End state: the first block, a paragraph opening with **End state:**.
    head_blocks = []
    sub = []
    for b in blocks:
        t = toks[b[0]]
        if t.type == "heading_open":
            if _heading_level(t) != 2:
                ctx.err(t, "plan.milestone-sections", f"only '##' headings belong inside {where}, found '{t.markup}'")
                continue
            sub.append((b[0], []))
        elif sub:
            sub[-1][1].append(b)
        else:
            head_blocks.append(b)

    es = None
    if head_blocks and toks[head_blocks[0][0]].type == "paragraph_open":
        lv = _labelled(toks[head_blocks[0][0] + 1])
        if lv and lv[0] == "End state":
            es = lv[1]
    if es is None:
        ctx.err(toks[h_idx], "plan.end-state", f"{where} must open with a '**End state:** ...' paragraph directly under its heading")
    else:
        ms["end_state"] = _render_inline(es)
    for b in head_blocks[1:]:
        ctx.err(toks[b[0]], "plan.milestone-sections", f"unexpected content in {where} between End state and '## Plan'")

    names = [_heading_inline(ctx, i).content.strip() for i, _ in sub]
    for (i, _), name in zip(sub, names):
        if name not in ("Plan", "Status"):
            ctx.err(toks[i], "plan.milestone-sections", f"{where} may only hold '## Plan' and '## Status', found '## {name}'")
    if names.count("Plan") != 1:
        ctx.err(toks[h_idx], "plan.plan-table", f"{where} needs exactly one '## Plan'")
    if names.count("Status") != 1:
        ctx.err(toks[h_idx], "plan.status-section", f"{where} needs exactly one '## Status'")
    if "Plan" in names and "Status" in names and names.index("Plan") > names.index("Status"):
        ctx.err(toks[h_idx], "plan.milestone-sections", f"in {where}, '## Plan' must come before '## Status'")

    for (i, bl), name in zip(sub, names):
        if name == "Plan":
            tables = [b for b in bl if toks[b[0]].type == "table_open"]
            paras = [b for b in bl if toks[b[0]].type == "paragraph_open"]
            others = [b for b in bl if toks[b[0]].type not in ("table_open", "paragraph_open")]
            if len(tables) != 1:
                ctx.err(toks[i], "plan.plan-table", f"{where} '## Plan' must hold exactly one table, found {len(tables)}")
            for b in others:
                ctx.err(toks[b[0]], "plan.plan-table", f"{where} '## Plan' holds only an optional intro paragraph and the table")
            if paras:
                if len(paras) > 1 or (tables and paras[0][0] > tables[0][0]):
                    ctx.err(toks[paras[-1][0]], "plan.plan-table",
                            f"{where} '## Plan' allows one intro paragraph, between the heading and the table")
                ms["plan_intro"] = _render_inline(toks[paras[0][0] + 1].children)
            if tables:
                ms["rows"] = _parse_plan_table(ctx, tables[0], body_lines, where)
        elif name == "Status":
            paras = [b for b in bl if toks[b[0]].type == "paragraph_open"]
            for b in bl:
                if toks[b[0]].type != "paragraph_open":
                    ctx.err(toks[b[0]], "plan.status-section", f"{where} '## Status' is prose only: paragraphs, no lists, tables or headings")
            if not paras:
                ctx.err(toks[i], "plan.status-section", f"{where} '## Status' is empty")
            ms["status_html"] = "".join(f"<p>{_render_inline(toks[b[0] + 1].children)}</p>" for b in paras)
            ms["status_paras"] = [_render_inline(toks[b[0] + 1].children) for b in paras]

    # Progress is derived here, from the status cells, and never written in the file.
    total = len(ms["rows"])
    done = sum(1 for r in ms["rows"] if r["status"] == "done")
    percent = int(100 * done / total + 0.5) if total else 0
    ms.update(total=total, done=done, percent=percent, progress=f"{percent}% · {done} of {total}")
    return ms


def _parse_decision_log(ctx: _Ctx, blocks) -> list[dict]:
    toks = ctx.tokens
    entries, cur = [], None
    for b in blocks:
        t = toks[b[0]]
        if t.type == "heading_open":
            if _heading_level(t) != 3:
                ctx.err(t, "plan.decision-entry", f"Decision log entries are '### YYYY-MM-DD · <title>', found a '{t.markup}' heading")
                cur = None
                continue
            text = _heading_inline(ctx, b[0]).content.strip()
            m = DATED_RE.match(text)
            date = None
            if m:
                try:
                    date = dt.date.fromisoformat(m.group(1))
                except ValueError:
                    pass
            if not m or date is None:
                ctx.err(t, "plan.decision-entry", f"Decision log entry must be '### YYYY-MM-DD · <title>', got {text!r}")
                cur = None
                continue
            cur = {"date": m.group(1), "_date": date, "_tok": t,
                   "title": _render_inline(_strip_prefix(_heading_inline(ctx, b[0]).children, len(m.group(1)) + 3)),
                   "paras": [], "_plain": []}
            entries.append(cur)
        elif cur is None:
            ctx.err(t, "plan.decision-entry", "content in the Decision log outside any '### YYYY-MM-DD · <title>' entry")
        elif t.type != "paragraph_open":
            ctx.err(t, "plan.decision-entry", "a Decision log entry body is paragraphs only")
        else:
            cur["paras"].append(_render_inline(toks[b[0] + 1].children))
            cur["_plain"].append(_plain(toks[b[0] + 1].children))

    for i, e in enumerate(entries):
        if not e["paras"]:
            ctx.err(e["_tok"], "plan.decision-entry", f"entry {e['date']} has no body")
        n = sum(_count_sentences(p) for p in e["_plain"])
        if n > 5:
            ctx.err(e["_tok"], "plan.decision-length", f"entry {e['date']} runs {n} sentences; at most five")
        if i and e["_date"] > entries[i - 1]["_date"]:
            ctx.err(e["_tok"], "plan.decision-order",
                    f"entry {e['date']} is newer than the one above it ({entries[i - 1]['date']}); newest first")
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]


# --------------------------------------------------------------------------------------
# roadmap.md
# --------------------------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_roadmap(path: Path) -> tuple[dict, list[FormatError]]:
    ctx, meta = _load(path)
    toks = ctx.tokens
    pre, secs = _sections(ctx, 0, len(toks), 1)
    for b in pre:
        ctx.err(toks[b[0]], "roadmap.headings", "content before the first '#' heading; only comments may sit there")

    sections, backlog, ids = [], None, {}
    backlog_count = 0
    for k, (h_idx, blocks) in enumerate(secs):
        h = toks[h_idx]
        inline = _heading_inline(ctx, h_idx)
        title = inline.content.strip()
        m = MILESTONE_RE.match(title)
        if title == "Backlog":
            backlog_count += 1
            if backlog_count > 1:
                ctx.err(h, "roadmap.backlog", "there must be exactly one '# Backlog'")
                continue
            if k != len(secs) - 1:
                ctx.err(h, "roadmap.backlog", "'# Backlog' must be the last top-level heading")
            backlog = _parse_backlog(ctx, blocks, ids)
            sections.append({"kind": "backlog", "id": "backlog", **backlog})
        elif m:
            sections.append(_parse_roadmap_milestone(ctx, h_idx, blocks, m))
        else:
            # A prose section, rendered in place.
            html = _render_blocks([t for b in blocks for t in toks[b[0]:b[1]]])
            sections.append({"kind": "prose", "id": _slug(_plain(inline.children)),
                             "title": _render_inline(inline.children), "title_text": _plain(inline.children), "html": html})

    if backlog_count == 0:
        ctx.err(ctx.offset + 1, "roadmap.backlog", "roadmap.md needs a '# Backlog' section, last")

    milestones = [s for s in sections if s["kind"] == "milestone"]
    now = [s for s in milestones if s.get("status") == "NOW"]
    if len(now) != 1:
        lines = ", ".join(f"{s['code']} (line {s['_line']})" for s in now) or "none"
        ctx.err(now[1]["_line"] if len(now) > 1 else ctx.offset + 1, "roadmap.one-now",
                f"exactly one milestone must have Status: NOW, found {len(now)}: {lines}")
    for s in milestones:
        s.pop("_line", None)

    # WHAT: group each run of non-NOW milestones under one "Next milestones" block.
    # CONCEPT: the page shows the NOW milestone as its own section and the ones after it
    # as cards under a shared heading, numbered once in the ToC. File order is kept.
    blocks = []
    for s in sections:
        if s["kind"] == "milestone" and s.get("status") != "NOW":
            if blocks and blocks[-1]["kind"] == "later":
                blocks[-1]["milestones"].append(s)
            else:
                blocks.append({"kind": "later", "id": "milestones", "milestones": [s]})
        elif s["kind"] == "milestone":
            blocks.append({**s, "kind": "now"})
        else:
            blocks.append(s)

    return {"meta": meta, "sections": sections, "blocks": blocks}, ctx.errors


def _parse_roadmap_milestone(ctx: _Ctx, h_idx: int, blocks, m) -> dict:
    toks = ctx.tokens
    code = f"M{m.group(1)}"
    where = code
    inline = _heading_inline(ctx, h_idx)
    ms = {"kind": "milestone", "code": code, "id": code.lower(), "_line": ctx.line(toks[h_idx]),
          "theme": _render_inline(_strip_prefix(inline.children, len(code) + 3)),
          "theme_text": _plain(_strip_prefix(inline.children, len(code) + 3))}

    head, sub = [], []
    for b in blocks:
        t = toks[b[0]]
        if t.type == "heading_open":
            if _heading_level(t) != 3:
                ctx.err(t, "roadmap.milestone-sections", f"only '###' headings belong inside {where}, found '{t.markup}'")
                continue
            sub.append((b[0], []))
        elif sub:
            sub[-1][1].append(b)
        else:
            head.append(b)

    if not head:
        ctx.err(toks[h_idx], "roadmap.milestone-fields", f"{where} must start with its field list (- **Status:** ...)")
        fields = {}
    else:
        fields = _field_list(ctx, head[0], MILESTONE_FIELDS, MILESTONE_REQUIRED, "roadmap.milestone-fields", where)
        for b in head[1:]:
            ctx.err(toks[b[0]], "roadmap.milestone-sections", f"unexpected content in {where} between its field list and its first '###'")

    if "Status" in fields:
        inl, val = fields["Status"]
        st = _plain(val)
        if st not in ROADMAP_STATUSES:
            ctx.err(inl, "roadmap.milestone-status", f"{where} Status must be NOW, NEXT or LATER, got {st!r}")
        else:
            ms["status"] = st
    for label, key in (("One line", "one_line"), ("Why", "why"), ("Depends on", "depends_on"), ("Also includes", "also_includes")):
        if label in fields:
            ms[key] = _render_inline(fields[label][1])
    if "Plan" in fields:
        inl, val = fields["Plan"]
        links = [c for c in val if c.type == "link_open"]
        if len(links) != 1 or val[0].type != "link_open" or val[-1].type != "link_close":
            ctx.err(inl, "roadmap.milestone-fields", f"{where} Plan must be a single markdown link, e.g. [Detailed plan](/plan)")
        else:
            ms["plan"] = {"href": links[0].attrGet("href"), "label": _render_inline(val[1:-1])}
    if "Live plan" in fields:
        # "<where the plan stands> — <why>": the state renders as a pill, the why beneath it.
        val = fields["Live plan"][1]
        src = fields["Live plan"][0].content.split(":**", 1)[1].strip()
        state, _, why = src.partition(" — ")
        ms["live_plan"] = {"state": MD.renderInline(state.strip()), "why": MD.renderInline(why.strip()) if why else ""}

    names = [_heading_inline(ctx, i).content.strip() for i, _ in sub]
    order = [n for n in MILESTONE_LISTS if n in names]
    for (i, bl), name in zip(sub, names):
        if name not in MILESTONE_LISTS:
            ctx.err(toks[i], "roadmap.milestone-sections", f"{where} sections are '### Scope', '### Exit' and '### Not in'; found '### {name}'")
            continue
        if names.count(name) > 1:
            ctx.err(toks[i], "roadmap.milestone-sections", f"{where} has '### {name}' more than once")
        if len(bl) != 1 or toks[bl[0][0]].type != "bullet_list_open":
            ctx.err(toks[i], "roadmap.milestone-sections", f"{where} '### {name}' must hold exactly one bullet list")
            continue
        key = {"Scope": "scope", "Exit": "exit", "Not in": "not_in"}[name]
        ms[key] = [_render_inline(x.children) for x in _list_items(ctx, bl[0], "roadmap.milestone-sections")]
    if [n for n in names if n in MILESTONE_LISTS] != order:
        ctx.err(toks[h_idx], "roadmap.milestone-sections", f"{where} sections must come in the order Scope, Exit, Not in")
    return ms


def _parse_backlog(ctx: _Ctx, blocks, ids: dict) -> dict:
    toks = ctx.tokens
    intro, groups = [], []
    group, item = None, None
    for b in blocks:
        t = toks[b[0]]
        if t.type == "heading_open" and _heading_level(t) == 2:
            name_inline = _heading_inline(ctx, b[0])
            group = {"name": _render_inline(name_inline.children), "id": f"bk-{len(groups) + 1}", "entries": []}
            groups.append(group)
            item = None
        elif t.type == "heading_open" and _heading_level(t) == 3:
            if group is None:
                ctx.err(t, "roadmap.backlog-groups", "a backlog item must sit under a '##' group heading")
                item = None
                continue
            text = _heading_inline(ctx, b[0]).content.strip()
            m = BACKLOG_ITEM_RE.match(text)
            if not m or not BACKLOG_ID_RE.match(m.group(1)):
                ctx.err(t, "roadmap.backlog-item", f"backlog item must be '### bk-<id> · <title>', got {text!r}")
                item = None
                continue
            bid = m.group(1)
            if bid in ids:
                ctx.err(t, "roadmap.backlog-unique-id", f"backlog id '{bid}' is already used on line {ids[bid]}")
            else:
                ids[bid] = ctx.line(t)
            item = {"id": bid, "_tok": t, "_fields": False,
                    "title": _render_inline(_strip_prefix(_heading_inline(ctx, b[0]).children, len(bid) + 3)),
                    "paras": []}
            group["entries"].append(item)
        elif t.type == "heading_open":
            ctx.err(t, "roadmap.backlog-groups", f"the Backlog holds '##' groups and '###' items only, found '{t.markup}'")
        elif group is None:
            if t.type != "paragraph_open" or intro:
                ctx.err(t, "roadmap.backlog-groups", "the Backlog allows one intro paragraph before its first '##' group")
            else:
                intro.append(_render_inline(toks[b[0] + 1].children))
        elif item is None:
            ctx.err(t, "roadmap.backlog-groups", "content under a backlog group outside any '### bk-<id> · <title>' item")
        elif not item["_fields"]:
            item["_fields"] = True
            f = _field_list(ctx, b, BACKLOG_FIELDS, BACKLOG_FIELDS, "roadmap.backlog-fields", item["id"])
            if "Size" in f:
                size = _plain(f["Size"][1])
                if size not in SIZES:
                    ctx.err(f["Size"][0], "roadmap.backlog-fields", f"{item['id']} Size must be S, M, L or —, got {size!r}")
                item["size"] = size
            if "Origin" in f:
                item["origin"] = _render_inline(f["Origin"][1])
        elif t.type != "paragraph_open":
            ctx.err(t, "roadmap.backlog-item", f"{item['id']} body is paragraphs only")
        else:
            item["paras"].append(_render_inline(toks[b[0] + 1].children))

    for g in groups:
        for it in g["entries"]:
            if not it["_fields"]:
                ctx.err(it["_tok"], "roadmap.backlog-fields", f"{it['id']} must open with '- **Size:**' and '- **Origin:**'")
            elif not it["paras"]:
                ctx.err(it["_tok"], "roadmap.backlog-item", f"{it['id']} has no body")
            for k in [k for k in it if k.startswith("_")]:
                del it[k]
        g["count"] = len(g["entries"])
    total = sum(g["count"] for g in groups)
    return {"intro": intro[0] if intro else "", "groups": groups, "total": total}


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------

def load_plan(path: Path) -> dict:
    model, errors = parse_plan(path)
    if errors:
        raise PlanFormatError(errors)
    return model


def load_roadmap(path: Path) -> dict:
    model, errors = parse_roadmap(path)
    if errors:
        raise PlanFormatError(errors)
    return model


def check(plan_path: Path, roadmap_path: Path) -> list[FormatError]:
    return parse_plan(plan_path)[1] + parse_roadmap(roadmap_path)[1]
