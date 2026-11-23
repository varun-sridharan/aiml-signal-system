"""Run 12 (2026-11-23): the follow-ups to Run 11's ranking fix (debrief 21 in autopilot/M1.md).

    python -m unittest tests.test_followups -v

One class per step: TechCrunch's feed (and why VentureBeat's is not there), the discoveries table that keeps every drop, the place kept for a
paper, and the new interest. The replay of the recorded week is in tests/test_rank.py (ReplayOfWeek48), which these changes must not break.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, graph, items, rank, reader, sources  # noqa: E402
from tests._db import needs_db  # noqa: E402
from tests.test_rank import NEW_TOPIC, RINGS, cand  # noqa: E402

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
FEEDS = {f.id: f for f in reader.FEEDS}


class TechCrunchFeed(unittest.TestCase):
    def test_the_feed_is_registered_at_the_address_its_category_page_serves(self):
        feed = FEEDS["techcrunch-ai"]
        self.assertEqual((feed.url, feed.domain, feed.kind, feed.source_id),
                         ("https://techcrunch.com/category/artificial-intelligence/feed/", "techcrunch.com", "rss", "techcrunch-ai"))

    def test_the_recorded_feed_parses_to_dated_techcrunch_items(self):
        got = reader.parse((REPO / "tests/fixtures/feeds-2026-11-23/techcrunch-ai.xml").read_bytes(), FEEDS["techcrunch-ai"])
        self.assertGreaterEqual(len(got), 5)
        self.assertTrue(all(c.url.startswith("https://techcrunch.com/") and c.published for c in got))

    def test_the_listed_source_is_tier_c_with_the_domain(self):
        row = {s.id: s for s in sources.load_digest_rows()}["techcrunch-ai"]
        self.assertEqual((row.tier, row.domain), ("C", "techcrunch.com"))

    def test_venturebeat_is_not_added_because_no_address_could_be_read(self):
        self.assertFalse(any("venturebeat" in f.url for f in reader.FEEDS))      # 429 from a security checkpoint on both its feed and its page, 2026-11-23

    def test_a_techcrunch_report_counts_as_another_company_reporting_a_launch(self):
        alone = rank.significance(title="Introducing GPT-9 Astra", domain="openai.com", tier="A")
        with_tc = rank.significance(title="Introducing GPT-9 Astra", domain="openai.com", tier="A", corroborators=["techcrunch.com"])
        self.assertEqual(with_tc[0] - alone[0], rank.SIG_CORROBORATION)
        self.assertIn("reported by 1 other company", with_tc[1])
        self.assertNotIn("techcrunch.com", rank.AGGREGATOR_DOMAINS)

    def test_two_outlets_count_twice_and_the_owners_own_site_not_at_all(self):
        two = rank.significance(title="x", domain="example.com", tier="C", corroborators=["techcrunch.com", "thenewstack.io"])[0]
        self.assertEqual(two, 2 * rank.SIG_CORROBORATION)
        self.assertEqual(rank.significance(title="x", domain="techcrunch.com", tier="C", corroborators=["techcrunch.com"])[0], 0)


@needs_db
class TechCrunchCorroboratesInTheLedger(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        reader.prepare(self.conn)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_a_techcrunch_item_about_a_launch_becomes_corroboration_of_the_labs_item(self):
        lab = reader.Candidate("https://openai.com/index/zz-introducing-gpt-9-astra-sol", "Introducing GPT-9 Astra and Sol models for agentic coding",
                               "", NOW, FEEDS["openai"])
        tc = reader.Candidate("https://techcrunch.com/2026/11/23/zz-openai-introduces-gpt-9-astra-and-sol/",
                              "OpenAI introduces GPT-9 Astra and Sol models for agentic coding", "", NOW, FEEDS["techcrunch-ai"])
        kept = reader.build_items(self.conn, [lab, tc], NOW, reader.Stats())
        lab_id = next(i for i, c in kept if c.url == lab.url)
        row = next(r for r in rank.load_candidates(self.conn, self.conn.execute(
            "SELECT i.id, i.canonical_url, i.domain, i.title, i.relevance_terms, i.discovered_at, i.hn_points, s.tier FROM items i "
            "JOIN sources s ON s.id = i.source_id WHERE i.id = %s", (lab_id,)).fetchall()))
        self.assertIn("techcrunch.com", row["corroborators"])
        sig, reasons = rank.significance(title=row["title"], domain=row["domain"], tier=row["tier"], corroborators=row["corroborators"])
        self.assertIn("reported by 1 other company", reasons)
        self.assertGreater(sig, rank.SIG_LAUNCH)


@needs_db
class DiscoveriesKeepEveryDrop(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        reader.prepare(self.conn)
        self.conn.execute("DELETE FROM discoveries")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def c(self, url, title, feed="openai", published=NOW, points=0):
        return reader.Candidate(url, title, "", published, FEEDS[feed], points=points)

    def row(self, url):
        return self.conn.execute("SELECT title, domain, feed, score, terms, hn_points, outcome FROM discoveries WHERE url = %s", (url,)).fetchone()

    def test_a_score_zero_candidate_is_stored_with_its_score_terms_and_outcome(self):
        stats = reader.Stats()
        reader.build_items(self.conn, [self.c("https://example.com/zz-sourdough", "A recipe for sourdough", feed="hacker-news", points=12)], NOW, stats)
        self.assertEqual(stats.irrelevant, 1)
        self.assertEqual(self.row("https://example.com/zz-sourdough"), ("A recipe for sourdough", "example.com", "hacker-news", 0, [], 12, "irrelevant"))

    def test_a_kept_candidate_is_stored_with_the_terms_that_scored_it(self):
        reader.build_items(self.conn, [self.c("https://openai.com/index/zz-mcp", "OpenAI adds MCP servers to Codex")], NOW, reader.Stats())
        title, domain, feed, score, terms, pts, outcome = self.row("https://openai.com/index/zz-mcp")
        self.assertEqual((outcome, domain, feed), ("kept", "openai.com", "openai"))
        self.assertIn("MCP architecture", terms)
        self.assertGreater(score, 0)

    def test_a_candidate_outside_the_window_is_stored_as_such(self):
        stats = reader.Stats()
        reader.build_items(self.conn, [self.c("https://openai.com/index/zz-old", "MCP old news", published=NOW - timedelta(days=40))], NOW, stats)
        self.assertEqual(stats.out_of_window, 1)
        self.assertEqual(self.row("https://openai.com/index/zz-old")[6], "out_of_window")

    def test_a_labs_launch_that_matches_nothing_is_kept_and_recorded_kept(self):
        reader.build_items(self.conn, [self.c("https://anthropic.com/news/zz-ipo", "Anthropic announces closing of initial public offering",
                                              feed="anthropic-news")], NOW, reader.Stats())
        self.assertEqual(self.row("https://anthropic.com/news/zz-ipo")[3:], (0, [], 0, "kept"))

    def test_seen_again_it_keeps_first_seen_and_the_most_points(self):
        url = "https://example.com/zz-again"
        reader.build_items(self.conn, [self.c(url, "Nothing here", feed="hacker-news", points=300)], NOW, reader.Stats())
        reader.build_items(self.conn, [self.c(url, "Nothing here", feed="hacker-news", points=40)], NOW + timedelta(days=2), reader.Stats())
        first, last, pts = self.conn.execute("SELECT first_seen, last_seen, hn_points FROM discoveries WHERE url = %s", (url,)).fetchone()
        self.assertEqual((first, last, pts), (NOW, NOW + timedelta(days=2), 300))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM discoveries WHERE url = %s", (url,)).fetchone()[0], 1)

    def test_rows_not_seen_for_sixty_days_are_pruned_and_newer_ones_kept(self):
        for url, age in (("https://example.com/zz-61", 61), ("https://example.com/zz-59", 59)):
            items.record_discovery(self.conn, url, "t", "example.com", "hacker-news", 0, [], 0, "irrelevant", NOW - timedelta(days=age))
        self.assertEqual(items.prune_discoveries(self.conn, NOW), 1)
        self.assertIsNone(self.row("https://example.com/zz-61"))
        self.assertIsNotNone(self.row("https://example.com/zz-59"))
        self.assertEqual(items.DISCOVERY_KEEP_DAYS, 60)

    def test_a_reader_run_prunes(self):
        items.record_discovery(self.conn, "https://example.com/zz-stale", "t", "example.com", "hacker-news", 0, [], 0, "irrelevant", NOW - timedelta(days=90))
        stats = reader.Stats()
        stats.discoveries_pruned = items.prune_discoveries(self.conn, NOW)
        self.assertEqual(stats.discoveries_pruned, 1)
        self.assertIn("discoveries_pruned", stats.as_dict())


# Ten launches in ten clusters of their own, all ranking above an adjacent paper, so only the reservation can let the paper in.
LAUNCH_RINGS = dict(RINGS, **{f"launch topic {i}": "core" for i in range(10)})


def paper(title, topics, domain="arxiv.org", at=0.0):
    return cand(title, domain, topics, tier="B", at=at)


class OnePlaceForAPaper(unittest.TestCase):
    def pick(self, cands, limit=7, **kw):
        return rank.choose_stories(cands, LAUNCH_RINGS, limit, **kw)

    def launches(self, n, start=10):
        return [cand(f"Introducing model {i}", f"lab{i}.example", [f"launch topic {i}"], at=start + i) for i in range(n)]

    def test_a_paper_that_scores_below_the_other_stories_still_gets_a_place(self):
        papers = [paper("A paper on evals", ["evals"])]
        picked = self.pick(self.launches(10) + papers)
        self.assertEqual(len(picked), 7)
        self.assertIn("arxiv.org", [c["domain"] for c in picked])

    def test_without_the_reservation_it_would_have_been_left_out(self):
        picked = self.pick(self.launches(10) + [paper("A paper on evals", ["evals"])], reserved_papers=0)
        self.assertNotIn("arxiv.org", [c["domain"] for c in picked])

    def test_exactly_one_place_is_reserved_not_all_of_them(self):
        papers = [paper(f"Paper {i}", ["evals"], at=i) for i in range(5)]
        picked = self.pick(self.launches(10) + papers)
        self.assertEqual(sum(c["domain"] == "arxiv.org" for c in picked), 1)

    def test_hugging_face_papers_count_as_papers(self):
        picked = self.pick(self.launches(10) + [paper("Daily paper", ["evals"], domain="huggingface.co")])
        self.assertIn("huggingface.co", [c["domain"] for c in picked])

    def test_a_paper_that_matches_nothing_holds_no_place(self):
        picked = self.pick(self.launches(10) + [paper("A paper on proteins", [])])
        self.assertNotIn("arxiv.org", [c["domain"] for c in picked])
        self.assertEqual(len(picked), 7)

    def test_a_week_with_no_paper_fills_all_seven_places_with_the_rest(self):
        self.assertEqual(len(self.pick(self.launches(10))), 7)

    def test_a_paper_the_checks_refuse_gives_its_place_back(self):
        picked = self.pick(self.launches(10) + [paper("A paper on evals", ["evals"])], usable=lambda c: c["domain"] != "arxiv.org")
        self.assertEqual(len(picked), 7)

    def test_the_cluster_cap_still_holds_with_the_paper_inside_it(self):
        agents = [cand(f"Agent story {i}", f"site{i}.org", ["multi-agent systems"], tier="C", at=i + 10) for i in range(6)]
        agents += [paper("Agent paper", ["multi-agent systems"], at=0)]
        picked = self.pick(agents + self.launches(6))
        self.assertEqual(sum(rank.cluster_of(c["topics"], LAUNCH_RINGS) == "agents" for c in picked), rank.MAX_PER_CLUSTER)
        self.assertIn("arxiv.org", [c["domain"] for c in picked])          # one of the three agent places is the paper's

    def test_the_company_cap_still_holds_for_papers(self):
        papers = [paper(f"Paper {i}", [f"topic {i}"], at=i) for i in range(5)]
        rings = dict(LAUNCH_RINGS, **{f"topic {i}": "adjacent" for i in range(5)})
        picked = rank.choose_stories(self.launches(10) + papers, rings, 7, reserved_papers=2)
        self.assertLessEqual(sum(c["domain"] == "arxiv.org" for c in picked), rank.MAX_STORIES_PER_COMPANY)

    def test_the_stories_come_back_best_rank_first(self):
        picked = self.pick(self.launches(10) + [paper("A paper on evals", ["evals"])])
        ranks = [rank.rank_of(c, LAUNCH_RINGS) for c in picked]
        self.assertEqual(ranks, sorted(ranks, reverse=True))


@needs_db
class WeeklyHoldsAPlace(unittest.TestCase):
    def test_build_raw_day_uses_the_reservation(self):
        import inspect
        from agents import weekly
        src = inspect.getsource(weekly.build_raw_day)
        self.assertIn("rank.walk_stories", src)
        self.assertEqual(rank.RESERVED_PAPER_SLOTS, 1)


class TheNewInterest(unittest.TestCase):
    ENTRA = "AI is changing access. See how Microsoft Entra helps you stay ahead."

    def test_it_is_in_the_profile_as_an_adjacent_interest(self):
        profile = json.loads((REPO / "config" / "profile.json").read_text(encoding="utf-8"))
        self.assertIn(NEW_TOPIC, profile["interests"]["adjacent"])
        self.assertNotIn(NEW_TOPIC, profile["interests"]["core"])

    def test_the_entra_post_matches_it_and_scores_above_zero(self):
        self.assertEqual(graph.topics_in(self.ENTRA, [NEW_TOPIC]), [NEW_TOPIC])
        self.assertGreater(rank.relevance([NEW_TOPIC], dict(RINGS, **{NEW_TOPIC: "adjacent"})), 0)

    def test_the_bare_words_agent_and_security_recognise_nothing(self):
        for text in ("Work IQ agent is generally available", "Agents everywhere", "A Windows security update", "Security patch for Chrome",
                     "Introducing a new coding agent", "AI agents for supply chains"):
            self.assertNotIn(NEW_TOPIC, graph.topics_in(text, [NEW_TOPIC]), text)

    def test_its_phrases_do_match(self):
        for text in ("Prompt injection defences for browsing agents", "Who is liable? Agent liability and the law", "Zero trust for AI agents",
                     "Securing agent identity with Entra", "New AI security guidance"):
            self.assertIn(NEW_TOPIC, graph.topics_in(text, [NEW_TOPIC]), text)

    def test_over_the_recorded_week_it_matches_four_titles_and_none_is_unrelated(self):
        rec = json.loads((REPO / "tests/fixtures/w48-candidates.json").read_text(encoding="utf-8"))
        hits = {c["title"] for c in rec["candidates"] + rec["late_candidates"] if graph.topics_in(c["title"], [NEW_TOPIC])}
        self.assertEqual(len(hits), 4, hits)
        self.assertIn(self.ENTRA, hits)
        for title in hits:
            self.assertTrue(any(w in title.lower() for w in ("injection", "security", "entra")), title)      # each is about security or identity

    def test_the_topic_has_its_own_cluster_so_it_does_not_crowd_the_others(self):
        rings = dict(RINGS, **{NEW_TOPIC: "adjacent"})
        self.assertEqual(rank.cluster_of([NEW_TOPIC], rings), "topic:" + NEW_TOPIC)


@needs_db
class TheNewInterestInTheLedger(unittest.TestCase):
    def test_the_entra_post_scores_two_and_a_vendor_agent_post_still_scores_nothing_for_it(self):
        conn = budget.connect(autocommit=False)
        try:
            reader.prepare(conn)
            score, topics = graph.score(conn, "varun", "AI is changing access. See how Microsoft Entra helps you stay ahead.")
            self.assertEqual((score, topics), (2, [NEW_TOPIC]))
            self.assertNotIn(NEW_TOPIC, graph.score(conn, "varun", "A new agent for your inbox is generally available")[1])
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
