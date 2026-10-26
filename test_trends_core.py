"""
Offline tests for trends_core.py.  NOTHING here talks to Google: every response is a saved/mocked
DataFrame shaped like the real trendspy / pytrends output.

Run:   python -m unittest test_trends_core -v        (or:  pytest test_trends_core.py)
"""
import unittest
from datetime import datetime, timedelta

import pandas as pd

import trends_core as tc


class FakeHTTPError(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"{status} Client Error")
        self.response = type("R", (), {"status_code": status, "headers": headers or {}})()


def daily_df(values, keyword="bitcoin", partial_last=False, start=datetime(2026, 9, 21)):
    idx = pd.DatetimeIndex([start + timedelta(days=i) for i in range(len(values))], name="time [UTC]")
    df = pd.DataFrame({keyword: values}, index=idx)
    df["isPartial"] = [False] * (len(values) - 1) + [partial_last]
    return df


def region_df():
    return pd.DataFrame({"geoName": ["Switzerland", "Nigeria", "El Salvador", "Austria", "Germany", "Nowhere"],
                         "geoCode": ["CH", "NG", "SV", "AT", "DE", "XX"],
                         "bitcoin": [100, 91, 88, 80, 77, 0]})


def queries_bundle():
    return {"top": pd.DataFrame({"query": ["bitcoin price", "bitcoin usd"], "value": [100, 92]}),
            "rising": pd.DataFrame({"query": ["btc etf flows", "halving date", "new thing"],
                                    "value": ["Breakout", 350, 5200]})}


def topics_bundle():
    return {"top": pd.DataFrame({"mid": ["/m/1", "/m/2"], "title": ["Bitcoin", "Price"],
                                 "type": ["Cryptocurrency", "Topic"], "value": [100, 40]}),
            "rising": pd.DataFrame()}


def make_factory(interest=None, regions=None, queries=None, topics=None, errors=None):
    """Backend stub. errors = {'interest': exc, ...} makes that section's request raise."""
    errors = errors or {}
    calls_log = []

    def factory(keyword, gprop, timeframe, geo):
        def mk(name, payload):
            def run():
                calls_log.append(name)
                if name in errors:
                    raise errors[name]
                return payload
            return (name, run)
        return "fake", [mk("interest", interest), mk("regions", regions),
                        mk("queries", queries), mk("topics", topics)]
    factory.calls_log = calls_log
    return factory


