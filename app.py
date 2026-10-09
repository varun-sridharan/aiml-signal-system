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

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from agents import budget
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


app.mount("/", StaticFiles(directory=ROOT, html=True), name="site")
