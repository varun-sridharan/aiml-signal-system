import sys, unittest, importlib.util
sys.path.insert(0, ".")
from agents import dedupe, excerpts
which = sys.argv[1]
names = ["tests.test_dedupe.SafeUrlLayer"]
if which == "no_share_params": excerpts._is_share_param = lambda *a: False
if which == "no_amp_path":
    orig = excerpts.canonicalise
    excerpts.canonicalise = lambda u: orig(u.replace("/amp", "/amp-x")) if False else orig(u)
    import re
    src = open("agents/excerpts.py").read().replace('if path.endswith("/amp") and path[:-len("/amp")].count("/") >= 2:', 'if False:')
    exec(compile(src.replace("from __future__ import annotations", ""), "agents/excerpts.py", "exec"), excerpts.__dict__)
if which == "no_mobile_host":
    src = open("agents/excerpts.py").read().replace('for prefix in ("m.", "amp."):', 'for prefix in ():')
    exec(compile(src, "agents/excerpts.py", "exec"), excerpts.__dict__)
if which == "no_dedupe_in_reader":
    dedupe.dedupe_fetched = lambda conn, client, ids, now, **kw: {"compared": 0, "merged": [], "judged": 0, "left_different": 0, "judge_failed": 0, "skipped_known": 0, "cost_usd": 0.0}
    names = ["tests.test_reader.ReaderAgainstDatabase.test_r23_the_same_article_at_two_addresses_is_one_story_in_the_output_and_can_be_undone"]
r = unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromNames(names))
print(which, "FAILURES:", len(r.failures) + len(r.errors))
