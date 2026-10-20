# Autopilot

Unattended Claude Code runs that build plan rows overnight. Everything about them lives in this
folder, and nothing about them lives anywhere else.

| Path | What it holds |
|---|---|
| `M<n>.md` | **One file per milestone**: the standing rules, every run's instructions, and every run's debrief |
| `evidence/YYYY-MM-DD/` | Raw output behind every number a debrief claims, dated like the debrief |

**Inside `M<n>.md`** the order is fixed: how the file works, the standing rules, one `## Run N` section
per run, then the debriefs. A new run's section is written before it starts. It opens with a
"Starting this run" section holding **one `/goal` command**, the single paste that starts the run and
sets its finish condition (Claude Code cannot type its own slash commands, so a run section without it
is incomplete). An empty debrief block with that run's date markers is added at the same time.

**A run writes only inside its own debrief markers**, and only one run happens at a time.
Debriefs are historical records: once a run ends, its debrief is not rewritten.

**A new milestone starts a new file.** `M2.md` copies M1's standing rules as its starting point, so a
rule change in M2 never alters what M1's runs were told.

**Eval results stay with the evals** (`data/evals/`), because they are the product's history, not the
run's. Debriefs cite them by path.
