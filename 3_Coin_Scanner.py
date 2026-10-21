"""
pages/3_Coin_Scanner.py
Coin Scanner — Universal 7-Color Terminal Style

Changes in this revision:
1.  Removed the TOP 100 strip header + QUICK SELECT (top-10 boxes)
2.  Removed the full top-100 table
3.  Search bar is the entry point.  Type a coin (ticker or name) + ENTER / SCAN and it
    searches every RSS feed in rss_active.csv (title + summary text) AND YouTube,
    then reports what it found with links.
4.  Removed the 7 metric cards (price / mkt cap / vol / FDV / 1H / 24H / 7D) and the
    whole MOMENTUM SCORE section (total score + 6 component boxes)
5.  Kept the market boxes: PRICE OVERVIEW, ALL-TIME LEVELS, PRICE CHANGES,
    VOLUME SIGNAL and ABOUT THIS COIN
6.  DEV ACTIVITY + COMMUNITY SIZE now sit in their own box (flagged NOT WORKING YET)
7.  ABOUT THIS COIN: native expander arrow removed (that was the overlapping
    "keyboard_arrow_down" text) — replaced with a simple ↓ / ↑ marker
8.  ABOUT THIS COIN: description is cleaned of HTML and uses ONE font size
"""

import csv
import html
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import requests
import streamlit as st

# ── urllib3 2.x / pytrends compatibility patch ──────────────────────────────
# pytrends still builds its retry policy with the pre-2.0 urllib3 kwarg
# `method_whitelist`; urllib3 2.0 renamed it to `allowed_methods`.
# Upstream issue: https://github.com/GeneralMills/pytrends/issues/591
try:
    from urllib3.util.retry import Retry as _Urllib3Retry
    _orig_retry_init = _Urllib3Retry.__init__

    def _patched_retry_init(self, *args, **kwargs):
        if "method_whitelist" in kwargs:
            kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
        _orig_retry_init(self, *args, **kwargs)

    _Urllib3Retry.__init__ = _patched_retry_init
except ImportError:
    pass

try:
    from pytrends.request import TrendReq
    PYTRENDS_OK = True
except ImportError:
    PYTRENDS_OK = False

try:
    import feedparser
    RSS_OK = True
except ImportError:
    RSS_OK = False

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# ── Paths / keys (same folder logic as 1_News_Feed.py, with a parent-folder fallback) ──
BASE_DIR = Path(__file__).parent.resolve()


def _find_file(name: str):
    for d in (BASE_DIR, BASE_DIR.parent, Path.cwd()):
        p = d / name
        if p.exists():
            return p
    return None


_env_path = _find_file(".env")
if load_dotenv and _env_path:
    load_dotenv(_env_path)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

CG_BASE = "https://api.coingecko.com/api/v3"
YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YT_LOOKBACK_DAYS = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
FEED_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}

COLORS = ["#00FFFF", "#00FF7F", "#FF5F1F", "#FF00FF", "#007FFF", "#8F00FF", "#FF3131"]
C_NEWS = "#39FF14"
C_YT = "#A855F7"
C_NEW = "#FF1493"

# Same channel IDs as TRUSTED_CHANNELS in 1_News_Feed.py (keep in sync if you edit that list).
# Used only to put a ★ on videos from channels you already trust.
TRUSTED_CHANNELS = {
    "Crypto Banter":        "UCN9Nj4tjXbVTLYWN0EKly_Q",
    "Raoul Pal Journeyman": "UCGXWKlq1Oxr3ddEtmKhAkPg",
    "Waters Above":         "UC2r1oKJFKGNw5THNPNHwBtQ",
    "Bankless":             "UCAl9Ld79qaZxp9JzEOwd3aA",
    "InvestAnswers":        "UClgJyzwGs-GyaNxUHcLZrkg",
    "jsnip4":               "UCYI2mPvsSXz99QPAVAPIG5Q",
    "Neil McCoy-Ward":      "UC3o2eSpoecewhemY1f2ZI-A",
    "CTO Larsson":          "UCFU-BE5HRJoudqIz1VDKlhQ",
    "VirtualBacon":         "UCcrEA_xd9Ldf1C8DIJYdyyA",
    "Paul Barron Network":  "UC4VPa7EOvObpyCRI4YKRQRw",
    "World Crypto Network": "UCR9gdpWisRwnk_k23GsHfcA",
    "Kitco News":           "UC9ijza42jVR3T6b8bColgvg",
    "Ivan on Tech":         "UCrYmtJBtLdtm2ov84ulV-yg",
    "Altcoin Daily":        "UCbLhGKVY-bJPcawebgtNfbw",
    "Boxmining":            "UCxODjeUwZHk3p-7TU-IsDOA",
}
TRUSTED_IDS = set(TRUSTED_CHANNELS.values())

st.set_page_config(page_title="Coin Scanner", layout="wide")

