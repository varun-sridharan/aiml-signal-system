# Autopilot

Unattended Claude Code runs that build plan rows overnight. Everything about them lives in this
folder, and nothing about them lives anywhere else.

| Folder | What goes in it | Name |
|---|---|---|
| `runbooks/` | The instructions for one run | `YYYY-MM-DD-<slug>.md`, dated the day it was written |
| `debriefs/` | The morning report a run writes | `YYYY-MM-DD.md`, dated the morning it is read |
| `evidence/` | Raw output behind every number a debrief claims | `evidence/YYYY-MM-DD/`, matching the debrief |

**Standing rules.** `runbooks/2026-10-17-m1-build.md` holds the permissions, prohibitions, setup, push
command, review loop, stop rules and debrief format that every run inherits. A later runbook lists only
its work and any rule it changes.

**Every runbook starts with a "Starting this run" section holding one `/goal` command.** That single
paste starts the run and sets the condition for finishing it. Claude Code cannot type its own slash
commands, so a runbook without that section is incomplete.

**Eval results stay with the evals** (`data/evals/`), because they are the product's history, not the
run's. Debriefs cite them by path.
