"""
6_Shill_Tracker.py

Read-only Shill Tracker dashboard.
- Stat cards
- BTC price + hype overlay chart
- Source leaderboard (avg hype + accuracy per horizon)
- Source drill-down table with pending/resolved horizons
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logic.shill_db import (
    init_db,
    log_price_snapshot,
    resolve_pending_horizons,
    count_headlines,
    avg_hype,
    loudest_source,
    source_leaderboard,
    headlines_for_source,
    recent_headlines,
    get_price_series,
    best_backtest_summary,
)
from market_data import get_btc_price

C_BG = "#000000"
C_WHITE = "#FFFFFF"
C_MUTED = "#555555"
C_BORDER = "#222222"
C_GREEN = "#39FF14"
C_CYAN = "#00FFFF"
C_PINK = "#FF1493"
C_CARD = "#0a0a0a"
C_ORANGE = "#F7931A"

st.markdown(
    f"""
<style>
.stApp, .main, section.main, .block-container {{
    background-color: {C_BG} !important;
    color: {C_WHITE} !important;
    font-family: 'Courier New', monospace !important;
}}
section[data-testid="stSidebar"] {{
    background-color: #050505 !important;
    border-right: 1px solid {C_BORDER} !important;
}}
#MainMenu, footer, header {{ visibility: hidden; }}
div[data-testid="stDecoration"] {{ display: none; }}
.block-container {{ padding-top: 1.5rem; }}

.stat-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 18px;
}}
.stat-card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 14px 16px;
}}
.stat-lbl {{
    font-size: 11px;
    color: {C_MUTED};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
}}
.stat-val {{
    font-size: 22px;
    font-weight: 700;
    color: {C_GREEN};
    line-height: 1.2;
}}
.stat-sub {{
    font-size: 11px;
    color: {C_MUTED};
    margin-top: 4px;
}}

.section-hdr {{
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: {C_CYAN};
    border-bottom: 1px solid {C_BORDER};
    padding-bottom: 6px;
    margin: 20px 0 12px;
}}
</style>
""",
    unsafe_allow_html=True,
)

init_db()

hc, ac1, ac2 = st.columns([6, 1.2, 1.2])
with hc:
    st.markdown(
        f'<h1 style="color:{C_WHITE};font-size:26px;margin-bottom:2px;">📡 SHILL TRACKER</h1>',
        unsafe_allow_html=True,
    )
with ac1:
    if st.button("Snapshot BTC", help="Log current BTC price into price_snapshots"):
        btc = get_btc_price()
        price = btc.get("price")
        if price:
            ok = log_price_snapshot(price)
            st.toast(f"Snapshot {'saved' if ok else 'skipped'}: ${price:,.0f}")
        else:
            st.toast("BTC price unavailable", icon="⚠️")
with ac2:
    if st.button("Resolve horizons", help="Backfill pending 24h/7d/14d/30d windows"):
        n = resolve_pending_horizons()
        st.toast(f"Resolved {n} horizon row(s)")

st.markdown(
    f'<p style="color:{C_MUTED};font-size:12px;margin-top:-6px;margin-bottom:14px;">'
    f"Logged headlines · hype scores · BTC reaction by horizon · read-only from SQLite</p>",
    unsafe_allow_html=True,
)

range_label = st.radio(
    "Chart window",
    options=["24h", "7d", "14d", "30d"],
    index=1,
    horizontal=True,
    label_visibility="collapsed",
)
range_hours = {"24h": 24, "7d": 24 * 7, "14d": 24 * 14, "30d": 24 * 30}[range_label]

n_head = count_headlines()
avg_h = avg_hype()
loud = loudest_source()
best = best_backtest_summary()

loud_txt = f"{loud[0]} ({loud[1]})" if loud else "—"
best_txt = f"{best['horizon']}  {best['avg_pct']:+.1f}%" if best else "—"
best_sub = f"n={best['n']} high-hype" if best else "need more resolved data"

st.markdown(
    f"""
<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-lbl">Headlines logged</div>
    <div class="stat-val">{n_head}</div>
    <div class="stat-sub">articles + videos</div>
  </div>
  <div class="stat-card">
    <div class="stat-lbl">Avg hype score</div>
    <div class="stat-val">{avg_h if avg_h is not None else '—'}</div>
    <div class="stat-sub">0 – 10 scale</div>
  </div>
  <div class="stat-card">
    <div class="stat-lbl">Loudest source</div>
    <div class="stat-val" style="font-size:16px;">{loud_txt}</div>
    <div class="stat-sub">highest avg hype (≥3)</div>
  </div>
  <div class="stat-card">
    <div class="stat-lbl">Best backtest</div>
    <div class="stat-val" style="font-size:18px;">{best_txt}</div>
    <div class="stat-sub">{best_sub}</div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown('<div class="section-hdr">PRICE / HYPE OVERLAY</div>', unsafe_allow_html=True)

