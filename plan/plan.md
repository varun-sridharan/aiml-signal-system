---
title: Plan & Progress
kicker: AI/ML Signal · build plan
lede: One milestone at a time, in detail, with everything it rests on in a single table. Later milestones live in the Roadmap; every decision and when it was made is at the bottom.
footer: Plan · the live milestone and the archive of completed work for `aiml-signal-system`. Public-safe.
updated: 2026-09-20
---

<!--
Content source for the Plan & Progress page. **Two sections only, and that is the whole
structure:** M1 — collapsible, holding a Plan table and a Status paragraph — and Decision
Log. The Plan table carries completed and outstanding work in one list; prerequisites are
rows marked done, never a separate section. Status is five sentences: what is finished,
what is being built, what follows, the estimate, the contingency. Decision Log entries
are datestamped, newest first, and none runs past five sentences. Cross-cutting
infrastructure belongs in System Design; future milestones belong in Roadmap & Backlog.
Neither is duplicated here.
-->

<!--
The current-status banner. Removed 2026-09-17. It was a banner restating what
the Status section now says, and two statuses drift the moment one is updated and the
other is not. Status lives in exactly one place: inside the milestone it describes.
-->

# M1 · Reading, Writing & Hosting

**End state:** Reader and Framer running on Railway with no local dependency, both gated, producing the weekly debrief at a URL. **Exit is binary:** the canonicalisation gate green including a planted duplicate; faithfulness and structural gates green on a golden week including a planted fabrication; the Weekly Digest tab rendering the debrief; thumbs captured.

## Plan

Everything this milestone rests on, complete and outstanding in one table. The four rows marked done were finished before the pivot, and all four carried whole — nothing was partially reused. One row carries an ordering constraint rather than just a status: the storage layer must land before the hosted system makes a paid call.

| Activity | Outcome | Why it matters | Status |
|---|---|---|---|
| The Claude Code loop and the repo | Scaffolding, README, first commit, and the JSON-content → Claude Design → HTML pipeline every page still uses | This is the method the milestone is built with, not an artifact it produced. The pivot did not touch it. | DONE |
| The Framer agent | `agents/framer.py`, `framer-prompt.md`, `framer-contract.md`, structured output, prompt caching, model routing, the cost breaker | M1 ports this unchanged. The pivot edited two profile key names and a docstring, and nothing else. | DONE |
| The eval harness and golden set | `run_evals.py`, two frozen cases with hand labels, faithfulness and structural hard gates, a measured baseline | M1's exit criteria are written in terms of these gates, and the baseline is the only thing the next change can be compared against. | DONE |
| The facts / interpretation split | Framer emits `split` and `threadSplit`; only factual clauses reach the grader; coverage is a hard gate | Took false positives on the clean day from 18 to 0, structurally — flagged clauses are no longer sent, so they cannot be flagged. | DONE |
| Deploy the skeleton on Railway | App, Postgres and scheduler live at `aiml-signal-system-production.up.railway.app`; all five pages serve from it with no local dependency; `/health` reports the deployed commit | Everything else assumes a host. It also dissolves the constraint the old digest was built around — that a scheduled run cannot write to its own page. | DONE |
| Extract the shared plumbing | `agents/budget.py` and `agents/llm.py`, with the breaker upgraded to stop-*before* | Reader is the breaker's second caller. Extract at the second use — not the first, which is premature, and not the third, by which point there are copies. | DONE |
| Storage layer, with the excerpt rule | Postgres for items, sources, graph, runs and feedback; blob storage for excerpts, **written once at fetch time and never re-fetched** | The faithfulness gate compares a span against the stored excerpt — re-fetch later and a publisher's edit turns the gate red for no reason. **And it must land before the first hosted run that spends money.** The cost breaker reads `usage.json`, a file rebuilt from the repository on every deploy, so on Railway its memory of the month's spend silently reverts to whatever was last committed. It would not crash or warn — it would simply keep authorising runs. | TO DO · blocks paid runs |
| Reader — Scout and Gatekeeper merged | Canonicalisation, dedup, provenance and corroboration; seeded with 91 tiered domains; `/add-news` fetches on arrival. **Fetching is delegated, not reimplemented.** | Those 91 rows were tested through a hosted fetcher with its own addresses and rendering. Reimplementing `fetch()` would mean re-earning a result that already exists. | TO DO |
| Reader's canonicalisation gate | A golden set of URL pairs that must and must not collapse, including a planted near-duplicate. Free, deterministic, no model | Dedup silently merging two *different* articles is Reader's only invisible failure. Everything else it gets wrong, you see in the edition. | TO DO |
| Port the Framer and its gate | The Framer writes Learning for the Week and This Week's Stories on the hosted system; the existing golden cases run against it | **Port it, do not extend it.** Moving a component and changing it at once means a red run cannot be attributed to either. | TO DO |
| The Weekly Digest surface | Two subsections with a sticky sub-nav, thumbs up and down, and a free-form box on thumbs-down | The first real label this system has had. The usefulness rubric has been reported and never gated because there was nothing to calibrate it against. | TO DO |

## Status

