# Coding conventions

Standing rules that every agent build prompt in `docs/prompts/` must include, so generated code is consistent and teaches as it runs.

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

## 2. Reproducibility

- Prompts in `docs/prompts/` are canonical and holistic (overwritten wholesale, never diffed).
- Record the model used + result commit at the bottom of each prompt file.
- Git-committed code is the true source of truth; the prompt is the recipe.
