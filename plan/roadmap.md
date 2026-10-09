---
title: Roadmap & Backlog
kicker: AI/ML Signal · roadmap
lede: What is being built now, what comes next, and every item deliberately left for later. One backlog, so there is nothing to reconcile.
footer: Roadmap · the only backlog in this system. Personal telemetry stays in the private Control Hub; this page is public-safe.
updated: 2026-10-09
---

<!--
Carried verbatim from the retired Roadmap.json. Its key names (`now`, `milestones`,
`backlog`, `livePlan`, `origin`) and the Claude Design round-trip describe that file;
here they are the NOW milestone, the other milestones, # Backlog, Live plan and Origin.

Content source for the Roadmap & Backlog page. Edit this; ask Claude Design to refresh the
design from it. THIS FILE IS THE ONLY BACKLOG. Anything deferred, discussed and dropped,
or promised for later belongs here and nowhere else — a second backlog drifts, and then
neither is true. Structure: `now` is the milestone being built (its detailed plan lives in
Plan & Progress); `milestones` are what comes after, each with binary exit criteria;
`backlog` is everything not yet scheduled, grouped by kind. A milestone's `livePlan` links
to its detailed plan once that milestone becomes current — until then it states when it
will exist, because M2 and M3 are shaped by what M1 teaches and writing them now would be
fiction. Every backlog item carries `origin`: where the item came from, so a future reader
can tell a considered deferral from a stray idea.
-->

# Where this is going

Two systems are converging into one. The **AI Weekly News Digest** runs today as a scheduled cloud task — it knows what to say, has 91 tiered sources and a reader. The **AI/ML Signal System** knows how to prove what it says is true, and has no reader at all. The pivot is to move the Digest's job onto the Signal System's engine, hosted, and retire the Digest.

**Cadence is weekly, and that is a decision rather than a default.** A daily brief optimises for missing nothing, and what it produces is a queue — the reason the previous daily digest was retired despite working as designed. Monthly is too slow to be useful in a field that moves weekly. **Ingestion runs continuously; publication is weekly.** Those are separate clocks: reading costs almost nothing because it is deterministic, while judgement is what costs money, and judgement happens once a week.

> **Sunset criterion**
>
> **The old Digest retires on a criterion, not a date:** when M3 has run clean for two to three consecutive weeks. Not feature parity — M2 alone replaces the debrief but not the predictions and drafts, so switching off earlier costs a Friday read.

# M1 · Reading and writing, hosted

- **Status:** NOW
- **One line:** Reader and Framer live on Railway, both gated, producing the weekly debrief at a URL.
- **Why:** The first milestone that is a system rather than a store. Reader alone fills a database nobody can see; a milestone whose exit criterion is invisible is a weak one. Two agents, two gates, one page.
- **Also includes:** The shared-plumbing extraction (`agents/budget.py`, `agents/llm.py`) sits inside M1 rather than in the backlog — Reader is the cost breaker's second caller, which is exactly when it should be extracted.
- **Plan:** [Detailed plan](/plan)

# M2 · Two voices, one ledger

- **Status:** NEXT
- **One line:** Thought Leadership agent: predictions, and what named people actually said.
- **Why:** This is the half of the old Digest that has no equivalent in the Signal System, and it carries the sharpest reuse in the whole pivot — the quote check is the span check already built, pointed at a different document.
- **Depends on:** M1 — needs Reader's verified pool and the hosted store
- **Live plan:** arrives when M1 completes — M2's plan is shaped by what M1 teaches. Writing it now would be fiction, and slipped or newly-discovered M1 items land here first.

### Scope

