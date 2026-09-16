# After the facts/interpretation split — 2026-09-15

Measured against `baseline-2026-09-15.md`. Same pinned model, same frozen cases,
N=3 per case, faithfulness only. Spend $0.0512.

## The change

The Framer now divides every field into `fact` and `interpretation` runs. Only the
`fact` runs reach the grader. The boundary was labelled by two independent models
(Sonnet, Opus) working blind; they agreed on 94.6% of characters on `2026-08-04` and
96.3% on `negative-01`, and the disagreements — six and one — were ruled by hand.

Two deterministic checks arrived with it: **coverage** (a split must reconstruct its
field exactly; hard gate) and **lifted_claims** (a claim verbatim in the source but
absent from the writing; warns, does not gate).

## Result

| | before | after |
|---|---|---|
| `2026-08-04` false positives | **18**, identical 5/5 | **0**, identical 3/3 |
| `2026-08-04` grader errors | 1 every run | 0 |
| `2026-08-04` output tokens | 2253 | 1309 |
| `2026-08-04` gate | 0/5 pass | **3/3 pass** |
| `negative-01` plant caught | 2 of 5 | 2 of 3 |
| `negative-01` gate | 0/5 pass | 2/3 pass |

**The too-strict failure is fixed, structurally.** Those 18 clauses are no longer
sent to the grader, so they cannot be flagged however it behaves. 12 of 13 sampled
flagged clauses became ungradable; the 13th — "a client pinned to the old spec cannot
talk to a new server" — is a `fact` clause the source does support, and the grader now
marks it supported.

**The too-lenient failure is not fixed.** 2/5 → 2/3 establishes nothing at this n, and
the split was never aimed at it. The miss is the grader reading the wrong document,
which a facts-only payload does not address.

## What `lifted_claims` showed

Zero false alarms in six runs: 0 firings across all 75 claims on the clean day — 51 on
items, 24 on the thread, over three runs. Every firing on `negative-01` was a true detection of a claim phrased
from the source.

| run | item lifts | thread lifts | plant |
|---|---|---|---|
| negative-01 run 1 | 5 of 8 | 1 of 6 | **missed** |
| negative-01 run 2 | 1 of 8 | 3 of 6 | caught |
| negative-01 run 3 | 1 of 8 | 5 of 7 | caught |

Per-unit, lifts on `mega-rounds` — the unit carrying the plant — are 2 / 0 / 0,
aligned with miss / catch / catch, and 0 everywhere on `2026-08-04`. The residual
`mcp` lift on the catches is real: the grader reproduced the excerpt's word order where
the writing had reordered the clause. Its verdict happened to be right anyway.

**So the check detects the behaviour reliably but does not predict the outcome.** A
lifted verdict can still be correct. That is enough for a retry and not enough for a
gate: re-deriving a verdict that was read off the wrong document costs $0.008 and
loses nothing, while failing a build on it would fail correct runs.

## What was deliberately not done

No threshold was fitted. Separating 5 from {1, 1} across three runs is drawing a line
through three points to make a run go green. The retry trigger — *any lift on a unit
retries that unit* — is the only non-arbitrary form, and it rests on n=1 for the event
it fires on. It gets built and measured in Phase 6, against more negative cases.
