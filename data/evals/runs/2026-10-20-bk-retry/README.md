# bk-retry measured, 2026-10-20

Raw results: one JSON per run (`<arm>-<case>-run<n>.json`, claim ledgers included), `summary.json`,
`summary.md`, and the console log `../2026-10-20-bk-retry.log`. Reproduce with
`python data/evals/measure_retry.py --runs 5 --out <dir>`. Spend $0.3015 of the $3 allowed.

## What the numbers say

Every planted fabrication was caught 5 of 5 runs, with the retry off and with it on (negative-01 to -04),
and the clean day (`2026-08-04`) had 0 false positives in all 10 runs. By the runbook's bar (caught in at
least 4 of 5, 0 clean-day false positives) the bar is met.

## What they do not say (read this before citing the table)

1. **The retry did not cause any catch.** The grader caught every plant with the retry off. The retry
   fired in only one case: on `negative-01-mcp`, the clean control unit (a claim of the grader's, "The
   change breaks cross-version compatibility", sits verbatim in the excerpt and not in the writing), not on
   a unit carrying a plant, and it changed nothing there. On negative-02, -03 and -04 the lifted-claim
   check never fired. So bk-retry is built and unit-tested, but **its effect on a missed plant has not been
   observed**.
2. **Effective n is 1 per cell.** At temperature 0 the five runs of every cell returned identical claim
   ledgers (and identical cost). "5/5" is one sample repeated, not a rate.
3. **The grader's behaviour has changed since 2026-10-18.** On that day the same grader, with a
   byte-identical request, never enumerated the planted `$965B` claim on `negative-01` (2 runs of 2, and 1
   of 3 on 2026-09-15). Today it flags it. Nothing in this repo changed that; the model's behaviour at
   temperature 0 is not stable from day to day, so a catch today is not a guarantee.
4. **negative-01 still fails its hard gate**, for a different reason: in all 10 runs the grader flags the
   thread claim "Anthropic's $965B valuation" as unsupported, while quoting a verified span for it
   ("Anthropic is valued at $965B"). The label says the thread is clean. That is a false positive on a
   clean unit of the case that holds the plant. (The "5" in the summary table for negative-01 is that one
   flag per run.)
5. **The new cases are easy.** Each plant swaps one figure, date or name between two sentences of a short
   excerpt. They test recall on that class, not on subtle fabrications. They were hand-constructed by the
   autopilot run and are awaiting Varun's review.
6. `caught` means no false negative was reported for the labelled plant unit. It ignores
   `regression_cases` and does not identify which flag matched; the full ledger per run is in the JSON.
8. **Its first real firing made a correct verdict wrong** (added after Step D; evidence in
   `autopilot/evidence/2026-10-20/stepD-retry-in-the-wild.txt`). In the first real weekly run the thread's
   claim "OpenAI models account for the majority of tokens used in Amp" was lifted (its wording is the
   excerpt's, which says exactly that), the thread was retried, and the retry's verdict flagged the writing's
   own version of that claim as unsupported with no span, so a thread the first pass had passed failed the
   gate. Two plausible causes, neither tested: the retry replaces the verdict, and the retry note tells the
   grader not to repeat the lifted claim, which may push it away from a claim that was in fact supported.
   It fired once in three real attempts (attempts 2 and 3 had no lift). Treat the retry as unsafe to rely on
   until it is reworked or switched off (`framer.RETRY_ON_LIFT`); that is Varun's call, and it was not changed
   during the run so that nothing is tuned against one observation.
7. The retry replaces a unit's verdict. In principle a retry that drops a flag the first pass made would
   erase a catch; this was not observed because the retry never fired on a plant, and it is untested on the
   real grader.
