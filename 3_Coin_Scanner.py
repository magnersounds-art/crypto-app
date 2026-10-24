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

Round 2:
9.  Table rows: label and value sit close together, bigger fonts
10. SUPPLY METRICS moved under MARKET; the SUPPLY tab is gone
11. DEVELOPER tab + dev box = live GitHub API data (repos come from CoinGecko's links)
12. COMMUNITY tab = honest data status + live Reddit panel (shows WAITING FOR REDDIT LOGIN until
    REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are in .env) + official links incl. GitHub
13. GOOGLE TRENDS rebuilt: shared panel (trends_ui.py) + one guarded route (trends_core.py):
    manual LOAD/REFRESH only, spacing + 429 cooldown, saved cache, errors shown as errors (never zeros)
Needs these files in the same folder:  trends_core.py  trends_ui.py  devsocial_core.py
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")                      # optional: 60 -> 5,000 requests/hour
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "script:celestial-intel-scanner:v1.0 (personal use)")

import sys
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
try:
    import trends_core
    import trends_ui
    import devsocial_core
    CORE_OK, CORE_ERR = True, ""
except ImportError as _e:
    CORE_OK, CORE_ERR = False, str(_e)

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
        f'<table style="width:100%;border-collapse:collapse;font-size:15px;">'
        f'{rows_html}</table></div>'
    )

