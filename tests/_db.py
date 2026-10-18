"""Shared by the database tests: whether the database is reachable, and what to do if not.

    @needs_db
    class SomethingAgainstTheRealDatabase(unittest.TestCase): ...

By default a test that needs the database skips when it is unreachable, so the suite still
runs on a machine without the tunnel. Set REQUIRE_DB=1 and the same tests FAIL instead:
use it before a push, where a green run with every database test skipped proves nothing.

    REQUIRE_DB=1 .venv/bin/python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import functools
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import budget  # noqa: E402


def database_available() -> bool:
    try:
        with budget.connect():
            return True
    except budget.LedgerUnavailable:
        return False


DB = database_available()
REQUIRED = os.environ.get("REQUIRE_DB", "").strip() not in ("", "0")
REASON = f"database unreachable — {budget.TUNNEL_HINT}"


def needs_db(target):
    """Skip a test or TestCase when the database is unreachable; fail it when REQUIRE_DB=1."""
    if DB:
        return target
    if not REQUIRED:
        return unittest.skip(REASON)(target)
    message = f"{REASON} (REQUIRE_DB=1, so this fails instead of skipping)"
    if isinstance(target, type):
        # Fail in setUp, before the class's own setUp (or setUpClass, replaced too) tries
        # to connect: one clear failure per test, rather than a connection error.
        def setUp(self):
            self.fail(message)
        target.setUp = setUp
        target.setUpClass = classmethod(lambda cls: None)
        target.tearDownClass = classmethod(lambda cls: None)
        return target

    @functools.wraps(target)
    def failing(self, *args, **kwargs):
        self.fail(message)
    return failing
