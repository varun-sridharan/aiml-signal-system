"""The excerpt store, against the real database, without keeping anything.

    python -m unittest tests.test_excerpts -v

Every test runs inside a transaction that is rolled back, so nothing it stores, cites or
deletes survives it; tearDown checks the live row counts are unchanged. Tests skip when
the database is unreachable (start the tunnel first), or fail with REQUIRE_DB=1.

To prove the trigger tests can fail, run with a trigger disabled:

    EXCERPT_TEST_DISABLE_TRIGGER=excerpts_write_once python -m unittest tests.test_excerpts

The trigger is disabled inside each test's own transaction (ALTER TABLE is transactional
in Postgres), so the rollback restores it and the live table is never left unguarded.
"""

from __future__ import annotations

import os
import sys
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import psycopg  # noqa: E402

from agents import budget, excerpts  # noqa: E402
from tests._db import needs_db  # noqa: E402

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
URL = "https://www.Example.com/news/Story-One/?utm_source=x&ref=y#:~:text=hello"
TEXT = "OpenAI closed a record $122B round at an $852B post-money valuation."


DISABLED = os.environ.get("EXCERPT_TEST_DISABLE_TRIGGER", "").strip()


class Canonicalise(unittest.TestCase):
    def test_matches_add_news(self):
        self.assertEqual(
            excerpts.canonicalise("https://www.ciodive.com/news/rising-spend-cios-AI-success/829484/"
                                  "?utm_source=Sailthru&utm_campaign=x"),
            "https://ciodive.com/news/rising-spend-cios-AI-success/829484",
        )
        self.assertEqual(excerpts.canonicalise(URL), "https://example.com/news/Story-One")
        self.assertEqual(excerpts.canonicalise("http://WWW.example.com/"), "https://example.com")


