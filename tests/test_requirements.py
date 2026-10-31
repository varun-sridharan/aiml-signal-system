"""requirements.txt pins every dependency to an exact version (2026-10-30: an unpinned anthropic>=0.116.0 let a Railway rebuild install 2.x, which has no
`temperature` argument, and the first scheduled Friday run stopped on it)."""

import re
import unittest
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,_-]+\])?==[0-9][A-Za-z0-9.!+_-]*$")


def unpinned(text: str) -> list:
    lines = [l.split("#", 1)[0].strip() for l in text.splitlines()]
    return [l for l in lines if l and not PINNED.match(l)]


class Pinned(unittest.TestCase):
    def test_every_line_is_pinned_with_double_equals(self):
        self.assertEqual(unpinned(REQUIREMENTS.read_text(encoding="utf-8")), [])

    def test_the_check_rejects_ranges_and_bare_names(self):
        for bad in ("anthropic>=0.116.0", "anthropic", "anthropic~=0.121", "anthropic==0.121.*", "uvicorn[standard]>=0.30.0"):
            self.assertEqual(unpinned(bad), [bad])
        self.assertEqual(unpinned("anthropic==0.121.0\nuvicorn[standard]==0.39.0  # server\n\n"), [])

    def test_anthropic_is_the_version_the_calls_were_written_for(self):
        self.assertIn("anthropic==0.121.0", REQUIREMENTS.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    unittest.main()