def _tbl_row(label, value, color, header=False):
    """Label column shrinks to its longest label (width:1%) so the value sits right next to it."""
    lc = color if header else "#FFFFFF"
    return (
        f'<tr style="border-bottom:1px solid #333333;">'
        f'<td style="padding:9px 8px 9px 14px;color:{lc};text-transform:uppercase;'
        f'font-size:12px;white-space:nowrap;width:1%;">{label}</td>'
        f'<td style="padding:9px 14px 9px 8px;color:#FFFFFF;text-align:left;font-size:15px;">{value}</td>'
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


@st.cache_resource
def _get_sources():
    """One GitHub source + one Reddit source for the whole app (keeps cache + rate-limit pauses)."""
    gh = devsocial_core.CachedSource(lambda urls: devsocial_core.fetch_github(urls, GITHUB_TOKEN), devsocial_core.GH_TTL)
    rd = devsocial_core.CachedSource(
        lambda sub, term: devsocial_core.fetch_reddit(sub, term, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT),
        devsocial_core.REDDIT_TTL)
    return gh, rd


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

    # GitHub (dev activity) + Reddit (community): cached, no retries, pause on rate limit
    gh_res = rd_res = None
    if coin and detail and CORE_OK:
        _links = detail.get("links") or {}
        repo_urls = tuple([u for u in ((_links.get("repos_url") or {}).get("github") or []) if u][:devsocial_core.MAX_REPOS])
        _gh_src, _rd_src = _get_sources()
        gh_res = _gh_src.get(repo_urls, repo_urls)
        _sub = devsocial_core.parse_subreddit(_links.get("subreddit_url", ""))
        _term = (f'"{coin["name"]}"' if coin["name"].lower() == coin["symbol"].lower()
                 else f'"{coin["name"]}" OR {coin["symbol"]}')
        rd_res = _rd_src.get((_sub, _term), _sub, _term)

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
tg_f     = cd.get("telegram_channel_user_count") or 0
reddit_s = cd.get("reddit_subscribers") or 0
price_str = _price_full(price)
links   = detail.get("links") or {}


# ── helpers for the GitHub / Reddit panels ────────────────────────────────────
def _note_box(title, text, color="#FF5F1F"):
    return (
        f'<div style="border:1px solid {color};padding:10px 14px;margin:6px 0;font-size:12px;'
        f'color:#FFFFFF;line-height:1.6;"><b style="color:{color};">{title}</b> — {text}</div>'
    )

def _fmt_iso(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{dt.strftime('%d %b %Y')} ({_age(dt)})"
    except Exception:
        return "—"

def _gh_rate_txt(rate):
    if not rate: return ""
    return f"GITHUB API QUOTA: {rate['remaining']}/{rate['limit']} LEFT THIS HOUR"

def _gh_best_commits(gh):
    """-> (best_4w_commits_or_None, repos_list)"""
    if not gh or not gh.get("ok") or not gh["data"] or gh["data"].get("no_repo"):
        return None, []
    repos = gh["data"]["repos"]
    vals = [r["commits_4w"] for r in repos if r.get("commits_4w") is not None]
    return (max(vals) if vals else None), repos

def _dev_activity_html(gh):
    if not CORE_OK:  return '<span style="color:#FF3131;">devsocial_core.py MISSING</span>'
    if gh is None:   return "—"
    if not gh.get("ok"):
        return f'<span style="color:#FF3131;">{_esc(gh["error"]["message"])}</span>'
    if gh["data"].get("no_repo"):
        return "NO GITHUB REPO LINKED ON COINGECKO"
    best, _ = _gh_best_commits(gh)
    if best is None:
        return "COMMIT DATA NOT READY — SCAN AGAIN IN A MINUTE"
    return f'{_num(best)} <span style="font-size:11px;">COMMITS / 4 WKS (GITHUB)</span>'

def _community_html():
    if rd_res and rd_res.get("ok") and rd_res["data"].get("subreddit"):
        sub = rd_res["data"]["subreddit"]
        return f'{_num(sub["subscribers"])} <span style="font-size:11px;">REDDIT SUBSCRIBERS (LIVE)</span>'
    if reddit_s:
        return f'{_num(reddit_s)} <span style="font-size:11px;">REDDIT SUBSCRIBERS (COINGECKO)</span>'
    if tg_f:
        return f'{_num(tg_f)} <span style="font-size:11px;">TELEGRAM MEMBERS (COINGECKO)</span>'
    if rd_res and not rd_res.get("ok") and rd_res["error"]["kind"] == "config":
        return '<span style="color:#FF5F1F;">WAITING FOR REDDIT LOGIN</span>'
    if rd_res and not rd_res.get("ok"):
        return f'<span style="color:#FF3131;">{_esc(rd_res["error"]["message"])}</span>'
    return "NO COMMUNITY DATA FOUND"


st.markdown(_section_header("DETAILED DATA", "#FF5F1F"), unsafe_allow_html=True)

tab_mkt, tab_community, tab_dev, tab_trends = st.tabs([
    "📊  MARKET", "👥  COMMUNITY", "💻  DEVELOPER", "📈  GOOGLE TRENDS",
])

# ── Tab 1: Market (+ Supply Metrics) ──────────────────────────────────────────
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
            _tbl_row("DEV ACTIVITY",   _dev_activity_html(gh_res), "#8F00FF", True)
            + _tbl_row("COMMUNITY SIZE", _community_html(),        "#8F00FF")
            + _tbl_row("DETAIL", '<span style="font-size:12px;">SEE THE DEVELOPER AND COMMUNITY TABS</span>', "#8F00FF")
        )
        st.markdown(_tbl_wrap(rows5, "#8F00FF"), unsafe_allow_html=True)

    # SUPPLY METRICS (moved here from its own tab)
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
            f'<div style="font-size:11px;color:#FFFFFF;text-transform:uppercase;'
            f'letter-spacing:.08em;margin-bottom:6px;">'
            f'CIRCULATING SUPPLY  {circ_pct:.1f}%  OF MAX</div>'
            f'<div style="background:#111111;height:8px;border:1px solid #222222;">'
            f'<div style="width:{circ_pct:.1f}%;height:100%;background:#FF5F1F;"></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # ABOUT THIS COIN — HTML cleaned, single font size, no Streamlit expander icon
    desc = _clean_text((detail.get("description") or {}).get("en", ""))
    if desc:
        LIMIT = 700
        if len(desc) > LIMIT:
            desc = desc[:LIMIT].rsplit(" ", 1)[0] + "…"
        st.markdown(
            f'<details class="about-box"><summary>ABOUT THIS COIN</summary>'
            f'<div class="about-body">{_esc(desc)}</div></details>',
            unsafe_allow_html=True,
        )

# ── Tab 2: Community ──────────────────────────────────────────────────────────
with tab_community:
    st.markdown(_section_header("COMMUNITY SCALE", "#FF00FF"), unsafe_allow_html=True)
    st.markdown(
        '<div style="border:1px solid #222;padding:8px 12px;margin-bottom:10px;font-size:11px;color:#FFFFFF;line-height:1.6;">'
        'SOURCES: Reddit official API (live, needs login keys) · CoinGecko (Telegram / Reddit counts, often empty). '
        'X/Twitter follower counts are NOT available: CoinGecko removed that field in May 2025 and X has no free read API. '
        'Missing data is shown as missing — never as a small community.</div>',
        unsafe_allow_html=True,
    )
    ca, cb = st.columns(2)
    with ca:
        rows = (
            _tbl_row("TWITTER / X", '<span style="color:#FF5F1F;font-size:12px;">NOT AVAILABLE (NO FREE SOURCE)</span>', "#FF00FF", True)
            + _tbl_row("TELEGRAM", _num(tg_f) if tg_f else '<span style="font-size:12px;">NOT REPORTED BY COINGECKO</span>', "#FF00FF")
            + _tbl_row("REDDIT (CG)", _num(reddit_s) if reddit_s else '<span style="font-size:12px;">NOT REPORTED BY COINGECKO</span>', "#FF00FF")
        )
        st.markdown(_tbl_wrap(rows, "#FF00FF"), unsafe_allow_html=True)

    with cb:
        st.markdown(_section_header("REDDIT — LIVE", "#FF5F1F"), unsafe_allow_html=True)
        waiting = (rd_res is not None and not rd_res.get("ok") and rd_res["error"]["kind"] == "config")
        if not CORE_OK:
            status = '<span style="color:#FF3131;">devsocial_core.py MISSING NEXT TO THIS PAGE</span>'
        elif waiting:
            status = '<span style="color:#FF5F1F;font-weight:700;">WAITING FOR REDDIT LOGIN</span>'
        elif rd_res and not rd_res.get("ok"):
            status = f'<span style="color:#FF3131;">{_esc(rd_res["error"]["message"])}</span>'
        elif rd_res:
            status = '<span style="color:#39FF14;font-weight:700;">CONNECTED</span>' + (
                ' <span style="font-size:11px;">(CACHED)</span>' if rd_res.get("from_cache") else "")
        else:
            status = "—"
        sub = (rd_res or {}).get("data", {}) or {}
        sub_about = sub.get("subreddit") if sub else None
        mentions = sub.get("mentions") if sub else None
        sub_name = devsocial_core.parse_subreddit(links.get("subreddit_url", "")) if CORE_OK else None
        rows_r = (
            _tbl_row("STATUS", status, "#FF5F1F", True)
            + _tbl_row("SUBREDDIT", f'r/{_esc(sub_name)}' if sub_name else "NONE LINKED ON COINGECKO", "#FF5F1F")
            + _tbl_row("SUBSCRIBERS", _num(sub_about["subscribers"]) if sub_about else "—", "#FF5F1F")
            + _tbl_row("ACTIVE NOW", _num(sub_about["active"]) if sub_about and sub_about.get("active") is not None else "—", "#FF5F1F")
            + _tbl_row("POSTS MENTIONING (7D)", _num(mentions["count"]) if mentions else "—", "#FF5F1F")
        )
        st.markdown(_tbl_wrap(rows_r, "#FF5F1F"), unsafe_allow_html=True)

    if waiting:
        st.markdown(_note_box(
            "TO TURN THIS ON",
            'Create a Reddit app at reddit.com/prefs/apps (type "script"), then add REDDIT_CLIENT_ID and '
            'REDDIT_CLIENT_SECRET to your .env and rescan. Until then the rows above stay empty on purpose.',
            "#FF5F1F"), unsafe_allow_html=True)
    elif rd_res and not rd_res.get("ok") and not waiting:
        st.markdown(_note_box("REDDIT ERROR", _esc(rd_res["error"]["message"]), "#FF3131"), unsafe_allow_html=True)
    if rd_res and rd_res.get("ok"):
        for n in (rd_res["data"].get("notes") or []):
            st.markdown(_note_box("NOTE", _esc(n), "#FF5F1F"), unsafe_allow_html=True)
        if mentions and mentions["count"]:
            pa, pb = st.columns(2)
            with pa:
                st.markdown(_section_header("MOST ACTIVE POSTERS (7D)", "#FF00FF"), unsafe_allow_html=True)
                rows_a = "".join(
                    _tbl_row(f'u/{_esc(a)}', f'{n} POST{"S" if n != 1 else ""}', "#FF00FF", i == 0)
                    for i, (a, n) in enumerate(mentions["top_authors"])
                ) or _tbl_row("—", "NONE", "#FF00FF")
                st.markdown(_tbl_wrap(rows_a, "#FF00FF"), unsafe_allow_html=True)
            with pb:
                st.markdown(_section_header("TOP POSTS (7D)", "#FF00FF"), unsafe_allow_html=True)
                posts_html = "".join(
                    _item_html(p_["title"], p_["url"], "#FF00FF",
                               f'<span>r/{_esc(p_["subreddit"])}</span><span>u/{_esc(p_["author"])}</span>'
                               f'<span>{p_["score"]} pts</span><span>{p_["comments"]} comments</span>', "")
                    for p_ in mentions["top_posts"]
                )
                st.markdown(f'<div style="border:1px solid #FF00FF;padding:6px 10px;">{posts_html}</div>', unsafe_allow_html=True)

    # OFFICIAL LINKS (now includes GitHub + Telegram)
    tw_handle = links.get("twitter_screen_name", "")
    homepage  = (links.get("homepage") or [""])[0]
    subreddit = links.get("subreddit_url", "")
    gh_links  = [u for u in ((links.get("repos_url") or {}).get("github") or []) if u]
    tg_id     = links.get("telegram_channel_identifier", "")
    st.markdown(_section_header("OFFICIAL LINKS", "#FF00FF"), unsafe_allow_html=True)
    chips = []
    def _chip(url, text):
        chips.append(
            f'<span style="color:#FF00FF;"><a href="{_esc(url)}" target="_blank" '
            f'style="text-decoration:none;border:1px solid #FF00FF;padding:4px 12px;margin:3px;'
            f'display:inline-block;font-size:12px;">{text}</a></span>')
    if homepage:  _chip(homepage, "🌐 WEBSITE")
    if tw_handle: _chip(f"https://twitter.com/{tw_handle}", f"🐦 @{_esc(tw_handle.upper())}")
    if subreddit: _chip(subreddit, "🟠 REDDIT")
    if tg_id:     _chip(f"https://t.me/{tg_id}", "✈ TELEGRAM")
    for u in gh_links[:5]:
        pr = devsocial_core.parse_repo_url(u) if CORE_OK else None
        _chip(u, f"💻 GITHUB: {_esc('/'.join(pr)) if pr else 'REPO'}")
    if chips:
        st.markdown(f'<div style="margin-top:6px;">{"".join(chips)}</div>', unsafe_allow_html=True)
    else:
        st.markdown(_note_box("NO LINKS", "CoinGecko lists no official links for this coin."), unsafe_allow_html=True)
    if not gh_links:
        st.markdown(_note_box("NO GITHUB LINK", "CoinGecko has no GitHub repository listed for this coin."), unsafe_allow_html=True)

# ── Tab 3: Developer (live GitHub) ────────────────────────────────────────────
with tab_dev:
    st.markdown(_section_header("GITHUB DEVELOPER ACTIVITY", "#007FFF"), unsafe_allow_html=True)
    rate_txt = _gh_rate_txt((gh_res or {}).get("rate"))
    st.markdown(
        '<div style="border:1px solid #222;padding:8px 12px;margin-bottom:10px;font-size:11px;color:#FFFFFF;line-height:1.6;">'
        'SOURCE: GitHub official API, live. Repositories come from the links CoinGecko lists for the coin '
        f'(first {devsocial_core.MAX_REPOS if CORE_OK else 2}). Cached 1 hour. '
        '"4 WKS" = the last four weekly buckets GitHub reports (the newest is the week still running). '
        + (f'<b style="color:#007FFF;">{rate_txt}</b>' if rate_txt else
           'Add GITHUB_TOKEN to .env to raise the limit from 60 to 5,000 requests/hour.') + '</div>',
        unsafe_allow_html=True,
    )
    if not CORE_OK:
        st.markdown(_note_box("MODULE MISSING", f"devsocial_core.py not found next to this page ({_esc(CORE_ERR)})", "#FF3131"), unsafe_allow_html=True)
    elif gh_res is None:
        st.markdown(_note_box("NO DATA", "GitHub was not queried."), unsafe_allow_html=True)
    elif not gh_res.get("ok"):
        st.markdown(_note_box("GITHUB ERROR", _esc(gh_res["error"]["message"]), "#FF3131"), unsafe_allow_html=True)
    elif gh_res["data"].get("no_repo"):
        st.markdown(_note_box(
            "NO GITHUB REPO LINKED",
            "CoinGecko lists no GitHub repository for this coin. That can mean the project is closed-source, "
            "or simply that CoinGecko has no link. Development activity cannot be verified from here.", "#FF5F1F"),
            unsafe_allow_html=True)
    else:
        repos = gh_res["data"]["repos"]
        for e in gh_res["data"].get("repo_errors", []):
            st.markdown(_note_box("REPO ERROR", _esc(e["message"]), "#FF3131"), unsafe_allow_html=True)
        for r in repos:
            info = r["info"]
            st.markdown(_section_header(f'{_esc(r["owner"])}/{_esc(r["repo"])}', "#007FFF"), unsafe_allow_html=True)
            c4  = r["commits_4w"]
            cc  = ("100+" if r["contributors_capped"] else _num(r["contributors"])) if r["contributors"] is not None else "—"
            rows = (
                _tbl_row("COMMITS (4 WKS)",   _num(c4) if c4 is not None else "—",                "#007FFF", True)
                + _tbl_row("COMMITS (52 WKS)", _num(r["commits_52w"]) if r["commits_52w"] is not None else "—", "#007FFF")
                + _tbl_row("PRs MERGED (4 WKS)", _num(r["prs_merged_4w"]) if r["prs_merged_4w"] is not None else "—", "#007FFF")
                + _tbl_row("CONTRIBUTORS",    cc,                                                  "#007FFF")
                + _tbl_row("STARS",           _num(info["stars"]),                                 "#007FFF")
                + _tbl_row("FORKS",           _num(info["forks"]),                                 "#007FFF")
                + _tbl_row("OPEN ISSUES + PRs", _num(info["open_issues_and_prs"]),                 "#007FFF")
                + _tbl_row("LAST PUSH",       _fmt_iso(info["pushed_at"]),                         "#007FFF")
                + _tbl_row("ARCHIVED",        "YES — READ-ONLY" if info["archived"] else "NO",      "#007FFF")
            )
            st.markdown(_tbl_wrap(rows, "#007FFF"), unsafe_allow_html=True)
            for n in r["notes"]:
                st.markdown(_note_box("NOTE", _esc(n), "#FF5F1F"), unsafe_allow_html=True)

        best, _ = _gh_best_commits(gh_res)
        if best is None:
            dev_note, dev_color = "UNKNOWN — GitHub has not returned commit statistics yet.  Scan again in a minute.", "#FF5F1F"
        elif best >= 100:
            dev_note, dev_color = "HIGHLY ACTIVE — Strong ongoing development.", "#39FF14"
        elif best >= 20:
            dev_note, dev_color = "ACTIVE — Regular development cadence.", "#39FF14"
        elif best >= 1:
            dev_note, dev_color = "LOW — Few recent commits.  Monitor for stagnation.", "#FF5F1F"
        else:
            dev_note, dev_color = "NO COMMITS IN THE LAST 4 WEEKS (verified on GitHub).  Check LAST PUSH above.", "#FF3131"
        st.markdown(_section_header("DEV STATUS", "#007FFF"), unsafe_allow_html=True)
        st.markdown(_tbl_wrap(
            _tbl_row("VERDICT", f'<span style="color:{dev_color};font-weight:700;">{dev_note}</span>', "#007FFF", True),
            "#007FFF"), unsafe_allow_html=True)
        if best is not None:
            commit_pct = min(best / 500 * 100, 100)
            st.markdown(
                f'<div style="margin-top:14px;">'
                f'<div style="font-size:11px;color:#FFFFFF;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">'
                f'4-WEEK COMMIT INTENSITY  ({best} / 500 max scale, most active repo)</div>'
                f'<div style="background:#111111;height:8px;border:1px solid #222222;">'
                f'<div style="width:{commit_pct:.1f}%;height:100%;background:{dev_color};"></div></div></div>',
                unsafe_allow_html=True,
            )

# ── Tab 4: Google Trends — shared panel (manual button, shared cache + cooldown) ──
with tab_trends:
    if not CORE_OK:
        st.markdown(_note_box("MODULE MISSING", f"trends_core.py / trends_ui.py not found next to this page ({_esc(CORE_ERR)})", "#FF3131"), unsafe_allow_html=True)
    else:
        trends_ui.render_trends_panel(coin_name, key_prefix="crypto", coin_id=coin["id"])
