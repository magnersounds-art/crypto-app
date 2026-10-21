# Crypto App — Data Collection and Market Analysis Audit

- Project directory: `/home/daniel/Documents/Crypto App`
- Purpose: establish what data exists before designing market analysis.
- Method: local, read-only inspection; no network requests.
- Secrets: .env and credential files are excluded.

## 1. Local data and project files

- `data/sector_history.db` — 24,576 bytes
- `home_news_sources.csv` — 456 bytes
- `legacy_score_breakdown.csv` — 193,155 bytes
- `news_stories_log.csv` — 144,775 bytes
- `rss_active.csv` — 485 bytes
- `shill_tracker.db` — 1,003,520 bytes

## 2. Database tables, fields and record counts

### `data/sector_history.db`

**Table: `sector_daily`** — 18 rows
Fields: `d` (TEXT), `sector` (TEXT), `score` (REAL), `top_coins` (TEXT), `logged_at` (TEXT)

### `shill_tracker.db`

**Table: `headlines`** — 1,054 rows
Fields: `id` (INTEGER), `source_name` (TEXT), `source_type` (TEXT), `title` (TEXT), `url` (TEXT), `category` (TEXT), `published_at` (TEXT), `logged_at` (TEXT), `hype_score` (REAL), `btc_price_at_post` (REAL)
Potential time fields: `published_at`

**Table: `horizon_prices`** — 4,216 rows
Fields: `id` (INTEGER), `headline_id` (INTEGER), `horizon` (TEXT), `target_at` (TEXT), `btc_price` (REAL), `pct_change` (REAL), `status` (TEXT), `resolved_at` (TEXT)

**Table: `price_snapshots`** — 18 rows
Fields: `id` (INTEGER), `timestamp` (TEXT), `price` (REAL)
Potential time fields: `timestamp`

## 3. CSV files and their fields

### `home_news_sources.csv`
Data rows (excluding header): 7
Columns: `name`, `url`, `signal_type`, `enabled`, `score`

### `legacy_score_breakdown.csv`
Data rows (excluding header): 676
Columns: `logged_at_utc`, `column`, `source`, `link`, `title`, `mode`, `base`, `feed_weight`, `feed_weight_pts`, `topic`, `topic_pts`, `threat_words_hit`, `threat_pts`, `institutional_words_hit`, `institutional_pts`, `clone_count`, `velocity_pts`, `hype_count`, `hype_penalty`, `raw_total`, `final_score`

### `news_stories_log.csv`
Data rows (excluding header): 390
Columns: `logged_at_utc`, `headline`, `link`, `attention_score`, `source_count`, `sources`, `external_signal_count`, `external_signals`, `age_minutes`, `duplicate_count`, `entities`, `topic`, `calc_line`

### `rss_active.csv`
Data rows (excluding header): 8
Columns: `name`, `rss_url`, `category`, `weight`

## 4. Where the application collects and stores data

The following are source-code references, not proof that a source currently works or that its data is retained.

### `0_home.py`
- Line 8: `ALT  — sentiment/velocity scoring. Culture and casino floor.`
- Line 13: `Score pill        coloured by score value`
- Line 15: `Sentiment         Bullish/Bearish/Neutral`
- Line 20: `Polymarket badge  matching prediction market with probability`
- Line 22: `POLYMARKET:`
- Line 35: `import feedparser`
- Line 55: `RSS_PATH             = ROOT / "rss_active.csv"`
- Line 58: `LEGACY_SCORE_LOG_PATH   = ROOT / "legacy_score_breakdown.csv"`
- Line 170: `.bs-score{{color:#FFD60A;font-weight:800;font-size:16px;margin-bottom:12px;}}`
- Line 271: `def load_polymarkets():`
- Line 272: `# Polymarket temporarily disabled.`
- Line 279: `r = requests.get(`
- Line 280: `"https://api.coingecko.com/api/v3/coins/markets",`
- Line 290: `chgs = [coin_map[i]["price_change_percentage_24h"] or 0`
- Line 311: `"on-chain","onchain","depin","rwa","real world asset","polymarket","prediction market",`
- Line 341: `"""Fetch RSS, score with both BTC and ALT weights, add velocity + PM matches."""`
- Line 342: `if not RSS_PATH.exists():`
- Line 344: `f"⚠️ RSS feed list not found at: `{RSS_PATH}`. "`
- Line 345: `f"Create `rss_active.csv` with columns: name, rss_url, weight"`
- Line 349: `df = pd.read_csv(RSS_PATH, on_bad_lines="skip", engine="python")`
- Line 353: `if "rss_url" not in df.columns:`
- Line 354: `st.error("⚠️ `rss_active.csv` is missing a `rss_url` column.")`
- Line 361: `feed = feedparser.parse(row["rss_url"])`
- Line 364: `f"{row.get('name', row['rss_url'])}: "`
- Line 391: `feed_errors.append(f"{row.get('name', row.get('rss_url', '?'))}: {e}")`

