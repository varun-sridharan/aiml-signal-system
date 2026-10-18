"""Sources, items, the interest graph, briefs and feedback, against the real database,
without keeping anything.

    python -m unittest tests.test_storage -v

Every test runs inside a transaction that is rolled back; tearDown checks the live row
counts are unchanged. Tests skip when the database is unreachable (start the tunnel
first), or fail with REQUIRE_DB=1.

To prove a trigger test can fail, run with that trigger disabled:

    STORAGE_TEST_DISABLE_TRIGGER=items_guard python -m unittest tests.test_storage

The trigger is disabled inside each test's own transaction, so the rollback restores it.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import psycopg  # noqa: E402

from agents import briefs, budget, excerpts, graph, items, runs, sources  # noqa: E402
from tests._db import needs_db  # noqa: E402

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
DISABLED = os.environ.get("STORAGE_TEST_DISABLE_TRIGGER", "").strip()
TABLE_OF = {"items_guard": "items", "item_sources_append_only": "item_sources",
            "briefs_append_only": "briefs", "feedback_append_only": "feedback"}
TABLES = ("sources", "items", "item_sources", "interest_nodes", "interest_terms", "briefs", "feedback")
PROFILE = {"user_id": "storage-test", "interests": {
    "core": ["multi-agent systems", "MCP architecture"], "adjacent": ["evals"], "explore": []}}
HASH = "a" * 64


class SourceList(unittest.TestCase):
    """The parser, offline: the list is the only authority on what a source is."""

    def test_parses_every_listed_publisher_and_invents_no_domain(self):
        seeded = sources.load_seed()
        self.assertEqual(len(seeded), 21)
        by_id = {s.id: s for s in seeded}
        self.assertEqual({s.domain for s in seeded if s.domain},
                         {"huggingface.co", "blog.modelcontextprotocol.io", "github.blog"})
        self.assertIsNone(by_id["arxiv"].domain)  # 'cs.AI' is a category, not an address
        self.assertEqual((by_id["arxiv"].tier, by_id["arxiv"].reputation_prior), ("A", "high"))
        self.assertEqual((by_id["github-changelog"].tier, by_id["github-changelog"].reputation_prior), ("B", "high"))
        self.assertEqual((by_id["latent-space"].tier, by_id["latent-space"].reputation_prior), ("C", "medium"))
        self.assertEqual((by_id["r-localllama"].tier, by_id["r-localllama"].reputation_prior), ("D", "low"))
        self.assertEqual(sorted(s.tier for s in seeded if s.id in ("openai", "anthropic")), ["A", "A"])

    def test_domain_of(self):
        self.assertEqual(sources.domain_of("https://WWW.Example.com/a?b=1"), "example.com")


@needs_db
class Storage(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")  # open the outer transaction; everything below is a savepoint
        self.live = self.counts()
        if DISABLED:
            self.conn.execute(f"ALTER TABLE {TABLE_OF[DISABLED]} DISABLE TRIGGER {DISABLED}")
        sources.seed(self.conn)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        conn = conn or self.conn
        return conn.execute("SELECT " + ", ".join(f"(SELECT count(*) FROM {t})" for t in TABLES)).fetchone()

    def assertRefusedByDatabase(self, sql, args=(), error=psycopg.errors.RestrictViolation):
        with self.assertRaises(error) as refused:
            with self.conn.transaction():  # a savepoint, so the failure does not end the test
                self.conn.execute(sql, args)
        return str(refused.exception)

    def item(self, url="https://openai.com/index/a-story/", title="A story"):
        return items.upsert(self.conn, url, title, NOW)[0]

    def fetched_item(self, url="https://openai.com/index/a-story/", text="Body text of the story."):
        iid = self.item(url)
        eid = excerpts.store(self.conn, url, text, NOW)
        items.attach_excerpt(self.conn, iid, eid)
        return iid, eid

    # sources ---------------------------------------------------------------------------
    def test_s1_seed_is_idempotent_and_never_overwrites(self):
        self.assertEqual(sources.seed(self.conn), 0)  # setUp already seeded
        self.conn.execute("UPDATE sources SET notes = 'edited' WHERE id = 'openai'")
        sources.seed(self.conn)
        self.assertEqual(self.conn.execute("SELECT notes FROM sources WHERE id='openai'").fetchone()[0], "edited")
        n = self.conn.execute("SELECT count(*) FROM sources WHERE id = ANY(%s)", ([s.id for s in sources.load_seed()],)).fetchone()[0]
        self.assertEqual(n, 21)

    def test_s2_an_unlisted_domain_is_unrated_not_guessed(self):
        sid = sources.for_domain(self.conn, "Some-Blog.example")
        self.assertEqual(sources.for_domain(self.conn, "some-blog.example"), sid)
        row = self.conn.execute("SELECT tier, reputation_prior FROM sources WHERE id = %s", (sid,)).fetchone()
        self.assertEqual(row, ("unrated", "unrated"))
        self.assertEqual(sources.for_domain(self.conn, "github.blog"), "github-changelog")
        self.assertRefusedByDatabase(  # a tier and its prior cannot disagree
            "UPDATE sources SET reputation_prior = 'high' WHERE id = 'latent-space'", error=psycopg.errors.CheckViolation)

    def test_s3_a_met_domain_never_inherits_a_listed_sources_tier(self):
        sid = sources.for_domain(self.conn, "meta.ai")      # slug 'meta-ai' is the listed Meta AI (tier A)
        self.assertNotEqual(sid, "meta-ai")
        self.assertEqual(self.conn.execute("SELECT tier FROM sources WHERE id = %s", (sid,)).fetchone()[0], "unrated")
        self.assertEqual(self.conn.execute("SELECT tier FROM sources WHERE id = 'meta-ai'").fetchone()[0], "A")

    def test_s4_attaching_an_address_after_it_was_met_is_refused_clearly(self):
        sources.for_domain(self.conn, "late.example")
        with self.assertRaises(ValueError) as refused:
            sources.set_feed(self.conn, "openai", "late.example", "https://late.example/feed")
        self.assertIn("already held", str(refused.exception))

    # items -----------------------------------------------------------------------------
    def test_i1_one_item_per_canonical_url_with_primary_provenance(self):
        a, created = items.upsert(self.conn, "http://www.OpenAI.com/index/a-story/?utm_source=x", "A story", NOW)
        b, again = items.upsert(self.conn, "https://openai.com/index/a-story", "A story, retitled", NOW + timedelta(days=1))
        self.assertEqual((a, created, again), (b, True, False))
        row = self.conn.execute("SELECT canonical_url, domain, title, status FROM items WHERE id = %s", (a,)).fetchone()
        self.assertEqual(row, ("https://openai.com/index/a-story", "openai.com", "A story", "discovered"))
        prov = self.conn.execute("SELECT role FROM item_sources WHERE item_id = %s", (a,)).fetchall()
        self.assertEqual(prov, [("primary",)])

    def test_i2_database_refuses_to_change_an_items_identity(self):
        iid = self.item()
        for col, val in (("canonical_url", "https://x.test/y"), ("domain", "x.test"), ("source_id", "arxiv"),
                         ("discovered_at", NOW - timedelta(days=9))):
            msg = self.assertRefusedByDatabase(f"UPDATE items SET {col} = %s WHERE id = %s", (val, iid))
            self.assertIn("identity", msg)

    def test_i3_database_refuses_to_swap_an_attached_excerpt(self):
        iid, eid = self.fetched_item()
        other = excerpts.store(self.conn, "https://openai.com/index/a-story/", "Edited body text.", NOW)
        msg = self.assertRefusedByDatabase("UPDATE items SET excerpt_id = %s WHERE id = %s", (other, iid))
        self.assertIn("attached once", msg)
        self.assertEqual(self.conn.execute("SELECT excerpt_id, status FROM items WHERE id = %s", (iid,)).fetchone(), (eid, "fetched"))

    def test_i4_fetched_is_final(self):
        iid, _ = self.fetched_item()
        for status in ("discovered", "skipped", "failed"):
            msg = self.assertRefusedByDatabase("UPDATE items SET status = %s WHERE id = %s", (status, iid))
            self.assertIn("not allowed", msg)
        other = self.item("https://openai.com/index/b/")  # the allowed moves
        items.mark(self.conn, other, "failed", error="timeout")
        self.assertRefusedByDatabase("UPDATE items SET status = 'skipped' WHERE id = %s", (other,))  # trigger only
        items.mark(self.conn, other, "discovered")

    def test_i5_database_refuses_to_delete_an_item(self):
        iid = uuid.uuid4()  # raw insert, no provenance row, so no foreign key can be what refuses
        self.conn.execute(
            "INSERT INTO items (id, canonical_url, domain, title, source_id, discovered_at, status) "
            "VALUES (%s, 'https://openai.com/index/bare', 'openai.com', 'Bare', 'openai', %s, 'discovered')", (iid, NOW))
        msg = self.assertRefusedByDatabase("DELETE FROM items WHERE id = %s", (iid,))
        self.assertIn("never deleted", msg)

    def test_i7_an_excerpt_must_be_this_items_own_page(self):
        iid = self.item("https://openai.com/index/one/")
        other = excerpts.store(self.conn, "https://openai.com/index/two/", "Text of a different article.", NOW)
        with self.assertRaises(items.ItemRefused):
            items.attach_excerpt(self.conn, iid, other)
        with self.assertRaises(items.ItemRefused):
            items.attach_excerpt(self.conn, uuid.uuid4(), other)
        own = excerpts.store(self.conn, "https://www.openai.com/index/one?utm_source=x", "Text of the article.", NOW)
        items.attach_excerpt(self.conn, iid, own)  # same canonical page, spelled differently: fine

    def test_i6_a_fetched_item_has_an_excerpt_and_only_then(self):
        iid = self.item()
        self.assertRefusedByDatabase("UPDATE items SET status = 'fetched' WHERE id = %s", (iid,),
                                     error=psycopg.errors.CheckViolation)

    # provenance and corroboration ------------------------------------------------------
    def test_p1_provenance_is_append_only(self):
        iid = self.item()
        self.assertTrue(items.add_provenance(self.conn, iid, "https://www.theregister.com/2026/10/story/", NOW))
        self.assertFalse(items.add_provenance(self.conn, iid, "https://theregister.com/2026/10/story", NOW))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM item_sources WHERE item_id = %s", (iid,)).fetchone()[0], 2)

    def test_p1b_provenance_cannot_be_edited(self):
        iid = self.item()
        self.assertRefusedByDatabase("UPDATE item_sources SET role = 'corroboration' WHERE item_id = %s", (iid,))

    def test_p1c_provenance_cannot_be_deleted(self):
        iid = self.item()
        self.assertRefusedByDatabase("DELETE FROM item_sources WHERE item_id = %s", (iid,))

    def test_p2_only_a_trusted_source_surfaces_an_item_alone(self):
        surf = lambda url, extra=(): self.surfaces(url, extra)  # noqa: E731
        # The list gives no address for OpenAI; a verified feed attaches one (Reader does
        # this at startup, before it discovers anything).
        sources.set_feed(self.conn, "openai", "openai.com", "https://openai.com/news/rss.xml")
        self.assertTrue(surf("https://openai.com/index/a/"))                       # tier A alone: yes
        self.assertFalse(surf("https://news.ycombinator.com/item/1"))              # unrated alone: no
        self.assertFalse(surf("https://news.ycombinator.com/item/2",
                              ["https://other.example/a", "https://third.example/b"]))  # unrated + unrated: no
        self.assertTrue(surf("https://news.ycombinator.com/item/3", ["https://github.blog/changelog/x/"]))  # + tier B: yes
        sources.set_feed(self.conn, "latent-space", "latent.space", "https://www.latent.space/feed")
        self.assertFalse(surf("https://latent.space/p/one"))                       # tier C alone: no
        self.assertTrue(surf("https://latent.space/p/two", ["https://other.example/c"]))  # C + a second: yes

    def surfaces(self, url, extra):
        iid = self.item(url)
        for e in extra:
            items.add_provenance(self.conn, iid, e, NOW)
        return items.surfaceable(self.conn, iid)

    # interest graph --------------------------------------------------------------------
    def test_g1_graph_loads_from_the_profile_and_scores_deterministically(self):
        self.assertEqual(graph.sync(self.conn, PROFILE), 3)
        score, topics = graph.score(self.conn, "storage-test", "MCP spec goes stateless")
        self.assertEqual((score, topics), (3, ["MCP architecture"]))
        self.assertEqual(graph.score(self.conn, "storage-test", "New benchmark for evals lands")[1], ["evals"])
        score, topics = graph.score(self.conn, "storage-test", "Multi-agent MCP servers")
        self.assertEqual((score, topics), (6, ["MCP architecture", "multi-agent systems"]))
        self.assertEqual(graph.score(self.conn, "storage-test", "Celebrity gossip roundup"), (0, []))
        self.assertEqual(graph.score(self.conn, "storage-test", "The mcpserver mcps")[0], 0)  # whole terms only

    def test_g3_a_generic_word_recognises_nothing_and_a_double_listed_topic_keeps_its_best_ring(self):
        self.assertNotIn("model", graph.terms_for("model releases"))
        self.assertIn("model release", graph.terms_for("model releases"))
        twice = {"user_id": "storage-test", "interests": {"core": ["evals"], "adjacent": [], "explore": ["evals"]}}
        graph.sync(self.conn, twice)
        ring = self.conn.execute("SELECT ring FROM interest_nodes WHERE user_id = 'storage-test'").fetchone()[0]
        self.assertEqual(ring, "core")
        self.assertEqual(graph.score(self.conn, "storage-test", "A new model ships today")[0], 0)

    def test_g2_sync_follows_the_profile(self):
        graph.sync(self.conn, PROFILE)
        narrower = {"user_id": "storage-test", "interests": {"core": ["MCP architecture"], "adjacent": [], "explore": []}}
        self.assertEqual(graph.sync(self.conn, narrower), 1)
        self.assertEqual(graph.score(self.conn, "storage-test", "new evals")[0], 0)
        n = self.conn.execute("SELECT count(*) FROM interest_terms WHERE user_id = 'storage-test'").fetchone()[0]
        self.assertEqual(n, len(graph.terms_for("MCP architecture")))

    # briefs and feedback ---------------------------------------------------------------
    def brief(self, week="1999-W02"):
        runs.open_run(self.conn, week, NOW)
        briefs.publish(self.conn, week, week, {"learning": [], "stories": []}, NOW)
        return week

    def test_b1_a_published_brief_is_never_edited_or_deleted(self):
        week = self.brief()
        self.assertEqual(briefs.get(self.conn, week)[1], {"learning": [], "stories": []})
        with self.assertRaises(psycopg.errors.UniqueViolation):  # one brief per week
            with self.conn.transaction():
                briefs.publish(self.conn, week, week, {}, NOW)

    def test_b1b_a_published_brief_cannot_be_edited(self):
        week = self.brief()
        self.assertRefusedByDatabase("UPDATE briefs SET body = '{}' WHERE id = %s", (week,))

    def test_b1c_a_published_brief_cannot_be_deleted(self):
        week = self.brief()
        self.assertRefusedByDatabase("DELETE FROM briefs WHERE id = %s", (week,))

    def test_f1_feedback_is_append_only(self):
        week = self.brief()
        briefs.vote(self.conn, week, "story-1", "down", "too vague", HASH, NOW)
        self.assertRefusedByDatabase("UPDATE feedback SET item_ref = 'story-2' WHERE brief_id = %s", (week,))

    def test_f1b_feedback_cannot_be_deleted(self):
        week = self.brief()
        briefs.vote(self.conn, week, "story-1", "down", "too vague", HASH, NOW)
        self.assertRefusedByDatabase("DELETE FROM feedback WHERE brief_id = %s", (week,))

    def test_f1c_a_vote_on_an_unknown_brief_is_refused_cleanly(self):
        with self.assertRaises(briefs.FeedbackRefused):
            briefs.vote(self.conn, "1999-W52", "story-1", "up", None, HASH, NOW)

    def test_f2_a_note_goes_with_a_thumbs_down_only(self):
        week = self.brief()
        with self.assertRaises(briefs.FeedbackRefused):
            briefs.vote(self.conn, week, "story-1", "up", "great", HASH, NOW)
        self.assertRefusedByDatabase(  # and the database says the same, for any other writer
            "INSERT INTO feedback (id, brief_id, item_ref, vote, comment, client_hash, created_at) "
            "VALUES (%s, %s, 's', 'up', 'x', %s, %s)", (uuid.uuid4(), week, HASH, NOW), error=psycopg.errors.CheckViolation)
        with self.assertRaises(briefs.FeedbackRefused):
            briefs.vote(self.conn, week, "story-1", "down", "x" * 2001, HASH, NOW)
        briefs.vote(self.conn, week, "story-1", "down", None, HASH, NOW)  # a bare thumbs-down is fine

    def test_f3_only_a_salted_hash_of_the_address_is_stored(self):
        week = self.brief()
        a, b = briefs.client_hash("203.0.113.9", "salt-one-" + "x" * 16), briefs.client_hash("203.0.113.9", "salt-two-" + "x" * 16)
        self.assertNotEqual(a, b)
        self.assertEqual(a, briefs.client_hash("203.0.113.9", "salt-one-" + "x" * 16))
        self.assertNotIn("203.0.113.9", a)
        for weak in ("", "short-salt"):
            with self.assertRaises(briefs.FeedbackRefused):
                briefs.client_hash("203.0.113.9", weak)
        briefs.vote(self.conn, week, "story-1", "up", None, a, NOW)
        cols = [r[0] for r in self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'feedback'").fetchall()]
        self.assertFalse({"ip", "address", "ip_address", "client"} & set(cols), cols)
        self.assertRefusedByDatabase(  # the column only accepts a 64-character hash
            "INSERT INTO feedback (id, brief_id, item_ref, vote, client_hash, created_at) "
            "VALUES (%s, %s, 's', 'up', '203.0.113.9', %s)", (uuid.uuid4(), week, NOW), error=psycopg.errors.CheckViolation)

    def test_f4_a_client_is_rate_limited_and_others_are_not(self):
        week = self.brief()
        for i in range(briefs.RATE_LIMIT_VOTES):
            briefs.vote(self.conn, week, f"story-{i}", "up", None, HASH, NOW + timedelta(seconds=i))
        with self.assertRaises(briefs.RateLimited):
            briefs.vote(self.conn, week, "story-x", "up", None, HASH, NOW + timedelta(minutes=5))
        briefs.vote(self.conn, week, "story-x", "up", None, "b" * 64, NOW + timedelta(minutes=5))  # someone else
        briefs.vote(self.conn, week, "story-x", "up", None, HASH, NOW + briefs.RATE_LIMIT_WINDOW + timedelta(minutes=1))

    def test_f5_the_latest_vote_per_client_counts(self):
        week = self.brief()
        briefs.vote(self.conn, week, "story-1", "up", None, HASH, NOW)
        briefs.vote(self.conn, week, "story-1", "down", "changed my mind", HASH, NOW + timedelta(minutes=1))
        briefs.vote(self.conn, week, "story-1", "up", None, "c" * 64, NOW)
        self.assertEqual(briefs.tally(self.conn, week), {"story-1": {"up": 1, "down": 1}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
