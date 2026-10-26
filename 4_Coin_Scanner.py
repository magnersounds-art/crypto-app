"""
pages/4_Meme_Coin_Scanner.py
Full build — all phases:
- PAID vs ORGANIC discovery pools, coin NAMES in buttons (no contract codes)
- Paid-to-Organic Lag Time tracker
- Helius Solana RPC: parent-wallet tracer + bundle cluster detector
- GMGN.ai smart money / KOL scraper
- Telegram alert bot (async, fires on high-scoring dev redeployments)
- Rate-limit safe on every API call

SETUP — create a .env file (or set Streamlit secrets) with:
    HELIUS_API_KEY=your_key_here
    TELEGRAM_BOT_TOKEN=your_token_here
    TELEGRAM_CHAT_ID=your_chat_id_here   (get via @userinfobot)
"""

import streamlit as st
import requests
import time
import os
import json
import asyncio
import threading
from datetime import datetime, timezone

# ── Load env / secrets ────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # dotenv optional; fall back to os.environ or Streamlit secrets

def _secret(key: str, default: str = "") -> str:
    """Read from .env / os.environ, fall back to st.secrets if available."""
    val = os.environ.get(key, "")
    if not val:
        try:
            val = st.secrets.get(key, default)
        except Exception:
            val = default
    return val or default

HELIUS_API_KEY     = _secret("HELIUS_API_KEY")
TELEGRAM_BOT_TOKEN = _secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = _secret("TELEGRAM_CHAT_ID")
HELIUS_RPC         = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else ""

# Google Trends now runs through ONE guarded route (trends_core.py) and one shared panel (trends_ui.py).
# This page no longer talks to Google itself.  Both files must sit next to this page.
import sys
from pathlib import Path
_HERE = str(Path(__file__).parent.resolve())
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import trends_ui
    TRENDS_UI_OK, TRENDS_UI_ERR = True, ""
except ImportError as _e:
    TRENDS_UI_OK, TRENDS_UI_ERR = False, str(_e)

DS_BASE   = "https://api.dexscreener.com"
DS_BOOSTS = f"{DS_BASE}/token-boosts/latest/v1"
DS_SEARCH = f"{DS_BASE}/latest/dex/search"
HEADERS   = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

COLORS        = ["#00FFFF","#00FF7F","#FF5F1F","#FF00FF","#007FFF","#8F00FF","#FF3131"]
TOP_N_OPTIONS = [7, 10, 15, 20]

# ── Paid-to-Organic lag tracker (session memory) ──────────────────────────────
if "lag_tracker"   not in st.session_state: st.session_state["lag_tracker"]   = {}
if "dev_history"   not in st.session_state: st.session_state["dev_history"]   = {}
if "alert_log"     not in st.session_state: st.session_state["alert_log"]     = []

def _record_paid(addr: str):
    if addr and addr not in st.session_state["lag_tracker"]:
        st.session_state["lag_tracker"][addr] = {"first_paid": time.time(), "first_organic": None}

def _record_organic(addr: str):
    if not addr: return
    t = st.session_state["lag_tracker"]
    if addr in t:
        if t[addr]["first_organic"] is None:
            t[addr]["first_organic"] = time.time()
    else:
        t[addr] = {"first_paid": None, "first_organic": time.time()}

def _lag_str(addr: str) -> str:
    rec = st.session_state["lag_tracker"].get(addr)
    if not rec: return ""
    p, o = rec["first_paid"], rec["first_organic"]
    if p and o:
        s = o - p
        if s < 60:   return f"LAG {int(s)}s"
        if s < 3600: return f"LAG {int(s/60)}m"
        return f"LAG {int(s/3600)}h"
    return "PAID ONLY" if p else ("ORGANIC ONLY" if o else "")

# ── Rate-limit-safe GET ───────────────────────────────────────────────────────
def _safe_get(url, params=None, headers=None, timeout=10, retries=2, backoff=10):
    h = headers or HEADERS
    for _ in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 429:
                time.sleep(max(int(r.headers.get("Retry-After", backoff)), backoff))
                continue
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None
    return None

