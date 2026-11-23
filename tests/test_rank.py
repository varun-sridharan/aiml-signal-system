"""Run 11 (2026-11-22): Edition 06 (2026-W48) was seven stories of Microsoft ERP and arXiv and missed the week's main stories.

    python -m unittest tests.test_rank -v

Root cause (debrief 20 in autopilot/M1.md): both the fetch slots and the edition were ordered by the interest score alone, and that score paid
a core 3 for the bare word "agent" and for "Copilot", so Microsoft's Work IQ posts and arXiv papers outranked every launch; nothing capped a
company; six of the ten stories were never discovered (feeds not read) or were dropped at score 0. Each rule of the fix has a test here, and the
replay at the end runs the recorded week through the new ranking: tests/fixtures/w48-candidates.json (373 stored candidates, plus the six that
only the live feeds showed) against tests/golden/w48-must-catch.json. Nothing here touches the network or a model; only the last two classes need
the database.
"""

from __future__ import annotations

import collections
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, graph, items, rank, reader, sources, weekly  # noqa: E402
from tests._db import needs_db  # noqa: E402

FIX = REPO / "tests" / "fixtures"
NEW_TOPIC = "AI security and agent liability"
RINGS = {"multi-agent systems": "core", "reinforcement learning": "core", "MCP architecture": "core", "agentic coding": "core",
         "enterprise AI and ERP": "core", "vertical AI agents (manufacturing, supply chain)": "core",
         "model releases": "adjacent", "serving & inference": "adjacent", "evals": "adjacent", "harness engineering": "adjacent",
         "AI vendor product launches": "adjacent", "ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)": "adjacent"}


def cand(title="x", domain="example.com", topics=(), tier="A", at=0.0, **kw):
    return {"id": title + domain, "url": f"https://{domain}/{abs(hash(title)) % 10**8}", "title": title, "domain": domain, "topics": list(topics),
            "tier": tier, "discovered_at": at, "corroborators": [], "hn_points": 0, "backlog": False, "surfaceable": True, **kw}