st.markdown("""
<style>
  html, body,
  [data-testid="stAppViewContainer"],
  [data-testid="stMain"],
  section.main,
  [data-testid="block-container"],
  [data-testid="stSidebar"] {
    background-color: #000000 !important;
    color: #FFFFFF !important;
    font-family: 'Courier New', Courier, monospace !important;
  }
  div[data-testid="stVerticalBlock"] > div,
  div[data-testid="stHorizontalBlock"] > div { background: transparent !important; }
  h1,h2,h3,h4,h5,h6,p,label,span,div {
    font-family: 'Courier New', Courier, monospace !important;
  }
  /* The rule above also hits Streamlit's icon glyphs (they are text ligatures such as
     "keyboard_arrow_down").  Give icons their own font back so they never show as text. */
  [data-testid="stIconMaterial"],
  .material-symbols-rounded,
  .material-icons {
    font-family: 'Material Symbols Rounded', 'Material Icons' !important;
  }
  /* search form: no frame, no "press enter" hint */
  [data-testid="stForm"] {
    border: none !important;
    padding: 0 !important;
    background: transparent !important;
  }
  [data-testid="InputInstructions"] { display: none !important; }
  div[data-testid="stTextInput"] input {
    background-color: #000000 !important;
    border: 1px solid #222222 !important;
    color: #FFFFFF !important;
    font-family: 'Courier New', Courier, monospace !important;
    border-radius: 2px !important;
  }
  div[data-testid="stTextInput"] input:focus {
    border-color: #00FFFF !important;
    box-shadow: none !important;
  }
  div[data-testid="stTextInput"] label { display: none; }
  div[data-testid="stButton"] > button,
  div[data-testid="stFormSubmitButton"] > button {
    background: transparent !important;
    border: 1px solid #FFFFFF !important;
    color: #FFFFFF !important;
    font-family: 'Courier New', Courier, monospace !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    border-radius: 2px !important;
    letter-spacing: .06em !important;
  }
  div[data-testid="stButton"] > button:hover,
  div[data-testid="stFormSubmitButton"] > button:hover {
    border-color: #00FFFF !important;
    color: #00FFFF !important;
  }
  [data-testid="stTabs"] [role="tablist"] {
    border-bottom: 1px solid #222222 !important;
    background: transparent !important;
  }
  [data-testid="stTabs"] button[role="tab"] {
    color: #FFFFFF !important;
    font-family: 'Courier New', Courier, monospace !important;
    text-transform: uppercase !important;
    border: none !important;
    background: transparent !important;
    font-size: 11px !important;
  }
  [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #00FFFF !important;
    border-bottom: 2px solid #00FFFF !important;
  }
  [data-testid="stProgress"] > div > div { background-color: #00FFFF !important; }
  hr { border-color: #222222 !important; }
  [data-testid="stSpinner"] p { color: #00FFFF !important; }
  [data-testid="stAlert"] {
    background: #0a0a0a !important;
    border: 1px solid #333333 !important;
    color: #FFFFFF !important;
    font-family: 'Courier New', Courier, monospace !important;
  }
  .stMarkdown a { color: inherit !important; }

  /* ABOUT THIS COIN — plain HTML collapsible: no Streamlit icon, one font size throughout */
  details.about-box {
    border: 1px solid #1a1a1a;
    border-radius: 2px;
    margin-top: 14px;
    background: transparent;
  }
  details.about-box summary {
    cursor: pointer;
    padding: 10px 14px;
    color: #FFFFFF;
    font-size: 12px;
    line-height: 1.7;
    letter-spacing: .06em;
    text-transform: uppercase;
    list-style: none;
  }
  details.about-box summary::-webkit-details-marker { display: none; }
  details.about-box summary::marker { content: ""; }
  details.about-box summary::after { content: "  ↓"; }
  details.about-box[open] summary::after { content: "  ↑"; }
  details.about-box .about-body {
    padding: 2px 14px 14px;
    color: #FFFFFF;
    font-size: 12px;
    line-height: 1.7;
  }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt(n, prefix="$", dec=2):
    if n is None: return "—"
    try: n = float(n)
    except Exception: return str(n)
    if n >= 1e12: return f"{prefix}{n/1e12:.{dec}f}T"
    if n >= 1e9:  return f"{prefix}{n/1e9:.{dec}f}B"
    if n >= 1e6:  return f"{prefix}{n/1e6:.{dec}f}M"
    if n >= 1e3:  return f"{prefix}{n/1e3:.{dec}f}K"
    return f"{prefix}{n:.{dec}f}"

def _price_full(n):
    """Full price — no scientific notation."""
    if n is None: return "—"
    try:
        n = float(n)
        if n >= 1:    return f"${n:,.2f}"
        if n >= 0.01: return f"${n:,.4f}"
        return f"${n:,.8f}"
    except Exception:
        return str(n)

def _pct_html(v):
    if v is None: return '<span style="color:#555555;">—</span>'
    col  = "#39FF14" if v >= 0 else "#FF3131"
    sign = "+" if v > 0 else ""
    return f'<span style="color:{col};font-weight:700;">{sign}{v:.2f}%</span>'

def _section_header(text, color):
    return (
        f'<div style="font-size:10px;color:{color};text-transform:uppercase;'
        f'letter-spacing:.12em;border-left:3px solid {color};'
        f'padding-left:8px;margin:18px 0 10px;">{text}</div>'
    )

def _tbl_wrap(rows_html, color):
    return (
        f'<div style="border:1px solid {color};margin-top:4px;">'
        f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
        f'{rows_html}</table></div>'
    )

def _tbl_row(label, value, color, header=False):
    lc = color if header else "#FFFFFF"
    vc = "#FFFFFF"
    return (
        f'<tr style="border-bottom:1px solid #333333;">'
        f'<td style="padding:7px 12px;color:{lc};text-transform:uppercase;'
        f'font-size:10px;white-space:nowrap;">{label}</td>'
        f'<td style="padding:7px 12px;color:{vc};text-align:right;">{value}</td>'
        f'</tr>'
    )

def _num(n):
    if n is None: return "—"
    try: return f"{int(n):,}"
    except Exception: return str(n)

def _clean_text(s: str) -> str:
    """Strip HTML tags/entities and collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)

def _age(dt):
    if not dt: return "DATE ?"
    s = max(int((datetime.now(timezone.utc) - dt).total_seconds()), 0)
    if s < 3600:  return f"{max(s // 60, 1)}m ago"
    if s < 86400: return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"

def _when(dt):
    if not dt: return "DATE ?"
    return f"{_age(dt)} · {dt.strftime('%d %b %H:%M')} UTC"

def _is_new(dt, hours=6):
    return bool(dt) and (datetime.now(timezone.utc) - dt) < timedelta(hours=hours)


# ── CoinGecko fetchers (errors are RAISED so a rate-limit is never cached) ────
@st.cache_data(ttl=3600, show_spinner=False)
def _cg_search(query: str):
    r = requests.get(f"{CG_BASE}/search", params={"query": query}, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("coins", [])

def _pick_coin(query, coins):
    """Prefer an exact ticker match (best market-cap rank), then exact name/id, then first hit."""
    ql = query.strip().lower()
    def rank(c):
        r = c.get("market_cap_rank")
        return r if isinstance(r, int) else 10**9
    sym = [c for c in coins if (c.get("symbol") or "").lower() == ql]
    if sym: return min(sym, key=rank)
    nm = [c for c in coins if (c.get("name") or "").lower() == ql or (c.get("id") or "").lower() == ql]
    if nm: return min(nm, key=rank)
    return coins[0]

def resolve_coin(query):
    """Returns (coin_dict_or_None, error_message)."""
    try:
        coins = _cg_search(query)
    except Exception as e:
        return None, f"COINGECKO SEARCH FAILED ({str(e)[:60]}) — wait ~60 seconds and press SCAN again"
    if not coins:
        return None, "COIN NOT FOUND ON COINGECKO"
    c = _pick_coin(query, coins)
    return {"id": c["id"], "name": c.get("name", query), "symbol": (c.get("symbol") or "").upper()}, ""

@st.cache_data(ttl=300, show_spinner=False)
def _cg_detail(coin_id: str):
    r = requests.get(
        f"{CG_BASE}/coins/{coin_id}",
        params={
            "localization": "false", "tickers": "false",
            "market_data": "true", "community_data": "true",
            "developer_data": "true", "sparkline": "false",
        },
        headers=HEADERS, timeout=15,
    )
    r.raise_for_status()
    return r.json()

def get_coin_detail(coin_id: str):
    try:
        return _cg_detail(coin_id)
    except Exception:
        return {}

@st.cache_data(ttl=3600)
def get_trends(keyword: str, gprop: str = ""):
    """Fetch Google Trends data. gprop: '' (web), 'youtube', 'news', 'images'"""
    empty = {"7d": 0, "30d": 0, "rising": [], "top_queries": [],
             "rising_queries": [], "related_topics": [], "region_data": {}}
    if not PYTRENDS_OK:
        return empty
    try:
        pt  = TrendReq(hl="en-US", tz=0, timeout=(5, 25))
        kw  = keyword if len(keyword) > 2 else keyword + " crypto"

        pt.build_payload([kw], timeframe="now 7-d", gprop=gprop)
        iot  = pt.interest_over_time()
        avg7 = int(iot[kw].mean()) if not iot.empty else 0
        time.sleep(1)

        pt.build_payload([kw], timeframe="today 1-m", gprop=gprop)
        iot2  = pt.interest_over_time()
        avg30 = int(iot2[kw].mean()) if not iot2.empty else 0
        time.sleep(1)

        pt.build_payload([kw], timeframe="today 1-m", gprop=gprop)
        related   = pt.related_queries()
        rq_data   = related.get(kw, {}) or {}

        top_df    = rq_data.get("top")
        top_queries = []
        if top_df is not None and not top_df.empty:
            for _, row in top_df.head(10).iterrows():
                top_queries.append({
                    "query": str(row.get("query", "")),
                    "value": int(row.get("value", 0)),
                })

        rising_df = rq_data.get("rising")
        rising_queries = []
        if rising_df is not None and not rising_df.empty:
            for _, row in rising_df.head(10).iterrows():
                val = row.get("value", 0)
                rising_queries.append({
                    "query": str(row.get("query", "")),
                    "value": int(val) if val != "Breakout" else 5001,
                    "breakout": (val == "Breakout" or int(val) >= 5000 if val != "Breakout" else True),
                })
        rising_legacy = [r["query"] for r in rising_queries[:5]]

        time.sleep(1)
        rt_data      = pt.related_topics()
        rt_kw        = rt_data.get(kw, {}) or {}
        topics_df    = rt_kw.get("top")
        related_topics = []
        if topics_df is not None and not topics_df.empty:
            for _, row in topics_df.head(15).iterrows():
                title = str(row.get("topic_title", ""))
                val   = int(row.get("value", 0))
                if title:
                    related_topics.append({"title": title, "value": val})

        time.sleep(1)
        pt.build_payload([kw], timeframe="today 1-m", gprop=gprop)
        try:
            irb = pt.interest_by_region(resolution="COUNTRY", inc_low_vol=True, inc_geo_code=True)
            region_data = {}
            if not irb.empty:
                irb_sorted = irb[kw].sort_values(ascending=False)
                for geo, val in irb_sorted.head(20).items():
                    if val > 0:
                        region_data[str(geo)] = int(val)
        except Exception:
            region_data = {}

        return {
            "7d": avg7, "30d": avg30, "rising": rising_legacy,
            "top_queries": top_queries, "rising_queries": rising_queries,
            "related_topics": related_topics, "region_data": region_data,
        }
    except Exception:
        return empty


# ── FEED SCAN: RSS ────────────────────────────────────────────────────────────
def load_sources():
    """Read rss_active.csv (name, rss_url, ...).  Returns (tuple_of_(name,url), path_or_None)."""
    path = _find_file("rss_active.csv")
    if not path:
        return (), None
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if row.get("rss_url"):
                out.append((row.get("name") or row["rss_url"], row["rss_url"]))
    return tuple(out), path

@st.cache_data(ttl=300, show_spinner=False)
def load_feed_entries(sources: tuple):
    """Download every feed in parallel and keep ALL entries (title + summary text).
    Searching happens locally afterwards, so repeat scans are instant for 5 minutes."""
    def _one(src):
        name, url = src
        try:
            r = requests.get(url, headers=FEED_HEADERS, timeout=12)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            items = []
            for e in parsed.entries:
                title = _clean_text(e.get("title", ""))
                if not title:
                    continue
                summ = e.get("summary") or e.get("description") or ""
                if not summ and e.get("content"):
                    summ = e["content"][0].get("value", "")
                pub_dt = None
                for key in ("published_parsed", "updated_parsed"):
                    tup = e.get(key)
                    if tup:
                        try:
                            pub_dt = datetime(*tup[:6], tzinfo=timezone.utc)
                            break
                        except Exception:
                            pass
                items.append({
                    "title": title, "link": e.get("link", "#"),
                    "text": _clean_text(summ)[:1500], "pub_dt": pub_dt, "source": name,
                })
            if not items:
                return {"name": name, "ok": False, "count": 0, "items": [], "error": "no articles returned"}
            return {"name": name, "ok": True, "count": len(items), "items": items, "error": ""}
        except Exception as ex:
            return {"name": name, "ok": False, "count": 0, "items": [], "error": str(ex)[:120]}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_one, sources))
    entries = [it for res in results for it in res["items"]]
    feeds = [{k: v for k, v in res.items() if k != "items"} for res in results]
    return {"entries": entries, "feeds": feeds}


