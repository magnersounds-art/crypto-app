"""
logic/shill_db.py

SQLite persistence for the Shill Tracker.

Tables
------
headlines
    One row per article / video logged at ingest time.
price_snapshots
    Hourly (or on-demand) BTC USD price history.
horizon_prices
    Pending → resolved price + % change at 24h / 7d / 14d / 30d.
"""

from __future__ import annotations

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Iterable, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "shill_tracker.db"

HORIZONS_HOURS = {
    "24h": 24,
    "7d": 24 * 7,
    "14d": 24 * 14,
    "30d": 24 * 30,
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS headlines (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name     TEXT NOT NULL,
                source_type     TEXT NOT NULL CHECK(source_type IN ('article','video')),
                title           TEXT NOT NULL,
                url             TEXT NOT NULL,
                category        TEXT,
                published_at    TEXT NOT NULL,
                logged_at       TEXT NOT NULL,
                hype_score      REAL NOT NULL DEFAULT 0,
                btc_price_at_post REAL,
                UNIQUE(url)
            );

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL UNIQUE,
                price       REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS horizon_prices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                headline_id     INTEGER NOT NULL,
                horizon         TEXT NOT NULL CHECK(horizon IN ('24h','7d','14d','30d')),
                target_at       TEXT NOT NULL,
                btc_price       REAL,
                pct_change      REAL,
                status          TEXT NOT NULL DEFAULT 'pending'
                                    CHECK(status IN ('pending','resolved','missing')),
                resolved_at     TEXT,
                UNIQUE(headline_id, horizon),
                FOREIGN KEY(headline_id) REFERENCES headlines(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_headlines_published
                ON headlines(published_at);
            CREATE INDEX IF NOT EXISTS idx_headlines_source
                ON headlines(source_name);
            CREATE INDEX IF NOT EXISTS idx_horizon_status
                ON horizon_prices(status, target_at);
            CREATE INDEX IF NOT EXISTS idx_snapshots_ts
                ON price_snapshots(timestamp);
            """
        )
    logger.info("Shill Tracker DB initialised at %s", DB_PATH)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def log_price_snapshot(price: float, ts: Optional[datetime] = None) -> bool:
    if price is None or price <= 0:
        return False
    if ts is None:
        ts = datetime.now(timezone.utc)
    ts = ts.replace(minute=0, second=0, microsecond=0)
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO price_snapshots (timestamp, price) VALUES (?, ?)",
                (_iso(ts), float(price)),
            )
        return True
    except Exception as e:
        logger.warning("log_price_snapshot failed: %s", e)
        return False


def get_closest_price(target: datetime, max_skew_hours: int = 3) -> Optional[float]:
    target_iso = _iso(target)
    window_start = _iso(target - timedelta(hours=max_skew_hours))
    window_end = _iso(target + timedelta(hours=max_skew_hours))
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT price, timestamp FROM price_snapshots
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) ASC
            LIMIT 1
            """,
            (window_start, window_end, target_iso),
        ).fetchone()
    return float(row["price"]) if row else None


def get_price_series(hours: int = 24 * 30) -> List[Dict[str, Any]]:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, price FROM price_snapshots
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (cutoff,),
        ).fetchall()
    return [{"timestamp": r["timestamp"], "price": r["price"]} for r in rows]


def log_headline(
    *,
    source_name: str,
    source_type: str,
    title: str,
    url: str,
    category: str,
    published_at: datetime,
    hype_score: float,
    btc_price_at_post: Optional[float] = None,
) -> Optional[int]:
    if not url or not title:
        return None
    pub_iso = _iso(published_at)
    now_iso = _iso(datetime.now(timezone.utc))
    try:
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO headlines
                    (source_name, source_type, title, url, category,
                     published_at, logged_at, hype_score, btc_price_at_post)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    source_type,
                    title,
                    url,
                    category or "General",
                    pub_iso,
                    now_iso,
                    float(hype_score),
                    btc_price_at_post,
                ),
            )
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT id FROM headlines WHERE url = ?", (url,)
                ).fetchone()
                return int(row["id"]) if row else None

            headline_id = cur.lastrowid

            for horizon, hours in HORIZONS_HOURS.items():
                target = published_at + timedelta(hours=hours)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO horizon_prices
                        (headline_id, horizon, target_at, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (headline_id, horizon, _iso(target)),
                )
            return int(headline_id)
    except Exception as e:
        logger.warning("log_headline failed for %s: %s", url, e)
        return None


def log_headlines_batch(
    items: Iterable[Dict[str, Any]],
    btc_price: Optional[float] = None,
) -> int:
    inserted = 0
    for it in items:
        hid = log_headline(
            source_name=it["source_name"],
            source_type=it["source_type"],
            title=it["title"],
            url=it["url"],
            category=it.get("category", "General"),
            published_at=it["published_at"],
            hype_score=it.get("hype_score", 0.0),
            btc_price_at_post=it.get("btc_price_at_post", btc_price),
        )
        if hid is not None:
            inserted += 1
    return inserted


