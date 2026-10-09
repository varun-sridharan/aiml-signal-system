# aiml-signal-system

A multi-agent system that turns the AI/ML firehose into a **weekly** signal brief and hands-on exercises grounded in your own repository. The goal is to kill FOMO rather than feed it: success is the fewest things you'd regret not understanding, not the most items covered — the system is explicitly allowed to declare a quiet week and tell you to go build.

**Weekly is a decision, not a default.** A nightly predecessor ran for weeks and worked exactly as designed. It was retired anyway, in its reader's words: *“I am not finding this AI news useful as I have to react every time I see a news.”* A daily brief optimises for missing nothing, and what it produces is a queue. Reading still runs continuously — it is deterministic and nearly free. Judgement runs weekly, because judgement is what costs money.

---

## Status: one agent built, four designed — and mid-pivot

This repo is a **design with one vertical slice built end to end**, not a finished five-agent system. It is also mid-pivot: a separate weekly digest that has run for weeks is being merged onto this engine and retired. See [Roadmap & Backlog](https://aiml-signal-system-production.up.railway.app/roadmap). Being precise about that up front, because the architecture section below describes more than the code does.

| | Built and running | Designed, not built |
| --- | --- | --- |
| **Agents** | Framer (`agents/framer.py`) | Reader (Scout + Gatekeeper, merged), Thought Leadership, Coach, Tuner |
| **Evals** | Framer's offline harness (`data/evals/run_evals.py`, 473 LOC) — faithfulness gate, structural gate, usefulness rubric | Evaluator / Prescriber / Control Room as separate services |
| **Infrastructure** | Append-only cost ledger + circuit breaker, frozen golden cases, non-zero exit gating | Multi-agent orchestration, feedback-driven recalibration |

The slice that exists was chosen deliberately: the agent whose output a human actually reads, plus the harness that can stop it from shipping. Everything else is easier once those two exist, and much harder to retrofit afterwards.

---

## See it without running it

No API key needed, nothing to install:

| | |
| --- | --- |
| [The Weekly Digest](https://aiml-signal-system-production.up.railway.app/application/Weekly-Digest.html) | real Framer output for a fixed date, rendered |
| [Design record](https://aiml-signal-system-production.up.railway.app/design/System-Design.html) | architecture + an append-only decision log, 38 entries |
| [Roadmap & Backlog](https://aiml-signal-system-production.up.railway.app/roadmap) | what is next, with binary exit criteria — and every item deliberately deferred, with the reason |
| [Plan & Progress](https://aiml-signal-system-production.up.railway.app/plan) | the one live milestone in detail, plus the archive of finished work |

Prefer the raw data? [`data/briefs/framer_2026-08-04.json`](data/briefs/framer_2026-08-04.json) is the same brief as the agent produced it, and [`data/evals/golden/2026-08-04/labels.json`](data/evals/golden/2026-08-04/labels.json) is a hand-labelled eval case with the reasoning for each label written down.

## What runs today

### The Framer

Takes a verified pool of items and a user profile, and writes the edition — why each item matters, 90-second paper summaries, the thread connecting them, and the quiet-day path when nothing clears the bar. `(profile, data) → output`; no personal facts are hardcoded in prompts.

It ships with its own guardrails rather than relying on the eval to catch things later:

- **Faithfulness self-check** — a second, cheaper model regrades the generated brief against the source pool and returns a claim-by-claim verdict (`check_faithfulness`, `suspicious_unsupported`, `normalise_verdict`). Omission and fabrication are treated as different failures.
- **Cost ledger and circuit breaker** — every API call is booked in the `spend_ledger` table in Railway Postgres (`agents/schema.sql`). Before a call, its worst case is reserved in the same locked transaction as the check against `MONTHLY_BUDGET_USD`, so a call that would cross the ceiling is refused before it is made; after it, the row settles to the actual cost (`budget.reserve`, `budget.settle`). With no reachable database, no paid call is made.

### The eval harness

`python data/evals/run_evals.py`

This is the part of the repo I'd most want read. Three checks, deliberately unequal:

1. **Faithfulness (hard gate)** — regrades a frozen golden day and compares the verdict to hand-written labels, reporting false positives and false negatives against the human judgement.
2. **Structural (hard gate)** — schema and contract assertions on the generated brief.
3. **Usefulness (reported, not gated)** — a rubric score that is printed but deliberately does **not** block, because it is not yet trustworthy enough to stop a release. Gating on a metric you don't trust teaches you to ignore gates.

Two properties that matter more than the checks themselves:

- **The golden cases are frozen copies, not references.** `data/evals/golden/<date>/` holds `input.json`, `output.json` and hand-written `labels.json`. Evaluating the live files would let a Framer re-run quietly move its own baseline. Adding a day is a new directory; the runner needs no change.
- **A failing hard gate exits non-zero.** A gate that only reports is a dashboard. This one can guard a prompt change the way a failing test guards a refactor.

Flags: `--no-api` runs the free half, `--show-claims` prints the grader's full claim ledger, `--case` runs one golden day, `--skip-usefulness` drops the soft judge and halves the spend while iterating on the grader. About $0.02 a full run, on the same ledger and circuit breaker as the Framer.

---

## The design the rest of it belongs to

Everything below this line is **architecture and roadmap**, not shipped code. It's included because the design decisions are the point of the project, and because the built slice only makes sense as part of it.

### The six agents

| Agent | Purpose | Status |
| --- | --- | --- |
| **Scout** | Discover and ingest candidates across the interest graph | designed |
| **Gatekeeper** | Verify provenance, corroboration, dedup, reputation, materiality; kill noise | designed |
| **Framer** | Write the daily brief | **built** |
| **Coach** | Design weekly 60-minute exercises grounded in your own repos | designed |
| **Tuner** | Learn taste over time and recalibrate the other agents | designed |
| **Orchestrator** | Front door — classify the request and route it, collate the answer | designed |

### The eval subsystem

The intended separation, of which only the Framer's harness exists today:

**Evaluator** (read-only sensor — scores each agent, cannot change anything) → **Prescriber** (turns diagnoses into recommended actions, each with a predicted impact and a confidence) → **Tuner** (the only actuator) → **Control Room** (reports results and asks questions whose answers become new gold labels).

The reason for the split is an incentive problem, not an architectural preference: if the component that measures can also change things, the metric improves and the product does not.

Each agent is intended to carry its own metric rather than inherit a system-level average — Scout on recall, Gatekeeper on precision/recall biased toward recall (a false negative is FOMO), Framer on faithfulness as a hard gate plus usefulness, Coach on code-grounding and doability, Tuner on the slope of every other metric over time. A standing diversity guardrail is specified to stop the Tuner optimising into a filter bubble; it is part of the design, not yet enforced by code.

`data/state/metrics.json` carries this as `"state": "seed"` — every value is `null`. Real numbers need weeks of usage before a trend means anything, and placeholder metrics that look real are worse than empty ones.

---

## Structure

Folders are organised by **role**; data files are named after the agent that produced them, so the producer is obvious at a glance. The same file is one agent's output and the next agent's input.

```
agents/            Agent code — framer.py today
application/       Surfaces — Signal (the daily brief) · Control-Hub (private surface, sample data)
                   each is <name>.json (content contract) → <name>.html (generated)
config/            Hand-authored — profile.json · scout-sources.md
data/
  verified/        Gatekeeper-shaped input pool  → gatekeeper_YYYY-MM-DD.json
  briefs/          Framer output                 → framer_YYYY-MM-DD.json
                   one real generated brief is committed, for 2026-08-04
  state/           metrics.json (seed) · backlog.json — the cost ledger is in Postgres, not here
  evals/           run_evals.py + golden/<date>/ (frozen input + output + hand labels)
design/            System_Design.json → System-Design.html — design record & decision log
plan/              plan.md · roadmap.md → rendered at /plan and /roadmap on every request;
                   the format is plan/FORMAT.md and a check refuses a malformed file
prompts/           Canonical prompt per phase + CONVENTIONS.md
                   <agent>-prompt.md   = that agent's system prompt, nothing else
                   <agent>-contract.md = its input/output contract + hard rules
```

Data is separated from presentation by design: agents write JSON to `data/`, the pages render it. Wiring the pages to read from `data/` is a later phase — today they hold the same content inline.

## Documents — content vs design

Every page here is **JSON content rendered by a generated design**. The JSON is the source of truth; the HTML is generated and never hand-edited. Static docs (build plan, design record) are regenerated on demand at the end of every phase. Live surfaces render agent output that changes daily, so they get a renderer with a data slot instead, and their design is locked before the backend is wired. See `prompts/CONVENTIONS.md` §4 and §6.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-..." > .env

python data/evals/run_evals.py --no-api      # structural gates only, no API call, no database
python data/evals/run_evals.py               # full harness, ~$0.02
```

Any run that calls the API books it in the spend ledger, a Postgres table, and refuses to start without one. The project's database is private on Railway; locally it is reached through a tunnel on a fixed port:

```bash
railway connect Postgres --tunnel-only --port 15432   # leave running in another terminal
# .env: DATABASE_URL=postgresql://<user>:<password>@127.0.0.1:15432/<db>
python -m unittest tests.test_ledger -v              # the ledger's tests; no API calls
```

Your own deployment needs only a `DATABASE_URL` pointing at any Postgres; the table is created on first connect.

## Notes

Single-user today, but every record carries `user_id` and agents take `(profile, data) → output` with no personal facts hardcoded in prompts — so going multi-tenant is a data change, not a rewrite.

`config/profile.json` is a **problem statement, not a settings file** — it describes the reader the system is solving for, and every agent takes it as input rather than having any of it baked into a prompt. That is the seam personalization runs through: the pipeline up to the Framer is identical for every reader, and only the framing step is per-person. Replace it with your own and nothing in the agent code changes.

`data/` is committed on purpose, so the repo can be read without running it: `metrics.json` is seed-state with every value `null`, and `data/evals/golden/` holds a real frozen case with hand-written labels. The Control Hub is the surface that stays private in the design — where a real user's ratings, spend and pending actions would live. It is not built and not linked; its content contract is kept in `application/Control-Hub.json` for the milestone that builds it.

Built in timeboxed ~60-minute sessions, each one planned and committed as a phase — `plan/plan.md` is the rolling record and `prompts/` holds the canonical prompt for each phase. That workflow is itself part of the experiment.