class Significance(unittest.TestCase):
    def sig(self, title, domain, tier="A", **kw):
        return rank.significance(title=title, domain=domain, tier=tier, **kw)[0]

    def test_a_frontier_labs_launch_counts_and_the_same_title_from_an_unrated_site_does_not(self):
        self.assertEqual(self.sig("Introducing GPT-6.2 Astra and Sol", "openai.com"), rank.SIG_LAUNCH)
        self.assertEqual(self.sig("Introducing GPT-6.2 Astra and Sol", "example.com"), 0)
        self.assertEqual(self.sig("Introducing GPT-6.2 Astra and Sol", "openai.com", tier="unrated"), 0)       # only a tier A source is believed on its own word

    def test_a_labs_customer_story_counts_less_than_its_launch_and_its_technical_account_more(self):
        story = self.sig("How Revolut raises ambition without lowering the bar", "openai.com")
        tech = self.sig("Inside the Codex sandbox", "openai.com")
        self.assertEqual((story, tech), (rank.SIG_LAB_POST, rank.SIG_LAB_TECH))
        self.assertLess(story, tech)
        self.assertLess(tech, rank.SIG_LAUNCH)

    def test_google_news_counts_only_with_an_ai_word_in_the_title(self):
        self.assertEqual(self.sig("Genie 4 is now available", "blog.google"), rank.SIG_LAUNCH)
        self.assertEqual(self.sig("Introducing the new Pixel Watch", "blog.google"), 0)
        self.assertEqual(self.sig("Holiday shopping tips in Search", "blog.google"), 0)

    def test_an_evaluators_report_counts(self):
        self.assertEqual(self.sig("AI R&D-relevant model capabilities are improving rapidly", "metr.org"), rank.SIG_LAB_TECH)
        self.assertEqual(self.sig("Notes from a workshop", "metr.org"), rank.SIG_LAB_POST)

    def test_corroboration_counts_distinct_other_companies_not_aggregators_or_itself(self):
        base = dict(title="Some story", domain="thenewstack.io", tier="C")
        self.assertEqual(rank.significance(**base, corroborators=["theverge.com"])[0], rank.SIG_CORROBORATION)
        self.assertEqual(rank.significance(**base, corroborators=["theverge.com", "wired.com", "ft.com", "bbc.com"])[0],
                         rank.SIG_CORROBORATION * rank.SIG_CORROBORATION_MAX)              # capped
        self.assertEqual(rank.significance(**base, corroborators=["huggingface.co", "news.ycombinator.com"])[0], 0)
        self.assertEqual(rank.significance(title="x", domain="openai.com", tier="C", corroborators=["openai.com"])[0], 0)   # itself is not a second source
        self.assertEqual(rank.significance(title="x", domain="deepmind.google", tier="C", corroborators=["blog.google"])[0], 0)   # nor is its other site

    def test_hacker_news_points_step_up(self):
        points = {pts: rank.significance(title="x", domain="example.com", tier="C", hn_points=pts)[0] for pts in (50, 100, 300, 600, 5000)}
        self.assertEqual(points, {50: 0, 100: 2, 300: 4, 600: 6, 5000: 6})

    def test_significance_is_capped(self):
        total = rank.significance(title="Introducing GPT-7", domain="openai.com", tier="A",
                                  corroborators=["a.com", "b.com", "c.com"], hn_points=900)[0]
        self.assertEqual(total, rank.SIG_MAX)

    def test_it_is_added_to_relevance_never_substituted_for_it(self):
        mine = cand("A paper on multi-agent reinforcement learning and MCP and evals", "example.org", ["multi-agent systems", "MCP architecture", "evals"], tier="C")
        lab = cand("Notes from the team", "openai.com", [])
        self.assertGreater(rank.rank_of(mine, RINGS), rank.rank_of(lab, RINGS))                        # what Varun follows still wins over a lab's ordinary post
        launch = cand("Introducing GPT-6.2", "openai.com", ["model releases"])
        self.assertGreater(rank.rank_of(launch, RINGS), rank.rank_of(mine, RINGS))                      # and a launch wins over what he follows
        both = cand("Introducing GPT-6.2 for agentic coding", "openai.com", ["agentic coding"])
        self.assertEqual(rank.rank_of(both, RINGS), rank.relevance(["agentic coding"], RINGS) + rank.SIG_LAUNCH)

    def test_a_saved_link_is_boosted(self):
        saved = cand("Anthropic launches Claude for Manufacturing", "manufacturingdive.com", [], tier="C", backlog=True)
        self.assertEqual(rank.rank_of(saved, RINGS), rank.BACKLOG_BOOST)

    def test_a_lab_launch_is_kept_even_when_it_matches_no_term_of_the_profile(self):
        self.assertTrue(rank.worth_keeping("Anthropic announces closing of its initial public offering", "anthropic.com", "A"))
        self.assertFalse(rank.worth_keeping("A recipe for sourdough", "example.com", "A"))
        self.assertFalse(rank.worth_keeping("Introducing GPT-6.2", "example.com", "unrated"))


class InterestRelevance(unittest.TestCase):
    def test_topics_that_read_the_same_text_count_once_in_full_and_a_little_more_each(self):
        work_iq = ["AI vendor product launches", "ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)", "agentic coding", "multi-agent systems"]
        self.assertEqual(sum(rank.RING_WEIGHT[RINGS[t]] for t in work_iq), 10)                          # what the old score paid for the Work IQ post
        self.assertEqual(rank.relevance(work_iq, RINGS), (2 + 1) + (3 + 1))                              # enterprise cluster: two adjacent topics, 2+1; agents: 3+1
        self.assertLess(rank.relevance(work_iq, RINGS), 10)
        with_core = work_iq + ["enterprise AI and ERP"]
        self.assertEqual(rank.relevance(with_core, RINGS), (3 + 2) + (3 + 1))                             # a third enterprise topic adds 1 more, to the cap of 2
        self.assertLess(rank.relevance(with_core, RINGS), sum(rank.RING_WEIGHT[RINGS[t]] for t in with_core))

    def test_a_cluster_adds_at_most_two_to_its_best_topic(self):
        every = ["multi-agent systems", "agentic coding", "MCP architecture", "harness engineering"]
        self.assertEqual(rank.relevance(every, RINGS), 3 + rank.CLUSTER_EXTRA_MAX)

    def test_separate_clusters_add_and_an_unknown_topic_is_skipped(self):
        self.assertEqual(rank.relevance(["evals", "MCP architecture", "from your backlog"], RINGS), 2 + 3)
        self.assertEqual(rank.relevance(None, RINGS), 0)

    def test_a_story_counts_against_the_cluster_of_its_heaviest_topic(self):
        self.assertEqual(rank.cluster_of(["model releases", "enterprise AI and ERP"], RINGS), "enterprise")
        self.assertEqual(rank.cluster_of(["evals"], RINGS), "models")
        self.assertIsNone(rank.cluster_of([], RINGS))