def resolve_pending_horizons(max_rows: int = 500) -> int:
    now = datetime.now(timezone.utc)
    now_iso = _iso(now)
    resolved = 0

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT hp.id AS hp_id, hp.headline_id, hp.horizon, hp.target_at,
                   h.btc_price_at_post, h.published_at
            FROM horizon_prices hp
            JOIN headlines h ON h.id = hp.headline_id
            WHERE hp.status = 'pending' AND hp.target_at <= ?
            ORDER BY hp.target_at ASC
            LIMIT ?
            """,
            (now_iso, max_rows),
        ).fetchall()

        for row in rows:
            target = _parse_iso(row["target_at"])
            price = get_closest_price(target)
            entry_price = row["btc_price_at_post"]

            if price is None or entry_price is None or entry_price <= 0:
                conn.execute(
                    """
                    UPDATE horizon_prices
                    SET status = 'missing', resolved_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, row["hp_id"]),
                )
            else:
                pct = ((price - entry_price) / entry_price) * 100.0
                conn.execute(
                    """
                    UPDATE horizon_prices
                    SET btc_price = ?, pct_change = ?, status = 'resolved',
                        resolved_at = ?
                    WHERE id = ?
                    """,
                    (price, round(pct, 4), now_iso, row["hp_id"]),
                )
            resolved += 1

    return resolved


def count_headlines() -> int:
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM headlines").fetchone()["c"])


def avg_hype() -> Optional[float]:
    with _connect() as conn:
        row = conn.execute("SELECT AVG(hype_score) AS a FROM headlines").fetchone()
    return round(float(row["a"]), 2) if row and row["a"] is not None else None


def loudest_source() -> Optional[Tuple[str, float]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT source_name, AVG(hype_score) AS avg_h, COUNT(*) AS n
            FROM headlines
            GROUP BY source_name
            HAVING n >= 3
            ORDER BY avg_h DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    return row["source_name"], round(float(row["avg_h"]), 2)


def source_leaderboard() -> List[Dict[str, Any]]:
    with _connect() as conn:
        sources = conn.execute(
            """
            SELECT source_name,
                   COUNT(*) AS n,
                   AVG(hype_score) AS avg_hype,
                   AVG(btc_price_at_post) AS avg_entry
            FROM headlines
            GROUP BY source_name
            ORDER BY avg_hype DESC
            """
        ).fetchall()

        horiz = conn.execute(
            """
            SELECT h.source_name, hp.horizon, AVG(hp.pct_change) AS avg_pct, COUNT(*) AS n
            FROM horizon_prices hp
            JOIN headlines h ON h.id = hp.headline_id
            WHERE hp.status = 'resolved'
            GROUP BY h.source_name, hp.horizon
            """
        ).fetchall()

    horiz_map: Dict[str, Dict[str, float]] = {}
    for r in horiz:
        horiz_map.setdefault(r["source_name"], {})[r["horizon"]] = round(float(r["avg_pct"]), 2)

    out = []
    for s in sources:
        name = s["source_name"]
        entry = {
            "source": name,
            "n": int(s["n"]),
            "avg_hype": round(float(s["avg_hype"]), 2),
            "avg_entry": round(float(s["avg_entry"]), 2) if s["avg_entry"] else None,
        }
        for h in ("24h", "7d", "14d", "30d"):
            entry[f"acc_{h}"] = horiz_map.get(name, {}).get(h)
        out.append(entry)
    return out


def headlines_for_source(source_name: str, limit: int = 100) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT h.id, h.title, h.url, h.category, h.published_at,
                   h.hype_score, h.btc_price_at_post, h.source_type
            FROM headlines h
            WHERE h.source_name = ?
            ORDER BY h.published_at DESC
            LIMIT ?
            """,
            (source_name, limit),
        ).fetchall()

        out = []
        for r in rows:
            item = dict(r)
            hrows = conn.execute(
                """
                SELECT horizon, btc_price, pct_change, status
                FROM horizon_prices
                WHERE headline_id = ?
                """,
                (r["id"],),
            ).fetchall()
            for hr in hrows:
                item[f"{hr['horizon']}_price"] = hr["btc_price"]
                item[f"{hr['horizon']}_pct"] = hr["pct_change"]
                item[f"{hr['horizon']}_status"] = hr["status"]
            out.append(item)
    return out


def recent_headlines(hours: int = 24 * 7, limit: int = 500) -> List[Dict[str, Any]]:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, source_name, source_type, title, url, category,
                   published_at, hype_score, btc_price_at_post
            FROM headlines
            WHERE published_at >= ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def best_backtest_summary() -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT hp.horizon,
                   AVG(hp.pct_change) AS avg_pct,
                   COUNT(*) AS n
            FROM horizon_prices hp
            JOIN headlines h ON h.id = hp.headline_id
            WHERE hp.status = 'resolved' AND h.hype_score >= 5.0
            GROUP BY hp.horizon
            HAVING n >= 3
            ORDER BY avg_pct DESC
            """
        ).fetchall()
    if not rows:
        return None
    best = rows[0]
    return {
        "horizon": best["horizon"],
        "avg_pct": round(float(best["avg_pct"]), 2),
        "n": int(best["n"]),
    }
