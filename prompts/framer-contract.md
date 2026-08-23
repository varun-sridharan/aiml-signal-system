# Framer — contract

> The Framer's **system prompt** lives in `prompts/framer-prompt.md` (that file is the prompt, nothing else).
> This file is the contract around it: what goes in, what comes out, and which rules are enforced in code.
> Change one and change the other in the **same commit**.

The Framer writes the daily brief. It is the only agent whose output you read every
morning, so its voice is the riskiest assumption in the system — this file is the
single source of truth for that voice.

`agents/framer.py` reads `prompts/framer-prompt.md` verbatim as its system prompt.
Edit the voice there, never in the Python.

> **Scope note.** This file covers the Framer only. The Scout seed source list
> (Tier A–D) referenced by the Control Room's pending action in `data/state/metrics.json`
> is not yet in this repo — it lands in `config/scout-sources.md` in Phase 4.

## Sources of truth this was assembled from

- `System-Design.html`: agent table, evals, tradeoffs, decision log.
- `Plan.html` → Phase 2 notes: "worked examples on AI items only; synthesis
  'thread' required; DO THIS reserved for AI; reading-time header; inline connections."
- `config/profile.json`: categories, `preferences`, `tuner` state — read at runtime,
  never hardcoded into the prompt.
- `application/Signal.html` and `data/evals/golden/reference_2026-08-04.json`: the target voice.

## Input contract

The Framer receives `(profile, raw_day)`. A raw day is the Gatekeeper's verified
pool — no framing in it yet:

| Field | Notes |
| --- | --- |
| `date` | `YYYY-MM-DD` |
| `items[].id` | Stable ID, echoed back so framing can be joined to the item |
| `items[].category` | One of `profile.categories` |
| `items[].headline` | Written upstream; the Framer does not rewrite it |
| `items[].tags` | `DO THIS` / `KNOW` / `RADAR` |
| `items[].sources` | `{label, url, tier}`; `tier` is `primary` or `corroboration` |
| `items[].source_excerpt` | 1–3 factual sentences of what the source says — **the only ground truth the Framer may use** |

Raw days carry no `user_id`: the verified pool is shared across users by design
(System-Design.html → Commercial path). Personalization starts at the Framer, so the
framed output carries `user_id` from the profile.

## Output contract

Per item: `why` (why-it-matters), `example` (worked example, AI items only),
`connection` (only where a real link exists), `ninety` (`method`/`result`/`caveat`,
papers only). Plus one day-level `thread`.

`readingTimeMin`, `counts`, and `rank` are computed in Python from the framed text
and the input order — not asked of the model, because they are arithmetic and
ordering, not judgment.

## Hard rules (enforced in code, not just prompted)

1. **Faithfulness is a hard gate.** Every factual claim traces to that item's
   `source_excerpt`. No numbers, dates, or names that aren't in it.
2. **DO THIS is reserved for `profile.preferences.doThisCategory`.**
3. **Worked examples only for `profile.preferences.examplesOnlyForCategories`.**
4. **The thread is required** and must carry both a correlation and a contradiction.
