"""
pages/0_Home.py

Command centre home page.

COLUMNS:
  BTC  — institutional/macro scoring. Financial weather report.
  ALT  — sentiment/velocity scoring. Culture and casino floor.

BANNER: any story scoring >= 95 shown across top of screen.

BADGES per story:
  Score pill        coloured by score value
  Tier badge        Tier 1/2/3/4
  Sentiment         Bullish/Bearish/Neutral
  Story Velocity    clone count across feeds
  Hype Meter        sensationalism word count
  Read Time         estimated read + style
  Entities          key nouns detected
  Polymarket badge  matching prediction market with probability

POLYMARKET:
  Loads once, cached 30 min.
  Each story gets a [PM ?] badge if a market matches.
  Click expander to approve/reject/fix the match.
  Liquidity label + extreme price alerts shown.
"""

import sys
import json
import streamlit as st
import requests
import re
import csv
import feedparser
import pandas as pd
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ── Path Optimization & Injection ───────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT / "logic") not in sys.path:
    sys.path.insert(0, str(ROOT / "logic"))

# ── Local Core Module Imports ────────────────────────────────────────────────

load_dotenv(ROOT / ".env")
logger   = logging.getLogger(__name__)
RSS_PATH             = ROOT / "rss_active.csv"
HOME_NEWS_SOURCES_PATH = ROOT / "home_news_sources.csv"
NEWS_STORIES_LOG_PATH  = ROOT / "news_stories_log.csv"
LEGACY_SCORE_LOG_PATH   = ROOT / "legacy_score_breakdown.csv"

from market_data import fetch_market_data
from scoring_engine import ScoringEngine
from home_news_sources import fetch_all_signals
from home_news_scoring import build_news_stories, render_news_stories, log_news_stories_snapshot

scoring_engine = ScoringEngine()

# ── Colours ───────────────────────────────────────────────────────────────────
C = {
    "bg":      "#000000",
    "card":    "#0a0a0a",
    "border":  "#222222",
    "white":   "#FFFFFF",
    "muted":   "#555555",
    "green":   "#39FF14",
    "cyan":    "#00FFFF",
    "gold":    "#FFD700",
    "red":     "#FF3131",
    "pink":    "#FF1493",
    "purple":  "#A855F7",
    "orange":  "#FF5F1F",
    "blue":    "#007FFF",
}

SECTOR_COLOURS = {
    "AI":"#00FFFF","RWA":"#00FF7F","Layer 1":"#FF5F1F","Gaming":"#FF00FF",
    "ISO 20022":"#007FFF","Privacy":"#8F00FF","Memes":"#FF3131",
    "DePIN":"#CCFF00","DeFi":"#4D4DFF",
}
SECTORS = {
    "AI":        ["fetch-ai","render-token","bittensor","singularitynet","ocean-protocol","akash-network","grass"],
    "RWA":       ["ondo-finance","mantra","pendle","centrifuge","clearpool","goldfinch"],
    "Layer 1":   ["solana","avalanche-2","sui","aptos","near","cosmos","toncoin"],
    "Gaming":    ["immutable-x","gala","axie-infinity","beam-2","pixels","gods-unchained"],
    "ISO 20022": ["ripple","stellar","algorand","quant-network","hedera-hashgraph","iota"],
    "Privacy":   ["monero","zcash","secret","pirate-chain","dash","oasis-network"],
    "Memes":     ["dogecoin","shiba-inu","pepe","dogwifcoin","bonk","floki","brett"],
    "DePIN":     ["helium","helium-mobile","iotex","hivemapper","dimo","powerpool"],
    "DeFi":      ["uniswap","aave","curve-dao-token","jupiter-exchange-solana","hyperliquid","sky","lido-dao"],
}


# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
.stApp,.main,section.main,.block-container{{
    background:{C['bg']}!important;color:{C['white']}!important;
    font-family:'Courier New',monospace!important;
}}
section[data-testid="stSidebar"]{{
    background:#050505!important;border-right:1px solid {C['border']}!important;
}}

