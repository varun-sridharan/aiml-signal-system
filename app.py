"""Minimal web server so the site serves from a URL instead of a local file path.

M1 activity 1. This file does one thing: serve the five existing HTML pages over
HTTP. It does not read the database, call the API, or run an agent — standing up
the host and moving the agents in one step would mean a later failure could not be
attributed to either.

The static mount is the repository root, so dotfiles are refused explicitly: an
unguarded root mount serves `.env` over HTTP, and the README invites people to run
this locally, where that file holds a real API key. Everything else under the root
is already in the public repo, so the guard is the whole of what needs hiding.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# WHAT: the repository root is the static root, and that is load-bearing.
# CONCEPT: the pages live in application/, design/ and plan/, and link across each
# other with relative paths like ../plan/Roadmap.html. Mounting each folder on its
# own prefix puts every sibling folder outside its mount, so those links 404 — a
# failure that reads like a broken design and is actually a broken server.
ROOT = Path(__file__).resolve().parent

HOME = "/application/Product.html"

templates = Jinja2Templates(directory=ROOT / "templates")

app = FastAPI(title="aiml-signal-system", docs_url=None, redoc_url=None)


# WHAT: refuse any path with a dot-prefixed segment, before routing.
# CONCEPT: the cost of mounting the whole tree. `.env` is gitignored so it never
# reaches the deploy, but it does exist locally, and a static mount would hand it
# to anyone who asked. Checking every segment also covers `.git/config` and any
# dotfile added later, which a single hardcoded `.env` check would not.
@app.middleware("http")
async def block_dotfiles(request: Request, call_next):
    if any(seg.startswith(".") for seg in request.url.path.split("/")):
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
    return JSONResponse(body)


# WHAT: render plan/Plan.json through templates/page.html at request time.
# CONCEPT: the first page that stopped being a hand-regenerated ~800KB artefact. The
# frozen plan/Plan.html served beside it for a while so the rendered page could be
# compared against the reference rather than trusted; that comparison is done and the
# bundle is gone, because a second copy of a page is a second thing to go stale.
#
# Keys beginning with an underscore are authoring notes and are never rendered. The
# template reads named keys only, so no underscore key can reach the page.
@app.get("/plan")
def plan(request: Request):
    content = json.loads((ROOT / "plan" / "Plan.json").read_text(encoding="utf-8"))
    # WHAT: pass the JSON through by name.
    # CONCEPT: the template derives progress from the Plan table, so there is no
    # progress key to pass — it was deleted once the derivation was proved.
    return templates.TemplateResponse(
        request=request,
        name="page.html",
        context={
            "site": content["site"],
            "nav": content["nav"],
            "phases": content["phases"],
            "decisionLog": content["decisionLog"],
            "lastUpdated": content["lastUpdated"],
        },
    )


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


app.mount("/", StaticFiles(directory=ROOT, html=True), name="site")
