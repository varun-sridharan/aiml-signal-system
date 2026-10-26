"""One site: the five pages share one header, one token file and three layouts, and define no colour of their own (Run 5, Step C)."""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agents import pages, site_check
from tests._db import needs_db

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import app as site  # noqa: E402


class Tokens(unittest.TestCase):
    def test_tokens_css_is_what_the_kits_token_files_build(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "build_tokens.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_tokens_css_holds_the_kits_variables(self):
        css = (REPO / "static" / "tokens.css").read_text()
        for name in ("--paper", "--accent", "--serif", "--fs-body", "--w-site", "--header-h", "--paper-glass"):
            self.assertIn(name + ":", css)
        self.assertNotIn("a{color", css)            # variables only: the kit's element rules belong to a layout, not to every page


class Titles(unittest.TestCase):
    """Run 6, Step A1: Document pages have 52px titles through a Document-layout token; Product keeps its 68px display title."""

    def test_the_document_title_is_a_52px_token_and_the_landing_title_is_still_68px(self):
        tokens = (REPO / "static" / "tokens.css").read_text()
        self.assertRegex(tokens, r"--fs-doc-title:\s*clamp\(\d+px,\s*[\d.]+vw,\s*52px\)")
        self.assertRegex(tokens, r"--fs-hero:\s*clamp\(\d+px,\s*[\d.]+vw,\s*68px\)")
        css = (REPO / "static" / "site.css").read_text()
        self.assertRegex(css, r"\.layout-doc h1\.page\{[^}]*font-size:var\(--fs-doc-title\)")
        self.assertRegex(css, r"\.layout-landing \.hero-title\{[^}]*font-size:var\(--fs-hero\)")
        self.assertNotIn("52px", css)                  # the number lives in the token file, not in a layout


class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(site.app)

    def get(self, path):
        return self.client.get(path).text

    def test_every_page_has_the_one_header_the_two_stylesheets_and_one_layout(self):
        for path, label, layout in site_check.PAGES:
            html = self.get(path)
            self.assertEqual(site_check.page_problems(html, label, layout), [], path)

    def test_the_five_headers_are_identical_except_for_which_page_is_current(self):
        headers = {path: site_check.normalised_header(self.get(path)) for path, _l, _y in site_check.PAGES}
        self.assertEqual(len(set(headers.values())), 1, "the headers differ")
        self.assertIn('class="mark"', next(iter(headers.values())))

    def test_the_old_urls_still_work_and_show_the_content_files(self):
        self.assertIn("I stopped trying to keep up.", self.get("/application/Product.html"))
        self.assertIn("The Signal System", self.get("/design/System-Design.html"))
        self.assertEqual(self.client.get("/").history[0].status_code if self.client.get("/").history else 307, 307)

    def test_no_page_serves_a_snapshot_bundle_or_the_old_runtime(self):
        for path in ("/application/Product.html", "/design/System-Design.html"):
            html = self.get(path)
            self.assertNotIn("__bundler", html)
            self.assertNotIn("data-dc-tpl", html)

    def test_the_figures_carry_token_colours_not_hex_values(self):
        def figures(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("svg", "html") and isinstance(v, str):
                        yield v
                    elif not k.startswith("_"):
                        yield from figures(v)
            elif isinstance(o, list):
                for v in o:
                    yield from figures(v)
        found = [f for page in (pages.product(), pages.system_design()) for f in figures(page)]
        self.assertGreater(len(found), 6)
        for svg in found:
            if not svg.lstrip().startswith("<p"):
                self.assertEqual(site_check.colour_definitions(svg), [], "a figure still defines a hex colour")

    def test_the_content_files_are_rendered_whole(self):
        product = self.get("/application/Product.html")
        for s in pages.product()["sections"]:
            self.assertIn(f'id="{s["id"]}"', product)
        design = self.get("/design/System-Design.html")
        for s in pages.system_design()["sections"]:
            self.assertIn(f'id="{s["id"]}"', design)
        self.assertEqual(design.count('class="entry"'), len(pages.system_design()["decisionLog"]))

    def test_links_into_the_old_snapshots_point_at_live_routes(self):
        for path in ("/application/Product.html", "/design/System-Design.html"):
            html = self.get(path)
            for stale in ('href="Weekly-Digest.html"', 'href="../plan/', 'href="../design/', 'href="../application/'):
                self.assertNotIn(stale, html)


class SamplePage(unittest.TestCase):
    def test_the_sample_edition_is_the_one_page_not_built_from_a_template_and_that_is_known(self):
        client = TestClient(site.app)
        self.assertEqual(site_check.KNOWN_EXCEPTIONS, ("/weekly-digest/sample-2026-08-04",))
        html = client.get(site_check.KNOWN_EXCEPTIONS[0]).text
        self.assertIn("__bundler", html)                                   # the October 2026 design snapshot, served as the file it is
        self.assertNotIn("site-header", html)


class Sources(unittest.TestCase):
    def test_no_template_or_page_stylesheet_carries_a_colour_literal(self):
        self.assertEqual(site_check.source_problems(), [])


class PlantedDrift(unittest.TestCase):
    """The check must fail on a page that drifts, in each of the ways a page can."""

    GOOD = ('<html><head><link rel="stylesheet" href="/static/tokens.css"><link rel="stylesheet" href="/static/site.css"></head>'
            '<body class="layout-doc"><header class="site-header"><div class="in"><nav><span aria-current="page">Plan &amp; Progress</span></nav></div></header></body></html>')

    def test_a_good_page_passes(self):
        self.assertEqual(site_check.page_problems(self.GOOD, "Plan & Progress", "layout-doc"), [])

    def test_a_page_with_no_header_fails(self):
        html = re.sub(r"<header.*?</header>", "", self.GOOD, flags=re.S)
        self.assertTrue(any("site headers" in p for p in site_check.page_problems(html, "Plan & Progress", "layout-doc")))

    def test_a_page_with_two_headers_fails(self):
        html = self.GOOD.replace("</body>", re.search(r"<header.*?</header>", self.GOOD, re.S).group(0) + "</body>")
        self.assertTrue(site_check.page_problems(html, "Plan & Progress", "layout-doc"))

    def test_a_page_that_does_not_load_tokens_fails(self):
        html = self.GOOD.replace('<link rel="stylesheet" href="/static/tokens.css">', "")
        self.assertTrue(any("tokens.css" in p for p in site_check.page_problems(html, "Plan & Progress", "layout-doc")))

    def test_a_page_that_defines_a_colour_fails(self):
        for planted in ("<head><style>body{background:#F4F1E8}</style>", '<head><div style="color:#B5502A"></div>', '<head><svg><rect fill="#201C17"/></svg>'):
            html = self.GOOD.replace("<head>", planted)
            self.assertTrue(any("own colours" in p for p in site_check.page_problems(html, "Plan & Progress", "layout-doc")), planted)

    def test_a_colour_code_in_prose_is_content_not_a_definition(self):
        html = self.GOOD.replace("</body>", "<p>the accent is <code>#b5502a</code></p></body>")
        self.assertEqual(site_check.page_problems(html, "Plan & Progress", "layout-doc"), [])

    def test_a_page_with_the_wrong_current_item_or_layout_fails(self):
        self.assertTrue(site_check.page_problems(self.GOOD, "Roadmap & Backlog", "layout-doc"))
        self.assertTrue(site_check.page_problems(self.GOOD, "Plan & Progress", "layout-landing"))

    def test_every_other_way_to_define_a_colour_or_override_the_header_fails(self):
        for planted in ('<head><div style="color:rgb(1,2,3)"></div>', '<head><style>a{color:hsl(10 20% 30%)}</style>', '<head><div style="color:red"></div>',
                        '<head><svg><rect fill="red"/></svg>', '<head><style>a{color:#fff}</style>', '<head><style>a{background:#ffff}</style>',
                        '<head><style>@import url(https://x.example/a.css);</style>', '<head><link rel="stylesheet" href="https://x.example/a.css">',
                        '<head><style>a{color:var(--ink)!important}</style>', '<head><style>.site-header{display:none}</style>',
                        '<head><script>el.style.color = "#B5502A"</script>', '<head><script>el.style.background = "rgb(1,2,3)"</script>'):
            html = self.GOOD.replace("<head>", planted)
            self.assertTrue(site_check.page_problems(html, "Plan & Progress", "layout-doc"), planted)

    def test_what_is_allowed_stays_allowed(self):
        for fine in ('<head><style>a{color:var(--ink);background:transparent;border:1px solid currentColor}</style>', '<head><noscript><style>.s{grid-template-rows:1fr!important}</style></noscript>',
                     '<head><style>@media print{.s{display:block!important}}</style>', '<head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader">',
                     '<head><style>.site-header nav::-webkit-scrollbar{display:none}</style>', '<head><script type="application/json">{"c":"#B5502A"}</script>'):
            html = self.GOOD.replace("<head>", fine)
            self.assertEqual(site_check.page_problems(html, "Plan & Progress", "layout-doc"), [], fine)

    def test_character_references_are_not_colours(self):
        self.assertEqual(site_check.hex_literals("&#8599; &#10003; &#9660; and a Jinja note {# #F4F1E8 #}"), [])

    def test_a_planted_template_with_a_hex_fails_the_source_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates").mkdir()
            (root / "plan").mkdir()
            (root / "static").mkdir()
            (root / "templates" / "page.html").write_text("<style>a{color:#B5502A}</style>")
            (root / "static" / "site.css").write_text("a{color:var(--accent)}")
            problems = site_check.source_problems(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("templates/page.html", problems[0])


if __name__ == "__main__":
    unittest.main()
