# Digest sources: where the scheduled Weekly Digest looks, and what it has learned

A **copy**, taken 2026-10-19, of the source catalogue the scheduled Weekly Digest keeps in project
memory (`claude/digest-sources.md`). It is condensed to what the Reader needs: which sources, which
pages, and the fetch and dating traps already found. The original stays in project memory and keeps
evolving while the old digest runs; refresh this copy at go-live.

This is the "91 tiered domains" the plan refers to: a prose catalogue, not a table. Tiers follow
`config/scout-sources.md`. **A = primary / official**, **B = ecosystem / release notes**,
**C = curated or original reporting (corroborate)**, **D = discovery only, never cited as primary**.

## Varun's own additions (checked first, highest weight)

- **The New Stack**, thenewstack.io (C, high-yield funnel). It does original reporting, so it can be
  cited, but follow through to the vendor's own release notes as primary. A fetch can come back short:
  if it looks dry, fetch again asking for the newest headlines with times.
- **CIO Dive**, ciodive.com/news (C, buyer-side enterprise reporting). Its front page carries no dates
  in a fetch extract. That is normal, not a dry source; open an article to date it.
- **India payments infrastructure**: NPCI circulars (npci.org.in/uploads, public and dateable PDFs),
  RBI, Global Fintech Fest (A). reuters.com blocks fetchers, so use a syndicated copy and anchor to the
  NPCI document.
- **AI consciousness and model welfare**: reciprocalresearch.org, ai-frontiers.org (C). Anchor claims to
  the researcher's own arXiv paper or organisation site.
- **Ollama** release notes and GitHub releases (B).

## Frontier labs (A)

- **Anthropic: two indexes, check both every run.** anthropic.com/news and anthropic.com/research. A
  research post does not reliably appear on the news index. Slugs cannot be derived from headlines:
  resolve the URL by search, never by guessing.
- **OpenAI: two indexes**, openai.com/news and openai.com/research. The research index is a curated,
  often months-stale model-family list; real research posts appear on /news under Research and Safety
  tags.
- **Google**: the Gemini API changelog (ai.google.dev/gemini-api/docs/changelog) is the dateable
  source. deepmind.google shows month-only dates, and blog.google/technology/ai shows no dates in a
  fetch extract. Use both for discovery only.
- **Microsoft AI**, microsoft.ai/news. Primary for the MAI series. The index renders no dates; open the
  newest post to date it.
- **Meta AI, Mistral, DeepSeek, Qwen, xAI, Ai2, Z.ai.** Z.ai blog slugs 404 like Anthropic's; the
  Hugging Face model card is an acceptable primary source for an open-weights release.
- **arXiv** cs.AI, cs.LG, cs.CL. The index lags about five days, so it is useful on a weekly lookback,
  not a daily one.
- **Benchmarks**: LMArena, Epoch AI, ARC Prize (primary: it publishes cost and token figures, not just
  scores), SWE-bench, Terminal-Bench.

## Tooling (A/B)

- **Model Context Protocol**: blog.modelcontextprotocol.io/posts/ lands spec news before the GitHub
  releases page. It is quiet for long stretches.
- **GitHub Blog and Changelog**, github.blog. Primary, with cost and quality figures.
- **NVIDIA Developer Blog**, developer.nvidia.com/blog. Primary and dateable for Nvidia software.
- Agent SDKs: OpenAI Agents SDK, Anthropic Agent SDK, Claude Code releases.
- Frameworks: LangChain / LangGraph, LlamaIndex, CrewAI, AutoGen.
- Evals and observability: LangSmith, Braintrust, W&B Weave, Arize Phoenix; the OpenTelemetry GenAI
  semantic conventions.
- Prime Intellect: blog and GitHub (harness, RL-agent and security research).

## Enterprise (A for vendors' own statements, C for coverage)

- Vendor newsrooms: SAP (often blocks fetchers), Oracle, Workday, Infor, Microsoft Copilot / Azure AI,
  Salesforce Agentforce, ServiceNow, Snowflake, Databricks, UiPath, Sierra, Glean, Palantir, Decagon,
  Harvey.
- Buyer-side reporting, which fixes this track's known weakness: CIO Dive (above), the Gartner
  newsroom, Linux Foundation press, and VentureBeat's orchestration section.
- Vendor newsrooms only ever carry the vendor's own announcements; an empty enterprise week is normal.

## Funding (C)

Crunchbase News, TechCrunch AI, Axios Pro Rata, SEC Form D filings; fund announcements from a16z,
Sequoia, Index, Accel, Lightspeed and Peak XV; Y Combinator launches.

## Discovery, then chase the primary source (D)

- VentureBeat and The Decoder (dated same-day coverage; read the front page, not a date search).
  TechCrunch's AI category.
- Hacker News, Import AI, The Batch, Interconnects, Latent Space, Simon Willison, Ahead of AI, TLDR AI.
  **Never cited as primary.**

## Fetch and dating rules already learned

- **Known blocks:** reuters.com, arstechnica.com and often SAP's newsroom refuse fetchers. Use a
  syndicated copy and anchor to a primary document.
- **Never guess a URL slug from a headline.** Resolve it by search.
- **Pages that render no dates in a fetch extract:** microsoft.ai/news, ciodive.com/news,
  blog.google/technology/ai, deepmind.google (month only). A dateless extract is not a dry source.
- **An item's date is the primary source's publication date**, never the date of later coverage.
- **Check the whole front page for the date**, not just the newest few headlines. Shallow skims caused
  three documented misses.
- **"Sources say" from several outlets is one leak, not confirmation.** Hold until the company states it.
- Hugging Face licence files: `raw/main/LICENSE` fetches; `blob/main/LICENSE` has returned 401.
- Batch the front-page fetches. Most of the budget should go on reading primary sources, which is where
  item quality comes from.
