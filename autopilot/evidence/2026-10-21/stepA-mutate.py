import sys, unittest
sys.path.insert(0, ".")
from agents import framer, gates
which = sys.argv[1]
if which == "dash": framer._DASHES = {}
if which == "radar": gates._is_radar = lambda i: False
if which == "radar_loose": gates._full_ninety = lambda n: bool(n)
if which == "retry": framer.RETRY_ON_LIFT = True
names = ["tests.test_retry.RetryIsOffByDefault"] if which == "retry" else ["tests.test_gate_decisions"]
r = unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromNames(names))
print(which, "FAILURES:", len(r.failures)+len(r.errors))
