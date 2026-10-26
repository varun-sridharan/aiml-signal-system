"""Minimal web server so the site serves from a URL instead of a local file path.

M1 activity 1. This file does one thing: serve the five existing HTML pages over
HTTP. It does not read the database, call the API, or run an agent — standing up
the host and moving the agents in one step would mean a later failure could not be
attributed to either.

The static mount is the repository root, so dotfiles are refused explicitly: an
unguarded root mount serves `.env` over HTTP, and the README invites people to run
this locally, where that file holds a real API key. Page sources (*.md,
*.template.html, templates/, scripts/) are refused too: they are public in the repo, but the site
serves their rendered pages, not a second raw copy of each.

Plan and Roadmap render from markdown at request time (plan/FORMAT.md is the format),
and the app refuses to start if either file breaks that format.
"""

import hmac
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from jinja2 import ChoiceLoader, FileSystemLoader

from agents import briefs, budget, capture, digest, llm, pages, reader, review
from scripts.plan_format import check, load_plan, load_roadmap

# WHAT: the repository root is the static root, and that is load-bearing.
# CONCEPT: the pages live in application/, design/ and plan/, and link across each
# other with relative paths like ../plan/Roadmap.html. Mounting each folder on its
# own prefix puts every sibling folder outside its mount, so those links 404 — a
# failure that reads like a broken design and is actually a broken server.
ROOT = Path(__file__).resolve().parent

HOME = "/application/Product.html"

PLAN_MD = ROOT / "plan" / "plan.md"
ROADMAP_MD = ROOT / "plan" / "roadmap.md"

# WHAT: refuse to start when either markdown source breaks plan/FORMAT.md.
# CONCEPT: a malformed file must stop the deploy, not become a page that quietly shows
# the wrong thing. Exiting here means the process never binds its port, so /health
# never answers, so Railway's healthcheck fails and the previous deployment keeps
# serving. The same check runs in the pre-commit hook, which catches it sooner.
_errors = check(PLAN_MD, ROADMAP_MD)
if _errors:
    for _e in _errors:
        print(_e, file=sys.stderr)
    sys.exit(f"plan format check failed with {len(_errors)} error(s); refusing to start")

# WHAT: create the spend ledger's table if it is missing, from agents/schema.sql.
# CONCEPT: unlike the format check, this does not stop the app. The pages do not need
# the database, and the ledger fails closed on its own: with no table or no database,
# every paid call is refused. /health reports whether the ledger is reachable.
try:
    with budget.connect():
        pass  # connect() applies the schema idempotently on first use
except budget.LedgerUnavailable as _missing:
    print(f"spend ledger unavailable at startup: {_missing}", file=sys.stderr)

# WHAT: templates come from plan/ (the two page templates, beside their sources) and
# templates/ (the shared head and header partials).
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.filters["wbr_code"] = pages.break_code      # a long identifier in a table cell may break only after / _ . or (
templates.env.loader = ChoiceLoader([
    FileSystemLoader(ROOT / "plan"),
    FileSystemLoader(ROOT / "templates"),
])

app = FastAPI(title="aiml-signal-system", docs_url=None, redoc_url=None)


# WHAT: refuse dotfiles and page sources, before routing.
# CONCEPT: the cost of mounting the whole tree. `.env` is gitignored so it never
# reaches the deploy, but it does exist locally, and a static mount would hand it
# to anyone who asked. Checking every segment also covers `.git/config` and any
# dotfile added later, which a single hardcoded `.env` check would not.
#
# The Plan and Roadmap sources are refused for a different reason: they are served as
# rendered pages at /plan and /roadmap, and the raw markdown, its templates and the
# format contract would be a second, unrendered copy of each page — including the HTML
# comments that are authoring notes and never meant to be shown. Matched on the path
# rather than the directory so that a source added anywhere later is covered too.
# templates/ holds the shared Jinja partials, which are sources in the same sense.
def _is_source(path: str) -> bool:
    segs = path.split("/")
    if any(seg.startswith(".") for seg in segs):
        return True
    if "scripts" in segs or "templates" in segs:
        return True
    name = segs[-1].lower()
    return name.endswith(".md") or name.endswith(".template.html")


@app.middleware("http")
async def block_sources(request: Request, call_next):
    if _is_source(request.url.path):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


# WHAT: both real routes are declared before the mount, not after.
# CONCEPT: Starlette matches routes in registration order and a mount at "/" matches
# every path. Registered after the mount, these would be unreachable.
@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse(HOME)


