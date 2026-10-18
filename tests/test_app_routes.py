"""The app's own routes that are not static pages: /add-news.

    python -m unittest tests.test_app_routes -v

No database and no model: the Reader call is replaced, so these prove who may ask and
what is refused, not what a fetch returns (tests/test_reader.py covers that).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

import app as site  # noqa: E402

TOKEN = "t" * 24


class AddNews(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(site.app)
        site._add_news_times.clear()

    def post(self, body, token=TOKEN, **kw):
        headers = {"x-add-news-token": token} if token is not None else {}
        return self.client.post("/add-news", json=body, headers=headers, **kw)

    def test_off_until_a_token_is_configured(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": ""}):
            self.assertEqual(self.post({"url": "https://example.com/a"}).status_code, 503)

    def test_wrong_or_missing_token_is_forbidden_and_spends_nothing(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), mock.patch.object(site, "_add_news") as add:
            self.assertEqual(self.post({"url": "https://example.com/a"}, token="wrong").status_code, 403)
            self.assertEqual(self.post({"url": "https://example.com/a"}, token=None).status_code, 403)
            add.assert_not_called()

    def test_bad_urls_are_refused(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), mock.patch.object(site, "_add_news") as add:
            for bad in ("ftp://example.com/a", "not a url", "", "https://example.com/" + "a" * 300):
                self.assertEqual(self.post({"url": bad}).status_code, 400, bad)
            add.assert_not_called()

    def test_a_valid_request_fetches_through_reader(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), \
                mock.patch.object(site, "_add_news", return_value={"item": "x", "fetched": True}) as add:
            r = self.post({"url": "https://example.com/a"})
            self.assertEqual((r.status_code, r.json()["fetched"]), (200, True))
            add.assert_called_once_with("https://example.com/a")

    def test_the_ledger_refusal_is_a_503_not_a_500(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), \
                mock.patch.object(site, "_add_news", side_effect=site.budget.LedgerUnavailable("no ledger")):
            self.assertEqual(self.post({"url": "https://example.com/a"}).status_code, 503)

    def test_a_loop_with_the_right_token_is_rate_limited(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), mock.patch.object(site, "_add_news", return_value={}):
            codes = [self.post({"url": f"https://example.com/{i}"}).status_code for i in range(site.ADD_NEWS_LIMIT + 2)]
        self.assertEqual(codes[:site.ADD_NEWS_LIMIT], [200] * site.ADD_NEWS_LIMIT)
        self.assertEqual(codes[site.ADD_NEWS_LIMIT:], [429, 429])

    def test_a_non_ascii_token_is_forbidden_not_a_crash(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), mock.patch.object(site, "_add_news") as add:
            r = self.client.post("/add-news", json={"url": "https://example.com/a"},
                                 headers={"x-add-news-token": "tökén".encode("utf-8")})
            self.assertEqual(r.status_code, 403)
            add.assert_not_called()

    def test_an_unexpected_failure_is_a_502_with_no_detail(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), \
                mock.patch.object(site, "_add_news", side_effect=RuntimeError("secret internals")):
            r = self.post({"url": "https://example.com/a"})
            self.assertEqual(r.status_code, 502)
            self.assertNotIn("secret", r.text)

    def test_a_missing_api_key_is_a_502_not_a_process_exit(self):
        with mock.patch.dict(os.environ, {"ADD_NEWS_TOKEN": TOKEN}), \
                mock.patch.object(site.llm, "build_client", side_effect=SystemExit("ANTHROPIC_API_KEY is not set")):
            self.assertEqual(self.post({"url": "https://example.com/a"}).status_code, 502)

    def test_the_pages_still_serve(self):
        for path in ("/plan", "/roadmap", "/health"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
