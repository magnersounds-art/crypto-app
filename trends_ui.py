"""
trends_ui.py — the Google Trends panel shared by the Crypto Scanner and the Meme Coin Scanner.

    import trends_ui
    trends_ui.render_trends_panel(coin_name, key_prefix="crypto", coin_id="bitcoin")

Rendering this panel NEVER contacts Google.  A live request happens only inside the LOAD / REFRESH
button branch, and only if trends_core.TrendsGuard allows it (spacing, cooldown, recent-cache rules).
"""

import html
from datetime import datetime, timezone

import streamlit as st

import trends_core as tc


def _esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _hdr(text, color="#FF3131"):
    return (f'<div style="font-size:10px;color:{color};text-transform:uppercase;letter-spacing:.12em;'
            f'border-left:3px solid {color};padding-left:8px;margin:18px 0 10px;">{text}</div>')


def _box(title, text, color):
    return (f'<div style="border:1px solid {color};padding:10px 14px;margin:6px 0;font-size:12px;color:#FFFFFF;'
            f'line-height:1.6;"><b style="color:{color};">{title}</b> — {text}</div>')


def _utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d %b %H:%M UTC")


def _ago(sec):
    sec = int(sec or 0)
    if sec < 90:    return "moments ago"
    if sec < 5400:  return f"{max(sec // 60, 1)} min ago"
    if sec < 172800: return f"{sec // 3600} h ago"
    return f"{sec // 86400} d ago"


def _section_state_box(sec):
    c = {"empty": "#FF5F1F", "insufficient": "#FF5F1F", "malformed": "#FF3131",
         "failed": "#FF3131", "rate_limited": "#FF3131", "skipped": "#FFFFFF"}.get(sec["state"], "#FFFFFF")
    t = {"empty": "VALID EMPTY RESULT", "insufficient": "INSUFFICIENT SEARCH DATA",
         "malformed": "RESPONSE COULD NOT BE READ", "failed": "REQUEST FAILED",
         "rate_limited": "RATE LIMITED BY GOOGLE", "skipped": "NOT REQUESTED"}.get(sec["state"], sec["state"].upper())
    return _box(t, _esc(sec.get("message", "")), c)


def _avg_card(label, value, n, note=""):
    if value is None:
        col, big, sub = "#FF5F1F", "—", _esc(note or "NOT ENOUGH DATA")
    else:
        col = "#39FF14" if value >= 60 else ("#FF5F1F" if value >= 30 else "#FF3131")
        big, sub = f"{value:g}", f"OUT OF 100  ·  {n} OBSERVATIONS"
    return (f'<div style="border:1px solid {col};padding:16px;text-align:center;">'
            f'<div style="font-size:11px;color:{col};text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px;">{label}</div>'
            f'<div style="font-size:42px;font-weight:700;color:{col};">{big}</div>'
            f'<div style="font-size:11px;color:#FFFFFF;margin-top:4px;">{sub}</div></div>')


