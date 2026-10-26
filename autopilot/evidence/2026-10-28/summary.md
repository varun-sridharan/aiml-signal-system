# Run 6 evidence (2026-10-28)

No paid call was made in this run. Everything below is code, tests and local measurement.

## A1 · Document titles, measured in headless Chrome (scripts/site_measure.py, local server)

| page | layout | H1 at 1440px, Run 5 | H1 at 1440px, now | H1 at 390px, Run 5 | H1 at 390px, now |
|---|---|---|---|---|---|
| Product | Landing | 68px | 68px | 40px | 40px |
| Weekly Digest | Edition | 24px | 24px | 24px | 24px |
| System Design | Document | 68px | **52px** | 40px | 36px |
| Roadmap | Document | 68px | **52px** | 40px | 36px |
| Plan | Document | 68px | **52px** | 40px | 36px |

The header is unchanged on all five pages: sticky, 58px (50px at phone width), wordmark 17px Newsreader at x=163 (x=16 at phone width), nav 13px Public Sans. Body text is 17px on the four non-Edition pages. Raw numbers: `stepA-after-1440/measurements.json`, `stepA-after-390/measurements.json` and the page screenshots beside them. The token is `--fs-doc-title: clamp(36px,5.4vw,52px)` in `design/system/tokens/site.css`; `tests/test_site.py::Titles` pins it and that `site.css` holds no literal size.

## A3 · The review page

`/weekly-digest/review`, read from the real database through a local server: HTTP 200, `x-robots-tag: noindex, nofollow`, a `<meta name="robots">` tag, not in the nav or the archive. It shows edition 02 (2026-W44) with 3 flags, 3 dismissed, 0 rewritten, 0 removed, 0 story holds, 2 warnings, and edition 01 with counts only (the log kept no sentence text before this run). `tests/test_review.py::AgainstTheRealEditions` renders it from the real editions and scans the page against every stored excerpt with the evidence scanner (200 consecutive characters, folded): no hit.

## B · /health

Local server on the real database: `{"status":"ok","ledger":"ok","last_edition":{"week":"2026-W44","published_at":"2026-10-26T05:35:20Z"}}`.

## C · POST /add-news/batch

Without a token and with no `ADD_NEWS_TOKEN` set: 503 (checked locally, to be checked live in Step E). The route, the importer and the weekly run's handling of an imported link are covered by `tests/test_capture.py`, including a test that patches the model call and the fetcher to raise and posts a batch, and one that runs a whole week with an imported link and checks the flag in the stored brief and on the rendered page.
