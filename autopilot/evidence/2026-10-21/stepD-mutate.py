"""Break one thing in the Weekly Digest page and count the failures in its tests.

    REQUIRE_DB=1 python autopilot/evidence/2026-10-21/stepD-mutate.py <name>
"""
import sys, unittest
sys.path.insert(0, ".")
import app as site
from agents import digest
which = sys.argv[1]
if which == "tokens_unparsed":
    digest.segments = lambda text, **k: [{"kind": "plain", "text": text or ""}]
elif which == "two_primaries":
    orig = digest._story_sources
    def many(raw):
        out = orig(raw)
        for s in out: s["primary"] = True
        return out
    digest._story_sources = many
elif which == "archive_oldest_first":
    orig_archive = site._archive
    site._archive = lambda conn: (lambda a, n: (list(reversed(a[:-1])) + a[-1:], n))(*orig_archive(conn))
elif which == "no_sample_row":
    orig_archive = site._archive
    site._archive = lambda conn: (lambda a, n: (a[:-1], n))(*orig_archive(conn))
elif which == "unsafe_links_allowed":
    digest._safe_url = lambda url: url if isinstance(url, str) else None
elif which == "ids_unchecked":
    digest.STORY_ID = __import__("re").compile(r".*")
elif which == "unknown_tokens_visible":
    digest.TOKEN = __import__("re").compile(r"\[\[(#[A-Za-z0-9_-]+|fn:[A-Za-z0-9_-]+|hl)\|([^\]]*)\]\]")
names = ["tests.test_digest"]
r = unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromNames(names))
print(which, "FAILURES:", len(r.failures) + len(r.errors))