# ── FEED SCAN: matching ───────────────────────────────────────────────────────
def build_patterns(coin, query):
    """Ticker = case-sensitive UPPERCASE whole word ('QNT', '$QNT').
    Name   = case-insensitive whole word ('Quant', but not 'quantum').
    Returns (patterns, label_terms)."""
    q = query.strip()
    tickers, names = [], []
    if coin:
        sym, nm = coin["symbol"], coin["name"]
        if nm and sym and nm.lower() == sym.lower():
            names.append(nm)
        else:
            if sym: tickers.append(sym.upper())
            if nm:  names.append(nm)
        known = {t.lower() for t in tickers} | {n.lower() for n in names}
        if q.lower() not in known:
            names.append(q)
    else:
        if len(q) <= 6 and " " not in q:
            tickers.append(q.upper())
        names.append(q)

    def _wb(term):
        return r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"

    pats = [("TICKER", re.compile(_wb(t))) for t in tickers]
    pats += [("NAME", re.compile(_wb(n), re.I)) for n in names]
    label, seen = [], set()
    for t in tickers + names:
        if t.upper() not in seen:
            seen.add(t.upper()); label.append(t.upper())
    return pats, label

def _kinds(pats, text):
    return {kind for kind, p in pats if p.search(text or "")}

def search_items(items, pats, text_key):
    """Return items mentioning the coin, strongest first (title hit > text-only hit, then newest)."""
    floor = datetime.min.replace(tzinfo=timezone.utc)
    hits = []
    for it in items:
        k_title = _kinds(pats, it["title"])
        k_text  = _kinds(pats, it.get(text_key, ""))
        kinds   = k_title | k_text
        if not kinds:
            continue
        hits.append({**it, "in_title": bool(k_title), "kinds": kinds})
    hits.sort(key=lambda h: (h["in_title"], len(h["kinds"]), h["pub_dt"] or floor), reverse=True)
    return hits


# ── FEED SCAN: YouTube (one search call = 100 quota units, cached 1 h) ────────
def _yt_error(r) -> str:
    try:
        j = r.json().get("error", {})
        reason = (j.get("errors") or [{}])[0].get("reason", "")
        msg = j.get("message", "")
    except Exception:
        reason, msg = "", r.text[:150]
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        return "YOUTUBE API QUOTA USED UP FOR TODAY (resets at midnight Pacific time)"
    return f"YOUTUBE API ERROR  HTTP {r.status_code}  {reason}  {msg}"[:220]

def _fmt_duration(iso: str) -> str:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m or not any(m.groups()):
        return "LIVE/—"
    h, mi, s = (int(x or 0) for x in m.groups())
    return f"{h}:{mi:02d}:{s:02d}" if h else f"{mi}:{s:02d}"

@st.cache_data(ttl=3600, show_spinner=False)
def yt_search_raw(api_key: str, query: str, days: int = YT_LOOKBACK_DAYS):
    """Raises on any API problem (so failures are not cached)."""
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(YT_SEARCH_URL, params={
        "part": "snippet", "type": "video", "q": query, "order": "relevance",
        "maxResults": 30, "publishedAfter": after, "key": api_key,
    }, timeout=12)
    if r.status_code != 200:
        raise RuntimeError(_yt_error(r))
    vids = []
    for it in r.json().get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        if not vid:
            continue
        sn = it.get("snippet", {})
        pub_dt = None
        try:
            pub_dt = datetime.fromisoformat(sn.get("publishedAt", "").replace("Z", "+00:00"))
        except Exception:
            pass
        vids.append({
            "id": vid, "title": _clean_text(sn.get("title", "")),
            "desc": _clean_text(sn.get("description", "")),
            "channel": sn.get("channelTitle", ""), "channel_id": sn.get("channelId", ""),
            "link": f"https://www.youtube.com/watch?v={vid}", "pub_dt": pub_dt,
            "views": None, "duration": "",
        })
    if vids:   # 1 extra quota unit: view counts + length
        try:
            r2 = requests.get(YT_VIDEOS_URL, params={
                "part": "statistics,contentDetails",
                "id": ",".join(v["id"] for v in vids), "key": api_key,
            }, timeout=12)
            if r2.status_code == 200:
                stats = {i["id"]: i for i in r2.json().get("items", [])}
                for v in vids:
                    s = stats.get(v["id"])
                    if s:
                        vc = (s.get("statistics") or {}).get("viewCount")
                        v["views"] = int(vc) if vc is not None else None
                        v["duration"] = _fmt_duration((s.get("contentDetails") or {}).get("duration", ""))
        except Exception:
            pass
    return vids


