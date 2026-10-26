"""
Offline tests for devsocial_core.py (GitHub + Reddit). Every HTTP response is mocked.
Run:  python -m unittest test_devsocial_core -v
"""
import time
import unittest

import devsocial_core as dc


class Resp:
    def __init__(self, status=200, js=None, headers=None):
        self.status_code, self._js, self.headers = status, js, headers or {}

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


class FakeSession:
    """Routes by URL substring; counts calls."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _answer(self, url):
        self.calls.append(url)
        for key, val in self.routes.items():
            if key in url:
                if isinstance(val, list):
                    return val.pop(0) if len(val) > 1 else val[0]
                return val
        return Resp(404, {"message": "Not Found"})

    def get(self, url, **kw):
        return self._answer(url)

    def post(self, url, **kw):
        return self._answer(url)


RATE = {"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "55", "X-RateLimit-Reset": str(int(time.time()) + 3000)}
REPO = {"full_name": "o/r", "stargazers_count": 120, "forks_count": 15, "open_issues_count": 7,
        "pushed_at": "2026-10-18T10:00:00Z", "archived": False, "description": "d", "html_url": "https://github.com/o/r"}
WEEKS = [{"total": 0, "week": 1, "days": [0] * 7}] * 48 + [{"total": 5}, {"total": 6}, {"total": 7}, {"total": 8}]


def gh_routes(**over):
    r = {"/repos/o/r/stats/commit_activity": Resp(200, WEEKS, RATE),
         "/repos/o/r/contributors": Resp(200, [{"login": "a"}, {"login": "b"}], RATE),
         "/search/issues": Resp(200, {"total_count": 3}, RATE),
         "/repos/o/r": Resp(200, REPO, RATE)}
    r.update(over)
    return r


class TestGitHub(unittest.TestCase):
    def test_parse_repo_url_variants(self):
        self.assertEqual(dc.parse_repo_url("https://github.com/quantnetwork/overledger-sdk"), ("quantnetwork", "overledger-sdk"))
        self.assertEqual(dc.parse_repo_url("https://github.com/o/r.git"), ("o", "r"))
        self.assertEqual(dc.parse_repo_url("http://www.github.com/o/r/tree/main"), ("o", "r"))
        self.assertIsNone(dc.parse_repo_url("https://gitlab.com/o/r"))
        self.assertIsNone(dc.parse_repo_url(""))
        self.assertIsNone(dc.parse_repo_url(None))

    def test_commit_activity_summary(self):
        s = dc.summarize_commit_activity(WEEKS)
        self.assertEqual(s["commits_4w"], 26)
        self.assertEqual(s["commits_52w"], 26)
        with self.assertRaises(ValueError):
            dc.summarize_commit_activity({"nope": 1})

    def test_fetch_repo_happy_path(self):
        out = dc.fetch_repo("o", "r", session=FakeSession(gh_routes()))
        self.assertEqual(out["commits_4w"], 26)
        self.assertEqual(out["info"]["stars"], 120)
        self.assertEqual(out["contributors"], 2)
        self.assertEqual(out["prs_merged_4w"], 3)
        self.assertEqual(out["rate"]["remaining"], 55)
        self.assertEqual(out["notes"], [])

    def test_zero_commits_is_a_real_zero(self):
        s = FakeSession(gh_routes(**{"/repos/o/r/stats/commit_activity": Resp(200, [{"total": 0}] * 52, RATE)}))
        out = dc.fetch_repo("o", "r", session=s)
        self.assertEqual(out["commits_4w"], 0)

    def test_stats_202_retries_once_then_reports_pending(self):
        s = FakeSession(gh_routes(**{"/repos/o/r/stats/commit_activity": Resp(202, None, RATE)}))
        out = dc.fetch_repo("o", "r", session=s, sleep=lambda x: None)
        self.assertIsNone(out["commits_4w"])                       # unknown, NOT zero
        self.assertTrue(any("still being computed" in n for n in out["notes"]))
        self.assertEqual(sum("commit_activity" in u for u in s.calls), 2)   # exactly one retry

    def test_rate_limit_detected_with_reset(self):
        h = {"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 600)}
        s = FakeSession({"/repos/o/r": Resp(403, {"message": "API rate limit exceeded"}, h)})
        with self.assertRaises(dc.SourceError) as cx:
            dc.fetch_repo("o", "r", session=s)
        self.assertEqual(cx.exception.kind, "rate_limit")
        self.assertGreater(cx.exception.retry_after, 500)
        self.assertEqual(len(s.calls), 1)                          # stopped immediately

    def test_404_is_not_found(self):
        with self.assertRaises(dc.SourceError) as cx:
            dc.fetch_repo("o", "gone", session=FakeSession({}))
        self.assertEqual(cx.exception.kind, "not_found")

    def test_malformed_repo_payload(self):
        with self.assertRaises(ValueError):
            dc.parse_repo_info({"hello": 1})

    def test_no_repo_linked(self):
        out = dc.fetch_github(("https://gitlab.com/x/y", ""))
        self.assertTrue(out["ok"])
        self.assertTrue(out["data"]["no_repo"])

    def test_cache_and_rate_limit_pause(self):
        t = [1000.0]
        calls = []

        def fetch(urls):
            calls.append(urls)
            if urls == ("limited",):
                return {"ok": False, "data": None, "rate": None, "error": {"kind": "rate_limit", "message": "x", "retry_after": 300}}
            return {"ok": True, "data": {"n": 1}, "rate": None, "error": None}
        src = dc.CachedSource(fetch, ttl=3600, clock=lambda: t[0])
        src.get("a", ("a",)); src.get("a", ("a",))
        self.assertEqual(len(calls), 1)                            # cached
        self.assertFalse(src.get("limited", ("limited",))["ok"])
        blocked = src.get("b", ("b",))
        self.assertEqual(blocked["error"]["kind"], "rate_limit")
        self.assertEqual(len(calls), 2)                            # paused: no 3rd request
        t[0] += 301
        src.get("b", ("b",))
        self.assertEqual(len(calls), 3)


class TestReddit(unittest.TestCase):
    ABOUT = {"data": {"display_name": "Quant", "subscribers": 12345, "accounts_active": 77, "title": "t", "created_utc": 1}}
    SEARCH = {"data": {"children": [
        {"data": {"title": "QNT pumping", "author": "alice", "score": 50, "num_comments": 9, "subreddit": "x", "permalink": "/r/x/1"}},
        {"data": {"title": "Quant news", "author": "alice", "score": 5, "num_comments": 1, "subreddit": "y", "permalink": "/r/y/2"}},
        {"data": {"title": "other", "author": "bob", "score": 90, "num_comments": 3, "subreddit": "z", "permalink": "/r/z/3"}},
        {"data": {"title": "mod", "author": "AutoModerator", "score": 1, "num_comments": 0, "subreddit": "z", "permalink": "/r/z/4"}}]}}

    def setUp(self):
        dc._token_cache.update(token=None, exp=0.0)

    def test_waiting_for_login_when_no_credentials(self):
        out = dc.fetch_reddit("Quant", "Quant QNT", "", "", "ua")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["kind"], "config")
        self.assertEqual(out["error"]["message"], "WAITING FOR REDDIT LOGIN")

    def test_parse_subreddit(self):
        self.assertEqual(dc.parse_subreddit("https://www.reddit.com/r/Quant/"), "Quant")
        self.assertIsNone(dc.parse_subreddit(""))

    def test_parse_about_and_search(self):
        a = dc.parse_about(self.ABOUT)
        self.assertEqual((a["subscribers"], a["active"]), (12345, 77))
        s = dc.parse_search(self.SEARCH)
        self.assertEqual(s["count"], 4)
        self.assertEqual(s["top_authors"][0], ("alice", 2))
        self.assertNotIn("AutoModerator", [n for n, _ in s["top_authors"]])
        self.assertEqual(s["top_posts"][0]["author"], "bob")
        with self.assertRaises(ValueError):
            dc.parse_about({"data": {}})
        with self.assertRaises(ValueError):
            dc.parse_search({"x": 1})

    def test_full_fetch_with_mocked_session(self):
        s = FakeSession({"access_token": Resp(200, {"access_token": "tok", "expires_in": 3600}),
                         "/about": Resp(200, self.ABOUT), "/search": Resp(200, self.SEARCH)})
        out = dc.fetch_reddit("Quant", "Quant QNT", "id", "secret", "ua", session=s)
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["subreddit"]["subscribers"], 12345)
        self.assertEqual(out["data"]["mentions"]["count"], 4)
        self.assertEqual(len(s.calls), 3)

    def test_bad_login(self):
        s = FakeSession({"access_token": Resp(401, {"error": "unauthorized"})})
        out = dc.fetch_reddit("Quant", "q", "id", "secret", "ua", session=s)
        self.assertEqual(out["error"]["kind"], "auth")

    def test_rate_limit_stops_further_requests(self):
        s = FakeSession({"access_token": Resp(200, {"access_token": "tok", "expires_in": 3600}),
                         "/about": Resp(429, None, {"Retry-After": "90"}), "/search": Resp(200, self.SEARCH)})
        out = dc.fetch_reddit("Quant", "q", "id", "secret", "ua", session=s)
        self.assertEqual(out["error"]["kind"], "rate_limit")
        self.assertEqual(out["error"]["retry_after"], 90)
        self.assertFalse(any("/search" in u for u in s.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