class AliasesAreNarrowed(unittest.TestCase):
    def topics(self, text):
        return set(graph.topics_in(text, RINGS))

    def test_the_bare_words_that_every_ai_post_carries_no_longer_make_a_core_topic(self):
        for text in ("Business Applications in Work IQ is now generally available",
                     "Unlock new possibilities for Dynamics 365 ERP with AI agents and business skills",
                     "Replication of Agentic Security Events: Audit and Investigation",
                     "Microsoft 365 Copilot adds agents to Teams"):
            self.assertFalse({"multi-agent systems", "agentic coding"} & self.topics(text), text)

    def test_the_topics_are_still_recognised_by_what_names_them(self):
        self.assertIn("multi-agent systems", self.topics("A multi-agent debate improves reasoning"))
        self.assertIn("multi-agent systems", self.topics("Sub-agents and agent orchestration in practice"))
        self.assertIn("agentic coding", self.topics("Gemini 4 Hydrogen in GitHub Copilot"))
        self.assertIn("agentic coding", self.topics("Inside the Codex sandbox"))
        self.assertIn("agentic coding", self.topics("Claude Code gets a new review mode"))

    def test_a_launch_post_is_not_a_vendor_launch_topic_for_saying_so(self):
        self.assertNotIn("AI vendor product launches", self.topics("Business Applications in Work IQ is now generally available"))
        self.assertNotIn("AI vendor product launches", self.topics("Acme launches a faster database"))
        self.assertIn("AI vendor product launches", self.topics("Acme announces a product launch for Q1"))

    def test_erp_is_one_topics_word_not_two(self):
        self.assertEqual(self.topics("Dynamics 365 ERP gets a new planner") & {"enterprise AI and ERP", "ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)"},
                         {"enterprise AI and ERP", "ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)"})
        self.assertNotIn("ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)", self.topics("An ERP rollout checklist"))

    def test_the_interests_are_still_in_the_profile(self):
        profile = graph.load_profile()
        self.assertIn("enterprise AI and ERP", profile["interests"]["core"])
        self.assertIn("vertical AI agents (manufacturing, supply chain)", profile["interests"]["core"])
        self.assertIn("AI vendor product launches", profile["interests"]["adjacent"])
        self.assertIn("ERP competitor moves (SAP, Oracle, Workday, Microsoft Dynamics)", profile["interests"]["adjacent"])


class Companies(unittest.TestCase):
    def test_a_companys_domains_are_one_company(self):
        self.assertEqual({rank.company_of(d) for d in ("microsoft.com", "microsoft.ai", "techcommunity.microsoft.com", "cosmosdb.microsoft.com")}, {"microsoft"})
        self.assertEqual({rank.company_of(d) for d in ("deepmind.google", "blog.google", "ai.google.dev")}, {"google"})
        self.assertEqual({rank.company_of(d) for d in ("anthropic.com", "claude.com")}, {"anthropic"})
        self.assertEqual(rank.company_of("some.sub.openai.com"), "openai")
        self.assertEqual(rank.company_of("arxiv.org"), "arxiv.org")
        self.assertNotEqual(rank.company_of("github.blog"), rank.company_of("microsoft.com"))