class TestParsing(unittest.TestCase):
    # 1 ─ valid time series
    def test_1_valid_time_series(self):
        out = tc.parse_interest(daily_df(list(range(40, 70))), "bitcoin")
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["n_period"], 30)
        self.assertEqual(out["last_value"], 69.0)

    # 2 ─ 7-day average uses only observations within 7 days of the last complete one
    def test_2_seven_day_average(self):
        vals = [10] * 23 + [50, 52, 54, 56, 58, 60, 62]          # last 7 days = 50..62 -> mean 56
        out = tc.parse_interest(daily_df(vals), "bitcoin")
        self.assertEqual(out["n7"], 7)
        self.assertEqual(out["avg7"], 56.0)

    # 3 ─ 30-day (period) average uses every complete observation
    def test_3_thirty_day_average(self):
        vals = [10] * 23 + [50, 52, 54, 56, 58, 60, 62]
        out = tc.parse_interest(daily_df(vals), "bitcoin")
        self.assertEqual(out["avg_period"], round(sum(vals) / 30, 1))

    def test_partial_last_day_is_excluded(self):
        vals = [50] * 29 + [0]                                   # today still in progress and near 0
        out = tc.parse_interest(daily_df(vals, partial_last=True), "bitcoin")
        self.assertEqual(out["n_period"], 29)
        self.assertEqual(out["avg_period"], 50.0)

    def test_genuine_zero_is_kept_as_zero(self):
        out = tc.parse_interest(daily_df([0] * 30), "bitcoin")
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["avg7"], 0.0)
        self.assertEqual(out["avg_period"], 0.0)

    def test_too_few_points_gives_none_not_zero(self):
        out = tc.parse_interest(daily_df([40, 45]), "bitcoin")
        self.assertIsNone(out["avg7"])
        self.assertEqual(out["avg_period"], 42.5 if out["n_period"] == 2 else out["avg_period"])

    # 4 ─ regions
    def test_4_regions(self):
        out = tc.parse_regions(region_df(), "bitcoin")
        self.assertEqual(out["state"], "ok")
        self.assertEqual([i["name"] for i in out["items"][:5]],
                         ["Switzerland", "Nigeria", "El Salvador", "Austria", "Germany"])
        self.assertNotIn("Nowhere", [i["name"] for i in out["items"]])   # 0 = no data, not a country result

    def test_4b_regions_geoName_as_index_pytrends_shape(self):
        df = region_df().set_index("geoName").drop(columns="geoCode")
        out = tc.parse_regions(df, "bitcoin")
        self.assertEqual(out["items"][0]["name"], "Switzerland")

    # 5 ─ top related queries
    def test_5_top_queries(self):
        top, _ = tc.parse_queries(queries_bundle())
        self.assertEqual(top["state"], "ok")
        self.assertEqual(top["items"][0], {"query": "bitcoin price", "value": 100})

    # 6 ─ rising related queries incl. Breakout
    def test_6_rising_queries(self):
        _, rising = tc.parse_queries(queries_bundle())
        by = {r["query"]: r for r in rising["items"]}
        self.assertTrue(by["btc etf flows"]["breakout"])
        self.assertIsNone(by["btc etf flows"]["value"])
        self.assertFalse(by["halving date"]["breakout"])
        self.assertEqual(by["halving date"]["value"], 350)
        self.assertTrue(by["new thing"]["breakout"])             # numeric >= 5000 is a breakout too

    # 7 ─ related topics (trendspy 'title' and pytrends 'topic_title' shapes)
    def test_7_topics(self):
        out = tc.parse_topics(topics_bundle())
        self.assertEqual(out["items"][0]["title"], "Bitcoin")
        alt = {"top": pd.DataFrame({"topic_title": ["Ether"], "topic_type": ["Cryptocurrency"], "value": [55]})}
        self.assertEqual(tc.parse_topics(alt)["items"][0]["title"], "Ether")

    # 8 ─ valid but genuinely empty
    def test_8_valid_empty_results(self):
        self.assertEqual(tc.parse_interest(pd.DataFrame(columns=["bitcoin"]), "bitcoin")["state"], "insufficient")
        self.assertEqual(tc.parse_regions(pd.DataFrame(), "bitcoin")["state"], "empty")
        top, rising = tc.parse_queries({"top": pd.DataFrame(), "rising": pd.DataFrame()})
        self.assertEqual((top["state"], rising["state"]), ("empty", "empty"))
        self.assertEqual(tc.parse_topics({"top": pd.DataFrame(), "rising": pd.DataFrame()})["state"], "empty")

    # 11 ─ missing / malformed fields
    def test_11_malformed(self):
        with self.assertRaises(tc.MalformedResponse):
            tc.parse_queries({"top": pd.DataFrame({"foo": [1]}), "rising": pd.DataFrame()})
        with self.assertRaises(tc.MalformedResponse):
            tc.parse_queries(None)
        with self.assertRaises(tc.MalformedResponse):
            tc.parse_regions(pd.DataFrame({"x": [1]}), "bitcoin")
        with self.assertRaises(tc.MalformedResponse):
            tc.parse_topics({"top": pd.DataFrame({"value": [1]})})

    def test_11b_malformed_is_reported_not_zeroed(self):
        f = make_factory(interest=daily_df(list(range(30))), regions=pd.DataFrame({"x": [1]}),
                         queries=queries_bundle(), topics=topics_bundle())
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["sections"]["regions"]["state"], "malformed")
        self.assertEqual(res["sections"]["interest"]["state"], "ok")
        # Google answered every request; a section we cannot read is shown as such but asking again would not
        # help, so the pass counts as complete ("ok"), not "partial" (partial = a REQUEST failed part-way).
        self.assertEqual(res["status"], "ok")
        self.assertIsNone(res["error"])