@app.get("/health")
def health() -> JSONResponse:
    # WHAT: report the deployed commit when the platform tells us what it is.
    # CONCEPT: Railway injects RAILWAY_GIT_COMMIT_SHA. It is absent locally, and the
    # check still has to answer 200 there — health is "this process is serving",
    # not "this process knows its own provenance".
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_SHA")
    body = {"status": "ok"}
    if sha:
        body["sha"] = sha[:7]
    # WHAT: say whether the spend ledger answers, and nothing else about it.
    # CONCEPT: no figures and no URL, because /health is public. An unreachable ledger
    # is still a 200: the site is serving, and paid calls are refused on their own.
    body["ledger"] = "ok" if _ledger_ok() else "unavailable"
    # WHAT: the newest published edition's week and the time it was published (Run 6, Step B), so the Friday job can be checked from outside.
    # CONCEPT: null when there is none or the database does not answer; the answer is cached with the ledger's, so the page costs no query per hit.
    body["last_edition"] = _last_edition()
    return JSONResponse(body)


# A public endpoint that opens a database connection per request is a cheap way to
# load the database, so the answer is cached briefly.
_LEDGER_TTL_S = 15
# `at` starts as None, not 0: time.monotonic() can itself start near zero in a fresh
# process, and a zero would read as "checked just now" for the first 15 seconds, which
# is exactly when the deploy healthcheck asks.
_ledger_cache = {"at": None, "ok": False}


_edition_cache = {"at": None, "value": None}


