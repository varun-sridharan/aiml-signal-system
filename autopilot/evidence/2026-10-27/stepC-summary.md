# Step C · one site: the five pages before and after

Measured with `scripts/site_measure.py` (headless Chrome, 1440px wide; the live site at sha 39e2db3 for before, the new build for after). Screenshots of each page's top are in `stepC-before-screens/` and `stepC-after/` (and `stepC-after-390/` at phone width).

| page | before: wordmark | header | nav | body | h1 |
|---|---|---|---|---|---|
| Product | 17px Newsreader 600 at x=163 | sticky 58px | 13px Public Sans | 17px Public Sans | 68px |
| Weekly Digest | 17px Newsreader 600 at x=328 | fixed 44px | 13px Newsreader | 16px Newsreader | 24px |
| System Design | 15px Newsreader 600 at x=328 | fixed 52px | 13px Public Sans | 15px Public Sans | 52px |
| Roadmap | 15px Newsreader 600 at x=328 | fixed 51px | 13px Public Sans | 15px Public Sans | 52px |
| Plan | 17px Newsreader 600 at x=328 | fixed 55px | 13px Public Sans | 17px Public Sans | 68px |

| page | after: wordmark | header | nav | body | h1 |
|---|---|---|---|---|---|
| Product | 17px Newsreader 600 at x=163 | sticky 58px | 13px Public Sans | 17px Public Sans | 68px |
| Weekly Digest | 17px Newsreader 600 at x=163 | sticky 58px | 13px Public Sans | 18px Newsreader | 24px |
| System Design | 17px Newsreader 600 at x=163 | sticky 58px | 13px Public Sans | 17px Public Sans | 68px |
| Roadmap | 17px Newsreader 600 at x=163 | sticky 58px | 13px Public Sans | 17px Public Sans | 68px |
| Plan | 17px Newsreader 600 at x=163 | sticky 58px | 13px Public Sans | 17px Public Sans | 68px |

**All five headers now match exactly:** sticky, 58px tall, wordmark 17px Newsreader 600 at the same x, nav 13px Public Sans. Before: wordmark 15 or 17px, nav in Newsreader on the Weekly Digest, headers 44, 51, 52, 55 and 58px, fixed or sticky, offset 0 or 288px. At 390px wide all five are 50px tall and the nav scrolls sideways instead of wrapping.
Body and H1 follow the layout: Landing and Document 17px Public Sans, H1 68px (40px under 900px, from `--fs-hero`); Edition 18px Newsreader (the reading scale).

## What changed besides the header, and what is disclosed

- `static/tokens.css` is built from `design/system/tokens/` by `scripts/build_tokens.py` (a test fails if it drifts). The kit's own files are untouched; additions are in the new `design/system/tokens/site.css` (header glass, scrollbar, a warm third colour, the figure palette, shadows, header height, rail width). Each value was already in a live page.
- `--faint` is the kit's corrected #6B6357 everywhere; Plan, Roadmap, System Design and its figures had #8B8375, so their small grey text is a little darker.
- Roadmap and System Design body text is 17px and their H1 68px (they were 15px and 52px): the Document layout has one scale.
- Product and System Design are rendered from `application/Product.json` and `design/System_Design.json`. The JSON is newer than the old snapshots (a storage sentence, a decision-log entry and "Last updated" were edited in the JSON on 2026-10-12 and never re-exported), so those lines differ from the old pages. The old pages printed each figure's design brief as visible text under it; the JSON marks those as specs, and the new pages do not print them.
- The two System Design flow diagrams and the three Product step figures were drawn by the old bundle's runtime; they were captured from the rendered pages into `static/figures/` with their colours turned into tokens.
- The sample edition (`/weekly-digest/sample-2026-08-04`) is the one page not built from a template: it is the October 2026 design snapshot, served as the file it is, with its own header. It is a known exception (`site_check.KNOWN_EXCEPTIONS`), not covered by the drift check.
- Not carried over: the Product steps' scroll-driven highlight of the step numbers. Carried over: glossary definitions on hover and focus, the contents filter and scrollspy, the rail toggle, the decision log's expand all.
- The snapshot pages (`application/Product.html`, `design/System-Design.html`) are not edited and no longer served; they are listed as stale artifacts.