class FetchSlots(unittest.TestCase):
    def pick(self, cands, limit=20, **kw):
        return rank.choose_fetches(cands, RINGS, limit, surfaceable=lambda c: c["surfaceable"], **kw)

    def test_one_company_takes_at_most_five_slots_even_with_the_five_best_scores_and_more(self):
        many = [cand(f"Dynamics 365 ERP post {i}", "microsoft.com", ["enterprise AI and ERP", "agentic coding"], at=i) for i in range(12)]
        many += [cand(f"Dynamics 365 blog post {i}", "techcommunity.microsoft.com", ["enterprise AI and ERP"], at=i) for i in range(6)]
        others = [cand(f"Paper {i}", f"site{i}.org", ["evals"], tier="C", at=i) for i in range(15)]
        picked = self.pick(many + others)
        self.assertEqual(sum(rank.company_of(c["domain"]) == "microsoft" for c in picked), rank.MAX_FETCHES_PER_COMPANY)
        self.assertEqual(len(picked), 20)

    def test_a_domain_still_takes_at_most_four(self):
        papers = [cand(f"RL paper {i}", "arxiv.org", ["reinforcement learning"], at=i) for i in range(12)]
        self.assertEqual(len(self.pick(papers)), 4)

    def test_a_launch_beats_a_flood_of_papers_for_a_slot(self):
        papers = [cand(f"RL paper {i}", "arxiv.org", ["reinforcement learning", "evals", "multi-agent systems"], at=i) for i in range(30)]
        launch = cand("Introducing Gemini 4 Neon", "deepmind.google", [])
        picked = self.pick(papers + [launch], limit=3)
        self.assertIn(launch, picked)

    def test_the_significance_lane_is_shared_out_so_one_labs_stories_cannot_fill_it(self):
        lab = [cand(f"How customer {i} uses OpenAI", "openai.com", [], at=i) for i in range(10)]
        others = [cand("Introducing Gemini 4 Neon", "deepmind.google", []), cand("Anthropic announces its IPO", "anthropic.com", [])]
        picked = self.pick(lab + others, limit=6, reserved=6, reserved_per_company=rank.SIGNIFICANCE_PER_COMPANY)
        self.assertEqual(collections.Counter(rank.company_of(c["domain"]) for c in picked)["openai"], rank.SIGNIFICANCE_PER_COMPANY)
        self.assertIn(others[0], picked)
        self.assertIn(others[1], picked)

    def test_unsurfaceable_blocked_and_zero_rank_candidates_never_get_a_slot(self):
        ok = cand("MCP ships an update", "example.com", ["MCP architecture"], tier="C")
        hidden = cand("MCP news", "example.org", ["MCP architecture"], tier="C", surfaceable=False)
        blocked = cand("MCP again", "blocked.example", ["MCP architecture"], tier="C")
        nothing = cand("A recipe", "example.net", [], tier="C")
        picked = self.pick([ok, hidden, blocked, nothing], blocked=lambda c: c["domain"] == "blocked.example")
        self.assertEqual(picked, [ok])

    def test_the_same_launch_on_two_of_a_companys_sites_takes_one_slot(self):
        one = cand("Introducing Gemini 4 Neon", "deepmind.google", [])
        two = cand("Introducing Gemini 4 Neon", "blog.google", [])
        self.assertEqual(len(self.pick([one, two])), 1)
        self.assertFalse(rank.near_duplicate(cand("Introducing Gemini 4 Neon", "deepmind.google"), cand("Introducing Gemini 4 Neon", "openai.com")))
        self.assertFalse(rank.near_duplicate(cand("Introducing Gemini 4 Neon", "deepmind.google"), cand("Gemini 4 Carbon is now available", "blog.google")))


