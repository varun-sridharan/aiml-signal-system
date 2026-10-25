# Step B · why the Console showed a 1% cache hit rate

Numbers only; every one is in `stepB-ledger-cache-by-phase.txt` (the ledger) or `stepB-cache-probe.json` (the probe).

## What the ledger says

Across every settled call to date, cache reads are 1.2% of all input tokens (12,576 of about 1.01 million, before the
probe's own calls), which is the Console's 1%. Where the tokens are:

| Where | Input tokens | Cacheable? |
|---|---|---|
| Haiku graders and the Reader (`faithfulness`, `eval-*`, `composing-faithfulness`, `reader-*`, `dedup-*`) | about 739,000 (73%) | No. Haiku 4.5 caches only a prefix of 4,096 tokens or more; the grader's system prompt is far shorter, and what follows it (the writing and its excerpts) is different on every call. |
| Opus calls (`framing`, `composing`, `composing-revise`) | about 272,000 (27%), mostly cache writes | Yes, and each has `cache_control` on its system block. |

## Does the cache work where it is switched on? Yes.

The probe (five calls, composer's cached prefix of 6,830 tokens (its system text is about 4,300 tokens; the rest is the output schema), nothing varying but the call):

| Call | Cache write | Cache read | Cost |
|---|---|---|---|
| 1 composing schema | 6,830 | 0 | $0.0444 |
| 2 the same again | 0 | 6,830 | $0.0051 |
| 3 revising schema, same system text | 5,632 | 0 | $0.0365 |
| 4 the same again | 0 | 5,632 | $0.0041 |
| 5 composing schema again | 0 | 6,830 | $0.0051 |

A repeat call reads 99.8% of its input from the cache and costs 11.5% as much.

## Why real runs almost never hit

1. **Gaps.** A cache entry lives 5 minutes from its last use. A weekly run makes one composing call and one framing call;
   the next call with the same prefix is days away. In the ledger the only reads on a composing or framing prompt are
   the framing calls of 10-20 02:23 and 02:27 (4 minutes after a write) and one revision round at 10-21 00:19.
2. **The output schema is part of the cached prefix.** Calls 3 and 5 show it: the same system text with a different
   `output_config` schema is a different entry (6,830 tokens against 5,632). The composing call and its revision
   therefore do not share an entry, only two revision rounds do.
3. **The writes cost something.** A cache write is 1.25 times the input price. A composing call that is never followed
   by another inside 5 minutes pays about $0.009 more than it would uncached, a rounding error.

## Changes

None to production code. `cache_control` is on the Framer's, the composing pass's and the revision's system blocks, and
nothing volatile (date, week, story data) is in front of them. `tests/test_prompt_cache.py` now guards that, and
`stepB-mutations.txt` shows it failing when a date is put in the prefix or the breakpoint is removed.

## What would raise the rate, and why it was not done

- Give the revision pass the composing pass's schema, or one shared schema, so a first revision reads the composing
  entry: saves about $0.03 per revised edition. Not done; it changes the model's output contract for a few cents.
- A one-hour breakpoint on the composer's prompt: pays twice for the write, and only helps a rerun within the hour.
- Restructure the grader so the excerpts lead and are cached: the grading scope depends on the writing coming first
  (`framer.build_check_payload`), and the gate's measured behaviour is not to be disturbed for a few cents.

The honest reading of the 1%: most of the money is in calls that can never hit, and the calls that can hit are one a
week. The percentage is not a defect to repair; a weekly pipeline cannot have a high hit rate.
