"""Step D mutation proofs: break agents/citegate.py or agents/composer_cited.py, run tests/test_citegate.py, restore."""
import subprocess, sys
M = [
 ("citegate", "the substring check", 'if len(passage.strip()) < MIN_PASSAGE or passage not in doc["text"]:', 'if False:'),
 ("citegate", "the minimum passage length", 'len(passage.strip()) < MIN_PASSAGE or ', ''),
 ("citegate", "the offset check", 'elif c.get("start") is not None and doc["text"][c["start"]:c["end"]] != passage:', 'elif False:'),
 ("citegate", "the own-story restriction", "        allowed = {own}", "        allowed = None"),
 ("citegate", "the uncited check", "    elif not sent[\"citations\"]:\n        out.append(f\"{path}: uncited factual sentence", "    elif False:\n        out.append(f\"{path}: uncited factual sentence"),
 ("citegate", "the figure check on marked sentences", "if _FIGURE.search(plain) or _NUMBER_WORD.search(plain):", "if False:"),
 ("citegate", "the spelled-out number check", " or _NUMBER_WORD.search(plain)", ""),
 ("citegate", "any citation is enough -> all must pass", 'if any(f["supports"] for f in s["verdict"]["first"]):', 'if all(f["supports"] for f in s["verdict"]["first"]):'),
 ("citegate", "the second check is ignored", 's["verdict"]["supported"] = bool(a and isinstance(a.get("unsupported"), list) and not a["unsupported"])', 's["verdict"]["supported"] = True'),
 ("citegate", "the second check uses the first check's model", "extra = dict(model=llm.FRAMER_MODEL, max_tokens=12_000,", "extra = dict(model=llm.CHECK_MODEL, max_tokens=12_000,"),
 ("citegate", "a missing first answer counts as support", 'ok = bool(a and a.get("supports") is True)', 'ok = a is None or bool(a.get("supports"))'),
 ("citegate", "the judgement check accepts anything", 'if a is None or a.get("states_fact") is not False:', 'if False:'),
 ("citegate", "MIN_STORIES 3", "MIN_STORIES = 4", "MIN_STORIES = 3"),
 ("citegate", "structural failures ignored", "blockers += list(structural or [])", "pass"),
 ("citegate", "week-level failures do not block", "        else:\n            blockers.extend(reasons)", "        else:\n            pass"),
 ("citegate", "the abbreviation guard", "        if _ABBREV.search(piece):\n            continue\n        cuts.append", "        cuts.append"),
 ("citegate", "last section wins", "out.setdefault(m.group(1), (m.end(), end))", "out[m.group(1)] = (m.end(), end)"),
 ("citegate", "ties may cite nothing else", 'if path.endswith(".tie"):\n        allowed = None', 'if False:\n        allowed = None'),
 ("composer_cited", "no checks after a hold", "    held = set(decision[\"held\"])\n    if not held:\n        return []", "    return []"),
 ("composer_cited", "labels are not checked", 'labelled = path.endswith((".hashtags", ".term")) or path.startswith("divider.")', 'labelled = False'),
]
out = []
files = {"citegate": "agents/citegate.py", "composer_cited": "agents/composer_cited.py"}
orig = {k: open(v).read() for k, v in files.items()}
try:
    for mod, name, old, new in M:
        assert old in orig[mod], name
        open(files[mod], "w").write(orig[mod].replace(old, new, 1))
        r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_citegate"], capture_output=True, text=True)
        open(files[mod], "w").write(orig[mod])
        out.append(f"{'KILLED  ' if r.returncode else 'SURVIVED'} {name}  ({r.stderr.strip().splitlines()[-1]})")
finally:
    for k, v in files.items():
        open(v, "w").write(orig[k])
r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_citegate"], capture_output=True, text=True)
out.append(f"clean code: {r.stderr.strip().splitlines()[-1]}")
print("\n".join(out))
