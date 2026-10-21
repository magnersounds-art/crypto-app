"""
pages/5_Celestial_Intel.py
========================================================================
CELESTIAL INTEL — QUANTITATIVE CONTEXTUAL FILTERS SYSTEM
========================================================================
"""

import sys
import math
import logging
import requests
import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COLORS = ["#00FFFF","#00FF7F","#FF5F1F","#FF00FF","#007FFF","#8F00FF","#FF3131"]
POS_COLOR  = "#39FF14"
NEG_COLOR  = "#FF3131"
WARN_COLOR = "#FF1493"
WARN_BG    = "#1a000d"

# ══════════════════════════════════════════════════════════════════════════
#  API AUTH CONFIGURATION NODE
# ══════════════════════════════════════════════════════════════════════════
# OPTIONAL: Insert your CoinGecko Key here. If empty, the engine uses your 
# native user-agent rotation or Yahoo Finance fallback to bypass rate limits.
COINGECKO_API_KEY = ""  
IS_PRO_KEY = False # Set to True if using a paid CoinGecko Pro plan

# ══════════════════════════════════════════════════════════════════════════
#  ASTRONOMY & TIME-CYCLE MATRICES
# ══════════════════════════════════════════════════════════════════════════

def lunar_phase(d: date) -> float:
    """Return lunar day 0-29.53 for a given date."""
    known_new = date(2025, 1, 29)
    cycle = 29.530588853
    diff = (d - known_new).days
    return round(((diff % cycle) + cycle) % cycle, 2)

def phase_bucket(phase: float) -> str:
    if phase < 3.5:               return "NEW MOON"
    if phase < 7.4:               return "WAX CRESCENT"
    if phase < 10.4:              return "FIRST QUARTER"
    if phase < 14.8:              return "WAX GIBBOUS"
    if phase < 18.5:              return "FULL MOON"
    if phase < 22.1:              return "WANE GIBBOUS"
    if phase < 25.1:              return "LAST QUARTER"
    return                                "WANE CRESCENT"

def get_sun_sign(d: date) -> str:
    """Maps a calendar date strictly to its Astrological Sun Sign Season."""
    m, day = d.month, d.day
    if (m == 3 and day >= 21) or (m == 4 and day <= 19):  return "ARIES"
    if (m == 4 and day >= 20) or (m == 5 and day <= 20):  return "TAURUS"
    if (m == 5 and day >= 21) or (m == 6 and day <= 20):  return "GEMINI"
    if (m == 6 and day >= 21) or (m == 7 and day <= 22):  return "CANCER"
    if (m == 7 and day >= 23) or (m == 8 and day <= 22):  return "LEO"
    if (m == 8 and day >= 23) or (m == 9 and day <= 22):  return "VIRGO"
    if (m == 9 and day >= 23) or (m == 10 and day <= 22): return "LIBRA"
    if (m == 10 and day >= 23) or (m == 11 and day <= 21):return "SCORPIO"
    if (m == 11 and day >= 22) or (m == 12 and day <= 21):return "SAGITTARIUS"
    if (m == 12 and day >= 22) or (m == 1 and day <= 19): return "CAPRICORN"
    if (m == 1 and day >= 20) or (m == 2 and day <= 18):  return "AQUARIUS"
    return "PISCES"

# Mercury retrograde 2026 data arrays
RETRO_2026 = [
    (date(2026,  2, 25), date(2026,  3, 20), "Feb 25 - Mar 20"),
    (date(2026,  6, 29), date(2026,  7, 23), "Jun 29 - Jul 23"),
    (date(2026, 10, 24), date(2026, 11, 13), "Oct 24 - Nov 13"),
]

def mercury_status(d: date) -> tuple:
    """Returns (is_retro, label_string, is_stationary)"""
    for start, end, label in RETRO_2026:
        # Stationary triggers are calibrated to +/- 2 days around the shift lines
        if abs((d - start).days) <= 2:
            return True, f"STATIONARY RETROGRADE — {label}", True
        if abs((d - end).days) <= 2:
            return False, f"STATIONARY DIRECT — {label}", True
        if start <= d <= end:
            return True, f"ACTIVE — {label}", False
            
    upcoming = [(s, lbl) for s, _, lbl in RETRO_2026 if s > d]
    if upcoming:
        s, lbl = upcoming[0]
        return False, f"CLEAR — next {lbl} in {(s-d).days}d", False
    return False, "CLEAR — no more periods in 2026", False

