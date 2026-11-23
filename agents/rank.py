"""Ranking: which candidates get a paid fetch, and which fetched ones make the edition. Deterministic, free, no model.

WHAT: rank = interest relevance + significance + a boost for a link Varun saved, then diversity caps.
WHY (Edition 06, 2026-W48, debrief 20): both choices used the interest score alone, so a week of Microsoft ERP posts and arXiv papers
(each matching three or four of Varun's topics through words like "agents") outranked the week's launches, and one company filled the page.
CONCEPT: significance is what happened in the world regardless of what Varun follows: a frontier lab's own launch, other outlets
reporting the same story, how many people upvoted it. It is added to relevance, never substituted for it, and it is capped so it cannot bury a
story Varun cares about. Diversity is a hard cap per company and per interest cluster, applied after the order is set.

Every function here takes plain dicts, so the replay of a recorded week (tests/golden/w48-must-catch.json) runs without a database.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

# --- companies ---------------------------------------------------------------------------------------------------------------------------
# A publisher's domains, as the company they belong to. An address not listed is its own company (arxiv.org, thenewstack.io).
# GitHub is kept apart from Microsoft: its changelog is a separate editorial voice that was never the problem.
COMPANY_BY_DOMAIN = {
    "openai.com": "openai", "anthropic.com": "anthropic", "claude.com": "anthropic",
    "deepmind.google": "google", "blog.google": "google", "ai.google.dev": "google", "research.google": "google",
    "microsoft.com": "microsoft", "microsoft.ai": "microsoft", "techcommunity.microsoft.com": "microsoft",
    "cosmosdb.microsoft.com": "microsoft", "azure.microsoft.com": "microsoft",
    "github.blog": "github", "ai.meta.com": "meta", "mistral.ai": "mistral", "developer.nvidia.com": "nvidia",
    "news.sap.com": "sap", "sap.com": "sap", "infor.com": "infor", "newsroom.workday.com": "workday", "workday.com": "workday",
    "oracle.com": "oracle",
}


def company_of(domain: str) -> str:
    domain = (domain or "").lower()
    best = ""
    for known in COMPANY_BY_DOMAIN:
        if (domain == known or domain.endswith("." + known)) and len(known) > len(best):
            best = known
    return COMPANY_BY_DOMAIN[best] if best else domain


# --- interest relevance, by cluster ------------------------------------------------------------------------------------------------------
RING_WEIGHT = {"core": 3, "adjacent": 2, "explore": 1}

# Topics that recognise overlapping text sit in one cluster. Inside a cluster the best topic counts in full and each further matching topic
# adds one, up to two: three topics matched by the same sentence are one interest, not three. The cluster is also what the edition's
# "at most three stories" cap counts.
CLUSTERS = {
    "multi-agent systems": "agents", "agentic coding": "agents", "MCP architecture": "agents", "harness engineering": "agents",
    "reinforcement learning": "models", "model releases": "models", "serving & inference": "models", "evals": "models",
    "enterprise AI and ERP": "enterprise", "vertical AI agents (manufacturing, supply chain)": "enterprise",
    "AI vendor product launches": "enterprise", "ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)": "enterprise",
}
CLUSTER_EXTRA_MAX = 2
MAX_PER_CLUSTER = 3
MAX_STORIES_PER_COMPANY = 2
MAX_FETCHES_PER_COMPANY = 5


def relevance(topics, rings: dict) -> int:
    """Interest relevance from the topics an item matched. `rings` maps topic -> ring; a topic it does not know ('from your backlog') is skipped."""
    by_cluster = defaultdict(list)
    for topic in topics or []:
        if topic in rings:
            by_cluster[CLUSTERS.get(topic, "topic:" + topic)].append(RING_WEIGHT[rings[topic]])
    return sum(max(w) + min(len(w) - 1, CLUSTER_EXTRA_MAX) for w in by_cluster.values())


def cluster_of(topics, rings: dict) -> str | None:
    """The interest cluster a story counts against: that of its heaviest topic. None when it matched no topic (a launch, a saved link)."""
    known = [(RING_WEIGHT[rings[t]], t) for t in topics or [] if t in rings]
    if not known:
        return None
    best = max(known, key=lambda wt: (wt[0], -sorted(CLUSTERS).index(wt[1]) if wt[1] in CLUSTERS else 0))
    return CLUSTERS.get(best[1], "topic:" + best[1])


# --- significance ------------------------------------------------------------------------------------------------------------------------
# Domains whose every post is about models and AI products. blog.google and ai.google.dev carry Pixel and Search news too, so a post there
# counts only with an AI word in its title.
LAB_DOMAINS = {"openai.com", "anthropic.com", "claude.com", "deepmind.google", "ai.meta.com", "mistral.ai", "microsoft.ai"}
LAB_DOMAINS_NEED_AI_CUE = {"blog.google", "ai.google.dev"}
EVALUATOR_DOMAINS = {"metr.org"}
PAPER_DOMAINS = {"arxiv.org", "huggingface.co"}      # a research paper: one of the edition's stories is kept for one (RESERVED_PAPER_SLOTS)
RESERVED_PAPER_SLOTS = 1
AGGREGATOR_DOMAINS = {"huggingface.co", "news.ycombinator.com"}      # say where it was seen, not who else reported it

LAUNCH_CUE = re.compile(r"\b(introducing|introduces|introduced|launch(?:es|ed)?|announc(?:es|ing|ed)|unveil(?:s|ed|ing)?|now available|"
                        r"generally available|available today|release[sd]?|rolls? out|open[- ]sourc(?:es|ed))\b", re.I)
AI_CUE = re.compile(r"\b(ai|gpt[- ]?\d[\w.]*|gemini|claude|llama|model|models|agent|agents|agentic|codex|genie|sora|veo|imagen|chatgpt|"
                    r"deepmind|alpha\w+|benchmark|reasoning|open[- ]weights?|robotics?)\b", re.I)

# A lab explaining how its own system works, or reporting its own measurements, is a primary document, unlike its customer stories.
TECH_CUE = re.compile(r"\b(inside|under the hood|how we|system card|technical report|capability|capabilities|architecture|sandbox)\b", re.I)

SIG_LAUNCH = 6        # a frontier lab's own launch, tier A
SIG_LAB_POST = 2      # any other post from such a lab, and a frontier evaluator's report
SIG_LAB_TECH = 4      # ...when it is the lab's own technical account (TECH_CUE)
SIG_CORROBORATION = 2   # per distinct other company that reported it, up to three
SIG_CORROBORATION_MAX = 3
HN_STEPS = ((600, 6), (300, 4), (100, 2))       # (points, significance)
SIG_MAX = 12
BACKLOG_BOOST = 6     # a link Varun saved himself: he decided it is worth reading


def significance(*, title: str, domain: str, tier: str, corroborators=(), hn_points: int = 0) -> tuple[int, list[str]]:
    """What made this a story independent of Varun's interests: (points, reasons). `corroborators` is the domains of other pages that reported it."""
    points, reasons = 0, []
    domain = (domain or "").lower()
    has_ai = bool(AI_CUE.search(title or ""))
    if tier == "A":
        lab = domain in LAB_DOMAINS or (domain in LAB_DOMAINS_NEED_AI_CUE and has_ai)
        if lab and LAUNCH_CUE.search(title or "") and (has_ai or domain in LAB_DOMAINS):
            points += SIG_LAUNCH
            reasons.append("frontier-lab launch")
        elif (lab or domain in EVALUATOR_DOMAINS) and TECH_CUE.search(title or ""):
            points += SIG_LAB_TECH
            reasons.append("frontier lab or evaluator technical account")
        elif lab or domain in EVALUATOR_DOMAINS:
            points += SIG_LAB_POST
            reasons.append("frontier lab or evaluator post")
    own = company_of(domain)
    others = {company_of(d) for d in corroborators if d and d not in AGGREGATOR_DOMAINS} - {own}
    if others:
        n = min(len(others), SIG_CORROBORATION_MAX)
        points += SIG_CORROBORATION * n
        reasons.append(f"reported by {n} other compan{'y' if n == 1 else 'ies'}")
    for floor, value in HN_STEPS:
        if hn_points >= floor:
            points += value
            reasons.append(f"{hn_points} Hacker News points")
            break
    return min(points, SIG_MAX), reasons


