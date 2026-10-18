"""The interest graph: what the profile cares about, and a deterministic way to match it.

config/profile.json lists interests in three rings (core, adjacent, explore). This loads
them into `interest_nodes`, with the terms that recognise each topic in `interest_terms`,
and scores a candidate's title and summary against them. No model is involved: the first
relevance filter in Reader is free, deterministic and testable, so the paid fetch only
happens for candidates that pass it.

There are no edges between topics yet. The profile does not say how its topics relate,
and inventing relations would be a guess dressed as structure; edges arrive with the
Tuner, which learns them from feedback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO / "config" / "profile.json"

# WHAT: ring weights for the relevance score.
# CONCEPT: interest graph. A core topic is what the profile is for; adjacent topics are
# worth knowing about; explore is a probe for new interests. Weights only order candidates
# against one another, so their ratio matters and their scale does not.
RING_WEIGHT = {"core": 3, "adjacent": 2, "explore": 1}

# WHAT: extra recognisable terms per topic, beyond the words of its own name.
# CONCEPT: alias table. "MCP architecture" never appears in a headline, but "MCP" and
# "Model Context Protocol" do. Kept short and explicit so every match is explainable; a
# topic missing here still matches on its own words.
ALIASES = {
    "multi-agent systems": ["multi-agent", "multi agent", "multiagent", "agent swarm", "agent orchestration"],
    "reinforcement learning": ["reinforcement learning", "rlhf", "rlvr", "grpo", "policy gradient"],
    "mcp architecture": ["mcp", "model context protocol"],
    "agentic coding": ["agentic coding", "coding agent", "claude code", "codex", "swe-bench", "terminal-bench"],
    "model releases": ["model release", "releases", "new model", "open-weight", "open weights"],
    "serving & inference": ["inference", "serving", "vllm", "latency", "throughput", "quantization"],
    "evals": ["eval", "evals", "benchmark", "benchmarks", "leaderboard"],
    "harness engineering": ["harness", "scaffold", "agent loop", "tool use", "tool-use"],
}


# Words that appear in topic names but recognise nothing on their own ("model" is in most
# AI headlines). A topic is recognised by its full name, its aliases and its distinctive words.
GENERIC_WORDS = frozenset({"systems", "architecture", "learning", "engineering", "serving", "inference", "coding",
                           "model", "models", "releases", "agents", "agentic"})


def terms_for(topic: str) -> list[str]:
    """Terms that recognise a topic: its name, its aliases, and its distinctive words."""
    key = topic.strip().lower()
    terms = {key, *ALIASES.get(key, [])}
    for word in re.findall(r"[a-z][a-z-]{3,}", key):
        if word not in GENERIC_WORDS:
            terms.add(word)
    return sorted(t for t in terms if len(t) >= 2)


def load_profile(path: Path = PROFILE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sync(conn, profile: dict | None = None) -> int:
    """Make the stored graph for this profile's user match the profile. Returns topic count."""
    profile = profile or load_profile()
    user = profile["user_id"]
    # A topic listed in two rings keeps its most important one.
    best: dict[str, str] = {}
    for ring in ("core", "adjacent", "explore"):
        for topic in profile["interests"].get(ring, []):
            best.setdefault(topic, ring)
    wanted = {(topic, ring) for topic, ring in best.items()}
    with conn.transaction():
        conn.execute("DELETE FROM interest_nodes WHERE user_id = %s AND (topic, ring) NOT IN "
                     "(SELECT * FROM unnest(%s::text[], %s::text[]))",
                     (user, [t for t, _ in wanted], [r for _, r in wanted]))
        for topic, ring in sorted(wanted):
            conn.execute("INSERT INTO interest_nodes (user_id, topic, ring) VALUES (%s, %s, %s) "
                         "ON CONFLICT (user_id, topic) DO UPDATE SET ring = EXCLUDED.ring", (user, topic, ring))
            conn.execute("DELETE FROM interest_terms WHERE user_id = %s AND topic = %s", (user, topic))
            for term in terms_for(topic):
                conn.execute("INSERT INTO interest_terms (user_id, topic, term) VALUES (%s, %s, %s)", (user, topic, term))
    return len(wanted)


def score(conn, user_id: str, text: str) -> tuple[int, list[str]]:
    """Relevance of a text to the stored graph: (score, matched topics). Zero means none."""
    haystack = " " + re.sub(r"[^a-z0-9&+#-]+", " ", text.lower()) + " "
    rows = conn.execute(
        "SELECT n.topic, n.ring, t.term FROM interest_nodes n "
        "JOIN interest_terms t USING (user_id, topic) WHERE n.user_id = %s", (user_id,)).fetchall()
    best: dict[str, int] = {}
    for topic, ring, term in rows:
        needle = " " + re.sub(r"[^a-z0-9&+#-]+", " ", term).strip() + " "
        if needle in haystack:
            best[topic] = max(best.get(topic, 0), RING_WEIGHT[ring])
    return sum(best.values()), sorted(best)