price_rows = get_price_series(hours=range_hours)
hl_rows = recent_headlines(hours=range_hours, limit=300)

if price_rows:
    pdf = pd.DataFrame(price_rows)
    pdf["timestamp"] = pd.to_datetime(pdf["timestamp"])
    pdf = pdf.set_index("timestamp").sort_index()
    chart_df = pd.DataFrame({"BTC USD": pdf["price"]})
    st.line_chart(chart_df, height=260, use_container_width=True)
else:
    st.info("No price snapshots yet. Click **Snapshot BTC** to start the series.")

if hl_rows:
    hdf = pd.DataFrame(hl_rows)
    hdf["published_at"] = pd.to_datetime(hdf["published_at"])
    hype_series = (
        hdf.set_index("published_at")["hype_score"]
        .resample("1h")
        .mean()
        .dropna()
    )
    if not hype_series.empty:
        st.caption("Mean hype score per hour (same window)")
        st.line_chart(hype_series.to_frame("Hype"), height=180, use_container_width=True)
else:
    st.caption("No headlines in this window yet — open NEWS FEED to log some.")

st.markdown('<div class="section-hdr">SOURCE LEADERBOARD</div>', unsafe_allow_html=True)

board = source_leaderboard()
if board:
    bdf = pd.DataFrame(board)
    bdf = bdf.rename(
        columns={
            "source": "Source",
            "n": "N",
            "avg_hype": "Avg Hype",
            "avg_entry": "Avg Entry $",
            "acc_24h": "24h %",
            "acc_7d": "7d %",
            "acc_14d": "14d %",
            "acc_30d": "30d %",
        }
    )
    st.dataframe(
        bdf,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Avg Hype": st.column_config.NumberColumn(format="%.2f"),
            "Avg Entry $": st.column_config.NumberColumn(format="$%.0f"),
            "24h %": st.column_config.NumberColumn(format="%+.2f%%"),
            "7d %": st.column_config.NumberColumn(format="%+.2f%%"),
            "14d %": st.column_config.NumberColumn(format="%+.2f%%"),
            "30d %": st.column_config.NumberColumn(format="%+.2f%%"),
        },
    )
else:
    st.info("Leaderboard empty — log headlines from the News Feed first.")

st.markdown('<div class="section-hdr">SOURCE DRILL-DOWN</div>', unsafe_allow_html=True)

source_names = [b["source"] for b in board] if board else []
if source_names:
    chosen = st.selectbox("Source", options=source_names)
    detail = headlines_for_source(chosen, limit=80)
    if detail:
        ddf = pd.DataFrame(detail)
        cols = [
            "published_at",
            "title",
            "hype_score",
            "btc_price_at_post",
            "24h_pct",
            "7d_pct",
            "14d_pct",
            "30d_pct",
            "24h_status",
            "7d_status",
            "14d_status",
            "30d_status",
            "url",
        ]
        present = [c for c in cols if c in ddf.columns]
        show = ddf[present].copy()
        show = show.rename(
            columns={
                "published_at": "Published",
                "title": "Title",
                "hype_score": "Hype",
                "btc_price_at_post": "BTC @ post",
                "24h_pct": "24h %",
                "7d_pct": "7d %",
                "14d_pct": "14d %",
                "30d_pct": "30d %",
                "24h_status": "24h",
                "7d_status": "7d",
                "14d_status": "14d",
                "30d_status": "30d",
                "url": "URL",
            }
        )
        st.dataframe(
            show,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Hype": st.column_config.NumberColumn(format="%.2f"),
                "BTC @ post": st.column_config.NumberColumn(format="$%.0f"),
                "24h %": st.column_config.NumberColumn(format="%+.2f%%"),
                "7d %": st.column_config.NumberColumn(format="%+.2f%%"),
                "14d %": st.column_config.NumberColumn(format="%+.2f%%"),
                "30d %": st.column_config.NumberColumn(format="%+.2f%%"),
                "URL": st.column_config.LinkColumn("URL"),
            },
        )
    else:
        st.caption("No rows for this source.")
else:
    st.caption("No sources available yet.")

st.markdown(
    f'<p style="font-size:10px;color:{C_MUTED};margin-top:24px;text-align:center;">'
    f"SHILL TRACKER · data from shill_tracker.db · horizons resolve after windows elapse</p>",
    unsafe_allow_html=True,
)
