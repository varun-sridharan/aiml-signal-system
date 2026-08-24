# Coding conventions

Standing rules that every agent build prompt in `prompts/` must include, so generated code is consistent and teaches as it runs.

## 1. Comment every code block with intent + concept

Each meaningful code block in an agent must carry a comment that states **two things**:

1. **What this block does** — in plain language.
2. **Which agentic-architecture concept it uses** — e.g., harness (verification, tool use, context injection, structured I/O), loop (feedback, reflection, retry), credit assignment, guardrail, eval/grader, model routing.

Example:

```python
# WHAT: corroborate each item against >= 2 independent sources before it passes.
# CONCEPT: harness — verification layer (don't trust a single source).
def corroborate(item, sources):
    ...
```

Keep comments short. The goal is that reading the code teaches the concept behind it.

## 2. One file per concern: prompt vs contract

Each agent gets two files in `prompts/`:

- **`<agent>-prompt.md`** — the system prompt, **verbatim and nothing else**. The agent reads the whole file; no markers, no extraction.
- **`<agent>-contract.md`** — the input/output contract and the hard rules enforced in code.

Why: a prompt-only change should produce a prompt-only diff, and voice variants map as N prompt files against one shared contract. Change the contract and the prompt in the **same commit** — the structural assertions in the eval harness are what catch them disagreeing.

## 3. Reproducibility

- Prompts in `prompts/` are canonical and holistic (overwritten wholesale, never diffed).
- Record the model used + result commit at the bottom of each prompt file.
- Git-committed code is the true source of truth; the prompt is the recipe.

## 4. Content and design are separate — edit only the content

Every document is **JSON content + a design generated in Claude Design**. The JSON is the source of truth; the HTML is generated and **never hand-edited**. If they disagree, the JSON wins.

| Document | Content (edit this) | Page (generated) |
|---|---|---|
| Concepts reference | `~/Desktop/Personal/Knowledge Hub/Agentic_Concepts.json` | `Agentic-Concepts.html` |
| Build plan | `plan/Plan.json` | `plan/Plan.html` |
| Design record | `design/System_Design.json` | `design/System-Design.html` |
| News | `application/Signal.json` (contract) + agent output under `data/briefs/` | `application/Signal.html` |
| Control Hub | `application/Control-Hub.json` (contract) + `data/state/` | `application/Control-Hub.html` |

Set `lastUpdated` in the JSON whenever it changes, so a stale page is visible at a glance.

**Two classes of document, and they behave differently:**

- **Static docs** (Concepts, Plan, System-Design) — content changes weekly-ish, by Claude. Regenerated on demand in Claude Design.
- **Live surfaces** (`Signal.html`, `Control-Hub.html`) — render *agent output* that changes daily, so regeneration is impossible. Claude Design delivers a **renderer with a data slot**, and the design is **locked at Phase 5** before the backend is wired. Locking freezes the data contract the template reads, not just the visuals.

## 5. Record decisions in TWO places

Every decision, tradeoff, or prioritization gets recorded in both:

- **the plan** — in the phase it relates to (status + phase notes).
- **the design record** — the append-only **Decision Log**, plus the relevant architecture section (e.g. the per-agent concept map or the eval loop) whenever the decision changes the design.

Updating only the plan is incomplete. The plan tracks *what we're doing and when*; the design record is the durable *why*. A decision that isn't in the design record effectively didn't happen for anyone reading the repo later.

## 6. Rituals — when to remind Varun to regenerate

Claude edits JSON; Varun regenerates the pages in Claude Design. Two triggers:

- **End of every session** — if `Agentic_Concepts.json` changed, remind him to refresh the concepts page.
- **End of every phase** — remind him to regenerate **all** static docs whose JSON changed: concepts, plan, and design record. A phase boundary is when the plan and decision log always move, so this is the natural checkpoint.

Reminder format:

> 📋 **Regenerate:** `[files]` changed — refresh in Claude Design and re-download the HTML.

Note the concepts reference lives at `Desktop/Personal/`, **not** under `Desktop/Projects/` — outside this repo and outside Claude Code's working directory, so it is maintained from the Cowork session, not by Claude Code.