### `1_News_Feed.py`
- Line 2: `NEWS:    All RSS articles clustered by category. Loads instantly — no API.`
- Line 3: `YOUTUBE: Verified curated channel network filtering.`
- Line 11: `import feedparser`
- Line 17: `import aiohttp`
- Line 25: `from logic.hype_scorer import score_hype`
- Line 36: `RSS_PATH        = BASE_DIR / "rss_active.csv"`
- Line 39: `YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/search"`
- Line 62: `"Memes":           {"colour": "#FF3131", "keywords": ["meme","memecoin","dogecoin","doge","shiba","pepe","bonk","wif","floki","brett","pump.fun"]},`
- Line 87: `"CTO Larsson":            "UCFU-BE5HRJoudqIz1VDKlhQ",`
- Line 202: `if not RSS_PATH.exists():`
- Line 204: `df = pd.read_csv(RSS_PATH, on_bad_lines="skip", engine="python")`
- Line 217: `feed = feedparser.parse(url)`
- Line 276: `resp = await fetch_async_yt_endpoint(session, YOUTUBE_API_URL, p)`
- Line 284: `pub_raw = snip.get("publishedAt", "")`
- Line 294: `"link":           f"https://www.youtube.com/watch?v={vid_id}",`
- Line 312: `async with aiohttp.ClientSession() as session:`
- Line 427: `st.error(f"Cannot find rss_active.csv at: {RSS_PATH}")`
- Line 430: `source_tuples = tuple((row["name"], row["rss_url"], row["weight"], row["category"]) for _, row in sources_df.iterrows())`
- Line 439: `st.session_state["yt_batch_cache"] = _cached_youtube_fetch(`
- Line 444: `logger.error("YouTube batch fetch failed: %s", e)`
- Line 478: `published_at=a["pub_dt"],`
- Line 479: `hype_score=score_hype(a["title"]),`
- Line 488: `source_name=v.get("channel") or "YouTube",`
- Line 493: `published_at=v["pub_dt"],`
- Line 494: `hype_score=score_hype(v["title"]),`

### `2_Sectors.py`
- Line 12: `COINGECKO_URL      = "https://api.coingecko.com/api/v3/coins/markets"`
- Line 79: `COINGECKO_URL,`
- Line 101: `chg = c.get("price_change_percentage_24h") or 0.0`
- Line 107: `"Mkt Cap": c.get("market_cap") or 0,`
- Line 121: `@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');`
- Line 189: `{datetime.now().strftime('%H:%M:%S')} &nbsp;·&nbsp; COINGECKO`

### `3_Coin_Scanner.py`
- Line 6: `2.  Delete subtitle bar (TOP 100 BY MARKET CAP · COINGECKO · etc)`
- Line 7: `3.  Delete MARKET SENTIMENT / FEAR & GREED bar above strip`
- Line 14: `10. Momentum score components → boxed cards like quick-select`
- Line 15: `11. Delete large Fear/Greed and Momentum Score standalone boxes`
- Line 18: `14. GitHub dev activity note — CoinGecko source, may be zero for many coins`
- Line 56: `CG_BASE  = "https://api.coingecko.com/api/v3"`
- Line 69: `SCORE_WEIGHTS = {`
- Line 77: `SCORE_LABELS = {`
- Line 86: `SCORE_TOOLTIPS = {`
- Line 88: `"fear_greed":     "Alternative.me Fear & Greed Index (0-100). Market-wide sentiment context. Weight: 20%.",`
- Line 90: `"dev_activity":   "GitHub 4-week commit count from CoinGecko developer_data. Weight: 15%.",`
- Line 244: `r = requests.get(`
- Line 248: `"order": "market_cap_desc",`
- Line 252: `"price_change_percentage": "1h,24h,7d",`
- Line 264: `r = requests.get(`
- Line 281: `r = requests.get(FNG_URL, params={"limit": 1}, headers=HEADERS, timeout=8)`
- Line 293: `"""Fetch Google Trends data. gprop: '' (web), 'youtube', 'news', 'images'"""`
- Line 381: `# ── Score engine ──────────────────────────────────────────────────────────────`
- Line 382: `def compute_score(detail, fng_value, trends_7d):`
- Line 391: `mcap    = (md.get("market_cap")    or {}).get("usd")`
- Line 398: `ath_score = norm(abs(ath_chg or 0), 0, 95)`
- Line 399: `comm_score= norm(math.log10(max(tw, 1)), 3, 8) * 100 if tw else 0`
- Line 406: `"community":      min(comm_score, 100),`
- Line 407: `"ath_distance":   ath_score,`
- Line 409: `score = sum(components[k] * SCORE_WEIGHTS[k] for k in components)`

_Output limited to avoid an excessively large report._

## 5. Market analysis this data may support

These are potential analyses. They are not confirmed as possible until the actual fields, coverage and history have been checked.

1. **Attention:** articles/videos per coin or topic, by time period.
2. **Attention acceleration:** whether coverage is rising faster than its own recent baseline.
3. **Source breadth:** how many independent publishers cover a story.
4. **Market reaction:** compare attention changes with price and trading-volume changes using matching timestamps.
5. **Meme-coin discovery:** detect new coin names and tickers, then verify identity to avoid confusing similarly named tokens.
6. **Signal quality:** check whether attention increases preceded market moves or followed them.

## 6. Important limitations to establish

- RSS items visible in a feed are not necessarily saved historically.
- Re-fetching the same story can inflate counts unless deduplicated.
- Publisher weights and keyword filters can bias attention scores.
- Price snapshots without timestamps or consistent intervals cannot reliably establish lead/lag relationships.
- Attention is not proof of buying pressure, liquidity or future returns.
- Historical testing must avoid using information that was unavailable at the time of the simulated decision.

## 7. Recommended next step

Use the database schemas, row counts and source-code references above to establish which data is genuinely retained. Then select one analysis that the existing data can support before adding new feeds or changing the application.