class StoryDiversity(unittest.TestCase):
    def pick(self, cands, limit=7):
        return rank.choose_stories(cands, RINGS, limit)

    def test_at_most_two_stories_from_one_company(self):
        ms = [cand(f"Work IQ post {i}", "microsoft.com", ["enterprise AI and ERP"], at=i) for i in range(5)]
        ms += [cand("Entra post", "techcommunity.microsoft.com", ["enterprise AI and ERP"])]
        other = [cand(f"Story {i}", f"site{i}.org", ["evals"], tier="C", at=i) for i in range(5)]
        picked = self.pick(ms + other)
        self.assertEqual(sum(rank.company_of(c["domain"]) == "microsoft" for c in picked), rank.MAX_STORIES_PER_COMPANY)

    def test_at_most_three_stories_from_one_interest_cluster(self):
        agents = [cand(f"Agent story {i}", f"site{i}.org", ["multi-agent systems"], tier="C", at=i + 10) for i in range(6)]
        other = [cand(f"Model story {i}", f"models{i}.org", ["evals"], tier="C", at=i) for i in range(4)]
        other += [cand(f"Enterprise story {i}", f"ent{i}.org", ["enterprise AI and ERP"], tier="C", at=i) for i in range(2)]
        picked = self.pick(agents + other)
        counts = collections.Counter(rank.cluster_of(c["topics"], RINGS) for c in picked)
        self.assertEqual(counts["agents"], rank.MAX_PER_CLUSTER)
        self.assertEqual(len(picked), 7)

    def test_a_story_with_no_interest_cluster_is_not_capped_by_one(self):
        launches = [cand(f"Introducing model {i}", f"lab{i}.example", [], at=i) for i in range(4)]
        for c in launches:
            c["tier"] = "A"
        self.assertEqual(len(self.pick(launches)), 4)

    def test_work_iq_twice_is_bounded_by_the_company_cap(self):
        pair = [cand("Unlock new possibilities for Dynamics 365 ERP with Work IQ and business skills", "microsoft.com", ["enterprise AI and ERP"], at=2),
                cand("Business Applications in Work IQ is now generally available", "microsoft.com", ["enterprise AI and ERP"], at=1),
                cand("Move beyond legacy ERP and CRM with Dynamics 365 Activate", "microsoft.com", ["enterprise AI and ERP"], at=0)]
        self.assertEqual(len(self.pick(pair)), 2)


class FeedsAddedOn20261122(unittest.TestCase):
    FEEDS = {f.id: f for f in reader.FEEDS}
    FX = FIX / "feeds-2026-11-22"

    def parse(self, feed_id, name):
        return reader.parse((self.FX / name).read_bytes(), self.FEEDS[feed_id])

    def test_the_three_feeds_are_registered_and_belong_to_listed_sources(self):
        for fid, source, domain in (("blog-google", "blog-google", "blog.google"), ("metr", "metr", "metr.org"),
                                    ("entra-blog", "microsoft-tech-community", "techcommunity.microsoft.com")):
            self.assertEqual((self.FEEDS[fid].source_id, self.FEEDS[fid].domain, self.FEEDS[fid].kind), (source, domain, "rss"))
        listed = {s.id: s for s in sources.load_digest_rows()}
        self.assertEqual((listed["metr"].tier, listed["metr"].domain), ("A", "metr.org"))
        self.assertEqual((listed["microsoft-tech-community"].tier, listed["microsoft-tech-community"].domain), ("A", "techcommunity.microsoft.com"))

    def test_the_blog_google_feed_carries_the_two_launches_the_week_missed(self):
        urls = {c.url for c in self.parse("blog-google", "blog-google-rss.xml")}
        self.assertIn("https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-neon/", urls)
        self.assertIn("https://blog.google/innovation-and-ai/models-and-research/google-deepmind/genie-4-now-available-project-genie/", urls)

    def test_the_metr_feed_carries_the_capability_update_and_dates_it(self):
        found = {c.url: c for c in self.parse("metr", "metr-feed.xml")}
        update = found["https://metr.org/blog/2026-11-20-q4-2026-capability-update/"]
        self.assertEqual(update.published.date().isoformat(), "2026-11-20")

    def test_the_entra_board_carries_the_missed_post(self):
        urls = {c.url for c in self.parse("entra-blog", "entra-board.xml")}
        self.assertTrue(any(u.endswith("/ba-p/4556456") for u in urls))

    def test_the_metr_feed_fits_the_read_limit(self):
        self.assertGreaterEqual(reader.FEED_MAX_BYTES, 4_700_000)          # it is 4.6 MB; cut short it is not XML and the whole feed was lost

    def test_hacker_news_points_are_kept(self):
        payload = json.dumps({"hits": [{"url": "https://example.com/a", "title": "A post", "points": 412, "objectID": "1", "created_at": "2026-11-20T00:00:00Z"},
                                       {"url": "https://example.com/b", "title": "Another", "objectID": "2", "created_at": "2026-11-20T00:00:00Z"}]}).encode()
        got = reader.parse_hn(payload, self.FEEDS["hacker-news"])
        self.assertEqual([c.points for c in got], [412, 0])


