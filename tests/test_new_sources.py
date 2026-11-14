"""Run 11 (2026-11-14): Edition 05 missed "Anthropic launches Claude for Manufacturing". These tests pin the fix.

    python -m unittest tests.test_new_sources -v

The story was never discovered: Anthropic posts launches at claude.com/resources/product-updates and nothing read it, and Manufacturing Dive's
feed was not read either. The fixtures in tests/fixtures/feeds-2026-11-14 are those pages as fetched that day (the index pages with their scripts
and drawings removed; the long feeds cut to their first three items). No test touches the network.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, graph, items, reader, sources  # noqa: E402
from tests._db import needs_db  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "feeds-2026-11-14"
PRIMARY = "https://claude.com/resources/articles/claude-for-manufacturing"
TITLE = "Anthropic launches ‘Claude for Manufacturing’"
EXCERPT = ("Claude for Manufacturing brings Claude to plant operations, supply chain planning and ERP data, "
           "with connectors for manufacturing systems.")
FEEDS = {f.id: f for f in reader.FEEDS}
NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)


def parse(feed_id: str, name: str):
    return reader.parse((FIX / name).read_bytes(), FEEDS[feed_id])


class Registry(unittest.TestCase):
    def test_the_six_feeds_are_registered_with_their_evidence(self):
        for fid in ("claude-product-updates", "manufacturing-dive", "sap-news", "workday-newsroom", "dynamics-365-blog", "infor-blog"):
            self.assertIn(fid, FEEDS)
        self.assertEqual(len(reader.FEEDS), 23)
        self.assertEqual(len({f.id for f in reader.FEEDS}), 23)
        self.assertNotIn("oracle", " ".join(FEEDS))                       # left out, with the reason in the FEEDS comment
        text = (REPO / "agents" / "reader.py").read_text(encoding="utf-8")
        self.assertIn("Left out of that batch, and why. Oracle", text)

    def test_every_new_feed_belongs_to_a_source_the_seed_creates(self):
        known = {s.id for s in sources.load_seed()} | {s.id for s in sources.load_digest_rows()} | {"manufacturingdive-com"}
        for fid in ("claude-product-updates", "manufacturing-dive", "sap-news", "workday-newsroom", "dynamics-365-blog", "infor-blog"):
            self.assertIn(FEEDS[fid].source_id, known, fid)

    def test_the_new_sources_are_rated_in_the_catalogue(self):
        rows = {r.id: r for r in sources.load_digest_rows()}
        self.assertEqual((rows["claude-com"].tier, rows["claude-com"].domain), ("A", "claude.com"))
        self.assertEqual((rows["manufacturingdive-com"].tier, rows["manufacturingdive-com"].domain), ("C", "manufacturingdive.com"))
        self.assertEqual((rows["microsoft-dynamics-365"].tier, rows["microsoft-dynamics-365"].domain), ("A", "microsoft.com"))
        self.assertEqual([rows[i].domain for i in ("sap", "workday", "infor")], ["news.sap.com", "newsroom.workday.com", "infor.com"])


class ClaudeIndex(unittest.TestCase):
    def test_the_pattern_matches_the_primary_url_and_only_article_addresses(self):
        pattern = re.compile(FEEDS["claude-product-updates"].pattern)
        self.assertTrue(pattern.fullmatch(PRIMARY))
        for other in ("https://claude.com/resources/product-updates", "https://claude.com/resources/articles", "https://claude.com/pricing",
                      "https://www.anthropic.com/news/announcing-our-updated-responsible-scaling-policy"):
            self.assertFalse(pattern.fullmatch(other), other)

    def test_the_recorded_index_yields_the_missed_story_dated_and_titled(self):
        cards = {c.url: c for c in parse("claude-product-updates", "claude-product-updates.html")}
        self.assertEqual(len(cards), 10)
        story = cards[PRIMARY]
        self.assertEqual(story.title, "Introducing Claude for Manufacturing")
        self.assertEqual(story.published.date().isoformat(), "2026-11-12")
        self.assertTrue(all(c.published for c in cards.values()))


class Feeds(unittest.TestCase):
    def test_the_manufacturing_dive_feed_parses_and_carries_the_missed_story(self):
        got = parse("manufacturing-dive", "manufacturingdive-news.xml")
        self.assertEqual(len(got), 10)
        story = next(c for c in got if "Claude for Manufacturing" in c.title)
        self.assertEqual(story.title, TITLE)
        self.assertTrue(story.url.startswith("https://www.manufacturingdive.com/news/"))
        self.assertTrue(all(c.published for c in got))

    def test_each_vendor_feed_parses_with_dates_and_its_own_addresses(self):
        for fid, name, count, host in (("sap-news", "sap-news.xml", 3, "news.sap.com"),
                                       ("workday-newsroom", "workday-press-releases.xml", 5, "newsroom.workday.com"),
                                       ("dynamics-365-blog", "dynamics-365-blog.xml", 3, "microsoft.com")):
            got = parse(fid, name)
            self.assertEqual(len(got), count, fid)
            self.assertTrue(all(sources.domain_of(c.url) == host and c.published and c.title for c in got), fid)

    def test_the_infor_index_parses_dated_cards_and_includes_its_post_on_claude(self):
        got = {c.url: c for c in parse("infor-blog", "infor-blog.html")}
        self.assertEqual(len(got), 9)
        post = got["https://www.infor.com/blog/infor-anthropic-for-manufacturing-operations"]
        self.assertEqual(post.published.date().isoformat(), "2026-11-12")
        self.assertTrue(all(c.published for c in got.values()))


class Scoring(unittest.TestCase):
    """The four interests the profile always had are untouched; the story now clears the fetch cut-off."""

    def test_the_profile_has_the_four_new_interests(self):
        interests = graph.load_profile()["interests"]
        for topic in ("enterprise AI and ERP", "vertical AI agents (manufacturing, supply chain)"):
            self.assertIn(topic, interests["core"])
        for topic in ("AI vendor product launches", "ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)"):
            self.assertIn(topic, interests["adjacent"])
        self.assertEqual(interests["core"][:4], ["multi-agent systems", "reinforcement learning", "MCP architecture", "agentic coding"])

    def test_a_bare_word_that_means_something_else_matches_none_of_the_new_topics(self):
        # "dynamics" is physics, "chain" is a blockchain, "product" and "enterprise" are in half the week's headlines
        new = {"enterprise AI and ERP", "vertical AI agents (manufacturing, supply chain)", "AI vendor product launches",
               "ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)"}
        terms = {t for topic in new for t in graph.terms_for(topic)}
        for word in ("dynamics", "chain", "product", "enterprise", "vendor", "vertical", "supply", "microsoft", "competitor", "moves"):
            self.assertNotIn(word, terms)


@needs_db
class ScoringAndSurfacing(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        reader.prepare(self.conn)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def score(self, text):
        return graph.score(self.conn, "varun", text)

    def test_the_story_scores_above_zero_and_above_the_cut_off_the_week_needed(self):
        title_only = self.score(TITLE)
        self.assertGreater(title_only[0], 2)                     # it scored 2 before (model releases, through "claude"); 2 was below the fetch cut-off
        self.assertEqual(title_only[0], 7)
        self.assertEqual(title_only[1], ["AI vendor product launches", "model releases", "vertical AI agents (manufacturing, supply chain)"])
        with_excerpt = self.score(f"{TITLE}. {EXCERPT}")
        self.assertGreater(with_excerpt[0], title_only[0])
        self.assertIn("enterprise AI and ERP", with_excerpt[1])

    def test_the_four_old_core_interests_still_match_as_before(self):
        for text, topic in (("Multi-agent systems that plan together", "multi-agent systems"), ("A reinforcement learning recipe", "reinforcement learning"),
                            ("MCP adds file uploads", "MCP architecture"), ("Claude Code ships an agentic coding mode", "agentic coding")):
            self.assertIn(topic, self.score(text)[1], text)
        self.assertEqual(self.score("Gemini 4 Hydrogen in GitHub Copilot"), (5, ["agentic coding", "model releases"]))      # Edition 05's story, unchanged

    def test_physics_chains_and_enterprise_servers_do_not_pick_up_the_new_topics(self):
        for text in ("Chain of thought is not a plan", "Hamiltonian dynamics of training", "GitHub Enterprise Server support in the app"):
            self.assertFalse({"enterprise AI and ERP", "vertical AI agents (manufacturing, supply chain)", "AI vendor product launches"} & set(self.score(text)[1]), text)

    def test_the_missed_story_is_kept_surfaceable_and_selected_from_the_claude_index_alone(self):
        stats = reader.Stats()
        cards = parse("claude-product-updates", "claude-product-updates.html")
        kept = reader.build_items(self.conn, cards, NOW, stats)
        by_url = {c.url: iid for iid, c in kept}
        self.assertIn(PRIMARY, by_url)
        self.assertEqual(self.conn.execute("SELECT source_id FROM items WHERE id = %s", (by_url[PRIMARY],)).fetchone()[0], "claude-com")
        self.assertTrue(items.surfaceable(self.conn, by_url[PRIMARY]))        # tier A, so it may surface on its own
        chosen = [url for _, url in reader.select_for_fetch(self.conn, NOW)]
        self.assertIn(PRIMARY, chosen)

    def test_a_domain_met_before_the_list_named_it_is_promoted_from_unrated(self):
        self.conn.execute("UPDATE sources SET tier = 'unrated', reputation_prior = 'unrated' WHERE id = 'manufacturingdive-com'")
        sources.seed_digest(self.conn)
        self.assertEqual(self.conn.execute("SELECT tier, reputation_prior FROM sources WHERE id = 'manufacturingdive-com'").fetchone(), ("C", "medium"))
        self.conn.execute("UPDATE sources SET tier = 'B', reputation_prior = 'high' WHERE id = 'cio-dive'")           # a rating someone set is never touched
        sources.seed_digest(self.conn)
        self.assertEqual(self.conn.execute("SELECT tier FROM sources WHERE id = 'cio-dive'").fetchone()[0], "B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
