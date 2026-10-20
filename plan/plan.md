---
title: Plan & Progress
kicker: AI/ML Signal · build plan
lede: One milestone at a time, in detail, with everything it rests on in a single table. Later milestones live in the Roadmap; every decision and when it was made is at the bottom.
footer: Plan · the live milestone and the archive of completed work for `aiml-signal-system`. Public-safe.
updated: 2026-10-20
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

**End state:** Reader and Framer running on Railway with no local dependency, both gated, producing the weekly debrief at a URL. **Exit is binary:** the canonicalisation gate green including a planted duplicate; faithfulness and structural gates green on a golden week including a planted fabrication; the Weekly Digest tab rendering the debrief; thumbs captured; the story video's M1 chapter approved.

## Plan

Everything this milestone rests on, complete and outstanding in one table. The five rows marked done that were finished before the pivot all carried whole — nothing was partially reused. One row carries an ordering constraint rather than just a status: the storage layer must land before the hosted system makes a paid call.

| Activity | Outcome | Why it matters | Status |
|---|---|---|---|
| The Claude Code loop and the repo | Scaffolding, README, first commit, and the content-file → page workflow every page uses: Plan and Roadmap render from markdown on every request, the other three pages are Claude Design snapshots | This is the method the milestone is built with, not an artifact it produced. The pivot did not touch it. | DONE |
| The Framer agent | `agents/framer.py`, `framer-prompt.md`, `framer-contract.md`, structured output, prompt caching, model routing, the cost breaker | M1 ports this unchanged. The pivot edited two profile key names and a docstring, and nothing else. | DONE |
| The eval harness and golden set | `run_evals.py`, two frozen cases with hand labels, faithfulness and structural hard gates, a measured baseline | M1's exit criteria are written in terms of these gates, and the baseline is the only thing the next change can be compared against. | DONE |
| The facts / interpretation split | Framer emits `split` and `threadSplit`; only factual clauses reach the grader; coverage is a hard gate | Took false positives on the clean day from 18 to 0, structurally — flagged clauses are no longer sent, so they cannot be flagged. | DONE |
| Deploy the skeleton on Railway | App, Postgres and scheduler live at `aiml-signal-system-production.up.railway.app`; all five pages serve from it with no local dependency; `/health` reports the deployed commit | Everything else assumes a host. It also dissolves the constraint the old digest was built around — that a scheduled run cannot write to its own page. | DONE |
| Extract the shared plumbing | `agents/budget.py` and `agents/llm.py`, with the breaker upgraded to stop-*before* | Reader is the breaker's second caller. Extract at the second use — not the first, which is premature, and not the third, by which point there are copies. | DONE |
| Storage layer, with the excerpt rule | Postgres for sources, items, the interest graph, runs, briefs and feedback, beside the spend ledger and the excerpt store. Article text is **written once at fetch time and never re-fetched**, with the database refusing any rewrite; kept forever once a published brief cites it, expired after 56 days otherwise. Items are never deleted and their identity is fixed, and their excerpt is attached once and cleared only when its text expires; provenance, briefs and feedback are append-only; `runs` records each stage's status and a pointer to its output, so a failed run resumes from its last finished stage rather than starting over | The faithfulness gate compares a span against the stored excerpt — re-fetch later and a publisher's edit turns the gate red for no reason. **And it had to land before the first hosted run that spends money.** The cost breaker read `usage.json`, a file rebuilt from the repository on every deploy, so on Railway its memory of the month's spend silently reverted to whatever was last committed. Every rule a later caller could forget is a database trigger, and every trigger test has been run once with its trigger disabled and seen to fail. Volume decides the shape: about 900 articles a week would fill the 5 GB volume within a year as fetched pages, and stays under 100 MB a year as compressed text with a retention rule. | DONE
| Reader — Scout and Gatekeeper merged | Discovers from nine feeds and APIs, canonicalises, dedups, filters against the interest graph, and fetches the bodies of the few that may surface through Claude's web fetch, booked in the ledger at about half a cent a page; provenance and corroboration are recorded per item; `/add-news` fetches on arrival (off until `ADD_NEWS_TOKEN` is set). It has run for real: about 1,750 candidates in, 17 stories fetched over three runs, one week's brief out. **Not DONE because** the plan's 91 tiered domains are not in the repository (it is seeded from the 21 publishers `config/scout-sources.md` names) and some labs have no verified feed. **Fetching is delegated, not reimplemented.** | Those 91 rows were tested through a hosted fetcher with its own addresses and rendering. Reimplementing `fetch()` would mean re-earning a result that already exists. | IN PROGRESS
| Reader's canonicalisation gate | 21 URL pairs that must and must not collapse, three of them planted near-duplicates, in `data/evals/canonical/`. Free, deterministic, no model. **Draft, awaiting Varun's review:** every pair is a judgement about what counts as the same article, so this row is DONE only after he signs the set off | Dedup silently merging two *different* articles is Reader's only invisible failure. Everything else it gets wrong, you see in the edition. | IN PROGRESS
| Port the Framer and its gate | `agents/weekly.py` feeds the shipped Framer verified items from the database and records the work as the `framer` and `gates` stages of a run, so a failed stage resumes without re-fetching. The Framer's writing code is unchanged (its grader gained a bounded retry on 2026-10-20, after the port was verified); the hard gates are now functions in `agents/gates.py` that the eval harness and the weekly run both call, and a story the grader skipped or never checked fails the gate rather than passing as clean. **One hard gate is red: `negative-01` fails because the grader flags a clean thread claim while quoting its supporting span. The planted $965B fabrication, which the grader missed on 2026-09-15 and 2026-10-18, was caught on 2026-10-20, and so were three newly added plants; a bounded retry on a lifted claim is built, but has never fired on a missed plant, so it is unproven.** Structural gates pass on both golden cases and faithfulness passes on the clean day with no false positives, so this row is not DONE. It has now run end to end on real pages and published a brief on its third attempt: the first two were stopped by the gates, correctly, on faults found in how pages were turned into excerpts | **Port it, do not extend it.** Moving a component and changing it at once means a red run cannot be attributed to either. | IN PROGRESS
| The Weekly Digest surface | `/weekly-digest` renders the newest published brief from the database at request time, with the site header and nav, two subsections (Learning for the Week, This Week's Stories) and a sticky sub-nav. Every story takes a thumbs up, or a thumbs down with an optional note, from anyone with no login; the database keeps only a salted hash of the voter's address, and a client is rate-limited. The old `/application/Weekly-Digest.html` redirects here, and a brief that cannot be shown becomes an error panel rather than a 500. A real week's brief, seven stories, renders on it | The first real label this system has had. The usefulness rubric has been reported and never gated because there was nothing to calibrate it against. | DONE
| Go live: move both capture inboxes into the repo | The Signal System runs every week alongside the scheduled Weekly Digest, which **keeps running until Varun decides to turn it off**. `news-backlog.md` and `style-guide-backlog.md` stay in project memory as the inbox; each weekly run copies that week's captures into the repo and leaves the inbox untouched while the old digest still reads it, and clears it only once the old digest is off | Capture has to keep working from a phone with the Mac closed, which project memory gives today; the system of record has to be versioned and readable by the hosted run, which only the repo gives. **Running both in parallel turns the switch-over into a comparison:** the same week, the same fetcher, two briefs side by side, and the old one is retired on evidence rather than on a date. | TO DO |
| The Framer writes in Varun's style | The Framer reads the repo copy of `style-guide-backlog.md` in its cached system prompt, stopping at its Sources used table; the usefulness judge scores against the same rules; a leakage check confirms no line from a style source reaches a brief; the golden case is run before and after | His style references only count if the brief measurably improves while faithfulness stays green. **After the port and after go-live, never during them:** changing the writer while moving it would leave a worse run unattributable. Until go-live the guide lives in project memory, where `/AGI-add-news-style` and `/AGI-add-news-style-email` keep it. | TO DO |
| Move the Railway config before 2026-12-01 | `railway.toml` is gone and the healthcheck path `/health` is now a Railway service setting. Proven again: a deliberately broken `plan.md` was pushed, its deploy failed the healthcheck, and the previous deployment kept serving throughout | That file held the healthcheck that stops a malformed plan from taking the site down, and Railway stops reading it on 2026-12-01. **Date-bound, so it was done ahead of the rest of M1.** | DONE |
| Story video: the M1 chapter (last step) | `application/story.md` gains the M1 chapter, drafted from this plan and its decision log; the video is re-exported from it; Varun approves the script, then the video; committed as `application/media/story.mp4`, tagged `story-m1`, and shown on the Product page | The video is how the system explains itself to someone new, and it only stays true if updating it is part of finishing. **M1 is not done until its chapter is approved.** | TO DO |

## Status

**Nine of fifteen activities are done.** The five carried in from before the pivot, the shared plumbing, the Railway config move, the storage layer, and now the Weekly Digest surface, which renders a real edition. **The whole pipeline has run end to end** on the live web: Reader to Framer to gates to publish, with a resume proven by the ledger (no fetch after the Reader finished) and seven stories live, but it took three tries, and the gates blocked the first two for reasons worth keeping. **Three rows are open for decisions, not for code:** Reader needs the 91-domain list the plan cites or a decision to proceed without it, the canonicalisation gate needs Varun's review, and the Framer's faithfulness gate still misses the planted fabrication in `negative-01`. **Then go-live, the style row and the story video**, all Varun's. **The remaining estimate is 1 to 3 sessions**, and the risk has moved from canonicalisation to the grader: a zero-tolerance gate over real prose will block some weeks, and a gate that misses a plant is the opposite failure.

# Decision log

### 2026-10-20 · The grader's retry is built, but nothing yet shows it fixes a miss

Any lift on a unit now retries that unit once, alone, with a note naming the lifted claims; a retry that comes back unusable keeps the first verdict, and the retry is booked and recorded in the brief. Three planted-fabrication cases were added (a changed number, a changed date, an invented attribution), all hand-built and awaiting Varun's review, and five runs per case caught every plant with the retry off and on, with no false positive on the clean day. That does not show the retry works: the grader caught the plants without it, the retry fired only on a clean control unit, and at temperature 0 the five runs were one result repeated. The same grader missed the `$965B` plant on 2026-10-18 and catches it today, and `negative-01` still fails its gate on a different unit: a thread claim it flags unsupported while quoting the supporting span.

### 2026-10-20 · A failed week is reframed as a numbered attempt, and an edition is named by what its Reader read

A finished stage can never be redone, so a week whose gates failed used to be unrecoverable under its own key, and run 1 published under an older key to get round it. A week now takes numbered attempts (`2026-W43`, then `-r2`, `-r3`), a new one starts at the Reader, and `--reuse-reader` adopts an earlier attempt's finished Reader output so a reframe fetches nothing. The edition is still stored under the bare week, which the database limits to one brief, and a second attempt at a published week is refused before it spends. The page now labels an edition by the dates its Reader read, stored with the brief, or for the append-only W40 brief derived from its publish date, and never by the run key. Failed attempts stay as evidence and the status view marks the ones a later attempt superseded.

### 2026-10-19 · The digest takes one template, hybrid depth, and shows only what is built

Varun chose the shape of the Weekly Digest from the scheduled digest's own page and the Gen Academy newsletter: a thread that ties the week together comes first, then numbered story tiles with a two-level tag and a "why it matters" line, then a closing thread. Depth is hybrid: eight to ten short tiles, with the top one or two written as longer features. Industry leaders and predictions are M2's agent and homework is M3's, so their tabs are designed now but not rendered until those agents exist, which keeps the rule against half-built surfaces. The template is designed once in Claude Design from `prompts/design-weekly-digest.md`, and is built only after Varun signs it off. The source catalogue the old digest keeps in project memory is now copied to `config/digest-sources.md`; it is the 91-domain list the plan referred to.

### 2026-10-18 · The first real run was stopped twice by the gates, and both times they were right

Run under the current week, the Reader and the resume worked, and then the gates refused to publish: one story's excerpt was an arXiv author list and site notice rather than its abstract, a headline carried a date the excerpt did not, and the grader flagged a paraphrase that distorted its source. Those were faults in how pages become excerpts, not in the gates, so the fixes were on the input side: read the abstract from the page metadata, and do not frame a story whose headline states figures or dates its excerpt lacks. A done stage cannot be reframed, so the retries ran under earlier week keys rather than the next one, which would have made Monday's real run skip itself. The third run published seven stories at about $0.22; the grader also flagged some faithful composite paraphrases, so a zero-tolerance gate over real prose will block some weeks, and that is Varun's policy to set.

### 2026-10-18 · Anyone can vote on the digest, and the database never learns who

The Weekly Digest page takes a thumbs up or down on every story with no login, because the first real label this system gets should not wait on an account system. The only thing kept about a voter is a keyed hash of their address; the key is `VOTE_SALT` if set, else derived from `DATABASE_URL`, so voting works without a new setting and a leaked table cannot be reversed by trying every address. A client is limited to 30 votes an hour, a note is allowed only with a thumbs-down, and a vote for a story that is not in the brief is refused. A stored brief that cannot be shown renders an error panel with a 200, and an unreachable database renders one with a 503, because the healthcheck guards deploys and not data. The Learning section is the stories tagged DO THIS, since the Framer does not know the page and was ported unchanged.

### 2026-10-18 · The Framer is ported unchanged, and a gate that cannot see a story now fails it

The weekly run gives the shipped Framer the same input shape the golden cases have, built from verified items in the database, and the Framer's code and prompt are untouched. Each story's `source_excerpt` is the opening few sentences of its fetched page, because the contract asks for one to three sentences and the gate grades against exactly what the writer was shown. A review found that a story the grader skipped, or one the Framer left out, would pass the faithfulness gate as clean, since the gate only reads flags; now every story must have been written, have a verdict and have at least one enumerated claim. The paid eval still fails `negative-01`: the grader misses the planted fabrication on two runs out of two, with its request unchanged since the baseline, so that recorded flaw is open rather than new.

### 2026-10-18 · The storage layer is complete, and canonicalisation stopped dropping every query string

Sources, items, the interest graph, briefs and feedback joined the ledger, excerpts and runs, with the same rule: what must not be forgotten is a trigger, and each trigger test was run once with its trigger disabled and failed. Reader's canonicalisation diverges from AGI-add-news on one point, on purpose: it drops tracking parameters (`utm_*`, click ids, `ref`) but keeps a query that names the article, because dropping all of it would merge `?id=1041` with `?id=1042`, the one dedup failure nobody sees in the finished edition. arXiv's abstract, PDF and HTML pages, with or without a version, collapse to one paper. The plan's 91 domains are not in the repository, so sources are seeded from the 21 publishers the list names and a domain it does not name is recorded as unrated, never guessed, and never surfaced alone. Anyone can vote on the Weekly Digest, so the database keeps only a salted hash of the voter's address, and a client is rate-limited.

### 2026-10-17 · Reader fetches through Claude's web fetch, and the old digest keeps running

The 91 sources were proven reachable through Claude's web fetch, and a plain Python fetcher on Railway would have to re-earn that from datacenter addresses that are blocked harder. So Reader discovers candidates from feeds and official APIs, which is free and deterministic, and fetches article bodies through the API's web fetch tool, booked in the ledger like any other call; only candidates that pass a relevance filter are fetched, which keeps the cost to a few dollars a month rather than tens. The scheduled Weekly Digest is not turned off at go-live: both run in parallel, on the same fetcher, until Varun decides the new one has earned the switch. The capture inboxes are copied rather than moved while the old digest still reads them. The ledger ceiling rises from $20 to $40 a month for the build, inside the $100 monthly API credit the Max plan includes.

### 2026-10-17 · Run checkpoints landed, and the database guards them

`runs` holds one row per week and `run_stages` one row per attempt at a stage, with pointers to that stage's output and never article text. Triggers make a done stage and a finished run write-once, allow only running to done or failed, and refuse a stage whose predecessor is not done; a partial index allows one running attempt per stage, and an attempt silent for two hours is marked abandoned so a new one can start. Every trigger test was run once with its trigger disabled and failed, which is the evidence each test can catch what it claims to. Errors are redacted before they are stored, because a stack trace is where a connection string leaks. One gap is known and left: a run row can still be inserted already done, which a resume would surface as a refusal rather than silently skip.

### 2026-10-14 · The healthcheck is a Railway setting, not a file, and the whole-project config waits

Railway stops reading `railway.toml` on 2026-12-01, and the only thing in it was the healthcheck path. Its replacement, `.railway/railway.ts` applied with `railway config apply`, manages the whole project and deletes any resource the file leaves out, which here includes the Postgres database holding the spend ledger and the excerpts; other projects also report `railway config migrate` dropping settings. For one setting that is more risk than it removes, so the file was deleted and `/health` set in the service's Deploy settings. The cost is that the setting is no longer versioned in the repo. The test was the real one: a malformed `plan.md` deployed, failed the healthcheck after about five minutes, and the previous deploy served throughout.

### 2026-10-12 · A failed run resumes from its last finished stage

A weekly run is Reader, then Framer, then the gates, then publish. If the Framer fails, the run today starts again from the Reader: it re-fetches sources that may have changed overnight, pays again for any paid step that already finished, and the retried brief may rest on different evidence than the failed one. The `runs` table therefore records each stage's status and a pointer to its output, and a rerun loads the last finished stage and continues from the next. It is decided now because `runs` is being designed in row 7, where it is a few columns and a helper; added after the Reader and Framer exist, it would be a migration plus a rework of both. It is the run-state slice of `bk-agentic-memory-architecture`; the rest of that concept waits for M2 triage.

### 2026-10-12 · The excerpt store landed, and the database enforces write-once

Excerpts are the `excerpts` table: zlib-compressed UTF-8 text with its SHA-256, one row per canonical URL and text, so a publisher's edit becomes a new row and the old text stays what earlier briefs were checked against. Triggers refuse any change except pinning, refuse to delete a cited excerpt, and keep citations append-only, so write-once no longer depends on every caller remembering it; `get()` re-hashes on every read and refuses text that no longer matches. `cite()` pins an excerpt forever, and `expire()` deletes only uncited rows past their 56-day date. Measured on the golden inputs, the 7 distinct excerpts average 301 bytes of text and 214 stored, 1.41x compression, against the design's assumption of about 6 KB at 3x. Those are curated snippets rather than article text, so they do not test the 6 KB figure, and even at 1.41x a year of 6 KB articles stays near 200 MB, well inside the 5 GB volume.

### 2026-10-12 · Excerpts are compressed text in Postgres, and only cited evidence is kept forever

Stored as fetched pages, about 900 articles a week would fill the 5 GB volume within a year. As article text, compressed, they come to under 100 MB a year. An excerpt a published brief cites is evidence and is kept for good, about seven a week; the rest expire after a few weeks, once deduplication, corroboration and prediction checks are done with them. The separate blob store is dropped, because at this volume it would add a service and a credential for nothing Postgres cannot do. Expiry only ever deletes uncited rows; a cited excerpt is never rewritten or removed.

### 2026-10-11 · Project memory is the capture inbox; GitHub becomes the record at go-live

`news-backlog.md` and the new `style-guide-backlog.md` stay in the claude.ai project for now, because that is what lets the AGI skills capture from a phone with the Mac closed. At go-live, when the scheduled Weekly Digest is turned off, both move into the repo, and from then on each weekly run carries new captures across and clears the inbox. The style guide keeps its own Sources used table instead of a separate register: one file to move, and the table is marked as bookkeeping so the writer never reads the product names in its subject lines. The style row now follows go-live, so the guide reaches the Framer from the repo rather than through a channel built only for the interim.

### 2026-10-11 · Style references are captured separately from news, and the guide holds style only

Varun's newsletters are the model for how the brief should read, so they are captured by their own skills: `/AGI-add-news-style` for a URL and `/AGI-add-news-style-email` for the newest email in his AGI News folder, which runs only after he confirms it and never changes the mailbox. Both distil a style guide in our own words rather than storing the newsletters, because the repo is public, the writing is someone else's, and a fact copied into a guide can leak into a brief as a claim no source supports. A URL can be news, style or both, and invoking both skills on it means both. The Framer adopts the guide only after it is ported, so that a change in its writing can be measured rather than confused with the move.

### 2026-10-09 · The spend ledger moved to Postgres, and every call is booked

The ledger was `data/state/usage.json`, and Railway rebuilds the repository on every deploy, so the live breaker forgot the month's spend each time and kept approving calls. It is now the `spend_ledger` table in Railway Postgres, and `llm.call()` books every call under a required phase and user, which retires both the eval harness's separate booking and the usefulness judge's bypass of the breaker. Each call reserves its worst case under an advisory lock, in the same transaction as the ceiling check, then settles to the actual cost: two runs cannot spend the same headroom, and a call that dies mid-flight keeps counting at worst case. The worst case now prices input from the request's size (characters / 3) rather than assuming `max_tokens`, which a large payload could exceed. With no reachable database every paid call is refused, and there is deliberately no fallback to a file, because two ledgers was the bug.

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