def rank_of(c: dict, rings: dict) -> int:
    """The number a candidate is ordered by. `c` needs: topics, title, domain, tier, corroborators, hn_points, backlog."""
    sig, _ = significance(title=c["title"], domain=c["domain"], tier=c.get("tier", "unrated"),
                          corroborators=c.get("corroborators", ()), hn_points=c.get("hn_points", 0))
    return relevance(c.get("topics"), rings) + sig + (BACKLOG_BOOST if c.get("backlog") else 0)


def worth_keeping(title: str, domain: str, tier: str) -> bool:
    """A candidate that matches none of Varun's terms is still stored when significance alone gives it a place (a lab's own post)."""
    return significance(title=title, domain=domain, tier=tier)[0] > 0


# --- near duplicates -----------------------------------------------------------------------------------------------------------------------
_TITLE_STOP = set("the a an of to in on for and or with is are was be as at by from this that it its new how why what who you your we our can will "
                  "has have not more than into about over after now".split())


def title_words(title: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9][a-z0-9.-]*", (title or "").lower()) if w not in _TITLE_STOP)


def near_duplicate(a: dict, b: dict) -> bool:
    """One company's two pages with the same headline words: the same launch posted on two of its sites (Gemini 4 Neon on deepmind.google and on
    blog.google took two of a run's slots). Needs at least two words each and one title's words to contain the other's."""
    if company_of(a["domain"]) != company_of(b["domain"]):
        return False
    wa, wb = title_words(a["title"]), title_words(b["title"])
    return min(len(wa), len(wb)) >= 2 and (wa <= wb or wb <= wa)