class SavedLinkAndLead(unittest.TestCase):
    PAGE = ("---\ncanonical: https://www.manufacturingdive.com/news/anthropic-launches-claude-for-manufacturing/874303/\nmeta-og:title: Anthropic launches ‘Claude for Manufacturing’\n"
            "meta-description: The new plugin with 17 skills and four connectors helps manufacturers draft quotes.\n---\nBody text.")
    URL = "https://manufacturingdive.com/news/anthropic-launches-claude-for-manufacturing/874303"

    def test_a_saved_link_titled_with_its_address_is_framed_under_the_pages_own_title(self):
        self.assertEqual(weekly.page_title(self.URL, self.PAGE), "Anthropic launches ‘Claude for Manufacturing’")
        self.assertEqual(weekly.page_title("A real headline", self.PAGE), "A real headline")

    def test_without_a_page_title_the_addresss_words_are_used_and_its_digits_are_not(self):
        words = weekly.page_title(self.URL, "no metadata here")
        self.assertEqual(words, "Anthropic launches claude for manufacturing")
        self.assertEqual(weekly.headline_facts_missing(words, "Anthropic launches Claude for Manufacturing, a plugin."), [])

    def test_the_address_title_was_what_dropped_it(self):
        self.assertEqual(weekly.headline_facts_missing(self.URL, "The new plugin with 17 skills and four connectors."), ["874303"])

    def test_a_paragraph_that_ends_in_a_lead_in_line_keeps_its_paragraph(self):
        page = ("Introduction\nOver the course of this year, AI systems have become increasingly capable, potentially increasing their ability to contribute "
                "to AI research and development themselves. Today, we are providing an update on research acceleration.\nOur biggest takeaways:\n"
                "- Agents now complete nearly half of the tasks.\n")
        page = page.replace("Our biggest takeaways:\n- Agents now complete nearly half of the tasks.\n", "Our biggest takeaways:\n\n- Agents now complete nearly half of the tasks.\n")
        self.assertIn("Over the course of this year", weekly.lead(page))
        self.assertNotIn("biggest takeaways", weekly.lead(page))


# --------------------------------------------------------------------------------------
# The replay: Edition 06's candidates through the new ranking
# --------------------------------------------------------------------------------------