/* Sidebar page navigation — bigger, brighter text + hover highlight */
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"],
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] span,
section[data-testid="stSidebar"] nav a,
section[data-testid="stSidebar"] nav span,
section[data-testid="stSidebar"] ul li a{{
    font-size:17px!important;
    font-weight:600!important;
    color:#EDEDED!important;
    font-family:'Courier New',monospace!important;
    letter-spacing:.02em!important;
    transition:color .15s ease, background-color .15s ease!important;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]{{
    padding:6px 10px!important;
    border-radius:4px!important;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a:hover,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a:hover span,
section[data-testid="stSidebar"] nav a:hover,
section[data-testid="stSidebar"] nav a:hover span,
section[data-testid="stSidebar"] ul li a:hover{{
    color:{C['green']}!important;
    background-color:rgba(57,255,20,0.08)!important;
}}
/* Currently active page in the nav */
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"],
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] span,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a[aria-current="page"],
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a[aria-current="page"] span{{
    color:{C['cyan']}!important;
    font-weight:700!important;
}}
#MainMenu,footer,header{{visibility:hidden;}}
div[data-testid="stDecoration"]{{display:none;}}
.block-container{{padding-top:1rem;}}
h1,h2,h3,h4{{
    font-family:'Courier New',monospace!important;
    color:{C['white']}!important;font-weight:700!important;
}}

/* BREAKING SIGNAL — stand-alone callout for News Stories scoring >80 */
.bs-card{{
    background:{C['card']};
    border:3px solid #FF3B30;
    border-radius:8px;
    padding:20px 24px;
    margin-bottom:16px;
    box-shadow:0 0 20px rgba(255,59,48,0.35);
    animation:bs-pulse 2s infinite;
}}
.bs-label{{
    color:#FF453A;font-weight:800;font-size:15px;
    letter-spacing:.10em;margin-bottom:4px;
}}
.bs-score{{color:#FFD60A;font-weight:800;font-size:16px;margin-bottom:12px;}}
.bs-headline{{font-size:28px;line-height:1.3;font-weight:700;margin-bottom:10px;}}
.bs-headline a{{color:#FFFFFF;text-decoration:none;}}
.bs-headline a:hover{{text-decoration:underline;}}
.bs-meta{{font-size:14px;color:{C['muted']};font-family:'Courier New',monospace;}}
@keyframes bs-pulse{{0%{{border-color:#FF3B30;}}50%{{border-color:#8f0d0a;}}100%{{border-color:#FF3B30;}}}}

/* Metric cards */
.mc{{
    background:{C['card']};border:1px solid {C['border']};
    border-radius:6px;padding:12px 14px;text-align:center;
}}
.mc-lbl{{font-size:16px;color:{C['muted']};text-transform:uppercase;
          letter-spacing:.08em;margin-bottom:5px;}}
.mc-val{{font-size:20px;font-weight:700;line-height:1.1;}}
.mc-sub{{font-size:16px;color:{C['muted']};margin-top:3px;}}
.fg-bar{{background:#111;border-radius:99px;height:8px;overflow:hidden;margin:6px 0 3px;}}

/* Section headers */
.shdr{{
    font-size:13px;font-weight:700;letter-spacing:.08em;
    text-transform:uppercase;border-bottom:2px solid;
    padding-bottom:6px;margin-bottom:12px;
}}

/* Story card */
.sc{{
    background:{C['card']};border:1px solid {C['border']};
    border-radius:6px;padding:12px 14px;margin-bottom:8px;
}}
.sc:hover{{border-color:#333;}}
.sc-title{{font-size:13px;line-height:1.4;margin-bottom:6px;}}
.sc-title a{{color:{C['white']};text-decoration:none;}}
.sc-title a:hover{{color:{C['green']};}}

/* Badge row */
.badges{{
    display:flex;flex-wrap:wrap;gap:5px;
    margin-bottom:5px;align-items:center;
}}
.badge{{
    padding:2px 7px;border-radius:3px;font-size:10px;
    font-weight:700;font-family:'Courier New',monospace;
    white-space:nowrap;
}}

/* PM badge variants */
.pm-unverified{{background:#111122;color:{C['blue']};border:1px solid {C['blue']}44;cursor:pointer;}}
.pm-approved  {{background:#052e16;color:{C['green']};border:1px solid {C['green']}44;}}
.pm-rejected  {{background:#1a0000;color:{C['muted']};border:1px solid #33000044;text-decoration:line-through;}}

/* Story meta */
.sc-meta{{display:flex;gap:10px;font-size:11px;color:{C['muted']};flex-wrap:wrap;}}

/* News Stories card — separate from .sc so its sizing never affects
   the legacy BTC/ALT columns above/below it */
.ns-card{{
    background:{C['card']};border:1px solid {C['border']};
    border-radius:6px;padding:14px 16px;margin-bottom:10px;
}}
.ns-card:hover{{border-color:#333;}}
.ns-title{{font-size:22px;line-height:1.4;margin-bottom:8px;font-weight:600;}}
.ns-title a{{color:{C['white']};text-decoration:none;}}
.ns-title a:hover{{color:{C['green']};}}
.ns-meta{{
    display:flex;gap:12px;font-size:20px;color:{C['muted']};
    flex-wrap:wrap;margin-top:2px;
}}
.ns-calc{{
    font-size:20px;color:{C['white']};margin-top:8px;padding-top:8px;
    border-top:1px dashed {C['border']};line-height:1.6;
    font-family:'Courier New',monospace;
}}

/* Buttons */
div.stButton>button{{
    background:transparent!important;color:{C['white']}!important;
    border:1px solid {C['border']}!important;
    font-family:'Courier New',monospace!important;font-size:10px!important;
    padding:2px 8px!important;
}}
div.stButton>button:hover{{border-color:{C['green']}!important;color:{C['green']}!important;}}

/* Expander */
.stExpander{{background:{C['bg']}!important;border:1px solid {C['border']}!important;}}
.stExpander summary{{
    font-family:'Courier New',monospace!important;
    color:{C['white']}!important;background:{C['bg']}!important;
    font-size:11px!important;
}}
</style>
""", unsafe_allow_html=True)


# ── Data loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_market_data():
    return fetch_market_data()


@st.cache_data(ttl=1800)
def load_polymarkets():
    # Polymarket temporarily disabled.
    return []

@st.cache_data(ttl=300)
def load_sector_leader():
    all_ids = list(dict.fromkeys([c for ids in SECTORS.values() for c in ids]))
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency":"usd","ids":",".join(all_ids),"per_page":250,"sparkline":"false"},
            timeout=15,
        )
        r.raise_for_status()
        coin_map = {c["id"]: c for c in r.json()}
    except Exception:
        return None, None
    avgs = {}
    for name, ids in SECTORS.items():
        chgs = [coin_map[i]["price_change_percentage_24h"] or 0
                for i in ids if i in coin_map]
        if chgs: avgs[name] = round(sum(chgs)/len(chgs), 2)
    if not avgs: return None, None
    best = max(avgs, key=avgs.get)
    return (best, avgs[best]), (min(avgs, key=avgs.get), avgs[min(avgs, key=avgs.get)])


# Keywords that must appear in a title for it to be considered crypto-relevant.
# Checked case-insensitively. Add to this list as needed.
CRYPTO_KEYWORDS = {
    "bitcoin","btc","ethereum","eth","crypto","blockchain","defi","nft","altcoin",
    "stablecoin","usdt","usdc","solana","sol","xrp","ripple","cardano","ada",
    "binance","coinbase","kraken","uniswap","aave","polygon","matic","avax",
    "avalanche","chainlink","link","polkadot","dot","cosmos","atom","toncoin",
    "ton","sui","aptos","near","arbitrum","optimism","base","layer 2","l2",
    "layer 1","l1","degen","meme coin","memecoin","shiba","doge","dogecoin",
    "pepe","bonk","floki","wif","bittensor","tao","render","rndr","fetch","fet",
    "ocean","akash","ondo","mantra","pendle","hyperliquid","jupiter","lido",
    "staking","mining","halving","satoshi","wallet","exchange","cex","dex",
    "token","tokenized","tokenisation","tokenization","web3","dao","protocol",
    "on-chain","onchain","depin","rwa","real world asset","polymarket","prediction market",
    "etf","spot etf","sec","cftc","gensler","treasury","fed rate","interest rate",
    "institutional","blackrock","fidelity","microstrategy","strategy","grayscale",
    "whale","hodl","hodler","stablecoin","cbdc","central bank digital",
    "hack","exploit","rug pull","rugpull","liquidation","liquidated","funding rate",
    "open interest","long squeeze","short squeeze","market cap","dominance",
}

def _is_crypto_relevant(title: str) -> bool:
    """Return True if the title contains at least one crypto keyword."""
    t = title.lower()
    return any(kw in t for kw in CRYPTO_KEYWORDS)

def _dedupe(articles: list) -> list:
    """Remove near-duplicate titles (same base text, different wire prefixes)."""
    import re
    seen = {}
    result = []
    # Strip common wire prefixes like "(URGENT)", "(2nd LD)", "(LEAD)" before comparing
    prefix_re = re.compile(r"^\([^)]{1,20}\)\s*", re.IGNORECASE)
    for a in articles:
        key = prefix_re.sub("", a["title"]).strip().lower()[:120]
        if key not in seen:
            seen[key] = True
            result.append(a)
    return result


@st.cache_data(ttl=600)
def load_articles():
    """Fetch RSS, score with both BTC and ALT weights, add velocity + PM matches."""
    if not RSS_PATH.exists():
        st.warning(
            f"⚠️ RSS feed list not found at: `{RSS_PATH}`. "
            f"Create `rss_active.csv` with columns: name, rss_url, weight"
        )
        return [], [], []

    df = pd.read_csv(RSS_PATH, on_bad_lines="skip", engine="python")
    df.columns = [c.strip().lower() for c in df.columns]
    df["weight"] = pd.to_numeric(df.get("weight", 3), errors="coerce").fillna(3)

    if "rss_url" not in df.columns:
        st.error("⚠️ `rss_active.csv` is missing a `rss_url` column.")
        return [], [], []

    raw = []
    feed_errors = []
    for _, row in df.iterrows():
        try:
            feed = feedparser.parse(row["rss_url"])
            if feed.bozo and not feed.entries:
                feed_errors.append(
                    f"{row.get('name', row['rss_url'])}: "
                    f"{getattr(feed, 'bozo_exception', 'parse error')}"
                )
                continue
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                # ── Hard filter: skip anything not crypto-related ──────────
                if not _is_crypto_relevant(title):
                    continue
                pub_dt, pub_str = None, ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub_dt  = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                        pub_str = pub_dt.strftime("%d %b %H:%M")
                    except Exception:
                        pass
                raw.append({
                    "title":   title,
                    "link":    entry.get("link", "#"),
                    "source":  row["name"],
                    "weight":  row["weight"],
                    "pub_dt":  pub_dt,
                    "pub_str": pub_str,
                })
        except Exception as e:
            feed_errors.append(f"{row.get('name', row.get('rss_url', '?'))}: {e}")

    if feed_errors:
        with st.expander(f"⚠️ {len(feed_errors)} feed(s) failed to load"):
            for err in feed_errors:
                st.markdown(f"- `{err}`")

    # Deduplicate wire clones before scoring
    raw = _dedupe(raw)

    if not raw:
        st.error("No crypto articles retrieved. Check your RSS feeds or keyword list.")
        return [], [], []

    # Velocity (clone counts) is computed once across the full deduped set,
    # then BTC and ALT columns are scored separately since they use
    # different topic/weighting modes.
    raw = scoring_engine.add_velocity_scores(raw)
    btc_scored = scoring_engine.calculate_scores([a.copy() for a in raw], mode="btc")
    alt_scored = scoring_engine.calculate_scores([a.copy() for a in raw], mode="alt")

    # Filter BTC column: only Bitcoin/institutional/macro-adjacent topics
    btc_topics = {"Regulation", "Institutional", "Bitcoin", "ETF", "Macro",
                  "Security", "Market Stress", "Stablecoin", "Whale Activity"}
    btc_top    = [a for a in btc_scored if a.get("topic") in btc_topics][:10]
    if not btc_top:
        btc_top = btc_scored[:10]

    # Filter ALT column: altcoin/degen-adjacent topics only, never pure
    # Bitcoin/Regulation/Macro (those stay BTC-only)
    alt_excl   = {"Bitcoin", "Regulation", "Macro"}
    alt_only   = {"DeFi", "Layer 1", "Memes", "AI", "RWA/Tokenisation",
                  "Stablecoin", "Whale Activity", "Market Stress",
                  "Security", "ETF", "Institutional"}
    alt_top    = [a for a in alt_scored
                  if a.get("topic") not in alt_excl
                  and a.get("topic") in alt_only][:10]
    if not alt_top:
        # Broad fallback: anything that passed the crypto keyword filter and isn't pure macro
        alt_top = [a for a in alt_scored if a.get("topic") not in alt_excl][:10]

    # `btc_scored` is the full deduped/scored pool (not truncated to Top 10,
    # not topic-filtered) — returned as-is for the Home News Stories
    # attention filter, which does its own separate clustering/scoring on
    # top of it in home_news_scoring.py. This does NOT change what BTC/ALT
    # columns show above.
    return btc_top, alt_top, btc_scored


def get_banners(btc, alt):
    banners = [a for a in btc + alt if a.get("banner")]
    banners.sort(key=lambda x: x.get("score", 0), reverse=True)
    return banners


def highlight_headline(headline: str, green_words, red_words) -> str:
    """Wraps bullish words in green and bearish/critical words in red
    within a headline, reusing scoring_engine's existing word lists
    rather than inventing a new sentiment vocabulary. Used only by the
    Breaking Signal callout."""
    green_set = {w.lower() for w in green_words}
    red_set = {w.lower() for w in red_words}

    def _wrap(match):
        word = match.group(0)
        lw = word.lower()
        if lw in red_set:
            return f'<span style="color:#FF453A;">{word}</span>'
        if lw in green_set:
            return f'<span style="color:#30D158;">{word}</span>'
        return word

    return re.sub(r"[A-Za-z']+", _wrap, headline)


LEGACY_LOG_COLUMNS = [
    "logged_at_utc", "column", "source", "link", "title", "mode", "base",
    "feed_weight", "feed_weight_pts", "topic", "topic_pts",
    "threat_words_hit", "threat_pts", "institutional_words_hit",
    "institutional_pts", "clone_count", "velocity_pts", "hype_count",
    "hype_penalty", "raw_total", "final_score",
]


def log_legacy_score_breakdown(btc_list, alt_list, engine, log_path: Path, min_interval_minutes: int = 5):
    """Exports the exact point-by-point breakdown behind every article
    currently in the BTC/ALT pools (the same 20 articles the News
    Sentiment average is computed from) to a CSV. Throttled per session
    so normal page reruns don't spam it."""
    now = datetime.now(timezone.utc)
    last = st.session_state.get("_legacy_score_log_last_ts")
    if last and (now - last) < timedelta(minutes=min_interval_minutes):
        return
    st.session_state["_legacy_score_log_last_ts"] = now

    rows = []
    for a in btc_list:
        row = engine.explain_score(a, mode="btc")
        row.update({
            "logged_at_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "column": "BTC", "source": a.get("source"), "link": a.get("link"),
        })
        rows.append(row)
    for a in alt_list:
        row = engine.explain_score(a, mode="alt")
        row.update({
            "logged_at_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "column": "ALT", "source": a.get("source"), "link": a.get("link"),
        })
        rows.append(row)

    if not rows:
        return

    is_new = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEGACY_LOG_COLUMNS)
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in LEGACY_LOG_COLUMNS})


# ── Badge renderers ───────────────────────────────────────────────────────────
def score_colour(s):
    if s >= 90: return C["pink"]
    if s >= 75: return C["green"]
    if s >= 60: return C["gold"]
    if s >= 45: return C["orange"]
    return C["muted"]

def sentiment_colour(s):
    if s == "Bullish": return C["green"]
    if s == "Bearish": return C["red"]
    return C["muted"]

def velocity_colour(level):
    return {"Extreme":C["pink"],"High":C["red"],"Medium":C["gold"],"Low":C["muted"]}.get(level,C["muted"])

def hype_colour(level):
    return {"High Risk":C["red"],"Medium Risk":C["gold"],"Low Risk":C["orange"],"Clean":C["muted"]}.get(level,C["muted"])

def render_badge(text, colour, bg_alpha="22"):
    return f'<span class="badge" style="background:{colour}{bg_alpha};color:{colour};border:1px solid {colour}44;">{text}</span>'

def render_pm_badge(pm, article_idx):
    """Render Polymarket badge. State stored in session_state."""
    if pm is None:
        return ""
    key    = f"pm_state_{article_idx}"
    state  = st.session_state.get(key, "unverified")
    prob   = pm.get("probability")
    p_str  = f"{prob:.0f}%" if prob is not None else "?"

    if state == "approved":
        return f'<span class="badge pm-approved">PM ✓ {p_str}</span>'
    if state == "rejected":
        return ""
    return f'<span class="badge pm-unverified">PM ? {p_str}</span>'


def render_story(article: dict, col_colour: str, story_idx: int, pm_markets: list):
    """Render a full story card with all badges and Polymarket integration."""
    score   = article.get("score", 0)
    sc      = score_colour(score)
    title   = article.get("title","")
    link    = article.get("link","#")
    source  = article.get("source","")
    pub_str = article.get("pub_str","")
    sent    = article.get("sentiment","Neutral")
    tier    = article.get("score_tier","")
    topic   = article.get("topic","")
    hype_n  = article.get("hype_count",0)
    hype_l  = article.get("hype_level","Clean")
    vel_n   = article.get("clone_count",0)
    vel_l   = article.get("velocity_level","Low")
    read    = article.get("readability",{})
    ents    = article.get("entities",[])
    pm      = article.get("polymarket")
    alert   = article.get("alert",False)
    banner  = article.get("banner",False)

    alert_html  = render_badge("⚡ ALERT",  C["pink"])  if alert  else ""
    banner_html = render_badge("🚨 BANNER", C["pink"])  if banner else ""
    pm_html     = render_pm_badge(pm, story_idx)

    # Score badge
    score_badge = f'<span class="badge" style="background:{sc}22;color:{sc};border:1px solid {sc}44;font-size:12px;padding:2px 10px;">{score:.0f}</span>'

    # Tier
    tier_col = {
        "Tier 1": C["green"],
        "Tier 2": C["cyan"],
        "Tier 3": C["gold"],
        "Tier 4": C["muted"],
    }.get(tier, C["muted"])
    tier_badge = render_badge(tier, tier_col)

    # Sentiment
    sent_badge = render_badge(sent, sentiment_colour(sent))

    # Topic
    topic_badge = render_badge(topic, col_colour) if topic else ""

    # Velocity
    vel_label  = f"🔄 Velocity: {vel_l} ({vel_n} clones)" if vel_n > 0 else f"🔄 Velocity: {vel_l}"
    vel_badge  = render_badge(vel_label, velocity_colour(vel_l))

    # Hype
    if hype_n > 0:
        hype_badge = render_badge(f"⚡ Hype: {hype_n} [{hype_l}]", hype_colour(hype_l))
    else:
        hype_badge = render_badge("✓ Clean", C["muted"])

    # Read time
    read_badge = render_badge(
        f"📖 {read.get('read_min','?')} min · {read.get('style','?')}",
        C["muted"],
    ) if read else ""

    # Entities
    ent_badges = " ".join(render_badge(e.title(), C["blue"]) for e in ents[:3]) if ents else ""

    st.markdown(f"""
<div class="sc">
  <div class="sc-title">
    <a href="{link}" target="_blank">{title}</a>
  </div>
  <div class="badges">
    {score_badge}
    {tier_badge}
    {sent_badge}
    {topic_badge}
    {alert_html}
    {banner_html}
    {pm_html}
  </div>
  <div class="badges">
    {vel_badge}
    {hype_badge}
    {read_badge}
    {ent_badges}
  </div>
  <div class="sc-meta">
    <span style="color:#888;">{source}</span>
    <span>{pub_str}</span>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Polymarket detail expander ─────────────────────────────────────────
    if pm is not None:
        pm_key   = f"pm_state_{story_idx}"
        pm_state = st.session_state.get(pm_key, "unverified")
        fix_key  = f"pm_fix_{story_idx}"

        if pm_state != "rejected":
            prob     = pm.get("probability")
            vol      = pm.get("volume", 0)
            liq      = pm.get("liquidity_label", {})
            pa       = pm.get("price_alert")
            question = pm.get("question","")
            end_date = pm.get("end_date","")[:10]
            pm_url   = pm.get("url","#")
            match_sc = pm.get("match_score", 0)

            with st.expander(f"📊 Polymarket match — {question[:55]}…"):
                # Market info
                st.markdown(f"""
<div style="background:#050510;border:1px solid {C['blue']}44;border-radius:6px;padding:12px 14px;font-family:'Courier New',monospace;">
  <div style="color:{C['blue']};font-size:11px;letter-spacing:.08em;margin-bottom:8px;">PREDICTION MARKET MATCH</div>

  <div style="font-size:13px;color:{C['white']};margin-bottom:10px;">
    <a href="{pm_url}" target="_blank" style="color:{C['white']};text-decoration:none;">
      {question}
    </a>
  </div>

  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:10px;">
    <div>
      <div style="font-size:10px;color:{C['muted']};">PROBABILITY</div>
      <div style="font-size:20px;font-weight:700;color:{C['blue']};">{prob:.0f}%</div>
    </div>
    <div>
      <div style="font-size:10px;color:{C['muted']};">TOTAL POOL</div>
      <div style="font-size:20px;font-weight:700;color:{liq.get('colour',C['muted'])};">{fmt_volume(vol)}</div>
    </div>
    <div>
      <div style="font-size:10px;color:{C['muted']};">EXPIRES</div>
      <div style="font-size:14px;color:{C['white']};">{end_date or '—'}</div>
    </div>
    <div>
      <div style="font-size:10px;color:{C['muted']};">MATCH CONFIDENCE</div>
      <div style="font-size:14px;color:{C['gold']};">{match_sc*100:.0f}%</div>
    </div>
  </div>
""", unsafe_allow_html=True)

                # Liquidity badge
                liq_label = liq.get("label","—")
                liq_col   = liq.get("colour", C["muted"])
                liq_trust = liq.get("trustworthy", False)
                trust_str = "✓ Trustworthy signal" if liq_trust else "⚠ Untrustworthy — too thin"
                st.markdown(
                    f'<div style="margin-bottom:8px;">'
                    f'<span class="badge" style="background:{liq_col}22;color:{liq_col};border:1px solid {liq_col}44;">'
                    f'💧 {liq_label}</span>'
                    f'&nbsp;<span style="font-size:11px;color:{liq_col};">{trust_str}</span></div>',
                    unsafe_allow_html=True,
                )

                # Price alert
                if pa:
                    st.markdown(
                        f'<div class="badge" style="background:{pa["colour"]}22;color:{pa["colour"]};'
                        f'border:1px solid {pa["colour"]}44;display:inline-block;margin-bottom:8px;">'
                        f'{pa["icon"]} {pa["label"]}</div>',
                        unsafe_allow_html=True,
                    )

                st.markdown('</div>', unsafe_allow_html=True)

                # Approve / Reject / Fix buttons
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    if st.button("✓ Approve", key=f"approve_{story_idx}"):
                        st.session_state[pm_key] = "approved"
                        st.rerun()
                with bc2:
                    if st.button("✕ Reject",  key=f"reject_{story_idx}"):
                        st.session_state[pm_key] = "rejected"
                        st.rerun()
                with bc3:
                    if st.button("🔍 Fix",    key=f"fix_{story_idx}"):
                        st.session_state[fix_key] = not st.session_state.get(fix_key, False)
                        st.rerun()

                # Fix input
                if st.session_state.get(fix_key):
                    new_kw = st.text_input(
                        "Re-search keyword:",
                        key=f"fixinput_{story_idx}",
                        placeholder="e.g. Powell rate cut",
                    )
                    if new_kw and st.button("Search", key=f"fixsearch_{story_idx}"):
                        new_match = match_article_to_market(new_kw, pm_markets)
                        if new_match:
                            article["polymarket"] = new_match
                            st.success(f"New match: {new_match['question'][:60]}")
                        else:
                            st.warning("No match found for that keyword.")


# ────────────────────────────────────────────────────────────────────────────
# PAGE
# ────────────────────────────────────────────────────────────────────────────
hc, rc = st.columns([7,1])
with hc:
    st.markdown(
        f'<h1 style="font-size:24px;margin-bottom:2px;">₿ CRYPTO FUNDAMENTAL</h1>',
        unsafe_allow_html=True,
    )
with rc:
    if st.button("↻"):
        st.cache_data.clear()
        st.rerun()

st.markdown(
    f'<p style="color:{C["muted"]};font-size:11px;margin-top:-4px;margin-bottom:14px;">'
    f'Command centre · {datetime.now().strftime("%d %b %Y  %H:%M")}</p>',
    unsafe_allow_html=True,
)

# ── Load data ─────────────────────────────────────────────────────────────────
with st.spinner("Loading…"):
    market_data          = load_market_data()
    pm_markets           = load_polymarkets()
    best_sec, worst_sec  = load_sector_leader()
    btc_stories, alt_stories, all_scored = load_articles()
    log_legacy_score_breakdown(btc_stories, alt_stories, scoring_engine, LEGACY_SCORE_LOG_PATH)

    # Home News Stories attention filter — separate CSV, separate scorer.
    # home_news_sources.csv (NOT rss_active.csv) drives the external
    # Top/Featured/Most Read signal checks.
    external_signals = fetch_all_signals(HOME_NEWS_SOURCES_PATH)
    news_stories = build_news_stories(all_scored, external_signals, top_n=10)
    log_news_stories_snapshot(news_stories, NEWS_STORIES_LOG_PATH)

# Add PM matches
# Polymarket matching temporarily disabled.

fg   = market_data["fear_greed"]
glbl = market_data["global"]
btc  = market_data["btc"]

# ── BREAKING SIGNAL — top News Story with attention score > 80 ──────────────
_breaking = sorted(
    [s for s in news_stories if s["attention_score"] > 80],
    key=lambda s: s["attention_score"], reverse=True,
)
if _breaking:
    top = _breaking[0]
    highlighted = highlight_headline(
        top["headline"],
        green_words=scoring_engine.BULLISH_WORDS,
        red_words=scoring_engine.BEARISH_WORDS + scoring_engine.SYSTEMIC_THREAT_WORDS,
    )
    st.markdown(f"""
<div class="bs-card">
  <div class="bs-label">🚨 BREAKING SIGNAL</div>
  <div class="bs-score">SCORE {top['attention_score']}/100</div>
  <div class="bs-headline"><a href="{top['link']}" target="_blank">{highlighted}</a></div>
  <div class="bs-meta">{top['calc_line']}</div>
</div>
""", unsafe_allow_html=True)
    # Don't repeat it in the regular News Stories list below.
    news_stories = [s for s in news_stories if s is not top]
else:
    st.markdown(f"""
<div style="background:{C['card']};border:1px dashed {C['border']};border-radius:6px;
    padding:16px 20px;margin-bottom:16px;text-align:center;color:{C['muted']};font-size:16px;">
  No News Story scoring over 80 right now.
</div>
""", unsafe_allow_html=True)

# ── Market metrics row ────────────────────────────────────────────────────────
st.markdown(
    f'<div class="shdr" style="border-color:{C["muted"]};color:{C["white"]};">Market Overview</div>',
    unsafe_allow_html=True,
)

m1,m2,m3,m4,m5,m6 = st.columns(6)

with m1:
    fv = fg["value"]
    fgc = C["green"] if (fv or 0) >= 50 else C["red"]
    st.markdown(f"""
<div class="mc" style="background:{fgc}1A;border-color:{fgc};">
  <div class="mc-lbl">Fear & Greed</div>
  <div class="mc-val" style="color:{fgc};">{fv or '—'}</div>
  <div class="fg-bar"><div style="width:{fv or 0}%;height:8px;background:{fgc};border-radius:99px;"></div></div>
  <div class="mc-sub" style="color:{fgc};">{fg['label']}</div>
</div>""", unsafe_allow_html=True)

with m2:
    avg_score = sum(a.get("score",0) for a in btc_stories+alt_stories) / max(len(btc_stories+alt_stories),1)
    sl  = "Bullish" if avg_score>=65 else ("Bearish" if avg_score<45 else "Neutral")
    slc = C["green"] if avg_score>=50 else C["red"]
    st.markdown(f"""
<div class="mc" style="background:{slc}1A;border-color:{slc};">
  <div class="mc-lbl">News Sentiment</div>
  <div class="mc-val" style="color:{slc};">{sl}</div>
  <div class="mc-sub" style="color:{slc};">avg score {avg_score:.0f}/100</div>
</div>""", unsafe_allow_html=True)

with m3:
    bp = btc["price"]; bc2 = btc["change_24h"] or 0
    bcc = C["green"] if bc2>=0 else C["red"]
    bvol = btc.get("volume_24h")
    vol_line = f"Vol 24h: ${bvol:,.0f}" if bvol else "Vol 24h: —"
    st.markdown(f"""
<div class="mc" style="background:{bcc}1A;border-color:{bcc};">
  <div class="mc-lbl">BTC Price</div>
  <div class="mc-val" style="color:{bcc};">${f'{bp:,.0f}' if bp else '—'}</div>
  <div class="mc-sub" style="color:{bcc};font-weight:700;">{'+'if bc2>0 else ''}{bc2:.2f}% 24h</div>
  <div class="mc-sub" style="color:{bcc};margin-top:6px;">{vol_line}</div>
</div>""", unsafe_allow_html=True)

with m4:
    bd = glbl["btc_dominance"]
    bdc = C["green"] if (bd or 0) >= 50 else C["red"]
    st.markdown(f"""
<div class="mc" style="background:{bdc}1A;border-color:{bdc};">
  <div class="mc-lbl">BTC Dominance</div>
  <div class="mc-val" style="color:{bdc};">{f'{bd:.1f}%' if bd else '—'}</div>
  <div class="mc-sub" style="color:{bdc};">of total mkt cap</div>
</div>""", unsafe_allow_html=True)

with m5:
    tc = glbl["total_mcap"]; tcc2 = glbl["mcap_change_24h"] or 0
    tccc = C["green"] if tcc2>=0 else C["red"]
    st.markdown(f"""
<div class="mc" style="background:{tccc}1A;border-color:{tccc};">
  <div class="mc-lbl">Total Mkt Cap</div>
  <div class="mc-val" style="color:{tccc};">{'—' if not tc else tc}</div>
  <div class="mc-sub" style="color:{tccc};font-weight:700;">{'+'if tcc2>0 else ''}{tcc2:.2f}% 24h</div>
</div>""", unsafe_allow_html=True)

with m6:
    if best_sec:
        bn,bv = best_sec; bsc = C["green"] if bv >= 0 else C["red"]
        st.markdown(f"""
<div class="mc" style="background:{bsc}1A;border-color:{bsc};">
  <div class="mc-lbl">Top Sector</div>
  <div class="mc-val" style="color:{bsc};">{bn}</div>
  <div class="mc-sub" style="color:{bsc};">{'+'if bv>0 else ''}{bv:.2f}%</div>
</div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Home News Stories — attention filter (separate from BTC/ALT scoring) ──────
st.markdown(
    f'<div class="shdr" style="border-color:{C["orange"]};color:{C["orange"]};">'
    f'📰 News Stories&nbsp;<span style="font-size:10px;color:{C["muted"]};">'
    f'(attention filter · last 24h · top {len(news_stories) if news_stories else 10})</span></div>',
    unsafe_allow_html=True,
)
render_news_stories(news_stories, C, st, score_colour)
st.markdown("<br>", unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(f'<hr style="border-color:{C["border"]};margin:20px 0 10px;">', unsafe_allow_html=True)
st.markdown(
    f'<div style="font-size:11px;color:{C["muted"]};display:flex;gap:20px;flex-wrap:wrap;">'
    f'<span>PM markets loaded: <span style="color:{C["white"]};">{len(pm_markets)}</span></span>'
    f'<span>PM cache: 30 min</span>'
    f'<span>News cache: 10 min</span>'
    f'<span>Market cache: 5 min</span>'
    f'</div>',
    unsafe_allow_html=True,
)