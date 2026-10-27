"""Corrections to a published edition (Run 9, Step A): an append-only table, one write path with two gates, and a page that shows the latest.

The database tests run inside one rolled-back transaction on a synthetic week, so no row of theirs survives; the tests that publish a
brief hold a transaction open first (the helpers' own transaction blocks then become savepoints).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

import psycopg

import app as site
from agents import briefs, budget, review, revisions, runs
from tests import _page
from tests._db import needs_db
from tests.test_digest import composed_body

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
WEEK = "1998-W40"
PASS = {"claims": [{"claim": "a claim", "supported": True, "span_verified": True}], "unsupported_claims": [], "grader_errors": []}
FAIL = {"claims": [{"claim": "a claim", "supported": False, "span_verified": False}], "unsupported_claims": ["a claim"], "grader_errors": []}
NEW_TITLE = "Helio shipped a review queue"
NEW_LINES = ["Queues replace branches.", "Review still merges everything."]


def counts(conn):
    return conn.execute("SELECT (SELECT count(*) FROM briefs), (SELECT count(*) FROM runs), (SELECT count(*) FROM edition_revisions)").fetchone()


@needs_db
class Database(unittest.TestCase):
    def setUp(self):
        self.conn = budget.connect(autocommit=False)
        self.conn.execute("SELECT 1")
        self.before = counts(self.conn)
        runs.open_run(self.conn, WEEK, NOW)
        self.body = composed_body()
        briefs.publish(self.conn, WEEK, WEEK, self.body, NOW)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()
        with budget.connect() as check:
            self.assertEqual(counts(check), self.before, "a test leaked into the live tables")

    def revise(self, field="stories.title", value=NEW_TITLE, reason="headline shortened", verdict=PASS, **kw):
        if isinstance(verdict, dict) and "text" not in verdict:
            verdict = dict(verdict, text=revisions.verdict_text(value))       # a verdict names the text it was given for
        return revisions.revise(self.conn, WEEK, field, value, reason, verdict=verdict, **kw)

    # the table ------------------------------------------------------------------------
    def test_the_table_refuses_update_delete_and_truncate_and_so_does_the_brief(self):
        self.revise()
        for sql in ("UPDATE edition_revisions SET reason = 'x'", "DELETE FROM edition_revisions", "TRUNCATE edition_revisions",
                    "UPDATE briefs SET body = '{}'::jsonb", "DELETE FROM briefs"):
            with self.subTest(sql):
                with self.assertRaises(psycopg.errors.RestrictViolation):
                    with self.conn.transaction():
                        self.conn.execute(sql)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM edition_revisions WHERE brief_id = %s", (WEEK,)).fetchone()[0], 1)

    def test_the_table_refuses_a_row_without_a_passing_verdict_or_with_a_long_reason(self):
        bad = [("a failing verdict", json.dumps(FAIL), "ok"), ("no claims", json.dumps({"claims": [], "unsupported_claims": []}), "ok"),
               ("no verdict shape", json.dumps({}), "ok"), ("a reason over 200", json.dumps(PASS), "x" * 201), ("an empty reason", json.dumps(PASS), "")]
        for label, verdict, reason in bad:
            with self.subTest(label):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.transaction():
                        self.conn.execute("INSERT INTO edition_revisions (brief_id, field, old_value, new_value, reason, gate_verdict) "
                                          "VALUES (%s, 'stories.title', '\"a\"', '\"b\"', %s, %s::jsonb)", (WEEK, reason, verdict))

    def test_the_table_refuses_a_verdict_whose_claims_are_not_all_supported_with_a_verified_span(self):
        bad = [("a claim marked unsupported but not listed", {"claims": [{"claim": "c", "supported": False, "span_verified": True}], "unsupported_claims": []}),
               ("a span not verified", {"claims": [{"claim": "c", "supported": True, "span_verified": False}], "unsupported_claims": []}),
               ("a claim with no flags", {"claims": [{"claim": "c"}], "unsupported_claims": []}),
               ("one good claim and one bad", {"claims": [{"claim": "c", "supported": True, "span_verified": True}, {"claim": "d", "supported": False, "span_verified": True}],
                                               "unsupported_claims": []})]
        for label, verdict in bad:
            with self.subTest(label):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.transaction():
                        self.conn.execute("INSERT INTO edition_revisions (brief_id, field, old_value, new_value, reason, gate_verdict) "
                                          "VALUES (%s, 'stories.title', '\"a\"', '\"b\"', 'r', %s::jsonb)", (WEEK, json.dumps(verdict)))

    def test_the_table_refuses_a_field_that_is_not_a_headline(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.conn.execute("INSERT INTO edition_revisions (brief_id, field, old_value, new_value, reason, gate_verdict) "
                                  "VALUES (%s, 'thread.body', '\"a\"', '\"b\"', 'r', %s::jsonb)", (WEEK, json.dumps(PASS)))

    # the write path --------------------------------------------------------------------
    def test_a_good_value_writes_one_row_with_the_old_value_the_reason_and_the_verdict(self):
        old = revisions.current_value(self.body, "stories.title")
        out = self.revise()
        self.assertTrue(out["written"])
        row = revisions.rows_for(self.conn, WEEK)[0]
        self.assertEqual((row["field"], row["old_value"], row["new_value"], row["reason"]), ("stories.title", old, NEW_TITLE, "headline shortened"))
        self.assertEqual(row["gate_verdict"]["claims"][0]["supported"], True)

    def test_a_value_that_breaks_the_headline_cap_writes_nothing(self):
        too_long = " ".join(["word"] * 15)
        for field, value in (("stories.title", too_long), ("learning.headline", "w" * 91), ("thread.lines", ["Cost fell by 8 percent.", "Vendors ship."]),
                             ("thread.lines", ["one two three four five six seven eight nine", "Vendors ship."])):
            with self.subTest(field, value=str(value)[:30]):
                with self.assertRaises(revisions.RevisionRefused) as why:
                    self.revise(field, value)
                self.assertIn("headline cap", str(why.exception))
        self.assertEqual(counts(self.conn)[2], self.before[2])

    def test_a_value_the_claim_gate_does_not_pass_writes_nothing(self):
        with self.assertRaises(revisions.RevisionRefused) as why:
            self.revise(verdict=FAIL)
        self.assertIn("claim gate", str(why.exception))
        self.assertEqual(counts(self.conn)[2], self.before[2])

    def test_the_other_refusals_write_nothing(self):
        cases = [dict(reason=""), dict(reason="r" * 201), dict(field="thread.body", value="x"), dict(field="thread.lines", value=["only one"]),
                 dict(value=self.body["edition"]["storiesTitle"])]
        for kw in cases:
            with self.subTest(kw):
                with self.assertRaises(revisions.RevisionRefused):
                    self.revise(**kw)
        with self.assertRaises(revisions.RevisionRefused):
            revisions.revise(self.conn, "1998-W41", "stories.title", NEW_TITLE, "r", verdict=PASS)
        self.assertEqual(counts(self.conn)[2], self.before[2])

    def test_a_recorded_verdict_must_be_for_exactly_this_text(self):
        for verdict in (PASS, dict(PASS, text="some other headline"), dict(PASS, text=revisions.verdict_text(NEW_TITLE) + " extra")):
            with self.subTest(verdict.get("text")):
                with self.assertRaises(revisions.RevisionRefused) as why:
                    revisions.revise(self.conn, WEEK, "stories.title", NEW_TITLE, "r", verdict=verdict)
                self.assertIn("not for this text", str(why.exception))
        self.assertEqual(counts(self.conn)[2], self.before[2])

    def test_without_a_recorded_verdict_the_claim_gate_runs_and_its_answer_decides(self):
        with unittest.mock.patch.object(revisions, "grade", return_value=dict(PASS, text=revisions.verdict_text(NEW_TITLE))) as gate:
            out = revisions.revise(self.conn, WEEK, "stories.title", NEW_TITLE, "headline shortened")
        gate.assert_called_once()
        self.assertEqual(gate.call_args.args[3], NEW_TITLE)           # it graded the new value
        self.assertTrue(out["written"])
        with unittest.mock.patch.object(revisions, "grade", return_value=dict(FAIL, text="x")) as gate:
            with self.assertRaises(revisions.RevisionRefused) as why:
                revisions.revise(self.conn, WEEK, "stories.title", "Helio shipped a queue", "headline shortened")
        gate.assert_called_once()
        self.assertIn("claim gate", str(why.exception))
        self.assertEqual(counts(self.conn)[2], self.before[2] + 1)    # only the first one was written

    def test_grade_sends_the_value_alone_against_every_stories_excerpts_and_returns_what_the_grader_found(self):
        sent = {}

        def fake_call(client, **kw):
            sent.update(kw)
            raw = json.dumps({"thread": {"claims": [{"claim": "Helio shipped a queue", "supported": True, "supporting_span": "Helio shipped a queue today"}],
                                         "unsupported_claims": [], "confidence": 0.9}})
            return raw, {}, 0.001
        items = [{"id": "s01", "excerpt": "Helio shipped a queue today and more."}, {"id": "s02", "excerpt": "Other text."}]
        with unittest.mock.patch.object(revisions.runs, "load_artifact", lambda *a, **k: {"raw_day": {}}), \
                unittest.mock.patch.object(revisions.weekly, "composer_day", lambda conn, raw: ({}, {}, 0)), \
                unittest.mock.patch.object(revisions.composer, "composer_items", lambda wide, pub: items), \
                unittest.mock.patch.object(revisions.llm, "call", fake_call):
            verdict = revisions.grade(self.conn, WEEK, WEEK, "Helio shipped a queue", client=object(), user_id="u")
        body = json.loads(sent["messages"][0]["content"])
        self.assertEqual(body["thread"]["writing_under_test"], "Helio shipped a queue")
        self.assertEqual([e["id"] for e in body["thread"]["ground_truth_excerpt_union"]], ["s01", "s02"])
        self.assertEqual(verdict["text"], "Helio shipped a queue")
        self.assertTrue(revisions.verdict_passes(verdict))

    def test_a_dry_run_runs_only_the_free_checks_and_writes_nothing(self):
        out = revisions.revise(self.conn, WEEK, "stories.title", NEW_TITLE, "headline shortened", dry_run=True)       # no verdict and no client: nothing is paid
        self.assertEqual((out["written"], out["free_checks"]), (False, "passed"))
        self.assertEqual(counts(self.conn)[2], self.before[2])

    def test_a_second_revision_corrects_the_first_and_keeps_both_rows(self):
        self.revise()
        self.revise(value="Helio shipped a queue", reason="shortened again")
        rows = revisions.rows_for(self.conn, WEEK)
        self.assertEqual([r["new_value"] for r in rows], [NEW_TITLE, "Helio shipped a queue"])
        self.assertEqual(rows[1]["old_value"], NEW_TITLE)           # the second one corrects the corrected text

    # the read path ---------------------------------------------------------------------
    def test_the_page_shows_the_latest_revision_with_a_dated_note_and_never_the_old_text(self):
        old = self.body["edition"]["storiesTitle"]
        self.revise()
        self.revise(value="Helio shipped a queue", reason="shortened again")
        self.revise(field="thread.lines", value=NEW_LINES, reason="headline shortened to the 14-word cap")
        self.revise(field="learning.headline", value="Helio cut merge conflicts sharply", reason="headline shortened to the 14-word cap")
        html = self._page()
        self.assertIn("Helio shipped a queue", html)
        self.assertNotIn(NEW_TITLE, html)                              # superseded
        self.assertNotIn(old, html)                                    # the old text is not on the page, in the note or anywhere
        for line in NEW_LINES:
            self.assertIn(line, html)
        self.assertIn("Helio cut merge conflicts sharply", html)
        notes = re.findall(r'<span class="corrected">(.*?)</span>', html)
        self.assertEqual(len(notes), 3)
        for n in notes:
            self.assertRegex(n, r"^Corrected [A-Z][a-z]{2} \d{1,2}: ")
        self.assertIn("Corrected", notes[0])
        self.assertTrue(any(n.endswith("shortened again") for n in notes))

    def _page(self) -> str:
        return self._render()

    def _render(self) -> str:
        import unittest.mock as m
        archive = ([{"label": "ED 01", "range": "", "l1": "", "l2": "", "stories": 0, "href": f"/weekly-digest?week={WEEK}"}], {WEEK: 1})
        from fastapi.testclient import TestClient
        with m.patch.object(site, "_connect", _page.Borrowed(self.conn)), m.patch.object(site, "_archive", lambda c: archive):
            return TestClient(site.app).get(f"/weekly-digest?week={WEEK}").text

    def test_the_archive_shows_the_corrected_thread_lines(self):
        self.revise(field="thread.lines", value=NEW_LINES, reason="headline shortened to the 14-word cap")
        entries, numbers = site._archive(self.conn)
        mine = [e for e in entries if e.get("label") == f"ED {numbers[WEEK]:02d}" and WEEK in str(e)]
        self.assertTrue(mine)
        joined = json.dumps(mine, default=str)
        self.assertIn("Queues replace branches", joined)

    def test_the_review_page_lists_each_revision_with_its_reason(self):
        self.revise(reason="headline shortened to the 14-word cap")
        with unittest.mock.patch.object(review, "REVIEW_EDITIONS", 50):
            note = [n for n in review.entries(self.conn) if n["week"] == WEEK][0]
        self.assertEqual(len(note["revisions"]), 1)
        self.assertEqual(note["revisions"][0]["reason"], "headline shortened to the 14-word cap")
        self.assertEqual((note["revisions"][0]["claims"], note["revisions"][0]["unsupported"]), (1, 0))
        from fastapi.testclient import TestClient
        with unittest.mock.patch.object(site, "_connect", _page.Borrowed(self.conn)), unittest.mock.patch.object(review, "REVIEW_EDITIONS", 50):
            html = TestClient(site.app).get("/weekly-digest/review").text
        self.assertIn("Corrections after publication", html)
        self.assertIn("headline shortened to the 14-word cap", html)
        self.assertIn(NEW_TITLE, html)


@needs_db
class NoRevisions(unittest.TestCase):
    def test_a_brief_with_no_revisions_renders_byte_for_byte_as_before(self):
        golden = (REPO / "tests" / "golden" / "digest-no-revisions.html").read_text(encoding="utf-8")
        self.assertEqual(_page.render(composed_body(), week="1998-W20"), golden)


class Pure(unittest.TestCase):
    ROW = {"id": 1, "brief_id": "x", "field": "stories.title", "old_value": "old", "new_value": "new", "reason": "headline shortened",
           "gate_verdict": PASS, "created_at": datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc)}

    def test_apply_with_no_rows_returns_the_body_untouched(self):
        body = composed_body()
        out, notes = revisions.apply(body, [])
        self.assertIs(out, body)
        self.assertEqual(notes, {})

    def test_apply_puts_the_latest_row_per_field_in_a_copy_and_leaves_the_brief_alone(self):
        body = composed_body()
        snapshot = json.dumps(body, sort_keys=True)
        rows = [self.ROW, dict(self.ROW, id=2, new_value="newer", reason="again", created_at=datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc))]
        out, notes = revisions.apply(body, rows)
        self.assertEqual(out["edition"]["storiesTitle"], "newer")
        self.assertEqual(notes, {"stories.title": "Corrected Oct 27: again"})
        self.assertEqual(json.dumps(body, sort_keys=True), snapshot)

    def test_the_note_has_the_date_and_the_reason_only(self):
        self.assertEqual(revisions.note(self.ROW["created_at"], "headline shortened"), "Corrected Oct 26: headline shortened")

    def test_the_date_is_the_pacific_date(self):
        late = datetime(2026, 10, 27, 2, 35, tzinfo=timezone.utc)          # 7:35pm on Oct 26 in Pacific time
        self.assertEqual(revisions.when(late), "Oct 26")
        self.assertEqual(revisions.when(datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)), "Oct 27")

    def test_verdict_passes_only_for_a_verified_verdict_with_no_unsupported_claim(self):
        self.assertTrue(revisions.verdict_passes(PASS))
        for bad in (FAIL, {}, None, {"claims": [], "unsupported_claims": []}, dict(PASS, grader_errors=["x"]),
                    {"claims": [{"supported": True, "span_verified": False}], "unsupported_claims": []}):
            self.assertFalse(revisions.verdict_passes(bad), bad)

    def test_a_legacy_brief_without_an_edition_cannot_be_revised(self):
        with self.assertRaises(revisions.RevisionRefused):
            revisions.current_value({"items": []}, "stories.title")


class NoWritePath(unittest.TestCase):
    """No code path can UPDATE or DELETE `briefs` or `edition_revisions`: the only statement that writes either is revise's INSERT."""
    def test_no_source_file_updates_or_deletes_a_brief_or_a_revision(self):
        pattern = re.compile(r"\b(UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(briefs|edition_revisions)\b", re.I)
        offenders = []
        for path in list((REPO / "agents").glob("*.py")) + list((REPO / "scripts").glob("*.py")) + [REPO / "app.py"]:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{n}")
        self.assertEqual(offenders, [])

    def test_the_only_insert_into_the_revision_table_is_in_revise(self):
        hits = []
        for path in list((REPO / "agents").glob("*.py")) + list((REPO / "scripts").glob("*.py")) + [REPO / "app.py"]:
            text = path.read_text(encoding="utf-8")
            if re.search(r"INSERT\s+INTO\s+edition_revisions", text, re.I):
                hits.append(path.name)
        self.assertEqual(hits, ["revisions.py"])


if __name__ == "__main__":
    unittest.main()
