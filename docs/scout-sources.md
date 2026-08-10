# Scout — seed source list (Tier A–D)

Scout's starting set of sources, tagged by **reputation prior**. High-prior = primary/official, can surface on its own. Low-prior = discovery + corroboration only, never surfaced un-corroborated. This tiering is what the Gatekeeper leans on (Phase 5), and the Tuner adjusts per-source over time from your feedback (Phase 10).

Relevance filter = your whole interest graph (multi-agent, RL, MCP, agentic coding, and adjacent) — **not** ERP-locked. See `data/profile.json`.

> These are **priors, not verdicts.** A high tier means "trust by default"; a low tier means "needs a second source before it reaches you."

## Tier A — Primary / official (high prior)
- **arXiv** — cs.AI, cs.LG, cs.MA (multi-agent), cs.CL. Pull via the listing RSS or the arXiv API.
- **Hugging Face Papers** — huggingface.co/papers (community-ranked; a good signal filter over the arXiv firehose).
- **Lab blogs** — Anthropic, OpenAI, Google DeepMind, Meta AI, Mistral, Qwen (Alibaba), DeepSeek, Moonshot / Kimi, NVIDIA Developer Blog.
- **Model Context Protocol** — blog.modelcontextprotocol.io + the spec repo. Direct relevance to your MCP work — treat as must-watch.
- **Benchmarks / leaderboards** — Terminal-Bench, SWE-bench, LMArena.

## Tier B — Ecosystem / release notes (high prior, scoped)
- **GitHub Changelog** — github.blog/changelog (infra/tooling shifts).
- **Agent frameworks** — LangChain / LangGraph, CrewAI, AutoGen / Semantic Kernel release notes.

## Tier C — Curated newsletters (medium prior — good framing, still corroborate claims)
- Import AI (Jack Clark), The Batch (DeepLearning.AI), Latent Space.

## Tier D — Aggregators (low prior — discovery + corroboration only, never surfaced alone)
- Hacker News front page, r/LocalLLaMA, r/MachineLearning.

---

## How Scout uses this (Phase 4)
- Seed only — no auto-discovery yet. Scout pulls candidate items from these sources into `data/raw/`.
- Each fetched item records which source and tier it came from, so the Gatekeeper can apply the reputation prior and the Tuner can re-weight sources later.
- The Control Room's pending action in `data/metrics.json` ("Add LangGraph + CrewAI to Scout Tier B") is an example of the Tuner/Prescriber proposing a change to this list.
