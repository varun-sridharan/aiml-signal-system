# Step A · a failure stops a sentence or a story, never the week

The claim gate (GATE=claims) now acts on an "unsupported" verdict in four steps (agents/repair.py, wired in agents/weekly.py `claims_gates`):
a second, independent reader (a different model, asked whether the claim is in the excerpt and which numbered sentence states it) must agree;
the writer rewrites that one sentence once (the rewrite is always graded as a fact); everything is graded again; a sentence that still fails is
removed, or its story is held when the sentence was load-bearing (a headline, the only fact left in a body, or a part of a paper's 90-second
summary). A last grading checks that the removed sentence was the right one. Style, length and split problems are repaired once by the writer
(`composer.MAX_REVISIONS = 1`) and are then warnings recorded in the run artifact. The edition publishes when at least four stories pass
(`composer.MIN_STORIES`); the reason it does not is recorded. A field whose split is missing, does not reproduce it or calls it wholly
interpretation is graded whole, for both the composed pass and the first pass.

## Measurement (golden day and the four planted cases, two runs each, final code): `stepA-measure/`

| case | planted claim kept out of the final text | flags first graded | dismissed by the second reader | rewritten | removed | held | clean text touched | cost per run |
|---|---|---|---|---|---|---|---|---|
| negative-01 | 2 of 2 runs | 1 | 1 (the known false flag on a clean thread claim) | 1 | 0 | 0 | 0 | $0.030 |
| negative-02 | 2 of 2 | 1 | 0 | 1 | 0 | 0 | 0 | $0.022 |
| negative-03 | 2 of 2 | 1 | 0 | 1 | 0 | 0 | 0 | $0.022 |
| negative-04 | 2 of 2 | 1 | 0 | 1 | 0 | 0 | 0 | $0.022 |
| 2026-08-04 (clean) | none planted | 0 | 0 | 0 | 0 | 0 | 0 | $0.012 |

"Kept out" means the sentence that carried the planted claim is no longer in the final text. In negative-02 and negative-03 the rewrite put the true
figure and date back (58.4% belongs to Terminal-Ops; v1 stops on 2027-03-01, and 2026-12-01 is v2's default date), which the saved final sentences
show; in negative-01 and negative-04 the planted clause is gone. Two runs per case is a small sample, and the regrade is the same Haiku grader's.
The first measurement (before the reviewer's findings were fixed) is in `stepA-measure-before-review/`; the mutation proofs are in `stepA-mutations*.txt`.
