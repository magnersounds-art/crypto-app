import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
COINGECKO_URL      = "https://api.coingecko.com/api/v3/coins/markets"
OUTLIER_THRESHOLD  = 2.5
BATCH_SIZE         = 30          # smaller batches = less chance of 429

st.set_page_config(page_title="Sectors", layout="wide")

# ─────────────────────────────────────────────
# SECTORS & COLOURS
# ─────────────────────────────────────────────
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

SECTOR_COLOURS = {
    "AI":        "#00FFFF",
    "RWA":       "#00FF7F",
    "Layer 1":   "#FF5F1F",
    "Gaming":    "#FF00FF",
    "ISO 20022": "#007FFF",
    "Privacy":   "#8F00FF",
    "Memes":     "#FF3131",
    "DePIN":     "#CCFF00",
    "DeFi":      "#4D4DFF",
}

POS_COL     = "#39FF14"
NEG_COL     = "#FF3131"
OUTLIER_COL = "#FF1493"
OUTLIER_BG  = "#1a000d"

# ─────────────────────────────────────────────
# BATCHED FETCH (with rate-limit protection)
# ─────────────────────────────────────────────
def chunk_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]

def make_session():
    session = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

@st.cache_data(ttl=900)   # 15 minutes – plenty for a sector overview
def fetch_all():
    all_ids  = sorted({cid for ids in SECTORS.values() for cid in ids})
    coin_map = {}
    session  = make_session()

    for i, batch in enumerate(chunk_list(all_ids, BATCH_SIZE)):
        if i > 0:
            time.sleep(1.8)          # polite pause between batches

        resp = session.get(
            COINGECKO_URL,
            params={
                "vs_currency": "usd",
                "ids":         ",".join(batch),
                "per_page":    str(BATCH_SIZE),
                "sparkline":   "false",
            },
            timeout=25,
        )
        resp.raise_for_status()
        for coin in resp.json():
            coin_map[coin["id"]] = coin

    return coin_map

# ─────────────────────────────────────────────
# SUMMARISE
# ─────────────────────────────────────────────
def summarize(coins):
    rows    = []
    changes = []
    for c in coins:
        chg = c.get("price_change_percentage_24h") or 0.0
        changes.append(chg)
        rows.append({
            "Coin":    c.get("name", "?"),
            "Symbol":  c.get("symbol", "").upper(),
            "Price":   c.get("current_price") or 0,
            "Mkt Cap": c.get("market_cap") or 0,
            "24h %":   round(chg, 2),
            "Volume":  c.get("total_volume") or 0,
        })
    avg       = sum(changes) / len(changes) if changes else 0.0
    threshold = max(abs(avg) * OUTLIER_THRESHOLD, 5)
    outlier   = next((r for r in rows if abs(r["24h %"] - avg) > threshold), None)
    return {"avg": avg, "rows": rows, "outlier": outlier}

# ─────────────────────────────────────────────
# GLOBAL BASE STYLES
# ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');

