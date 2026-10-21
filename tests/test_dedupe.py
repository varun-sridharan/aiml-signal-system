"""Reader's layered deduplication gate, and proof that each layer can fail.

    python -m unittest tests.test_dedupe -v

The first classes are free and offline. The database class runs inside a transaction that is rolled
back (skips without the tunnel, fails with REQUIRE_DB=1). To prove the trigger test can fail:

    DEDUP_TEST_DISABLE_TRIGGER=dedup_decisions_guard python -m unittest tests.test_dedupe
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import psycopg  # noqa: E402

from agents import budget, dedupe, excerpts, items, sources  # noqa: E402
from tests._db import needs_db  # noqa: E402

_spec = importlib.util.spec_from_file_location("run_content", REPO / "data" / "evals" / "canonical" / "content" / "run_content.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
DISABLED = os.environ.get("DEDUP_TEST_DISABLE_TRIGGER", "").strip()
PAIRS = {p["id"]: p for p in gate.load_pairs()["pairs"]}


def docs(pair_id):
    p = PAIRS[pair_id]
    return dedupe.Doc(**p["a"]), dedupe.Doc(**p["b"])


def failing_ids(decide) -> set[str]:
    return {line.split(":", 1)[0] for line in gate.evaluate(decide)}


class ContentPairsGate(unittest.TestCase):
    def test_the_real_layers_behave_as_labelled(self):
        self.assertEqual(gate.evaluate(), [])

    def test_the_set_is_a_marked_draft_with_the_required_kinds(self):
        data = gate.load_pairs()
        self.assertIn("draft, awaiting Varun's review", data["status"])
        self.assertGreaterEqual(len(data["pairs"]), 10)
        for needed in ("same-article-fetched-twice", "ga-post-from-preview-template", "two-papers-one-lab-shared-boilerplate",
                       "short-fetch-against-full", "article-updated-after-publication", "syndicated-copy"):
            self.assertIn(needed, PAIRS)
        self.assertTrue(any(p["expect"] == "same" for p in data["pairs"]))
        self.assertTrue(sum(p["expect"] == "different" for p in data["pairs"]) >= 4)
        # every text is invented: no real company or person is named in a pair
        blob = json.dumps(data).lower()
        for real in ("openai", "anthropic", "google", "microsoft", "meta ai", "nvidia", "reuters"):
            self.assertNotIn(real, blob)

    # Each test below breaks one layer and checks the gate notices, naming the pair that should catch it.
    def test_a_gate_that_merges_everything_fails_every_different_pair(self):
        always = lambda a, b: dedupe.Decision("same", "fingerprint", "x")  # noqa: E731
        ids = failing_ids(always)
        self.assertTrue({"two-papers-different-arxiv-ids", "ga-post-from-preview-template", "two-releases-one-template",
                         "two-outlets-cover-one-announcement"} <= ids)

    def test_a_gate_that_never_merges_fails_every_same_pair(self):
        never = lambda a, b: dedupe.Decision("different", "default", "x")  # noqa: E731
        self.assertTrue({"same-article-fetched-twice", "short-fetch-against-full", "syndicated-copy", "same-paper-two-hosts"} <= failing_ids(never))

    def test_losing_the_identity_layer_merges_two_papers_with_one_text_and_splits_one_paper(self):
        def no_identity(a, b):
            if excerpts.canonicalise(a.url) == excerpts.canonicalise(b.url):
                return dedupe.Decision("same", "url", "x")
            return dedupe.compare_fingerprints(dedupe.fingerprint(a.text), dedupe.fingerprint(b.text))
        self.assertTrue({"two-papers-different-arxiv-ids", "same-paper-two-hosts"} <= failing_ids(no_identity))

    def test_the_old_resemblance_rule_merges_a_one_word_flip_and_a_swapped_name(self):
        """The first version merged near-identical text. One changed word can reverse a story, so it must not."""
        def merges_near_identical(a, b):
            if excerpts.canonicalise(a.url) == excerpts.canonicalise(b.url):
                return dedupe.Decision("same", "url", "x")
            ia, ib = dedupe.identity_of(a.url), dedupe.identity_of(b.url)
            if ia and ib and ia[0] == ib[0]:
                return dedupe.Decision("same" if ia == ib else "different", "identity", "x")
            fa, fb = dedupe.fingerprint(a.text), dedupe.fingerprint(b.text)
            resemblance = len(fa.shingles & fb.shingles) / len(fa.shingles | fb.shingles)
            return dedupe.Decision("same", "fingerprint", "x") if resemblance >= 0.7 else dedupe.decide(a, b)
        self.assertTrue({"one-word-flip-no-digits", "template-entity-swap-no-digits"} <= failing_ids(merges_near_identical))

    def test_losing_the_flush_and_size_rules_merges_a_follow_up_that_quotes_a_statement(self):
        with unittest.mock.patch.object(dedupe, "CONTAIN_MIN_RATIO", 0.0), unittest.mock.patch.object(dedupe, "CONTAIN_FLUSH_WORDS", 10 ** 6):
            self.assertIn("follow-up-quotes-an-earlier-statement", failing_ids(dedupe.decide))

    def test_losing_the_unsure_band_decides_the_reworded_article_without_the_model(self):
        with unittest.mock.patch.object(dedupe, "DIFFERENT_RESEMBLANCE", 0.9), unittest.mock.patch.object(dedupe, "DIFFERENT_CONTAINMENT", 0.99):
            self.assertIn("article-reworded-after-publication", failing_ids(dedupe.decide))

    def test_nothing_inside_the_unsure_band_is_ever_merged_by_the_free_layers(self):
        for pid in ("ga-post-from-preview-template", "two-papers-one-lab-shared-boilerplate", "article-with-a-different-headline-figure",
                    "article-reworded-after-publication", "one-word-flip-no-digits", "template-entity-swap-no-digits",
                    "follow-up-quotes-an-earlier-statement", "two-releases-one-template"):
            self.assertEqual(dedupe.decide(*docs(pid)).verdict, "unsure", pid)


class Identity(unittest.TestCase):
    def test_addresses_that_name_a_paper(self):
        for url in ("https://arxiv.org/abs/2610.04417", "https://arxiv.org/abs/2610.04417v3", "http://www.arxiv.org/pdf/2610.04417v2.pdf",
                    "https://arxiv.org/html/2610.04417v1/", "https://alphaxiv.org/abs/2610.04417", "https://huggingface.co/papers/2610.04417",
                    "https://ar5iv.labs.arxiv.org/html/2610.04417"):
            self.assertEqual(dedupe.identity_of(url), ("arxiv", "2610.04417"), url)
        self.assertEqual(dedupe.identity_of("https://arxiv.org/abs/cs/0301001v2"), ("arxiv", "cs/0301001"))
        self.assertEqual(dedupe.identity_of("https://doi.org/10.1038/S41586-026-0001"), ("doi", "10.1038/s41586-026-0001"))
        self.assertEqual(dedupe.identity_of("https://dx.doi.org/10.1038/s41586-026-0001/"), ("doi", "10.1038/s41586-026-0001"))

    def test_addresses_that_name_nothing(self):
        for url in ("https://example.com/blog/2610.04417", "https://arxiv.org/list/cs.AI/recent", "https://arxiv.org/", "not a url",
                    "https://huggingface.co/papers", "https://example.com/doi/10.1000/x"):
            self.assertIsNone(dedupe.identity_of(url), url)

    def test_the_same_id_is_the_same_paper_and_two_ids_are_two_papers(self):
        same = dedupe.decide(dedupe.Doc("https://arxiv.org/abs/2610.04417v1", "a"), dedupe.Doc("https://huggingface.co/papers/2610.04417", "b"))
        self.assertEqual((same.verdict, same.layer), ("same", "identity"))
        different = dedupe.decide(dedupe.Doc("https://arxiv.org/abs/2610.04417", "x"), dedupe.Doc("https://arxiv.org/abs/2610.04418", "x"))
        self.assertEqual((different.verdict, different.layer), ("different", "identity"))

    def test_an_id_in_the_text_is_never_read(self):
        text = "As shown in arXiv:2610.04417 and https://arxiv.org/abs/2610.04417, the method works. " * 6
        self.assertIsNone(dedupe.identity_of("https://lab.example/research/post"))
        self.assertEqual(dedupe.decide(dedupe.Doc("https://lab.example/p", text), dedupe.Doc("https://arxiv.org/abs/2610.04417", "other " * 60)).verdict, "different")


class SafeUrlLayer(unittest.TestCase):
    """Three draft additions to the URL pairs (data/evals/canonical/pairs.json): AMP, m., a Substack share code."""

    def test_the_draft_pairs_are_in_the_url_set_and_marked(self):
        data = json.loads((REPO / "data" / "evals" / "canonical" / "pairs.json").read_text())
        new = {p["id"]: p for p in data["pairs"] if p.get("added") == "2026-10-20 run 3 (draft)"}
        self.assertEqual({"amp-suffix", "mobile-subdomain", "substack-share-code"} <= set(new), True)
        self.assertTrue(all(p["expect"] == "same" for p in new.values()))

    def test_amp_mobile_and_share_code_collapse(self):
        c = excerpts.canonicalise
        self.assertEqual(c("https://example.com/news/story-one/amp"), c("https://example.com/news/story-one"))
        self.assertEqual(c("https://example.com/news/story-one/amp/"), c("https://example.com/news/story-one"))
        self.assertEqual(c("https://example.com/news/story-one?amp=1"), c("https://example.com/news/story-one"))
        self.assertEqual(c("https://amp.example.com/news/story-one"), c("https://example.com/news/story-one"))
        self.assertEqual(c("https://m.example.com/news/story-one"), c("https://example.com/news/story-one"))
        self.assertEqual(c("https://newsletter.substack.com/p/post-title?r=2abcd&utm_campaign=post"), c("https://newsletter.substack.com/p/post-title"))
        self.assertEqual(c("https://newsletter.substack.com/p/post-title?r=2abcd&triedRedirect=true"), c("https://newsletter.substack.com/p/post-title"))

    def test_a_site_section_called_amp_and_shared_hosts_are_left_alone(self):
        c = excerpts.canonicalise
        self.assertEqual(c("https://example.com/tools/amp"), "https://example.com/tools/amp")             # one segment deep: a section, not a copy
        self.assertEqual(c("https://example.com/amp"), "https://example.com/amp")
        for host in ("github.io", "substack.com", "co.uk", "media-amazon.com", "vercel.app"):
            self.assertEqual(c(f"https://m.{host}/a"), f"https://m.{host}/a", host)                       # a tenant, not a mobile copy
            self.assertEqual(c(f"https://amp.{host}/a"), f"https://amp.{host}/a", host)
        self.assertEqual(c("https://newsletter.notsubstack.com/p/x?r=1"), "https://newsletter.notsubstack.com/p/x?r=1")
        self.assertEqual(c("https://m.localhost/a"), "https://m.localhost/a")

    def test_nothing_that_names_a_different_page_collapses(self):
        c = excerpts.canonicalise
        self.assertNotEqual(c("https://example.com/amp"), c("https://example.com"))             # a page called amp, with nothing before it
        self.assertNotEqual(c("https://example.com/news/amp-guide"), c("https://example.com/news"))
        self.assertNotEqual(c("https://example.com/docs/amp/intro"), c("https://example.com/docs/intro"))
        self.assertNotEqual(c("https://example.com/a?r=1"), c("https://example.com/a?r=2"))      # r= is a share code only on Substack
        self.assertNotEqual(c("https://mail.example.com/a"), c("https://example.com/a"))
        self.assertNotEqual(c("https://m.example.com/a"), c("https://m2.example.com/a"))
        self.assertNotEqual(c("https://example.com/a?amp=0&id=3"), c("https://example.com/a?id=4"))


class FingerprintEdges(unittest.TestCase):
    def test_no_text_and_short_text_are_different_unless_identical(self):
        self.assertEqual(dedupe.decide(dedupe.Doc("https://a.example/x", None), dedupe.Doc("https://b.example/x", "text")).verdict, "different")
        short = "A short note of nine words, nothing more here."
        for other in (short, short + " Plus one."):          # identical or not: too little text to say two addresses are one article
            self.assertEqual(dedupe.decide(dedupe.Doc("https://a.example/x", short), dedupe.Doc("https://b.example/y", other)).verdict, "different")
        wall = "Please enable JavaScript and cookies to continue."
        self.assertEqual(dedupe.decide(dedupe.Doc("https://a.example/story-one", wall), dedupe.Doc("https://b.example/story-two", wall)).verdict, "different")
        long_ = " ".join(["The same long article text repeated for forty words or more so that it is a real page."] * 5)
        self.assertEqual(dedupe.decide(dedupe.Doc("https://a.example/x", long_), dedupe.Doc("https://b.example/y", long_)).verdict, "same")

    def test_page_furniture_and_case_and_whitespace_do_not_matter(self):
        a, b = docs("same-article-fetched-twice")
        shouted = dedupe.Doc(b.url, b.text.upper().replace(" ", "\n  "), b.title)
        self.assertEqual(dedupe.decide(a, shouted).verdict, "same")

    def test_fingerprinting_is_deterministic(self):
        a, _ = docs("same-article-fetched-twice")
        self.assertEqual(dedupe.fingerprint(a.text), dedupe.fingerprint(a.text))


class Judge(unittest.TestCase):
    def call(self, reply):
        class Usage:
            input_tokens = output_tokens = 1
        with unittest.mock.patch.object(dedupe.llm, "call", return_value=(reply, Usage(), 0.002)) as m:
            a, b = docs("ga-post-from-preview-template")
            return dedupe.judge(object(), a, b), m

    def test_a_clean_same_with_a_reason_merges(self):
        (d, cost), m = self.call(json.dumps({"same": True, "reason": "Both are the same post."}))
        self.assertEqual((d.verdict, d.layer, d.reason, cost), ("same", "model", "Both are the same post.", 0.002))
        kw = m.call_args.kwargs
        self.assertEqual((kw["temperature"], kw["phase"]), (0, dedupe.JUDGE_PHASE))      # booked under its own phase, deterministic

    def test_everything_else_is_different(self):
        for reply in (json.dumps({"same": False, "reason": "A preview and a release."}), json.dumps({"same": True, "reason": ""}),
                      json.dumps({"same": "yes", "reason": "x"}), json.dumps({"same": 1, "reason": "x"}), "not json", "{}", "[]", json.dumps({"reason": "x"})):
            (d, _), _ = self.call(reply)
            self.assertEqual(d.verdict, "different", reply)

    def test_the_request_sends_both_openings_and_the_doubt_rule(self):
        a, b = docs("ga-post-from-preview-template")
        req = dedupe.judge_request(a, b)
        self.assertIn("When in doubt", req["system"])
        sent = json.loads(req["messages"][0]["content"])
        self.assertEqual((sent["page_a"]["address"], sent["page_b"]["address"]), (a.url, b.url))
        self.assertLessEqual(len(sent["page_a"]["opening"]), dedupe.JUDGE_CHARS)


@needs_db
class Decisions(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.live = self.counts()
        if DISABLED:
            self.conn.execute(f"ALTER TABLE dedup_decisions DISABLE TRIGGER {DISABLED}")
        sources.seed(self.conn)
        self.n = 0
        self.created = []                  # the live table holds real decisions from real runs: every query below is about these items only

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        conn = conn or self.conn
        return conn.execute("SELECT (SELECT count(*) FROM items), (SELECT count(*) FROM item_sources), (SELECT count(*) FROM dedup_decisions)").fetchone()

    def fetched(self, url, text, minutes=0):
        self.n += 1
        iid = items.upsert(self.conn, url, f"Title {self.n}", NOW + timedelta(minutes=minutes))[0]
        eid = excerpts.store(self.conn, url, text, NOW)
        items.attach_excerpt(self.conn, iid, eid)
        self.created.append(iid)
        return iid

    def mine(self):
        return [str(i) for i in self.created]

    def pair(self, pair_id):
        p = PAIRS[pair_id]
        return self.fetched(p["a"]["url"], p["a"]["text"], 0), self.fetched(p["b"]["url"], p["b"]["text"], 5)

    def run_dedupe(self, ids, judge_fn=None, **kw):
        return dedupe.dedupe_fetched(self.conn, object(), ids, NOW + timedelta(hours=1), judge_fn=judge_fn or self.refuse, **kw)

    @staticmethod
    def refuse(client, a, b, conn=None):
        raise AssertionError("the model must not be asked here")

    def decisions(self):
        return self.conn.execute("SELECT verdict, layer, undone_at FROM dedup_decisions WHERE kept_id = ANY(%s::uuid[]) OR other_id = ANY(%s::uuid[]) "
                                 "ORDER BY decided_at", (self.mine(), self.mine())).fetchall()

    def decision_row(self, columns):
        return self.conn.execute(f"SELECT {columns} FROM dedup_decisions WHERE kept_id = ANY(%s::uuid[]) OR other_id = ANY(%s::uuid[])",
                                 (self.mine(), self.mine())).fetchone()

    def test_a_merge_keeps_the_dropped_link_as_a_sighting_and_the_earlier_item(self):
        a, b = self.pair("syndicated-copy")
        report = self.run_dedupe([b, a])             # given out of order on purpose
        self.assertEqual([(m["kept"], m["dropped"], m["layer"]) for m in report["merged"]], [(str(a), str(b), "fingerprint")])
        self.assertEqual(dedupe.merged_away(self.conn, [a, b]), {b})
        urls = {r[0] for r in self.conn.execute("SELECT url FROM item_sources WHERE item_id = %s", (a,)).fetchall()}
        self.assertIn(excerpts.canonicalise(PAIRS["syndicated-copy"]["b"]["url"]), urls)       # the syndicated address is a sighting of the kept story
        self.assertEqual(self.decisions(), [("same", "fingerprint", None)])

    def test_a_merge_can_be_undone_and_is_not_made_again(self):
        a, b = self.pair("syndicated-copy")
        self.run_dedupe([a, b])
        did = self.decision_row("id")[0]
        dedupe.undo_merge(self.conn, did, "these are two different stories after all", NOW)
        self.assertEqual(dedupe.merged_away(self.conn, [a, b]), set())
        again = self.run_dedupe([a, b])
        self.assertEqual((again["merged"], again["skipped_known"]), ([], 1))
        self.assertEqual(dedupe.merged_away(self.conn, [a, b]), set())
        with self.assertRaises(ValueError):
            dedupe.undo_merge(self.conn, did, "twice", NOW)                                  # already undone
        with self.assertRaises(ValueError):
            dedupe.undo_merge(self.conn, did, "  ", NOW)                                     # a reason is required

    def test_different_pairs_leave_no_merge(self):
        for pid in ("two-outlets-cover-one-announcement", "two-papers-different-arxiv-ids", "unrelated-articles-from-one-site"):
            a, b = self.pair(pid)
            self.assertEqual(self.run_dedupe([a, b])["merged"], [], pid)
            self.assertEqual(dedupe.merged_away(self.conn, [a, b]), set(), pid)

    def test_the_model_sees_only_the_unsure_pairs_and_its_reason_is_recorded(self):
        a, b = self.pair("ga-post-from-preview-template")
        asked = []

        def says_different(client, x, y, conn=None):
            asked.append((x.url, y.url))
            return dedupe.Decision("different", "model", "A preview and a release are two events."), 0.002
        report = self.run_dedupe([a, b], says_different)
        self.assertEqual(len(asked), 1)
        self.assertEqual((report["judged"], report["merged"], report["cost_usd"]), (1, [], 0.002))
        self.assertEqual(self.decisions(), [("different", "model", None)])
        self.assertEqual(self.decision_row("reason")[0], "A preview and a release are two events.")
        self.assertEqual(self.run_dedupe([a, b], says_different)["judged"], 0)               # decided once; not asked again
        self.assertEqual(len(asked), 1)

    def test_a_model_that_says_same_merges_with_its_reason(self):
        a, b = self.pair("article-reworded-after-publication")
        report = self.run_dedupe([a, b], lambda c, x, y, conn=None: (dedupe.Decision("same", "model", "One article, figure corrected."), 0.001))
        self.assertEqual([m["layer"] for m in report["merged"]], ["model"])
        self.assertEqual(dedupe.merged_away(self.conn, [a, b]), {b})

    def test_an_unreadable_answer_is_not_recorded_so_the_pair_is_asked_again(self):
        a, b = self.pair("ga-post-from-preview-template")
        garbled = lambda c, x, y, conn=None: (dedupe.Decision("different", "default", "the model's answer could not be read"), 0.001)  # noqa: E731
        weird = lambda c, x, y, conn=None: (dedupe.Decision("unsure", "model", "x"), 0.001)  # noqa: E731
        for fn in (garbled, weird):
            report = self.run_dedupe([a, b], fn)
            self.assertEqual((report["merged"], report["judge_failed"], report["judged"]), ([], 1, 0))
        self.assertEqual(self.decisions(), [])
        self.assertEqual(self.run_dedupe([a, b], lambda c, x, y, conn=None: (dedupe.Decision("different", "model", "two events"), 0.0))["judged"], 1)

    def test_the_real_judge_runs_on_the_callers_connection_and_is_booked_in_the_ledger(self):
        from types import SimpleNamespace
        a, b = docs("ga-post-from-preview-template")

        class Client:
            messages = None

            def __init__(self):
                self.messages = self
                self.requests = []

            def create(self, **request):
                self.requests.append(request)
                return SimpleNamespace(stop_reason="end_turn", stop_details=None, content=[SimpleNamespace(
                    type="text", text=json.dumps({"same": False, "reason": "A preview and a release."}))],
                    usage=SimpleNamespace(input_tokens=900, output_tokens=30, cache_read_input_tokens=0, cache_creation_input_tokens=0))
        client = Client()
        before = self.conn.execute("SELECT count(*) FROM spend_ledger WHERE phase = %s", (dedupe.JUDGE_PHASE,)).fetchone()[0]
        decision, cost = dedupe.judge(client, a, b, conn=self.conn)
        after = self.conn.execute("SELECT count(*) FROM spend_ledger WHERE phase = %s", (dedupe.JUDGE_PHASE,)).fetchone()[0]
        self.assertEqual((decision.verdict, decision.layer), ("different", "model"))
        self.assertGreater(cost, 0)
        self.assertEqual((len(client.requests), after - before), (1, 1))

    def test_doubt_means_different(self):
        a, b = self.pair("ga-post-from-preview-template")

        def broken(client, x, y, conn=None):
            raise RuntimeError("model unavailable")

        def spent(client, x, y, conn=None):
            raise budget.BudgetExceeded("over the ceiling")
        for fn in (broken, spent):
            report = self.run_dedupe([a, b], fn)
            self.assertEqual((report["merged"], report["judge_failed"]), ([], 1), fn.__name__)
            self.assertEqual(dedupe.merged_away(self.conn, [a, b]), set())
        self.assertEqual(self.decisions(), [])                                               # a failure is not recorded as a decision: it is asked again next run
        capped = self.run_dedupe([a, b], max_judgements=0)
        self.assertEqual((capped["judged"], capped["merged"], capped["left_different"]), (0, [], 1))

    def test_a_merged_away_item_stays_merged_in_the_next_pass(self):
        a, b = self.pair("syndicated-copy")
        c = self.fetched("https://third.example/quillbase", PAIRS["syndicated-copy"]["a"]["text"], 9)
        self.run_dedupe([a, b])
        later = self.run_dedupe([a, b, c])
        self.assertEqual({m["dropped"] for m in later["merged"]}, {str(c)})
        self.assertEqual(dedupe.merged_away(self.conn, [a, b, c]), {b, c})

    def test_decisions_are_append_only_except_the_undo(self):
        a, b = self.pair("syndicated-copy")
        self.run_dedupe([a, b])
        scope = "WHERE kept_id = ANY(%s::uuid[])"
        for sql in (f"DELETE FROM dedup_decisions {scope}", f"UPDATE dedup_decisions SET reason = 'edited' {scope}",
                    f"UPDATE dedup_decisions SET verdict = 'different' {scope}", f"UPDATE dedup_decisions SET kept_id = other_id {scope}"):
            with self.assertRaises(psycopg.Error, msg=sql):
                with self.conn.transaction():
                    self.conn.execute(sql, (self.mine(),))
        did = self.decision_row("id")[0]
        dedupe.undo_merge(self.conn, did, "mistake", NOW)
        with self.assertRaises(psycopg.Error):
            with self.conn.transaction():
                self.conn.execute("UPDATE dedup_decisions SET undone_reason = 'rewritten' WHERE id = %s", (did,))

    # captured links -----------------------------------------------------------------------------
    def adder(self, texts):
        def add(conn, client, url, now):
            iid = items.upsert(conn, url, url, now, relevance=(1, ["added by hand"]))[0]
            if url not in texts:
                return {"item": str(iid), "fetched": False}
            items.attach_excerpt(conn, iid, excerpts.store(conn, url, texts[url], now))
            return {"item": str(iid), "fetched": True}
        return add

    @staticmethod
    def fetched_ids(conn, now):
        return [r[0] for r in conn.execute("SELECT id FROM items WHERE status = 'fetched'").fetchall()]

    def test_a_capture_that_reader_add_url_already_folded_is_reported_as_merged(self):
        known = self.fetched("https://wire.example/2026/10/quillbase-series-a", PAIRS["syndicated-copy"]["a"]["text"], 0)
        copy_url = "https://paper.example/business/quillbase-raises"
        calls = []

        def add(conn, client, url, now):                     # what reader.add_url does: fetch, then run the layers itself
            iid = items.upsert(conn, url, url, now, relevance=(1, ["added by hand"]))[0]
            items.attach_excerpt(conn, iid, excerpts.store(conn, url, PAIRS["syndicated-copy"]["b"]["text"], now))
            report = dedupe.dedupe_fetched(conn, client, [iid, known], now)
            calls.append(report)
            return {"item": str(iid), "fetched": True, "merged_into": report["merged"][0]["kept"] if report["merged"] else None}
        report = dedupe.ingest_captured(self.conn, object(), [copy_url], NOW + timedelta(hours=2), add=add, window_ids=self.fetched_ids)
        self.assertEqual((report[0]["outcome"], report[0]["into"]), ("merged", str(known)))
        self.assertIn("fingerprint", report[0]["detail"])
        self.assertEqual(len(calls), 1)                      # not compared a second time

    def test_a_capture_that_raises_does_not_lose_the_rest_and_window_ids_is_required(self):
        def add(conn, client, url, now):
            if "boom" in url:
                raise RuntimeError("network")
            return {"item": str(items.upsert(conn, url, url, now)[0]), "fetched": False}
        report = dedupe.ingest_captured(self.conn, object(), ["https://boom.example/a", "https://ok.example/b"], NOW, add=add, window_ids=self.fetched_ids)
        self.assertEqual([r["outcome"] for r in report], ["error", "fetch-failed"])
        with self.assertRaises(TypeError):
            dedupe.ingest_captured(self.conn, object(), [], NOW, add=add)

    def test_captured_links_report_what_happened_to_each(self):
        known = self.fetched("https://wire.example/2026/10/quillbase-series-a", PAIRS["syndicated-copy"]["a"]["text"], 0)
        paper = self.fetched("https://arxiv.org/abs/2610.04417", "x " * 80, 1)
        copy_url = "https://paper.example/business/quillbase-raises"
        new_url = "https://helio.example/blog/orbit-1-0"
        urls = ["not a url", "ftp://x.example/a", "https://www.reuters.com/world/some-story", "https://WIRE.example/2026/10/quillbase-series-a/?utm_source=x",
                "https://alphaxiv.org/abs/2610.04417", copy_url, new_url, "https://broken.example/page", new_url]
        add = self.adder({copy_url: PAIRS["syndicated-copy"]["b"]["text"], new_url: PAIRS["same-article-fetched-twice"]["a"]["text"]})
        report = dedupe.ingest_captured(
            self.conn, object(), urls, NOW + timedelta(hours=2), add=add,
            window_ids=self.fetched_ids)
        outcomes = [r["outcome"] for r in report]
        self.assertEqual(outcomes, ["rejected", "rejected", "blocked", "already-known", "same-paper", "merged", "added", "fetch-failed", "already-known"])
        self.assertEqual(report[5]["into"], str(known))
        self.assertTrue(all(r.get("detail") for r in report))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM items WHERE canonical_url LIKE '%reuters%'").fetchone()[0], 0)    # never even stored
        sightings = {r[0] for r in self.conn.execute("SELECT url FROM item_sources WHERE item_id = %s", (paper,)).fetchall()}
        self.assertIn("https://alphaxiv.org/abs/2610.04417", sightings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
