"""Render the Weekly Digest page for a composed edition inside a rolled-back transaction, with the archive fixed, so the output is deterministic.

Used by tests/test_revisions.py to prove an edition with no revisions renders byte for byte as it did before revisions existed
(tests/golden/digest-no-revisions.html was written by the code of the commit before them).
"""

from __future__ import annotations

import unittest.mock
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app as site
from agents import briefs, budget, runs

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
WEEK = "1998-W20"


class Borrowed:
    def __init__(self, conn):
        self.conn = conn

    def __call__(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def render(body: dict, *, week: str = WEEK, before_page=None) -> str:
    """The page for `body` published as `week`; `before_page(conn)` may add rows (revisions) in the same transaction first."""
    conn = budget.connect(autocommit=False)
    conn.execute("SELECT 1")          # hold a transaction open, so the helpers' own transaction blocks are savepoints and rollback undoes everything
    try:
        runs.open_run(conn, week, NOW)
        briefs.publish(conn, week, week, body, NOW)
        if before_page:
            before_page(conn)
        archive = ([{"label": "ED 01", "range": "", "l1": "", "l2": "", "stories": 0, "href": f"/weekly-digest?week={week}"}], {week: 1})
        with unittest.mock.patch.object(site, "_connect", Borrowed(conn)), unittest.mock.patch.object(site, "_archive", lambda c: archive):
            return TestClient(site.app).get(f"/weekly-digest?week={week}").text
    finally:
        conn.rollback()
        conn.close()