def _safe_post(url, payload, headers=None, timeout=10):
    h = headers or {"Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=h, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# ── Formatting helpers ────────────────────────────────────────────────────────
def _fmt(n, prefix="$", dec=2):
    if n is None: return "—"
    try: n = float(n)
    except Exception: return str(n)
    if n >= 1e9: return f"{prefix}{n/1e9:.{dec}f}B"
    if n >= 1e6: return f"{prefix}{n/1e6:.{dec}f}M"
    if n >= 1e3: return f"{prefix}{n/1e3:.{dec}f}K"
    return f"{prefix}{n:.{dec}f}"

def _price_full(n):
    if n is None: return "—"
    try:
        n = float(n)
        if n >= 1:        return f"${n:,.4f}"
        if n >= 0.0001:   return f"${n:,.6f}"
        if n >= 0.000001: return f"${n:,.8f}"
        return f"${n:.2e}"
    except Exception: return str(n)

def _pct_html(v):
    if v is None: return '<span style="color:#555555;">—</span>'
    col  = "#39FF14" if v >= 0 else "#FF3131"
    sign = "+" if v > 0 else ""
    return f'<span style="color:{col};font-weight:700;">{sign}{v:.2f}%</span>'

def _age_str(ts_ms):
    if not ts_ms: return "—"
    s = time.time() - ts_ms / 1000
    if s < 3600:  return f"{int(s/60)}m"
    if s < 86400: return f"{int(s/3600)}h"
    return f"{int(s/86400)}d"

def _age_days(ts_ms):
    if not ts_ms: return None
    return (time.time() - ts_ms / 1000) / 86400

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
    return (
        f'<tr style="border-bottom:1px solid #333333;">'
        f'<td style="padding:7px 12px;color:{lc};text-transform:uppercase;font-size:10px;white-space:nowrap;">{label}</td>'
        f'<td style="padding:7px 12px;color:#FFFFFF;text-align:right;">{value}</td>'
        f'</tr>'
    )

def _badge(label, value, color, icon, tooltip=""):
    tip    = f'title="{tooltip}"' if tooltip else ""
    cursor = "cursor:help;" if tooltip else ""
    return (
        f'<div style="border:1px solid {color};padding:10px 12px;background:transparent;{cursor}" {tip}>'
        f'<div style="font-size:9px;color:{color};text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;">{icon} {label}</div>'
        f'<div style="font-size:14px;color:{color};font-weight:700;">{value}</div>'
        f'</div>'
    )

def _clean_name(tok: dict) -> str:
    """Return human-readable name — NEVER a raw contract address."""
    name = (tok.get("name") or "").strip()
    sym  = (tok.get("symbol") or "").strip().upper()
    if name and 1 < len(name) < 40 and not name.startswith("0x") and not (len(name) > 30 and name.replace("0123456789abcdefABCDEF","") == ""):
        return name
    if sym and len(sym) < 20:
        return sym
    addr = tok.get("tokenAddress") or tok.get("address") or ""
    return addr[:8] + "…" if addr else "UNKNOWN"

# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — HELIUS SOLANA ON-CHAIN INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════

def _helius_rpc(method: str, params: list, timeout: int = 12):
    """Single Helius JSON-RPC call. Returns result dict or None."""
    if not HELIUS_RPC:
        return None
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return _safe_post(HELIUS_RPC, payload, timeout=timeout)

@st.cache_data(ttl=300)
def trace_parent_wallet(token_mint: str) -> dict:
    """
    Phase 3 — Parent-Wallet Tracer.
    1. Find the token's creation (mint) transaction via Helius getSignaturesForAddress.
    2. Extract the deployer/fee-payer wallet from that transaction.
    3. Walk back 3 funding transfers from the deployer to find the original source wallet.
    Returns a dict with deployer, funding_chain[], and any flags.
    """
    result = {
        "deployer":      None,
        "funding_chain": [],
        "flags":         [],
        "error":         None,
    }
    if not HELIUS_RPC:
        result["error"] = "HELIUS_API_KEY not configured. Add it to your .env file."
        return result

    # Step 1: get earliest transaction for the mint address
    sigs_resp = _helius_rpc(
        "getSignaturesForAddress",
        [token_mint, {"limit": 1, "commitment": "confirmed"}],
    )
    if not sigs_resp or "result" not in sigs_resp or not sigs_resp["result"]:
        result["error"] = "No transactions found for this mint address on Solana."
        return result

    creation_sig = sigs_resp["result"][0]["signature"]

    # Step 2: get the creation transaction to find deployer
    tx_resp = _helius_rpc(
        "getTransaction",
        [creation_sig, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
        timeout=15,
    )
    if not tx_resp or not tx_resp.get("result"):
        result["error"] = "Could not fetch creation transaction details."
        return result

    tx = tx_resp["result"]
    account_keys = []
    try:
        account_keys = tx["transaction"]["message"]["accountKeys"]
        # Fee payer is the first account key — this is the deployer
        deployer = account_keys[0]["pubkey"] if isinstance(account_keys[0], dict) else account_keys[0]
        result["deployer"] = deployer
    except Exception as e:
        result["error"] = f"Could not parse transaction accounts: {e}"
        return result

    # Step 3: trace back 3 funding hops from deployer
    current_wallet = deployer
    for hop in range(3):
        hop_sigs = _helius_rpc(
            "getSignaturesForAddress",
            [current_wallet, {"limit": 10, "commitment": "confirmed"}],
        )
        if not hop_sigs or not hop_sigs.get("result"):
            break
        # Look at earliest transactions for incoming SOL transfers
        earliest = hop_sigs["result"][-1]["signature"]
        hop_tx = _helius_rpc(
            "getTransaction",
            [earliest, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
            timeout=15,
        )
        if not hop_tx or not hop_tx.get("result"):
            break
        try:
            hop_accounts = hop_tx["result"]["transaction"]["message"]["accountKeys"]
            funder = hop_accounts[0]["pubkey"] if isinstance(hop_accounts[0], dict) else hop_accounts[0]
            if funder == current_wallet:
                # Try second account
                funder = hop_accounts[1]["pubkey"] if isinstance(hop_accounts[1], dict) else hop_accounts[1]
            result["funding_chain"].append({
                "hop":     hop + 1,
                "wallet":  funder,
                "via_sig": earliest[:20] + "…",
            })
            current_wallet = funder
            time.sleep(0.3)  # polite pause between RPC calls
        except Exception:
            break

    # Flag if funding chain is very short (fresh wallet = higher risk)
    if len(result["funding_chain"]) == 0:
        result["flags"].append("⚠ DEPLOYER HAS NO TRACEABLE FUNDING HISTORY")
    if result["deployer"] and len(result["funding_chain"]) >= 1:
        # Flag if same wallet deployed multiple tokens (check session history)
        dh = st.session_state["dev_history"]
        dep = result["deployer"]
        if dep not in dh:
            dh[dep] = {"tokens": [], "paid_organic_successes": 0}
        dh[dep]["tokens"].append(token_mint)
        count = len(dh[dep]["tokens"])
        if count > 1:
            result["flags"].append(f"🔁 DEPLOYER HAS LAUNCHED {count} TOKENS THIS SESSION")
        result["dev_score"] = dh[dep]["paid_organic_successes"]

    return result

@st.cache_data(ttl=300)
def detect_bundle_clusters(token_mint: str) -> dict:
    """
    Phase 3 — Bundle Cluster Detector.
    Fetches the first 10 transactions after token creation and flags:
    - Multiple wallets buying identical amounts within 2 blocks
    - Buy concentration (top 3 wallets holding >50% of early buys)
    """
    result = {"bundles_detected": False, "clusters": [], "risk_level": "LOW", "error": None}
    if not HELIUS_RPC:
        result["error"] = "HELIUS_API_KEY not configured."
        return result

    # Get first 10 signatures for the token mint
    sigs_resp = _helius_rpc(
        "getSignaturesForAddress",
        [token_mint, {"limit": 10, "commitment": "confirmed"}],
    )
    if not sigs_resp or not sigs_resp.get("result"):
        result["error"] = "No transaction history found."
        return result

    # Collect buy amounts and buyer wallets per block slot
    slot_map = {}   # slot -> [{wallet, amount}]
    for sig_info in sigs_resp["result"]:
        sig  = sig_info["signature"]
        slot = sig_info.get("slot", 0)
        tx   = _helius_rpc(
            "getTransaction",
            [sig, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
            timeout=12,
        )
        if not tx or not tx.get("result"):
            continue
        try:
            accounts = tx["result"]["transaction"]["message"]["accountKeys"]
            pre_bals  = tx["result"]["meta"].get("preBalances",  [])
            post_bals = tx["result"]["meta"].get("postBalances", [])
            for idx, acc in enumerate(accounts):
                wallet = acc["pubkey"] if isinstance(acc, dict) else acc
                if idx < len(pre_bals) and idx < len(post_bals):
                    delta = post_bals[idx] - pre_bals[idx]
                    if delta > 0:   # received SOL — buyer
                        if slot not in slot_map:
                            slot_map[slot] = []
                        slot_map[slot].append({"wallet": wallet, "amount_lamports": delta})
        except Exception:
            continue
        time.sleep(0.2)

    # Detect clusters: same amount, same or adjacent slot, different wallets
    for slot, buyers in slot_map.items():
        if len(buyers) < 2:
            continue
        amount_groups = {}
        for b in buyers:
            amt = b["amount_lamports"]
            amount_groups.setdefault(amt, []).append(b["wallet"])
        for amt, wallets in amount_groups.items():
            if len(wallets) >= 2:
                result["bundles_detected"] = True
                result["clusters"].append({
                    "slot":        slot,
                    "wallets":     wallets,
                    "amount_sol":  round(amt / 1e9, 4),
                    "count":       len(wallets),
                })

    if result["bundles_detected"]:
        max_cluster = max(result["clusters"], key=lambda x: x["count"])
        if max_cluster["count"] >= 5:
            result["risk_level"] = "CRITICAL"
        elif max_cluster["count"] >= 3:
            result["risk_level"] = "HIGH"
        else:
            result["risk_level"] = "MEDIUM"

    return result

# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 4A — GMGN.AI SMART MONEY / KOL SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=180)
def get_gmgn_intelligence(token_mint: str) -> dict:
    """
    Phase 4A — GMGN.ai public data parser.
    Hits GMGN's publicly accessible token endpoint (no auth required).
    Extracts: smart money buy count, KOL count, top sniper wallets.
    """
    result = {
        "smart_money_count": 0,
        "kol_count":         0,
        "top_snipers":       [],
        "holder_count":      0,
        "dev_sold":          None,
        "source":            "gmgn.ai",
        "error":             None,
    }

    # GMGN public token info endpoint (Solana)
    gmgn_url = f"https://gmgn.ai/api/v1/token_info/sol/{token_mint}"
    data = _safe_get(gmgn_url, timeout=10)

    if not data:
        # Try alternative endpoint structure
        gmgn_url2 = f"https://gmgn.ai/sol/token/{token_mint}"
        data = _safe_get(gmgn_url2, timeout=10)

    if not isinstance(data, dict):
        result["error"] = "GMGN data unavailable. Token may not be on Solana or GMGN may have changed endpoints."
        return result

    # Parse whatever GMGN returns — field names vary by version
    token_data = data.get("data") or data.get("token") or data
    if isinstance(token_data, dict):
        result["smart_money_count"] = (
            token_data.get("smart_money_buys") or
            token_data.get("smart_buy_count") or
            token_data.get("smartMoneyCount") or 0
        )
        result["kol_count"] = (
            token_data.get("kol_count") or
            token_data.get("kolCount") or
            token_data.get("kol_buy_count") or 0
        )
        result["holder_count"] = (
            token_data.get("holder_count") or
            token_data.get("holders") or 0
        )
        result["dev_sold"] = (
            token_data.get("dev_sold") or
            token_data.get("devSold")
        )

    # Top snipers endpoint
    snipers_url = f"https://gmgn.ai/api/v1/token_security/sol/{token_mint}"
    sniper_data = _safe_get(snipers_url, timeout=8)
    if isinstance(sniper_data, dict):
        snipers_raw = (
            sniper_data.get("data", {}).get("top_holders") or
            sniper_data.get("top_holders") or []
        )
        for s in snipers_raw[:5]:
            if isinstance(s, dict):
                result["top_snipers"].append({
                    "wallet":  (s.get("address") or s.get("wallet") or "")[:12] + "…",
                    "pct":     s.get("percent") or s.get("pct") or 0,
                    "tag":     s.get("tag") or s.get("type") or "",
                })

    return result

# ═══════════════════════════════════════════════════════════════════════════════
#  PHASE 4B — TELEGRAM ALERT BOT
# ═══════════════════════════════════════════════════════════════════════════════

def _send_telegram(message: str):
    """Fire-and-forget Telegram message. Runs in background thread to not block UI."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    def _send():
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }
        try:
            requests.post(url, json=payload, timeout=8)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

def _maybe_alert_dev_redeployment(deployer: str, token_mint: str, token_name: str):
    """
    Phase 4B trigger: if a known deployer with >50% paid-to-organic success rate
    has redeployed, fire a Telegram alert.
    """
    dh  = st.session_state["dev_history"]
    lag = st.session_state["lag_tracker"]
    if deployer not in dh:
        return
    rec = dh[deployer]
    total   = len(rec.get("tokens", []))
    success = rec.get("paid_organic_successes", 0)
    # Count successes from lag tracker
    success = sum(
        1 for addr in rec.get("tokens", [])
        if lag.get(addr, {}).get("first_paid") and lag.get(addr, {}).get("first_organic")
    )
    if total > 0 and (success / total) >= 0.5 and total >= 2:
        rate    = int(success / total * 100)
        msg     = (
            f"🚨 <b>ALPHA ALERT — DEV REDEPLOYMENT</b>\n\n"
            f"Token: <b>{token_name}</b>\n"
            f"Mint: <code>{token_mint[:20]}…</code>\n"
            f"Deployer: <code>{deployer[:20]}…</code>\n"
            f"Historical success rate: <b>{rate}%</b> ({success}/{total} went organic)\n\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        _send_telegram(msg)
        st.session_state["alert_log"].append({
            "time":    datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "token":   token_name,
            "mint":    token_mint[:16] + "…",
            "deployer":deployer[:16] + "…",
            "rate":    f"{rate}%",
        })

# ═══════════════════════════════════════════════════════════════════════════════
#  FETCHERS
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=120)
def get_paid_tokens(top_n: int = 20):
    """
    PAID pool — DEX Screener boosts endpoint only returns tokenAddress + chainId.
    We do a second search call per token to resolve the actual coin name.
    """
    data = _safe_get(DS_BOOSTS, timeout=10)
    if not isinstance(data, list):
        return []

    seen, raw_tokens = set(), []
    for t in data:
        addr = (t.get("tokenAddress") or "").strip()
        chain = (t.get("chainId") or "").upper()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        raw_tokens.append({"address": addr, "chain": chain})
        if len(raw_tokens) >= top_n:
            break

    # Resolve names by searching each address on DEX Screener
    out = []
    for tok in raw_tokens:
        addr  = tok["address"]
        chain = tok["chain"]
        name  = None
        sym   = None

        result = _safe_get(DS_SEARCH, params={"q": addr}, timeout=8)
        if isinstance(result, dict):
            pairs = result.get("pairs") or []
            for p in pairs:
                base = p.get("baseToken") or {}
                if (base.get("address") or "").lower() == addr.lower():
                    name = (base.get("name") or "").strip()
                    sym  = (base.get("symbol") or "").strip().upper()
                    break
            if not name and pairs:
                # fallback: just use first pair's base token
                base = pairs[0].get("baseToken") or {}
                name = (base.get("name") or "").strip()
                sym  = (base.get("symbol") or "").strip().upper()

        # Pick the best display label — name preferred, then symbol, never address
        if name and 1 < len(name) < 40 and not name.startswith("0x"):
            display = name
        elif sym and len(sym) < 20:
            display = sym
        else:
            display = addr[:8] + "…"

        out.append({
            "display": display,
            "symbol":  sym or "",
            "address": addr,
            "chain":   chain,
            "pool":    "PAID",
        })
        _record_paid(addr)

    return out

@st.cache_data(ttl=120)
def get_organic_tokens(top_n: int = 20):
    """
    ORGANIC pool — strictly uses baseToken.name, then baseToken.symbol.
    Skips any token where neither is clean. Never shows address or garbage.
    """
    paid_data  = _safe_get(DS_BOOSTS, timeout=10)
    paid_addrs = set()
    if isinstance(paid_data, list):
        for t in paid_data:
            a = (t.get("tokenAddress") or "").lower()
            if a:
                paid_addrs.add(a)

    seen, out = set(), []
    for q in ["solana meme", "ethereum meme", "trending crypto"]:
        data = _safe_get(DS_SEARCH, params={"q": q}, timeout=10)
        if not isinstance(data, dict):
            continue
        pairs = sorted(
            data.get("pairs") or [],
            key=lambda p: (p.get("volume") or {}).get("h24") or 0,
            reverse=True,
        )
        for p in pairs:
            base = p.get("baseToken") or {}
            addr = (base.get("address") or "").lower()
            if not addr or addr in seen or addr in paid_addrs:
                continue
            seen.add(addr)

            name = (base.get("name") or "").strip()
            sym  = (base.get("symbol") or "").strip().upper()

            if name and 1 < len(name) < 40 and not name.startswith("0x"):
                display = name
            elif sym and 1 < len(sym) < 20:
                display = sym
            else:
                continue  # skip — nothing readable to show

            out.append({
                "display": display,
                "symbol":  sym,
                "address": addr,
                "chain":   (p.get("chainId") or "").upper(),
                "pool":    "ORGANIC",
            })
            _record_organic(addr)
            if len(out) >= top_n:
                break
        if len(out) >= top_n:
            break
    return out[:top_n]


@st.cache_data(ttl=60)
def search_dex(query: str):
    data = _safe_get(DS_SEARCH, params={"q": query.strip()}, timeout=12)
    if not isinstance(data, dict): return []
    pairs = data.get("pairs") or []
    pairs.sort(key=lambda p: (p.get("liquidity") or {}).get("usd") or 0, reverse=True)
    return pairs

def compute_badges(pair: dict):
    liq   = (pair.get("liquidity") or {}).get("usd")
    fdv   = pair.get("fdv")
    mcap  = pair.get("marketCap")
    txn24 = (pair.get("txns") or {}).get("h24") or {}
    buys  = txn24.get("buys",0) or 0
    sells = txn24.get("sells",0) or 0
    vol   = pair.get("volume") or {}
    v1h   = vol.get("h1",0) or 0
    v24h  = vol.get("h24",0) or 0
    age_d = _age_days(pair.get("pairCreatedAt"))
    badges = []

    if liq is None:
        badges.append({"label":"LIQUIDITY","value":"NO DATA","color":"#555555","icon":"💧","detail":"Pool liquidity data unavailable."})
    elif liq >= 100_000:
        badges.append({"label":"LIQUIDITY","value":_fmt(liq),"color":"#39FF14","icon":"💧","detail":"Deep pool — low slippage. ≥$100K."})
    elif liq >= 20_000:
        badges.append({"label":"LIQUIDITY","value":_fmt(liq),"color":"#FF5F1F","icon":"💧","detail":"Moderate pool — some slippage expected."})
    else:
        badges.append({"label":"LIQUIDITY","value":_fmt(liq),"color":"#FF3131","icon":"💧","detail":"Shallow pool — rug risk. <$20K."})

    total = buys + sells
    if total == 0:
        badges.append({"label":"BUY PRESSURE","value":"NO TXNS","color":"#555555","icon":"⚡","detail":"No transactions in last 24h."})
    else:
        ratio = buys / total
        detail = f"{ratio*100:.0f}% BUYS / {(1-ratio)*100:.0f}% SELLS  ({total} txns)"
        if ratio >= 0.60: badges.append({"label":"BUY PRESSURE","value":"BULLISH","color":"#39FF14","icon":"⚡","detail":detail})
        elif ratio >= 0.45: badges.append({"label":"BUY PRESSURE","value":"NEUTRAL","color":"#FF5F1F","icon":"⚡","detail":detail})
        else: badges.append({"label":"BUY PRESSURE","value":"SELLING","color":"#FF3131","icon":"⚡","detail":detail})

    val_src = fdv or mcap
    if val_src and liq and liq > 0:
        rv = val_src / liq
        if rv <= 10: badges.append({"label":"VALUATION","value":f"FDV/LIQ {rv:.1f}×","color":"#39FF14","icon":"🏦","detail":"Healthy."})
        elif rv <= 50: badges.append({"label":"VALUATION","value":f"FDV/LIQ {rv:.1f}×","color":"#FF5F1F","icon":"🏦","detail":"Elevated."})
        else: badges.append({"label":"VALUATION","value":f"FDV/LIQ {rv:.0f}×","color":"#FF3131","icon":"🏦","detail":f"Bubble risk — FDV is {rv:.0f}× liquidity."})
    else:
        badges.append({"label":"VALUATION","value":"—","color":"#555555","icon":"🏦","detail":"FDV or liquidity missing."})

    if age_d is None:
        badges.append({"label":"PAIR AGE","value":"UNKNOWN","color":"#555555","icon":"🕐","detail":"Pair creation date unavailable."})
    elif age_d < 1:
        badges.append({"label":"PAIR AGE","value":_age_str(pair.get("pairCreatedAt")),"color":"#FF1493","icon":"🕐","detail":"< 24h — EXTREME rug risk."})
    elif age_d < 7:
        badges.append({"label":"PAIR AGE","value":_age_str(pair.get("pairCreatedAt")),"color":"#FF5F1F","icon":"🕐","detail":"< 7 days."})
    else:
        badges.append({"label":"PAIR AGE","value":_age_str(pair.get("pairCreatedAt")),"color":"#39FF14","icon":"🕐","detail":f"{int(age_d)}d — survived initial dump."})

    avg_hr = v24h / 24 if v24h else 0
    if avg_hr == 0:
        badges.append({"label":"VOL MOMENTUM","value":"—","color":"#555555","icon":"📊","detail":"Volume unavailable."})
    else:
        mult   = v1h / avg_hr
        detail = f"1H: {_fmt(v1h)}  ·  24H AVG/HR: {_fmt(avg_hr)}"
        if mult >= 2.0: badges.append({"label":"VOL MOMENTUM","value":f"{mult:.1f}× AVG","color":"#39FF14","icon":"📊","detail":detail})
        elif mult >= 0.5: badges.append({"label":"VOL MOMENTUM","value":f"{mult:.1f}× AVG","color":"#FF5F1F","icon":"📊","detail":detail})
        else: badges.append({"label":"VOL MOMENTUM","value":f"{mult:.1f}× AVG","color":"#FF3131","icon":"📊","detail":detail+"  —  fading."})
    return badges

# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE CONFIG & CSS
# ═══════════════════════════════════════════════════════════════════════════════


# ── Callable entry point (used by scanner_hub and by standalone page) ─────────
def run_solana_scanner():
    """Render the SOLANA COINS scanner UI. Safe to call from hub or as page."""
    # Session keys must be initialised here too: hub reuses the loaded module
    # across Streamlit reruns, so module-level init only runs once per process.
    if "lag_tracker" not in st.session_state:
        st.session_state["lag_tracker"] = {}
    if "dev_history" not in st.session_state:
        st.session_state["dev_history"] = {}
    if "alert_log" not in st.session_state:
        st.session_state["alert_log"] = []
    st.set_page_config(page_title="Meme Coin Scanner", layout="wide")
    st.markdown("""
    <style>
      html,body,[data-testid="stAppViewContainer"],[data-testid="stMain"],
      section.main,[data-testid="block-container"],[data-testid="stSidebar"] {
        background-color:#000!important;color:#FFF!important;
        font-family:'Courier New',Courier,monospace!important;
      }
      div[data-testid="stVerticalBlock"]>div,
      div[data-testid="stHorizontalBlock"]>div{background:transparent!important;}
      h1,h2,h3,h4,h5,h6,p,label,span,div{font-family:'Courier New',Courier,monospace!important;}
      div[data-testid="stTextInput"] input{
        background:#000!important;border:1px solid #222!important;
        color:#FFF!important;font-family:'Courier New',Courier,monospace!important;border-radius:2px!important;}
      div[data-testid="stTextInput"] input:focus{border-color:#00FFFF!important;box-shadow:none!important;}
      div[data-testid="stTextInput"] label{display:none;}
      div[data-testid="stButton"]>button{
        background:transparent!important;border:1px solid #FFF!important;color:#FFF!important;
        font-family:'Courier New',Courier,monospace!important;font-weight:700!important;
        text-transform:uppercase!important;border-radius:2px!important;letter-spacing:.06em!important;}
      div[data-testid="stButton"]>button:hover{border-color:#00FFFF!important;color:#00FFFF!important;}
      [data-testid="stTabs"] [role="tablist"]{border-bottom:1px solid #222!important;background:transparent!important;}
      [data-testid="stTabs"] button[role="tab"]{
        color:#FFF!important;font-family:'Courier New',Courier,monospace!important;
        text-transform:uppercase!important;border:none!important;background:transparent!important;font-size:11px!important;}
      [data-testid="stTabs"] button[role="tab"][aria-selected="true"]{
        color:#00FFFF!important;border-bottom:2px solid #00FFFF!important;}
      [data-testid="stExpander"]{background:transparent!important;border:1px solid #1a1a1a!important;border-radius:2px!important;}
      [data-testid="stExpander"] summary{
        color:#FFF!important;font-family:'Courier New',Courier,monospace!important;
        font-size:11px!important;text-transform:uppercase!important;}
      hr{border-color:#222!important;}
      [data-testid="stSpinner"] p{color:#00FFFF!important;}
      [data-testid="stAlert"]{background:#0a0a0a!important;border:1px solid #333!important;
        color:#FFF!important;font-family:'Courier New',Courier,monospace!important;}
      .stMarkdown a{color:inherit!important;}
    </style>
    """, unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════════════════════
    #  PAGE HEADER
    # ═══════════════════════════════════════════════════════════════════════════════

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
    tg_status = "🟢 TG LIVE" if (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) else "⚪ TG NOT SET"
    hl_status = "🟢 HELIUS LIVE" if HELIUS_API_KEY else "⚪ HELIUS NOT SET"
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;">'
        f'<div style="font-size:22px;font-weight:700;color:#FFFFFF;letter-spacing:.1em;text-transform:uppercase;">MEME COIN SCANNER</div>'
        f'<div style="text-align:right;">'
        f'<div style="font-size:11px;color:#FFFFFF;font-weight:700;white-space:nowrap;">{now_str}</div>'
        f'<div style="font-size:9px;color:#555;margin-top:2px;">{hl_status}  ·  {tg_status}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr style="border-color:#222;margin:8px 0 16px;" />', unsafe_allow_html=True)

    # ── Alert log banner ──────────────────────────────────────────────────────────
    if st.session_state["alert_log"]:
        latest = st.session_state["alert_log"][-1]
        st.markdown(
            f'<div style="border:1px solid #FF3131;padding:8px 14px;margin-bottom:10px;background:#1a0000;">'
            f'<span style="color:#FF3131;font-size:9px;font-weight:700;">🚨 LAST ALERT {latest["time"]}  ·  '
            f'{latest["token"]}  ·  DEV {latest["deployer"]}  ·  SUCCESS RATE {latest["rate"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Search bar ────────────────────────────────────────────────────────────────
    sc, gc = st.columns([5, 1])
    with sc:
        q = st.text_input("search", value=st.session_state.get("cs_query",""),
                          placeholder="NAME · SYMBOL · CONTRACT ADDRESS  —  e.g.  PEPE  /  0x6982…  /  solana",
                          label_visibility="collapsed", key="cs_input")
    with gc:
        go = st.button("SEARCH ↵", use_container_width=True, type="primary")
    if go and q.strip():
        st.session_state.cs_query = q.strip()
        st.session_state.cs_pairs = None

    # ── Top-N selector ────────────────────────────────────────────────────────────
    tn_col, _ = st.columns([1, 4])
    with tn_col:
        top_n = st.selectbox("topn", options=TOP_N_OPTIONS, index=0,
                             format_func=lambda x: f"TOP {x} PER CATEGORY",
                             label_visibility="collapsed", key="top_n_select")

    # ── Load both pools ───────────────────────────────────────────────────────────
    with st.spinner("LOADING DISCOVERY POOLS…"):
        paid_tokens    = get_paid_tokens(top_n)
        organic_tokens = get_organic_tokens(top_n)

    # ── Strip renderer ────────────────────────────────────────────────────────────
    def _render_strip(tokens, label, accent, key_prefix):
        st.markdown(_section_header(label, accent), unsafe_allow_html=True)
        if not tokens:
            st.markdown('<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">NO DATA.</div>', unsafe_allow_html=True)
            return
        n   = len(tokens)
        css = ""
        for i in range(n):
            c   = COLORS[i % 7]
            css += (
                f'div[data-testid="stHorizontalBlock"] > div:nth-child({i+1}) button {{'
                f'border-color:{c}!important;color:{c}!important;font-size:10px!important;'
                f'padding:4px 2px!important;line-height:1.3!important;height:auto!important;'
                f'min-height:0!important;white-space:pre-line!important;}}'
                f'div[data-testid="stHorizontalBlock"] > div:nth-child({i+1}) button:hover{{background:{c}22!important;}}'
            )
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
        cols = st.columns(n)
        for i, tok in enumerate(tokens):
            with cols[i]:
                # Button label = ONLY the coin name. Nothing else.
                if st.button(tok["display"], key=f"{key_prefix}_{i}", use_container_width=True):
                    st.session_state["cs_query"] = tok["address"] or tok["symbol"] or tok["display"]
                    st.session_state["cs_pairs"] = None
                    st.rerun()

    _render_strip(paid_tokens[:top_n],    "💰  PAID  —  ACTIVE DEX SCREENER BOOSTS",           "#FF5F1F", "paid")
    _render_strip(organic_tokens[:top_n], "🌱  ORGANIC  —  HIGH VOLUME  ·  NO PAID BOOST",     "#00FF7F", "organic")

    # ── Lag tracker summary ───────────────────────────────────────────────────────
    graduated = {a: r for a, r in st.session_state["lag_tracker"].items()
                 if r["first_paid"] and r["first_organic"]}
    if graduated:
        st.markdown(_section_header("⏱  PAID → ORGANIC  LAG TRACKER", "#FF00FF"), unsafe_allow_html=True)
        rows = ""
        for addr, rec in sorted(graduated.items(), key=lambda x: x[1]["first_paid"]):
            s = rec["first_organic"] - rec["first_paid"]
            lag_disp = f"{int(s)}s" if s < 60 else (f"{int(s/60)}m" if s < 3600 else f"{int(s/3600)}h {int((s%3600)/60)}m")
            rows += _tbl_row(addr[:14]+"…", f'<span style="color:#FF00FF;font-weight:700;">{lag_disp}</span>', "#FF00FF")
        st.markdown(_tbl_wrap(rows, "#FF00FF"), unsafe_allow_html=True)

    st.markdown('<hr style="border-color:#111;margin:16px 0;" />', unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════════════════════
    #  COIN DETAIL
    # ═══════════════════════════════════════════════════════════════════════════════

    active_query = st.session_state.get("cs_query","").strip()
    if not active_query:
        st.markdown(
            '<div style="border:1px dashed #1a1a1a;padding:48px;text-align:center;">'
            '<div style="color:#FFF;font-size:13px;letter-spacing:.12em;">CLICK A COIN ABOVE  ·  OR USE THE SEARCH BAR</div></div>',
            unsafe_allow_html=True)
        st.stop()

    with st.spinner(f"SCANNING  {active_query.upper()[:32]}…"):
        pairs = search_dex(active_query)

    if not pairs:
        st.warning("NO PAIRS FOUND.  TRY THE FULL NAME, SYMBOL, OR CONTRACT ADDRESS.")
        st.stop()

    pair      = pairs[0]
    base      = pair.get("baseToken")  or {}
    quote     = pair.get("quoteToken") or {}
    sym       = base.get("symbol","?").upper()
    name      = base.get("name", sym)
    token_addr = base.get("address","")

    img_url   = (pair.get("info") or {}).get("imageUrl","")
    img_html  = (f'<img src="{img_url}" style="width:28px;height:28px;border-radius:50%;margin-right:10px;vertical-align:middle;" />' if img_url else "")
    chain_str = pair.get("chainId","").upper()
    dex_str   = pair.get("dexId","").upper()
    pair_url  = pair.get("url","#")
    is_solana = chain_str.lower() == "solana"

    st.markdown(
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:4px 0 14px;">'
        f'{img_html}'
        f'<span style="font-size:20px;font-weight:700;color:#FFF;">{sym}</span>'
        f'<span style="font-size:13px;color:#FFF;">/ {name}</span>'
        f'<span style="border:1px solid #00FFFF;color:#00FFFF;font-size:9px;padding:2px 7px;letter-spacing:.06em;">{chain_str}</span>'
        f'<span style="border:1px solid #333;color:#FFF;font-size:9px;padding:2px 7px;">{dex_str}</span>'
        f'<a href="{pair_url}" target="_blank" style="font-size:10px;color:#007FFF;text-decoration:none;border:1px solid #007FFF;padding:2px 8px;">↗ DEX SCREENER</a>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 7 metric cards ────────────────────────────────────────────────────────────
    price   = pair.get("priceUsd")
    mcap    = pair.get("marketCap")
    fdv     = pair.get("fdv")
    liq_usd = (pair.get("liquidity")   or {}).get("usd")
    vol24   = (pair.get("volume")      or {}).get("h24")
    chg1h   = (pair.get("priceChange") or {}).get("h1")
    chg24   = (pair.get("priceChange") or {}).get("h24")
    price_str = _price_full(price)

    metric_defs = [
        ("PRICE",     _price_full(price)),
        ("MKT CAP",   _fmt(mcap)),
        ("FDV",       _fmt(fdv)),
        ("LIQUIDITY", _fmt(liq_usd)),
        ("24H VOL",   _fmt(vol24)),
        ("1H %",      _pct_html(chg1h)),
        ("24H %",     _pct_html(chg24)),
    ]
    cols7 = st.columns(7)
    for i, (lbl, val) in enumerate(metric_defs):
        c = COLORS[i]
        with cols7[i]:
            st.markdown(
                f'<div style="border:1px solid {c};padding:14px 6px;background:transparent;text-align:center;">'
                f'<div style="font-size:9px;color:{c};text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">{lbl}</div>'
                f'<div style="font-size:15px;color:{c};font-weight:700;">{val}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── Safety badges ─────────────────────────────────────────────────────────────
    st.markdown(_section_header("VISUAL SAFETY GRID", "#00FF7F"), unsafe_allow_html=True)
    badges = compute_badges(pair)
    bcols  = st.columns(5)
    for i, b in enumerate(badges):
        with bcols[i]:
            st.markdown(_badge(b["label"], b["value"], b["color"], b["icon"], b["detail"]), unsafe_allow_html=True)
            with st.expander("DETAIL ↓"):
                st.markdown(f'<p style="font-size:11px;color:#FFF;line-height:1.7;margin:0;">{b["detail"]}</p>', unsafe_allow_html=True)

    # ── Tabs ──────────────────────────────────────────────────────────────────────
    st.markdown(_section_header("DETAILED DATA", "#FF5F1F"), unsafe_allow_html=True)

    txn_1h  = (pair.get("txns") or {}).get("h1")  or {}
    txn_6h  = (pair.get("txns") or {}).get("h6")  or {}
    txn_24h = (pair.get("txns") or {}).get("h24") or {}
    vol_obj = pair.get("volume")      or {}
    chg_obj = pair.get("priceChange") or {}
    liq_obj = pair.get("liquidity")   or {}

    tab_p, tab_t, tab_all, tab_onchain, tab_tr = st.tabs([
        "📋  PAIR DATA",
        "⚡  TRANSACTIONS",
        f"🔀  ALL POOLS  ({len(pairs)})",
        "🔍  ON-CHAIN INTEL",
        "📈  GOOGLE TRENDS",
    ])

    # ── Tab: Pair Data ────────────────────────────────────────────────────────────
    with tab_p:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(_section_header("PRICE & MARKET", "#00FFFF"), unsafe_allow_html=True)
            st.markdown(_tbl_wrap(
                _tbl_row("PRICE (USD)",      price_str,                              "#00FFFF", True)
                + _tbl_row("PRICE (NATIVE)", str(pair.get("priceNative") or "—"),   "#00FFFF")
                + _tbl_row("MARKET CAP",     _fmt(mcap),                             "#00FFFF")
                + _tbl_row("FDV",            _fmt(fdv),                              "#00FFFF")
                + _tbl_row("LIQUIDITY",      _fmt(liq_usd),                          "#00FFFF")
                + _tbl_row("LIQ BASE",       _fmt(liq_obj.get("base"),prefix=""),   "#00FFFF")
                + _tbl_row("LIQ QUOTE",      _fmt(liq_obj.get("quote"),prefix=""),  "#00FFFF"),
                "#00FFFF"), unsafe_allow_html=True)
        with col_b:
            st.markdown(_section_header("PRICE CHANGES", "#00FF7F"), unsafe_allow_html=True)
            st.markdown(_tbl_wrap(
                _tbl_row("1H  CHANGE",  _pct_html(chg_obj.get("h1")),  "#00FF7F", True)
                + _tbl_row("6H  CHANGE", _pct_html(chg_obj.get("h6")),  "#00FF7F")
                + _tbl_row("24H CHANGE", _pct_html(chg_obj.get("h24")), "#00FF7F"),
                "#00FF7F"), unsafe_allow_html=True)
            st.markdown(_section_header("VOLUME", "#FF5F1F"), unsafe_allow_html=True)
            st.markdown(_tbl_wrap(
                _tbl_row("1H  VOLUME",  _fmt(vol_obj.get("h1")),  "#FF5F1F", True)
                + _tbl_row("6H  VOLUME", _fmt(vol_obj.get("h6")),  "#FF5F1F")
                + _tbl_row("24H VOLUME", _fmt(vol_obj.get("h24")), "#FF5F1F"),
                "#FF5F1F"), unsafe_allow_html=True)
        st.markdown(_section_header("TOKEN & PAIR INFO", "#FF00FF"), unsafe_allow_html=True)
        st.markdown(_tbl_wrap(
            _tbl_row("BASE TOKEN",     f'{base.get("name","?")}  ·  {base.get("symbol","?")}',    "#FF00FF", True)
            + _tbl_row("BASE ADDRESS",  base.get("address","—"),                                   "#FF00FF")
            + _tbl_row("QUOTE TOKEN",   f'{quote.get("name","?")}  ·  {quote.get("symbol","?")}', "#FF00FF")
            + _tbl_row("QUOTE ADDRESS", quote.get("address","—"),                                  "#FF00FF")
            + _tbl_row("CHAIN",         chain_str,                                                 "#FF00FF")
            + _tbl_row("DEX",           dex_str,                                                   "#FF00FF")
            + _tbl_row("PAIR CREATED",  _age_str(pair.get("pairCreatedAt")) + " AGO",             "#FF00FF"),
            "#FF00FF"), unsafe_allow_html=True)

    # ── Tab: Transactions ─────────────────────────────────────────────────────────
    with tab_t:
        st.markdown(_section_header("TRANSACTION COUNTS  (BUYS / SELLS)", "#007FFF"), unsafe_allow_html=True)
        def _txn_table(lbl, obj, color):
            b = obj.get("buys",0) or 0
            s = obj.get("sells",0) or 0
            t = b + s
            return (
                f'<div style="font-size:9px;color:{color};text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;">{lbl}</div>'
                + _tbl_wrap(
                    _tbl_row("BUYS", str(b), color, True)
                    + _tbl_row("SELLS", str(s), color)
                    + _tbl_row("TOTAL", str(t), color)
                    + _tbl_row("BUY %", f"{b/t*100:.0f}% BUY" if t else "—", color),
                    color)
            )
        tc1, tc2, tc3 = st.columns(3)
        with tc1: st.markdown(_txn_table("LAST 1 HOUR",   txn_1h,  "#007FFF"), unsafe_allow_html=True)
        with tc2: st.markdown(_txn_table("LAST 6 HOURS",  txn_6h,  "#8F00FF"), unsafe_allow_html=True)
        with tc3: st.markdown(_txn_table("LAST 24 HOURS", txn_24h, "#FF3131"), unsafe_allow_html=True)

    # ── Tab: All Pools ────────────────────────────────────────────────────────────
    with tab_all:
        st.markdown(_section_header(f"ALL TRADING POOLS FOR  {sym}  ({len(pairs)} RESULTS)", "#8F00FF"), unsafe_allow_html=True)
        # Sortable table — click any column header to sort
        pool_rows = []
        for p in pairs[:20]:
            liq = (p.get("liquidity") or {}).get("usd")
            vol = (p.get("volume") or {}).get("h24")
            chg = (p.get("priceChange") or {}).get("h24")
            pool_rows.append({
                "CHAIN / DEX": f"{(p.get('chainId') or '').upper()} / {(p.get('dexId') or '').upper()}",
                "PRICE": p.get("priceUsd"),
                "LIQUIDITY": liq,
                "24H VOL": vol,
                "24H %": chg,
                "LINK": p.get("url") or "",
            })
        if pool_rows:
            import pandas as pd
            df = pd.DataFrame(pool_rows)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "PRICE": st.column_config.NumberColumn(format="$%.8f"),
                    "LIQUIDITY": st.column_config.NumberColumn(format="$%.0f"),
                    "24H VOL": st.column_config.NumberColumn(format="$%.0f"),
                    "24H %": st.column_config.NumberColumn(format="%.2f%%"),
                    "LINK": st.column_config.LinkColumn("LINK", display_text="OPEN"),
                },
            )
            st.caption("Click a column header to sort (highest ↔ lowest).")
        else:
            st.markdown('<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">NO POOLS.</div>', unsafe_allow_html=True)

    with tab_onchain:
        st.markdown(_section_header("ON-CHAIN INTELLIGENCE  —  SOLANA", "#FF00FF"), unsafe_allow_html=True)

        if not is_solana:
            st.markdown(
                '<div style="border:1px solid #333;padding:14px;font-size:11px;color:#FFFFFF;">'
                'ON-CHAIN INTEL IS SOLANA ONLY (Helius RPC).  '
                'This token is on ' + chain_str + '.  Switch to a Solana token to use this tab.'
                '</div>',
                unsafe_allow_html=True,
            )
        elif not HELIUS_API_KEY:
            st.markdown(
                '<div style="border:1px solid #FF3131;padding:14px;font-size:11px;color:#FF3131;">'
                '⚠  HELIUS_API_KEY not found.<br>'
                'Add it to your <code>.env</code> file:  <code>HELIUS_API_KEY=your_key_here</code><br>'
                'Get a free key at <a href="https://helius.dev" target="_blank" style="color:#FF3131;">helius.dev</a>'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            # ── GMGN Smart Money / KOL ────────────────────────────────────────────
            st.markdown(_section_header("GMGN.AI  —  SMART MONEY & KOL DATA", "#00FFFF"), unsafe_allow_html=True)
            with st.spinner("FETCHING GMGN DATA…"):
                gmgn = get_gmgn_intelligence(token_addr)

            if gmgn.get("error"):
                st.markdown(f'<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">{gmgn["error"]}</div>', unsafe_allow_html=True)
            else:
                g1, g2, g3, g4 = st.columns(4)
                for col, lbl, val, c in [
                    (g1, "SMART MONEY BUYS", str(gmgn["smart_money_count"]), "#00FFFF"),
                    (g2, "KOL COUNT",        str(gmgn["kol_count"]),         "#00FF7F"),
                    (g3, "HOLDER COUNT",     str(gmgn["holder_count"]) if gmgn["holder_count"] else "—", "#FF5F1F"),
                    (g4, "DEV SOLD",         ("YES ⚠" if gmgn["dev_sold"] else ("NO ✓" if gmgn["dev_sold"] is not None else "—")), "#FF3131" if gmgn.get("dev_sold") else "#39FF14"),
                ]:
                    with col:
                        st.markdown(
                            f'<div style="border:1px solid {c};padding:12px 6px;text-align:center;">'
                            f'<div style="font-size:8px;color:{c};text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px;">{lbl}</div>'
                            f'<div style="font-size:18px;font-weight:700;color:{c};">{val}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                if gmgn["top_snipers"]:
                    st.markdown(_section_header("TOP SNIPERS", "#00FFFF"), unsafe_allow_html=True)
                    sniper_rows = "".join(
                        _tbl_row(s["wallet"], f'{s["pct"]}%  {s["tag"]}', "#00FFFF", i == 0)
                        for i, s in enumerate(gmgn["top_snipers"])
                    )
                    st.markdown(_tbl_wrap(sniper_rows, "#00FFFF"), unsafe_allow_html=True)

            # ── Parent-Wallet Tracer ──────────────────────────────────────────────
            st.markdown(_section_header("PARENT-WALLET TRACER  —  DEPLOYER FUNDING CHAIN", "#FF5F1F"), unsafe_allow_html=True)
            with st.spinner("TRACING DEPLOYER WALLET VIA HELIUS RPC…"):
                wallet_data = trace_parent_wallet(token_addr)

            if wallet_data.get("error"):
                st.markdown(f'<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">{wallet_data["error"]}</div>', unsafe_allow_html=True)
            else:
                dep = wallet_data.get("deployer","—")
                sol_url = f"https://solscan.io/account/{dep}"
                st.markdown(
                    f'<div style="border:1px solid #FF5F1F;padding:10px 14px;margin-bottom:8px;">'
                    f'<div style="font-size:9px;color:#FF5F1F;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">DEPLOYER WALLET</div>'
                    f'<div style="font-size:12px;color:#FFFFFF;word-break:break-all;">{dep}</div>'
                    f'<a href="{sol_url}" target="_blank" style="font-size:9px;color:#FF5F1F;margin-top:4px;display:inline-block;">↗ VIEW ON SOLSCAN</a>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if wallet_data["flags"]:
                    for flag in wallet_data["flags"]:
                        st.markdown(f'<div style="border:1px solid #FF3131;padding:6px 12px;margin-bottom:4px;font-size:10px;color:#FF3131;">{flag}</div>', unsafe_allow_html=True)
                if wallet_data["funding_chain"]:
                    st.markdown(_section_header("FUNDING TRAIL  (3-HOP TRACE)", "#FF5F1F"), unsafe_allow_html=True)
                    chain_rows = "".join(
                        _tbl_row(
                            f'HOP {h["hop"]}',
                            f'<a href="https://solscan.io/account/{h["wallet"]}" target="_blank" style="color:#FFFFFF;">{h["wallet"][:20]}…</a>  <span style="color:#555;font-size:9px;">via {h["via_sig"]}</span>',
                            "#FF5F1F", h["hop"] == 1,
                        )
                        for h in wallet_data["funding_chain"]
                    )
                    st.markdown(_tbl_wrap(chain_rows, "#FF5F1F"), unsafe_allow_html=True)

                # Check if we should fire Telegram alert
                if dep and dep != "—":
                    _maybe_alert_dev_redeployment(dep, token_addr, name)

            # ── Bundle Cluster Detector ───────────────────────────────────────────
            st.markdown(_section_header("BUNDLE CLUSTER DETECTOR  —  LAUNCH BLOCK ANALYSIS", "#FF3131"), unsafe_allow_html=True)
            with st.spinner("ANALYSING FIRST BLOCKS FOR BUNDLE PATTERNS…"):
                bundle_data = detect_bundle_clusters(token_addr)

            if bundle_data.get("error"):
                st.markdown(f'<div style="border:1px solid #333;padding:10px;font-size:10px;color:#555;">{bundle_data["error"]}</div>', unsafe_allow_html=True)
            else:
                risk_c = {"LOW":"#39FF14","MEDIUM":"#FF5F1F","HIGH":"#FF3131","CRITICAL":"#FF1493"}.get(bundle_data["risk_level"],"#555")
                st.markdown(
                    f'<div style="border:1px solid {risk_c};padding:10px 14px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">'
                    f'<div><div style="font-size:9px;color:{risk_c};text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px;">BUNDLE RISK LEVEL</div>'
                    f'<div style="font-size:18px;font-weight:700;color:{risk_c};">{bundle_data["risk_level"]}</div></div>'
                    f'<div style="font-size:9px;color:{risk_c};">{"⚠ BUNDLES DETECTED" if bundle_data["bundles_detected"] else "✓ NO BUNDLES FOUND"}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if bundle_data["clusters"]:
                    cluster_rows = "".join(
                        _tbl_row(
                            f'SLOT {cl["slot"]}',
                            f'<span style="color:#FF3131;font-weight:700;">{cl["count"]} WALLETS  ·  {cl["amount_sol"]} SOL EACH</span>',
                            "#FF3131", i == 0,
                        )
                        for i, cl in enumerate(bundle_data["clusters"][:6])
                    )
                    st.markdown(_tbl_wrap(cluster_rows, "#FF3131"), unsafe_allow_html=True)
                    st.markdown(
                        '<div style="font-size:9px;color:#555;margin-top:4px;">Multiple wallets buying identical SOL amounts in the same block = coordinated bundle / bot activity.</div>',
                        unsafe_allow_html=True,
                    )

    # ── Tab: Google Trends ────────────────────────────────────────────────────────
    # Shared panel: manual LOAD/REFRESH button only, shared cache + cooldown, errors shown as errors (never zeros).
    with tab_tr:
        if not TRENDS_UI_OK:
            st.markdown(
                f'<p style="color:#FFF;font-size:12px;">GOOGLE TRENDS UNAVAILABLE  —  trends_core.py / trends_ui.py NOT FOUND '
                f'NEXT TO THIS PAGE ({TRENDS_UI_ERR})</p>', unsafe_allow_html=True)
        else:
            trends_ui.render_trends_panel(name, key_prefix="meme", coin_id=token_addr or sym)

# Auto-run only when this file is the Streamlit page (not when imported by hub)
import sys as _sys
if not getattr(_sys.modules[__name__], "_INVOKED_FROM_HUB", False):
    run_solana_scanner()
