"""
logic/scoring_engine.py

Calculates story importance scores, hype indicators, velocity cloning, 
readability, and sentiment for incoming RSS feeds.
"""

import re
import math
from hashlib import md5
from datetime import datetime, timezone, timedelta

class ScoringEngine:
    BANNER_THRESHOLD = 95

    # Topic taxonomy — ORDER IS THE PRIORITY RULE. detect_topic() returns
    # the FIRST category (in this order) whose keywords match. A headline
    # touching several categories at once (e.g. "BlackRock Bitcoin ETF
    # inflows amid Fed decision") gets filed under whichever of these
    # comes first — Regulation, then Institutional, then Bitcoin, etc.
    # Reordering this dict changes real scoring behaviour.
    TOPIC_KEYWORDS = {
        "Regulation": [
            "sec", "cftc", "lawsuit", "sued", "illegal", "sanctions", "doj",
            "court", "compliance", "regulation", "bill", "congress",
            "clarity act", "digital asset market clarity act", "genius act",
            "market structure", "crypto legislation", "crypto regulation",
            "stablecoin regulation",
        ],
        "Institutional": [
            "blackrock", "fidelity", "vaneck", "grayscale", "ark invest",
            "21shares", "bitwise", "franklin templeton", "invesco",
            "wisdomtree", "cme", "bakkt", "imf", "ecb", "occ", "fdic",
            "fca", "bank of england", "institutional", "custody",
        ],
        "Bitcoin": [
            "bitcoin", "btc", "satoshi", "halving", "mining", "hashrate",
            "lightning network", "mstr", "microstrategy", "strategy",
            "ordinals", "runes", "taproot", "utxo", "spot etf",
            "bitcoin etf", "hash rate", "mining difficulty",
            "difficulty adjustment", "miner capitulation", "miner selling",
            "bitcoin treasury",
        ],
        "Stablecoin": [
            "stablecoin", "usdt", "usdc", "tether", "circle",
            "stablecoin supply", "stablecoin market cap", "reserves",
            "redemption", "depeg", "stablecoin payments",
            "tokenized deposits",
        ],
        "DeFi": [
            "defi", "aave", "uniswap", "lending", "tvl", "yield", "dex",
            "liquidity pool", "yield farming", "perps",
        ],
        "RWA/Tokenisation": [
            "rwa", "tokenisation", "tokenization", "tokenized treasuries",
            "tokenized gold", "real world assets",
        ],
        "ETF": [
            "etf", "etf inflow", "etf inflows", "etf outflow",
            "etf outflows", "institutional buying", "sec approval",
        ],
        "Security": [
            "51% attack", "hack", "exploit", "vulnerability", "breach",
            "rug pull", "frozen", "insolvency", "bankruptcy", "bankrupt",
            "suspended", "withdrawal halt", "oracle attack",
            "bridge exploit", "wallet drain", "liquidity crisis",
            "contagion", "quantum", "seized", "investigation", "collapse",
        ],
        "Market Stress": [
            "liquidations", "leverage", "open interest", "funding rates",
            "forced selling",
        ],
        "Macro": [
            "fed", "powell", "inflation", "cpi", "rate cut", "treasury",
            "recession", "fomc", "gdp", "central bank", "dxy",
            "treasury yields",
        ],
        "Whale Activity": [
            "whale", "dormant", "transfer", "binance reserve",
            "exchange outflow", "wallet move",
        ],
        "Layer 1": [
            "solana", "ethereum", "eth", "sui", "aptos", "avalanche",
            "near", "cardano", "staking", "l2", "rollups", "vitalik",
        ],
        "Memes": ["pepe", "doge", "shib", "wif", "bonk", "memecoin", "pump.fun"],
        "AI": ["ai", "bittensor", "render", "fetch.ai", "nvidia", "artificial intelligence", "ai agent"],
    }

    # Proposed point values per topic, per column mode — PROPOSED
    # DEFAULTS, not yet confirmed. Extends the original 15/10/0 tiering:
    # BTC mode rewards Regulation/Institutional/Macro/ETF most (15),
    # Bitcoin itself stays at 10 (unchanged from before), Security gets
    # 12 (matches the existing systemic-threat word bonus), everything
    # ALT-native (DeFi/Layer1/Memes/AI) scores 0 for BTC. ALT mode
    # mirrors that in reverse. Adjust any number here directly — this
    # dict is the single source of truth for topic scoring.
    TOPIC_POINTS_BTC = {
        "Regulation": 15, "Institutional": 15, "Macro": 15, "ETF": 15,
        "Bitcoin": 10, "Security": 12, "Stablecoin": 8, "Market Stress": 8,
        "Whale Activity": 5, "RWA/Tokenisation": 5,
        "DeFi": 0, "Layer 1": 0, "Memes": 0, "AI": 0,
    }
    TOPIC_POINTS_ALT = {
        "DeFi": 15, "Layer 1": 15, "Memes": 15, "AI": 15,
        "RWA/Tokenisation": 12, "Stablecoin": 10, "Security": 8,
        "Market Stress": 8, "Whale Activity": 8,
        "ETF": 5, "Institutional": 5,
        "Regulation": 0, "Bitcoin": 0, "Macro": 0,
    }

    SYSTEMIC_THREAT_WORDS = [
        "quantum", "collapse", "exploit", "hack", "bankrupt", "lawsuit",
        "insolvent", "ban", "investigation", "seized", "contagion",
        "51% attack", "vulnerability", "breach", "rug pull", "depeg",
        "frozen", "insolvency", "suspended", "withdrawal halt",
        "oracle attack", "bridge exploit", "wallet drain",
        "liquidity crisis",
    ]

    INSTITUTIONAL_ENTITIES = [
        "citi", "citigroup", "blackrock", "fidelity", "fed", "sec", "doj",
        "goldman", "jpmorgan", "treasury", "powell",
        "vaneck", "grayscale", "ark invest", "21shares", "bitwise",
        "franklin templeton", "invesco", "wisdomtree", "cme", "bakkt",
        "imf", "ecb", "cftc", "occ", "fdic", "fca", "bank of england",
        "microstrategy", "strategy", "coinbase", "circle", "tether",
    ]

    HYPE_WORDS = [
        "shocking", "insane", "soars", "plunges", "skyrockets", "explodes", 
        "destroyed", "massive", "unbelievable", "guaranteed", "moon", "panic"
    ]

    BEARISH_WORDS = [
        "lawsuit", "sued", "collapse", "hack", "stolen", "crackdown", "risk",
        "drop", "plunge", "ban", "warning", "bankrupt", "insolvent", "threat"
    ]

    BULLISH_WORDS = [
        "approval", "surge", "record", "inflow", "soar", "gain", "adopt",
        "buy", "partnership", "launch", "bullish", "rally", "breakthrough"
    ]

    def __init__(self):
        pass

    @staticmethod
    def _contains_word(text_lower: str, phrase: str) -> bool:
        """Whole-word / whole-phrase match using regex word boundaries —
        replaces plain substring `in` checks that were false-firing on
        coincidental text (e.g. 'ban' matching inside 'Bank', 'sec'
        matching inside 'Second'). Multi-word phrases like 'spot etf'
        are matched as a literal phrase with boundaries at each end."""
        return re.search(r'\b' + re.escape(phrase) + r'\b', text_lower) is not None

    def detect_topic(self, title: str) -> str:
        """Classify story into a primary topic based on keyword matches.
        Returns the FIRST matching category in TOPIC_KEYWORDS' order —
        see the comment above that dict for why order matters here."""
        title_lower = title.lower()
        for topic, keywords in self.TOPIC_KEYWORDS.items():
            if any(self._contains_word(title_lower, kw) for kw in keywords):
                return topic
        return "General Crypto"

    def detect_sentiment(self, title: str) -> str:
        """Determine Bullish, Bearish, or Neutral sentiment."""
        title_lower = title.lower()
        bear_count = sum(1 for w in self.BEARISH_WORDS if self._contains_word(title_lower, w))
        bull_count = sum(1 for w in self.BULLISH_WORDS if self._contains_word(title_lower, w))

        if bear_count > bull_count:
            return "Bearish"
        elif bull_count > bear_count:
            return "Bullish"
        return "Neutral"

    def calculate_hype(self, title: str) -> tuple[int, str]:
        """Detect clickbait/hype words in headline."""
        title_lower = title.lower()
        count = sum(1 for word in self.HYPE_WORDS if self._contains_word(title_lower, word))
        
        if count >= 3:
            level = "High Risk"
        elif count == 2:
            level = "Medium Risk"
        elif count == 1:
            level = "Low Risk"
        else:
            level = "Clean"
            
        return count, level

    def calculate_readability(self, title: str) -> dict:
        """Estimate reading duration and style density."""
        words = title.split()
        word_count = len(words)
        # Standard average estimation
        est_minutes = max(1, math.ceil(word_count / 12)) 
        
        style = "Standard"
        if any(w.isupper() for w in words if len(w) > 2):
            style = "Sensational"
        elif word_count > 15:
            style = "Academic"

        return {"read_min": est_minutes, "style": style}

    def extract_entities(self, title: str) -> list[str]:
        """Extract prominent nouns/entities mentioned in headline."""
        title_lower = title.lower()
        found = []
        known_entities = ["bitcoin", "ethereum", "eth", "solana", "citi", "sec", "fed", "blackrock", "binance", "coinbase"]
        for ent in known_entities:
            if re.search(r'\b' + re.escape(ent) + r'\b', title_lower):
                found.append(ent)
        return list(set(found))

    def add_velocity_scores(self, articles: list[dict]) -> list[dict]:
        """Group similar titles to detect story clone counts (Velocity)."""
        seen_hashes = {}
        for a in articles:
            # Generate coarse hash based on normalized key words
            clean_title = "".join([c for c in a["title"].lower() if c.isalnum() or c.isspace()])
            words = sorted(set(clean_title.split()))[:5]
            hash_key = md5("".join(words).encode()).hexdigest()

            if hash_key in seen_hashes:
                seen_hashes[hash_key] += 1
            else:
                seen_hashes[hash_key] = 0

            a["_hash"] = hash_key

        for a in articles:
            clones = seen_hashes.get(a.get("_hash"), 0)
            a["clone_count"] = clones
            if clones >= 5:
                a["velocity_level"] = "Extreme"
            elif clones >= 3:
                a["velocity_level"] = "High"
            elif clones >= 1:
                a["velocity_level"] = "Medium"
            else:
                a["velocity_level"] = "Low"

        return articles

    def _topic_points(self, topic: str, mode: str) -> int:
        """Single source of truth for topic scoring — both
        calculate_scores() and explain_score() call this, so they can
        never drift out of sync with each other."""
        table = self.TOPIC_POINTS_BTC if mode == "btc" else self.TOPIC_POINTS_ALT
        return table.get(topic, 0)

    def explain_score(self, article: dict, mode: str = "btc") -> dict:
        """Diagnostic only — recomputes the exact same point-by-point
        breakdown calculate_scores() applies internally for ONE article,
        without changing calculate_scores()'s behavior or touching the
        article. Used to answer 'why did this get X score' for real
        data, rather than a made-up example."""
        title = article.get("title", "")
        title_lower = title.lower()
        base_weight = article.get("weight", 3)
        topic = article.get("topic") or self.detect_topic(title)
        hype_count, _ = self.calculate_hype(title)
        clone_count = article.get("clone_count", 0)

        feed_weight_pts = (base_weight - 3) * 5
        topic_pts = self._topic_points(topic, mode)

        has_threat = any(self._contains_word(title_lower, w) for w in self.SYSTEMIC_THREAT_WORDS)
        has_inst = any(self._contains_word(title_lower, w) for w in self.INSTITUTIONAL_ENTITIES)
        threat_pts = 12 if has_threat else 0
        inst_pts = 8 if has_inst else 0

        velocity_pts = min(clone_count * 3, 15)
        hype_penalty = hype_count * 2

        raw_total = 50 + feed_weight_pts + topic_pts + threat_pts + inst_pts + velocity_pts - hype_penalty
        final_score = max(5.0, min(99.0, raw_total))

        return {
            "title": title,
            "mode": mode,
            "base": 50,
            "feed_weight": base_weight,
            "feed_weight_pts": feed_weight_pts,
            "topic": topic,
            "topic_pts": topic_pts,
            "threat_words_hit": has_threat,
            "threat_pts": threat_pts,
            "institutional_words_hit": has_inst,
            "institutional_pts": inst_pts,
            "clone_count": clone_count,
            "velocity_pts": velocity_pts,
            "hype_count": hype_count,
            "hype_penalty": hype_penalty,
            "raw_total": raw_total,
            "final_score": round(final_score, 1),
        }

    def calculate_scores(self, articles: list[dict], mode: str = "btc") -> list[dict]:
        """
        Run master scoring calculation.
        
        Modes:
          - 'btc': Prioritizes macro, regulatory, institutional, and systemic risk.
          - 'alt': Prioritizes velocity, social sentiment, memes, and narrative hype.
        """
        for a in articles:
            title = a.get("title", "")
            title_lower = title.lower()

            base_weight = a.get("weight", 3)  # Source feed multiplier (1-5)
            topic = self.detect_topic(title)
            sentiment = self.detect_sentiment(title)
            hype_count, hype_level = self.calculate_hype(title)
            readability = self.calculate_readability(title)
            entities = self.extract_entities(title)
            clone_count = a.get("clone_count", 0)

            # Start Base Score
            score = 50.0

            # 1. Feed Source Multiplier
            score += (base_weight - 3) * 5  # Adds/subtracts up to 10 points

            # 2. Topic Base Weighting — via TOPIC_POINTS_BTC/ALT (see
            # class-level comment for why these values are proposed
            # defaults, tuneable in one place).
            score += self._topic_points(topic, mode)

            # 3. Systemic Threats & Institutional Warnings Trigger (Alert Boost)
            has_threat = any(self._contains_word(title_lower, w) for w in self.SYSTEMIC_THREAT_WORDS)
            has_inst = any(self._contains_word(title_lower, w) for w in self.INSTITUTIONAL_ENTITIES)

            if has_threat:
                score += 12
            if has_inst:
                score += 8

            # Flag explicit ALERT if both high systemic threat + institutional entity present
            is_alert = (has_threat and has_inst) or (score >= 80 and has_threat)
            
            # 4. Velocity Bonus
            score += min(clone_count * 3, 15)

            # 5. Hype Penalty / Adjustment
            if hype_count > 0:
                score -= (hype_count * 2)  # Mild penalty for clickbait fluff

            # Clamp Score between 0 and 100
            final_score = max(5.0, min(99.0, score))

            # Determine Tier Bracket
            if final_score >= 85:
                tier = "Tier 1"
            elif final_score >= 70:
                tier = "Tier 2"
            elif final_score >= 50:
                tier = "Tier 3"
            else:
                tier = "Tier 4"

            # Assign properties back to article object
            a["score"] = round(final_score, 1)
            a["score_tier"] = tier
            a["topic"] = topic
            a["sentiment"] = sentiment
            a["hype_count"] = hype_count
            a["hype_level"] = hype_level
            a["readability"] = readability
            a["entities"] = entities
            a["alert"] = is_alert
            a["banner"] = final_score >= self.BANNER_THRESHOLD

        # Sort descending by calculated score
        articles.sort(key=lambda x: x["score"], reverse=True)
        return articles