class ReplayOfWeek48(unittest.TestCase):
    """The recorded week, free: no fetch, no model. If a later change to the ranking lets the week's launches slip again, this fails."""

    @classmethod
    def setUpClass(cls):
        cls.rec = json.loads((FIX / "w48-candidates.json").read_text(encoding="utf-8"))
        cls.golden = json.loads((REPO / "tests" / "golden" / "w48-must-catch.json").read_text(encoding="utf-8"))
        cls.rings = dict(cls.rec["rings"], **{NEW_TOPIC: "adjacent"})
        run_at = datetime.fromisoformat(cls.rec["window"][1]).timestamp()
        everything = cls.rec["candidates"] + cls.rec["late_candidates"]
        # The interest added on 2026-11-23: matched on the title, the recorded candidates holding no summary (pessimistic: the Entra post's
        # feed text says more).
        for c in everything:
            if graph.topics_in(c["title"], [NEW_TOPIC]):
                c["topics"] = sorted(set(c["topics"]) | {NEW_TOPIC})
        fetched_before = set(cls.rec["reader_fetched"])
        # What waited for a fetch: not already fetched earlier. (What a run fetched on its own pass was 'discovered' when it began.)
        cls.waiting = [c for c in everything if c.get("status_at_replay") != "fetched" or c["discovered_at"] >= run_at - 1]
        cls.before = [c for c in cls.rec["candidates"] if c["id"] in fetched_before and c["discovered_at"] < run_at - 1]
        cls.slots = rank.choose_fetches(cls.waiting, cls.rings, 20, surfaceable=lambda c: c["surfaceable"], blocked=lambda c: c["blocked"])
        cls.stories = rank.choose_stories(cls.slots + cls.before, cls.rings, 7)

    def has_slot(self, key):
        return any(key in c["url"] for c in self.slots)

    def test_the_recorded_week_is_what_the_run_saw(self):
        self.assertEqual((len(self.rec["candidates"]), len(self.rec["reader_fetched"]), len(self.rec["published"])), (373, 25, 7))
        self.assertEqual(len(self.rec["late_candidates"]), 6)

    def test_at_least_seven_of_the_ten_must_catch_stories_get_a_fetch_slot(self):
        caught = [m["key"] for m in self.golden["must_catch"] if self.has_slot(m["key"])]
        self.assertGreaterEqual(len(caught), self.golden["pass_bar"]["min_slots"], f"slots for {caught}")
        for key in ("introducing-gpt-6-2-astra-sol", "introducing-gemini-4-neon", "ipo-close", "q4-2026-capability-update",
                    "research-acceleration-jagged-frontier", "inside-the-codex-sandbox", "genie-4-now-available"):
            self.assertIn(key, caught)

    def test_what_the_rules_cannot_catch_is_the_three_that_matched_nothing_or_had_no_address(self):
        missed = {m["key"] for m in self.golden["must_catch"] if not self.has_slot(m["key"])}
        self.assertEqual(missed, {"4556456", "cosmosdb.microsoft.com", "introducing-medchembench"})

    def test_no_company_takes_more_than_five_slots(self):
        by_company = collections.Counter(rank.company_of(c["domain"]) for c in self.slots)
        self.assertLessEqual(max(by_company.values()), rank.MAX_FETCHES_PER_COMPANY, by_company)
        self.assertEqual(len(self.slots), 20)

    def test_the_old_ranking_gave_the_launches_no_such_place(self):
        old = sorted([c for c in self.waiting if c["surfaceable"]], key=lambda c: (-c.get("stored_score", 0), -c["discovered_at"]))[:20]
        self.assertFalse(any("inside-the-codex-sandbox" in c["url"] or "introducing-gemini-4-neon" in c["url"] for c in old))

    def test_the_seven_stories_span_four_companies_and_no_company_has_more_than_two(self):
        by_company = collections.Counter(rank.company_of(c["domain"]) for c in self.stories)
        self.assertEqual(len(self.stories), 7)
        self.assertGreaterEqual(len(by_company), self.golden["pass_bar"]["min_companies"], by_company)
        self.assertLessEqual(max(by_company.values()), self.golden["pass_bar"]["max_stories_per_company"], by_company)

    def test_the_seven_stories_hold_no_more_than_three_of_one_interest_cluster(self):
        clusters = collections.Counter(rank.cluster_of(c["topics"], self.rings) for c in self.stories)
        clusters.pop(None, None)
        self.assertLessEqual(max(clusters.values() or [0]), rank.MAX_PER_CLUSTER)

    def test_the_saved_manufacturing_dive_link_is_among_them(self):
        self.assertTrue(any(self.golden["saved_link"]["key"] in c["url"] for c in self.stories))

    def test_the_seven_hold_a_launch_from_each_of_openai_google_and_anthropic_or_the_saved_link(self):
        companies = {rank.company_of(c["domain"]) for c in self.stories}
        self.assertTrue({"openai", "google"} <= companies)
        self.assertIn("manufacturingdive.com", companies)

    def test_the_seven_hold_exactly_one_paper_since_a_place_is_reserved_for_one(self):
        self.assertEqual([c["domain"] for c in self.stories].count("arxiv.org"), 1)
        without = rank.choose_stories(self.slots + self.before, self.rings, 7, reserved_papers=0)
        self.assertEqual([c["domain"] for c in without].count("arxiv.org"), 0)        # the three launches had filled the agents cluster

    def test_the_entra_post_now_scores_above_zero_through_the_new_interest(self):
        entra = next(c for c in self.waiting if c["url"].endswith("/ba-p/4556456"))
        self.assertEqual(entra["topics"], [NEW_TOPIC])
        self.assertGreater(rank.rank_of(entra, self.rings), 0)

    def test_edition_06_would_have_been_the_opposite_of_these(self):
        published = {c["id"]: c for c in self.rec["candidates"] if c["id"] in set(self.rec["published"])}
        by_company = collections.Counter(rank.company_of(c["domain"]) for c in published.values())
        self.assertEqual(by_company["microsoft"], 3)
        self.assertEqual(by_company["arxiv.org"], 3)