# ── FEED SCAN: rendering ──────────────────────────────────────────────────────
def _badge(text, bg=None, fg="#FFFFFF", border=None):
    st_bg = f"background:{bg};" if bg else ""
    st_bd = f"border:1px solid {border};" if border else ""
    return (
        f'<span style="{st_bg}{st_bd}color:{fg};font-size:9px;font-weight:700;'
        f'padding:1px 5px;margin-left:6px;vertical-align:middle;">{text}</span>'
    )

def _match_badges(h):
    kinds = " + ".join(k for k in ("TICKER", "NAME") if k in h["kinds"])
    where = "TITLE" if h["in_title"] else "TEXT"
    return _badge(f"{where} · {kinds}", border="#555555")

def _item_html(title, link, accent, meta_html, badges_html):
    return (
        f'<div style="padding:7px 0 8px 10px;border-bottom:1px solid #1a1a1a;">'
        f'<div style="font-size:13px;line-height:1.4;color:{accent};">'
        f'<a href="{_esc(link)}" target="_blank" style="text-decoration:none;">{_esc(title)}</a>'
        f'{badges_html}</div>'
        f'<div style="font-size:10px;color:#FFFFFF;margin-top:3px;display:flex;gap:10px;flex-wrap:wrap;">'
        f'{meta_html}</div></div>'
    )

def _panel(title, accent, strip_html, body_html):
    return (
        f'<div style="border:2px solid {accent};border-radius:6px;background:#0a0a0a;'
        f'padding:14px 16px;color:{accent};">'
        f'<div style="font-size:15px;font-weight:700;color:{accent};border-bottom:2px solid {accent};'
        f'padding-bottom:8px;margin-bottom:10px;">{title}</div>'
        f'{strip_html}{body_html}</div>'
    )

def _strip(text_html):
    return f'<div style="font-size:11px;color:#FFFFFF;margin-bottom:8px;">{text_html}</div>'

def _note(text_html, color="#FFFFFF"):
    return f'<div style="font-size:12px;color:{color};padding:10px 0;line-height:1.5;">{text_html}</div>'


# ── PAGE HEADER ───────────────────────────────────────────────────────────────
now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
st.markdown(
    f'<div style="display:flex;justify-content:space-between;'
    f'align-items:flex-start;margin-bottom:4px;">'
    f'<div style="font-size:22px;font-weight:700;color:#FFFFFF;'
    f'letter-spacing:.1em;text-transform:uppercase;">COIN SCANNER</div>'
    f'<div style="font-size:11px;color:#FFFFFF;font-weight:700;'
    f'text-align:right;white-space:nowrap;padding-top:4px;">{now_str}</div>'
    f'</div>',
    unsafe_allow_html=True,
)
st.markdown('<hr style="border-color:#222222;margin:8px 0 16px;" />', unsafe_allow_html=True)


# ── SEARCH BAR (inside a form so ENTER works as well as the button) ───────────
with st.form("scan_form", clear_on_submit=False):
    sc, gc = st.columns([5, 1])
    with sc:
        q = st.text_input(
            "search",
            value=st.session_state.get("tc_query", ""),
            placeholder="ENTER A COIN  —  e.g.  QNT  /  quant  /  XRP  /  solana",
            label_visibility="collapsed",
        )
    with gc:
        go = st.form_submit_button("SCAN ↵", use_container_width=True)

if go and q.strip():
    st.session_state.tc_query = q.strip()

active_query = st.session_state.get("tc_query", "").strip()

if not active_query:
    st.markdown(
        '<div style="border:1px dashed #1a1a1a;padding:48px;text-align:center;">'
        '<div style="color:#FFFFFF;font-size:13px;letter-spacing:.12em;">'
        'TYPE A COIN IN THE SEARCH BAR  ·  SCANS ALL RSS FEEDS + YOUTUBE + COIN DATA'
        '</div></div>',
        unsafe_allow_html=True,
    )
    st.stop()


