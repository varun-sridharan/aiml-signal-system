"""The Product and System Design pages' content, ready for their templates (Run 5, Step C: one build path for every page).

Plan and Roadmap render from markdown; Product and System Design render from application/Product.json and
design/System_Design.json the same way, through templates/product.template.html and templates/system_design.template.html.
This module reads the content files and does only what a template cannot:

  * rewrite the snapshot's relative page links (Weekly-Digest.html, ../plan/Plan.html, ...) to the live routes;
  * turn every colour in an inline figure into a token (`fill="#B5502A"` becomes `style="fill:var(--accent)"`), so no page
    carries a hex of its own: tokens.css is the one place colours are defined;
  * attach the two System Design diagrams that the old bundle drew from a spec at run time (static/figures/*.svg, captured
    from the rendered page) to their figureSpec blocks.

The content itself is not edited. Pure: no database, no network.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRODUCT_JSON = REPO / "application" / "Product.json"
SYSTEM_JSON = REPO / "design" / "System_Design.json"
FIGURES = REPO / "static" / "figures"
TOKEN_FILES = (REPO / "design" / "system" / "tokens" / "colors.css", REPO / "design" / "system" / "tokens" / "site.css")

LINKS = {
    "Product.html": "/application/Product.html", "../application/Product.html": "/application/Product.html",
    "Weekly-Digest.html": "/weekly-digest", "../application/Weekly-Digest.html": "/weekly-digest",
    "../design/System-Design.html": "/design/System-Design.html", "System-Design.html": "/design/System-Design.html",
    "../plan/Roadmap.html": "/roadmap", "../plan/Plan.html": "/plan",
}

# Colours the old figures used that the kit has since corrected: the nearest token, by role.
LEGACY = {"#8B8375": "--faint"}
PREFER = ("--accent", "--ink", "--soft", "--faint", "--rule", "--rule2", "--panel", "--paper", "--accent-soft", "--ok", "--active", "--miss")

_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
_TAG = re.compile(r"<(?P<name>[A-Za-z][\w:-]*)(?P<attrs>(?:\s+[^<>]*?)?)(?P<end>/?)>", re.S)
_ATTR = re.compile(r'(?P<k>[\w:-]+)\s*=\s*(?P<q>["\'])(?P<v>.*?)(?P=q)', re.S)
_PAINT = ("fill", "stroke", "stop-color", "flood-color")


@lru_cache(maxsize=1)
def token_names() -> dict:
    """{'#B5502A': '--accent', ...} from the kit's colour tokens, preferring the base names to the aliases."""
    found: dict = {}
    for path in TOKEN_FILES:
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})\b", path.read_text(encoding="utf-8")):
            found.setdefault(value.upper(), []).append(name)
    out = {}
    for value, names in found.items():
        names = sorted(names, key=lambda n: (n not in PREFER, PREFER.index(n) if n in PREFER else 0, len(n)))
        out[value] = names[0]
    out.update({k: v for k, v in LEGACY.items()})
    return out


def token_for(hex_value: str):
    name = token_names().get(hex_value.upper())
    return f"var({name})" if name else None


def tokenise_css(text: str) -> str:
    """Every #RRGGBB in a stretch of CSS (or a style attribute) that a token covers becomes var(--token)."""
    return _HEX.sub(lambda m: token_for(m.group(0)) or m.group(0), text)


def tokenise_svg(svg: str) -> str:
    """An inline figure with its colours as tokens: paint attributes move into `style`, the bundler's data-dc-tpl markers go."""
    def tag(m):
        attrs = m.group("attrs")
        if not attrs.strip():
            return m.group(0)
        style, kept = [], []
        existing = ""
        for a in _ATTR.finditer(attrs):
            k, v = a.group("k"), a.group("v")
            if k == "data-dc-tpl":
                continue
            if k in _PAINT and _HEX.fullmatch(v.strip()) and token_for(v.strip()):
                style.append(f"{k}:{token_for(v.strip())}")
            elif k == "style":
                existing = tokenise_css(v).strip().rstrip(";")
            else:
                kept.append(f'{k}="{v}"')
        css = "; ".join(p for p in ([existing] if existing else []) + style)
        parts = kept + ([f'style="{css}"'] if css else [])
        return "<" + m.group("name") + (" " + " ".join(parts) if parts else "") + m.group("end") + ">"
    return tokenise_css(_TAG.sub(tag, svg))          # then any colour left in a <style> element or a style attribute


def links(html: str) -> str:
    """The snapshot's relative page links, as the live routes."""
    return re.sub(r'href="([^"#]*)(#[^"]*)?"', lambda m: f'href="{LINKS.get(m.group(1), m.group(1))}{m.group(2) or ""}"', html or "")