- **Concept triage, first.** Before this milestone's plan is written, run `build-plan-concepts` against the shared backlog in `Knowledge Hub/AI-Concepts.html`: a verdict for this project on every open concept, and each adopted one becomes a row in the plan, named by its backlog id.
- **Thought Leadership agent.** Reads the week's verified pool from Reader; writes ranked predictions and a leaders section.
- **Quote fidelity as a hard gate.** Every quote attributed to a named person must be a literal substring of the excerpt fetched from that source. This is `span_found` unchanged. It turns the house rule “quote, never paraphrase” from an instruction into a check — and instructions have already been shown not to hold.
- **Truncation honesty.** A quote discovered through a search index rather than fetched is marked truncated and linked out. Deterministic.
- **Falsifiability as a hard gate.** Every prediction carries a stated falsification condition and a resolution date. Both checkable in code with no model.
- **The resolution ledger.** Three states — **Hit**, **Miss**, **Expired-unresolved**. Kept deliberately simple for this milestone: the weekly run looks for predictions that have come true and marks them Hit, resurrecting from Archive if needed. The rest is handled at the resolution date.
- **Archive surface.** Published and rejected content, expired and pushed-out predictions.

### Exit

- Predictions and the leaders section render on the hosted page
- Every leader quote passes the substring gate against its stored excerpt
- Every prediction carries a falsification condition and a resolution date, verified in code
- The ledger exists, holds its first entry, and all three states are reachable

### Not in

- Distinguishing *not yet* from *never* for an unresolved prediction — in the backlog, revisited after the ledger has real entries
- Prediction originality scoring, which is subjective and stays reported

# M3 · Practice

- **Status:** LATER
- **One line:** Coach reads the configured GitHub repository and writes homework that fits a real spot in it.
- **Why:** The highest hallucination risk in the system, which is why it comes after trust is built — and the first agent whose steps genuinely cannot be written in advance, so it is the one place an agent loop earns its keep.
- **Depends on:** M2 — and on several weeks of real editions existing
- **Live plan:** arrives when M2 completes — Same reason as M2. A plan written three milestones ahead is a guess wearing a schedule.

### Scope

- **Concept triage, first.** Before this milestone's plan is written, run `build-plan-concepts` against the shared backlog in `Knowledge Hub/AI-Concepts.html`: a verdict for this project on every open concept, and each adopted one becomes a row in the plan, named by its backlog id.
- **Coach on the Claude Agent SDK.** Real tool use over a repository it has not seen, with Read, Grep and Glob scoped to declared paths. The only agent here that gets a loop; every other one is a pipeline.
- **Repo grounding as a hard gate, with no model involved.** Every named file path exists, every named symbol appears in it, every command parses. Deterministic.
- **Practice surface.** A *How it Works* sub-tab, and *Open Exercises* with a gear for the repository setting.
- **Per-exercise tile:** status dropdown (To Start / In Progress / Complete), learning goal, estimated duration — **60 minutes or less** — and steps detailed enough that pasting one into a fresh chat is sufficient context to continue.
- **Time logging.** A timestamp when status moves to In Progress; actual minutes recorded at close, optional.
- **Spend and scope enforced by SDK hooks** — the reserve re-checked before every tool call, path allowlist as a permission rule. A loop has no natural cost bound, so it gets a turn cap the pipelines do not need.

### Exit

- Homework lands in Practice, generated from the configured repository
- Every named path and symbol is verified in code before the exercise ships
- Status dropdown and time logging work, and estimated-versus-actual is recorded

### Not in

- Prior-art reconciliation — same repo access, opposite question; backlog
- Completion rate as a target. It is a diagnostic only: a completion rate you optimise for is a generator that writes easy homework.

# After M3

**Control Hub** and **Tuner** both need weeks of real editions before they have anything to work with — a dashboard over two days of data shows nothing, and a model that learns your taste needs your taste expressed more than twice. They are not deprioritised; they are blocked, and the thing that unblocks them is the system running.

# Backlog

Every item here was deliberately deferred, with a reason. `origin` says where it came from so a later reader can tell a considered decision from a passing idea.

## Eval & correctness

### bk-config-contract · Assert the config/code contract in the structural gate

- **Size:** S
- **Origin:** Sep-17-2026 — found by a pre-commit check, after the break had already been committed to disk

