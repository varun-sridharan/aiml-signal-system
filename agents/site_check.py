"""The drift check for the five site pages (Run 5, Step C, bk-tokens-css): one header, one token file, three layouts, no colour of a page's own.

A page passes when its HTML has the one shared site header, loads /static/tokens.css and /static/site.css, uses one of the three layouts, and
defines no colour of its own: no hex of any length, colour function, named colour, @import, !important, stylesheet of its own, rule that hides
the header, or inline script that sets a colour. The sources are checked the same way, so a colour can only come from design/system/tokens/.
Pure functions over text; tests/test_site.py runs them on the real pages and on planted ones.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PAGES = (("/application/Product.html", "Product", "layout-landing"),
         ("/weekly-digest", "Weekly Digest", "layout-edition"),
         ("/design/System-Design.html", "System Design", "layout-doc"),
         ("/roadmap", "Roadmap & Backlog", "layout-doc"),
         ("/plan", "Plan & Progress", "layout-doc"))
LAYOUTS = ("layout-landing", "layout-doc", "layout-edition")
# The one other page that is not built from a template: the sample edition, kept as the October 2026 design snapshot and served as the file it is.
KNOWN_EXCEPTIONS = ("/weekly-digest/sample-2026-08-04",)

# A colour hex of six or eight digits, not an HTML character reference (&#8599;) and not part of a word.
HEX = re.compile(r"(?<![&\w])#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})\b(?!;)")
# In a style context (CSS or a paint attribute) a hex of any length is a colour: #fff and #ffff count.
_HEX_ANY = re.compile(r"(?<![&\w])#(?:[0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})\b(?!;)")
_COLOUR_FN = re.compile(r"\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color|color-mix)\(", re.I)
NAMED = ("red", "green", "blue", "black", "white", "gray", "grey", "orange", "yellow", "purple", "pink", "brown", "cyan", "magenta", "navy", "teal",
         "maroon", "olive", "lime", "aqua", "silver", "gold", "crimson", "coral", "salmon", "tomato", "indigo", "violet", "beige", "ivory", "khaki",
         "tan", "plum")
_NAMED = re.compile(r"(?:^|[;{\s])(?:color|background(?:-color)?|border(?:-[a-z]+)*|fill|stroke|outline(?:-color)?|stop-color)\s*[:=]\s*[^;}\"']*?(?<![\w-])(?:"
                    + "|".join(NAMED) + r")(?![\w-])", re.I)
_IMPORT = re.compile(r"@import\b", re.I)
_IMPORTANT = re.compile(r"(?:color|background|fill|stroke|display|visibility|opacity)[^;{}]*!\s*important", re.I)     # an override of a colour or of visibility
_HIDE_HEADER = re.compile(r"(?:^|[},\s])\.site-header\s*(?:,|\{)[^{}]*?(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0\b)", re.I)
_PRINT_BLOCK = re.compile(r"@media\s+print\s*\{(?:[^{}]|\{[^{}]*\})*\}", re.I)     # print styles may force things open with !important
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_LINE_COMMENT = re.compile(r"(?m)^\s*//.*$")

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HEADER = re.compile(r"<header class=\"site-header\">.*?</header>", re.S)
_CURRENT = re.compile(r"<span aria-current=\"page\">(.*?)</span>", re.S)
_NOSCRIPT = re.compile(r"<noscript\b[^>]*>.*?</noscript>", re.S | re.I)      # the no-JavaScript fallback may force sections open with !important
_STYLE_ELEMENT = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_STYLE_ATTR = re.compile(r"\bstyle=\"([^\"]*)\"|\bstyle='([^']*)'", re.S)
_PAINT_ATTR = re.compile(r"\b(?:fill|stroke|stop-color|flood-color|color|bgcolor)=[\"']([^\"']*)[\"']")
_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>(.*?)</script>", re.S | re.I)
_STYLESHEET = re.compile(r"<link\b[^>]*\brel=[\"']stylesheet[\"'][^>]*>", re.I)
ALLOWED_SHEETS = ("/static/tokens.css", "/static/site.css", "https://fonts.googleapis.com/")


def hex_literals(text: str) -> list:
    """Colour hexes (6 or 8 digits) in a source file, ignoring Jinja comments and character references."""
    return sorted(set(HEX.findall(_JINJA_COMMENT.sub("", text))))


def drift_in_css(text: str) -> list:
    """Every way a stretch of CSS or JavaScript can define a colour of its own, or override the shared files."""
    text = _PRINT_BLOCK.sub("", _CSS_COMMENT.sub("", _JS_LINE_COMMENT.sub("", _JINJA_COMMENT.sub("", text))))
    out = []
    if _HEX_ANY.search(text):
        out.append("a hex colour " + ", ".join(sorted(set(_HEX_ANY.findall(text)))[:4]))
    if _COLOUR_FN.search(text):
        out.append("a colour function (rgb, hsl, ...)")
    if _NAMED.search(text):
        out.append("a named colour")
    if _IMPORT.search(text):
        out.append("an @import")
    if _IMPORTANT.search(text):
        out.append("an !important on a colour or on visibility")
    if _HIDE_HEADER.search(text):
        out.append("a rule that hides the site header")
    return out


def header_html(html: str):
    m = _HEADER.search(html)
    return m.group(0) if m else None


def normalised_header(html: str):
    """The header with its current-page marker and every nav link made alike, so headers of different pages can be compared exactly."""
    head = header_html(html)
    if head is None:
        return None
    head = _CURRENT.sub(lambda m: f"<a href=\"ITEM\">{m.group(1)}</a>", head)
    return re.sub(r"(<nav[^>]*>.*?</nav>)", lambda m: re.sub(r'<a href="[^"]*">', '<a href="ITEM">', m.group(1)), head, flags=re.S)


def colour_definitions(html: str) -> list:
    """What a rendered page defines for itself in its <style> elements, style attributes, paint attributes and inline scripts. Text is not a
    definition: a decision-log entry that mentions a colour code is content."""
    found = []
    html = _NOSCRIPT.sub("", html)
    for m in _STYLE_ELEMENT.findall(html):
        found += drift_in_css(m)
    for a, b in _STYLE_ATTR.findall(html):
        found += drift_in_css(a or b)
    for v in _PAINT_ATTR.findall(html):
        found += drift_in_css("fill:" + v)
    for body in _SCRIPT.findall(html):
        found += [f"in an inline script: {p}" for p in drift_in_css(body)]
    return sorted(set(found))


def page_problems(html: str, nav_label: str, layout: str) -> list:
    """What is wrong with one rendered page, as plain sentences. Empty means it passes."""
    out = []
    headers = _HEADER.findall(html)
    if len(headers) != 1:
        out.append(f"{len(headers)} site headers, exactly 1 is required")
    else:
        current = _CURRENT.findall(headers[0])
        if current != [nav_label.replace("&", "&amp;")]:
            out.append(f"the header's current page is {current}, expected {nav_label!r}")
    for sheet in ("/static/tokens.css", "/static/site.css"):
        if f'href="{sheet}"' not in html:
            out.append(f"does not load {sheet}")
    classes = re.search(r"<body class=\"([^\"]*)\"", html)
    body = (classes.group(1).split() if classes else [])
    if [c for c in body if c in LAYOUTS] != [layout]:
        out.append(f"body layout is {body}, expected {layout}")
    drift = colour_definitions(html)
    if drift:
        out.append(f"defines its own colours or overrides: {'; '.join(drift[:5])}")
    for link in _STYLESHEET.findall(html):
        href = re.search(r"href=[\"']([^\"']*)", link)
        if not href or not href.group(1).startswith(ALLOWED_SHEETS):
            out.append(f"loads a stylesheet of its own: {href.group(1) if href else link[:60]}")
    return out


def source_files(root: Path = REPO) -> list:
    """Every file that styles a page: the templates, the two page templates beside their markdown, the shared and page stylesheets and scripts."""
    files = sorted((root / "templates").glob("*.html")) + sorted((root / "plan").glob("*.template.html"))
    files += [root / "static" / "site.css"] + sorted((root / "static").glob("*.js"))
    return [f for f in files if f.exists()]


def source_problems(root: Path = REPO) -> list:
    out = []
    for f in source_files(root):
        text = f.read_text(encoding="utf-8")
        if f.suffix in (".css", ".js"):
            found = drift_in_css(text)
        else:
            plain = _NOSCRIPT.sub("", _JINJA_COMMENT.sub("", text))
            found = drift_in_css("\n".join(_STYLE_ELEMENT.findall(plain) + [a or b for a, b in _STYLE_ATTR.findall(plain)] + _PAINT_ATTR.findall(plain)))
            found += [f"in an inline script: {p}" for body in _SCRIPT.findall(plain) for p in drift_in_css(body)]
        if found:
            out.append(f"{f.relative_to(root)} carries its own colours or overrides: {'; '.join(sorted(set(found))[:4])}")
    return out