@needs_db
class ExcerptStore(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")  # open the outer transaction; everything below is a savepoint
        self.live = self.counts()
        if DISABLED:
            table = "excerpt_citations" if DISABLED.startswith("excerpt_citations") else "excerpts"
            self.conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER {DISABLED}")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(self.counts(check), self.live, "a test leaked into the live tables")

    def counts(self, conn=None):
        conn = conn or self.conn
        return conn.execute(
            "SELECT (SELECT count(*) FROM excerpts), (SELECT count(*) FROM excerpt_citations)").fetchone()

    def raw(self, eid, cols="*"):
        return self.conn.execute(f"SELECT {cols} FROM excerpts WHERE id = %s", (eid,)).fetchone()

    def assertRefusedByDatabase(self, sql, args):
        with self.assertRaises(psycopg.errors.RestrictViolation) as refused:
            with self.conn.transaction():  # a savepoint, so the failure does not end the test
                self.conn.execute(sql, args)
        return str(refused.exception)

    # a ----------------------------------------------------------------------------
    def test_a_round_trips_byte_for_byte_including_non_ascii(self):
        text = ("Zürich, 東京, São Paulo — “quoted”, ‘single’, naïve café, emoji 🧪, "
                "a tab\there, CRLF\r\nand trailing spaces   ")
        eid = excerpts.store(self.conn, URL, text, NOW)
        got = excerpts.get(self.conn, eid)
        self.assertEqual(got, text)
        self.assertEqual(got.encode("utf-8"), text.encode("utf-8"))
        url, domain, text_bytes, expires = self.raw(eid, "canonical_url, domain, text_bytes, expires_at")
        self.assertEqual((url, domain), ("https://example.com/news/Story-One", "example.com"))
        self.assertEqual(text_bytes, len(text.encode("utf-8")))
        self.assertEqual(expires, NOW + timedelta(days=excerpts.RETENTION_DAYS))

    # b ----------------------------------------------------------------------------
    def test_b_same_text_is_one_row_changed_text_is_a_new_row(self):
        first = excerpts.store(self.conn, URL, TEXT, NOW)
        again = excerpts.store(self.conn, "http://example.com/news/Story-One/", TEXT, NOW + timedelta(days=1))
        self.assertEqual(first, again)
        before = self.raw(first)
        edited = excerpts.store(self.conn, URL, TEXT + " (updated)", NOW + timedelta(days=2))
        self.assertNotEqual(edited, first)
        self.assertEqual(self.raw(first), before, "the original excerpt was touched")
        n = self.conn.execute("SELECT count(*) FROM excerpts WHERE canonical_url = %s",
                              ("https://example.com/news/Story-One",)).fetchone()[0]
        self.assertEqual(n, 2)
        self.assertEqual(excerpts.get(self.conn, first), TEXT)

    # c ----------------------------------------------------------------------------
    def test_c_database_refuses_changes_to_stored_text(self):
        eid = excerpts.store(self.conn, URL, TEXT, NOW)
        self.assertRefusedByDatabase("UPDATE excerpts SET text_z = %s WHERE id = %s",
                                     (zlib.compress(b"rewritten"), eid))
        self.assertRefusedByDatabase("UPDATE excerpts SET content_sha256 = %s WHERE id = %s", ("0" * 64, eid))
        self.assertRefusedByDatabase("UPDATE excerpts SET canonical_url = %s WHERE id = %s", ("https://x.test", eid))
        self.assertRefusedByDatabase("UPDATE excerpts SET fetched_at = %s WHERE id = %s",
                                     (NOW - timedelta(days=1), eid))
        # expires_at may only move from a date to NULL, never to another date, never back.
        self.assertRefusedByDatabase("UPDATE excerpts SET expires_at = %s WHERE id = %s",
                                     (NOW + timedelta(days=999), eid))
        excerpts.cite(self.conn, eid, "brief-c")
        self.assertRefusedByDatabase("UPDATE excerpts SET expires_at = %s WHERE id = %s", (NOW, eid))
        self.assertEqual(excerpts.get(self.conn, eid), TEXT)

    # d ----------------------------------------------------------------------------
    def test_d_tampered_text_fails_the_hash_check(self):
        eid = excerpts.store(self.conn, URL, TEXT, NOW)
        # Tampering has to get past the trigger first, which only the table's owner can
        # do. Done here inside the rolled-back transaction, to simulate corruption.
        self.conn.execute("ALTER TABLE excerpts DISABLE TRIGGER excerpts_write_once")
        tampered = TEXT.replace("$122B", "$123B").encode("utf-8")
        self.conn.execute("UPDATE excerpts SET text_z = %s WHERE id = %s", (zlib.compress(tampered), eid))
        with self.assertRaises(excerpts.ExcerptCorrupted):
            excerpts.get(self.conn, eid)
        self.conn.execute("UPDATE excerpts SET text_z = %s WHERE id = %s", (b"not zlib at all", eid))
        with self.assertRaises(excerpts.ExcerptCorrupted):
            excerpts.get(self.conn, eid)

    # e ----------------------------------------------------------------------------
    def test_e_cite_pins_forever(self):
        eid = excerpts.store(self.conn, URL, TEXT, NOW - timedelta(days=400))
        excerpts.cite(self.conn, eid, "brief-2026-10-12")
        excerpts.cite(self.conn, eid, "brief-2026-10-12")  # idempotent
        excerpts.cite(self.conn, eid, "brief-2027-01-05")  # one excerpt, many briefs
        self.assertIsNone(self.raw(eid, "expires_at")[0])
        n = self.conn.execute("SELECT count(*) FROM excerpt_citations WHERE excerpt_id = %s", (eid,)).fetchone()[0]
        self.assertEqual(n, 2)
        excerpts.expire(self.conn, NOW + timedelta(days=36500))
        self.assertEqual(excerpts.get(self.conn, eid), TEXT)

    # f ----------------------------------------------------------------------------
    def test_f_database_refuses_to_delete_a_cited_excerpt(self):
        eid = excerpts.store(self.conn, URL, TEXT, NOW)
        excerpts.cite(self.conn, eid, "brief-f")
        msg = self.assertRefusedByDatabase("DELETE FROM excerpts WHERE id = %s", (eid,))
        self.assertIn("cited", msg)
        # ...and the protection cannot be removed by deleting the citation first.
        self.assertRefusedByDatabase("DELETE FROM excerpt_citations WHERE excerpt_id = %s", (eid,))

    # g ----------------------------------------------------------------------------
    def test_g_expire_deletes_only_uncited_rows_past_their_date(self):
        already = self.conn.execute(
            "SELECT count(*) FROM excerpts e WHERE expires_at < %s AND NOT EXISTS "
            "(SELECT 1 FROM excerpt_citations c WHERE c.excerpt_id = e.id)", (NOW,)).fetchone()[0]
        stale = excerpts.store(self.conn, "https://a.test/stale", "old and uncited", NOW - timedelta(days=60))
        fresh = excerpts.store(self.conn, "https://a.test/fresh", "new and uncited", NOW - timedelta(days=1))
        cited = excerpts.store(self.conn, "https://a.test/cited", "old but cited", NOW - timedelta(days=60))
        excerpts.cite(self.conn, cited, "brief-g")
        self.assertEqual(excerpts.expire(self.conn, NOW), already + 1)
        self.assertIsNone(self.raw(stale))
        self.assertIsNotNone(self.raw(fresh))
        self.assertIsNotNone(self.raw(cited))
        self.assertEqual(excerpts.expire(self.conn, NOW), 0)

    # h ----------------------------------------------------------------------------
    def test_h_pages_and_oversize_text_are_refused(self):
        before = self.counts()
        for page in ("<!doctype html><HTML><body>article</body></html>", "intro <body class=x> text"):
            with self.assertRaises(excerpts.ExcerptRefused):
                excerpts.store(self.conn, URL, page, NOW)
        with self.assertRaises(excerpts.ExcerptRefused):
            excerpts.store(self.conn, URL, "x" * (excerpts.MAX_TEXT_BYTES + 1), NOW)
        # The limit is on UTF-8 bytes, not characters: 70,000 three-byte characters is over.
        with self.assertRaises(excerpts.ExcerptRefused):
            excerpts.store(self.conn, URL, "東" * 70_000, NOW)
        excerpts.store(self.conn, URL, "x" * excerpts.MAX_TEXT_BYTES, NOW)  # exactly at the limit is fine
        self.assertEqual(self.counts()[0], before[0] + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