def _last_edition():
    now = time.monotonic()
    if _edition_cache["at"] is None or now - _edition_cache["at"] > _LEDGER_TTL_S:
        value = None
        try:
            with _connect() as conn:
                row = conn.execute("SELECT id, published_at FROM briefs ORDER BY published_at DESC, id DESC LIMIT 1").fetchone()
            if row is not None:
                value = {"week": row[0], "published_at": row[1].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        except Exception:                      # a database that does not answer is a null, never a failed health check
            value = None
        _edition_cache.update(at=now, value=value)
    return _edition_cache["value"]


def _ledger_ok() -> bool:
    now = time.monotonic()
    if _ledger_cache["at"] is None or now - _ledger_cache["at"] > _LEDGER_TTL_S:
        _ledger_cache.update(at=now, ok=budget.ledger_reachable())
    return _ledger_cache["ok"]


# WHAT: render plan/plan.md and plan/roadmap.md at request time.
# CONCEPT: the markdown is the page. There is no JSON and no build step: an edit to
# either file shows on the next request. Every number on the page (the plan's
# "55% · 6 of 11", the backlog counts) is derived by the parser from the content, so
# there is no written number to go stale.
#
# The startup check already proved both files parse; parsing again per request is what
# makes a local edit show without a restart. A file broken after startup raises here and
# returns a 500 rather than a page that quietly shows the wrong thing.
@app.get("/plan")
def plan(request: Request):
    return templates.TemplateResponse(request=request, name="plan.template.html", context=load_plan(PLAN_MD))


@app.get("/roadmap")
def roadmap(request: Request):
    return templates.TemplateResponse(request=request, name="roadmap.template.html", context=load_roadmap(ROADMAP_MD))


# WHAT: the old bundle's URL, permanently redirected to the route that replaced it.
# CONCEPT: the four other pages are generated bundles with ../plan/Plan.html baked into
# six links, and rewriting those means regenerating four pages through Claude Design to
# change one string. A redirect costs one route and makes every existing link — in the
# bundles, in anyone's history, in the decision log's prose — land on the live page.
# 301 rather than 302 because the move is permanent: the file is deleted in this commit.
#
# Registered BEFORE the mount. Starlette matches in registration order and a mount at
# "/" matches every path, so after it this would never fire — and it would not 404
# either, it would silently keep serving whatever file happened to be there.
# WHAT: the Product and System Design pages, rendered from their content files like Plan and Roadmap (Run 5, bk-tokens-css).
# CONCEPT: one build path for every page. The old URLs stay: they were Claude Design snapshots, and the snapshots are kept in the
# repository as they were (they are not served). The content is the JSON's; the templates only lay it out.
@app.get("/application/Product.html")
def product_page(request: Request):
    return templates.TemplateResponse(request=request, name="product.template.html", context={"page": pages.product()})


@app.get("/design/System-Design.html")
def system_design_page(request: Request):
    return templates.TemplateResponse(request=request, name="system_design.template.html", context={"page": pages.system_design()})


@app.get("/plan/Plan.html")
def plan_legacy() -> RedirectResponse:
    return RedirectResponse("/plan", status_code=301)


# WHAT: the same for the Roadmap bundle, which the other bundles also link to.
@app.get("/plan/Roadmap.html")
def roadmap_legacy() -> RedirectResponse:
    return RedirectResponse("/roadmap", status_code=301)


# WHAT: the Weekly Digest, rendered at request time from the newest published brief.
# CONCEPT: same pattern as /plan: the database holds the content, the template shows it,
# and nothing is built. A brief that cannot be shown (malformed, or the database is down)
# becomes an error panel on the page, never a 500: a bad row must not take the site down,
# and the healthcheck is deliberately blind to it (the brief is data, not code).
WEEK_RE = re.compile(r"\d{4}-W\d{2}")   # always used with .fullmatch: "$" would let a trailing newline through
NOTICES = {
    "voted-up": ("Thanks. Marked useful.", True),
    "voted-down": ("Thanks. Marked not useful, and your note was kept.", True),
    "slow-down": ("That is a lot of votes in a short time. Please try again later.", False),
    "bad-vote": ("That vote could not be recorded.", False),
}
MAX_FORM_BYTES = 8_192


def _connect():
    return budget.connect()


def _panel(request: Request, title: str, text: str, status: int = 200):
    return templates.TemplateResponse(request=request, name="digest.template.html", status_code=status,
                                      context={"digest": None, "error": {"title": title, "text": text},
                                               "notice": None, "notice_item": "", "archive": [], "active_tab": "news"})


# The October 2026 design snapshot of an edition, kept as the sample the archive links to. It is served as the file it is.
SAMPLE_EDITION_PATH = ROOT / "application" / "Weekly-Digest.html"
SAMPLE_EDITION_URL = "/weekly-digest/sample-2026-08-04"


def _archive(conn) -> tuple[list, dict]:
    """Every published edition, newest first, with the sample edition last; and each edition's number (01 is the first published)."""
    rows = conn.execute(
        "SELECT id, body #> '{edition,thread,lines}', body -> 'thread', "
        "CASE WHEN jsonb_typeof(body -> 'items') = 'array' THEN jsonb_array_length(body -> 'items') ELSE 0 END, "
        "body -> 'window', published_at FROM briefs ORDER BY published_at ASC, id ASC").fetchall()
    numbers = {r[0]: n for n, r in enumerate(rows, start=1)}
    entries = [digest.archive_entry(r[0], numbers[r[0]], r[1], r[2], r[3], r[4], r[5]) for r in reversed(rows)]
    return entries + [dict(digest.SAMPLE)], numbers


@app.get("/weekly-digest")
def weekly_digest(request: Request, week: Optional[str] = None, notice: Optional[str] = None, item: Optional[str] = None,
                  tab: Optional[str] = None):
    if week is not None and not WEEK_RE.fullmatch(week):
        return _panel(request, "No such edition", "That is not a week this digest knows.", 404)
    try:
        with _connect() as conn:
            archive, numbers = _archive(conn)
            row = briefs.get(conn, week) if week else briefs.latest(conn)
    except Exception as e:  # database down or unreachable: say so on the page, with the right status
        print(f"weekly digest: database unavailable ({type(e).__name__})", file=sys.stderr)
        return _panel(request, "The digest is unavailable right now", "The database that holds it did not answer. "
                      "Nothing is lost; try again in a minute.", 503)
    shown = NOTICES.get(notice or "")
    context = {"digest": None, "error": None, "archive": archive, "notice_item": (item or "")[:200],
               "active_tab": "archive" if tab == "archive" else "news",
               "notice": {"text": shown[0], "good": shown[1]} if shown else None}
    if row is None:
        if week:
            return _panel(request, "No such edition", f"There is no edition for {week}.", 404)
        return templates.TemplateResponse(request=request, name="digest.template.html", context=context)
    try:
        context["digest"] = digest.view(row[0], row[1], row[2], numbers.get(row[0]))
        if context["notice_item"] not in context["digest"]["item_ids"]:
            context["notice_item"] = ""          # a notice about a story this edition does not show is shown at the top instead of being lost
    except digest.DigestMalformed as bad:
        print(f"weekly digest: brief {row[0]} cannot be shown ({type(bad).__name__})", file=sys.stderr)
        context["error"] = {"title": "This edition cannot be shown",
                            "text": f"The brief for {row[0]} was published but is not in a shape this page can display. "
                                    "The edition is intact in the database; the page will show it once it is fixed."}
    return templates.TemplateResponse(request=request, name="digest.template.html", context=context)


# WHAT: the review note, an unlinked page of what the claim gate held, removed or rewrote in the first editions (Run 6, Step A3).
# CONCEPT: not in the nav, not in the archive, noindex in the page and in the header. It shows our own text only (agents/review.py)
# and stops growing after REVIEW_EDITIONS published editions. Registered before the mount like every other route.
@app.get("/weekly-digest/review")
def weekly_digest_review(request: Request):
    notes, error = [], None
    try:
        with _connect() as conn:
            notes = review.entries(conn)
    except Exception as e:
        print(f"review note: database unavailable ({type(e).__name__})", file=sys.stderr)
        error = "The database that holds the editions did not answer. Try again in a minute."
    return templates.TemplateResponse(request=request, name="review.template.html", status_code=503 if error else 200,
                                      context={"notes": notes, "error": error, "limit": review.REVIEW_EDITIONS},
                                      headers={"X-Robots-Tag": "noindex, nofollow"})


@app.get(SAMPLE_EDITION_URL)
def weekly_digest_sample() -> HTMLResponse:
    return HTMLResponse(SAMPLE_EDITION_PATH.read_text(encoding="utf-8"))


def _cast_vote(fields: dict, address: str):
    """Record one vote; returns the notice key. Runs off the event loop (it blocks on the database)."""
    brief_id, item, direction = fields.get("brief", ""), fields.get("item", ""), fields.get("vote", "")
    salt = digest.vote_salt()
    if not WEEK_RE.fullmatch(brief_id) or not salt:
        return "bad-vote", brief_id
    with _connect() as conn:
        row = briefs.get(conn, brief_id)
        try:
            known = digest.view(row[0], row[1])["item_ids"] if row else set()
        except digest.DigestMalformed:
            known = set()
        if item not in known:
            return "bad-vote", brief_id
        try:
            briefs.vote(conn, brief_id, item, direction, fields.get("comment"),
                        briefs.client_hash(address, salt), datetime.now(timezone.utc))
        except briefs.RateLimited:
            return "slow-down", brief_id
        except briefs.FeedbackRefused:
            return "bad-vote", brief_id
    return ("voted-down" if direction == "down" else "voted-up"), brief_id


@app.post("/weekly-digest/vote")
async def weekly_digest_vote(request: Request):
    # Same-origin only: a browser sends Origin on a cross-site POST, and another site should
    # not be able to cast votes from a visitor's address. Requests with no Origin (curl,
    # old clients) are allowed; the rate limits are what bound those.
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        return JSONResponse({"detail": "cross-site vote refused"}, status_code=403)
    if int(request.headers.get("content-length", "0") or 0) > MAX_FORM_BYTES:
        return JSONResponse({"detail": "too large"}, status_code=413)
    raw = b""
    async for chunk in request.stream():       # capped as it is read, not after
        raw += chunk
        if len(raw) > MAX_FORM_BYTES:
            return JSONResponse({"detail": "too large"}, status_code=413)
    try:
        fields = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), max_num_fields=10).items()}
    except ValueError:  # more fields than a vote has
        return RedirectResponse("/weekly-digest?notice=bad-vote", status_code=303)
    address = digest.client_address(request.headers, request.client.host if request.client else None)
    try:
        notice, brief_id = await run_in_threadpool(_cast_vote, fields, address)
    except Exception as e:
        print(f"weekly digest vote failed ({type(e).__name__})", file=sys.stderr)
        return JSONResponse({"detail": "voting is unavailable right now"}, status_code=503)
    week = brief_id if WEEK_RE.fullmatch(brief_id) else ""
    if week and notice.startswith("voted"):
        item_q = quote(fields.get("item", ""), safe="")
        # The notice is shown on the story that was voted on, and the page lands there.
        return RedirectResponse(f"/weekly-digest?week={week}&notice={notice}&item={item_q}#item-{item_q}", status_code=303)
    return RedirectResponse(f"/weekly-digest?week={week}&notice={notice}" if week else f"/weekly-digest?notice={notice}",
                            status_code=303)