# ── RUN THE SCAN ──────────────────────────────────────────────────────────────
with st.spinner(f"SCANNING  {active_query.upper()[:24]}…"):
    coin, coin_err = resolve_coin(active_query)
    detail = get_coin_detail(coin["id"]) if coin else {}

    pats, term_label = build_patterns(coin, active_query)
    terms_txt = " / ".join(term_label)

    # RSS
    sources, rss_path = load_sources()
    feed_data = {"entries": [], "feeds": []}
    if RSS_OK and sources:
        feed_data = load_feed_entries(sources)
    news_hits = search_items(feed_data["entries"], pats, "text")

    # YouTube
    yt_state = {"status": "ok", "msg": "", "returned": 0, "hits": []}
    if not YOUTUBE_API_KEY:
        yt_state.update(status="nokey")
    else:
        if coin and coin["name"].lower() != coin["symbol"].lower():
            yt_query = f"{coin['name']} {coin['symbol']} crypto"
        else:
            yt_query = f"{coin['name'] if coin else active_query} crypto"
        try:
            raw = yt_search_raw(YOUTUBE_API_KEY, yt_query)
            for v in raw:
                v["text"] = v["desc"]
            yt_state["returned"] = len(raw)
            yt_state["hits"] = search_items(raw, pats, "text")
            yt_state["hits"].sort(key=lambda h: h["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        except Exception as e:
            yt_state.update(status="error", msg=str(e))

yt_link = (
    "https://www.youtube.com/results?search_query="
    + quote_plus(f"{coin['name'] if coin else active_query} {coin['symbol'] if coin else ''} crypto".strip())
    + "&sp=CAI%253D"
)


# ── COIN IDENTITY ─────────────────────────────────────────────────────────────
if coin and detail:
    sym     = (detail.get("symbol") or coin["symbol"]).upper()
    name    = detail.get("name") or coin["name"]
    img_url = (detail.get("image") or {}).get("small", "")
    rank    = (detail.get("market_data") or {}).get("market_cap_rank")
    cats    = detail.get("categories") or []
    cg_url  = f"https://www.coingecko.com/en/coins/{coin['id']}"
    img_html = (
        f'<img src="{img_url}" style="width:28px;height:28px;border-radius:50%;'
        f'margin-right:10px;vertical-align:middle;" />' if img_url else ""
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:4px 0 14px;">'
        f'{img_html}'
        f'<span style="font-size:20px;font-weight:700;color:#FFFFFF;">{_esc(sym)}</span>'
        f'<span style="font-size:13px;color:#FFFFFF;">/ {_esc(name)}</span>'
        f'<span style="border:1px solid #00FFFF;color:#00FFFF;font-size:9px;'
        f'padding:2px 7px;letter-spacing:.06em;">RANK #{rank or "?"}</span>'
        + (f'<span style="border:1px solid #333333;color:#FFFFFF;font-size:9px;'
           f'padding:2px 7px;">{_esc(cats[0].upper())}</span>' if cats else "")
        + f'<span style="color:#007FFF;"><a href="{cg_url}" target="_blank" '
        f'style="font-size:10px;text-decoration:none;border:1px solid #007FFF;padding:2px 8px;">'
        f'↗ COINGECKO</a></span>'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    why = coin_err or "COINGECKO DATA UNAVAILABLE (usually a rate limit — wait ~60 seconds and press SCAN again)"
    st.markdown(
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:4px 0 14px;">'
        f'<span style="font-size:20px;font-weight:700;color:#FFFFFF;">{_esc(active_query.upper())}</span>'
        f'<span style="border:1px solid #FF3131;color:#FF3131;font-size:10px;padding:2px 8px;">{_esc(why)}</span>'
        f'</div>'
        f'<div style="font-size:11px;color:#FFFFFF;margin-bottom:6px;">'
        f'FEED SCAN BELOW STILL RUNS ON WHAT YOU TYPED.</div>',
        unsafe_allow_html=True,
    )


# ── FEED SCAN ─────────────────────────────────────────────────────────────────
st.markdown(_section_header(f"📡  FEED SCAN  —  {_esc(terms_txt)}", "#39FF14"), unsafe_allow_html=True)

col_news, col_yt = st.columns(2)

# NEWS (RSS)
with col_news:
    feeds      = feed_data["feeds"]
    ok_feeds   = [f for f in feeds if f["ok"]]
    bad_feeds  = [f for f in feeds if not f["ok"]]
    n_articles = len(feed_data["entries"])

    if not RSS_OK:
        body  = _note("FEEDPARSER NOT INSTALLED  —  RUN:  pip install feedparser", "#FF3131")
        strip = ""
    elif not sources:
        body  = _note("rss_active.csv NOT FOUND  (looked next to this page and one folder up).", "#FF3131")
        strip = ""
    else:
        strip = _strip(
            f'SCANNED <b style="color:{C_NEWS};">{len(ok_feeds)}/{len(feeds)}</b> FEEDS · '
            f'<b style="color:{C_NEWS};">{n_articles}</b> ARTICLES · '
            f'<b style="color:{C_NEWS};">{len(news_hits)}</b> MENTION {_esc(terms_txt)}'
            + (f'<br><span style="color:#FF3131;">⚠ NO RESPONSE: '
               f'{_esc(", ".join(f["name"] for f in bad_feeds))}</span>' if bad_feeds else "")
        )
        if news_hits:
            body = "".join(
                _item_html(
                    h["title"], h["link"], C_NEW if _is_new(h["pub_dt"]) else C_NEWS,
                    f'<span>{_esc(h["source"])}</span><span>{_esc(_when(h["pub_dt"]))}</span>',
                    (_badge("NEW", bg=C_NEW, fg="#000000") if _is_new(h["pub_dt"]) else "")
                    + _match_badges(h),
                )
                for h in news_hits[:25]
            )
            if len(news_hits) > 25:
                body += _note(f"+ {len(news_hits) - 25} MORE NOT SHOWN")
        else:
            dts = [e["pub_dt"] for e in feed_data["entries"] if e["pub_dt"]]
            reach = f" OLDEST ARTICLE SCANNED: {_age(min(dts)).upper()}." if dts else ""
            body = _note(
                f'NOTHING FOUND FOR {_esc(terms_txt)} IN {n_articles} ARTICLES ACROSS '
                f'{len(ok_feeds)} FEEDS.{reach}'
            )
    st.markdown(_panel("📰 NEWS  (RSS FEEDS)", C_NEWS, strip, body), unsafe_allow_html=True)

# YOUTUBE
with col_yt:
    hits = yt_state["hits"]
    if yt_state["status"] == "nokey":
        strip = ""
        body  = _note("YOUTUBE_API_KEY NOT FOUND IN .env — in-app video search is off.", "#FF3131")
    elif yt_state["status"] == "error":
        strip = ""
        body  = _note(f'⚠ {_esc(yt_state["msg"])}', "#FF3131")
    else:
        strip = _strip(
            f'LAST {YT_LOOKBACK_DAYS} DAYS · <b style="color:{C_YT};">{yt_state["returned"]}</b> VIDEOS RETURNED · '
            f'<b style="color:{C_YT};">{len(hits)}</b> MENTION {_esc(terms_txt)}'
        )
        if hits:
            def _yt_meta(v):
                parts = []
                star = "★ " if v["channel_id"] in TRUSTED_IDS else ""
                parts.append(f'<span style="color:{C_YT};font-weight:700;">{star}{_esc(v["channel"])}</span>')
                if v.get("views") is not None:
                    parts.append(f'<span>{_fmt(v["views"], prefix="", dec=1)} views</span>')
                if v.get("duration"):
                    parts.append(f'<span>{_esc(v["duration"])}</span>')
                parts.append(f'<span>{_esc(_when(v["pub_dt"]))}</span>')
                return "".join(parts)
            body = "".join(
                _item_html(
                    "▶ " + v["title"], v["link"], C_NEW if _is_new(v["pub_dt"]) else C_YT,
                    _yt_meta(v),
                    (_badge("NEW", bg=C_NEW, fg="#000000") if _is_new(v["pub_dt"]) else "")
                    + (_badge("TRUSTED", border=C_YT) if v["channel_id"] in TRUSTED_IDS else ""),
                )
                for v in hits[:15]
            )
        elif yt_state["returned"]:
            body = _note(
                f'{yt_state["returned"]} VIDEOS CAME BACK BUT NONE MENTION {_esc(terms_txt)} BY NAME OR TICKER.'
            )
        else:
            body = _note(f'NO VIDEOS FOUND FOR {_esc(terms_txt)} IN THE LAST {YT_LOOKBACK_DAYS} DAYS.')
    body += (
        f'<div style="padding:10px 0 2px;font-size:11px;color:{C_YT};">'
        f'<a href="{yt_link}" target="_blank" style="text-decoration:none;'
        f'border:1px solid {C_YT};padding:3px 9px;">OPEN FULL YOUTUBE SEARCH ↗</a></div>'
    )
    st.markdown(_panel("▶ YOUTUBE", C_YT, strip, body), unsafe_allow_html=True)


# ── COIN DATA ─────────────────────────────────────────────────────────────────
if not (coin and detail):
    st.stop()

md      = detail.get("market_data",    {}) or {}
cd      = detail.get("community_data", {}) or {}
dev     = detail.get("developer_data", {}) or {}
coin_name = detail.get("name") or coin["name"]

def _g(*keys):
    o = detail
    for k in keys:
        if not isinstance(o, dict): return None
        o = o.get(k)
    return o

price   = _g("market_data", "current_price", "usd")
mcap    = _g("market_data", "market_cap", "usd")
fdv     = _g("market_data", "fully_diluted_valuation", "usd")
vol24   = _g("market_data", "total_volume", "usd")
chg1h   = _g("market_data", "price_change_percentage_1h_in_currency", "usd")
chg24   = _g("market_data", "price_change_percentage_24h")
chg7d   = _g("market_data", "price_change_percentage_7d")
chg30d  = _g("market_data", "price_change_percentage_30d")
ath     = _g("market_data", "ath", "usd")
ath_chg = _g("market_data", "ath_change_percentage", "usd")
atl     = _g("market_data", "atl", "usd")
circ_s  = _g("market_data", "circulating_supply")
total_s = _g("market_data", "total_supply")
max_s   = _g("market_data", "max_supply")
rank    = _g("market_data", "market_cap_rank")
commits = dev.get("commit_count_4_weeks") or 0
tw_f    = cd.get("twitter_followers") or 0
tg_f    = cd.get("telegram_channel_user_count") or 0
reddit_s = cd.get("reddit_subscribers") or 0
price_str = _price_full(price)

st.markdown(_section_header("DETAILED DATA", "#FF5F1F"), unsafe_allow_html=True)

tab_mkt, tab_supply, tab_community, tab_dev, tab_trends = st.tabs([
    "📊  MARKET", "🪙  SUPPLY", "👥  COMMUNITY", "💻  DEVELOPER", "📈  GOOGLE TRENDS",
])

# Tab 1: Market
with tab_mkt:
    ma, mb = st.columns(2)
    with ma:
        st.markdown(_section_header("PRICE OVERVIEW", "#00FFFF"), unsafe_allow_html=True)
        rows = (
            _tbl_row("PRICE (USD)",  price_str,         "#00FFFF", True)
            + _tbl_row("MARKET CAP", _fmt(mcap),        "#00FFFF")
            + _tbl_row("FDV",        _fmt(fdv),         "#00FFFF")
            + _tbl_row("24H VOLUME", _fmt(vol24),       "#00FFFF")
            + _tbl_row("RANK",       f'#{rank or "—"}', "#00FFFF")
        )
        st.markdown(_tbl_wrap(rows, "#00FFFF"), unsafe_allow_html=True)

        st.markdown(_section_header("ALL-TIME LEVELS", "#FF00FF"), unsafe_allow_html=True)
        rows3 = (
            _tbl_row("ATH",          _price_full(ath),   "#FF00FF", True)
            + _tbl_row("ATH CHANGE", _pct_html(ath_chg), "#FF00FF")
            + _tbl_row("ATL",        _price_full(atl),   "#FF00FF")
        )
        st.markdown(_tbl_wrap(rows3, "#FF00FF"), unsafe_allow_html=True)

    with mb:
        st.markdown(_section_header("PRICE CHANGES", "#00FF7F"), unsafe_allow_html=True)
        rows2 = (
            _tbl_row("1H  CHANGE",   _pct_html(chg1h),  "#00FF7F", True)
            + _tbl_row("24H CHANGE", _pct_html(chg24),  "#00FF7F")
            + _tbl_row("7D  CHANGE", _pct_html(chg7d),  "#00FF7F")
            + _tbl_row("30D CHANGE", _pct_html(chg30d), "#00FF7F")
        )
        st.markdown(_tbl_wrap(rows2, "#00FF7F"), unsafe_allow_html=True)

        vol_mcap_ratio = (vol24 / mcap * 100) if (mcap and vol24 and mcap > 0) else None
        vm_color = (
            "#FF1493" if (vol_mcap_ratio or 0) > 50
            else ("#39FF14" if (vol_mcap_ratio or 0) > 10 else "#FF5F1F")
        )
        vm_note = (
            "OUTLIER — Unusually high activity. Something is happening."
            if (vol_mcap_ratio or 0) > 50
            else ("ELEVATED — Above average trading interest."
            if (vol_mcap_ratio or 0) > 10 else "NORMAL — Routine trading volume.")
        )
        st.markdown(_section_header("VOLUME SIGNAL", "#FF5F1F"), unsafe_allow_html=True)
        rows4 = (
            _tbl_row("VOL / MCAP %",
                     f'<span style="color:{vm_color};font-weight:700;">'
                     f'{vol_mcap_ratio:.2f}%</span>' if vol_mcap_ratio else "—",
                     "#FF5F1F", True)
            + _tbl_row("SIGNAL", f'<span style="color:{vm_color};">{vm_note}</span>', "#FF5F1F")
        )
        st.markdown(_tbl_wrap(rows4, "#FF5F1F"), unsafe_allow_html=True)

        st.markdown(_section_header("DEV ACTIVITY / COMMUNITY SIZE", "#8F00FF"), unsafe_allow_html=True)
        rows5 = (
            _tbl_row("DEV ACTIVITY",
                     f'{_num(commits)} <span style="font-size:9px;">COMMITS / 4 WKS</span>',
                     "#8F00FF", True)
            + _tbl_row("COMMUNITY SIZE",
                       f'{_num(tw_f)} <span style="font-size:9px;">TWITTER/X FOLLOWERS</span>',
                       "#8F00FF")
            + _tbl_row("STATUS",
                       '<span style="color:#FF3131;">NOT WORKING YET — TO BE LOOKED INTO</span>',
                       "#8F00FF")
        )
        st.markdown(_tbl_wrap(rows5, "#8F00FF"), unsafe_allow_html=True)

    # ABOUT THIS COIN — HTML cleaned, single font size, no Streamlit expander icon
    desc_raw = (detail.get("description") or {}).get("en", "")
    desc = _clean_text(desc_raw)
    if desc:
        LIMIT = 700
        if len(desc) > LIMIT:
            desc = desc[:LIMIT].rsplit(" ", 1)[0] + "…"
        st.markdown(
            f'<details class="about-box"><summary>ABOUT THIS COIN</summary>'
            f'<div class="about-body">{_esc(desc)}</div></details>',
            unsafe_allow_html=True,
        )

# Tab 2: Supply
with tab_supply:
    st.markdown(_section_header("SUPPLY METRICS", "#FF5F1F"), unsafe_allow_html=True)
    circ_pct = (circ_s / max_s * 100) if (circ_s and max_s and max_s > 0) else None
    sa, sb = st.columns(2)
    with sa:
        rows = (
            _tbl_row("CIRCULATING",  _fmt(circ_s, prefix="", dec=0), "#FF5F1F", True)
            + _tbl_row("TOTAL",      _fmt(total_s, prefix="", dec=0), "#FF5F1F")
            + _tbl_row("MAX SUPPLY", _fmt(max_s,  prefix="", dec=0) if max_s else "∞", "#FF5F1F")
        )
        st.markdown(_tbl_wrap(rows, "#FF5F1F"), unsafe_allow_html=True)
    with sb:
        fdv_mcap_ratio = (fdv / mcap) if (fdv and mcap and mcap > 0) else None
        fm_color = (
            "#FF3131" if (fdv_mcap_ratio or 0) > 5
            else ("#FF5F1F" if (fdv_mcap_ratio or 0) > 2 else "#39FF14")
        )
        fm_note = (
            "HIGH INFLATION RISK — Large portion of supply not yet in circulation."
            if (fdv_mcap_ratio or 0) > 5
            else ("MODERATE — Some future dilution expected."
            if (fdv_mcap_ratio or 0) > 2
            else "HEALTHY — FDV close to market cap.  Low dilution risk.")
        )
        rows2 = (
            _tbl_row("FDV / MCAP",
                     f'<span style="color:{fm_color};font-weight:700;">'
                     f'{fdv_mcap_ratio:.1f}×</span>' if fdv_mcap_ratio else "—",
                     "#FF5F1F", True)
            + _tbl_row("DILUTION RISK",
                       f'<span style="color:{fm_color};">{fm_note}</span>', "#FF5F1F")
        )
        st.markdown(_tbl_wrap(rows2, "#FF5F1F"), unsafe_allow_html=True)
    if circ_pct is not None:
        st.markdown(
            f'<div style="margin-top:14px;">'
            f'<div style="font-size:9px;color:#FFFFFF;text-transform:uppercase;'
            f'letter-spacing:.08em;margin-bottom:6px;">'
            f'CIRCULATING SUPPLY  {circ_pct:.1f}%  OF MAX</div>'
            f'<div style="background:#111111;height:8px;border:1px solid #222222;">'
            f'<div style="width:{circ_pct:.1f}%;height:100%;background:#FF5F1F;"></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

# Tab 3: Community
with tab_community:
    st.markdown(_section_header("COMMUNITY SCALE", "#FF00FF"), unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid #222;padding:8px 12px;margin-bottom:10px;'
        f'font-size:10px;color:#FFFFFF;">'
        f'SOURCE: CoinGecko community_data (free API). '
        f'Twitter/X data depends on CoinGecko\'s own scraping — may show 0 for some coins. '
        f'No direct X/Grok API is used. Telegram and Reddit data similarly from CoinGecko.</div>',
        unsafe_allow_html=True,
    )
    ca, cb = st.columns(2)
    with ca:
        rows = (
            _tbl_row("TWITTER / X",   _num(tw_f),     "#FF00FF", True)
            + _tbl_row("TELEGRAM",    _num(tg_f),     "#FF00FF")
            + _tbl_row("REDDIT SUBS", _num(reddit_s), "#FF00FF")
        )
        st.markdown(_tbl_wrap(rows, "#FF00FF"), unsafe_allow_html=True)
    with cb:
        largest = max(tw_f, tg_f, reddit_s, 1)
        if largest >= 1_000_000:
            comm_note = "TIER 1 — Massive established community.  Deep liquidity in retail."
            comm_c    = "#39FF14"
        elif largest >= 100_000:
            comm_note = "TIER 2 — Solid community base.  Meaningful organic distribution."
            comm_c    = "#FF5F1F"
        elif largest >= 10_000:
            comm_note = "TIER 3 — Growing community.  Still early-stage adoption."
            comm_c    = "#FF5F1F"
        else:
            comm_note = "MICRO — Very small community.  High influencer concentration risk."
            comm_c    = "#FF3131"
        rows2 = (
            _tbl_row("COMMUNITY TIER",
                     f'<span style="color:{comm_c};font-weight:700;">'
                     f'{comm_note.split("—")[0].strip()}</span>', "#FF00FF", True)
            + _tbl_row("CONTEXT",
                       f'<span style="color:{comm_c};font-size:10px;">{comm_note}</span>', "#FF00FF")
        )
        st.markdown(_tbl_wrap(rows2, "#FF00FF"), unsafe_allow_html=True)

    links     = detail.get("links") or {}
    tw_handle = links.get("twitter_screen_name", "")
    homepage  = (links.get("homepage") or [""])[0]
    subreddit = links.get("subreddit_url", "")
    if any([tw_handle, homepage, subreddit]):
        st.markdown(_section_header("OFFICIAL LINKS", "#FF00FF"), unsafe_allow_html=True)
        links_html = ""
        if homepage:
            links_html += f'<a href="{homepage}" target="_blank" style="color:#FF00FF;text-decoration:none;border:1px solid #FF00FF;padding:3px 10px;margin:3px;display:inline-block;font-size:10px;">🌐 WEBSITE</a>'
        if tw_handle:
            links_html += f'<a href="https://twitter.com/{tw_handle}" target="_blank" style="color:#FF00FF;text-decoration:none;border:1px solid #FF00FF;padding:3px 10px;margin:3px;display:inline-block;font-size:10px;">🐦 @{tw_handle.upper()}</a>'
        if subreddit:
            links_html += f'<a href="{subreddit}" target="_blank" style="color:#FF00FF;text-decoration:none;border:1px solid #FF00FF;padding:3px 10px;margin:3px;display:inline-block;font-size:10px;">🟠 REDDIT</a>'
        st.markdown(f'<div style="margin-top:6px;">{links_html}</div>', unsafe_allow_html=True)

# Tab 4: Developer
with tab_dev:
    st.markdown(_section_header("GITHUB DEVELOPER ACTIVITY", "#007FFF"), unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid #222;padding:8px 12px;margin-bottom:10px;'
        f'font-size:10px;color:#FFFFFF;">'
        f'SOURCE: CoinGecko developer_data (free API, pulled from GitHub). '
        f'Many coins show 0 — this means CoinGecko has no linked GitHub repo for this coin, '
        f'not necessarily that development is inactive. Check the official repo directly for confirmation.</div>',
        unsafe_allow_html=True,
    )
    da, db = st.columns(2)
    prs_merged   = dev.get("pull_requests_merged")     or 0
    contributors = dev.get("pull_request_contributors") or 0
    stars        = dev.get("stars")        or 0
    forks        = dev.get("forks")        or 0
    total_issues = dev.get("total_issues") or 0
    closed_issues= dev.get("closed_issues") or 0
    issue_close_pct = (closed_issues / total_issues * 100) if total_issues > 0 else None
    with da:
        rows = (
            _tbl_row("COMMITS (4 WEEKS)", _num(commits),      "#007FFF", True)
            + _tbl_row("PRs MERGED",      _num(prs_merged),   "#007FFF")
            + _tbl_row("CONTRIBUTORS",    _num(contributors), "#007FFF")
            + _tbl_row("STARS",           _num(stars),        "#007FFF")
            + _tbl_row("FORKS",           _num(forks),        "#007FFF")
        )
        st.markdown(_tbl_wrap(rows, "#007FFF"), unsafe_allow_html=True)
    with db:
        if commits >= 100:
            dev_note  = "HIGHLY ACTIVE — Strong ongoing development.  Low abandonment risk."
            dev_color = "#39FF14"
        elif commits >= 20:
            dev_note  = "ACTIVE — Regular development cadence."
            dev_color = "#FF5F1F"
        elif commits >= 1:
            dev_note  = "LOW — Minimal recent commits.  Monitor for stagnation."
            dev_color = "#FF5F1F"
        else:
            dev_note  = "INACTIVE — Zero commits in 4 weeks via CoinGecko.  Verify on GitHub directly."
            dev_color = "#FF3131"
        rows2 = (
            _tbl_row("DEV STATUS",
                     f'<span style="color:{dev_color};font-weight:700;">'
                     f'{dev_note.split("—")[0].strip()}</span>', "#007FFF", True)
            + _tbl_row("VERDICT",
                       f'<span style="color:{dev_color};font-size:10px;">{dev_note}</span>', "#007FFF")
            + _tbl_row("TOTAL ISSUES",   _num(total_issues),  "#007FFF")
            + _tbl_row("CLOSED ISSUES",  _num(closed_issues), "#007FFF")
            + _tbl_row("CLOSE RATE",
                       f'{issue_close_pct:.0f}%' if issue_close_pct else "—", "#007FFF")
        )
        st.markdown(_tbl_wrap(rows2, "#007FFF"), unsafe_allow_html=True)
    max_commits = 500
    commit_pct  = min(commits / max_commits * 100, 100)
    st.markdown(
        f'<div style="margin-top:14px;">'
        f'<div style="font-size:9px;color:#FFFFFF;text-transform:uppercase;'
        f'letter-spacing:.08em;margin-bottom:6px;">'
        f'4-WEEK COMMIT INTENSITY  ({commits} / {max_commits} max scale)</div>'
        f'<div style="background:#111111;height:8px;border:1px solid #222222;">'
        f'<div style="width:{commit_pct:.1f}%;height:100%;background:{dev_color};"></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

# Tab 5: Google Trends
with tab_trends:
    st.markdown(_section_header("GOOGLE TRENDS  —  RETAIL SEARCH CURIOSITY", "#FF3131"), unsafe_allow_html=True)
    if not PYTRENDS_OK:
        st.markdown(
            '<p style="color:#FFFFFF;font-size:12px;letter-spacing:.04em;">'
            'PYTRENDS NOT INSTALLED  —  RUN:  pip install pytrends</p>',
            unsafe_allow_html=True,
        )
    else:
        GPROP_MAP = {"WEB": "", "YOUTUBE": "youtube", "NEWS": "news", "IMAGES": "images"}
        PLAT_DESCS = {
            "WEB":     "Broadest retail interest — all Google web searches.",
            "YOUTUBE": "Video demand — tutorial and explainer curiosity on YouTube.",
            "NEWS":    "Formal financial news coverage and media attention.",
            "IMAGES":  "Visual search interest (lower signal for crypto assets).",
        }
        st.markdown(
            '<div style="font-size:9px;color:#FF3131;text-transform:uppercase;'
            'letter-spacing:.1em;margin-bottom:4px;">PLATFORM PROPERTY</div>',
            unsafe_allow_html=True,
        )
        plat_choice = st.radio(
            "platform", list(GPROP_MAP.keys()),
            horizontal=True, index=0,
            label_visibility="collapsed",
            key="trends_platform_radio",
        )
        cur_gprop = GPROP_MAP[plat_choice]
        st.markdown(
            f'<div style="font-size:10px;color:#FFFFFF;border:1px solid #222;'
            f'padding:6px 10px;margin-bottom:14px;">{PLAT_DESCS[plat_choice]}</div>',
            unsafe_allow_html=True,
        )

        with st.spinner("LOADING TRENDS DATA…"):
            tr = get_trends(coin_name, gprop=cur_gprop)

        gr1, gr2 = st.columns(2)
        with gr1:
            v7  = tr["7d"]
            t7c = "#39FF14" if v7 >= 60 else ("#FF5F1F" if v7 >= 30 else "#FF3131")
            st.markdown(
                f'<div style="border:1px solid {t7c};padding:16px;text-align:center;">'
                f'<div style="font-size:9px;color:{t7c};text-transform:uppercase;'
                f'letter-spacing:.1em;margin-bottom:8px;">7-DAY AVG INTEREST</div>'
                f'<div style="font-size:42px;font-weight:700;color:{t7c};">{v7}</div>'
                f'<div style="font-size:9px;color:#FFFFFF;margin-top:4px;">OUT OF 100</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with gr2:
            v30  = tr["30d"]
            t30c = "#39FF14" if v30 >= 60 else ("#FF5F1F" if v30 >= 30 else "#FF3131")
            st.markdown(
                f'<div style="border:1px solid {t30c};padding:16px;text-align:center;">'
                f'<div style="font-size:9px;color:{t30c};text-transform:uppercase;'
                f'letter-spacing:.1em;margin-bottom:8px;">30-DAY AVG INTEREST</div>'
                f'<div style="font-size:42px;font-weight:700;color:{t30c};">{v30}</div>'
                f'<div style="font-size:9px;color:#FFFFFF;margin-top:4px;">OUT OF 100</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown(_section_header("INTEREST BY REGION  —  TOP 20 COUNTRIES", "#FF3131"), unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:10px;color:#FFFFFF;border:1px solid #222;'
            'padding:6px 10px;margin-bottom:10px;">'
            'Relative search volume (0–100) by country. Higher bar = stronger regional demand. '
            'Pinpoints which global economies are driving current interest in this asset.</div>',
            unsafe_allow_html=True,
        )
        region_data = tr.get("region_data", {})
        if region_data:
            max_region = max(region_data.values(), default=1)
            region_html = ""
            for country, val in sorted(region_data.items(), key=lambda x: -x[1]):
                bar_w = int(val / max_region * 100)
                bar_c = "#39FF14" if val >= 70 else ("#FF5F1F" if val >= 40 else "#FF3131")
                region_html += (
                    f'<div style="display:flex;align-items:center;margin-bottom:5px;">'
                    f'<div style="width:130px;font-size:10px;color:#FFFFFF;'
                    f'text-transform:uppercase;flex-shrink:0;letter-spacing:.04em;">{country}</div>'
                    f'<div style="flex:1;background:#111;height:14px;border:1px solid #222;margin:0 8px;">'
                    f'<div style="width:{bar_w}%;height:100%;background:{bar_c};"></div>'
                    f'</div>'
                    f'<div style="width:28px;font-size:10px;color:{bar_c};font-weight:700;text-align:right;">{val}</div>'
                    f'</div>'
                )
            st.markdown(f'<div style="border:1px solid #FF3131;padding:12px;">{region_html}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">NO REGIONAL DATA RETURNED — GOOGLE MAY HAVE RATE-LIMITED THIS REQUEST.</div>', unsafe_allow_html=True)

        st.markdown(_section_header("TOP RELATED QUERIES", "#FF3131"), unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:10px;color:#FFFFFF;border:1px solid #222;'
            'padding:6px 10px;margin-bottom:10px;">'
            'High-volume search phrases users look up alongside this asset. '
            'Progress bar shows relative weight (0–100). Surfaces core retail narratives.</div>',
            unsafe_allow_html=True,
        )
        top_queries = tr.get("top_queries", [])
        if top_queries:
            tq_html = ""
            for tq in top_queries:
                bar_w = int(tq["value"])
                tq_html += (
                    f'<div style="margin-bottom:8px;">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:3px;">'
                    f'<span style="font-size:10px;color:#FFFFFF;text-transform:uppercase;letter-spacing:.04em;">{tq["query"]}</span>'
                    f'<span style="font-size:10px;color:#FF3131;font-weight:700;">{bar_w}</span>'
                    f'</div>'
                    f'<div style="background:#111;height:6px;border:1px solid #222;">'
                    f'<div style="width:{bar_w}%;height:100%;background:#FF3131;"></div>'
                    f'</div></div>'
                )
            st.markdown(f'<div style="border:1px solid #FF3131;padding:12px;">{tq_html}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">NO RELATED QUERY DATA RETURNED.</div>', unsafe_allow_html=True)

        st.markdown(_section_header("RISING RELATED QUERIES  —  BREAKOUT RADAR", "#FF3131"), unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:10px;color:#FFFFFF;border:1px solid #222;'
            'padding:6px 10px;margin-bottom:10px;">'
            'Sudden-growth search phrases. '
            '<span style="color:#FF3131;font-weight:700;">🔥 BREAKOUT</span> = &gt;5000% growth. '
            'Acts as a real-time radar for sudden market panic or FOMO forming right now.</div>',
            unsafe_allow_html=True,
        )
        rising_queries = tr.get("rising_queries", [])
        if not rising_queries and tr.get("rising"):
            rising_queries = [{"query": q_, "value": 100, "breakout": False} for q_ in tr["rising"]]
        if rising_queries:
            pills_html = ""
            for rq in rising_queries:
                is_breakout = rq.get("breakout") or rq["value"] >= 5000
                if is_breakout:
                    badge_bg, badge_txt, pill_border = "#FF3131", "🔥 BREAKOUT", "#FF3131"
                elif rq["value"] >= 1000:
                    badge_bg, badge_txt, pill_border = "#FF5F1F", f"+{rq['value']}%", "#FF5F1F"
                else:
                    badge_bg, badge_txt, pill_border = "#222", f"+{rq['value']}%", "#555"
                pills_html += (
                    f'<span style="display:inline-flex;align-items:center;'
                    f'border:1px solid {pill_border};padding:5px 10px;margin:4px;">'
                    f'<span style="font-size:10px;color:#FFFFFF;text-transform:uppercase;'
                    f'letter-spacing:.04em;margin-right:6px;">{rq["query"]}</span>'
                    f'<span style="font-size:9px;background:{badge_bg};color:#FFFFFF;'
                    f'font-weight:700;padding:2px 6px;">{badge_txt}</span>'
                    f'</span>'
                )
            st.markdown(f'<div style="border:1px solid #FF3131;padding:10px;">{pills_html}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">NO RISING QUERY DATA RETURNED.</div>', unsafe_allow_html=True)

        st.markdown(_section_header("RELATED TOPICS  —  MACRO THEME RADAR", "#FF3131"), unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:10px;color:#FFFFFF;border:1px solid #222;'
            'padding:6px 10px;margin-bottom:10px;">'
            'Macro themes, competitor assets, and broad concepts users explore alongside this coin. '
            'Tag size is proportional to relative search volume index.</div>',
            unsafe_allow_html=True,
        )
        related_topics = tr.get("related_topics", [])
        if related_topics:
            max_topic    = max((t["value"] for t in related_topics), default=1)
            topic_colors = ["#FF3131","#FF5F1F","#00FFFF","#39FF14","#FF00FF","#007FFF","#8F00FF"]
            bubbles_html = ""
            for ti, topic in enumerate(related_topics):
                tc       = topic_colors[ti % len(topic_colors)]
                norm_val = topic["value"] / max_topic
                fsize    = int(10 + norm_val * 8)
                pad_h    = int(6 + norm_val * 10)
                bubbles_html += (
                    f'<span style="display:inline-block;border:1px solid {tc};color:{tc};'
                    f'font-size:{fsize}px;font-weight:700;padding:4px {pad_h}px;margin:4px;'
                    f'text-transform:uppercase;letter-spacing:.06em;">'
                    f'{topic["title"]}</span>'
                )
            st.markdown(f'<div style="border:1px solid #FF3131;padding:12px;">{bubbles_html}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">NO RELATED TOPIC DATA RETURNED.</div>', unsafe_allow_html=True)
