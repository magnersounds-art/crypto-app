"""
trends_core.py — the ONE route to Google Trends for the whole app (no Streamlit imports).

Every scanner page calls TrendsGuard.request() — nothing else may talk to Google.  The guard:
  * sends a request ONLY when a user deliberately presses LOAD / REFRESH
  * enforces a minimum spacing between live requests (TRENDS_MIN_INTERVAL_S, default 90 s)
  * serves cached data instead of requesting if the stored result is recent (TRENDS_REFRESH_AFTER_S, default 1 h)
  * after HTTP 429: pauses ALL live requests for max(Retry-After, TRENDS_RATE_LIMIT_COOLDOWN_S, default 30 min)
  * after any other failure: pauses that search term (TRENDS_FAILURE_COOLDOWN_S, default 10 min)
  * keeps the last good result when a new request fails (a failure never writes zeros over good data)
  * saves cache + pauses to a small JSON file (TRENDS_STATE_PATH, default .trends_state.json next to this
    file) so they survive Streamlit reruns and process restarts; the file holds only Trends results and
    timestamps — no keys, cookies or credentials
  * TRENDS_ENABLED=false is an emergency stop only (live requests off, stored data still shown); default is on
The library (pytrends, or trendspy only if pytrends is not installed) is chosen once per request; there is no
second client that retries after a failure.  Both are UNOFFICIAL: Google has no public Trends API, publishes no
rate limit, and may refuse automated requests at any time.

History: the old get_trends() swallowed every exception into all-zero data, cached those zeros for an hour,
and fired ~10 Google calls on every coin scan (Streamlit runs every tab on every rerun).
"""

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TIMEFRAME = "today 1-m"          # daily resolution; 7d + 30d averages both come from this ONE series
CACHE_TTL = 6 * 3600
RATE_LIMIT_COOLDOWN = 30 * 60
FAILURE_COOLDOWN = 10 * 60
MAX_RETRY_AFTER = 6 * 3600
SECTIONS = ("interest", "regions", "queries", "topics")
TOP_N = 20


class MalformedResponse(Exception):
    """The source answered, but not in the shape we expect."""


class TrendsRequestError(Exception):
    def __init__(self, kind, message, retry_after=None, status=None):
        super().__init__(message)
        self.kind, self.message, self.retry_after, self.status = kind, message, retry_after, status

    def as_dict(self):
        return {"kind": self.kind, "message": self.message,
                "retry_after": self.retry_after, "status": self.status}


# ── error classification ──────────────────────────────────────────────────────
def _retry_after_seconds(resp):
    try:
        raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
        if raw is None:
            return None
        return max(0, min(int(float(raw)), MAX_RETRY_AFTER))
    except Exception:
        return None