@needs_db
class ReaderUsesTheRanking(unittest.TestCase):
    NOW = datetime(2026, 11, 21, 3, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        reader.prepare(self.conn)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def cands(self, *rows):
        return [reader.Candidate(url, title, summary, self.NOW, reader.Feed("t", src, dom, "https://x/feed", "rss"), points=pts)
                for url, title, summary, src, dom, pts in rows]

    def test_a_labs_launch_that_matches_no_term_is_stored_and_a_stranger_is_not(self):
        stats = reader.Stats()
        kept = reader.build_items(self.conn, self.cands(
            ("https://anthropic.com/news/zz-ipo-close-test", "Anthropic announces closing of initial public offering", "", "anthropic", "anthropic.com", 0),
            ("https://example.com/zz-recipe", "A recipe for sourdough", "", "unrated-x", "example.com", 0)), self.NOW, stats)
        self.assertEqual([c.url for _, c in kept], ["https://anthropic.com/news/zz-ipo-close-test"])
        self.assertEqual(stats.irrelevant, 1)
        self.assertEqual(self.conn.execute("SELECT relevance_score FROM items WHERE canonical_url = 'https://anthropic.com/news/zz-ipo-close-test'").fetchone()[0], 0)

    def test_hn_points_are_stored_and_only_ever_rise(self):
        kept = reader.build_items(self.conn, self.cands(
            ("https://example.com/zz-mcp", "MCP gets a new transport", "", "hacker-news-front-page", "news.ycombinator.com", 350)), self.NOW, reader.Stats())
        iid = kept[0][0]
        self.assertEqual(self.conn.execute("SELECT hn_points FROM items WHERE id = %s", (iid,)).fetchone()[0], 350)
        items.record_hn_points(self.conn, iid, 120)
        self.assertEqual(self.conn.execute("SELECT hn_points FROM items WHERE id = %s", (iid,)).fetchone()[0], 350)

    def test_the_new_sources_are_listed_and_a_feed_can_be_attached(self):
        for fid in ("blog-google", "metr", "entra-blog"):
            feed = next(f for f in reader.FEEDS if f.id == fid)
            self.assertEqual(self.conn.execute("SELECT domain FROM sources WHERE id = %s", (feed.source_id,)).fetchone()[0], feed.domain)

    def test_select_for_fetch_applies_the_company_cap_and_the_launch_lane(self):
        rows = [(f"https://microsoft.com/zz{i}", f"Dynamics 365 ERP post {i}", "", "microsoft-dynamics-365", "microsoft.com", 0) for i in range(8)]
        rows += [("https://deepmind.google/blog/zz-introducing-gemini-9", "Introducing Gemini 9", "", "google-deepmind", "deepmind.google", 0)]
        reader.build_items(self.conn, self.cands(*rows), self.NOW, reader.Stats())
        chosen = [u for _, u in reader.select_for_fetch(self.conn, self.NOW, limit=20)]
        self.assertLessEqual(sum("microsoft.com/zz" in u for u in chosen), 4)               # microsoft.com's own domain cap; the company cap is five
        self.assertIn("https://deepmind.google/blog/zz-introducing-gemini-9", chosen)


if __name__ == "__main__":
    unittest.main(verbosity=2)
