"""
devsocial_core.py — live GitHub (developer activity) and Reddit (community) data for the Coin Scanner.
No Streamlit imports, so it can be unit-tested offline.

GitHub  — official REST API, free.  Documented limits: 60 requests/hour without a token,
          5,000/hour with GITHUB_TOKEN.  The remaining quota is read from the response headers
          and shown in the UI.  A rate-limit response pauses all GitHub calls until the reset time.
Reddit  — official OAuth API (app-only 'client_credentials' login).  Needs REDDIT_CLIENT_ID and
          REDDIT_CLIENT_SECRET in .env.  Without them the UI shows "WAITING FOR REDDIT LOGIN".
Neither source retries automatically.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

GH_API = "https://api.github.com"
GH_TTL = 3600
REDDIT_TTL = 1800
MAX_REPOS = 2


class SourceError(Exception):
    """kind: rate_limit | not_found | http | network | pending | auth | config"""
    def __init__(self, kind, message, retry_after=None):
        super().__init__(message)
        self.kind, self.message, self.retry_after = kind, message, retry_after

    def as_dict(self):
        return {"kind": self.kind, "message": self.message, "retry_after": self.retry_after}


# ── generic TTL cache that never stores failures and honours rate-limit pauses ─
class CachedSource:
    def __init__(self, fetch_fn, ttl, clock=time.time):
        self._fetch, self.ttl, self._now = fetch_fn, ttl, clock
        self._cache, self._blocked_until, self._lock = {}, 0.0, threading.Lock()
        self.live_calls = 0

    def get(self, key, *args):
        with self._lock:
            now = self._now()
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return {**hit[1], "from_cache": True}
            if now < self._blocked_until:
                left = int(self._blocked_until - now)
                return {"ok": False, "from_cache": False, "data": None, "rate": None,
                        "error": {"kind": "rate_limit",
                                  "message": f"Paused after a rate-limit response — no request sent. Retry in ~{left // 60 + 1} min.",
                                  "retry_after": left}}
            self.live_calls += 1
            res = self._fetch(*args)
            now = self._now()
            err = res.get("error")
            if err and err.get("kind") == "rate_limit":
                self._blocked_until = now + max(err.get("retry_after") or 0, 60)
            elif res.get("ok"):
                self._cache[key] = (now, res)
            return {**res, "from_cache": False}


# ── GitHub: pure helpers ──────────────────────────────────────────────────────
_GH_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)


def parse_repo_url(url):
    """'https://github.com/quantnetwork/overledger-sdk.git/' -> ('quantnetwork','overledger-sdk')"""
    m = _GH_URL.match((url or "").strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    return (owner, repo) if repo else None


def summarize_commit_activity(weeks):
    """GitHub returns the last 52 weekly buckets, oldest first.  '4 WKS' = the last four buckets
    (the newest bucket is the current, still-running week)."""
    if not isinstance(weeks, list):
        raise ValueError("commit_activity is not a list")
    totals = [int(w["total"]) for w in weeks]
    return {"commits_4w": sum(totals[-4:]), "commits_52w": sum(totals), "weeks_returned": len(totals)}


def parse_repo_info(j):
    if not isinstance(j, dict) or "stargazers_count" not in j:
        raise ValueError("unexpected repo payload")
    return {"full_name": j.get("full_name", ""), "stars": int(j["stargazers_count"]),
            "forks": int(j.get("forks_count", 0)), "open_issues_and_prs": int(j.get("open_issues_count", 0)),
            "pushed_at": j.get("pushed_at"), "archived": bool(j.get("archived")),
            "description": j.get("description") or "", "html_url": j.get("html_url", "")}


def _rate_from_headers(h):
    try:
        return {"limit": int(h.get("X-RateLimit-Limit")), "remaining": int(h.get("X-RateLimit-Remaining")),
                "reset": int(h.get("X-RateLimit-Reset"))}
    except Exception:
        return None


def gh_get(path, token=None, params=None, session=None, timeout=12):
    """-> (json_or_None, status, rate_dict_or_None).  Raises SourceError. Single attempt."""
    s = session or requests
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "celestial-intel-coin-scanner"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = s.get(GH_API + path, headers=headers, params=params, timeout=timeout)
    except Exception as e:
        raise SourceError("network", f"Could not reach GitHub ({type(e).__name__})")
    rate = _rate_from_headers(r.headers or {})
    if r.status_code in (403, 429):
        remaining = (rate or {}).get("remaining")
        body = ""
        try:
            body = str(r.json().get("message", ""))
        except Exception:
            pass
        if remaining == 0 or "rate limit" in body.lower() or r.status_code == 429:
            reset = (rate or {}).get("reset")
            wait = max(int(reset - time.time()), 0) if reset else None
            ra = r.headers.get("Retry-After")
            wait = int(ra) if ra and str(ra).isdigit() else wait
            mins = f" Resets in ~{wait // 60 + 1} min." if wait is not None else ""
            raise SourceError("rate_limit", f"GitHub API rate limit reached.{mins} (add GITHUB_TOKEN to .env for 5,000/hour)", wait)
        raise SourceError("http", f"GitHub refused the request (HTTP {r.status_code}) {body[:80]}")
    if r.status_code == 404:
        raise SourceError("not_found", "Repository not found (moved, renamed or private)")
    if r.status_code == 202:
        raise SourceError("pending", "GitHub is still computing these statistics")
    if r.status_code == 204:
        return None, 204, rate
    if r.status_code >= 400:
        raise SourceError("http", f"GitHub returned HTTP {r.status_code}")
    try:
        return r.json(), r.status_code, rate
    except Exception:
        raise SourceError("http", "GitHub returned a non-JSON response")


def fetch_repo(owner, repo, token=None, session=None, now=None, sleep=time.sleep):
    """4 requests per repo: info, commit_activity (1 retry only if GitHub says 202), contributors, merged PRs."""
    now = now or datetime.now(timezone.utc)
    out = {"owner": owner, "repo": repo, "info": None, "commits_4w": None, "commits_52w": None,
           "contributors": None, "contributors_capped": False, "prs_merged_4w": None,
           "notes": [], "rate": None}

    def note(msg):
        out["notes"].append(msg)

    def track(rate):
        if rate:
            out["rate"] = rate

    j, _, rate = gh_get(f"/repos/{owner}/{repo}", token, session=session); track(rate)
    out["info"] = parse_repo_info(j)

    try:
        try:
            j, _, rate = gh_get(f"/repos/{owner}/{repo}/stats/commit_activity", token, session=session)
        except SourceError as e:
            if e.kind != "pending":
                raise
            sleep(3)                                                    # the ONE allowed retry
            j, _, rate = gh_get(f"/repos/{owner}/{repo}/stats/commit_activity", token, session=session)
        track(rate)
        s = summarize_commit_activity(j or [])
        out["commits_4w"], out["commits_52w"] = s["commits_4w"], s["commits_52w"]
    except SourceError as e:
        if e.kind == "rate_limit":
            raise
        note("commit stats: " + ("still being computed by GitHub — press SCAN again in a minute" if e.kind == "pending" else e.message))
    except (ValueError, KeyError, TypeError):
        note("commit stats: unexpected response shape")

    try:
        j, status, rate = gh_get(f"/repos/{owner}/{repo}/contributors", token,
                                 params={"per_page": 100, "anon": "false"}, session=session); track(rate)
        lst = j if isinstance(j, list) else []
        out["contributors"] = len(lst)
        out["contributors_capped"] = len(lst) >= 100
    except SourceError as e:
        if e.kind == "rate_limit":
            raise
        note("contributors: " + e.message)

    since = (now - timedelta(days=28)).strftime("%Y-%m-%d")
    try:
        j, _, rate = gh_get("/search/issues", token,
                            params={"q": f"repo:{owner}/{repo} is:pr is:merged merged:>={since}", "per_page": 1},
                            session=session); track(rate)
        out["prs_merged_4w"] = int(j["total_count"])
    except SourceError as e:
        if e.kind == "rate_limit":
            raise
        note("merged PRs: " + e.message)
    except (KeyError, TypeError, ValueError):
        note("merged PRs: unexpected response shape")
    return out


def fetch_github(repo_urls, token=None):
    """repo_urls: tuple of URLs (already limited). Returns {'ok','data','error','rate'}."""
    parsed = [p for p in (parse_repo_url(u) for u in repo_urls) if p][:MAX_REPOS]
    if not parsed:
        return {"ok": True, "data": {"repos": [], "no_repo": True}, "error": None, "rate": None}
    repos, errors, rate = [], [], None

    def one(pr):
        try:
            return fetch_repo(pr[0], pr[1], token)
        except SourceError as e:
            return e
    with ThreadPoolExecutor(max_workers=MAX_REPOS) as pool:
        for res in pool.map(one, parsed):
            if isinstance(res, SourceError):
                errors.append(res)
            else:
                repos.append(res)
                rate = res.get("rate") or rate
    if errors and not repos:
        e = errors[0]
        return {"ok": False, "data": None, "error": e.as_dict(), "rate": None}
    return {"ok": True, "data": {"repos": repos, "no_repo": False,
                                 "repo_errors": [e.as_dict() for e in errors]},
            "error": None, "rate": rate}


# ── Reddit ────────────────────────────────────────────────────────────────────
_REDDIT_SUB = re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)", re.I)
_token_cache = {"token": None, "exp": 0.0}


def parse_subreddit(url):
    m = _REDDIT_SUB.search(url or "")
    return m.group(1) if m else None


def parse_about(j):
    d = (j or {}).get("data") if isinstance(j, dict) else None
    if not isinstance(d, dict) or "subscribers" not in d:
        raise ValueError("unexpected subreddit payload")
    active = d.get("accounts_active", d.get("active_user_count"))
    return {"name": d.get("display_name", ""), "subscribers": int(d["subscribers"] or 0),
            "active": int(active) if active is not None else None, "title": d.get("title", ""),
            "created_utc": d.get("created_utc")}


def parse_search(j, limit=5):
    """-> {'count': n, 'top_posts': [...], 'top_authors': [(name, posts), ...]}"""
    children = ((j or {}).get("data") or {}).get("children") if isinstance(j, dict) else None
    if not isinstance(children, list):
        raise ValueError("unexpected search payload")
    posts = []
    for c in children:
        d = c.get("data", {})
        if not d.get("title"):
            continue
        posts.append({"title": d["title"], "author": d.get("author", "[deleted]"),
                      "score": int(d.get("score", 0)), "comments": int(d.get("num_comments", 0)),
                      "subreddit": d.get("subreddit", ""), "created_utc": d.get("created_utc"),
                      "url": "https://www.reddit.com" + d.get("permalink", "")})
    counts = {}
    for p in posts:
        if p["author"] not in ("[deleted]", "AutoModerator"):
            counts[p["author"]] = counts.get(p["author"], 0) + 1
    authors = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return {"count": len(posts), "top_posts": sorted(posts, key=lambda p: -p["score"])[:limit],
            "top_authors": authors}


def reddit_configured(client_id, client_secret):
    return bool(client_id and client_secret)


def _reddit_token(client_id, client_secret, user_agent, session=None):
    s = session or requests
    if _token_cache["token"] and time.time() < _token_cache["exp"] - 60:
        return _token_cache["token"]
    try:
        r = s.post("https://www.reddit.com/api/v1/access_token", auth=(client_id, client_secret),
                   data={"grant_type": "client_credentials"}, headers={"User-Agent": user_agent}, timeout=12)
    except Exception as e:
        raise SourceError("network", f"Could not reach Reddit ({type(e).__name__})")
    if r.status_code == 429:
        raise SourceError("rate_limit", "Reddit rate limit on login", _retry(r))
    if r.status_code in (401, 403):
        raise SourceError("auth", "Reddit rejected the login (check REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET, or app access not approved)")
    try:
        j = r.json()
        tok = j["access_token"]
    except Exception:
        raise SourceError("auth", "Reddit did not return an access token")
    _token_cache.update(token=tok, exp=time.time() + int(j.get("expires_in", 3600)))
    return tok


def _retry(r):
    try:
        return int(float(r.headers.get("Retry-After")))
    except Exception:
        return None


def _reddit_get(path, token, user_agent, params=None, session=None):
    s = session or requests
    try:
        r = s.get("https://oauth.reddit.com" + path, params=params, timeout=12,
                  headers={"Authorization": f"bearer {token}", "User-Agent": user_agent})
    except Exception as e:
        raise SourceError("network", f"Could not reach Reddit ({type(e).__name__})")
    if r.status_code == 429:
        raise SourceError("rate_limit", "Reddit rate limit reached", _retry(r))
    if r.status_code == 404:
        raise SourceError("not_found", "Subreddit not found")
    if r.status_code in (401, 403):
        raise SourceError("auth", f"Reddit refused the request (HTTP {r.status_code}) — subreddit may be private/banned")
    if r.status_code >= 400:
        raise SourceError("http", f"Reddit returned HTTP {r.status_code}")
    return r.json()


def fetch_reddit(subreddit, search_term, client_id, client_secret, user_agent, session=None):
    """Up to 3 requests: login, subreddit about, 7-day post search for `search_term`."""
    if not reddit_configured(client_id, client_secret):
        return {"ok": False, "data": None, "rate": None,
                "error": {"kind": "config", "message": "WAITING FOR REDDIT LOGIN", "retry_after": None}}
    try:
        tok = _reddit_token(client_id, client_secret, user_agent, session)
        data = {"subreddit": None, "mentions": None, "notes": []}
        if subreddit:
            try:
                data["subreddit"] = parse_about(_reddit_get(f"/r/{subreddit}/about", tok, user_agent, session=session))
            except SourceError as e:
                if e.kind == "rate_limit":
                    raise
                data["notes"].append(f"subreddit: {e.message}")
            except (ValueError, KeyError, TypeError):
                data["notes"].append("subreddit: unexpected response shape")
        try:
            j = _reddit_get("/search", tok, user_agent, session=session,
                            params={"q": search_term, "sort": "new", "t": "week", "limit": 50, "type": "link"})
            data["mentions"] = parse_search(j)
        except SourceError as e:
            if e.kind == "rate_limit":
                raise
            data["notes"].append(f"mentions: {e.message}")
        except (ValueError, KeyError, TypeError):
            data["notes"].append("mentions: unexpected response shape")
        return {"ok": True, "data": data, "error": None, "rate": None}
    except SourceError as e:
        return {"ok": False, "data": None, "rate": None, "error": e.as_dict()}