class TestFetchAndErrors(unittest.TestCase):
    def test_full_success_passes_through_everything(self):
        f = make_factory(daily_df(list(range(40, 70))), region_df(), queries_bundle(), topics_bundle())
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["status"], "ok")
        s = res["sections"]
        self.assertEqual(s["regions"]["items"][0]["name"], "Switzerland")
        self.assertEqual(s["queries"]["items"][0]["query"], "bitcoin price")
        self.assertEqual(len(s["rising"]["items"]), 3)
        self.assertEqual(s["topics"]["items"][0]["title"], "Bitcoin")
        self.assertEqual(f.calls_log, ["interest", "regions", "queries", "topics"])

    def test_pytrends_style_keyword_wrapping_is_unwrapped(self):
        f = make_factory(daily_df(list(range(30))), region_df(), {"bitcoin": queries_bundle()},
                         {"bitcoin": topics_bundle()})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["status"], "ok")

    # 9 ─ failed request (non-429): reported as failure, aborts the rest, NOT zeros
    def test_9_failed_request_aborts_and_is_not_zero(self):
        f = make_factory(errors={"interest": FakeHTTPError(500)})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["error"]["kind"], "http")
        self.assertEqual(f.calls_log, ["interest"])               # nothing else was sent
        self.assertEqual(res["sections"]["interest"]["state"], "failed")
        self.assertIsNone(res["sections"]["interest"]["avg7"])    # None, never 0
        self.assertEqual(res["sections"]["regions"]["state"], "skipped")

    def test_9b_network_error(self):
        class ConnectionError(Exception):
            pass
        f = make_factory(errors={"interest": ConnectionError("boom")})
        self.assertEqual(tc.fetch_live("bitcoin", backend_factory=f)["error"]["kind"], "network")

    def test_9c_unreadable_page_is_not_called_rate_limit(self):
        f = make_factory(errors={"interest": TypeError("'NoneType' object is not subscriptable")})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["error"]["kind"], "unexpected_response")

    # 10 ─ rate limit
    def test_10_rate_limit_response(self):
        f = make_factory(errors={"interest": FakeHTTPError(429, {"Retry-After": "120"})})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["error"]["kind"], "rate_limit")
        self.assertEqual(res["error"]["retry_after"], 120)
        self.assertEqual(res["sections"]["interest"]["state"], "rate_limited")
        self.assertEqual(f.calls_log, ["interest"])

    def test_rate_limit_mid_way_keeps_what_succeeded(self):
        f = make_factory(daily_df(list(range(30))), region_df(), errors={"queries": FakeHTTPError(429)})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["status"], "partial")
        self.assertEqual(res["sections"]["interest"]["state"], "ok")
        self.assertEqual(res["sections"]["regions"]["state"], "ok")
        self.assertEqual(res["sections"]["queries"]["state"], "rate_limited")
        self.assertEqual(res["sections"]["topics"]["state"], "skipped")
        self.assertEqual(f.calls_log, ["interest", "regions", "queries"])

    def test_library_missing(self):
        def factory(*a):
            raise tc.TrendsRequestError("library_missing", "not installed")
        res = tc.fetch_live("bitcoin", backend_factory=factory)
        self.assertEqual(res["error"]["kind"], "library_missing")


