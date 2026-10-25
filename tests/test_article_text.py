"""The evidence trees keep no article text (Run 4, Step A)."""

import json
import tempfile
import unittest
from pathlib import Path

from tests._db import needs_db
from agents import article_text as A, budget

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo "
         "sierra tango uniform victor whiskey xray yankee zulu").split()
SOURCE = " ".join(WORDS[(i * 7) % len(WORDS)] + str(i) for i in range(80))   # about 400 characters of distinct "article" text


def index():
    return A.Index([("excerpt:test", SOURCE)])


class PlantedText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "autopilot/evidence/d").mkdir(parents=True)
        (self.root / "data/evals/runs").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def plant(self, rel, content):
        p = self.root / rel
        p.write_text(content, encoding="utf-8")
        return p

    def hits(self):
        return [h[0] for h in A.scan_tree(index(), self.root)]

    def test_verbatim_copy_in_a_text_file_is_found(self):
        self.plant("autopilot/evidence/d/notes.txt", "our note\n" + SOURCE[:200] + "\nmore of our note")
        self.assertEqual(self.hits(), ["autopilot/evidence/d/notes.txt"])

    def test_199_characters_are_allowed(self):
        self.plant("autopilot/evidence/d/notes.txt", SOURCE[:199])
        self.assertEqual(self.hits(), [])

    def test_a_copy_in_the_runs_tree_is_found(self):
        self.plant("data/evals/runs/r.md", SOURCE[50:300])
        self.assertEqual(self.hits(), ["data/evals/runs/r.md"])

    def test_rewrapped_text_is_found(self):
        wrapped = SOURCE[:260].replace(" ", "\n   ")
        self.plant("autopilot/evidence/d/wrapped.log", wrapped)
        self.assertEqual(self.hits(), ["autopilot/evidence/d/wrapped.log"])

    def test_json_escaped_text_is_found(self):
        self.plant("autopilot/evidence/d/e.json", json.dumps({"a": {"b": ["x", "y " + SOURCE[:230]]}}, ensure_ascii=True))
        self.assertEqual(self.hits(), ["autopilot/evidence/d/e.json"])

    def test_typographic_variants_are_found(self):
        s = "He said it was fine, and that's it. " * 8
        idx = A.Index([("excerpt:t", s)])
        p = self.plant("autopilot/evidence/d/q.txt", s.replace("'", "’").replace(" it was", " it was"))
        self.assertTrue(A.scan_text(idx, p, p.read_text(encoding="utf-8")))

    def test_diff_prefixed_text_is_found(self):
        words = SOURCE[:320].split(" ")
        diff = "\n".join("+" + " ".join(words[i:i + 6]) for i in range(0, len(words), 6))
        self.plant("autopilot/evidence/d/step.diff", "diff --git a/x b/x\n" + diff)
        self.assertEqual(self.hits(), ["autopilot/evidence/d/step.diff"])

    def test_dressed_up_copies_are_found(self):
        cases = {
            "upper.txt": SOURCE[:300].upper(),
            "quote.md": "\n".join("> " + " ".join(SOURCE[:300].split(" ")[i:i + 5]) for i in range(0, 60, 5)),
            "bullets.md": " ".join("* " + w for w in SOURCE[:300].split(" ")),
            "tags.html": " ".join("<b>%s</b>" % w for w in SOURCE[:300].split(" ")),
            "hidden.txt": "\u200b".join(SOURCE[:300]),
            "hyphen.txt": SOURCE[:300].replace("charlie", "char-\nlie"),
        }
        for name, text in cases.items():
            self.plant("autopilot/evidence/d/" + name, text)
        self.assertEqual(sorted(Path(h).name for h in self.hits()), sorted(cases))

    def test_json_inside_json_and_utf16_are_found(self):
        inner = json.dumps({"x": SOURCE[:250]})
        self.plant("autopilot/evidence/d/nested.json", json.dumps({"raw": inner}))
        (self.root / "autopilot/evidence/d/u16.txt").write_bytes(SOURCE[:250].encode("utf-16"))
        self.assertEqual(sorted(Path(h).name for h in self.hits()), ["nested.json", "u16.txt"])

    def test_a_url_a_hash_and_a_length_pass(self):
        self.plant("autopilot/evidence/d/ok.json", json.dumps({"url": "https://example.com/a", "sha256": "0" * 64, "length": 4012, "verdict": "pass"}))
        self.assertEqual(self.hits(), [])


@needs_db
class CommittedEvidence(unittest.TestCase):
    def test_no_file_holds_200_characters_of_a_stored_excerpt(self):
        with budget.connect() as conn:
            corpus = A.db_corpus(conn) + A.golden_corpus()
        self.assertGreater(len(corpus), 0)
        hits = A.scan_tree(A.Index(corpus))
        self.assertEqual(hits, [], "copied article text in: " + ", ".join(h[0] for h in hits))


if __name__ == "__main__":
    unittest.main()