def classify_exception(exc):
    """Turn any library exception into a TrendsRequestError with an honest `kind`:
    rate_limit | http | network | unexpected_response | library_missing"""
    if isinstance(exc, TrendsRequestError):
        return exc
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if status == 429 or "429" in low or "too many requests" in low or "toomanyrequests" in name.lower() \
            or "quota" in name.lower():
        return TrendsRequestError("rate_limit", "Google returned HTTP 429 / quota exceeded (rate limit)",
                                  _retry_after_seconds(resp), status or 429)
    if status is not None:
        return TrendsRequestError("http", f"Google returned HTTP {status}", _retry_after_seconds(resp), status)
    if name in ("ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "SSLError", "ProxyError") \
            or "connection" in low or "timed out" in low:
        return TrendsRequestError("network", f"Network problem reaching Google ({name})")
    if isinstance(exc, (TypeError, KeyError, IndexError, AttributeError, ValueError)):
        return TrendsRequestError(
            "unexpected_response",
            f"Google answered with something the library could not read ({name}: {text[:80]}) — "
            "this can be a block / consent page (if it happens on the first call) or a parsing limitation of the library")
    return TrendsRequestError("http", f"{name}: {text[:120]}")


# ── pure parsing (works on pandas DataFrames or anything with the same duck-typing) ──
def _is_empty(df):
    return df is None or bool(getattr(df, "empty", False))


def _col_names(df):
    return [str(c) for c in list(df.columns)]


def compute_averages(points, short_days=7):
    """points: list of (timestamp|None, value, is_partial).
    * partial (still-in-progress) observations are excluded
    * avg_period = mean of ALL complete observations in the requested window (~30 days)
    * avg7       = mean of complete observations within `short_days` of the last complete one
                   (needs >= 3 points, otherwise None — never a made-up 0)
    Returns dict(avg7, avg_period, n_period, n7, last_value)."""
    complete = [(t, float(v)) for t, v, p in points if not p]
    if not complete:
        return {"avg7": None, "avg_period": None, "n_period": 0, "n7": 0, "last_value": None}
    vals = [v for _, v in complete]
    last_t = complete[-1][0]
    if last_t is not None and all(t is not None for t, _ in complete):
        recent = [v for t, v in complete if (last_t - t).total_seconds() < short_days * 86400]
    else:
        recent = vals[-short_days:]
    avg7 = round(sum(recent) / len(recent), 1) if len(recent) >= 3 else None
    return {"avg7": avg7, "avg_period": round(sum(vals) / len(vals), 1),
            "n_period": len(vals), "n7": len(recent), "last_value": vals[-1]}


def parse_interest(df, keyword):
    if _is_empty(df):
        return {"state": "insufficient", "avg7": None, "avg_period": None, "n_period": 0, "n7": 0,
                "last_value": None,
                "message": "Google returned no time series (too little search volume for this term)."}
    cols = _col_names(df)
    col = keyword if keyword in cols else next((c for c in cols if c != "isPartial"), None)
    if col is None:
        raise MalformedResponse("no value column in time series")
    pts = []
    for ts, row in df.iterrows():
        v = row[col]
        if v is None or v != v:
            continue
        partial = bool(row["isPartial"]) if "isPartial" in cols else False
        pts.append((ts if hasattr(ts, "total_seconds") or hasattr(ts, "year") else None, float(v), partial))
    out = compute_averages(pts)
    if out["n_period"] == 0:
        return {"state": "insufficient", **out, "message": "Only incomplete observations were returned."}
    return {"state": "ok", **out, "message": ""}


def parse_regions(df, keyword):
    if _is_empty(df):
        return {"state": "empty", "items": [], "total": 0,
                "message": "Google returned no regional breakdown for this term."}
    cols = _col_names(df)
    work = df.reset_index() if "geoName" not in cols else df
    wcols = _col_names(work)
    if "geoName" not in wcols:
        raise MalformedResponse("no geoName in regional data")
    vcol = keyword if keyword in wcols else next(
        (c for c in reversed(wcols) if c not in ("geoName", "geoCode", "lat", "lng", "isPartial")), None)
    if vcol is None:
        raise MalformedResponse("no value column in regional data")
    items = []
    for _, row in work.iterrows():
        v = row[vcol]
        if v is None or v != v:
            continue
        v = float(v)
        if v > 0:
            items.append({"name": str(row["geoName"]), "value": int(round(v))})
    items.sort(key=lambda x: -x["value"])
    return {"state": "ok" if items else "empty", "items": items[:TOP_N], "total": len(items),
            "message": "" if items else "Regional table came back with no regions above 0."}


def _parse_breakout(v):
    """-> (value_or_None, is_breakout)"""
    if isinstance(v, str):
        if v.strip().lower() == "breakout":
            return None, True
        try:
            v = float(v.replace("+", "").replace("%", "").replace(",", ""))
        except Exception:
            raise MalformedResponse(f"unreadable rising value {v!r}")
    if v is None or v != v:
        raise MalformedResponse("missing value")
    v = int(round(float(v)))
    return v, v >= 5000


def _rows(df):
    return [] if _is_empty(df) else [r for _, r in df.iterrows()]


def parse_queries(bundle):
    """bundle: {'top': df, 'rising': df} -> (top_section, rising_section)"""
    if bundle is None or not hasattr(bundle, "get"):
        raise MalformedResponse("related queries is not a dict")
    top, rising = [], []
    for r in _rows(bundle.get("top")):
        if "query" not in r.index or "value" not in r.index:
            raise MalformedResponse("top queries missing query/value columns")
        top.append({"query": str(r["query"]), "value": int(round(float(r["value"])))})
    for r in _rows(bundle.get("rising")):
        if "query" not in r.index or "value" not in r.index:
            raise MalformedResponse("rising queries missing query/value columns")
        v, b = _parse_breakout(r["value"])
        rising.append({"query": str(r["query"]), "value": v, "breakout": b})
    msg = "Google returned none for this term (valid empty result)."
    return (
        {"state": "ok" if top else "empty", "items": top[:TOP_N], "total": len(top), "message": "" if top else msg},
        {"state": "ok" if rising else "empty", "items": rising[:TOP_N], "total": len(rising), "message": "" if rising else msg},
    )


def parse_topics(bundle):
    if bundle is None or not hasattr(bundle, "get"):
        raise MalformedResponse("related topics is not a dict")
    items = []
    for r in _rows(bundle.get("top")):
        idx = list(r.index)
        tcol = "title" if "title" in idx else ("topic_title" if "topic_title" in idx else None)
        if tcol is None or "value" not in idx:
            raise MalformedResponse("topics missing title/value columns")
        ttype = r["type"] if "type" in idx else (r["topic_type"] if "topic_type" in idx else "")
        items.append({"title": str(r[tcol]), "value": int(round(float(r["value"]))), "type": str(ttype or "")})
    return {"state": "ok" if items else "empty", "items": items[:TOP_N], "total": len(items),
            "message": "" if items else "Google returned no related topics for this term (valid empty result)."}


def _unwrap_keyword(d, keyword):
    """pytrends returns {keyword: {...}}; trendspy returns {...} directly."""
    if isinstance(d, dict) and keyword in d and isinstance(d[keyword], dict):
        return d[keyword]
    return d


# ── fetch: one pass, abort on first request error, no retries ─────────────────
def _sections_blank(state="skipped", message=""):
    return {
        "interest": {"state": state, "avg7": None, "avg_period": None, "n_period": 0, "n7": 0,
                     "last_value": None, "message": message},
        "regions": {"state": state, "items": [], "total": 0, "message": message},
        "queries": {"state": state, "items": [], "total": 0, "message": message},
        "rising": {"state": state, "items": [], "total": 0, "message": message},
        "topics": {"state": state, "items": [], "total": 0, "message": message},
    }


# Backend preference. pytrends first: on 24 Oct 2026 a direct pytrends request succeeded from the project's
# machine while the same machine got HTTP 429 from trendspy.  trendspy is only used if pytrends cannot be
# imported.  A failed REQUEST never switches client — there is exactly one client per pass, no cross-client retry.
BACKEND_ORDER = ("pytrends", "trendspy")


def _build_trendspy(keyword, gprop, timeframe, geo):
    """max_retries=1 means exactly ONE attempt per HTTP request. Raises ImportError if not installed."""
    from trendspy import Trends
    tr = Trends(request_delay=2.0, max_retries=1)
    res = "COUNTRY" if not geo else None
    return "trendspy", [
        ("interest", lambda: tr.interest_over_time(keyword, timeframe=timeframe, geo=geo, gprop=gprop)),
        ("regions", lambda: tr.interest_by_region(keyword, timeframe=timeframe, geo=geo, gprop=gprop,
                                                  resolution=res, inc_low_vol=True)),
        ("queries", lambda: tr.related_queries(keyword, timeframe=timeframe, geo=geo, gprop=gprop)),
        ("topics", lambda: tr.related_topics(keyword, timeframe=timeframe, geo=geo, gprop=gprop)),
    ]


def _build_pytrends(keyword, gprop, timeframe, geo):
    """Raises ImportError if not installed."""
    from pytrends.request import TrendReq
    pt = TrendReq(hl="en-US", tz=0, timeout=(5, 25))     # retries left at default 0
    state = {"ready": False}

    def _payload():
        if not state["ready"]:
            pt.build_payload([keyword], timeframe=timeframe, geo=geo, gprop=gprop)
            state["ready"] = True

    def _wrap(fn):
        def run():
            _payload()
            return fn()
        return run
    return "pytrends", [
        ("interest", _wrap(lambda: pt.interest_over_time())),
        ("regions", _wrap(lambda: pt.interest_by_region(resolution="COUNTRY", inc_low_vol=True))),
        ("queries", _wrap(lambda: pt.related_queries())),
        ("topics", _wrap(lambda: pt.related_topics())),
    ]


_BUILDERS = {"pytrends": _build_pytrends, "trendspy": _build_trendspy}


def _load_backend(keyword, gprop, timeframe, geo):
    """First importable client in BACKEND_ORDER. Returns (backend_name, list_of_(section, callable))."""
    for name in BACKEND_ORDER:
        try:
            return _BUILDERS[name](keyword, gprop, timeframe, geo)
        except ImportError:
            continue
    raise TrendsRequestError("library_missing",
                             "Neither pytrends nor trendspy is installed — run: pip install pytrends trendspy")


def fetch_live(keyword, gprop="", timeframe=TIMEFRAME, geo="", backend_factory=None):
    """ONE pass over the four sections. Returns a result dict; never raises, never fakes zeros."""
    keyword = keyword.strip()
    result = {"keyword": keyword, "gprop": gprop, "timeframe": timeframe, "geo": geo,
              "fetched_at": datetime.now(timezone.utc), "backend": "", "status": "error",
              "error": None, "sections": _sections_blank("skipped", "Not requested."),
              "from_cache": False, "blocked": False, "calls_attempted": 0}
    try:
        backend, calls = (backend_factory or _load_backend)(keyword, gprop, timeframe, geo)
    except TrendsRequestError as e:
        result["error"] = e.as_dict()
        return result
    except Exception as e:
        result["error"] = classify_exception(e).as_dict()
        return result
    result["backend"] = backend

    raw, req_err = {}, None
    any_ok, stopped = False, False
    for name, fn in calls:
        if req_err is not None or stopped:
            raw[name] = ("skipped", None)
            continue
        result["calls_attempted"] += 1
        try:
            raw[name] = ("ok", fn())
            any_ok = True
        except Exception as e:
            err = classify_exception(e)
            if err.kind == "unexpected_response" and any_ok:
                # Google already answered an earlier call in this pass, so this is the library failing to read
                # ONE response (e.g. pytrends IndexError on related topics), not a block.  Record it against
                # that section only — no request failure, no cooldown — but still send nothing further.
                raw[name] = ("unreadable",
                             f"{backend} could not read Google's reply for this section ({type(e).__name__}: "
                             f"{str(e)[:60]}). Google did answer the request, so this is a library parsing "
                             "limitation, not a block.")
                stopped = True
            else:                                    # a REQUEST failed: stop sending anything else
                req_err = err
                raw[name] = ("error", err)

    secs = result["sections"]
    err_state = "rate_limited" if (req_err and req_err.kind == "rate_limit") else "failed"
    skip_msg = ("Not requested because an earlier request failed." if req_err
                else "Not requested: an earlier section could not be read, so the pass stopped there.")

    def _apply(name, parser, *args, targets=None):
        tag, payload = raw.get(name, ("skipped", None))
        targets = targets or (name,)
        if tag == "ok":
            try:
                parsed = parser(payload, *args)
                parsed = (parsed,) if isinstance(parsed, dict) else parsed
                for t, p in zip(targets, parsed):
                    secs[t] = p
            except (MalformedResponse, KeyError, TypeError, ValueError, AttributeError, IndexError) as e:
                for t in targets:
                    secs[t] = {**_sections_blank("malformed")[t], "message": f"Could not read Google's response: {e}"}
        elif tag == "error":
            for t in targets:
                secs[t] = {**_sections_blank(err_state)[t], "message": payload.message}
        elif tag == "unreadable":
            for t in targets:
                secs[t] = {**_sections_blank("malformed")[t], "message": payload}
        else:
            for t in targets:
                secs[t] = {**_sections_blank("skipped")[t], "message": skip_msg}

    _apply("interest", parse_interest, keyword)
    _apply("regions", parse_regions, keyword)
    if raw.get("queries", ("skipped",))[0] == "ok":
        raw["queries"] = ("ok", _unwrap_keyword(raw["queries"][1], keyword))
    if raw.get("topics", ("skipped",))[0] == "ok":
        raw["topics"] = ("ok", _unwrap_keyword(raw["topics"][1], keyword))
    _apply("queries", parse_queries, targets=("queries", "rising"))
    _apply("topics", parse_topics)

    result["error"] = req_err.as_dict() if req_err else None
    bad = {"failed", "rate_limited", "skipped", "malformed"}
    if secs["interest"]["state"] in bad:
        result["status"] = "error"
    elif req_err is not None:
        result["status"] = "partial"          # a request failed part-way: retry later is meaningful
    else:
        # Interest is good and Google answered every request we sent. A section the library could not read
        # (malformed / stopped) is shown as such, but asking again would not change it, so the pass counts as
        # complete: it is cached and the normal refresh window applies.
        result["status"] = "ok"
    return result


# ── configuration ─────────────────────────────────────────────────────────────
def _env_float(name, default):
    try:
        v = float(os.getenv(name, ""))
        return v if v >= 0 else default
    except ValueError:
        return default


def _env_flag(name, default=True):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "disabled")