# WHAT: the old snapshot's URL, permanently redirected to the rendered page that replaced it.
# CONCEPT: the same move /plan made. The other Design snapshots link to Weekly-Digest.html
# in their baked navigation; a 301 sends every existing link, bookmark and history entry to
# the live page, and must be registered before the mount for the reason /plan's was.
@app.get("/application/Weekly-Digest.html")
def weekly_digest_legacy() -> RedirectResponse:
    return RedirectResponse("/weekly-digest", status_code=301)


# WHAT: /add-news, a URL handed to the app is fetched on arrival through Reader's own path.
# CONCEPT: an open endpoint that spends money is an invitation. It needs a shared secret
# (the ADD_NEWS_TOKEN environment variable, sent as the X-Add-News-Token header) and is
# switched OFF, answering 503, until that variable is set. Each fetch is booked in the
# ledger and refused at the ceiling like any other paid call, and a per-process limit
# stops a loop with the right token from running up the month in an hour.
ADD_NEWS_LIMIT = 10
_add_news_times: list = []


@app.post("/add-news")
async def add_news(request: Request) -> JSONResponse:
    token = os.environ.get("ADD_NEWS_TOKEN", "")
    if not token:
        return JSONResponse({"detail": "add-news is not configured"}, status_code=503)
    if not hmac.compare_digest(request.headers.get("x-add-news-token", "").encode("utf-8"), token.encode("utf-8")):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        url = str((await request.json()).get("url", "")).strip()
    except Exception:
        return JSONResponse({"detail": "send JSON: {\"url\": \"https://...\"}"}, status_code=400)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > 250:
        return JSONResponse({"detail": "a http(s) URL of at most 250 characters is required"}, status_code=400)
    now = time.monotonic()
    _add_news_times[:] = [t for t in _add_news_times if now - t < 3600]
    if len(_add_news_times) >= ADD_NEWS_LIMIT:
        return JSONResponse({"detail": "too many additions this hour"}, status_code=429)
    _add_news_times.append(now)
    try:
        result = await run_in_threadpool(_add_news, url)  # blocking database and API work, off the event loop
    except (budget.BudgetExceeded, budget.LedgerUnavailable):
        return JSONResponse({"detail": "refused: the spend ledger will not allow another paid call"}, status_code=503)
    except Exception as e:  # after the money may be spent: say so plainly, never a bare 500
        print(f"add-news failed: {type(e).__name__}", file=sys.stderr)
        return JSONResponse({"detail": "the page could not be added"}, status_code=502)
    return JSONResponse(result)