Renaming two keys in `config/profile.json` for the weekly pivot broke the Framer: three call sites still indexed the old names and raised `KeyError` on the first real run. **The offline gate stayed green the whole time**, because both call sites sit on API paths that `--no-api` never reaches. **The check is trivial and free:** every `profile[...]` and `preferences[...]` key the code indexes must exist in `profile.json` — a regex over the source against the loaded config, no model, no API. It belongs in `run_structural` beside the coverage check. **The lesson is the more useful part:** “nothing changed behaviourally” was true of the code and false of the system, because the config the code reads is part of the system. A gate that only exercises the free paths will certify a broken run as green.

### bk-corroboration-labels · Hand labels for Reader's provenance and corroboration

- **Size:** M
- **Origin:** Sep-17-2026 — cut from M1 deliberately, not forgotten

Reader judges whether an item has a primary source and independent corroboration. That judgement is model-made and could be graded against hand labels, the way the Framer's is. **It was cut from M1 because Reader's failures are visible:** a weak item that clears the bar shows up in the edition and gets caught by the reader in seconds. A machine eval earns its cost where failure is *silent*, which is why the Framer has one and why dedup keeps its golden set. **Revisit when either is true:** weak items start reaching editions faster than they are noticed, or Reader's judgement starts gating something automatic. Until then the existing digest is the precedent — no machine eval, and it performs well, because its reader is its eval.

### bk-two-call · Two-call grading: enumerate blind, then judge

- **Size:** M
- **Origin:** Sep-15-2026 — measured defect, fix designed and parked

The faithfulness grader misses a planted fabrication roughly one run in three, because it enumerates the *source's* sentences instead of the writing's. Fix is structural, not instructional: call 1 lists claims with the excerpt withheld entirely, so there is nothing to copy from; call 2 judges that list against the excerpt. Roughly cost-neutral — one more call against a payload that shrank by two thirds when interpretation stopped being sent. **Guarantees the right sentences get judged, not that they get judged correctly.**

### bk-negative-02 · negative-02 — an omission case

- **Size:** S
- **Origin:** Sep-15-2026 — named as the residual the code cannot reach

A non-numeric fact parked in `interpretation`, where the numeric detector cannot see it. The one hole the split leaves open.

### bk-retry · Bounded retry on a lifted claim

- **Size:** S
- **Origin:** Sep-15-2026 — trigger identified, deliberately not threshold-fitted

`lifted_claims` fired zero times in 75 claims on the clean day and fired on the unit carrying the plant exactly when the plant was missed. It detects the behaviour reliably but does not predict the outcome — a lifted verdict can still be correct, and one was. That earns a retry, not a gate: re-deriving a verdict read off the wrong document costs $0.008, while failing a build on it would fail correct runs. **Rule: any lift on a unit retries that unit, once.** Rests on n=1 for the event it fires on, so it ships with more negative cases, not before them.

### bk-usefulness · Calibrate the usefulness rubric against real thumbs

- **Size:** M
- **Origin:** Sep-16-2026 — unlocked by the pivot

The usefulness score has been reported and never gated because there was nothing to calibrate it against. Thumbs-up and thumbs-down on the Weekly Digest tab is the first real label. Until the rubric agrees with the thumbs, it stays soft.

### bk-miss-vs-notyet · Distinguish *not yet* from *never* for predictions

- **Size:** M
- **Origin:** Sep-16-2026 — explicitly deferred out of M2

The hard case in prediction scoring. Handled for now by the resolution date and a third state, **Expired-unresolved**, which is honest about a claim that was not falsifiable enough to score. Revisit once the ledger holds real entries. **The trap this avoids:** marking only Hits guarantees the track record looks good, and a hidden miss makes it worthless.

## Agents & capability

### bk-prior-art · Prior-art reconciliation

- **Size:** M
- **Origin:** carried from the pre-pivot plan (Phase 8)

