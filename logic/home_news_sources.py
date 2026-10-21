"""
logic/home_news_sources.py

Loads and fetches the external "attention" signal sources for the Home
News Stories section. Driven entirely by home_news_sources.csv.

This is SEPARATE from rss_active.csv and the existing RSS ingestion in
0_home.py's load_articles(). That system is untouched and keeps feeding
the BTC/ALT columns. home_news_sources.csv is only the list of external
Top/Featured/Most Read pages checked for ranking signals on Home.

Expected CSV columns:
    name         display name, e.g. "CoinDesk"
    url          page to check
    signal_type  one of: "Top Stories", "Featured Stories",
                 "Most Read - 24h", "Top News"
    enabled      1/0 — rows with 0 are skipped entirely
    score        points awarded when a story matches this source's list
                 (rubric default is 25 for all rows — kept as a column
                 rather than hardcoded so sources can be weighted later
                 without touching code)

Fragility notice: these are live HTML scrapes of outside websites, not
APIs. Site redesigns will break extraction. If a source stops matching,
check _extract_by_heading() (generic sites) or _extract_cryptoslate()
(CryptoSlate's 24h/7d/30d tab layout) first.
"""

import re
import csv
import requests
import streamlit as st
from pathlib import Path
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 10

# signal_type -> (heading pattern to start at, heading pattern to stop at)
HEADING_PATTERNS = {
    "Top Stories":     (r"Top Stories",              r"Latest News|Latest"),
    "Featured Stories": (r"Featured Stories|Featured", r"Most Read|Research|Latest"),
    "Most Read - 24h": (r"Most read",                 r"Markets|Magazine"),
    "Hot Stories":     (r"Hot Stories",                r"Latest|Magazine|Recommended|Press releases"),
}


def load_source_rows(csv_path: Path) -> list[dict]:
    """Read home_news_sources.csv, return enabled rows only. Malformed
    rows are skipped (not fatal) so one bad row can't take the section
    down."""
    rows = []
    if not csv_path.exists():
        return rows
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                enabled_raw = str(row.get("enabled", "1")).strip().lower()
                if enabled_raw not in ("1", "true", "yes"):
                    continue
                rows.append({
                    "name": row["name"].strip(),
                    "url": row["url"].strip(),
                    "signal_type": row["signal_type"].strip(),
                    "score": float(row.get("score", 25) or 25),
                })
            except (KeyError, ValueError):
                continue
    return rows


def _extract_by_heading(soup, start_pattern, stop_pattern, max_items=10):
    """Find a heading matching start_pattern, then collect link text
    that follows it until a heading matching stop_pattern (or max_items)."""
    heading = None
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        if re.search(start_pattern, tag.get_text(), re.IGNORECASE):
            heading = tag
            break
    if heading is None:
        return []

    headlines = []
    for el in heading.find_all_next():
        if el.name and re.match(r"^h[1-6]$", el.name):
            if stop_pattern and re.search(stop_pattern, el.get_text(strip=True), re.IGNORECASE):
                break
        if el.name == "a":
            txt = el.get_text(strip=True)
            if len(txt) >= 15 and txt not in headlines:
                headlines.append(txt)
                if len(headlines) >= max_items:
                    break
    return headlines


def _extract_cryptoslate(soup, max_items=10):
    """CryptoSlate's Top News page renders 24H / 7D / 30D lists with
    near-identical markup. Keep only link text containing '... minute(s)
    /hour(s) ago' — that's how the 24H tab's items read, distinct from
    the 7D/30D tabs ('days ago' / 'weeks ago')."""
    headlines = []
    for a in soup.find_all("a"):
        txt = a.get_text(" ", strip=True)
        if not txt or len(txt) < 15:
            continue
        if re.search(r"\b\d+\s+(minute|hour)s?\s+ago\b", txt, re.IGNORECASE):
            cleaned = re.sub(r"^(Top Story|#\d+)\s*", "", txt).strip()
            cleaned = re.sub(r"\s*\d+\s+(minute|hour)s?\s+ago\s*$", "", cleaned, flags=re.IGNORECASE).strip()
            if cleaned and cleaned not in headlines:
                headlines.append(cleaned)
                if len(headlines) >= max_items:
                    break
    return headlines


def _extract_category_page(soup, max_items=10):
    """For a page that IS already a curated top/featured list — e.g.
    Decrypt's Editors' Picks category page — with no 'Top Stories'-style
    heading sitting above unrelated content to search for. Takes
    headline text straight from heading tags that wrap a link, in page
    order, since the whole page (or its main listing) is already the
    list we want."""
    headlines = []
    for tag in soup.find_all(re.compile(r"^h[1-4]$")):
        a = tag.find("a")
        if a:
            txt = a.get_text(strip=True)
            if len(txt) >= 15 and txt not in headlines:
                headlines.append(txt)
                if len(headlines) >= max_items:
                    break
    return headlines


@st.cache_data(ttl=600, show_spinner=False)
def fetch_source_headlines(name: str, url: str, signal_type: str) -> list:
    """Fetch + extract headlines for one source row. Never raises — an
    unreachable/redesigned site returns [] so it just contributes no
    signal that refresh, instead of breaking the whole section."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        if signal_type == "Top News":
            return _extract_cryptoslate(soup)
        if signal_type == "Category List":
            return _extract_category_page(soup)

        pattern = HEADING_PATTERNS.get(signal_type)
        if pattern is None:
            # Unrecognised signal_type — fall back to treating it as its
            # own heading text (lets you add a new row/type without a
            # code change, though matching may be looser).
            return _extract_by_heading(soup, re.escape(signal_type), None)
        start, stop = pattern
        return _extract_by_heading(soup, start, stop)
    except Exception:
        return []


def fetch_all_signals(csv_path: Path) -> dict:
    """Returns {source_name: {"headlines": [...], "score": float, "signal_type": str}}
    for every enabled row in home_news_sources.csv."""
    signals = {}
    for row in load_source_rows(csv_path):
        headlines = fetch_source_headlines(row["name"], row["url"], row["signal_type"])
        signals[row["name"]] = {
            "headlines": headlines,
            "score": row["score"],
            "signal_type": row["signal_type"],
        }
    return signals
