# p2 — Build & prove the Framer

**Phase:** 2 · **Activity:** build `agents/framer.py` — the Framer agent — and prove its voice on the sample day.
**Run:** paste the prompt below into `claude` from inside the `aiml-signal-system` repo.
**Depends on:** an `ANTHROPIC_API_KEY` in a local `.env` (the Framer runs headless via the API, not via Claude Code). If you don't have one yet, set it up first — see the note at the end.

---

## Prompt (canonical)

```
Build the Framer agent for this repo. Follow prompts/CONVENTIONS.md — every meaningful
code block gets a comment saying WHAT it does and WHICH agentic concept it uses.

Context to read first:
- prompts/framer-prompt.md + prompts/framer-contract.md  (the Framer's voice, role, rules, contract)
- config/profile.json                  (the user profile; the agent takes profile as input)
- data/evals/golden/reference_2026-08-04.json               (a fully-framed sample day — the TARGET quality)
- application/Signal.html         (the Bento voice/layout to match)

Step 1 — create a raw input fixture (the Framer must generate framing, not copy it):
- Write data/verified/gatekeeper_2026-08-04.json by stripping the generated framing out of
  data/evals/golden/reference_2026-08-04.json. KEEP only: date, and per item -> id, category, headline,
  tags (KNOW/DO/RADAR), sources, and a `source_excerpt` field (a 1-3 sentence factual
  blurb of what the source says; derive it from the existing `why`/`ninety` text).
  REMOVE: thread, why, example, connection, ninety, readingTimeMin, counts.

Step 2 — build agents/framer.py:
- Input: (profile.json, a raw day file). No personal facts hardcoded — read them from
  profile. [CONCEPT: harness — context injection]
- Frame ALL items in ONE structured API call (NOT one call per item). Send the Framer
  system prompt from prompts/framer-prompt.md plus all raw items, and request a
  single JSON response containing, per item: why-it-matters; a worked example ONLY for
  category == "AI"; a connection line where a real link exists; and for papers an "in 90
  seconds" (method/result/caveat); AND the day-level THREAD (correlations AND
  contradictions). Enforce: DO THIS tag only on AI items.
  [CONCEPT: harness — one batched structured call to cut cost, not N calls]
- Compute reading time (~200 wpm) and the counts block in plain Python (no API call).
- Faithfulness self-check: verify each FACTUAL claim traces to the item's source_excerpt;
  flag anything unsupported and lower a per-item confidence. Use a CHEAPER model (Haiku)
  for this check. Target <= 2 API calls per day total.
  [CONCEPT: loop — reflection / self-verification before emit; eval — grounding]
- Read ANTHROPIC_API_KEY from a .env (use python-dotenv). Pin models in top-of-file
  constants (FRAMER_MODEL, CHECK_MODEL). Use prompt caching on the reused system prompt.
  [CONCEPT: harness — model routing + prompt caching]

Step 3 — output:
- Write the result to data/briefs/framer_2026-08-04.json (DO NOT overwrite the hand-authored
  data/evals/golden/reference_2026-08-04.json — we want to compare the two).
- Print a short summary: item count, DO THIS count, any faithfulness flags.

Cost controls (required):
- One structured call to frame the day + at most one cheaper call for the faithfulness
  check. Target <= 2 API calls/day.
- Set a max_tokens cap on output; pin models in constants; use prompt caching.
- Define MONTHLY_BUDGET_USD at the top. Append every call's input/output tokens +
  estimated cost to data/state/usage.json. Before a run, if month-to-date estimate exceeds
  MONTHLY_BUDGET_USD, abort with a clear message.
  [CONCEPT: loop — cost circuit-breaker guardrail]
- Leave a TODO noting production scheduled runs should use the Anthropic Message
  Batches API (~50% cheaper, async).

Constraints:
- Keep it a single, readable file. Add a requirements.txt (anthropic, python-dotenv).
- Do not build Scout/Gatekeeper/Coach/Tuner — only the Framer.
- Do not modify Signal.html, Control-Hub.html, or the existing data files.
```

---

## Milestone / done when

- `agents/framer.py` runs on `data/verified/gatekeeper_2026-08-04.json` and writes `data/briefs/framer_2026-08-04.json`.
- You open the framed output next to `application/Signal.html` and judge: does the voice land? (This is the go/no-go on the whole system.)

## Notes (decisions/tradeoffs — maintained by Claude)

- Output written to a NEW file so we can eyeball the Framer's framing vs the hand-authored target.
- Faithfulness self-check added as an in-agent reflection loop (beyond the offline eval in Phase 3) — catches hallucination before emit.
- Examples AI-only, DO THIS AI-only, thread required — carried from prior decisions.

## API key setup (if needed, before running)

1. Get a key from console.anthropic.com (this is Console/API billing, separate from your Max plan).
2. In the repo: `echo "ANTHROPIC_API_KEY=sk-..." > .env`
3. Confirm `.env` is gitignored (the Python .gitignore already ignores it) so the key is never committed.

## Generated with (for reproducibility)
- **Model:** Claude Opus 5 (via Claude Code v2.1.222)
- **Result commit:** `df8adfd` on `origin/main`
- **Deviations from prompt (both correct):**
  - Built the Framer prompt doc fresh (the file didn't exist; option 3) — later split into `framer-prompt.md` + `framer-contract.md` — no Scout content invented.
  - Switched the framing call from `messages.create` to `messages.stream` + `get_final_message()` because the SDK blocks a non-streaming request with `max_tokens=24000` (would outlast the HTTP timeout). No API spend on the blocked attempt.
- **Run result:** 7 items, 2 DO THIS, ~5 min read, $0.0998. One faithfulness false positive — see Phase 3.
