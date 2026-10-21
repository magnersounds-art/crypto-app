"""
market_data.py

Macro market data feed for the Home page.

  - Fear & Greed Index          -> alternative.me   (free, no key)
  - BTC spot price + 24h change -> CoinGecko simple/price (free, no key)
  - BTC dominance / total mcap  -> CoinGecko /global (free, no key)

Public entry point: fetch_market_data() -> dict matching the exact shape
0_home.py's load_market_data() stub already returns, so it can be dropped
in as a one-line wrapper with no other changes to 0_home.py.
"""

import logging
import requests

logger = logging.getLogger(__name__)

FNG_URL              = "https://api.alternative.me/fng/?limit=1"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
COINGECKO_PRICE_URL  = "https://api.coingecko.com/api/v3/simple/price"
TIMEOUT = 10


# ── Fear & Greed ──────────────────────────────────────────────────────────
def _fng_colour(value):
    if value is None:
        return "#888888"
    if value <= 24:  return "#FF3131"   # Extreme Fear
    if value <= 44:  return "#FF5F1F"   # Fear
    if value <= 55:  return "#FFD700"   # Neutral
    if value <= 75:  return "#39FF14"   # Greed
    return "#00FFFF"                    # Extreme Greed


def get_fear_greed():
    try:
        r = requests.get(FNG_URL, timeout=TIMEOUT)
        r.raise_for_status()
        entry = r.json()["data"][0]
        value = int(entry["value"])
        return {
            "value":  value,
            "colour": _fng_colour(value),
            "label":  entry["value_classification"],
        }
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
        return {"value": None, "colour": "#888888", "label": "Unavailable"}


# ── Global market data (BTC dominance, total mcap) ──────────────────────────
def _fmt_mcap(n):
    """Return a display-ready string, e.g. '$2.41T'. 0_home.py renders
    glbl['total_mcap'] directly with no further formatting, so it must
    already be a string (or None)."""
    if n is None:
        return None
    if n >= 1_000_000_000_000:
        return f"${n / 1_000_000_000_000:.2f}T"
    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    return f"${n:,.0f}"


def get_global_data():
    try:
        r = requests.get(COINGECKO_GLOBAL_URL, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()["data"]
        btc_dom    = d.get("market_cap_percentage", {}).get("btc")
        total_mcap = d.get("total_market_cap", {}).get("usd")
        mcap_chg   = d.get("market_cap_change_percentage_24h_usd")
        return {
            "btc_dominance":   round(btc_dom, 1) if btc_dom is not None else None,
            "total_mcap":      _fmt_mcap(total_mcap),
            "mcap_change_24h": round(mcap_chg, 2) if mcap_chg is not None else None,
        }
    except Exception as e:
        logger.warning(f"Global market data fetch failed: {e}")
        return {"btc_dominance": None, "total_mcap": None, "mcap_change_24h": None}


# ── BTC price ────────────────────────────────────────────────────────────
def get_btc_price():
    try:
        r = requests.get(
            COINGECKO_PRICE_URL,
            params={
                "ids": "bitcoin",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()["bitcoin"]
        change = d.get("usd_24h_change")
        return {
            "price":      d.get("usd"),
            "change_24h": round(change, 2) if change is not None else None,
            "volume_24h": d.get("usd_24h_vol"),
        }
    except Exception as e:
        logger.warning(f"BTC price fetch failed: {e}")
        return {"price": None, "change_24h": None, "volume_24h": None}


# ── Combined entry point ────────────────────────────────────────────────────
def fetch_market_data():
    return {
        "fear_greed": get_fear_greed(),
        "global":     get_global_data(),
        "btc":        get_btc_price(),
    }
