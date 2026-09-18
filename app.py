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
# CONCEPT: the first page that stops being a hand-regenerated ~800KB artefact. The
# frozen plan/Plan.html stays exactly where it is and keeps serving from the static
# mount — the two URLs sit side by side on purpose, so the rendered page can be
# compared against the reference rather than trusted.
#
# Keys beginning with an underscore are authoring notes and are never rendered. The
# template reads named keys only, so no underscore key can reach the page.
@app.get("/plan")
def plan(request: Request):
    content = json.loads((ROOT / "plan" / "Plan.json").read_text(encoding="utf-8"))
    # WHAT: pass the JSON through, minus its own progress block.
    # CONCEPT: phases[0].progress is hardcoded and has already gone stale once. The
    # template derives progress from the table instead, so the stale copy is left in
    # the file for a later cleanup but never given to the renderer.
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


app.mount("/", StaticFiles(directory=ROOT, html=True), name="site")
