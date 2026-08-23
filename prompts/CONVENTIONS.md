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

## 4. Record decisions in TWO places

Every decision, tradeoff, or prioritization gets recorded in both:

- **`Plan.html`** — in the phase it relates to (status + phase notes).
- **`System-Design.html`** — the append-only **Decision Log**, plus the relevant architecture section (e.g., the per-agent concept map or the eval loop) whenever the decision changes the design.

Updating only the plan is incomplete. The Plan tracks *what we're doing and when*; the System page is the durable *why* — the design record. A decision that isn't in the System page effectively didn't happen for anyone reading the repo later.

## 5. Update the Knowledge Hub concepts reference (after every session)

After every session, add any AI/ML or agentic-architecture concepts we covered to the project-agnostic reference at `~/Desktop/Personal/Knowledge Hub/AI-ML-Concepts.html` (note: `Desktop/Personal/`, **not** under `Desktop/Projects/` — it is outside this repo and outside Claude Code's working directory, so it is maintained from the Cowork session, not by Claude Code) — each concept gets: one-liner, example, a visual, why it matters, and an "Applied" block with real code + a worked explanation. This file lives **outside** this repo (it grows across all projects), but maintaining it is part of the session ritual.