**Six of eleven activities are done.** The first five carried in from before the pivot: the build method, the Framer, the eval harness, and the facts/interpretation split that took false positives on the clean day from 18 to 0, plus Railway, which is now live and serving all five pages from a URL with the docs rendering from JSON at request time. **The sixth is the shared plumbing**, extracted this session into `agents/budget.py` and `agents/llm.py` at the moment Reader became the second caller, with the spend breaker upgraded from stop-after to stop-before so the call that crosses the ceiling is refused rather than merely noticed. **Next come** the storage layer with its write-once excerpt rule, which blocks paid runs until it exists, and then Reader, with fetching delegated to the same hosted fetcher the existing digest already uses rather than reimplemented against 91 sites. **The remaining estimate is 4 to 6 sessions**, and the risk concentrates in Reader's canonicalisation gate, whose failure mode is the only one in this milestone that is invisible in the finished edition.

# Decision log

### 2026-09-20 · The spend breaker now refuses the call that would cross the ceiling

Extracting `budget.py` and `llm.py` was scheduled for the moment Reader became the second caller, and the extraction surfaced that the breaker had been asking the wrong question. It tested whether month-to-date spend had *already* passed $20, which can only be true after the crossing, so the call that went over was always allowed through. It now adds the pending call's worst case to month-to-date first and refuses if the sum would exceed the ceiling, naming spent, estimate and ceiling in the error. **One half of the guarantee is still soft:** the pre-flight check is unconditional inside `llm.call()`, but recording is skipped when no ledger is passed, and an unrecorded call silently raises the ceiling for every call after it. That is logged as `bk-ledger-required` rather than patched here, because closing it means changing how the eval harness books its own calls.

### 2026-09-17 · The site serves from a URL, and the root mount had to be guarded

FastAPI, a Procfile and two dependencies — no database, no agents, nothing else, so that a later failure could be attributed to one change. The static root is the repository root, because the pages link across `application/`, `design/` and `plan/` and folder-level mounts would break every cross-page link. That mount also served `.env` over HTTP on a local run, a live API key to anyone who asked, so any path segment beginning with a dot now returns 404. `/health` reports the deployed commit, which proved on its first use that the running build was the pushed one rather than a cache.

### 2026-09-17 · The storage layer gates paid runs, not just persistence

Deploying to Railway exposed something the local system never had: a container's filesystem is rebuilt from the repository on every deploy, and the cost breaker reads its spend ledger from a committed file. Hosted, that ledger reverts on each deploy and the breaker keeps authorising runs against a month it thinks is emptier than it is. No crash, no warning. The storage layer was already planned; what changed is that it now blocks the first paid run rather than merely preceding it.

### 2026-09-17 · The plan carries one milestone and one log

Prerequisites, cross-cutting infrastructure and the appendix came out. Completed work now sits in the same table as outstanding work, marked done, because a reader wants one list of what the milestone rests on rather than three sections to reconcile. Cross-cutting infrastructure moved to System Design, where the architecture lives.

### 2026-09-17 · Reader delegates fetching rather than reimplementing it

The 91 source domains were tested rather than assumed, but tested through a hosted fetcher with its own addresses, user-agent and rendering. A Python process on Railway has none of that, and datacenter addresses are blocked harder. Reader calls the same fetcher instead, which took the milestone from 7–9 sessions to 5–7.

### 2026-09-17 · Reader's eval is deliberately thin

Only canonicalisation gets a golden set, because Reader's other failures are visible — a weak item shows up in the edition and is caught in seconds. A machine eval earns its cost where failure is *silent*, and dedup merging two different articles is the one silent case. The existing digest is the precedent: no machine eval, and it performs well, because its reader is its eval. Corroboration labelling moved to the backlog with a trigger for revisiting it.

### 2026-09-16 · Weekly, and the two clocks are separate

A daily brief optimises for missing nothing, and what it produces is a queue — which is why the nightly predecessor was retired despite working as designed. Monthly is too slow for a field that moves weekly. Ingestion runs continuously because it is deterministic and nearly free; judgement runs weekly because judgement is what costs money.

### 2026-09-16 · Half-built surfaces leave the site rather than wearing a badge

Practice and Control Hub are off the navigation until their milestones, and their pages were deleted rather than badged. A surface reading *soon* on one page while showing real content on another was live, and that inconsistency is the reason for the rule. Their content contracts stay in the repository.

### 2026-09-15 · The grader was failing in both directions, and a new case found it

Every golden unit expected nothing to be flagged, so a grader that flagged nothing passed everything. A case with a fabrication planted on purpose failed on its first run, and measuring five runs per case found 18 false positives on the clean day and the plant missed three times in five. Splitting the Framer's output into checkable claims and its own reading, then sending only the claims, took false positives to zero. The missed-fabrication half is still open and its fix is designed.

### 2026-08-19 · The eval harness lands, and the grader goes under test itself

`run_evals.py` runs faithfulness as a hard gate, structural assertions free, and a usefulness rubric that reports and never blocks. The grader's original bug was scope rather than carelessness: shown the excerpt first, it enumerated the *excerpt's* claims and checked them against themselves. Usefulness stays ungated until there are real ratings to calibrate against.

### 2026-08-09 · The Framer is built and its voice passes

Seven items, two marked DO THIS, about five minutes to read, $0.10 a run, batched into two API calls. The thread carries a real correlation and reaches its own conclusion rather than summarising. Faithfulness was set as a hard zero-hallucination gate and usefulness as a soft learned target, which is the split everything since has been built on. The run also produced the first false positive, which became the first golden case.

### 2026-08-09 · First commit, and the habit that came with it

Deliberately low-stakes — a README and a commit — to learn the Claude Code loop before touching agents. Each step was approved individually rather than waved through, which is the right habit while learning what the tool does unprompted.
