# Autopilot: the M1 overnight build

Written 2026-10-17 for one unattended run in Claude Code, started by Varun before he leaves for the night.
Varun is away until morning. **Do not ask him anything.** Work through the steps below in order. Where
this file and `plan/plan.md` do not settle a question, take the most reversible option, record it under
"Decisions I made" in the debrief, and carry on.

The morning deliverable is `DEBRIEF-2026-10-18.md` at the repo root: what was built, what is live, what
it cost, what was decided, and what is blocked. Keep it current **after every step**, not only at the end,
because the session may stop partway (see "If the session stops").

---

## What you may do without asking

- Write code, tests, schema and docs in this repo; commit as Varun; push to `main` with the push command
  below, and only after the full suite passes with `REQUIRE_DB=1`.
- Make paid API calls **only through `agents/llm.py`** (so every one is booked in the ledger), for: the
  Reader's web fetches, the eval gates, and one end-to-end weekly run.
- Open and close the Railway Postgres tunnel. Create tables through `agents/schema.sql`.
- Spawn subagents for review and QA.

## What you must never do

- Print, log or paste `DATABASE_URL`, any API key or any credential. Never commit `.env`. Run a secrets
  scan of the staged diff before every commit.
- Touch `data/evals/golden/`.
- Hand-edit the Claude Design snapshot pages (`application/*.html`, `design/*.html`). If a change makes
  one stale, list it in the debrief under "Stale artifacts".
- Force-push, rewrite history, or delete data outside a test's rolled-back transaction.
- Change Railway settings, the Claude Console, the claude.ai project memory, or the scheduled Weekly
  Digest. **The old digest keeps running in parallel; do not touch it.**
- Enable a recurring schedule for the new weekly run. Build the entrypoint and document how to schedule
  it; Varun switches it on.
- Send email or messages, or publish anywhere except this app's own pages.

## Setup (do first)

1. Keep the Mac awake for 12 hours: `caffeinate -ims -t 43200 &`
2. Open the tunnel:
   `(railway connect Postgres --tunnel-only --port 15432 >/dev/null 2>&1 &) ; for i in $(seq 1 15); do lsof -i :15432 >/dev/null && break; sleep 1; done`
   Before every test run, check `lsof -i :15432`; reopen it if it has dropped.
3. Record the starting point in the debrief: `git log --oneline -1`, the live `/health`, and the ledger's
   month-to-date spend (read it through `agents/budget.py`, and print the figure only).

**Tests:** `REQUIRE_DB=1 .venv/bin/python -m unittest discover -s tests -t .`

**Push command** (it never runs without the full suite passing):
`REQUIRE_DB=1 .venv/bin/python -m unittest discover -s tests -t . && git push`
Then poll `https://aiml-signal-system-production.up.railway.app/health` every 10 s, for up to 6 minutes,
until it shows the new sha. If it never does, open Railway's deploy logs through the CLI if you can (read
only), record the failure in the debrief, revert your last commit with a new commit, and push again.

---

## The work, in order

Each step names the `plan/plan.md` row it advances. Read the row and the decision log entries it cites
before starting. **The decision log is binding**: read it in full once, at the start.

### Step 0 · Raise the ledger ceiling to $40

- Set `MONTHLY_BUDGET_USD = Decimal("40.00")` in `agents/budget.py`.
- `tests/test_ledger.py` hard-codes the $20 figures. Pin the ceiling **inside the tests** (patch
  `budget.MONTHLY_BUDGET_USD` to `Decimal("20.00")` for the test class) rather than rewriting every
  figure, so the tests do not depend on the production value. Check `data/evals/run_evals.py` and
  `agents/framer.py`, which copy the value for display.
- Prove it: the full suite passes; one ceiling test fails if the pin is removed.
- Update the README if it states the figure. Push.

### Step 1 · The rest of the storage layer (row 7)

Items, sources, the interest graph and feedback, in the same style as `excerpts` and `runs`: DB-enforced
rules, tests in rolled-back transactions, each trigger test proven to fail with its trigger disabled.
Shape the columns by what the Reader (step 2) and the Weekly Digest surface (step 5) need; do steps 1 and 2
together if that is cleaner.

- `sources`: seeded from `config/scout-sources.md`, with tier and reputation prior. The plan speaks of 91
  tiered domains. If the full 91-domain list is not in the repo, seed what is there, and put "where is the
  91-domain list?" under "Questions for Varun". Do not invent domains.
- `items`: one per canonical URL, linking its source(s) and its excerpt, with provenance and
  corroboration.
- `interest graph`: from `config/profile.json`.
- `feedback`: thumbs and free-text per item per brief (step 5 writes it).

When done, row 7 becomes DONE.

### Step 2 · Reader: Scout and Gatekeeper merged (row 8)

Binding decision, 2026-10-17: **discover from feeds and official APIs, and fetch article bodies through
Claude's web fetch, called through the Anthropic API** (the server-side web fetch tool), the same fetcher
the scheduled digest uses.

- Check the current Anthropic docs for the web fetch tool: its exact tool type and version, its
  parameters (cap the fetched content size), whether it must be enabled for the organisation, and how the
  fetched text comes back in the response. If the tool is not enabled for this organisation, or the text
  does not come back to the client, stop this step, record exactly what you found, and continue with steps
  3–5 using the existing golden inputs.
- Use the cheapest current Haiku model as the fetch driver. Every fetch goes through `llm.call()` and is
  booked in the ledger.
