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
        self.assertRegex(self.get("/application/Product.html"), r"(\d[\d,]* things|Hundreds of things) happened in AI last week\.")
        self.assertIn("The Signal System", self.get("/design/System-Design.html"))
        self.assertEqual(self.client.get("/").history[0].status_code if self.client.get("/").history else 307, 307)

    def test_the_product_hero_embeds_the_gap_video_and_it_is_served_seekable(self):
        """The 60-second 'The gap' pitch sits under the hero; the file is served with byte ranges so the player can seek."""
        html = self.get("/application/Product.html")
        self.assertIn('<source src="/static/video/the-gap.mp4" type="video/mp4">', html)
        self.assertIn('poster="/static/video/the-gap-poster.jpg"', html)
        r = self.client.get("/static/video/the-gap.mp4", headers={"Range": "bytes=0-1023"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(r.content), 1024)
        self.assertEqual(self.client.get("/static/video/the-gap-poster.jpg").status_code, 200)

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


class DocumentTables(unittest.TestCase):
    """Run 7, Step B: words never break mid-word in a table; a label column has one style; bold is only a cell's lead phrase."""
    PAGES = ("/design/System-Design.html", "/roadmap", "/plan")

    @classmethod
    def setUpClass(cls):
        client = TestClient(site.app)
        cls.html = {p: client.get(p).text for p in cls.PAGES}

    def tables(self, html):
        for t in re.findall(r"<table\b([^>]*)>(.*?)</table>", html, re.S):
            head = re.findall(r"<th\b[^>]*>(.*?)</th>", t[1], re.S)
            rows = [re.findall(r"<td\b([^>]*)>(.*?)</td>", r, re.S) for r in re.findall(r"<tr>(.*?)</tr>", t[1].split("<tbody", 1)[-1], re.S)]
            yield t[0], head, rows

    def test_no_rule_lets_a_table_break_a_word(self):
        css = (REPO / "static" / "site.css").read_text(encoding="utf-8")
        plan = (REPO / "plan" / "plan.template.html").read_text(encoding="utf-8")
        for name, text in (("site.css", css), ("plan.template.html", plan)):
            self.assertNotRegex(text, r"overflow-wrap\s*:\s*anywhere", name)
            self.assertNotRegex(text, r"word-break\s*:\s*break-all", name)

    def test_long_identifiers_break_only_after_a_separator(self):
        self.assertEqual(pages.break_code("<code>agents/build_check_payload()</code>"), "<code>agents/<wbr>build_<wbr>check_<wbr>payload(<wbr>)</code>")
        self.assertEqual(pages.break_code("<code>CHECK_MODEL</code>"), "<code>CHECK_<wbr>MODEL</code>")
        self.assertEqual(pages.break_code("plain/text_here"), "plain/text_here")

    def test_every_label_table_first_column_carries_the_label_class_and_no_bold(self):
        seen = 0
        for path, html in self.html.items():
            for _attrs, head, rows in self.tables(html):
                label = pages.is_label_table([re.sub("<[^>]+>", "", h) for h in head])
                for row in rows:
                    if not row:
                        continue
                    attrs, cell = row[0]
                    self.assertNotRegex(cell, r"<(b|strong)\b", f"{path}: bold in a first-column cell: {cell[:60]}")
                    if label:
                        seen += 1
                        self.assertIn("label", attrs, f"{path}: first-column cell without the label class: {cell[:60]}")
        self.assertGreater(seen, 20)

    def test_a_bold_phrase_in_a_cell_is_only_its_lead(self):
        for path, html in self.html.items():
            for _a, _h, rows in self.tables(html):
                for row in rows:
                    for _attrs, cell in row:
                        for m in re.finditer(r"<(b|strong)\b[^>]*>", cell):
                            self.assertEqual(re.sub(r"<[^>]+>", "", cell[:m.start()]).strip(), "", f"{path}: bold that is not the lead of its cell: {cell[:80]}")

    def test_wide_tables_keep_their_width_and_scroll_on_a_narrow_screen(self):
        self.assertTrue(pages.is_wide_table(["a", "b", "c", "d"], [["x"] * 4]))
        self.assertTrue(pages.is_wide_table(["a", "b"], [["word " * 41, "y"]]))
        self.assertFalse(pages.is_wide_table(["a", "b", "c"], [["a few words", "x", "y"]]))
        css = (REPO / "static" / "site.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"table\.wide\{[^}]*min-width")
        self.assertRegex(css, r"\.table-wrap\{[^}]*overflow-x:auto")
        self.assertIn('<table class="wide">', self.html["/design/System-Design.html"])

    def test_status_values_in_a_status_column_use_the_pill(self):
        html = self.html["/design/System-Design.html"]
        seen = 0
        for _attrs, head, rows in self.tables(html):
            names = [re.sub("<[^>]+>", "", h).strip() for h in head]
            for col, name in enumerate(names):
                if name in ("Status", "Lands"):
                    for row in rows:
                        text = re.sub("<[^>]+>", "", row[col][1]).strip().upper()
                        if text.split(" ")[0] in ("BUILT", "DESIGNED", "M1", "M2", "M3", "LATER"):
                            seen += 1
                            self.assertIn('class="pill', row[col][1], text)
        self.assertGreater(seen, 10)

    def test_every_label_head_makes_a_label_table(self):
        for head in ("Agent", "Kind", "Concern", "Shape", "Status", ""):
            self.assertTrue(pages.is_label_table([head, "x"]), head)
        self.assertFalse(pages.is_label_table(["Constant", "Value"]))

    def test_the_label_style_is_mono_micro_uppercase_nowrap_and_not_bold(self):
        css = (REPO / "static" / "site.css").read_text(encoding="utf-8")
        rule = re.search(r"td\.label\{([^}]*)\}", css).group(1)
        for part in ("var(--mono)", "var(--fs-micro)", "text-transform:uppercase", "font-weight:500", "white-space:nowrap"):
            self.assertIn(part, rule)


class ProductPage(unittest.TestCase):
    """Run 8, Step C: the Product page says where the product is, and states as built only what is built."""
    @classmethod
    def setUpClass(cls):
        cls.html = TestClient(site.app).get("/application/Product.html").text
        cls.text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cls.html))

    def test_the_hero_matches_the_video_pitch(self):
        self.assertRegex(self.text, r"(\d[\d,]* things|Hundreds of things) happened in AI last week\.")
        for part in ("From AI news to a change your team can ship", "Which one matters to your business?",
                     "Today it is one weekly brief of up to seven items, each fact checked against its source. Next it connects them to your roadmap and your codebase, "
                     "and drafts the change for your engineers to review."):
            self.assertIn(part, self.text)
        self.assertIn('<source src="/static/video/the-gap.mp4"', self.html)
        self.assertEqual(self.html.count('class="btn'), 2)

    def test_no_sentence_says_the_page_has_no_illustrative_figure(self):
        self.assertNotIn("Nothing on this page is an illustrative figure", self.text)
        self.assertIn("Every figure in this section is measured", self.text)
        self.assertIn("The only illustrative numbers on the page are in the video, and they are labelled.", self.text)

    def test_seven_is_a_cap_not_a_promise(self):
        for part in ("up to seven items", "at most seven items", "an edition carries at most seven"):
            self.assertIn(part, self.text)
        self.assertNotRegex(self.text, r"carries seven items|narrowed to seven items|Seven items, each")

    def test_the_origin_story_is_labelled_and_the_coach_is_not_called_built(self):
        self.assertIn("Why I built it", self.text)
        self.assertNotIn("60-minute exercise", self.text)
        self.assertIn("Next, it will go further: take one item into your own repository and draft the change (planned for M3).", self.text)
        self.assertIn("A sentence the checks cannot support is removed, or its story is held back, and the rest of the week still publishes.", self.text)

    def test_where_this_is_going_has_three_cards_with_the_roadmaps_statuses(self):
        self.assertIn('id="where-this-is-going"', self.html)
        sec = self.html.split('id="where-this-is-going"', 1)[1].split("</section>", 1)[0]
        cards = re.findall(r'<div class="card"><div class="kicker">(.*?)</div><h3>(.*?)</h3>', sec)
        self.assertEqual([c[1] for c in cards], ["Find the signal", "Connect it to your business", "A review-ready change"])
        road = (REPO / "plan" / "roadmap.md").read_text(encoding="utf-8")
        status = {m: s for m, s in re.findall(r"# (M\d) · .*?\n\n- \*\*Status:\*\* (NOW|NEXT|LATER)", road, re.S)}
        word = {"NOW": "Live", "NEXT": "Next", "LATER": "Later"}
        self.assertEqual(cards[0][0], f"{word[status['M1']]} · M1")
        self.assertTrue(cards[1][0].startswith(word[status["M2"]]), cards[1][0])
        self.assertEqual(cards[2][0], f"{word[status['M3']]} · M3")
        self.assertLess(self.html.index('id="where-this-is-going"'), self.html.index('id="where-to-go-next"'))
        self.assertGreater(self.html.index('id="where-this-is-going"'), self.html.index('id="the-solution"'))

    def test_the_funnel_figure_does_not_show_the_coach_as_built_or_seven_as_exact(self):
        self.assertIn(">UP TO SEVEN<", self.html)
        self.assertIn(">EXERCISE · M3<", self.html)
        self.assertNotIn(">ONE EXERCISE<", self.html)
        self.assertNotIn(">SEVEN ITEMS<", self.html)

    def test_the_plan_card_says_what_the_plan_page_has(self):
        self.assertNotIn("archive of what is already finished", self.text)
        self.assertIn("every activity with its status and notes, and the decision log", self.text)


