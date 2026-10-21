"""
logic/home_news_scoring.py

The Home News Stories "attention filter" — kept fully separate from the
legacy BTC/ALT scoring in scoring_engine.py so the two never tangle.
Nothing here produces or uses Tier, Story Velocity badge, Hype Meter, or
Read Time — those stay on the BTC/ALT columns only.

Rubric (max 100):
    +30  same underlying story reported by 2+ independent RSS sources
    +25  story appears on an external Top/Featured/Most Read page
         (point value is read from home_news_sources.csv's 'score'
         column per source, not hardcoded — default is 25 for all rows)
    +20  bonus if it appears on 2+ distinct external sources
    +15  published within the last 2 hours
    +10  major crypto entity/event

Only stories published within the last 24 hours are considered — older
stories are dropped entirely, not just pushed down the ranking.
"""

import re
import csv
import html
import streamlit as st
from pathlib import Path
from datetime import datetime, timezone, timedelta

RECENCY_WINDOW_24H  = timedelta(hours=24)
VERY_RECENT_WINDOW  = timedelta(hours=2)

MULTI_SOURCE_POINTS = 30
MULTI_SOURCE_MIN    = 2

MULTI_RANK_BONUS    = 20
MULTI_RANK_MIN      = 2

RECENCY_POINTS       = 15
MAJOR_ENTITY_POINTS  = 10

# Topics already classified by scoring_engine.py's TOPIC_KEYWORDS that
# count as a "major event" for the +10 signal — reused, not re-detected.
MAJOR_TOPICS = {"ETF & Institutional", "Bitcoin", "Regulation", "Macro & Fed", "Whale Activity"}

STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "is", "as",
    "at", "with", "after", "its", "new", "says", "amid", "over", "how",
}