- Pipeline: discover (free) → canonicalise → dedup → relevance filter against the interest graph
  (deterministic first) → fetch the body only for candidates that pass → store text with
  `excerpts.store()` (write-once) → create or update the item, its provenance and its corroboration.
- Cost guard: estimate the weekly fetch cost from real numbers in the debrief. If one Reader run would
  cost more than $3, lower the fetch count and say so.
- `/add-news`: a URL submitted to the app is fetched on arrival through the same path.
- Record the Reader as the `reader` stage in `runs`, with pointers to its output.

### Step 3 · Reader's canonicalisation gate (row 9)

A golden set of URL pairs that must, and must not, collapse to one item, **including a planted
near-duplicate**. Free and deterministic, with no model. Put it in `data/evals/canonical/`, never in
`golden/`. Prove that the gate fails when canonicalisation is deliberately broken.
Mark the set **"draft, awaiting Varun's review"** in the file and in the debrief; the row is DONE only
after he signs it off, so leave it IN PROGRESS.

### Step 4 · Port the Framer and its gate (row 10)

**Port it, do not extend it** (see the row). The Framer runs on the hosted system and reads verified items
from the database. The existing golden cases run against the ported Framer, and the faithfulness and
structural gates stay green. Run the paid evals once; record cost and results. Record it as the `framer`
and `gates` stages in `runs`.

### Step 5 · The Weekly Digest surface (row 11)

Rendered at request time from the database, like `/plan`, with the site header and nav from
`templates/`. It has two subsections ("Learning for the Week" and "This Week's Stories") with a sticky
sub-nav, and thumbs up and down on each item, with a free-form box on thumbs-down, written to `feedback`.

- Anyone can vote, with no login. Rate-limit per client, and store only a salted hash of the client
  address, never the address itself.
- If the existing `Weekly-Digest` page link is replaced, keep the old URL working with a 301, the way
  `/plan` did.
- Format and guard checks: a malformed brief must not take the site down. The healthcheck already stops
  a broken deploy; make sure a bad *brief* in the DB shows an error panel, not a 500.

### Step 6 · One real end-to-end run

Run the whole pipeline once for the current ISO week, with real sources (paid, small): Reader → Framer →
gates → publish to the Weekly Digest surface. Exercise resume once on purpose: kill the run after the
Reader, rerun, and show from `run_stages` that it resumed at the Framer without re-fetching. Build the
weekly entrypoint (a command Railway can schedule), document it in the README, and **do not schedule
it**.

### Step 7 · Final QA (fresh context)

Spawn a QA subagent that has not seen the build. Give it `plan/plan.md`, this file and the debrief, and
have it verify independently:

- the full suite with `REQUIRE_DB=1`, and the eval gates;
- every live page (`/health`, `/plan`, `/roadmap`, the Weekly Digest surface, `/application/Product.html`)
  returns 200 and shows what the plan claims;
- every claim in the debrief is true. It marks each claim **Verified**, **Not verified** or **Wrong**, and
  the debrief carries its verdicts unchanged.

Then close the tunnel (`kill $(lsof -t -i :15432)`) and stop caffeinate.

---

## After every step

1. **Review.** Spawn a reviewer subagent with a fresh context. Give it the row, the diff and the test
   output, and ask it to be skeptical:
   - Does the work meet the row's Outcome?
   - Does every new test fail when the thing it checks is broken?
   - Are there secrets in the diff? Is there anything outside scope?

   Fix what it finds. Whatever you do not fix goes into the debrief under "Open review findings".
2. **Plan.** Update `plan/plan.md`:
   - set the row's status;
   - add a decision log entry for anything decided (newest first, at most five sentences);
   - update the Status paragraph;
   - run `python3 scripts/check_plan_format.py`.
3. **Commit and push.** Commit as Varun, with a message that names the row, then run the push command.
4. **Debrief.** Append the step's section to the debrief.

## Stop and skip rules

- **The same failure twice.** If two fix attempts fail, mark the step **Blocked** in the debrief, with
  the error and what you tried. Move on to the next step that does not depend on it. Steps 3 and 5 do not
  depend on step 2.
- **Spend.** If the ledger's month-to-date spend reaches **$35**, make no more paid calls. Finish the free
  work and record the spend.
- **Refused commands.** If a command needs approval that nobody is there to give, do not wait. Record the
  command and what it was for, and continue with something else.
- **Stopping for good.** Stop when nothing buildable is left. Go-live, the style row, the story video and
  turning off the old digest are Varun's steps, not this run's.

## If the session stops

On "continue", read the debrief's progress log and `git log`, re-run the setup (caffeinate and the tunnel),
and resume at the first step that is not complete. Never redo a step that is pushed and recorded.

---

## Debrief format (`DEBRIEF-2026-10-18.md`)

1. **The morning summary, in five lines.** What is live, what is not, what Varun must do first.
2. **Step table.** For each step: status (Done, Done pending review, Blocked, Not started), commit sha,
   and whether it is live.
3. **Live links** to check.
4. **Spend.** The ledger before and after, per step, and the cost estimate per weekly run.
5. **Decisions I made**, each with the reversible option taken and how to undo it.
6. **Blocked**, and what would unblock each.
7. **Open review findings.**
8. **QA verdicts** from step 7, unedited.
9. **Stale artifacts**: Design snapshot pages, README sections and roadmap entries that are now out of
   date.
10. **Questions for Varun.**
11. **Progress log.** One timestamped line per step.