def get_halving_epoch_year(d: date) -> str:
    """Identifies Bitcoin's position within the 4-year halving cycle engine."""
    y = d.year
    if y in [2012, 2016, 2020, 2024, 2028]: return "HALVING YEAR"
    if y in [2013, 2017, 2021, 2025, 2029]: return "POST-HALVING (EXPANSION)"
    if y in [2014, 2018, 2022, 2026, 2030]: return "BEAR BOTTOM / REPOSITIONING"
    return "PRE-HALVING (ACCUMULATION)"

# Keys match Python's date.weekday() → Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
# (The original dict had the right ruler names but was accessed via (dow+1)%7 which
#  shifted every ruler one day forward — Monday showed Mars instead of Moon, etc.)
DAY_RULERS = {
    0: ("MOON ☽",    "#00FFFF", "SENTIMENT / LIQUIDITY"),      # Monday
    1: ("MARS ♂",    "#FF3131", "AGGRESSION / VOLATILITY"),    # Tuesday
    2: ("MERCURY ☿", "#00FF7F", "DATA / COMMUNICATION"),       # Wednesday
    3: ("JUPITER ♃", "#007FFF", "GROWTH / OPTIMISM"),          # Thursday
    4: ("VENUS ♀",   "#FF00FF", "VALUATION / REBALANCE"),      # Friday
    5: ("SATURN ♄",  "#8F00FF", "RESTRICTION / CORRECTION"),   # Saturday
    6: ("SUN ☉",     "#FF5F1F", "EXPANSION / SPECULATION"),    # Sunday
}

MONTH_RULERS = {
    1:  ("SATURN",    "Capricorn",    "EARTH", "Contraction, debt, structural limits, long-term positioning"),
    2:  ("URANUS",    "Aquarius",     "AIR",   "Sudden reversals, tech disruption, unpredictable breakouts"),
    3:  ("NEPTUNE",   "Pisces",       "WATER", "Speculation, confusion, hype cycles, narrative-driven moves"),
    4:  ("MARS",      "Aries",        "FIRE",  "Aggressive rallies, impulsive buying, sharp reversals"),
    5:  ("VENUS",     "Taurus",       "EARTH", "Slow accumulation, value focus, stable grind higher or lower"),
    6:  ("MERCURY",   "Gemini",       "AIR",   "High volatility, fast news cycles, whipsaws, data-driven"),
    7:  ("MOON",      "Cancer",       "WATER", "Emotional sentiment swings, retail-driven, fear/greed spikes"),
    8:  ("SUN",       "Leo",          "FIRE",  "Confidence peaks, momentum plays, overextension risk"),
    9:  ("MERCURY",   "Virgo",        "EARTH", "Precision moves, system resets, accumulation in quiet grind"),
    10: ("VENUS",     "Libra",        "AIR",   "Rebalancing, asset rotation, structural price bottoms"),
    11: ("MARS",      "Scorpio",      "WATER", "Intensity, hidden power moves, deep corrections or power rallies"),
    12: ("JUPITER",   "Sagittarius",  "FIRE",  "Year-end optimism, breakouts, over-optimism risk"),
}

ZODIAC_ENERGY = {
    "ARIES":       ("FIRE",  "Impulsive, fast-moving. Markets break out aggressively then reverse sharply. Low patience energy."),
    "TAURUS":      ("EARTH", "Slow, stubborn accumulation. Grinding price action. Big moves take time to form. Strong hands dominate."),
    "GEMINI":      ("AIR",   "Dual-natured and unpredictable. Fast whipsaws, contradictory signals. News-driven volatility."),
    "CANCER":      ("WATER", "Emotionally reactive. Sentiment and retail flows dominate. Fear and greed spikes. Liquidity-sensitive."),
    "LEO":         ("FIRE",  "Confidence and spectacle. Bull run energy, momentum trading. Risk of overextension and euphoria."),
    "VIRGO":       ("EARTH", "Analytical, precise. Quiet accumulation phases. System resets. Data over narrative. Measured moves."),
    "LIBRA":       ("AIR",   "Balance-seeking. Asset revaluations and rotations. Structural price bottoms or tops. Indecision."),
    "SCORPIO":     ("WATER", "Intensity and hidden power. Deep corrections or explosive power rallies. Manipulation energy."),
    "SAGITTARIUS": ("FIRE",  "Optimism and expansion. Year-end rally energy. Over-optimism risk. Big bets, big swings."),
    "CAPRICORN":   ("EARTH", "Discipline and contraction. Bear market energy. Long-term repositioning. Institutions dominate."),
    "AQUARIUS":    ("AIR",   "Disruption and innovation. Sudden unexpected reversals. Technology-driven narratives. Community moves."),
    "PISCES":      ("WATER", "Dissolution and speculation. Hype cycles, narrative-over-fundamentals. Confusion, then clarity."),
}

