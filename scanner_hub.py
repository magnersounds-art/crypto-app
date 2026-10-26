"""
scanner_hub.py — Shared two-mode scanner interface.

Modes:
  • ALL CRYPTO   (default)  → invokes 3_Coin_Scanner.run_crypto_scanner()
  • SOLANA COINS            → invokes 4_Coin_Scanner.run_solana_scanner()

The two scanners remain fully independent files. This hub only selects
which one to run. Original /crypto-scanner and /meme-coin-scanner routes
continue to work as fallbacks.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import streamlit as st

# ── Page config (single call for the hub route) ───────────────────────────────
st.set_page_config(
    page_title="Scanner",
    page_icon="🔍",
    layout="wide",
)

# ── Shared terminal theme (matches both scanners) ─────────────────────────────
st.markdown(
    """
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
  div[data-testid="stRadio"] label,
  div[data-testid="stRadio"] div {
    font-family: 'Courier New', Courier, monospace !important;
    color: #EDEDED !important;
  }
</style>
""",
    unsafe_allow_html=True,
)

# ── Mode selector ─────────────────────────────────────────────────────────────
if "scanner_mode" not in st.session_state:
    st.session_state.scanner_mode = "ALL CRYPTO"

mode = st.radio(
    "MODE",
    options=["ALL CRYPTO", "SOLANA COINS"],
    index=0 if st.session_state.scanner_mode == "ALL CRYPTO" else 1,
    horizontal=True,
    key="scanner_mode_radio",
    label_visibility="collapsed",
)
st.session_state.scanner_mode = mode

st.markdown(
    f'<div style="font-size:11px;color:#555;letter-spacing:.08em;margin:4px 0 12px;">'
    f'MODE: <span style="color:#00FFFF;font-weight:700;">{mode}</span>'
    f'&nbsp;·&nbsp; independent scanners · state kept separate</div>',
    unsafe_allow_html=True,
)
st.markdown('<hr style="border-color:#222;margin:0 0 16px;" />', unsafe_allow_html=True)


def _load_and_run(filename: str, func_name: str) -> None:
    """
    Load a scanner module from the same directory, mark it as hub-invoked
    so its auto-run guard is skipped, then call its run_* function.
    """
    path = Path(__file__).resolve().parent / filename
    if not path.is_file():
        st.error(f"Scanner file not found: {filename}")
        return

    # Unique module name so the two scanners never collide in sys.modules
    mod_name = f"_hub_{filename.replace('.py', '').replace(' ', '_')}"

    if mod_name in sys.modules:
        mod = sys.modules[mod_name]
    else:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            st.error(f"Could not load {filename}")
            return
        mod = importlib.util.module_from_spec(spec)
        # Flag MUST be set before exec_module so the auto-run guard sees it
        mod._INVOKED_FROM_HUB = True
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

    fn = getattr(mod, func_name, None)
    if fn is None:
        st.error(f"{func_name} not found in {filename}")
        return
    fn()


# ── Dispatch ──────────────────────────────────────────────────────────────────
if mode == "ALL CRYPTO":
    _load_and_run("3_Coin_Scanner.py", "run_crypto_scanner")
else:
    _load_and_run("4_Coin_Scanner.py", "run_solana_scanner")
