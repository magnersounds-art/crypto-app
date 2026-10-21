"""
NEWS:    All RSS articles clustered by category. Loads instantly — no API.
YOUTUBE: Verified curated channel network filtering. 
         Uses precise API Channel IDs for flawless, high-integrity results.
         Fetches latest videos per channel, then bins locally by keyword —
         no keyword filtering on the API side means zero dropped videos.
         Asynchronous network handling ensures instant column loading.
"""

import streamlit as st
import feedparser
import pandas as pd
import requests
import os
import logging
import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dotenv import load_dotenv

# Shill Tracker logging (optional — failures never break the feed UI)
try:
    from logic.hype_scorer import score_hype
    from logic.shill_db import init_db, log_headline, log_price_snapshot
    from market_data import get_btc_price
    _SHILL_OK = True
except Exception as _shill_imp_err:
    _SHILL_OK = False
    logging.getLogger(__name__).warning("Shill Tracker imports unavailable: %s", _shill_imp_err)

logger = logging.getLogger(__name__)

BASE_DIR        = BASE_DIR = Path(__file__).parent.resolve()
RSS_PATH        = BASE_DIR / "rss_active.csv"
load_dotenv(BASE_DIR / ".env")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/search"
NEW_HOURS       = 6
NEW_7D_HOURS    = 168  # 7 days
ARTICLE_WINDOW_HOURS = 24  # articles older than this are dropped entirely

# ── Session: last login tracker ───────────────────────────────────────────────
if "last_login_time" not in st.session_state:
    st.session_state["last_login_time"] = datetime.now(timezone.utc)
LAST_LOGIN = st.session_state["last_login_time"]

# ── Colours ───────────────────────────────────────────────────────────────────
C_BG      = "#000000"
C_WHITE   = "#FFFFFF"
C_MUTED   = "#555555"
C_BORDER  = "#222222"
C_NEWS    = "#39FF14"
C_YT      = "#A855F7"
C_NEW     = "#FF1493"
C_CARD_BG = "#0a0a0a"

CATEGORIES = {
    "All":             {"colour": "#FFFFFF", "keywords": []},
    "Privacy":         {"colour": "#8F00FF", "keywords": ["monero","xmr","zcash","zec","privacy coin","dash","secret network","pirate chain","arrr"]},
    "Memes":           {"colour": "#FF3131", "keywords": ["meme","memecoin","dogecoin","doge","shiba","pepe","bonk","wif","floki","brett","pump.fun"]},
    "DePIN":           {"colour": "#CCFF00", "keywords": ["depin","helium","hnt","iotex","hivemapper","decentralized physical","dimo"]},
    "Gaming":          {"colour": "#FF00FF", "keywords": ["gaming","gamefi","immutable","gala","axie","beam","pixels","web3 game"]},
    "Layer 1":         {"colour": "#FF5F1F", "keywords": ["solana","avalanche","sui","aptos","near protocol","cosmos","toncoin","layer 1","l1"]},
    "ISO 20022":       {"colour": "#007FFF", "keywords": ["xrp","ripple","stellar","xlm","algorand","algo","quant","qnt","hedera","hbar","iota","iso 20022"]},
    "RWA":             {"colour": "#00FF7F", "keywords": ["real world asset","rwa","ondo","tokenized","tokenised","mantra","pendle","centrifuge"]},
    "AI":              {"colour": "#00FFFF", "keywords": ["artificial intelligence","ai agent","fetch-ai","render","bittensor","singularitynet","ocean protocol","akash"]},
    "ETH & DeFi":      {"colour": "#A855F7", "keywords": ["ethereum","eth","defi","uniswap","aave","curve","layer 2","l2","staking","gas","eip","hyperliquid","lido"]},
    "Bitcoin":         {"colour": "#F7931A", "keywords": ["bitcoin","btc","lightning","halving","mining","hash rate","satoshi"]},
    "Hacks & Scams":   {"colour": "#FF4500", "keywords": ["hack","exploit","scam","rug pull","phishing","stolen","breach","vulnerability","fraud"]},
    "Regulation":      {"colour": "#FF6B6B", "keywords": ["sec","regulation","lawsuit","congress","senate","ban","legal","compliance","cbdc","policy","cftc","legislation"]},
    "Macro & Fed":     {"colour": "#38BDF8", "keywords": ["federal reserve","fed","interest rate","inflation","cpi","liquidity","macro","gdp","powell","treasury","yield"]},
    "Market Analysis": {"colour": "#FFD700", "keywords": ["price","resistance","support","breakout","compression","volatility","rally","correction","technical","chart","ema","rsi"]},
    "General":         {"colour": "#555555", "keywords": []},
}