class TestGuard(unittest.TestCase):
    """Cache, spacing, 429 cooldown, last-good preservation, persistence, kill switch.
    Every network call is a stub; time is a fake clock."""

    def setUp(self):
        import tempfile, os
        self.t = [1_000_000.0]
        self.calls = 0
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")
        self.cfg = tc.TrendsConfig(enabled=True, min_interval_s=90, rate_limit_cooldown_s=1800,
                                   failure_cooldown_s=600, refresh_after_s=3600, stale_after_s=21600)

    def clock(self):
        return self.t[0]

    def advance(self, s):
        self.t[0] += s

    def guard(self, factory, cfg=None, state=None):
        def fetch(keyword, gprop, timeframe, geo):
            self.calls += 1
            return tc.fetch_live(keyword, gprop, timeframe, geo, backend_factory=factory)
        return tc.TrendsGuard(fetch_fn=fetch, config=cfg or self.cfg, state_path=state or self.state, clock=self.clock)

    def ok_factory(self, v=50):
        return make_factory(daily_df([v] * 30), region_df(), queries_bundle(), topics_bundle())

    # ---- success + cache + repeated clicks
    def test_success_is_stored_and_lookup_never_requests(self):
        g = self.guard(self.ok_factory())
        self.assertIsNone(g.lookup("bitcoin"))
        self.assertEqual(self.calls, 0)
        out = g.request("Bitcoin")
        self.assertEqual(out["outcome"], "fresh")
        self.assertEqual(out["data_source"], "fresh")
        self.assertEqual(out["data"]["sections"]["interest"]["avg_period"], 50.0)
        self.assertEqual(g.lookup("bitcoin ")["age_s"], 0)             # same term, other spelling
        self.assertEqual(self.calls, 1)

    def test_repeated_clicks_do_not_send_more_requests(self):
        g = self.guard(self.ok_factory())
        g.request("bitcoin")
        outs = [g.request("bitcoin") for _ in range(5)]                 # hammering the button
        self.assertTrue(all(o["outcome"] == "cached" for o in outs))
        self.assertEqual(self.calls, 1)

    def test_refresh_allowed_only_after_refresh_window(self):
        g = self.guard(self.ok_factory())
        g.request("bitcoin")
        self.advance(3599)
        self.assertEqual(g.request("bitcoin")["outcome"], "cached")
        self.advance(2)
        self.assertEqual(g.request("bitcoin")["outcome"], "fresh")
        self.assertEqual(self.calls, 2)

    def test_min_interval_spaces_requests_for_different_terms(self):
        g = self.guard(self.ok_factory())
        g.request("bitcoin")
        self.advance(30)
        out = g.request("ethereum")                                     # different term, too soon
        self.assertEqual(out["outcome"], "blocked")
        self.assertEqual(out["notice"]["reason"], "min_interval")
        self.assertEqual(self.calls, 1)
        self.advance(61)
        self.assertEqual(g.request("ethereum")["outcome"], "fresh")
        self.assertEqual(self.calls, 2)

    def test_stale_label_after_stale_window(self):
        g = self.guard(self.ok_factory())
        g.request("bitcoin")
        self.advance(self.cfg.stale_after_s + 1)
        self.assertTrue(g.lookup("bitcoin")["stale"])

    # ---- HTTP 429
    def test_429_pauses_everything_and_no_retry(self):
        g = self.guard(make_factory(errors={"interest": FakeHTTPError(429)}))
        first = g.request("bitcoin")
        self.assertEqual(first["outcome"], "failed")
        self.assertEqual(first["error"]["kind"], "rate_limit")
        self.assertEqual(self.calls, 1)
        for term in ("bitcoin", "ethereum", "solana"):                  # every later click, any term
            out = g.request(term)
            self.assertEqual(out["outcome"], "blocked")
            self.assertEqual(out["notice"]["reason"], "rate_limit")
        self.assertEqual(self.calls, 1)                                  # still exactly one request
        self.assertIsNone(g.lookup("bitcoin"))                           # no fake data stored
        self.advance(1801)
        g.request("ethereum")
        self.assertEqual(self.calls, 2)

    def test_429_tells_the_user_when_to_try_again(self):
        g = self.guard(make_factory(errors={"interest": FakeHTTPError(429)}))
        out = g.request("bitcoin")
        self.assertAlmostEqual(out["notice"]["next_allowed_epoch"], self.t[0] + 1800, delta=1)

    def test_retry_after_longer_than_default_is_respected(self):
        g = self.guard(make_factory(errors={"interest": FakeHTTPError(429, {"Retry-After": str(3 * 3600)})}))
        out = g.request("bitcoin")
        self.assertAlmostEqual(out["notice"]["next_allowed_epoch"], self.t[0] + 3 * 3600, delta=1)
        self.advance(1800 + 100)                                         # default cooldown would be over
        self.assertEqual(g.request("bitcoin")["outcome"], "blocked")

    def test_other_failure_pauses_that_term_only(self):
        g = self.guard(make_factory(errors={"interest": FakeHTTPError(500)}))
        g.request("bitcoin")
        self.advance(100)                                                # past min_interval
        out = g.request("bitcoin")
        self.assertEqual((out["outcome"], out["notice"]["reason"]), ("blocked", "failure"))
        self.assertEqual(self.calls, 1)

    # ---- good data survives failures
    def test_failed_refresh_keeps_last_good_data_not_zeros(self):
        good = self.guard(self.ok_factory(55))
        good.request("bitcoin")
        self.advance(3601)
        bad_factory = make_factory(errors={"interest": FakeHTTPError(429)})
        g = tc.TrendsGuard(fetch_fn=lambda k, gp, tf, geo: tc.fetch_live(k, gp, tf, geo, backend_factory=bad_factory),
                           config=self.cfg, state_path=self.state, clock=self.clock)   # same state file
        out = g.request("bitcoin")
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["data_source"], "cached")
        self.assertEqual(out["data"]["sections"]["interest"]["avg_period"], 55.0)       # kept
        self.assertEqual(g.lookup("bitcoin")["result"]["sections"]["interest"]["avg_period"], 55.0)

    def test_partial_result_never_overwrites_a_complete_one(self):
        g = self.guard(self.ok_factory(60))
        g.request("bitcoin")
        self.advance(3601)
        g2 = self.guard(make_factory(daily_df([10] * 30), region_df(), errors={"queries": FakeHTTPError(500)}))
        out = g2.request("bitcoin")
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(g2.lookup("bitcoin")["result"]["sections"]["interest"]["avg_period"], 60.0)

    def test_partial_result_is_kept_when_nothing_better_exists(self):
        g = self.guard(make_factory(daily_df([40] * 30), region_df(), errors={"queries": FakeHTTPError(500)}))
        out = g.request("bitcoin")
        self.assertEqual(out["outcome"], "partial")
        self.assertEqual(g.lookup("bitcoin")["result"]["sections"]["interest"]["state"], "ok")
        self.assertEqual(g.lookup("bitcoin")["result"]["sections"]["queries"]["state"], "failed")

    # ---- persistence across "restarts"
    def test_cache_survives_restart(self):
        self.guard(self.ok_factory(70)).request("bitcoin")
        g2 = self.guard(self.ok_factory(1))                              # brand-new guard, same state file
        view = g2.lookup("bitcoin")
        self.assertIsNotNone(view)
        self.assertEqual(view["result"]["sections"]["interest"]["avg_period"], 70.0)
        self.assertEqual(g2.request("bitcoin")["outcome"], "cached")
        self.assertEqual(self.calls, 1)

    def test_429_cooldown_survives_restart(self):
        self.guard(make_factory(errors={"interest": FakeHTTPError(429)})).request("bitcoin")
        g2 = self.guard(self.ok_factory())
        out = g2.request("ethereum")
        self.assertEqual((out["outcome"], out["notice"]["reason"]), ("blocked", "rate_limit"))
        self.assertEqual(self.calls, 1)

    def test_spacing_survives_restart(self):
        self.guard(self.ok_factory()).request("bitcoin")
        g2 = self.guard(self.ok_factory())
        self.advance(10)
        self.assertEqual(g2.request("ethereum")["outcome"], "blocked")

    def test_corrupt_state_file_does_not_crash(self):
        with open(self.state, "w") as fh:
            fh.write("{not json")
        g = self.guard(self.ok_factory())
        self.assertEqual(g.request("bitcoin")["outcome"], "fresh")

    def test_unwritable_state_falls_back_to_memory(self):
        g = self.guard(self.ok_factory(), state="/proc/definitely/not/writable.json")
        self.assertFalse(g.persistent)
        self.assertEqual(g.request("bitcoin")["outcome"], "fresh")

    def test_state_file_contains_no_secrets_only_expected_keys(self):
        import json
        self.guard(self.ok_factory()).request("bitcoin")
        with open(self.state) as fh:
            data = json.load(fh)
        self.assertEqual(set(data), {"version", "global_block_until", "last_live", "key_block", "cache"})
        txt = json.dumps(data).lower()
        for bad in ("api_key", "token", "cookie", "secret", "password"):
            self.assertNotIn(bad, txt)

    # ---- emergency switch
    def test_kill_switch_sends_nothing_but_shows_stored_data(self):
        self.guard(self.ok_factory(42)).request("bitcoin")
        off = tc.TrendsConfig(**{**self.cfg.__dict__, "enabled": False})
        g = self.guard(self.ok_factory(), cfg=off)
        out = g.request("bitcoin")
        self.assertEqual(out["outcome"], "disabled")
        self.assertEqual(out["data"]["sections"]["interest"]["avg_period"], 42.0)
        self.assertEqual(self.calls, 1)

    def test_config_from_env(self):
        import os
        os.environ.update({"TRENDS_ENABLED": "false", "TRENDS_MIN_INTERVAL_S": "5", "TRENDS_RATE_LIMIT_COOLDOWN_S": "junk"})
        try:
            c = tc.TrendsConfig.from_env()
            self.assertFalse(c.enabled)
            self.assertEqual(c.min_interval_s, 5.0)
            self.assertEqual(c.rate_limit_cooldown_s, 1800.0)            # bad value -> default
        finally:
            for k in ("TRENDS_ENABLED", "TRENDS_MIN_INTERVAL_S", "TRENDS_RATE_LIMIT_COOLDOWN_S"):
                os.environ.pop(k, None)


