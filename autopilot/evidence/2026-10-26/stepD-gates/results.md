# Step D · the claim gate and the citation gate on the same inputs

Five runs per cell. Fixed texts: the clean golden day (2026-08-04) and the four planted-fabrication cases. 'Caught' counts planted claims flagged across the runs; 'false alarms' are flags that the hand labels do not expect, per run.

| case | gate | planted caught | false alarms per run | units flagged per run | $ per run | seconds per run |
|---|---|---|---|---|---|---|
| negative-01 | claims | 5/5 | [1, 1, 1, 1, 1] | [2, 2, 2, 2, 2] | 0.0053 | 6 |
| negative-01 | citations | 5/5 | [0, 2, 1, 0, 1] | [1, 3, 2, 1, 2] | 0.0398 | 17 |
| negative-02 | claims | 5/5 | [0, 0, 0, 0, 0] | [1, 1, 1, 1, 1] | 0.0044 | 5 |
| negative-02 | citations | 5/5 | [0, 0, 0, 0, 0] | [1, 1, 1, 1, 1] | 0.0255 | 11 |
| negative-03 | claims | 5/5 | [0, 0, 0, 0, 0] | [1, 1, 1, 1, 1] | 0.0042 | 5 |
| negative-03 | citations | 5/5 | [3, 3, 4, 3, 3] | [3, 3, 3, 3, 3] | 0.0288 | 14 |
| negative-04 | claims | 5/5 | [0, 0, 0, 0, 0] | [1, 1, 1, 1, 1] | 0.0043 | 5 |
| negative-04 | citations | 4/5 | [1, 0, 1, 0, 0] | [2, 1, 2, 0, 1] | 0.0282 | 13 |
| 2026-08-04 | claims | none planted | [0, 0, 0, 0, 0] | [0, 0, 0, 0, 0] | 0.0103 | 11 |
| 2026-08-04 | citations | none planted | [6, 9, 8, 4, 9] | [3, 5, 5, 3, 5] | 0.1138 | 49 |

False alarms over all 25 runs on fixed text: claims 5, citations 58.

**How to read this.** The fixed texts are already written, so the citation gate was fed by a stand-in citer (a model asked to restate each field word for word and cite it, leaving unsupported sentences uncited and marking pure judgement with `~`); in production the writer cites as it writes. That stand-in makes a planted sentence easy to catch by code (negative-03 was caught by the code rules in four of five runs) and makes the false-alarm count depend on how well it marks judgement. The first stand-in parser had a defect (a heading run into the next word; folder `fixed-v1-heading-bug`), so the citation arm was rerun in `fixed-v2`; the claim-gate rows come from the v1 folder because that arm was unaffected. The claim gate's units are claims and the citation gate's are sentences, so false-alarm counts are of different things; the switch rule fails either way (58 against 5). On W43 the two gates judged different writer outputs, so recall cannot be compared, and a gate that flags little looks good on false alarms; the adjudicator is one model, and 61 of the 126 citation flags it called judgement. Per-run `cost_usd` is the run's own calls; `ledger_delta_usd` in the W43 files also includes concurrent runs. The first trials (three editions and a two-case run) are in `stepD-trial/`.

## The 2026-W43 Reader output (stored first pass reused; the writer and the gate run fresh each time)

| run | gate | publishes | $ | seconds | what stopped it |
|---|---|---|---|---|---|
| 1 | claims | no | 0.83 | 313 | week: unsupported (composed) — CodeQL agreeing the alert is closed |
| 2 | claims | no | 0.96 | 375 | style — essay paragraph 4: rule 1: the opening sentence carries no number |
| 3 | claims | no | 1.48 | 589 | essay: unsupported (composed) — scaling to 20,000 parallel environment |
| 4 | claims | no | 0.85 | 308 | [[hl|shipped an agent that reruns CodeQL]]: a highlight is one to five words; style — essay paragraph 3: rule 1: the opening sentence names no company or pers |
| 5 | claims | no | 0.81 | 296 | essay: unsupported (composed) — Terminal Bench, KernelBench, and SETA  |
| 1 | citations | no | 0.79 | 344 | 0 of 7 stories passed, 7 held, 18 blocking failures, 3 structural |
| 2 | citations | no | 0.76 | 352 | 2 of 7 stories passed, 5 held, 9 blocking failures, 0 structural |
| 3 | citations | no | 0.71 | 320 | 3 of 7 stories passed, 4 held, 8 blocking failures, 0 structural |
| 4 | citations | no | 0.71 | 308 | 0 of 7 stories passed, 7 held, 31 blocking failures, 3 structural |
| 5 | citations | no | 1.04 | 464 | 0 of 7 stories passed, 7 held, 15 blocking failures, 0 structural |

### Adjudicated flags on the W43 text (a fresh reader checked every flag against the excerpts)

| gate | flags | true alarms | false alarms (supported) | false alarms (judgement) | verbatim |
|---|---|---|---|---|---|
| claims | 3 | 0 | 3 | 0 | 0 |
| citations | 126 | 10 | 55 | 61 | 0 |
