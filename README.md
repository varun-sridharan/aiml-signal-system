# aiml-signal-system

A multi-agent system that turns the AI/ML firehose into a daily signal brief and weekly hands-on exercises. The goal is to kill FOMO rather than feed it: success is the fewest items you'd regret missing, not the most items covered — the system is explicitly allowed to declare a quiet day and tell you to go build. It does two jobs. First, awareness: a short daily brief that explains *why* each item matters, with the thread connecting them. Second, skill: a weekly 60-minute exercise grounded in your own repos, so reading turns into doing.

## The five agents

Five production agents on a pipeline:

| Agent | Purpose | You touch it via |
| --- | --- | --- |
| **Scout** | Discover + ingest candidate items across your interest graph | — |
| **Gatekeeper** | Verify (provenance, corroboration, dedup, reputation, materiality) and kill noise | Suppressed drawer |
| **Framer** | Write the daily brief — why-it-matters, 90-second papers, the thread, quiet-day | News |
| **Coach** | Design weekly 60-minute homework grounded in your repos | Backlog |
| **Tuner** | Learn your taste over time and recalibrate the other agents | Your feedback |

## The eval subsystem

Measurement is kept separate from fixing:

**Evaluator** (read-only sensor — scores each agent) → **Prescriber** (turns diagnoses into recommended actions, each with a predicted impact and confidence) → **Tuner** (the actuator that recalibrates agents) → **Control Room** (reports results and asks you questions, whose answers become new gold labels).

Each agent has its own metric: Scout on recall, Gatekeeper on precision/recall (biased toward recall, since false negatives are FOMO), Framer on faithfulness (a hard zero-hallucination gate) plus usefulness, Coach on code-grounding plus doability, and the Tuner on the slope of every other metric over time. A standing diversity guardrail prevents the Tuner from building a filter bubble by only feeding you what you already believe.

## Structure

```
Signal.html        Public-safe — the daily News brief
System-Design.html Public-safe — living design record & decision log
Control-Hub.html   Private surface — Control Room (eval metrics, pending actions, questions) + Backlog (weekly homework)
Plan.html          Rolling build plan, one ~60-minute phase per session
data/              Source-of-truth JSON, one record per day; every record carries user_id
design/            News-reference.html — the Bento layout/voice reference the Framer is built against
docs/              plan + prompts (docs/prompts/ holds the canonical Claude Code prompt for each phase)
```

Inside `data/`:

- `profile.json` — the user profile: goals, interests, categories, preferences, Tuner state
- `YYYY-MM-DD.json` — one immutable record per day (currently `2026-08-04.json`): items, thread, counts
- `backlog.json` — homework items and accomplishments
- `metrics.json` — agent metrics, guardrails, pending actions, prediction ledger, open questions

Data is separated from presentation by design: agents write JSON to `data/`, and the pages render it. Wiring the pages to read from `data/` is a later phase — today they hold the same content inline.

**`data/` holds seed/sample values until the system runs.** The metrics are placeholders; real numbers need several weeks of usage and feedback before any trend means anything. The pages show the shape of what you'll see, not real measurements.

## Notes

Single-user today, but every record carries `user_id` and agents take `(profile, data) → output` with no personal facts hardcoded in prompts — so going multi-tenant later is a data change, not a rewrite. The repo is private; `Control-Hub.html` and `data/` hold personal telemetry and are excluded if the rest is ever made public.
