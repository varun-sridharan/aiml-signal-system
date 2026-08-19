# p3 — Framer eval harness (grader fix + golden set)

**Phase:** 3 · **Activity:** fix the faithfulness grader, then stand up a small offline eval so "is the voice right?" becomes a number and prompt changes are regression-safe.
**Run:** paste the prompt below into `claude` from inside the `aiml-signal-system` repo.
**Why now:** the Phase 2 run produced a **false positive** — Haiku flagged "Anthropic is valued at $965B" as unsupported when it was in the excerpt's second clause. Two defects: (a) it missed a clause in the sentence it was checking, and (b) it graded the day-level **thread** (and headline text) against a *single item's* excerpt, when the thread synthesizes across all items.

---

## Prompt (canonical)

```
Build a small offline eval harness for the Framer. Follow prompts/CONVENTIONS.md — every
meaningful code block gets a comment saying WHAT it does and WHICH agentic concept it uses.

Read first: agents/framer.py, prompts/Framer-Prompt-and-Sources.md,
data/verified/gatekeeper_2026-08-04.json, data/briefs/framer_2026-08-04.json (a real Framer run).

PART 1 — Fix the faithfulness grader in agents/framer.py
1. Scope the check correctly. Per-item framing (why / example / connection / ninety) is
   graded against THAT item's source_excerpt. The day-level `thread` is graded separately
   against the UNION of all item excerpts, because it synthesizes across items. Never grade
   the thread against a single excerpt.
   [CONCEPT: eval — correct grading scope]
2. Do not grade headlines. They come from the Gatekeeper upstream; the Framer didn't write
   them, so they are not its faithfulness burden.
3. Reduce missed clauses: instruct the grader to enumerate every factual claim it finds and,
   for each, quote the exact supporting span from the excerpt (or state "no span found")
   BEFORE assigning confidence. Grounding the verdict in a quoted span is what stops it
   skipping a clause.
   [CONCEPT: harness — force the model to show its work before it judges]
4. Keep it on the cheap model and keep total run cost at <= 2 API calls per day.

PART 2 — Golden set
Create data/evals/golden/ with the 2026-08-04 case:
- input: the raw day
- output: the framed day from the Phase 2 run
- labels.json: hand-labelled expectations, seeded with the known case —
  the "$965B" claim IS supported (it appears in the mega-rounds excerpt), so a grader that
  flags it is WRONG. Record this as a regression case with the expected verdict.
Structure labels so more days can be added later without changing the runner.

PART 3 — data/evals/run_evals.py
- FAITHFULNESS (objective, hard gate): run the grader over a golden case and compare its
  verdicts to labels.json. Report false positives and false negatives explicitly. A false
  positive on the seeded $965B case must FAIL the run.
  [CONCEPT: eval — the grader itself is now under test; who checks the checker]
- STRUCTURAL ASSERTIONS (deterministic, no API): DO THIS appears only on AI items; worked
  examples only on categories in profile.preferences.examplesOnlyForCategories; every item
  has sources; the thread exists and is non-empty; reading time and counts match the items.
  [CONCEPT: harness — assert in code what the prompt only asks for]
- USEFULNESS (subjective, soft): score the framing against a rubric (relevance, insight,
  concision, tradeoff named) using an LLM judge. Print scores; do NOT gate on them yet —
  they need my ratings to calibrate first.
- Print a summary: pass/fail per check, faithfulness FP/FN counts, usefulness averages, cost.
- Exit non-zero if any hard gate fails, so this can gate future prompt changes.

Constraints:
- Do not change the Framer's system prompt or its voice — this phase only fixes the grader
  and adds the harness.
- Do not re-run the Framer to regenerate the brief; evaluate the committed framed output.
- Keep the API spend minimal and respect the existing cost circuit-breaker and usage ledger.
```

---

## Milestone / done when

- `python data/evals/run_evals.py` runs and reports faithfulness FP/FN, structural pass/fail, and usefulness scores.
- The seeded `$965B` regression case **passes** (the grader no longer flags it).
- Exit code gates a failing run, so future Framer prompt edits are regression-tested.

## Notes (decisions/tradeoffs — maintained by Claude)

- Faithfulness = hard gate (objective). Usefulness = reported but not gating until calibrated on my ratings.
- Thread graded against the union of excerpts; headlines excluded from the Framer's faithfulness scope.
- Golden set starts with one real case; the structure must accept more days without runner changes.
- Held-out hygiene: labels used for measurement stay separate from anything the Tuner later adapts on.

### What the build actually found

- **The grader's bug was scope, not carelessness.** It was handed `source_excerpt` first and the
  framing second, so it enumerated the *excerpt's* claims and checked them against the excerpt —
  grading the ground truth against itself. It never read the framing. That explains both symptoms:
  a supported claim looked unsupported, and (proven with a seeded fabrication test) invented facts
  passed clean. Fix: writing first in the payload, named `writing_under_test`; excerpt named
  `ground_truth_excerpt`; every claim phrased as the *writing* phrases it, with a verbatim span.
- **A second false positive turned up while hand-labelling.** The 2026-08-13 re-run flagged
  "~80% of Q1 dollars", which that item's excerpt states word for word. Seeded as a second
  regression case alongside `$965B`.
- **Golden input/output are frozen copies,** not references to the live files — otherwise a Framer
  re-run silently moves the baseline. (This is also why the two false positives come from two
  *different* runs: the committed brief was regenerated after Phase 2.)
- **Labels carry a `tolerated` tier** for claims that are genuinely borderline under the Framer's
  own contract (e.g. a worked example explaining a mechanism the excerpt only names). Scored
  neither way. It is a hole in the gate, so it is kept short and documented.

### Two additions beyond the brief, each forced by a failing test

- **Code-verified evidence.** Every quoted span is checked as a literal substring of the excerpt;
  a claim "supported" by a span that isn't there is downgraded and recorded as a *grader error*,
  gated separately from false positives. A grader can invent a citation; a substring match can't.
- **`temperature=0` on the grader.** At default sampling it disagreed with itself on borderline
  sentences roughly one run in three — a gate that flickers fails builds at random. 5/5 clean runs
  after; fabrication detection unaffected. (Haiku 4.5 still accepts `temperature`; Opus 5 rejects it.)

### Known limit

The gate is deterministic for a fixed input, which is what regression needs. On *unseen* days the
Haiku grader still sometimes enumerates an inference ("X means Y no longer matters") as a checkable
claim. Fine while the golden day is fixed; revisit before the grader guards live briefs — probably
by routing it to a stronger model, which is also what the usefulness judge needs before it can gate.

## Generated with (for reproducibility)
- Model: claude-opus-5 (Claude Code)
- Result commit: `04a4203`
- Verified: `python data/evals/run_evals.py` → structural PASS · faithfulness PASS (FP 0, FN 0) ·
  usefulness rele 4.2 / insi 3.8 / conc 4.1 / trad 4.0 · $0.0189. Negative tests (fabricated
  valuation, date, investor, benchmark number, and a thread-level fact) all caught, exit 1.
- Deviations from the prompt: none in scope. Two additions listed above; both were responses to a
  test failure rather than speculative work.
