# p1 — Scaffold README + first commit

**Phase:** 1 · **Activity:** flesh out the README and make the first commit/push.
**Run:** paste the prompt below into `claude` from inside the `aiml-signal-system` repo.

---

## Prompt (canonical)

```
Review the files in this repository, then:

1. Rewrite the repository ROOT readme — ./README.md (NOT prompts/README.md,
   which must be left unchanged) — to describe the project clearly:
   - One-paragraph summary: a multi-agent system that turns the AI/ML firehose
     into a daily signal brief and weekly hands-on exercises, designed to kill
     FOMO and turn news into skill.
   - The five agents and their purpose: Scout (discover + ingest), Gatekeeper
     (verify + filter noise), Framer (daily brief), Coach (weekly homework
     grounded in my repos), Tuner (learns my taste, recalibrates the others).
   - The eval subsystem: Evaluator (sensor) -> Prescriber -> Tuner (actuator)
     -> Control Room.
   - The folder structure: Signal.html (News + System), Control-Hub.html
     (Control Room + Backlog), data/ (source-of-truth JSON, one record per day,
     user_id on every record), design/ (Bento reference), docs/ (plan + prompts).
   - A note that data/ holds seed/sample values until the system runs.

2. Do NOT invent features or files that aren't in the repo. Describe only what
   exists.

3. Stage all files, make a single commit with the message
   "Scaffold Signal system: pages, data layer, design + docs", and push to origin.
```

---

## Milestone / done when

- `README.md` accurately describes the project and the actual folder structure.
- All scaffolding files are committed and pushed; the repo on GitHub shows them.

## Notes (decisions/tradeoffs — maintained by Claude)

- Repo is private; when public later, exclude `Control-Hub.html` + `data/` from the public build (they hold personal telemetry).
- This is deliberately low-stakes to learn the Claude Code approve-edits loop before building agents.

## Generated with (for reproducibility)

- **Model:** Claude Opus 5 (via Claude Code v2.1.222)
- **Result commit:** `e1661d7` on `origin/main`
- **Deviation from prompt:** Claude Code also appended `.DS_Store` to `.gitignore` (macOS hygiene) — outside the prompt's stated scope but correct. Not added to the prompt body since it's environment-specific, not part of the intended output.