ASPECTS = [
    ("0°",   "CONJUNCTION", "FUSION",   "Planets merge energy — unified explosive force. Watch for breakouts."),
    ("60°",  "SEXTILE",     "HARMONY",  "Supportive flow, low resistance. Gentle rally or quiet accumulation."),
    ("90°",  "SQUARE",      "TENSION",  "Friction and forced action. High volatility, corrections, catalyst events."),
    ("120°", "TRINE",       "FLOW",     "Natural ease and momentum. Sustained trend, aligned conditions."),
    ("180°", "OPPOSITION",  "POLARITY", "Maximum tension between two forces. Blow-off tops or capitulation bottoms."),
]

MACRO_EVENTS = [
    ("VENUS RETROGRADE",    date(2026, 10,  3), date(2026, 11, 14), "Asset revaluation window. Historically correlates with price cooling and asset rotation."),
    ("SATURN-NEPTUNE CONJ", date(2028,  6, 15), None, "Rare 0-degree fusion in Aries-Taurus. Structural rewriting of digital asset systems."),
    ("SHEMITAH OPENS",      date(2028,  9, 20), None, "7-year debt-cleansing cycle opens. Volatility catalyst window."),
    ("SATURN TURNS DIRECT", date(2028,  9, 24), None, "Compressed Saturn restriction energy releases. Structural market moves resume."),
    ("SHEMITAH CLOSES",     date(2029,  9,  9), None, "End of 7-year reset window. Systemic repositioning completes."),
]

def gematria(text: str) -> dict:
    clean = [c.lower() for c in text if c.isalpha()]
    vals  = [ord(c) - 96 for c in clean]
    ordinal = sum(vals)
    return {
        "ORDINAL":      ordinal,
        "REDUCED":      sum((v - 1) % 9 + 1 for v in vals),
        "REVERSE":      sum(27 - v for v in vals),
        "WORDS":        len(text.split()),
        "LETTERS":      len(clean),
        "DIGIT SUM":    sum(int(d) for d in str(ordinal)),
    }