@dataclass
class TrendsConfig:
    enabled: bool = True                      # emergency stop only; default ON
    min_interval_s: float = 90.0              # minimum gap between ANY two live passes
    rate_limit_cooldown_s: float = 1800.0     # pause after HTTP 429 (Retry-After wins if longer)
    failure_cooldown_s: float = 600.0         # pause for one term after another kind of failure
    refresh_after_s: float = 3600.0           # a refresh press within this age of a good result is served from cache
    stale_after_s: float = 21600.0            # older than this the UI labels the data OLD
    max_entries: int = 30

    @classmethod
    def from_env(cls):
        d = cls()
        return cls(
            enabled=_env_flag("TRENDS_ENABLED", d.enabled),
            min_interval_s=_env_float("TRENDS_MIN_INTERVAL_S", d.min_interval_s),
            rate_limit_cooldown_s=_env_float("TRENDS_RATE_LIMIT_COOLDOWN_S", d.rate_limit_cooldown_s),
            failure_cooldown_s=_env_float("TRENDS_FAILURE_COOLDOWN_S", d.failure_cooldown_s),
            refresh_after_s=_env_float("TRENDS_REFRESH_AFTER_S", d.refresh_after_s),
            stale_after_s=_env_float("TRENDS_STALE_AFTER_S", d.stale_after_s),
        )


