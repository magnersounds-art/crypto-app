"""
logic/hype_scorer.py

Pure-Python keyword-weighted hype score for Shill Tracker.
Scores a title 0.0 – 10.0 at ingest time. No external API / model.

Tunable: edit HYPE_LEXICON weights or add new terms.
"""

from __future__ import annotations

import re
from typing import Dict

# Weight scale is relative. Final score is clamped to [0, 10].
# Higher weight = stronger "shill / FOMO / urgency" signal.
HYPE_LEXICON: Dict[str, float] = {
    # Extreme urgency / FOMO
    "moon": 2.5,
    "mooning": 2.5,
    "to the moon": 3.0,
    "100x": 3.0,
    "1000x": 3.5,
    "gen": 1.5,          # "generational"
    "generational": 2.0,
    "life changing": 2.5,
    "life-changing": 2.5,
    "never too late": 2.0,
    "last chance": 2.5,
    "don't miss": 2.0,
    "dont miss": 2.0,
    "fomo": 2.5,
    "ape": 1.8,
    "aping": 1.8,
    "send it": 2.0,
    "full send": 2.2,

    # Explosive price language
    "explodes": 2.2,
    "explode": 2.0,
    "skyrockets": 2.2,
    "skyrocket": 2.0,
    "soars": 1.8,
    "soar": 1.6,
    "pumps": 1.8,
    "pumping": 1.8,
    "pumped": 1.5,
    "rips": 1.8,
    "ripping": 1.8,
    "breaks out": 1.7,
    "breakout": 1.7,
    "parabolic": 2.3,
    "vertical": 1.5,

    # Urgency / hype adjectives
    "insane": 1.8,
    "crazy": 1.5,
    "massive": 1.6,
    "huge": 1.3,
    "enormous": 1.5,
    "unbelievable": 1.8,
    "shocking": 1.7,
    "mind blowing": 2.0,
    "mind-blowing": 2.0,
    "historic": 1.6,
    "unprecedented": 1.7,
    "explosive": 2.0,
    "bullish": 1.2,
    "extremely bullish": 2.0,

    # Call-to-action / shill
    "buy now": 2.5,
    "buy the dip": 1.8,
    "accumulate": 1.4,
    "load up": 1.8,
    "stack": 1.3,
    "stacking": 1.3,
    "must buy": 2.2,
    "secret": 1.6,
    "hidden gem": 2.3,
    "gem": 1.5,
    "undervalued": 1.5,
    "sleeping giant": 2.0,
    "next 100x": 3.0,
    "next bitcoin": 2.5,
    "next eth": 2.2,
    "alpha": 1.4,
    "call": 1.0,         # "my call", "top call"
}

# Multi-word phrases checked first (longer match wins conceptually via order)
_PHRASES = sorted(
    [k for k in HYPE_LEXICON if " " in k],
    key=len,
    reverse=True,
)


def score_hype(title: str) -> float:
    """
    Return a hype score in [0.0, 10.0] for the given title.
    """
    if not title or not isinstance(title, str):
        return 0.0

    t = title.lower().strip()
    score = 0.0
    matched_spans = []

    # 1. Phrase matches (greedy longest-first)
    for phrase in _PHRASES:
        if phrase in t:
            score += HYPE_LEXICON[phrase]
            matched_spans.append(phrase)

    # 2. Single-word matches
    words = set(re.findall(r"[a-z0-9]+", t))
    for w in words:
        if w in HYPE_LEXICON and " " not in w:
            # skip if this word was part of an already-matched phrase
            if any(w in p.split() for p in matched_spans):
                continue
            score += HYPE_LEXICON[w]

    # 3. Extra signals
    # ALL-CAPS words of length >= 4 (shouting)
    caps = re.findall(r"\b[A-Z]{4,}\b", title)
    if caps:
        score += min(1.5, 0.4 * len(caps))

    # Multiple exclamation marks
    bangs = title.count("!")
    if bangs >= 2:
        score += 1.0
    elif bangs == 1:
        score += 0.4

    # Question + hype combo is common in thumbnails
    if "?" in title and score > 0:
        score += 0.3

    return round(min(10.0, max(0.0, score)), 2)


def hype_bucket(score: float) -> str:
    """Map numeric score to low / med / high for backtests."""
    if score < 2.0:
        return "low"
    if score < 5.0:
        return "med"
    return "high"