# ══════════════════════════════════════════════════════════════════════════
#  MATH ENGINE (QUANTITATIVE MATRIX INDICATORS)
# ══════════════════════════════════════════════════════════════════════════

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Computes technical trend, volatility, and momentum conditions."""

    # ── EMA-200: only meaningful with enough data ──────────────────────────
    # Require at least 250 rows so the 200-period EMA has had time to converge.
    # On shorter datasets the regime column would be noise, so we mark it N/A.
    if len(df) >= 250:
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
        df["regime"] = df.apply(
            lambda r: "ABOVE 200-EMA" if r["close"] >= r["ema200"] else "BELOW 200-EMA", axis=1
        )
    else:
        df["ema200"] = float("nan")
        df["regime"] = "INSUFFICIENT DATA"

    # ── RSI-14: proper Wilder method with SMA seed ─────────────────────────
    # The original code used EWM alpha=1/14 from bar-1, which causes a multi-
    # hundred-bar warm-up period where RSI stays near 50 and never reaches
    # oversold/overbought thresholds.  Wilder's correct method is:
    #   • Seed: plain 14-bar SMA of gains/losses for the very first average
    #   • Continuation: Wilder smoothing  avg = (prev_avg * 13 + current) / 14
    # This makes RSI accurate from bar 15 onward and matches every charting
    # platform (TradingView, Bloomberg, etc.).
    period = 14
    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    avg_gain = gain.copy() * float("nan")
    avg_loss = loss.copy() * float("nan")

    # First valid average = plain SMA over bars 1-14
    if len(df) > period:
        avg_gain.iloc[period] = gain.iloc[1:period + 1].mean()
        avg_loss.iloc[period] = loss.iloc[1:period + 1].mean()

        # Wilder smoothing from bar 15 onward
        for i in range(period + 1, len(df)):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["rsi"] = 100 - (100 / (1 + rs))

    # ── Volatility (Close-to-Close) ────────────────────────────────────────
    # The original code labelled this "ATR" but there are no high/low columns
    # in the API response — it was just |close[t] - close[t-1]|.  Renamed to
    # "cc_volatility" so the ATR deviation card displays honest data.
    df["cc_volatility"]          = df["close"].diff().abs()
    df["atr"]                    = df["cc_volatility"].rolling(window=14).mean()
    df["historical_avg_atr"]     = df["atr"].rolling(window=365, min_periods=30).mean()
    df["atr_deviation_pct"]      = round(((df["atr"] / df["historical_avg_atr"]) - 1) * 100, 2)

    return df

# ══════════════════════════════════════════════════════════════════════════
#  COINGECKO DATA RETRIEVAL (AUTHENTICATED & RESTORED FALLBACKS)
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_btc_history() -> pd.DataFrame | None:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    
    # Handle subdomain & header assignment based on Key Configuration rules
    base_subdomain = "api.coingecko.com"
    if COINGECKO_API_KEY:
        if IS_PRO_KEY:
            base_subdomain = "pro-api.coingecko.com"
            HEADERS["x-cg-pro-api-key"] = COINGECKO_API_KEY
        else:
            base_subdomain = "api-dev.coingecko.com"
            HEADERS["x-cg-demo-api-key"] = COINGECKO_API_KEY

    ENDPOINTS = [
        (f"https://{base_subdomain}/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=max&interval=daily", "prices"),
        (f"https://{base_subdomain}/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=365&interval=daily", "prices"),
        (f"https://{base_subdomain}/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=90&interval=daily", "prices")
    ]

    prices = None
    for url, key in ENDPOINTS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code == 429:
                import time
                time.sleep(3)
                r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            prices = r.json().get(key, [])
            if len(prices) > 30: break
        except Exception:
            continue

    # FAILSAFE ARCHITECTURE NODE: If CoinGecko firewalls, download via Yahoo Finance Engine
    if not prices or len(prices) < 30:
        try:
            import yfinance as yf
            hist = yf.Ticker("BTC-USD").history(period="max", interval="1d")
            if not hist.empty:
                df = hist.reset_index()
                df["date"] = df["Date"].dt.date
                df["close"] = df["Close"].astype(float)
                df = df[["date", "close"]].sort_values("date").reset_index(drop=True)
                return complete_dataframe_pipeline(df)
        except Exception:
            return None
        return None

    df = pd.DataFrame(prices, columns=["ts", "close"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.date
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    df["close"] = df["close"].astype(float)
    return complete_dataframe_pipeline(df)

def complete_dataframe_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    df["phase"] = df["date"].apply(lunar_phase)
    df["bucket"] = df["phase"].apply(phase_bucket)
    df["sun_season"] = df["date"].apply(get_sun_sign)
    df["halving_epoch"] = df["date"].apply(get_halving_epoch_year)
    df["dow"] = pd.to_datetime(df["date"]).dt.dayofweek
    df["month"] = pd.to_datetime(df["date"]).dt.month
    
    df["retro_tuple"] = df["date"].apply(mercury_status)
    df["retro"] = df["retro_tuple"].apply(lambda x: x[0])
    df["is_stationary"] = df["retro_tuple"].apply(lambda x: x[2])
    df["retro_type"] = df.apply(
        lambda r: "STATIONARY" if r["is_stationary"] else ("RETROGRADE" if r["retro"] else "DIRECT"), axis=1
    )
    return compute_indicators(df)

def backtest_filtered_matrix(df: pd.DataFrame, group_col: str, window: int) -> pd.DataFrame:
    working_df = df.copy()
    working_df["target_fwd_return"] = working_df["close"].shift(-window) / working_df["close"] - 1
    
    grp = (
        working_df.dropna(subset=["target_fwd_return"])
        .groupby(group_col)["target_fwd_return"]
        .agg(
            SAMPLES="count",
            WIN_RATE=lambda x: round((x > 0).mean() * 100, 1),
            AVG_RETURN=lambda x: round(x.mean() * 100, 2),
            MEDIAN_RETURN=lambda x: round(x.median() * 100, 2),
            MAX_RETURN=lambda x: round(x.max() * 100, 2),
            MIN_RETURN=lambda x: round(x.min() * 100, 2)
        )
        .reset_index()
    )
    # Flag statistically thin rows — fewer than 30 samples means win-rate and
    # average return figures are unreliable and should not drive decisions.
    grp["CONFIDENCE"] = grp["SAMPLES"].apply(lambda n: "LOW ⚠" if n < 30 else "OK")
    return grp

# ══════════════════════════════════════════════════════════════════════════
#  STYLE & CUSTOM RENDERING
# ══════════════════════════════════════════════════════════════════════════

def inject_css():
    st.markdown("""