# --- diversity ---------------------------------------------------------------------------------------------------------------------------
class Caps:
    """Counts per key against a limit. `allows` before taking, `take` once taken."""

    def __init__(self, **limits):
        self.limits, self.counts = limits, {name: defaultdict(int) for name in limits}

    def allows(self, **keys) -> bool:
        return all(key is None or self.counts[name][key] < self.limits[name] for name, key in keys.items())

    def take(self, **keys) -> None:
        for name, key in keys.items():
            if key is not None:
                self.counts[name][key] += 1


def _order(cands: list, rings: dict) -> list:
    return sorted(cands, key=lambda c: (-rank_of(c, rings), -(c["discovered_at"].timestamp() if isinstance(c.get("discovered_at"), datetime) else c.get("discovered_at", 0))))


SIGNIFICANCE_SLOTS = 12       # of a run's fetch slots, held for what is significant in itself: launches, lab and evaluator posts, widely reported stories
SIGNIFICANCE_PER_COMPANY = 4  # ...and no company takes more than this many of those, so a lab's week of customer stories cannot fill them


def sig_of(c: dict) -> int:
    return significance(title=c["title"], domain=c["domain"], tier=c.get("tier", "unrated"),
                        corroborators=c.get("corroborators", ()), hn_points=c.get("hn_points", 0))[0]


def choose_fetches(cands: list, rings: dict, limit: int, *, surfaceable, blocked=lambda c: False, per_domain: int = 4,
                   per_company: int = MAX_FETCHES_PER_COMPANY, reserved: int = SIGNIFICANCE_SLOTS,
                   reserved_per_company: int = SIGNIFICANCE_PER_COMPANY) -> list:
    """The candidates to pay a fetch for, best first. Two lanes, so neither the profile nor the news decides alone.

    Lane one: up to `reserved` slots for candidates with significance of their own (a frontier lab's launch, an evaluator's report, a story
    other outlets carried), best rank first, at most `reserved_per_company` per company. Lane two: every remaining slot by rank, whoever it is.
    A company never takes more than `per_company` of all the slots, nor a domain more than `per_domain`.
    """
    ordered = [c for c in _order(cands, rings) if rank_of(c, rings) > 0 and not blocked(c)]
    chosen, taken = [], set()
    caps, lane_caps = Caps(domain=per_domain, company=per_company), Caps(company=reserved_per_company)

    def take(c, lane_one: bool) -> bool:
        keys = {"domain": c["domain"], "company": company_of(c["domain"])}
        if id(c) in taken or any(near_duplicate(c, o) for o in chosen) or not caps.allows(**keys) or (lane_one and not lane_caps.allows(company=keys["company"])) or not surfaceable(c):
            return False
        caps.take(**keys)
        if lane_one:
            lane_caps.take(company=keys["company"])
        taken.add(id(c))
        chosen.append(c)
        return True

    for c in ordered:
        if len(chosen) >= min(reserved, limit):
            break
        if sig_of(c) > 0:
            take(c, True)
    for c in ordered:
        if len(chosen) >= limit:
            break
        take(c, False)
    return _order(chosen, rings)