Same repo access as grounding, opposite question: grounding asks *where would this fit*, reconciliation asks *have I already built it*. Recall-biased on purpose — a wrong suppression is invisible and a wrong offer costs minutes. States are none / partial / already built, and **built is terminal**: the exercise retires with a pointer to the code rather than carrying a status.

### bk-tuner · Tuner — learning from feedback

- **Size:** L
- **Origin:** Sep-16-2026 — scope deliberately kept high level and minimal

Takes the thumbs and the free-form Noise feedback and recalibrates the other agents. Carries two concepts nothing else in the plan covers: **credit assignment** (which agent caused a bad edition) and the **anti-echo-chamber guardrail** (a minimum share of items outside established priors, gating the Tuner's recommendations rather than the content). **Worth knowing:** this is also the only place in the pivot where reinforcement learning lives, so deferring it defers a stated focus area.

### bk-control-hub · Control Hub

- **Size:** L
- **Origin:** in the pivot document; blocked on data

GitHub status counts, per-agent metrics, pending actions and actions taken, a feedback form that routes to the agent or workflow it concerns, and a new-feature form that writes to this backlog. Needs weeks of editions before the metrics mean anything.

### bk-feeds · RSS/Atom polling for the tiered domains

- **Size:** S
- **Origin:** Sep-16-2026 — M1 if trivial, here if not

Most of the 91 domains publish feeds. Polling one is cheap and deterministic, and it is the closest thing to an event trigger a newsroom offers — webhooks effectively do not exist in this space. `/add-news` is the genuinely event-driven path and fetches immediately.

## Platform & cost

### bk-tokens-css · tokens.css and a local build step

- **Size:** M
- **Origin:** Sep-15-2026 — friction hit repeatedly in one session

Today every JSON edit needs a round-trip through Claude Design to regenerate the page, because only Design knows the styling. Extracting `tokens.css` and a local renderer makes a one-character change one command. The round-trip has already caused one stale-number regeneration.

### bk-mcp · MCP as Reader's fetch layer

- **Size:** M
- **Origin:** Sep-16-2026 — the one concept the pivot leaves uncovered

Nothing in the current plan uses MCP, which makes it the only entry in the concepts reference that stays purely theoretical. Reader fetching sources through an MCP server rather than bespoke HTTP is a small, real use with a natural boundary.

### bk-facade-removal · Point the eval harness at budget and llm directly, and delete the façade

- **Size:** S
- **Origin:** Sep-20-2026 — created deliberately during the row 6 extraction, as the cost of not breaking the harness

`agents/framer.py` re-exports twelve names purely so `run_evals.py` can keep reaching them through `framer.X`. That is why the file only went 826 to 797 lines while losing an entire concern. The shim was the right call at the time: every one of those twelve call sites is on an API path, so removing them without it would have broken the paid run while `--no-api` stayed green, which is exactly how the last config break hid. **It is still debt.** Two modules now own the plumbing and a third pretends to, so a reader cannot tell from `framer.py` which names are its own. Repoint the harness's imports and the twelve aliases delete themselves.

### bk-railway-config-migrate · Move railway.toml to Railway's new config before 2026-12-01

- **Size:** S
- **Origin:** Oct-9-2026 — deprecation warning from `railway link`

The Railway CLI warns that config as code (`railway.toml`) is deprecated and stops working on 2026-12-01; `railway config migrate` produces its replacement. That file holds the healthcheck that keeps a malformed `plan.md` or `roadmap.md` from taking the site down: without it, a deploy that refuses to start replaces the live one instead of being rejected. **Migrate it before that date**, then prove it again: push a deliberately broken file and confirm the previous deployment keeps serving.

## Product feature backlog

### bk-pfb · This is the only product feature backlog

- **Size:** —
- **Origin:** Sep-16-2026 — resolved to a single source of truth

Feature requests raised in the Control Hub land here. They do not live in the Archive tab, and the Archive tab holds content and predictions only. Two backlogs drift, and then neither is true.
