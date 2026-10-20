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
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from jinja2 import ChoiceLoader, FileSystemLoader

from agents import briefs, budget, digest, llm, reader
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
    return JSONResponse(body)


# A public endpoint that opens a database connection per request is a cheap way to
# load the database, so the answer is cached briefly.
_LEDGER_TTL_S = 15
# `at` starts as None, not 0: time.monotonic() can itself start near zero in a fresh
# process, and a zero would read as "checked just now" for the first 15 seconds, which
# is exactly when the deploy healthcheck asks.
_ledger_cache = {"at": None, "ok": False}


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
                                               "notice": None, "weeks": []})


@app.get("/weekly-digest")
def weekly_digest(request: Request, week: Optional[str] = None, notice: Optional[str] = None, item: Optional[str] = None):
    if week is not None and not WEEK_RE.fullmatch(week):
        return _panel(request, "No such edition", "That is not a week this digest knows.", 404)
    try:
        with _connect() as conn:
            weeks = [{"id": r[0], "label": digest.edition_label({"window": r[1]}, r[2]) or r[0]} for r in conn.execute(
                "SELECT id, body -> 'window', published_at FROM briefs ORDER BY id DESC LIMIT 20").fetchall()]
            row = briefs.get(conn, week) if week else briefs.latest(conn)
    except Exception as e:  # database down or unreachable: say so on the page, with the right status
        print(f"weekly digest: database unavailable ({type(e).__name__})", file=sys.stderr)
        return _panel(request, "The digest is unavailable right now", "The database that holds it did not answer. "
                      "Nothing is lost; try again in a minute.", 503)
    shown = NOTICES.get(notice or "")
    context = {"digest": None, "error": None, "weeks": weeks, "notice_item": (item or "")[:200],
               "notice": {"text": shown[0], "good": shown[1]} if shown else None}
    if row is None:
        if week:
            return _panel(request, "No such edition", f"There is no edition for {week}.", 404)
        return templates.TemplateResponse(request=request, name="digest.template.html", context=context)
    try:
        context["digest"] = digest.view(row[0], row[1], row[2])
    except digest.DigestMalformed as bad:
        print(f"weekly digest: brief {row[0]} cannot be shown ({type(bad).__name__})", file=sys.stderr)
        context["error"] = {"title": "This edition cannot be shown",
                            "text": f"The brief for {row[0]} was published but is not in a shape this page can display. "
                                    "The edition is intact in the database; the page will show it once it is fixed."}
    return templates.TemplateResponse(request=request, name="digest.template.html", context=context)


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


def _add_news(url: str) -> dict:
    try:
        client = llm.build_client()
    except SystemExit:  # build_client exits when the API key is missing; a route must not
        raise RuntimeError("no API key configured") from None
    with budget.connect() as conn:
        return reader.add_url(conn, client, url, datetime.now(timezone.utc))


app.mount("/", StaticFiles(directory=ROOT, html=True), name="site")