# ── Explicit API Channel ID Mappings ──────────────────────────────────────────
TRUSTED_CHANNELS = {
    "Crypto Banter":          "UCN9Nj4tjXbVTLYWN0EKly_Q",
    "Raoul Pal Journeyman":   "UCGXWKlq1Oxr3ddEtmKhAkPg",
    "Waters Above":           "UC2r1oKJFKGNw5THNPNHwBtQ",
    "Bankless":               "UCAl9Ld79qaZxp9JzEOwd3aA",
    "InvestAnswers":          "UClgJyzwGs-GyaNxUHcLZrkg",
    "jsnip4":                 "UCYI2mPvsSXz99QPAVAPIG5Q",
    "Neil McCoy-Ward":        "UC3o2eSpoecewhemY1f2ZI-A",
    "CTO Larsson":            "UCFU-BE5HRJoudqIz1VDKlhQ",
    "VirtualBacon":           "UCcrEA_xd9Ldf1C8DIJYdyyA",
    "Paul Barron Network":    "UC4VPa7EOvObpyCRI4YKRQRw",
    "World Crypto Network":   "UCR9gdpWisRwnk_k23GsHfcA",
    "Kitco News":             "UC9ijza42jVR3T6b8bColgvg",
    "Ivan on Tech":           "UCrYmtJBtLdtm2ov84ulV-yg",
    "Altcoin Daily":          "UCbLhGKVY-bJPcawebgtNfbw",
    "Boxmining":              "UCxODjeUwZHk3p-7TU-IsDOA",
}


def assign_category(title: str) -> str:
    tl = title.lower()
    for cat, cfg in CATEGORIES.items():
        if cat in ("All", "General"):
            continue
        if any(kw in tl for kw in cfg["keywords"]):
            return cat
    return "General"


# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
.stApp, .main, section.main, .block-container {{
    background-color: {C_BG} !important;
    color: {C_WHITE} !important;
}}
.main, section.main, .block-container {{
    font-family: 'Courier New', monospace !important;
}}
section[data-testid="stSidebar"] {{
    background-color: #050505 !important;
    border-right: 1px solid {C_BORDER} !important;
}}
[data-testid="column"] > div {{ border: none !important; }}
[data-testid="stVerticalBlock"] > div[style*="border"] {{ border: none !important; }}
div[data-testid="stHorizontalBlock"] > div {{ border: none !important; }}
#MainMenu, footer, header {{ visibility: hidden; }}
div[data-testid="stDecoration"] {{ display: none; }}
.block-container {{ padding-top: 1.5rem; }}
.main h1, .main h2, .main h3, .main h4,
.block-container h1, .block-container h2, .block-container h3, .block-container h4 {{
    font-family: 'Courier New', monospace !important;
    color: {C_WHITE} !important;
    font-weight: 700 !important;
}}

