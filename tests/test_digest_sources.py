"""The old digest's source catalogue, as rows the Reader seeds. Nothing in the rows is invented.

    python -m unittest tests.test_digest_sources -v

config/digest-source-rows.json is a hand transcription of config/digest-sources.md. These tests make
that auditable: every name and every address in the rows is literally in the catalogue, every address
the catalogue names is in the rows (or is a blocked domain), and tiers are the catalogue's A to D.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents import budget, reader, sources  # noqa: E402
from tests._db import needs_db  # noqa: E402

MD = (REPO / "config" / "digest-sources.md").read_text(encoding="utf-8")
FLAT = " ".join(MD.split()).lower()
DATA = json.loads((REPO / "config" / "digest-source-rows.json").read_text(encoding="utf-8"))


class Transcription(unittest.TestCase):
    def test_every_name_and_address_is_in_the_catalogue(self):
        for row in DATA["sources"]:
            self.assertIn(row["name"].lower(), FLAT, f"{row['name']!r} is not in config/digest-sources.md")
            if row.get("domain"):
                self.assertIn(row["domain"].lower(), FLAT, f"{row['domain']} is not in config/digest-sources.md")
        for blocked in DATA["blocked_domains"]:
            self.assertIn(blocked["domain"], FLAT)

    def test_every_address_the_catalogue_names_is_a_row_or_blocked(self):
        named = {m.group(0).rstrip(".,)") for m in re.finditer(
            r"\b(?:[a-z0-9-]+\.)+(?:com|org|io|ai|dev|in|google|blog)\b", MD)}
        have = {r["domain"] for r in DATA["sources"] if r.get("domain")} | {b["domain"] for b in DATA["blocked_domains"]}
        for domain in named:
            self.assertTrue(any(domain == h or domain.endswith("." + h) or h.endswith("." + domain) for h in have),
                            f"{domain} is in the catalogue but in no row")

    def test_tiers_and_addresses_are_well_formed(self):
        domains = [r["domain"] for r in DATA["sources"] if r.get("domain")]
        self.assertEqual(len(domains), len(set(domains)))                 # an address belongs to one source
        ids = [sources.slug(r["name"]) for r in DATA["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r["tier"] in "ABCD" and len(r["tier"]) == 1 for r in DATA["sources"]))
        rows = sources.load_digest_rows()
        self.assertEqual(len(rows), len(DATA["sources"]))
        self.assertEqual({r.reputation_prior for r in rows if r.tier == "D"}, {"low"})

    def test_a_source_without_an_address_has_none(self):
        by_name = {r.name: r for r in sources.load_digest_rows()}
        self.assertIsNone(by_name["SAP"].domain)
        self.assertIsNone(by_name["Ollama"].domain)
        self.assertEqual(by_name["The New Stack"].domain, "thenewstack.io")

    def test_blocked_domains(self):
        self.assertEqual(set(sources.blocked_domains()), {"reuters.com", "arstechnica.com"})
        self.assertTrue(sources.is_blocked("www.reuters.com"))
        self.assertFalse(sources.is_blocked("example.com"))

    def test_every_feed_belongs_to_a_source_the_seed_creates(self):
        known = {s.id for s in sources.load_seed()} | {s.id for s in sources.load_digest_rows()}
        for feed in reader.FEEDS:
            self.assertIn(feed.source_id, known, feed.id)
            if feed.kind == "index":
                self.assertTrue(feed.pattern, feed.id)


@needs_db
class Seeding(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_seeding_adds_what_is_missing_and_never_overwrites(self):
        self.conn.execute("DELETE FROM sources WHERE id = 'sap'")          # inside the rolled-back transaction
        before = self.conn.execute("SELECT id, tier, domain FROM sources WHERE id = 'anthropic'").fetchone()
        sources.seed_digest(self.conn)
        again = sources.seed_digest(self.conn)
        self.assertEqual(again, 0)                                           # idempotent
        self.assertEqual(self.conn.execute("SELECT tier, domain FROM sources WHERE id = 'sap'").fetchone(), ("A", None))
        self.assertEqual(self.conn.execute("SELECT id, tier, domain FROM sources WHERE id = 'anthropic'").fetchone(), before)
        row = self.conn.execute("SELECT tier, reputation_prior, domain FROM sources WHERE id = 'the-new-stack'").fetchone()
        self.assertEqual(row, ("C", "medium", "thenewstack.io"))

    def test_the_feeds_attach_to_their_sources(self):
        reader.prepare(self.conn)
        got = dict(self.conn.execute("SELECT id, domain FROM sources WHERE id IN ('anthropic', 'meta-ai', 'mistral', "
                                     "'nvidia-developer-blog', 'microsoft-ai', 'cio-dive', 'the-new-stack')").fetchall())
        self.assertEqual(got, {"anthropic": "anthropic.com", "meta-ai": "ai.meta.com", "mistral": "mistral.ai",
                               "nvidia-developer-blog": "developer.nvidia.com", "microsoft-ai": "microsoft.ai",
                               "cio-dive": "ciodive.com", "the-new-stack": "thenewstack.io"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