# ── tiny JSON state file (cache + pauses) ─────────────────────────────────────
def _candidate_paths():
    env = os.getenv("TRENDS_STATE_PATH")
    out = [Path(env)] if env else []
    out.append(Path(__file__).resolve().with_name(".trends_state.json"))
    out.append(Path(tempfile.gettempdir()) / "crypto_app_trends_state.json")
    return out


class _StateStore:
    def __init__(self, path=None):
        self.path, self.persistent = None, False
        for p in ([Path(path)] if path else _candidate_paths()):
            try:
                ok = os.access(p, os.W_OK) if p.exists() else os.access(p.parent, os.W_OK)
            except Exception:
                ok = False
            if ok:
                self.path, self.persistent = p, True
                break

    def load(self):
        if not self.path or not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}                                   # corrupt file: start clean, never crash the page

    def save(self, data):
        if not self.path:
            return False
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.path)
            return True
        except Exception:
            self.persistent = False                     # fall back to memory-only, keep running
            return False


def _ser_result(res):
    r = dict(res)
    fa = r.get("fetched_at")
    r["fetched_at"] = fa.isoformat() if hasattr(fa, "isoformat") else fa
    return r


def _de_result(r):
    r = dict(r)
    fa = r.get("fetched_at")
    if isinstance(fa, str):
        try:
            r["fetched_at"] = datetime.fromisoformat(fa)
        except Exception:
            r["fetched_at"] = datetime.now(timezone.utc)
    return r