class TestUnreadableSection(unittest.TestCase):
    """A library parse failure on ONE answered response (pytrends IndexError on related topics) is a section
    problem, not a request failure: no cooldown, pass counts as complete, other sections kept."""

    def setUp(self):
        import tempfile, os
        self.t = [5_000_000.0]
        self.calls = 0
        self.state = os.path.join(tempfile.mkdtemp(), "state.json")
        self.cfg = tc.TrendsConfig(min_interval_s=90, rate_limit_cooldown_s=1800, failure_cooldown_s=600, refresh_after_s=3600)

    def guard(self, factory):
        def fetch(keyword, gprop, timeframe, geo):
            self.calls += 1
            return tc.fetch_live(keyword, gprop, timeframe, geo, backend_factory=factory)
        return tc.TrendsGuard(fetch_fn=fetch, config=self.cfg, state_path=self.state, clock=lambda: self.t[0])

    def topics_index_error_factory(self):
        return make_factory(daily_df(list(range(40, 70))), region_df(), queries_bundle(), topics_bundle(),
                            errors={"topics": IndexError("list index out of range")})

    def test_topics_indexerror_is_a_section_problem_not_a_request_failure(self):
        f = self.topics_index_error_factory()
        res = tc.fetch_live("bitcoin", backend_factory=f)
        s = res["sections"]
        self.assertEqual(res["status"], "ok")
        self.assertIsNone(res["error"])                                   # no request-level error recorded
        self.assertEqual((s["interest"]["state"], s["regions"]["state"], s["queries"]["state"], s["rising"]["state"]),
                         ("ok", "ok", "ok", "ok"))
        self.assertEqual(s["topics"]["state"], "malformed")
        self.assertIn("not a block", s["topics"]["message"])
        self.assertIn("IndexError", s["topics"]["message"])
        self.assertEqual(res["calls_attempted"], 4)
        self.assertEqual(f.calls_log, ["interest", "regions", "queries", "topics"])

    def test_no_cooldown_and_result_is_cached_for_the_refresh_window(self):
        g = self.guard(self.topics_index_error_factory())
        out = g.request("bitcoin")
        self.assertEqual(out["outcome"], "fresh")
        self.assertEqual(g.status("ethereum")["reason"], "min_interval")          # only the normal spacing applies
        self.t[0] += 100
        self.assertTrue(g.status("ethereum")["can_request"])                        # no term/global pause was set
        again = g.request("bitcoin")
        self.assertEqual(again["outcome"], "cached")                                # served from cache, no new pass
        self.assertEqual(self.calls, 1)

    def test_state_file_has_no_failure_pause_after_unreadable_section(self):
        import json
        self.guard(self.topics_index_error_factory()).request("bitcoin")
        with open(self.state) as fh:
            data = json.load(fh)
        self.assertEqual(data["key_block"], {})
        self.assertEqual(data["global_block_until"], 0)

    def test_unreadable_in_the_middle_stops_the_pass_without_a_request_error(self):
        f = make_factory(daily_df(list(range(30))), region_df(), queries_bundle(), topics_bundle(),
                         errors={"regions": IndexError("boom")})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertIsNone(res["error"])
        self.assertEqual(res["sections"]["regions"]["state"], "malformed")
        self.assertEqual(res["sections"]["queries"]["state"], "skipped")
        self.assertIn("pass stopped", res["sections"]["queries"]["message"])
        self.assertEqual(f.calls_log, ["interest", "regions"])                      # nothing sent after it
        self.assertEqual(res["status"], "ok")

    def test_parse_error_on_the_FIRST_call_is_still_a_request_level_failure(self):
        f = make_factory(errors={"interest": IndexError("list index out of range")})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual(res["error"]["kind"], "unexpected_response")
        self.assertEqual(res["status"], "error")
        self.assertEqual(f.calls_log, ["interest"])
        g = self.guard(make_factory(errors={"interest": IndexError("x")}))
        g.request("bitcoin")
        self.t[0] += 100
        self.assertEqual(g.status("bitcoin")["reason"], "failure")                  # that one DOES pause the term

    def test_real_http_errors_after_good_sections_are_unchanged(self):
        f = make_factory(daily_df(list(range(30))), region_df(), errors={"queries": FakeHTTPError(429)})
        res = tc.fetch_live("bitcoin", backend_factory=f)
        self.assertEqual((res["status"], res["error"]["kind"]), ("partial", "rate_limit"))
        g = self.guard(make_factory(daily_df(list(range(30))), region_df(), errors={"queries": FakeHTTPError(500)}))
        out = g.request("bitcoin")
        self.assertEqual(out["outcome"], "partial")
        self.t[0] += 100
        self.assertEqual(g.status("bitcoin")["reason"], "failure")

    def test_default_pytrends_path_topics_indexerror_end_to_end(self):
        from unittest.mock import patch
        mods, log = TestBackendSelection.fakes()
        real = mods["pytrends.request"].TrendReq

        def bad_topics(self):
            log["calls"].append("related_topics")
            raise IndexError("list index out of range")
        real.related_topics = bad_topics
        with patch.dict("sys.modules", mods):
            res = tc.fetch_live("bitcoin")                       # real default path, fake library, no network
        self.assertEqual((res["backend"], res["status"]), ("pytrends", "ok"))
        self.assertIsNone(res["error"])
        self.assertEqual(res["sections"]["interest"]["avg_period"], 54.5)
        self.assertEqual(res["sections"]["regions"]["state"], "ok")
        self.assertEqual(res["sections"]["queries"]["state"], "ok")
        self.assertEqual(res["sections"]["topics"]["state"], "malformed")
        self.assertEqual(log["calls"].count("related_topics"), 1)                   # one attempt, no retry