def _sig_words(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def _similar(a: str, b: str) -> float:
    """Overlap (containment) coefficient of significant words — a
    first-pass heuristic for matching an RSS headline against an
    external site's (differently worded) headline for the same story.
    MATCH_THRESHOLD is tuneable.

    Deliberately NOT Jaccard (intersection / union): union-based scoring
    punishes headlines of different lengths, so a terse headline vs. a
    padded/wordier one covering the exact same event would systematically
    under-score and fail to cluster even with 4+ shared significant words.
    Dividing by the smaller title's word count instead measures "how much
    of the shorter headline is contained in the longer one," which is
    the actual question we care about here."""
    wa, wb = _sig_words(a), _sig_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


MATCH_THRESHOLD = 0.35

# Clustering threshold for "is this the same underlying story as that
# cluster's first article". Deliberately NOT the same as scoring_engine's
# exact-hash `_hash`/clone_count — that hash only matches near-identical
# wording, so two outlets covering the same event almost never collide
# on it, which was under-counting independent-source coverage. Fuzzy
# word-overlap catches "Coinbase, BlackRock file..." vs "BlackRock,
# Coinbase submit..." as the same story; exact-hash did not.
#
# Lowered from 0.40 -> 0.30 alongside the switch to an overlap
# (containment) coefficient in _similar(): real same-story headlines
# from different outlets often share only 4-5 significant words once
# one outlet pads its title with extra framing ("Why X Backed Y, and
# Why Z Thinks..."). 0.30 keeps that pair clustered while still
# requiring meaningful shared vocabulary, not just one stray word.
CLUSTER_THRESHOLD = 0.30


def cluster_stories(scored_articles: list) -> list:
    """Group articles into narrative clusters by headline similarity
    (greedy: each article joins the first existing cluster whose lead
    article it's similar enough to, else starts a new cluster)."""
    clusters = []
    for a in scored_articles:
        title = a.get("title", "")
        placed = False
        for cluster in clusters:
            if _similar(title, cluster[0]["title"]) >= CLUSTER_THRESHOLD:
                cluster.append(a)
                placed = True
                break
        if not placed:
            clusters.append([a])

    for group in clusters:
        group.sort(
            key=lambda x: x.get("pub_dt") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    return clusters


def _external_matches(group: list, external_signals: dict) -> list:
    """Returns [(source_name, score_value), ...] for every external
    source whose headline list contains something similar to this
    story."""
    titles = [a["title"] for a in group]
    matches = []
    for source_name, info in external_signals.items():
        for h in info.get("headlines", []):
            if any(_similar(t, h) >= MATCH_THRESHOLD for t in titles):
                matches.append((source_name, info.get("score", 25)))
                break
    return matches


def score_story(group: list, external_signals: dict, now: datetime) -> dict:
    """Returns the final score plus a `breakdown` dict recording exactly
    which signals fired, their point value, and the evidence behind
    them — used to render the visible per-story calculation."""
    score = 0
    breakdown = {}

    sources = {a.get("source") for a in group if a.get("source")}
    if len(sources) >= MULTI_SOURCE_MIN:
        score += MULTI_SOURCE_POINTS
        breakdown["multi_source"] = {"points": MULTI_SOURCE_POINTS, "sources": sorted(sources)}

    ext_matches = _external_matches(group, external_signals)
    matched_names = sorted({name for name, _ in ext_matches})
    ext_points = max((pts for _, pts in ext_matches), default=0)
    if ext_matches:
        score += ext_points
        breakdown["external_rank"] = {"points": ext_points, "sources": matched_names}
    if len(matched_names) >= MULTI_RANK_MIN:
        score += MULTI_RANK_BONUS
        breakdown["multi_rank"] = {"points": MULTI_RANK_BONUS, "count": len(matched_names)}

    pub_dts = [a["pub_dt"] for a in group if a.get("pub_dt")]
    newest = max(pub_dts) if pub_dts else None
    if newest and (now - newest) <= VERY_RECENT_WINDOW:
        score += RECENCY_POINTS
        breakdown["very_recent"] = {"points": RECENCY_POINTS}

    entities = sorted({e for a in group for e in a.get("entities", [])})
    topics = {a.get("topic") for a in group if a.get("topic") in MAJOR_TOPICS}
    if entities or topics:
        score += MAJOR_ENTITY_POINTS
        reason = ", ".join(entities[:3]) if entities else ", ".join(sorted(topics))
        breakdown["major_entity"] = {"points": MAJOR_ENTITY_POINTS, "reason": reason}

    return {
        "score": min(round(score), 100),
        "breakdown": breakdown,
        "sources": sorted(sources),
        "external_signals": matched_names,
        "newest": newest,
    }


def _format_calc(breakdown: dict, final_score: int) -> str:
    """Human-readable 'show your work' line for one story."""
    parts = []
    if "multi_source" in breakdown:
        b = breakdown["multi_source"]
        parts.append(f'+{b["points"]} multi-source ({len(b["sources"])} RSS: {", ".join(b["sources"][:4])})')
    if "external_rank" in breakdown:
        b = breakdown["external_rank"]
        parts.append(f'+{b["points"]} external rank ({", ".join(b["sources"])})')
    if "multi_rank" in breakdown:
        b = breakdown["multi_rank"]
        parts.append(f'+{b["points"]} multi-rank ({b["count"]} external signals)')
    if "very_recent" in breakdown:
        parts.append(f'+{breakdown["very_recent"]["points"]} &lt;2h old')
    if "major_entity" in breakdown:
        b = breakdown["major_entity"]
        parts.append(f'+{b["points"]} major entity ({b["reason"]})')
    if not parts:
        return f"No attention signals fired &middot; Score = {final_score}/100"
    return " &middot; ".join(parts) + f" = {final_score}/100"


def build_news_stories(scored_articles: list, external_signals: dict, top_n: int = 10) -> list:
    """Full pipeline: cluster -> 24h cutoff -> score -> sort -> top_n.
    Returns only the top_n highest-scoring stories, highest first."""
    now = datetime.now(timezone.utc)
    stories = []

    for group in cluster_stories(scored_articles):
        newest_dt = max((a["pub_dt"] for a in group if a.get("pub_dt")), default=None)
        if newest_dt is None or (now - newest_dt) > RECENCY_WINDOW_24H:
            continue  # dropped entirely, not deprioritized

        result = score_story(group, external_signals, now)
        age_min = int((now - result["newest"]).total_seconds() // 60)
        stories.append({
            "headline": group[0]["title"],
            "link": group[0].get("link", "#"),
            "attention_score": result["score"],
            "breakdown": result["breakdown"],
            "calc_line": _format_calc(result["breakdown"], result["score"]),
            "sources": result["sources"],
            "source_count": len(result["sources"]),
            "external_signals": result["external_signals"],
            "age_minutes": age_min,
            "duplicate_count": len(group),
            "entities": sorted({e for a in group for e in a.get("entities", [])}),
            "topic": group[0].get("topic", ""),
        })

    stories.sort(key=lambda s: s["attention_score"], reverse=True)
    return stories[:top_n]


def render_news_stories(stories: list, C: dict, st_module, score_colour_fn):
    """Renders the News Stories cards using their own .ns-card / .ns-title
    / .ns-meta / .ns-calc CSS classes (defined in 0_home.py's <style>
    block) — deliberately separate from the legacy .sc/.sc-title classes
    so headline size here can be tuned independently of the BTC/ALT
    columns. No Tier, Velocity, Hype, or Read Time badges — those stay
    on the legacy engine only."""
    st = st_module
    if not stories:
        st.markdown(
            f'<div style="background:{C["card"]};border:1px dashed {C["border"]};'
            f'border-radius:6px;padding:20px;text-align:center;color:{C["muted"]};font-size:13px;">'
            f'No stories cleared the 24h attention filter yet — refresh shortly.</div>',
            unsafe_allow_html=True,
        )
        return

    for s in stories:
        sc = score_colour_fn(s["attention_score"])
        age_str = f'{s["age_minutes"]}m ago' if s["age_minutes"] < 60 else f'{s["age_minutes"] // 60}h ago'
        src_str = f'{s["source_count"]} RSS source' + ("s" if s["source_count"] != 1 else "")
        if s["sources"]:
            src_str += ": " + ", ".join(s["sources"][:4])
        ext_badges = "".join(
            f'<span class="badge" style="background:{C["purple"]}22;color:{C["purple"]};'
            f'border:1px solid {C["purple"]}44;">{sig}</span>'
            for sig in s["external_signals"]
        )
        entity_str = ", ".join(s["entities"][:3]) if s["entities"] else s["topic"]

        st.markdown(f"""
<div class="ns-card">
  <div class="ns-title"><a href="{s['link']}" target="_blank">{s['headline']}</a></div>
  <div class="badges">
    <span class="badge" style="background:{sc}22;color:{sc};border:1px solid {sc}44;
        font-size:13px;padding:3px 11px;">{s['attention_score']}</span>
    {ext_badges}
  </div>
  <div class="ns-meta">
    <span>{src_str}</span>
    <span>{age_str}</span>
    <span>{entity_str}</span>
    <span>{s['duplicate_count']} article(s) clustered</span>
  </div>
  <div class="ns-calc">{s['calc_line']}</div>
</div>
""", unsafe_allow_html=True)


LOG_COLUMNS = [
    "logged_at_utc", "headline", "link", "attention_score", "source_count",
    "sources", "external_signal_count", "external_signals", "age_minutes",
    "duplicate_count", "entities", "topic", "calc_line",
]

# Minimum gap between log snapshots per browser session — stops every
# Streamlit rerun (each click/widget interaction reruns the whole
# script) from writing duplicate rows. A genuinely new snapshot is
# still written on first load and every LOG_MIN_INTERVAL after that.
LOG_MIN_INTERVAL = timedelta(minutes=5)


def _plain_calc_line(calc_line: str) -> str:
    """calc_line is built for HTML display (&lt;, &middot;) — unescape
    it for the CSV so it reads normally in Excel/LibreOffice."""
    return html.unescape(calc_line)


def log_news_stories_snapshot(stories: list, log_path: Path) -> None:
    """Append one row per story to log_path (created with a header row
    on first write). Throttled via st.session_state so a single page
    session logs at most once every LOG_MIN_INTERVAL, regardless of how
    many times Streamlit reruns the script in that window."""
    now = datetime.now(timezone.utc)
    last = st.session_state.get("_news_log_last_ts")
    if last and (now - last) < LOG_MIN_INTERVAL:
        return
    st.session_state["_news_log_last_ts"] = now

    if not stories:
        return

    is_new_file = not log_path.exists()
    logged_at = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if is_new_file:
            writer.writeheader()
        for s in stories:
            writer.writerow({
                "logged_at_utc": logged_at,
                "headline": s["headline"],
                "link": s["link"],
                "attention_score": s["attention_score"],
                "source_count": s["source_count"],
                "sources": "; ".join(s["sources"]),
                "external_signal_count": len(s["external_signals"]),
                "external_signals": "; ".join(s["external_signals"]),
                "age_minutes": s["age_minutes"],
                "duplicate_count": s["duplicate_count"],
                "entities": "; ".join(s["entities"]),
                "topic": s["topic"],
                "calc_line": _plain_calc_line(s["calc_line"]),
            })