# Heads of a table whose first column names the row (a kind, an agent, a concern): one label style for every row, never bold.
LABEL_HEADS = ("agent", "kind", "concern", "shape", "status", "")
WIDE_COLUMNS = 4
WIDE_CELL_WORDS = 40
_CODE = re.compile(r"(<code\b[^>]*>)(.*?)(</code>)", re.S)
_BREAK_AFTER = re.compile(r"([/_.(])(?=[^<\s&])")
_WORDS = re.compile(r"<[^>]+>")


def break_code(html: str) -> str:
    """Words never break mid-word in a table; a long identifier may break only after / _ . or ( , where a <wbr> allows it."""
    return _CODE.sub(lambda m: m.group(1) + _BREAK_AFTER.sub(r"\1<wbr>", m.group(2)) + m.group(3), html or "")


def is_label_table(head: list) -> bool:
    return bool(head) and re.sub(r"<[^>]+>", "", str(head[0])).strip().lower() in LABEL_HEADS


def is_wide_table(head: list, rows: list) -> bool:
    """A table of 4 or more columns, or with a cell over 40 words, keeps its full column and scrolls on a narrow screen."""
    longest = max((len(_WORDS.sub(" ", str(c)).split()) for r in rows for c in r), default=0)
    return len(head) >= WIDE_COLUMNS or longest > WIDE_CELL_WORDS


def fill(page, tokens: dict):
    """A copy of a page's content with `{token}` placeholders replaced in every string: the numbers a page takes from the database at request time.

    Content files are cached and shared, so this never changes the original; a token with no value is replaced by the empty string.
    """
    def go(obj):
        if isinstance(obj, str):
            for key, value in tokens.items():
                obj = obj.replace("{" + key + "}", value)
            return obj
        if isinstance(obj, list):
            return [go(x) for x in obj]
        if isinstance(obj, dict):
            return {k: go(v) for k, v in obj.items()}
        return obj
    return go(page)


def _walk(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk(x, fn) for x in obj]
    if isinstance(obj, dict):
        # a structured link (`"href": "Weekly-Digest.html"`) is mapped as a whole; any other string through fn
        return {k: (v if k.startswith("_") else (LINKS.get(v, v) if k == "href" and isinstance(v, str) else _walk(v, fn))) for k, v in obj.items()}
    return obj


def _prepare(data: dict) -> dict:
    return _walk(data, links)


@lru_cache(maxsize=1)
def product() -> dict:
    d = _prepare(json.loads(PRODUCT_JSON.read_text(encoding="utf-8")))
    for s in d["sections"]:
        fig = s.get("figure")
        if fig and fig.get("svg"):
            fig["svg"] = tokenise_svg(fig["svg"])
        for n, step in enumerate(s.get("steps") or [], start=1):
            path = FIGURES / f"product-step-{n}.svg"
            step["svg"] = tokenise_svg(path.read_text(encoding="utf-8")) if path.exists() else ""
    sprite = FIGURES / "product-sprite.svg"
    d["sprite"] = tokenise_svg(sprite.read_text(encoding="utf-8")) if sprite.exists() else ""       # the icons and arrowheads the step figures reference
    return d


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>|&\w+;", " ", text).lower()).strip("-")


@lru_cache(maxsize=1)
def system_design() -> dict:
    d = _prepare(json.loads(SYSTEM_JSON.read_text(encoding="utf-8")))
    toc = []
    for number, section in enumerate(d["sections"], start=1):
        section["num"] = f"{number:02d}"
        subs = []
        for b in section["blocks"]:
            t = b.get("t")
            if t == "h3":
                b["anchor"] = b.get("id") or f"{section['id']}-{_slug(b['title'])}"
                subs.append({"id": b["anchor"], "title": b["title"]})
            elif t == "h4":
                b["anchor"] = b.get("id") or ""
            elif t == "figureSpec":
                path = FIGURES / f"system-{b['id']}.svg"
                b["svg"] = tokenise_svg(path.read_text(encoding="utf-8")) if path.exists() else ""
            elif t == "figure":
                b["svg"] = tokenise_svg(b["svg"])
            elif t == "figureHtml":
                b["html"] = tokenise_css(b["html"])
            elif t == "table":
                b["label"], b["wide"] = is_label_table(b["head"]), is_wide_table(b["head"], b["rows"])
                b["rows"] = [[break_code(c) for c in row] for row in b["rows"]]
        toc.append({"id": section["id"], "num": section["num"], "title": section["title"], "subs": subs})
    d["toc"] = toc
    return d