class TestBackendSelection(unittest.TestCase):
    """pytrends must be tried first, trendspy only if pytrends cannot be imported.
    Both libraries are replaced by in-memory fakes: constructing the REAL TrendReq would send a request."""

    @staticmethod
    def fakes(with_pytrends=True, with_trendspy=True):
        import sys
        import types
        log = {"pytrends_init": 0, "trendspy_init": 0, "calls": []}
        mods = {}

        class FakeTrendReq:
            def __init__(self, *a, **k):
                log["pytrends_init"] += 1

            def build_payload(self, kw_list, **k):
                log["calls"].append(("build_payload", tuple(kw_list)))

            def interest_over_time(self):
                log["calls"].append("interest_over_time")
                return daily_df(list(range(40, 70)), keyword="bitcoin")

            def interest_by_region(self, **k):
                log["calls"].append("interest_by_region")
                return region_df().set_index("geoName").drop(columns="geoCode")        # pytrends shape

            def related_queries(self):
                log["calls"].append("related_queries")
                return {"bitcoin": queries_bundle()}                                    # pytrends shape

            def related_topics(self):
                log["calls"].append("related_topics")
                t = topics_bundle()
                t["top"] = t["top"].rename(columns={"title": "topic_title", "type": "topic_type"})
                return {"bitcoin": t}

        class FakeTrends:
            def __init__(self, *a, **k):
                log["trendspy_init"] += 1

        if with_pytrends:
            pkg, req = types.ModuleType("pytrends"), types.ModuleType("pytrends.request")
            req.TrendReq = FakeTrendReq
            mods.update({"pytrends": pkg, "pytrends.request": req})
        else:
            mods.update({"pytrends": None, "pytrends.request": None})            # None => ImportError on import
        if with_trendspy:
            ts = types.ModuleType("trendspy")
            ts.Trends = FakeTrends
            mods["trendspy"] = ts
        else:
            mods["trendspy"] = None
        return mods, log

    def test_default_order_prefers_pytrends(self):
        self.assertEqual(tc.BACKEND_ORDER, ("pytrends", "trendspy"))

    def test_both_installed_uses_pytrends_and_never_builds_trendspy(self):
        from unittest.mock import patch
        mods, log = self.fakes()
        with patch.dict("sys.modules", mods):
            name, calls = tc._load_backend("bitcoin", "", tc.TIMEFRAME, "")
        self.assertEqual(name, "pytrends")
        self.assertEqual([c[0] for c in calls], ["interest", "regions", "queries", "topics"])
        self.assertEqual((log["pytrends_init"], log["trendspy_init"]), (1, 0))
        self.assertEqual(log["calls"], [])                      # nothing was requested just by selecting it

    def test_falls_back_to_trendspy_only_when_pytrends_missing(self):
        from unittest.mock import patch
        mods, log = self.fakes(with_pytrends=False)
        with patch.dict("sys.modules", mods):
            name, calls = tc._load_backend("bitcoin", "", tc.TIMEFRAME, "")
        self.assertEqual(name, "trendspy")
        self.assertEqual([c[0] for c in calls], ["interest", "regions", "queries", "topics"])

    def test_neither_installed_reports_library_missing(self):
        from unittest.mock import patch
        mods, _ = self.fakes(with_pytrends=False, with_trendspy=False)
        with patch.dict("sys.modules", mods):
            res = tc.fetch_live("bitcoin")
        self.assertEqual(res["error"]["kind"], "library_missing")
        self.assertEqual(res["calls_attempted"], 0)

    def test_default_fetch_live_end_to_end_uses_pytrends_and_keeps_accounting(self):
        from unittest.mock import patch
        mods, log = self.fakes()
        with patch.dict("sys.modules", mods):
            res = tc.fetch_live("bitcoin")                      # real default path, fake library, no network
        self.assertEqual(res["backend"], "pytrends")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["calls_attempted"], 4)
        s = res["sections"]
        self.assertEqual((s["interest"]["avg7"], s["interest"]["avg_period"]), (66.0, 54.5))
        self.assertEqual(s["regions"]["items"][0]["name"], "Switzerland")
        self.assertEqual(s["queries"]["items"][0]["query"], "bitcoin price")
        self.assertEqual(s["topics"]["items"][0]["title"], "Bitcoin")
        self.assertEqual(sum(1 for c in log["calls"] if isinstance(c, tuple) and c[0] == "build_payload"), 1)  # one explore
        self.assertEqual(log["trendspy_init"], 0)

    def test_pytrends_failure_does_not_switch_client_or_retry(self):
        from unittest.mock import patch
        mods, log = self.fakes()
        real = mods["pytrends.request"].TrendReq

        def boom(self):
            log["calls"].append("interest_over_time")
            raise FakeHTTPError(429)
        real.interest_over_time = boom
        with patch.dict("sys.modules", mods):
            res = tc.fetch_live("bitcoin")
        self.assertEqual(res["error"]["kind"], "rate_limit")
        self.assertEqual(log["calls"].count("interest_over_time"), 1)           # one attempt only
        self.assertEqual(log["trendspy_init"], 0)                                # no second client
        self.assertEqual(res["calls_attempted"], 1)

    def test_real_pytrends_exception_types_are_classified(self):
        try:
            from pytrends import exceptions as pex
        except ImportError:
            self.skipTest("pytrends not installed")
        resp = type("R", (), {"status_code": 429, "headers": {"Retry-After": "300"}})()
        e = tc.classify_exception(pex.TooManyRequestsError.from_response(resp))
        self.assertEqual((e.kind, e.retry_after, e.status), ("rate_limit", 300, 429))
        resp500 = type("R", (), {"status_code": 500, "headers": {}})()
        self.assertEqual(tc.classify_exception(pex.ResponseError.from_response(resp500)).kind, "http")


if __name__ == "__main__":
    unittest.main(verbosity=2)