# WHAT: /add-news/batch, the Friday capture import (Run 6, Step C; docs/capture-import.md).
# CONCEPT: the scheduled task in the Claude app posts the week's saved links, a JSON list of {url, addedAt, note}, and they join the week's
# candidates flagged "from your backlog". It is FREE: nothing is fetched and no model is called here (the weekly run fetches), so there is no
# per-hour limit, only a size limit. Same token, same 503-until-configured as /add-news. Posting a batch twice changes nothing.
MAX_BATCH_BYTES = 64_000


@app.post("/add-news/batch")
async def add_news_batch(request: Request) -> JSONResponse:
    token = os.environ.get("ADD_NEWS_TOKEN", "")
    if not token:
        return JSONResponse({"detail": "add-news is not configured"}, status_code=503)
    if not hmac.compare_digest(request.headers.get("x-add-news-token", "").encode("utf-8"), token.encode("utf-8")):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    raw = await request.body()
    if len(raw) > MAX_BATCH_BYTES:
        return JSONResponse({"detail": f"a batch is at most {MAX_BATCH_BYTES} bytes"}, status_code=413)
    try:
        links = json.loads(raw)
        if isinstance(links, dict) and isinstance(links.get("links"), list):
            links = links["links"]
        if not isinstance(links, list):
            raise ValueError
    except (ValueError, RecursionError):
        return JSONResponse({"detail": "send a JSON list: [{\"url\": \"https://...\", \"addedAt\": \"...\", \"note\": \"...\"}]"}, status_code=400)
    if len(links) > capture.MAX_LINKS:
        return JSONResponse({"detail": f"at most {capture.MAX_LINKS} links in one batch"}, status_code=413)
    try:
        def work():
            with budget.connect() as conn:
                return capture.import_batch(conn, links, datetime.now(timezone.utc))
        return JSONResponse(await run_in_threadpool(work))
    except Exception as e:
        print(f"add-news batch failed: {type(e).__name__}", file=sys.stderr)
        return JSONResponse({"detail": "the batch could not be stored; nothing is lost, post it again"}, status_code=502)


def _add_news(url: str) -> dict:
    try:
        client = llm.build_client()
    except SystemExit:  # build_client exits when the API key is missing; a route must not
        raise RuntimeError("no API key configured") from None
    with budget.connect() as conn:
        return reader.add_url(conn, client, url, datetime.now(timezone.utc))


app.mount("/", StaticFiles(directory=ROOT, html=True), name="site")