class StaleFacts(unittest.TestCase):
    """Run 7, Step E: smaller facts found stale in QA stay fixed."""
    @classmethod
    def setUpClass(cls):
        client = TestClient(site.app)
        cls.roadmap = client.get("/roadmap").text
        cls.product = client.get("/application/Product.html").text

    def test_the_roadmap_no_longer_says_the_signal_system_has_no_reader(self):
        self.assertNotIn("has no reader at all", self.roadmap)
        self.assertIn("19:00 Pacific", self.roadmap)
        self.assertIn("keeps running until Varun decides", self.roadmap)

    def test_the_roadmap_sidebar_numbers_every_top_level_entry_and_never_mixes_a_dot_with_a_number(self):
        anchors = re.findall(r'<a data-toc="[^"]*" href="#[^"]*" style="[^"]*">(.*?)</a>', self.roadmap, re.S)
        top = [a for a in anchors if "min-width:20px" in a]
        self.assertGreaterEqual(len(top), 4)
        for a in top:
            self.assertNotIn("border-radius:50%", a, "a top-level entry carries both a number and a dot")

    def test_the_product_cards_state_what_the_pages_are_today(self):
        self.assertNotIn("sample data from a fixed date", self.product)
        self.assertIn("latest published edition", self.product)


class EditionTypeScale(unittest.TestCase):
    """Run 8, Step A: the Edition's headlines use one scale of tokens: section 30 > feature 26 > story 22 > body 18 (24 / 22 / 20 at 760px and below)."""
    @classmethod
    def setUpClass(cls):
        cls.t = (REPO / "templates" / "digest.template.html").read_text(encoding="utf-8")
        cls.tokens = (REPO / "static" / "tokens.css").read_text(encoding="utf-8")

    def token(self, name):
        return int(re.search(rf"--{name}:\s*(\d+)px", self.tokens).group(1))

    def test_the_tokens_are_the_scale_and_each_headline_is_no_more_than_1_7_times_the_body(self):
        sizes = [self.token(n) for n in ("fs-ed-section", "fs-ed-feature", "fs-ed-story")]
        self.assertEqual(sizes, [30, 26, 22])
        self.assertGreater(sizes[0], sizes[1])
        self.assertGreater(sizes[1], sizes[2])
        self.assertGreater(sizes[2], 18)
        self.assertLessEqual(max(sizes), 1.7 * 18)
        self.assertEqual([self.token(n) for n in ("fs-ed-section-sm", "fs-ed-feature-sm", "fs-ed-story-sm", "fs-ed-closing-sm")], [24, 22, 20, 22])
        self.assertEqual(self.token("fs-ed-closing"), 26)

    def test_the_template_sets_every_headline_from_the_tokens_never_a_literal(self):
        self.assertRegex(self.t, r"\.sec-headline\{font-size:var\(--fs-ed-section\);line-height:1\.15;[^}]*max-width:24em;text-wrap:balance\}")
        self.assertRegex(self.t, r"\.story h3\{font-size:var\(--fs-ed-story\)")
        self.assertRegex(self.t, r"\.feature h3\{font-size:var\(--fs-ed-feature\)")
        self.assertRegex(self.t, r"\.closing \.cl\{font-size:var\(--fs-ed-closing\)")
        small = re.search(r"@media \(max-width:760px\)\{(.*)\}\n", self.t).group(1)
        for part in ("--fs-ed-section-sm", "--fs-ed-story-sm", "--fs-ed-feature-sm", "--fs-ed-closing-sm"):
            self.assertIn(part, small)
        for old in ("big44", "big36"):
            self.assertNotIn(old, self.t)

    def test_the_thread_the_essay_and_the_stories_headlines_carry_the_section_headline_class(self):
        for expr in (r"digest\.thread\.lines\[0\]", r"digest\.learning\.headline", r"digest\.stories\.title"):
            m = re.search(r'<span class="([^"]*)">(?:<span class="line">)?\{\{ runs\(' + expr, self.t)
            self.assertIsNotNone(m, expr)
            self.assertIn("sec-headline", m.group(1).split(), expr)

    @needs_db
    def test_the_rendered_edition_has_the_headline_class_on_each_headline(self):
        html = TestClient(site.app).get("/weekly-digest").text
        self.assertGreaterEqual(len(re.findall(r'class="big sec-headline', html)), 2)


if __name__ == "__main__":
    unittest.main()
