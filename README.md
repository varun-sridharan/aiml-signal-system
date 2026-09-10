# aiml-signal-system

A multi-agent system that turns the AI/ML firehose into a daily signal brief and weekly hands-on exercises. The goal is to kill FOMO rather than feed it: success is the fewest items you'd regret missing, not the most items covered — the system is explicitly allowed to declare a quiet day and tell you to go build.

---

## Status: one agent built, five designed

This repo is a **design with one vertical slice built end to end**, not a finished six-agent system. Being precise about that up front, because the architecture section below describes more than the code does.

| | Built and running | Designed, not built |
| --- | --- | --- |
| **Agents** | Framer (`agents/framer.py`, 674 LOC) | Scout, Gatekeeper, Coach, Tuner, Orchestrator |
| **Evals** | Framer's offline harness (`data/evals/run_evals.py`, 473 LOC) — faithfulness gate, structural gate, usefulness rubric | Evaluator / Prescriber / Control Room as separate services |
| **Infrastructure** | Append-only cost ledger + circuit breaker, frozen golden cases, non-zero exit gating | Multi-agent orchestration, feedback-driven recalibration |

The slice that exists was chosen deliberately: the agent whose output a human actually reads, plus the harness that can stop it from shipping. Everything else is easier once those two exist, and much harder to retrofit afterwards.

---

## What runs today

### The Framer

Takes a verified pool of items and a user profile, and writes the daily brief — why each item matters, 90-second paper summaries, the thread connecting them, and the quiet-day path when nothing clears the bar. `(profile, data) → output`; no personal facts are hardcoded in prompts.

It ships with its own guardrails rather than relying on the eval to catch things later:

- **Faithfulness self-check** — a second, cheaper model regrades the generated brief against the source pool and returns a claim-by-claim verdict (`check_faithfulness`, `suspicious_unsupported`, `normalise_verdict`). Omission and fabrication are treated as different failures.
- **Cost ledger and circuit breaker** — every API call is appended to `data/state/usage.json` with tokens and estimated cost, and month-to-date spend is checked against `MONTHLY_BUDGET_USD` *before* a run starts, not reconciled after (`record_call`, `month_to_date_spend`).

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
application/       Surfaces — Signal (the daily brief) · Control-Hub (private telemetry)
                   each is <name>.json (content contract) → <name>.html (generated)
config/            Hand-authored — profile.json · scout-sources.md
data/
  verified/        Gatekeeper-shaped input pool  → gatekeeper_YYYY-MM-DD.json
  briefs/          Framer output                 → framer_YYYY-MM-DD.json
                   one real generated brief is committed, for 2026-08-04
  state/           usage.json (live cost ledger) · metrics.json (seed) · backlog.json
  evals/           run_evals.py + golden/<date>/ (frozen input + output + hand labels)
design/            System_Design.json → System-Design.html — design record & decision log
plan/              Plan.json → Plan.html — rolling build plan, one ~60-min phase per session
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

python data/evals/run_evals.py --no-api      # structural gates only, no API call
python data/evals/run_evals.py               # full harness, ~$0.02
```

## Notes

Single-user today, but every record carries `user_id` and agents take `(profile, data) → output` with no personal facts hardcoded in prompts — so going multi-tenant is a data change, not a rewrite.

`config/profile.json` is an example profile. Replace it with your own goals, interests and categories; nothing in the agent code depends on its contents.

`application/Control-Hub.html` and `data/` hold personal telemetry — cost, ratings, backlog. They stay out of anything made public.

Built in timeboxed ~60-minute sessions, each one planned and committed as a phase — `plan/Plan.json` is the rolling record and `prompts/` holds the canonical prompt for each phase. That workflow is itself part of the experiment.