def is_paper(c: dict) -> bool:
    return (c.get("domain") or "").lower() in PAPER_DOMAINS


def walk_stories(cands: list, rings: dict, limit: int, take, reserved: int = RESERVED_PAPER_SLOTS) -> list:
    """Offer candidates to `take` until `limit` are taken, with `reserved` places held for papers. Returns the taken candidates, best rank first.

    `take(c)` applies the caller's caps and checks and returns True when it took the candidate. The papers (arXiv and Hugging Face) that score
    above zero on relevance are offered first, best first, until `reserved` are taken; then everyone else, best first. Offering the paper first
    is what makes the place real: the caps are shared, and on 2026-11-22's replay the three launches that came first filled the agents cluster's
    three places, so a paper offered afterwards could never fit. The caps still hold (a paper counts against its company and its cluster like
    any story), and a paper the caller refuses, or a week without one, gives the place back to the rest."""
    ordered = _order(cands, rings)
    taken: list = []
    if reserved:
        for c in ordered:
            if len(taken) >= min(reserved, limit):
                break
            if is_paper(c) and relevance(c.get("topics"), rings) > 0 and take(c):
                taken.append(c)
    for c in ordered:
        if len(taken) >= limit:
            break
        if c not in taken and take(c):
            taken.append(c)
    position = {id(c): i for i, c in enumerate(ordered)}
    return sorted(taken, key=lambda c: position[id(c)])


def choose_stories(cands: list, rings: dict, limit: int, *, per_company: int = MAX_STORIES_PER_COMPANY, per_cluster: int = MAX_PER_CLUSTER,
                   usable=lambda c: True, reserved_papers: int = RESERVED_PAPER_SLOTS) -> list:
    """The stories of the edition from the fetched candidates: at most `per_company` of one company and `per_cluster` of one interest cluster,
    and a place kept for a paper (arXiv or Hugging Face) that scores above zero, inside those same caps."""
    chosen, caps = [], Caps(company=per_company, cluster=per_cluster)

    def take(c) -> bool:
        keys = {"company": company_of(c["domain"]), "cluster": cluster_of(c.get("topics"), rings)}
        if not caps.allows(**keys) or not usable(c) or any(near_duplicate(c, o) for o in chosen):
            return False
        caps.take(**keys)
        chosen.append(c)
        return True

    return walk_stories(cands, rings, limit, take, reserved_papers)


# --- loading from the database -----------------------------------------------------------------------------------------------------------
def load_rings(conn, user_id: str) -> dict:
    return dict(conn.execute("SELECT topic, ring FROM interest_nodes WHERE user_id = %s", (user_id,)).fetchall())


def load_candidates(conn, rows: list) -> list[dict]:
    """Candidate dicts for item rows (id, url, domain, title, relevance_terms, discovered_at, hn_points, tier)."""
    ids = [r[0] for r in rows]
    corr = defaultdict(list)
    for item_id, domain, tier in conn.execute(
            "SELECT i.item_id, s.domain, s.tier FROM item_sources i JOIN sources s ON s.id = i.source_id "
            "WHERE i.item_id = ANY(%s::uuid[]) AND i.role = 'corroboration' AND s.tier IN ('A', 'B', 'C')", (ids,)).fetchall():
        corr[item_id].append(domain)
    return [{"id": r[0], "url": r[1], "domain": r[2], "title": r[3], "topics": list(r[4] or []), "discovered_at": r[5],
             "hn_points": r[6], "tier": r[7], "corroborators": corr.get(r[0], []), "backlog": "from your backlog" in (r[4] or [])}
            for r in rows]