.stats-news-box {{
    border: 2px solid {C_NEWS}; border-radius: 6px; background: {C_CARD_BG};
    padding: 10px 16px; margin-bottom: 8px; font-family: 'Courier New', monospace;
    font-size: 12px; display: flex; flex-wrap: wrap; gap: 0 6px; align-items: center;
}}
.stats-yt-box {{
    border: 2px solid {C_YT}; border-radius: 6px; background: {C_CARD_BG};
    padding: 10px 16px; margin-bottom: 18px; font-family: 'Courier New', monospace;
    font-size: 12px; display: flex; flex-wrap: wrap; gap: 0 6px; align-items: center;
}}
.slbl-news {{ color: {C_NEWS}; font-weight: 700; font-size: 14px; margin-right: 6px; }}
.slbl-yt   {{ color: {C_YT};  font-weight: 700; font-size: 14px; margin-right: 6px; }}
.sval-news {{ color: {C_NEWS}; font-weight: 700; }}
.sval-yt   {{ color: {C_YT};  font-weight: 700; }}
.sval-new-login {{ color: {C_NEW}; font-weight: 700; }}
.sdiv      {{ color: #444; margin: 0 8px; }}
.skey      {{ color: {C_WHITE}; margin-right: 3px; }}

.news-box {{ border: 2px solid {C_NEWS}; border-radius: 8px; background: {C_CARD_BG}; padding: 14px 16px; }}
.yt-box {{ border: 2px solid {C_YT}; border-radius: 8px; background: {C_CARD_BG}; padding: 14px 16px; }}
.chdr-news {{
    color: {C_NEWS}; font-family: 'Courier New', monospace; font-size: 15px; font-weight: 700;
    border-bottom: 2px solid {C_NEWS}; padding-bottom: 8px; margin-bottom: 12px;
}}
.chdr-yt {{
    color: {C_YT}; font-family: 'Courier New', monospace; font-size: 15px; font-weight: 700;
    border-bottom: 2px solid {C_YT}; padding-bottom: 8px; margin-bottom: 12px;
}}

.art {{ padding: 6px 0 7px 10px; border-bottom: 1px solid #1a1a1a; font-family: 'Courier New', monospace; }}
.art-new-news   {{ font-size: 20px; color: {C_NEW};   line-height: 1.4; }}
.art-old-news   {{ font-size: 20px; color: {C_NEWS};  line-height: 1.4; }}
.art-new-news a {{ color: {C_NEW};   text-decoration: none; }}
.art-old-news a {{ color: {C_NEWS};  text-decoration: none; }}
.art-new-news a:hover {{ color: {C_WHITE}; }}
.art-old-news a:hover {{ color: {C_WHITE}; }}
.art-new-yt   {{ font-size: 13px; color: {C_NEW};  line-height: 1.4; }}
.art-old-yt   {{ font-size: 13px; color: {C_YT};   line-height: 1.4; }}
.art-new-yt a {{ color: {C_NEW};  text-decoration: none; }}
.art-old-yt a {{ color: {C_YT};  text-decoration: none; }}
.art-new-yt a:hover {{ color: {C_WHITE}; }}
.art-old-yt a:hover {{ color: {C_WHITE}; }}

.art-meta  {{ font-size: 11px; color: {C_WHITE}; margin-top: 3px; display: flex; gap: 10px; flex-wrap: wrap; }}
.msrc  {{ color: {C_WHITE}; }}
.mw    {{ color: #f59e0b; }}
.mtime {{ color: {C_WHITE}; }}

.nbadge {{ background: {C_NEW}; color: #000000; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: 700; margin-left: 5px; vertical-align: middle; }}
.newlogin-badge {{ background: {C_NEW}22; color: {C_NEW}; border: 1px solid {C_NEW}44; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: 700; margin-left: 5px; vertical-align: middle; }}

div.stButton > button {{ background: transparent !important; color: {C_WHITE} !important; border: 1px solid {C_BORDER} !important; font-family: 'Courier New', monospace !important; font-size: 11px !important; }}
div.stButton > button:hover {{ border-color: {C_NEWS} !important; color: {C_NEWS} !important; }}
.stExpander {{ background: {C_BG} !important; border: 1px solid {C_BORDER} !important; }}
.stExpander summary {{ font-family: 'Courier New', monospace !important; color: {C_WHITE} !important; background: {C_BG} !important; }}

.legend {{
    display: flex; flex-wrap: wrap; gap: 20px; background: {C_CARD_BG}; border: 1px solid {C_BORDER};
    border-radius: 6px; padding: 8px 16px; margin-top: 14px; font-family: 'Courier New', monospace; font-size: 12px; color: {C_WHITE};
}}
</style>
""", unsafe_allow_html=True)


# ── Data loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_sources():
    if not RSS_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(RSS_PATH, on_bad_lines="skip", engine="python")
    df.columns = [c.strip().lower() for c in df.columns]
    df["category"] = df["category"].str.strip().str.lower().fillna("crypto")
    df["weight"]   = pd.to_numeric(df.get("weight", 3), errors="coerce").fillna(3)
    return df


@st.cache_data(ttl=600)
def fetch_all_articles(source_tuples: tuple):
    articles = []
    now = datetime.now(timezone.utc)
    for name, url, weight, category in source_tuples:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                pub_dt, pub_str = None, ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub_dt  = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                        pub_str = pub_dt.strftime("%d %b  %H:%M")
                    except Exception:
                        pass

                if pub_dt is None or (now - pub_dt) > timedelta(hours=ARTICLE_WINDOW_HOURS):
                    continue

                articles.append({
                    "title":          title,
                    "link":           entry.get("link", "#"),
                    "pub_dt":         pub_dt,
                    "pub_str":        pub_str,
                    "is_new":         pub_dt is not None and (now - pub_dt) < timedelta(hours=NEW_HOURS),
                    "is_new_7d":      pub_dt is not None and (now - pub_dt) < timedelta(hours=NEW_7D_HOURS),
                    "is_since_login": pub_dt is not None and pub_dt >= LAST_LOGIN,
                    "source": name,
                    "weight":         weight,
                    "category":       assign_category(title),
                })
        except Exception as e:
            logger.warning("Feed failed %s: %s", url, e)
    articles.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return articles


async def fetch_async_yt_endpoint(session, url, params):
    try:
        async with session.get(url, params=params, timeout=10) as r:
            if r.status == 200:
                return await r.json()
            else:
                body = await r.text()
                logger.warning("YT API HTTP %s for channel %s: %s", r.status, params.get("channelId","?"), body[:300])
                return {"_error": r.status, "_body": body[:300]}
    except Exception as e:
        logger.warning("YT network exception: %s", e)
        return {"_error": str(e)}


async def fetch_all_videos_from_channel(session, api_key, ch_name, ch_id):
    now = datetime.now(timezone.utc)
    p = {
        "part":           "snippet",
        "type":           "video",
        "channelId":      ch_id,
        "order":          "date",
        "maxResults":     5,
        "publishedAfter": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key":            api_key,
    }
    resp = await fetch_async_yt_endpoint(session, YOUTUBE_API_URL, p)
    logger.info("YT raw response for %s (%s): keys=%s items=%s error=%s",
        ch_name, ch_id, list(resp.keys()), len(resp.get("items", [])),
        resp.get("_error", "none"))
    videos = []
    for item in resp.get("items", []):
        vid_id  = item["id"].get("videoId", "")
        snip    = item.get("snippet", {})
        pub_raw = snip.get("publishedAt", "")
        pub_dt, pub_str = None, ""
        try:
            pub_dt  = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
            pub_str = pub_dt.strftime("%d %b  %H:%M")
        except Exception:
            pass
        videos.append({
            "title":          snip.get("title", ""),
            "channel":        snip.get("channelTitle", ch_name),
            "link":           f"https://www.youtube.com/watch?v={vid_id}",
            "pub_dt":         pub_dt,
            "pub_str":        pub_str,
            "is_new":         pub_dt is not None and (now - pub_dt) < timedelta(hours=NEW_HOURS),
            "is_new_7d":      pub_dt is not None and (now - pub_dt) < timedelta(hours=NEW_7D_HOURS),
            "is_since_login": pub_dt is not None and pub_dt >= LAST_LOGIN,
            "category":       assign_category(snip.get("title", "")),
        })
    return videos


@st.cache_data(ttl=7200, show_spinner=False)
def _cached_youtube_fetch(api_key: str, cats_tuple: tuple) -> dict:
    return asyncio.run(batch_fetch_youtube_network(api_key, list(cats_tuple)))


async def batch_fetch_youtube_network(api_key, category_list):
    debug_log = {}
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_all_videos_from_channel(session, api_key, ch_name, ch_id)
            for ch_name, ch_id in TRUSTED_CHANNELS.items()
        ]
        all_results = await asyncio.gather(*tasks)

    by_cat = defaultdict(list)
    for (ch_name, _), channel_videos in zip(TRUSTED_CHANNELS.items(), all_results):
        debug_log[ch_name] = channel_videos
        for v in channel_videos:
            by_cat[v["category"]].append(v)

    import streamlit as _st
    _st.session_state["yt_debug_log"] = debug_log

    return {
        cat: sorted(
            by_cat.get(cat, []),
            key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )[:5]
        for cat in category_list
    }


def cnt_new(items):
    return sum(1 for a in items if a.get("is_new")) if items else 0

def cnt_since_login(items):
    return sum(1 for a in items if a.get("is_since_login")) if items else 0

def cnt_7d(items):
    return sum(1 for a in items if a.get("is_new_7d")) if items else 0


def render_article(a, col_type="news"):
    is_new = a.get("is_new", False)
    is_since_login = a.get("is_since_login", False)
    w = a.get("weight", 3)
    stars = "★" * int(w) if w >= 1 else "—"

    cls = "art-new-news" if is_new else "art-old-news" if col_type == "news" else "art-new-yt" if is_new else "art-old-yt"
    new_badge = '<span class="nbadge">NEW</span>' if is_new else ""
    login_badge = '<span class="newlogin-badge">SINCE LOGIN</span>' if is_since_login and not is_new else ""

    st.markdown(f"""
<div class="art">
  <div class="{cls}"><a href="{a['link']}" target="_blank">{a['title']}</a>{new_badge}{login_badge}</div>
  <div class="art-meta">
    <span class="msrc">{a.get('source','')}</span>
    <span class="mw">w:{w} {stars}</span>
    <span class="mtime">{a.get('pub_str','—')}</span>
  </div>
</div>""", unsafe_allow_html=True)


def render_video(v):
    is_new = v.get("is_new", False)
    is_since_login = v.get("is_since_login", False)
    cls = "art-new-yt" if is_new else "art-old-yt"
    new_badge = '<span class="nbadge">NEW</span>' if is_new else ""
    login_badge = '<span class="newlogin-badge">SINCE LOGIN</span>' if is_since_login and not is_new else ""
    st.markdown(f"""
<div class="art">
  <div class="{cls}"><a href="{v['link']}" target="_blank">{v['title']}</a>{new_badge}{login_badge}</div>
  <div class="art-meta">
    <span style="color:{C_YT}; font-weight:700;">{v['channel']}</span>
    <span class="mtime">{v.get('pub_str','—')}</span>
  </div>
</div>""", unsafe_allow_html=True)


def render_category_section(cat, articles, videos, colour, col_type="news"):
    safe_articles = articles if articles is not None else []
    safe_videos   = videos   if videos   is not None else []

    n_art = len(safe_articles)
    n_new_a = cnt_new(safe_articles)
    n_vid = len(safe_videos)
    n_new_v = cnt_new(safe_videos)

    if col_type == "news":
        new_tag = f' · {n_new_a} NEW' if n_new_a else ""
        count_str = f"{n_art} articles{new_tag}"
    else:
        new_tag = f' · {n_new_v} NEW' if n_new_v else ""
        count_str = f"{n_vid} videos{new_tag}"

    exp_label = f"{cat.upper()}  ({count_str})"

    with st.expander(exp_label):
        if col_type == "news":
            if not safe_articles:
                st.markdown(f'<p style="color:{C_WHITE};font-size:12px;">No articles in this category.</p>', unsafe_allow_html=True)
            else:
                since_login = [a for a in safe_articles if a.get("is_since_login")]
                older       = [a for a in safe_articles if not a.get("is_since_login")]
                for a in since_login:
                    render_article(a, col_type="news")
                if older:
                    st.markdown(f'<p style="font-size:10px;color:#444;margin:6px 0 4px;letter-spacing:0.08em">— OLDER ARTICLES —</p>', unsafe_allow_html=True)
                    for a in older:
                        render_article(a, col_type="news")
        else:
            if not safe_videos:
                st.markdown(f'<p style="color:{C_WHITE};font-size:12px;">No network updates matching keywords found this week.</p>', unsafe_allow_html=True)
            else:
                for v in safe_videos:
                    render_video(v)


# ── Load data pipelines ────────────────────────────────────────────────────────
sources_df = load_sources()
if sources_df.empty:
    st.error(f"Cannot find rss_active.csv at: {RSS_PATH}")
    st.stop()

source_tuples = tuple((row["name"], row["rss_url"], row["weight"], row["category"]) for _, row in sources_df.iterrows())

with st.spinner("Syncing network pipelines..."):
    all_articles = fetch_all_articles(source_tuples)

    show_cats = [c for c in CATEGORIES if c != "All"]
    if YOUTUBE_API_KEY:
        if "yt_batch_cache" not in st.session_state:
            try:
                st.session_state["yt_batch_cache"] = _cached_youtube_fetch(
                    YOUTUBE_API_KEY, tuple(show_cats)
                )
                st.session_state.pop("yt_fetch_error", None)
            except Exception as e:
                logger.error("YouTube batch fetch failed: %s", e)
                st.session_state["yt_batch_cache"] = {}
                st.session_state["yt_fetch_error"] = str(e)
    else:
        st.session_state["yt_batch_cache"] = {}

news_by_cat = defaultdict(list)
for a in all_articles:
    news_by_cat[a["category"]].append(a)

yt_data_for_sort = st.session_state.get("yt_batch_cache", {})
show_cats = sorted(
    show_cats,
    key=lambda c: len(news_by_cat.get(c, [])) + len(yt_data_for_sort.get(c, [])),
    reverse=True,
)

# ── Shill Tracker: log headlines + current BTC price (idempotent on URL) ──────
if _SHILL_OK:
    try:
        init_db()
        btc_now = get_btc_price().get("price")
        if btc_now:
            log_price_snapshot(btc_now)

        for a in all_articles:
            if not a.get("pub_dt") or not a.get("link"):
                continue
            log_headline(
                source_name=a.get("source") or "Unknown",
                source_type="article",
                title=a["title"],
                url=a["link"],
                category=a.get("category") or "General",
                published_at=a["pub_dt"],
                hype_score=score_hype(a["title"]),
                btc_price_at_post=btc_now,
            )

        for cat_vids in yt_data_for_sort.values():
            for v in cat_vids:
                if not v.get("pub_dt") or not v.get("link"):
                    continue
                log_headline(
                    source_name=v.get("channel") or "YouTube",
                    source_type="video",
                    title=v["title"],
                    url=v["link"],
                    category=v.get("category") or "General",
                    published_at=v["pub_dt"],
                    hype_score=score_hype(v["title"]),
                    btc_price_at_post=btc_now,
                )
    except Exception as e:
        logger.warning("Shill Tracker logging failed: %s", e)

n_total       = len(all_articles)
n_new         = cnt_new(all_articles)
n_7d          = cnt_7d(all_articles)
n_since_login = cnt_since_login(all_articles)
n_topics      = len(show_cats)

all_videos       = [v for vids in yt_data_for_sort.values() for v in vids]
n_total_yt       = len(all_videos)
n_new_yt         = cnt_new(all_videos)
n_7d_yt          = cnt_7d(all_videos)
n_since_login_yt = cnt_since_login(all_videos)
n_topics_yt      = len([c for c in show_cats if yt_data_for_sort.get(c)])


# ── Header ────────────────────────────────────────────────────────────────────
hc, rc = st.columns([7, 1])
with hc:
    st.markdown(f'<h1 style="color:{C_WHITE};font-size:26px;margin-bottom:2px;">📰 NEWS FEED</h1>', unsafe_allow_html=True)
with rc:
    if st.button("↻ Refresh"):
        if "yt_batch_cache" in st.session_state:
            del st.session_state["yt_batch_cache"]
        _cached_youtube_fetch.clear()
        st.cache_data.clear()
        st.rerun()

st.markdown(f'<p style="color:{C_WHITE};font-family:Courier New,monospace;font-size:12px;margin-top:-4px;margin-bottom:14px;">Last updated: {datetime.now().strftime("%H:%M:%S")} &nbsp;·&nbsp; {len(sources_df)} sources</p>', unsafe_allow_html=True)

# ── Stats bar ─────────────────────────────────────────────────────────────────
yt_connected = bool(YOUTUBE_API_KEY)

st.markdown(f"""
<div class="stats-news-box">
  <span class="slbl-news">📰 NEWS</span>
  <span class="sdiv">|</span>
  <span class="skey">TOTAL:</span><span class="sval-news">&nbsp;{n_total}</span>
  <span class="sdiv">|</span>
  <span class="skey">24HR NEW:</span><span class="sval-news">&nbsp;{n_new}</span>
  <span class="sdiv">|</span>
  <span class="skey">7 DAY:</span><span class="sval-news">&nbsp;{n_7d}</span>
  <span class="sdiv">|</span>
  <span class="skey">TOPICS:</span><span class="sval-news">&nbsp;{n_topics}</span>
  <span class="sdiv">|</span>
  <span class="skey">NEW SINCE LOGIN:</span><span class="sval-new-login">&nbsp;{n_since_login}</span>
</div>
<div class="stats-yt-box">
  <span class="slbl-yt">▶ YOUTUBE</span>
  <span class="sdiv">|</span>
  <span class="skey">TOTAL:</span><span class="sval-yt">&nbsp;{n_total_yt}</span>
  <span class="sdiv">|</span>
  <span class="skey">24HR NEW:</span><span class="sval-yt">&nbsp;{n_new_yt}</span>
  <span class="sdiv">|</span>
  <span class="skey">7 DAY:</span><span class="sval-yt">&nbsp;{n_7d_yt}</span>
  <span class="sdiv">|</span>
  <span class="skey">TOPICS:</span><span class="sval-yt">&nbsp;{n_topics_yt}</span>
  <span class="sdiv">|</span>
  <span class="skey">NEW SINCE LOGIN:</span><span class="sval-new-login">&nbsp;{n_since_login_yt}</span>
</div>
""", unsafe_allow_html=True)

# ── Main columns ──────────────────────────────────────────────────────────────
col_news, col_yt = st.columns(2)

with col_news:
    st.markdown(f'<div class="news-box"><div class="chdr-news">📰 NEWS <span style="color:{C_WHITE};font-size:13px;">(RSS Articles)</span></div>', unsafe_allow_html=True)
    for cat in show_cats:
        render_category_section(cat, news_by_cat.get(cat, []), [], CATEGORIES.get(cat, {"colour": C_NEWS})["colour"], col_type="news")
    st.markdown('</div>', unsafe_allow_html=True)

with col_yt:
    st.markdown(f'<div class="yt-box"><div class="chdr-yt">▶ YOUTUBE <span style="color:{C_WHITE};font-size:13px;">(High-View Verified Channels)</span></div>', unsafe_allow_html=True)
    if not yt_connected:
        st.markdown(f'<div style="background:#0d0014;border:1px solid {C_YT};border-radius:6px;padding:10px 14px;font-size:12px;color:{C_WHITE};">⚠ Add YOUTUBE_API_KEY to .env to enable video feeds.</div>', unsafe_allow_html=True)
    else:
        yt_data = st.session_state.get("yt_batch_cache", {})
        for cat in show_cats:
            render_category_section(cat, [], yt_data.get(cat, []), CATEGORIES.get(cat, {"colour": C_YT})["colour"], col_type="yt")
    st.markdown('</div>', unsafe_allow_html=True)

# ── Legend ────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="legend">
  <span><span style="color:{C_NEWS};">● NEWS</span> = RSS articles</span>
  <span><span style="color:{C_YT};">● YOUTUBE</span> = High-view curated channels ({len(TRUSTED_CHANNELS)} networks tracked)</span>
  <span><span style="color:{C_NEW};">RED</span> = Last {NEW_HOURS}h</span>
  <span><span style="color:{C_NEWS};">GREEN</span> = Older news</span>
  <span><span style="color:{C_YT};">PURPLE</span> = Older YouTube uploads</span>
</div>
""", unsafe_allow_html=True)