<style>
html,body,[data-testid="stAppViewContainer"],[data-testid="stMain"],[data-testid="block-container"]{
    background:#000!important; font-family:'Courier New',Courier,monospace!important; color:#fff!important;
}
[data-testid="stSidebar"], [data-testid="stHeader"] {background:#000!important}
h1,h2,h3 {font-family:'Courier New',monospace!important; text-transform:uppercase!important; letter-spacing:0.12em!important; color:#fff!important;}
[data-testid="stExpander"]{background:#000!important; border:1px solid #222!important; border-radius:0!important;}
[data-testid="stExpander"] summary{font-family:'Courier New',monospace!important; font-size:11px!important; letter-spacing:0.12em!important; text-transform:uppercase!important; color:#fff!important; background:#000!important;}
[data-testid="stExpander"] summary:hover{background:#0a0a0a!important}
button[kind="secondary"],[data-testid="baseButton-secondary"]{background:transparent!important; border:1px solid #fff!important; color:#fff!important; font-family:'Courier New',monospace!important; text-transform:uppercase!important; font-size:11px!important; border-radius:0!important;}
[data-testid="stProgress"]>div>div{background:#00FFFF!important} [data-testid="stProgress"]>div{background:#111!important}
.stSlider {font-family:'Courier New',monospace!important;}
[data-testid="stAlert"]{background:#0a0a0a!important; border:1px solid #333!important; border-radius:0!important;}
</style>
""", unsafe_allow_html=True)

def card(container, label: str, value: str, sub: str, color: str):
    container.markdown(
        f'<div style="border:1px solid {color};padding:10px 8px;background:transparent;">'
        f'<div style="font-size:9px;letter-spacing:0.1em;color:{color};text-transform:uppercase;margin-bottom:4px">{label}</div>'
        f'<div style="font-size:13px;font-weight:700;color:{color};text-transform:uppercase;line-height:1.2">{value}</div>'
        f'<div style="font-size:9px;color:{color};opacity:0.65;margin-top:5px;text-transform:uppercase;letter-spacing:0.05em">{sub}</div>'
        f'</div>', unsafe_allow_html=True,
    )

def render_custom_table(df: pd.DataFrame, key_col: str, color: str, atr_layer=False, original_df=None):
    if df is None or df.empty:
        st.markdown(f'<p style="color:{WARN_COLOR};font-size:11px">NO DATA MATCHES ACTIVE FILTERS</p>', unsafe_allow_html=True)
        return
    rows_html = ""
    for _, row in df.iterrows():
        wr_c = POS_COLOR if row["WIN_RATE"] >= 55 else (NEG_COLOR if row["WIN_RATE"] <= 45 else "#888888")
        ret_c = POS_COLOR if row["AVG_RETURN"] > 0 else NEG_COLOR
        med_c = POS_COLOR if row["MEDIAN_RETURN"] > 0 else NEG_COLOR
        conf_c = WARN_COLOR if row.get("CONFIDENCE", "OK") == "LOW ⚠" else POS_COLOR
        
        atr_cell = ""
        if atr_layer and original_df is not None:
            # Safely extracts standard baseline log volatility scaling parameter shifts
            atr_v = round(original_df[original_df[key_col] == row[key_col]]["atr_deviation_pct"].mean(), 2)
            if math.isnan(atr_v): atr_v = 0.0
            atr_c = WARN_COLOR if atr_v > 10 else "#888888"
            atr_cell = f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{atr_c}">{atr_v}%</td>'
            
        rows_html += (
            f'<tr>'
            f'<td style="color:{color};padding:5px 10px;border-bottom:1px solid #1a1a1a;font-weight:700">{row[key_col]}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{color}">{int(row["SAMPLES"])}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{conf_c};font-size:10px">{row.get("CONFIDENCE","OK")}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{wr_c};font-weight:700">{row["WIN_RATE"]}%</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{ret_c};font-weight:700">{row["AVG_RETURN"]}%</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{med_c}">{row["MEDIAN_RETURN"]}%</td>'
            f'{atr_cell}'
            f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{POS_COLOR}">{row["MAX_RETURN"]}%</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #1a1a1a;text-align:right;color:{NEG_COLOR}">{row["MIN_RETURN"]}%</td>'
            f'</tr>'
        )
    headers = ["CONDITION/REGIME","SAMPLES","CONFIDENCE","WIN RATE","AVG RETURN","MEDIAN","MAX DEV","MIN DEV"]
    if atr_layer: headers.insert(6, "ATR DEVIATION")
    th = "".join(f'<th style="color:{color};padding:6px 10px;border-bottom:1px solid {color};text-align:{"left" if i==0 else "right"};font-size:10px">{h}</th>' for i, h in enumerate(headers))
    st.markdown(f'<div style="border:1px solid {color}"><table style="width:100%;border-collapse:collapse;font-family:Courier New;font-size:11px"><thead><tr>{th}</tr></thead><tbody>{rows_html}</tbody></table></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════
#  MAIN EXECUTION DASHBOARD LOOP
# ══════════════════════════════════════════════════════════════════════════

def main():
    st.set_page_config(page_title="Celestial Intel System", page_icon="🌌", layout="wide")
    inject_css()

    today, now = date.today(), datetime.now()
    phase = lunar_phase(today)
    bucket = phase_bucket(phase)
    retro, retro_note, stationary = mercury_status(today)
    dow = today.weekday()
    day_name, dr_color, day_energy = DAY_RULERS[dow]
    m_ruler, m_sign, m_flavor, m_energy = MONTH_RULERS[today.month]
    phase_pct = round(phase / 29.53 * 100, 1)

    st.markdown('<h1 style="font-size:22px;font-weight:700;letter-spacing:0.14em;">🌌 CELESTIAL QUANTMATRIX ENGINE</h1>', unsafe_allow_html=True)
    
    with st.spinner("PROCESSING INTERACTIVE MULTI-VARIABLE DATA CLOUD..."):
        df = fetch_btc_history()

    # Dynamic Optimization Controls Node Sidebar Panel Injection
    st.sidebar.markdown('<p style="color:#00FFFF;font-size:12px;font-weight:700;margin-top:10px">⚙️ FILTER ENGINE ROUTING</p>', unsafe_allow_html=True)
    trend_filter = st.sidebar.selectbox("EMA TREND REGIME", ["ALL CONTEXTS", "ABOVE 200-EMA (BULL RUNS)", "BELOW 200-EMA (BEAR CONTEXTS)"])
    rsi_filter = st.sidebar.selectbox("DAILY MOMENTUM (RSI)", ["ALL CONTEXTS", "OVERSOLD ONLY (RSI < 35)", "OVERBOUGHT ONLY (RSI > 65)"])
    halving_filter = st.sidebar.selectbox("4-YEAR EPOCH SEGMENTATION", ["ALL CYCLES", "HALVING YEAR", "POST-HALVING (EXPANSION)", "BEAR BOTTOM / REPOSITIONING", "PRE-HALVING (ACCUMULATION)"])
    lookforward_days = st.sidebar.slider("FORWARD POSITION TARGET WINDOW (DAYS)", 1, 45, 7)

    if df is None:
        st.error("SYSTEM ERROR: HISTORICAL CORE RETRIEVAL FAILURE. CHECK INTERNET ROUTING DATA FLOWS.")
        return

    # Master Filter Pipeline Matrix Application Layer
    f_df = df.copy()
    if trend_filter == "ABOVE 200-EMA (BULL RUNS)": f_df = f_df[f_df["regime"] == "ABOVE 200-EMA"]
    elif trend_filter == "BELOW 200-EMA (BEAR CONTEXTS)": f_df = f_df[f_df["regime"] == "BELOW 200-EMA"]
        
    if rsi_filter == "OVERSOLD ONLY (RSI < 35)": f_df = f_df[f_df["rsi"] < 35]
    elif rsi_filter == "OVERBOUGHT ONLY (RSI > 65)": f_df = f_df[f_df["rsi"] > 65]
        
    if halving_filter != "ALL CYCLES": f_df = f_df[f_df["halving_epoch"] == halving_filter]

    # SECTION 1 — CORE SYSTEM TELEMETRY
    with st.expander("01 — ENGINE TELEMETRY MATRIX", expanded=True):
        cols = st.columns(7)
        card(cols[0], "LUNAR SIGNATURE", bucket, f"DAY {phase} / 29.53", COLORS[0])
        card(cols[1], "SIGNAL DIRECTION", "REDUCE / SELL" if phase < 3.5 or 12 <= phase <= 16 else "ACCUMULATE", "MATRIX SIGNAL", COLORS[1])
        card(cols[2], "MERCURY VELOCITY", "RETROGRADE" if retro else "DIRECT", retro_note[:28], NEG_COLOR if retro else POS_COLOR)
        card(cols[3], "MONTH RULER", m_ruler, f"{m_sign} / {m_flavor}", COLORS[3])
        card(cols[4], "CURRENT REGIME", f_df.iloc[-1]["regime"] if not f_df.empty else "NO DATA", f"RSI VALUE: {df.iloc[-1]['rsi']:.1f}", COLORS[4])
        card(cols[5], "DAY RULER", day_name, day_energy[:22], dr_color)
        card(cols[6], "CYCLE MATRIX %", f"{phase_pct}%", "PROGRESSION", COLORS[6])
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        st.progress(phase / 29.53)
        
        latest = df.iloc[-1]
        st.markdown(f'<p style="font-size:13px;margin-top:10px"><span style="color:#555">BTC CLOSE: </span><span style="color:#fff;font-weight:700">${latest["close"]:,.0f}</span> <span style="color:#333;margin-left:12px">TARGET SELECTIONS CALIBRATED TO: {lookforward_days}D FORWARD RETURNS</span></p>', unsafe_allow_html=True)

    # SECTION 2 — LUNAR FILTER INTERACTION ENGINE
    with st.expander("02 — BACKTEST: LUNAR PHASE vs BTC RETURNS"):
        st.markdown(f'<p style="font-size:10px;color:#444">Current Multi-Variable Engine State: {trend_filter} | {rsi_filter} | Calculating: {lookforward_days}-Day Fwd Windows</p>', unsafe_allow_html=True)
        bt_lunar = backtest_filtered_matrix(f_df, "bucket", lookforward_days)
        render_custom_table(bt_lunar, "bucket", COLORS[0])

    # SECTION 3 — MERCURY RETROGRADE PIVOTS & ATR MATRIX
    with st.expander("03 — BACKTEST: MERCURY RETROGRADE vs BTC RETURNS"):
        st.markdown('<p style="font-size:10px;color:#444">Isolating High-Friction Stationary Nodes (+/- 2 Days from Direct/Retro lines) and Mapping Systemic ATR Volume Shifts.</p>', unsafe_allow_html=True)
        bt_retro = backtest_filtered_matrix(f_df, "retro_type", lookforward_days)
        render_custom_table(bt_retro, "retro_type", COLORS[1], atr_layer=True, original_df=f_df)

    # SECTION 4 — PLANETARY WEEKLY MATRIX
    with st.expander("04 — BACKTEST: DAY OF WEEK vs BTC RETURNS"):
        day_map = {0:"MON (MOON)", 1:"TUE (MARS)", 2:"WED (MERCURY)", 3:"THU (JUPITER)", 4:"FRI (VENUS)", 5:"SAT (SATURN)", 6:"SUN (SUN)"}
        f_df["dow_label"] = f_df["dow"].map(day_map)
        bt_dow = backtest_filtered_matrix(f_df, "dow_label", lookforward_days)
        render_custom_table(bt_dow, "dow_label", COLORS[2])

    # SECTION 5 — SUN SEASONS vs HISTORICAL HALVING DELTAS
    with st.expander("05 — BACKTEST: ASTRO SEASONS & CYCLICAL DIVERGENCE"):
        st.markdown('<p style="font-size:11px;color:#00FF7F">LAYER A: DATA STRUCTURALLY SORTED BY ASTROLOGICAL SUN SIGN SEASONS (NOT GREGORIAN MONTHS)</p>', unsafe_allow_html=True)
        bt_seasons = backtest_filtered_matrix(f_df, "sun_season", lookforward_days)
        render_custom_table(bt_seasons, "sun_season", COLORS[3])
        
        st.markdown('<p style="font-size:11px;color:#FF5F1F;margin-top:20px">LAYER B: BASELINE MATRIX — HISTORICAL PERFORMANCE ARCHIVE BY 4-YEAR HALVING STAGE SEQUENCE</p>', unsafe_allow_html=True)
        bt_epoch = backtest_filtered_matrix(df, "halving_epoch", lookforward_days)
        render_custom_table(bt_epoch, "halving_epoch", COLORS[4])

    # SECTION 6 — GEMATRIA DECODER TERMINAL
    with st.expander("06 — GEMATRIA LINK ENGINE"):
        g1, g2 = st.columns([5, 1])
        with g1: headline = st.text_input(label="headline", label_visibility="hidden", placeholder="ENTER HEADLINE...", key="gem_input")
        with g2: decode_btn = st.button("DECODE", use_container_width=True)
        if decode_btn and headline:
            g = gematria(headline)
            gcols = st.columns(6)
            for col, (lbl, val) in zip(gcols, list(g.items())):
                card(col, lbl, str(val), "", COLORS[5])

    # SECTIONS 7, 8, & 9 — LEGACY INFORMATION ARRAYS
    with St_Expander_Data_Protection():
        render_legacy_tables(day_name, m_sign, m_flavor, retro)

class St_Expander_Data_Protection:
    """Wrapper class ensuring safety layout mechanics for underlying asset references."""
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

def render_legacy_tables(day_name, m_sign, m_flavor, retro):
    with st.expander("07 — ZODIAC SIGN ENERGIES REFERENCE"):
        rows_html = "".join(f'<tr><td style="color:{COLORS[i%7]};padding:6px 10px;border-bottom:1px solid #1a1a1a;font-weight:700">{k}</td><td style="color:{COLORS[i%7]};padding:6px 10px;border-bottom:1px solid #1a1a1a">{v[0]}</td><td style="color:#aaa;padding:6px 10px;border-bottom:1px solid #1a1a1a;font-size:11px">{v[1]}</td></tr>' for i, (k, v) in enumerate(ZODIAC_ENERGY.items()))
        st.markdown(f'<div style="border:1px solid {COLORS[6]}"><table style="width:100%;border-collapse:collapse;font-family:Courier New;font-size:11px"><thead><tr><th style="text-align:left;color:{COLORS[6]}">SIGN</th><th style="text-align:left;color:{COLORS[6]}">ELEMENT</th><th style="text-align:left;color:{COLORS[6]}">MARKET CHARACTERISTICS</th></tr></thead><tbody>{rows_html}</tbody></table></div>', unsafe_allow_html=True)
        
    with st.expander("08 — PLANETARY DAY RULERS DICTIONARY"):
        st.dataframe(pd.DataFrame(DAY_RULERS).T.rename(columns={0:"RULER", 1:"HEX", 2:"SYS METRIC"}), use_container_width=True)
        
    with st.expander("09 — MONTH RULERSHIP GUIDE"):
        st.dataframe(pd.DataFrame(MONTH_RULERS).T.rename(columns={0:"RULER", 1:"SIGN", 2:"ELEMENT", 3:"ENERGY REGIME"}), use_container_width=True)

    with st.expander("10 — MERCURY RETROGRADE 2026 CALENDAR"):
        rows_html = "".join(f'<tr><td style="color:{COLORS[i%7]};padding:8px 10px;border-bottom:1px solid #1a1a1a;font-weight:700">{l}</td><td style="color:#aaa;padding:8px 10px;border-bottom:1px solid #1a1a1a">{s.strftime("%d %b %Y")}</td><td style="color:#aaa;padding:8px 10px;border-bottom:1px solid #1a1a1a">{e.strftime("%d %b %Y")}</td></tr>' for i, (s, e, l) in enumerate(RETRO_2026))
        st.markdown(f'<div style="border:1px solid {COLORS[2]}"><table style="width:100%;border-collapse:collapse;font-family:Courier New;font-size:11px"><thead><tr><th style="text-align:left;color:{COLORS[2]}">WINDOW</th><th style="text-align:left;color:{COLORS[2]}">START LINE</th><th style="text-align:left;color:{COLORS[2]}">END LINE</th></tr></thead><tbody>{rows_html}</tbody></table></div>', unsafe_allow_html=True)

    with st.expander("11 — MACRO TIMELINE MAPS"):
        st.dataframe(pd.DataFrame(MACRO_EVENTS, columns=["EVENT", "START", "END", "CATALYST DESCRIPTION"]), use_container_width=True)
        
    with st.expander("12 — TRANSIT ASPECT CODES"):
        st.dataframe(pd.DataFrame(ASPECTS, columns=["DEGREES", "ASPECT", "ENERGY PHASE", "STRUCTURAL MARKET ACTION"]), use_container_width=True)

    st.divider()
    st.markdown('<p style="font-size:9px;color:#222;letter-spacing:0.06em;text-align:center">CELESTIAL INTEL · DATA SYSTEMS LOG LINK ACCELERATED · BACKTESTS REPRESENT CYCLICAL HISTORICAL CORRELATION ONLY</p>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()