html, body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="block-container"] {
    background-color: #000000 !important;
    color: #ffffff !important;
    font-family: 'JetBrains Mono', 'Fira Mono', 'Courier New', monospace !important;
}
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] * {
    font-family: 'JetBrains Mono', 'Fira Mono', 'Courier New', monospace !important;
}
[data-testid="stSidebar"] { background-color: #000000 !important; }

/* Strip ALL default expander chrome so our per-sector styles win cleanly */
[data-testid="stExpander"] {
    border: none !important;
    background: #000000 !important;
    box-shadow: none !important;
}
[data-testid="stExpander"] details {
    background: #000000 !important;
    border-radius: 6px !important;
}
[data-testid="stExpander"] details summary {
    background: #000000 !important;
    border-radius: 6px !important;
    list-style: none !important;
}
[data-testid="stExpander"] details summary::-webkit-details-marker { display: none; }
[data-testid="stExpander"] details summary:hover { background: #050505 !important; }

/* Dataframe iframe */
[data-testid="stDataFrame"] { background: #000000 !important; }

#MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────
with st.spinner("Fetching market data…"):
    try:
        coin_map = fetch_all()
    except Exception as e:
        st.error(f"Failed to fetch data: {e}")
        st.stop()

sector_data = {
    name: summarize([coin_map[cid] for cid in ids if cid in coin_map])
    for name, ids in SECTORS.items()
}

# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────
st.markdown(f"""
<div style="margin-bottom:4px;">
    <span style="font-size:24px;font-weight:700;color:#ffffff;
                 font-family:'JetBrains Mono',monospace;letter-spacing:0.06em;">
        SECTORS
    </span>
</div>
<div style="font-size:11px;color:#ffffff;font-family:'JetBrains Mono',monospace;
            letter-spacing:0.05em;margin-bottom:28px;">
    {datetime.now().strftime('%H:%M:%S')} &nbsp;·&nbsp; COINGECKO
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# OVERVIEW — Intel Cards
# ─────────────────────────────────────────────
st.markdown("""
<div style="font-size:10px;color:#666666;text-transform:uppercase;letter-spacing:0.16em;
            margin-bottom:14px;font-family:'JetBrains Mono',monospace;">Overview</div>
""", unsafe_allow_html=True)

cols = st.columns(len(SECTORS))

for col, (name, data) in zip(cols, sector_data.items()):
    colour  = SECTOR_COLOURS[name]
    avg     = data["avg"]
    sign    = "+" if avg >= 0 else ""
    pct_col = POS_COL if avg >= 0 else NEG_COL
    outlier = data["outlier"]

    outlier_html = (
        f'<div style="color:{OUTLIER_COL};font-size:10px;margin-top:6px;font-weight:700;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">&#9650; {outlier["Symbol"]}</div>'
        if outlier else ""
    )

    with col:
        st.markdown(f"""
<div style="border:1px solid {colour};border-top:3px solid {colour};
            padding:12px 8px 10px;border-radius:6px;background:#000000;
            font-family:'JetBrains Mono',monospace;text-align:center;
            box-sizing:border-box;width:100%;overflow:hidden;">
    <div style="color:{colour};font-weight:700;font-size:9px;letter-spacing:0.10em;
                margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
        {name.upper()}
    </div>
    <div style="color:{pct_col};font-size:16px;font-weight:700;line-height:1.2;
                white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
        {sign}{avg:.2f}%
    </div>
    <div style="color:#ffffff;font-size:9px;margin-top:6px;letter-spacing:0.06em;">
        {len(data["rows"])} COINS
    </div>
    {outlier_html}
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# DIVIDER
# ─────────────────────────────────────────────
st.markdown("<hr style='border:none;border-top:1px solid #1a1a1a;margin:28px 0;'>",
            unsafe_allow_html=True)

# ─────────────────────────────────────────────
# COIN BREAKDOWN
# ─────────────────────────────────────────────
st.markdown("""
<div style="font-size:10px;color:#666666;text-transform:uppercase;letter-spacing:0.16em;
            margin-bottom:14px;font-family:'JetBrains Mono',monospace;">Coin Breakdown</div>
""", unsafe_allow_html=True)

for name, data in sector_data.items():
    avg     = data["avg"]
    sign    = "+" if avg >= 0 else ""
    colour  = SECTOR_COLOURS[name]
    pct_col = POS_COL if avg >= 0 else NEG_COL
    out_sym = data["outlier"]["Symbol"] if data["outlier"] else None
    # Unique slug for CSS scoping  e.g. "iso-20022", "layer-1"
    slug    = name.lower().replace(" ", "-").replace("/", "-")

    # Inject per-sector CSS scoped to a wrapper id we place just before the expander
    st.markdown(f"""
<style>
/* ── {name} expander border ── */
#sector-{slug} ~ div [data-testid="stExpander"] details {{
    border: 1px solid {colour} !important;
    border-top: 3px solid {colour} !important;
    border-radius: 6px !important;
    background: #000000 !important;
    margin-bottom: 8px !important;
}}
/* summary bar — never change colour on hover */
#sector-{slug} ~ div [data-testid="stExpander"] details summary,
#sector-{slug} ~ div [data-testid="stExpander"] details summary:hover {{
    background: #000000 !important;
    padding: 10px 14px !important;
    cursor: pointer !important;
}}
/* ALL text inside summary — sector colour, including on hover */
#sector-{slug} ~ div [data-testid="stExpander"] details summary *,
#sector-{slug} ~ div [data-testid="stExpander"] details summary:hover *,
#sector-{slug} ~ div [data-testid="stExpander"] details summary p,
#sector-{slug} ~ div [data-testid="stExpander"] details summary:hover p {{
    color: {colour} !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 13px !important;
    font-weight: 700 !important;
    letter-spacing: 0.06em !important;
}}
/* Percentage via ::after — green or red, not overridden by hover */
#sector-{slug} ~ div [data-testid="stExpander"] details summary p::after,
#sector-{slug} ~ div [data-testid="stExpander"] details summary:hover p::after {{
    content: "   {sign}{avg:.2f}%" !important;
    color: {pct_col} !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 13px !important;
    font-weight: 700 !important;
}}
/* chevron */
#sector-{slug} ~ div [data-testid="stExpander"] details summary svg,
#sector-{slug} ~ div [data-testid="stExpander"] details summary:hover svg {{
    fill: {colour} !important;
    stroke: {colour} !important;
}}
</style>
<div id="sector-{slug}"></div>
""", unsafe_allow_html=True)

    with st.expander(f"{name.upper()}"):

        df = pd.DataFrame(data["rows"])

        def combined_style(row, _out_sym=out_sym, _colour=colour):
            if _out_sym and row["Symbol"] == _out_sym:
                return [f"background-color:{OUTLIER_BG};color:{OUTLIER_COL};font-weight:700"] * len(row)
            chg_col = POS_COL if row["24h %"] >= 0 else NEG_COL
            return [
                f"color:{_colour};font-weight:700;background-color:#000000",
                f"color:{_colour};opacity:0.7;background-color:#000000",
                f"color:{_colour};background-color:#000000;text-align:right",
                f"color:{_colour};background-color:#000000;text-align:right",
                f"color:{chg_col};font-weight:700;background-color:#000000;text-align:right",
                f"color:{_colour};background-color:#000000;text-align:right",
            ]

        styled = (
            df.style
            .apply(combined_style, axis=1)
            .format({
                "Price":   "${:,.4f}",
                "Mkt Cap": "${:,.0f}",
                "24h %":   "{:+.2f}%",
                "Volume":  "${:,.0f}",
            })
            .set_table_styles([
                {
                    "selector": "thead th",
                    "props": [
                        ("background-color", "#000000"),
                        ("color",            colour),
                        ("font-family",      "'JetBrains Mono', monospace"),
                        ("font-size",        "10px"),
                        ("font-weight",      "700"),
                        ("text-transform",   "uppercase"),
                        ("letter-spacing",   "0.08em"),
                        ("border-bottom",    "1px solid #333333"),
                        ("border-top",       "none"),
                        ("border-left",      "none"),
                        ("border-right",     "none"),
                        ("padding",          "8px 10px"),
                    ],
                },
                {
                    "selector": "td",
                    "props": [
                        ("font-family",   "'JetBrains Mono', monospace"),
                        ("font-size",     "12px"),
                        ("padding",       "7px 10px"),
                        ("border-bottom", "1px solid #333333"),
                        ("border-top",    "none"),
                        ("border-left",   "none"),
                        ("border-right",  "none"),
                    ],
                },
                {
                    "selector": "table",
                    "props": [
                        ("background-color", "#000000"),
                        ("border-collapse",  "collapse"),
                        ("width",            "100%"),
                    ],
                },
                {
                    "selector": "tr:last-child td",
                    "props": [("border-bottom", "none")],
                },
            ])
            .hide(axis="index")
        )

        st.dataframe(styled, use_container_width=True, hide_index=True)