def _render_result(res):
    """Draw one stored/fresh result. Missing or failed sections are explained, never shown as zeros."""
    secs = res["sections"]
    iv = secs["interest"]
    if iv["state"] == "ok":
        g1, g2 = st.columns(2)
        with g1:
            st.markdown(_avg_card("7-DAY AVG INTEREST", iv["avg7"], iv["n7"], "FEWER THAN 3 DAILY OBSERVATIONS"), unsafe_allow_html=True)
        with g2:
            st.markdown(_avg_card(f'{iv["n_period"]}-DAY AVG INTEREST', iv["avg_period"], iv["n_period"]), unsafe_allow_html=True)
        st.markdown('<div style="font-size:10px;color:#FFFFFF;margin-top:4px;">Averages use complete daily observations only '
                    '(today, still in progress, is excluded). 7-day = observations within 7 days of the latest complete day.</div>',
                    unsafe_allow_html=True)
    else:
        st.markdown(_section_state_box(iv), unsafe_allow_html=True)

    st.markdown(_hdr("INTEREST BY REGION  —  TOP COUNTRIES"), unsafe_allow_html=True)
    rg = secs["regions"]
    if rg["state"] == "ok":
        mx = max(i["value"] for i in rg["items"]) or 1
        rows = ""
        for it in rg["items"]:
            bw = int(it["value"] / mx * 100)
            bc = "#39FF14" if it["value"] >= 70 else ("#FF5F1F" if it["value"] >= 40 else "#FF3131")
            rows += (f'<div style="display:flex;align-items:center;margin-bottom:5px;">'
                     f'<div style="width:150px;font-size:12px;color:#FFFFFF;text-transform:uppercase;flex-shrink:0;">{_esc(it["name"])}</div>'
                     f'<div style="flex:1;background:#111;height:14px;border:1px solid #222;margin:0 8px;">'
                     f'<div style="width:{bw}%;height:100%;background:{bc};"></div></div>'
                     f'<div style="width:32px;font-size:12px;color:{bc};font-weight:700;text-align:right;">{it["value"]}</div></div>')
        st.markdown(f'<div style="border:1px solid #FF3131;padding:12px;">{rows}</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="font-size:10px;color:#FFFFFF;">{rg["total"]} REGIONS WITH DATA · SHOWING TOP {len(rg["items"])}</div>', unsafe_allow_html=True)
    else:
        st.markdown(_section_state_box(rg), unsafe_allow_html=True)

    st.markdown(_hdr("TOP RELATED QUERIES"), unsafe_allow_html=True)
    tq = secs["queries"]
    if tq["state"] == "ok":
        rows = ""
        for it in tq["items"]:
            rows += (f'<div style="margin-bottom:8px;"><div style="display:flex;justify-content:space-between;margin-bottom:3px;">'
                     f'<span style="font-size:12px;color:#FFFFFF;text-transform:uppercase;">{_esc(it["query"])}</span>'
                     f'<span style="font-size:12px;color:#FF3131;font-weight:700;">{it["value"]}</span></div>'
                     f'<div style="background:#111;height:6px;border:1px solid #222;">'
                     f'<div style="width:{min(it["value"], 100)}%;height:100%;background:#FF3131;"></div></div></div>')
        st.markdown(f'<div style="border:1px solid #FF3131;padding:12px;">{rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown(_section_state_box(tq), unsafe_allow_html=True)

    st.markdown(_hdr("RISING RELATED QUERIES  —  BREAKOUT RADAR"), unsafe_allow_html=True)
    rq = secs["rising"]
    if rq["state"] == "ok":
        pills = ""
        for it in rq["items"]:
            if it["breakout"]:
                bg, txt, bd = "#FF3131", "🔥 BREAKOUT", "#FF3131"
            elif (it["value"] or 0) >= 1000:
                bg, txt, bd = "#FF5F1F", f'+{it["value"]}%', "#FF5F1F"
            else:
                bg, txt, bd = "#222", f'+{it["value"]}%', "#555"
            pills += (f'<span style="display:inline-flex;align-items:center;border:1px solid {bd};padding:5px 10px;margin:4px;">'
                      f'<span style="font-size:12px;color:#FFFFFF;text-transform:uppercase;margin-right:6px;">{_esc(it["query"])}</span>'
                      f'<span style="font-size:11px;background:{bg};color:#FFFFFF;font-weight:700;padding:2px 6px;">{txt}</span></span>')
        st.markdown(f'<div style="border:1px solid #FF3131;padding:10px;">{pills}</div>', unsafe_allow_html=True)
    else:
        st.markdown(_section_state_box(rq), unsafe_allow_html=True)

    st.markdown(_hdr("RELATED TOPICS  —  MACRO THEME RADAR"), unsafe_allow_html=True)
    tp = secs["topics"]
    if tp["state"] == "ok":
        mxv = max(t_["value"] for t_ in tp["items"]) or 1
        cols = ["#FF3131", "#FF5F1F", "#00FFFF", "#39FF14", "#FF00FF", "#007FFF", "#8F00FF"]
        bub = ""
        for i, t_ in enumerate(tp["items"]):
            c, nv = cols[i % len(cols)], t_["value"] / mxv
            bub += (f'<span style="display:inline-block;border:1px solid {c};color:{c};font-size:{int(11 + nv * 7)}px;'
                    f'font-weight:700;padding:4px {int(6 + nv * 10)}px;margin:4px;text-transform:uppercase;">{_esc(t_["title"])}</span>')
        st.markdown(f'<div style="border:1px solid #FF3131;padding:12px;">{bub}</div>', unsafe_allow_html=True)
    else:
        st.markdown(_section_state_box(tp), unsafe_allow_html=True)


def render_trends_panel(coin_name, key_prefix="trends", coin_id=None):
    guard = tc.get_guard()
    cfg = guard.cfg
    gmap = {"WEB": "", "YOUTUBE": "youtube", "NEWS": "news", "IMAGES": "images"}

    st.markdown(_hdr("GOOGLE TRENDS  —  RETAIL SEARCH CURIOSITY"), unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:11px;color:#FFFFFF;border:1px solid #222;padding:8px 12px;margin-bottom:12px;line-height:1.6;">'
        'Google Trends has no official public API; this uses an unofficial library that Google can refuse at any time. '
        '<b>Nothing is requested until you press the button.</b> One press = at most one pass of four data calls. '
        f'Live requests are spaced at least {int(cfg.min_interval_s)} s apart, a result younger than '
        f'{int(cfg.refresh_after_s // 60)} min is reused, and after an HTTP 429 all requests pause for at least '
        f'{int(cfg.rate_limit_cooldown_s // 60)} min. Your last good result is kept if a request fails. '
        'Ambiguous coin names (e.g. "Quant", "Near") return unrelated searches: edit the term below if so.</div>',
        unsafe_allow_html=True)

    t1, t2 = st.columns([3, 2])
    with t1:
        term = st.text_input("trends term", value=coin_name, key=f"{key_prefix}_term_{coin_id or coin_name}",
                             label_visibility="collapsed")
    with t2:
        plat = st.radio("platform", list(gmap.keys()), horizontal=True, index=0, label_visibility="collapsed",
                        key=f"{key_prefix}_platform")
    gprop = gmap[plat]
    skey = tc.TrendsGuard.key(term, gprop)

    # read-only views: no network
    entry = guard.lookup(term, gprop)
    stt = guard.status(term, gprop)
    last = st.session_state.setdefault(f"{key_prefix}_last_attempt", {})

    label = "REFRESH TRENDS" if entry else "LOAD TRENDS"
    clicked = st.button(f"{label}  ({plat})", key=f"{key_prefix}_load", disabled=not cfg.enabled)

    outcome = None
    if clicked:
        with st.spinner("ASKING GOOGLE TRENDS — AT MOST ONE PASS…"):
            outcome = guard.request(term, gprop)
        last[str(skey)] = {"outcome": outcome["outcome"], "notice": outcome["notice"], "error": outcome["error"],
                           "at": datetime.now(timezone.utc).strftime("%H:%M UTC")}
        entry = guard.lookup(term, gprop)
        stt = guard.status(term, gprop)

    # status line
    if not cfg.enabled:
        st.markdown(_box("LIVE REQUESTS SWITCHED OFF", "TRENDS_ENABLED=false is set (emergency stop). Stored data is still shown.", "#FF5F1F"), unsafe_allow_html=True)
    elif stt["can_request"]:
        st.markdown('<div style="font-size:11px;color:#39FF14;">A LIVE REQUEST IS ALLOWED NOW (Google may still refuse it).</div>', unsafe_allow_html=True)
    else:
        nxt = f' Next live request possible after {_utc(stt["next_allowed_epoch"])}.' if stt["next_allowed_epoch"] else ""
        st.markdown(f'<div style="font-size:11px;color:#FF5F1F;">NO LIVE REQUEST ALLOWED RIGHT NOW.{nxt}</div>', unsafe_allow_html=True)

    # what happened on the last press
    la = last.get(str(skey))
    if la:
        o, notice, err = la["outcome"], la["notice"] or {}, la["error"]
        nxt = f' Next attempt possible after {_utc(notice["next_allowed_epoch"])} (not guaranteed to be accepted).' if notice.get("next_allowed_epoch") else ""
        if o == "fresh":
            st.markdown(_box(f'LAST PRESS {la["at"]}', "Live request succeeded.", "#39FF14"), unsafe_allow_html=True)
        elif o == "partial":
            st.markdown(_box(f'LAST PRESS {la["at"]} — PARTIAL', _esc((err or {}).get("message", "A request failed part-way.")) + nxt, "#FF5F1F"), unsafe_allow_html=True)
        elif o == "failed":
            kind = (err or {}).get("kind", "")
            title = "GOOGLE RATE LIMIT (HTTP 429)" if kind == "rate_limit" else "REQUEST FAILED"
            extra = " This is NOT zero interest. Stored data (if any) is kept below." if kind == "rate_limit" else " Stored data (if any) is kept below."
            st.markdown(_box(f'{title} — {la["at"]}', _esc((err or {}).get("message", "")) + extra + nxt, "#FF3131"), unsafe_allow_html=True)
        elif o == "cached":
            st.markdown(_box(f'LAST PRESS {la["at"]} — NO REQUEST SENT', _esc(notice.get("message", "")) + nxt, "#00FFFF"), unsafe_allow_html=True)
        elif o in ("blocked", "disabled"):
            st.markdown(_box(f'LAST PRESS {la["at"]} — NO REQUEST SENT', _esc(notice.get("message", "")) + nxt, "#FF5F1F"), unsafe_allow_html=True)

    # the data itself + clear label
    if not entry:
        st.markdown(_box("UNAVAILABLE", f'No Google Trends data is stored for "{_esc(term)}" ({plat}). Press LOAD to request it.', "#FFFFFF"), unsafe_allow_html=True)
    else:
        res = entry["result"]
        fresh_now = bool(outcome and outcome["data_source"] == "fresh")
        when = _utc(entry["fetched_epoch"])
        if fresh_now:
            tag, col = "FRESH", "#39FF14"
            msg = f"live request just completed ({when})"
        elif entry["stale"]:
            tag, col = "OLD CACHE", "#FF5F1F"
            msg = f"last successful update {when} ({_ago(entry['age_s'])}) — older than {int(cfg.stale_after_s // 3600)} h"
        else:
            tag, col = "CACHED", "#00FFFF"
            msg = f"last successful update {when} ({_ago(entry['age_s'])})"
        if res.get("status") == "partial":
            msg += " · PARTIAL: some sections were not retrieved"
        st.markdown(f'<div style="border:1px solid {col};padding:6px 12px;margin:10px 0;font-size:12px;color:#FFFFFF;">'
                    f'<b style="color:{col};">{tag}</b> — {msg}</div>', unsafe_allow_html=True)
        _render_result(res)
        st.markdown(
            f'<div style="font-size:10px;color:#FFFFFF;margin-top:12px;line-height:1.6;">SOURCE: Google Trends via {_esc(res.get("backend") or "n/a")} '
            f'(unofficial) · TERM "{_esc(res["keyword"])}" · WORLDWIDE · {_esc(res["timeframe"])} · {_esc(plat)} · '
            f'{res.get("calls_attempted", 0)} DATA CALLS IN THAT PASS · '
            f'SAVED STATE: {"FILE (survives restarts, lost on redeploy)" if guard.persistent else "MEMORY ONLY (lost on restart)"}</div>',
            unsafe_allow_html=True)
