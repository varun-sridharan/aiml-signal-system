"""The cached prefix of each Opus call is the same on every call (Run 4, Step B).

The probe in autopilot/evidence/2026-10-26/ showed that the cache works: a second identical call reads the whole
prefix. What decides a hit is that nothing in the prefix (the output schema, the system prompt) changes between
calls, so these tests build requests for different days and check that everything before the user turn is
byte-identical, and that the one volatile thing, the day's data, is only in the user turn.
"""

import datetime
import json
import unittest

from agents import composer, framer
from tests.test_composer import raw_day


def prefix(req):
    return json.dumps({k: req[k] for k in req if k != "messages"}, sort_keys=True, ensure_ascii=False)


class Prefix(unittest.TestCase):
    def setUp(self):
        self.profile = framer.load_json(framer.PROFILE_PATH)
        self.system = composer.load_system_prompt()

    def compose(self, day, label="Oct 10 – Oct 18, 2026", date="2026-10-11"):
        items = {"items": [{"id": i["id"], "why": "W", "ninety": None} for i in day["items"]]}
        return composer.compose_request(self.system, self.profile, day, items, label, {i["id"]: date for i in day["items"]})

    def test_the_composing_prefix_is_identical_for_different_days(self):
        a, b = raw_day(5), raw_day(3)
        b["date"] = "2026-11-01"
        ra, rb = self.compose(a), self.compose(b, "Oct 24 – Nov 1, 2026", "2026-10-30")
        self.assertEqual(prefix(ra), prefix(rb))
        self.assertNotEqual(ra["messages"], rb["messages"])
        self.assertEqual(ra["system"][0]["cache_control"], {"type": "ephemeral"})

    def test_the_prefix_holds_no_date_or_week(self):
        """Nothing the loaders add is today's date, week or month: the text has each exactly as often as its source files do."""
        today = datetime.date.today()
        marks = (today.isoformat(), f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}", today.strftime("%B %d, %Y"),
                 today.strftime("%b %d, %Y"), today.strftime("%Y%m%d"), str(today.year))
        for text, files in ((self.system, (composer.PROMPT_PATH, composer.STYLE_GUIDE_PATH)),
                            (framer.load_framer_system_prompt(), (framer.PROMPT_PATH,))):
            source = "\n".join(f.read_text() for f in files)
            for mark in marks:
                self.assertEqual(text.count(mark), source.count(mark), mark)

    def test_loading_the_prompt_twice_gives_the_same_text(self):
        self.assertEqual(composer.load_system_prompt(), composer.load_system_prompt())
        self.assertEqual(framer.load_framer_system_prompt(), framer.load_framer_system_prompt())

    def test_the_framing_prefix_is_identical_for_different_days(self):
        text = framer.load_framer_system_prompt()
        a, b = raw_day(5), raw_day(2)
        b["date"] = "2026-11-01"
        ra, rb = framer.framing_request(text, self.profile, a), framer.framing_request(text, self.profile, b)
        self.assertEqual(prefix(ra), prefix(rb))
        self.assertNotEqual(ra["messages"], rb["messages"])
        self.assertEqual(ra["system"][0]["cache_control"], {"type": "ephemeral"})

    def test_a_revision_shares_the_composers_system_block(self):
        day = raw_day(5)
        base = self.compose(day)
        rev = composer.revise_request(self.system, self.profile, day, {"items": []}, "Oct 10 – Oct 18, 2026", None,
                                      _composed_for(day), {"thread.body": ["too short"]})
        self.assertEqual(rev["system"], base["system"])


def _composed_for(day):
    from tests.test_composer import valid
    return valid(len(day["items"]))


if __name__ == "__main__":
    unittest.main()
