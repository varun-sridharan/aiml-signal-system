# Prompt library — canonical Claude Code prompts

This folder holds the **final, canonical** Claude Code prompt for each build activity. One file per activity, named by phase (e.g., `p1-scaffold-commit.md`).

## Rules

1. **Holistic, not incremental.** When a prompt changes, the file is **overwritten wholesale** to reflect the current canonical version. We never keep a changelog of edits inside the prompt file — the file always represents the one prompt you would run today.
2. **One activity, one file.** Each file is self-contained: paste it into `claude` and it produces the intended code with no external context needed beyond the repo.
3. **Reproducibility is best-effort, not bit-exact.** LLM code generation is not deterministic — the same prompt and model can produce slightly different output, and models update over time. Therefore:
   - The **git-committed code is the true source of truth.**
   - The prompt file is the **canonical recipe** that produced it.
   - Pin the model where possible and keep the prompt current so a re-run reproduces *equivalent* code.

## Index

- `p1-scaffold-commit.md` — Phase 1: flesh out README + first commit/push.
- (later phases add their prompt files here as they're reached)