# ── the guard: the only place that may send a live request ────────────────────
class TrendsGuard:
    def __init__(self, fetch_fn=None, config=None, state_path=None, clock=time.time):
        self._fetch = fetch_fn or fetch_live
        self.cfg = config or TrendsConfig.from_env()
        self._now = clock
        self._store = _StateStore(state_path)
        self._lock = threading.Lock()
        self.live_calls = 0
        self._global_block_until = 0.0
        self._last_live = 0.0
        self._key_block = {}
        self._cache = {}
        self._load()

    # -- helpers
    @staticmethod
    def key(keyword, gprop="", timeframe=TIMEFRAME, geo=""):
        return (keyword.strip().lower(), gprop, timeframe, geo)

    @classmethod
    def _ks(cls, keyword, gprop, timeframe, geo):
        return json.dumps(list(cls.key(keyword, gprop, timeframe, geo)))

    @property
    def persistent(self):
        return self._store.persistent

    def _load(self):
        data = self._store.load()
        now = self._now()
        try:
            self._global_block_until = float(data.get("global_block_until", 0) or 0)
            self._last_live = float(data.get("last_live", 0) or 0)
            self._key_block = {k: float(v) for k, v in (data.get("key_block") or {}).items() if float(v) > now}
            for k, e in (data.get("cache") or {}).items():
                self._cache[k] = {"t": float(e["t"]), "result": _de_result(e["result"])}
        except Exception:
            self._global_block_until, self._last_live, self._key_block, self._cache = 0.0, 0.0, {}, {}

    def _persist(self):
        now = self._now()
        keep = sorted(self._cache.items(), key=lambda kv: -kv[1]["t"])[: self.cfg.max_entries]
        self._cache = dict(keep)
        self._store.save({
            "version": 1,
            "global_block_until": self._global_block_until if self._global_block_until > now else 0,
            "last_live": self._last_live,
            "key_block": {k: v for k, v in self._key_block.items() if v > now},
            "cache": {k: {"t": e["t"], "result": _ser_result(e["result"])} for k, e in self._cache.items()},
        })

    def _view(self, e):
        if not e:
            return None
        age = max(0.0, self._now() - e["t"])
        return {"result": {**e["result"], "from_cache": True}, "fetched_epoch": e["t"],
                "age_s": int(age), "stale": age >= self.cfg.stale_after_s}

    def _status_locked(self, ks):
        now, cfg = self._now(), self.cfg
        if not cfg.enabled:
            return {"enabled": False, "can_request": False, "reason": "disabled", "next_allowed_epoch": None,
                    "persistent": self.persistent}
        e = self._cache.get(ks)
        waits = []                                            # in priority order
        if now < self._global_block_until:
            waits.append(("rate_limit", self._global_block_until))
        if now < self._key_block.get(ks, 0):
            waits.append(("failure", self._key_block[ks]))
        if e and e["result"].get("status") == "ok" and now < e["t"] + cfg.refresh_after_s:
            waits.append(("recent_cache", e["t"] + cfg.refresh_after_s))
        if now < self._last_live + cfg.min_interval_s:
            waits.append(("min_interval", self._last_live + cfg.min_interval_s))
        return {"enabled": True, "can_request": not waits,
                "reason": waits[0][0] if waits else None,
                "next_allowed_epoch": max(w[1] for w in waits) if waits else None,
                "persistent": self.persistent}

    def _notice(self, status, error=None):
        reason, cfg = status["reason"], self.cfg
        msg = {
            "disabled": "Live Google Trends requests are switched off (TRENDS_ENABLED=false). Showing stored data only.",
            "rate_limit": "Google recently refused requests (HTTP 429), so ALL live Trends requests are paused. "
                          "Another attempt is possible after the time shown, but Google may still refuse it.",
            "failure": "The last request for this term failed, so it is paused briefly before another attempt.",
            "min_interval": f"A live request was sent very recently. Live requests are spaced at least {int(cfg.min_interval_s)} s apart.",
            "recent_cache": "The stored result is recent, so no new request was sent.",
        }.get(reason, "")
        if error and not msg:
            msg = error.get("message", "")
        return {"reason": reason, "message": msg, "next_allowed_epoch": status["next_allowed_epoch"]}

    def _outcome(self, outcome, entry, status, error=None, fresh=False):
        view = self._view(entry)
        data = view["result"] if view else None
        if fresh and data is not None:
            data = {**data, "from_cache": False}
        return {"outcome": outcome, "data": data, "data_source": ("fresh" if fresh else ("cached" if data else None)),
                "age_s": 0 if fresh else (view["age_s"] if view else None),
                "stale": False if fresh else (view["stale"] if view else False),
                "fetched_epoch": view["fetched_epoch"] if view else None,
                "error": error, "notice": self._notice(status, error), "status": status}

    # -- public API
    def lookup(self, keyword, gprop="", timeframe=TIMEFRAME, geo=""):
        """Stored result for this term, or None. Never sends a request."""
        with self._lock:
            return self._view(self._cache.get(self._ks(keyword, gprop, timeframe, geo)))

    def status(self, keyword, gprop="", timeframe=TIMEFRAME, geo=""):
        """Whether a live request would be allowed right now. Never sends a request."""
        with self._lock:
            return self._status_locked(self._ks(keyword, gprop, timeframe, geo))

    def request(self, keyword, gprop="", timeframe=TIMEFRAME, geo=""):
        """Deliberate user action (button press). Sends AT MOST one live pass, only if every rule allows it.
        outcome: fresh | partial | failed | cached | blocked | disabled"""
        ks = self._ks(keyword, gprop, timeframe, geo)
        with self._lock:
            st = self._status_locked(ks)
            entry = self._cache.get(ks)
            if not st["can_request"]:
                reason = st["reason"]
                kind = "disabled" if reason == "disabled" else ("cached" if reason == "recent_cache" else "blocked")
                return self._outcome(kind, entry, st)

            now = self._now()
            self.live_calls += 1
            self._last_live = now
            self._persist()                       # saved BEFORE the call: a crash/restart cannot bypass the spacing
            res = self._fetch(keyword, gprop, timeframe, geo)
            now = self._now()
            err = res.get("error")
            fresh = False
            if res.get("status") == "ok":
                self._cache[ks] = {"t": now, "result": res}
                self._key_block.pop(ks, None)
                outcome, fresh = "fresh", True
            else:
                if err and err.get("kind") == "rate_limit":
                    self._global_block_until = now + max(err.get("retry_after") or 0, self.cfg.rate_limit_cooldown_s)
                else:
                    self._key_block[ks] = now + max((err or {}).get("retry_after") or 0, self.cfg.failure_cooldown_s)
                better = res.get("status") == "partial" and (entry is None or entry["result"].get("status") != "ok")
                if better:                         # real data beats nothing; never overwrite a complete result
                    self._cache[ks] = {"t": now, "result": res}
                    outcome, fresh = "partial", True
                else:
                    outcome = "failed"
            self._persist()
            return self._outcome(outcome, self._cache.get(ks), self._status_locked(ks), error=err, fresh=fresh)


_GUARD = None
_GUARD_LOCK = threading.Lock()


def get_guard():
    """Process-wide singleton, created on first use so .env / secrets are already loaded."""
    global _GUARD
    with _GUARD_LOCK:
        if _GUARD is None:
            _GUARD = TrendsGuard()
        return _GUARD
