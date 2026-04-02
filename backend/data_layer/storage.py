"""
Persistence layer using DuckDB for analytics and SQLite for state.

DuckDB handles:
  - Historical market snapshots
  - Trade history
  - Signal logs
  - Equity curve data

SQLite handles:
  - Active positions
  - Strategy state
  - Configuration overrides
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DUCKDB_PATH = DATA_DIR / "analytics.duckdb"
SQLITE_PATH = DATA_DIR / "state.sqlite3"


class DuckDBStorage:
    """Analytics storage using DuckDB."""

    def __init__(self, db_path: Path = DUCKDB_PATH) -> None:
        self.db_path = db_path
        self._conn = None

    def connect(self) -> None:
        try:
            import duckdb
            self._conn = duckdb.connect(str(self.db_path))
            self._init_tables()
            logger.info(f"DuckDB connected: {self.db_path}")
        except ImportError:
            logger.warning("DuckDB not installed — analytics storage disabled")

    def _init_tables(self) -> None:
        if self._conn is None:
            return
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                market_id VARCHAR,
                question VARCHAR,
                yes_price DOUBLE,
                no_price DOUBLE,
                liquidity DOUBLE,
                volume_24h DOUBLE,
                entropy_bits DOUBLE,
                kl_divergence DOUBLE,
                model_probability DOUBLE
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                strategy VARCHAR,
                market_id VARCHAR,
                side VARCHAR,
                price DOUBLE,
                size_usdc DOUBLE,
                confidence DOUBLE,
                kl_divergence DOUBLE,
                kelly_fraction DOUBLE,
                reason VARCHAR
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                market_id VARCHAR,
                question VARCHAR,
                side VARCHAR,
                price DOUBLE,
                size_usdc DOUBLE,
                strategy VARCHAR,
                paper BOOLEAN DEFAULT TRUE,
                pnl DOUBLE DEFAULT 0,
                trade_type VARCHAR DEFAULT 'open',
                exit_reason VARCHAR DEFAULT ''
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_curve (
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                balance DOUBLE,
                unrealized_pnl DOUBLE,
                realized_pnl DOUBLE,
                strategy VARCHAR DEFAULT 'total'
            )
        """)

    def insert_snapshot(self, **kwargs: Any) -> None:
        if self._conn is None:
            return
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        self._conn.execute(
            f"INSERT INTO market_snapshots ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )

    def insert_signal(self, **kwargs: Any) -> None:
        if self._conn is None:
            return
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        self._conn.execute(
            f"INSERT INTO signals ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )

    def insert_trade(self, **kwargs: Any) -> None:
        if self._conn is None:
            return
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        self._conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )

    def get_trade_log(self, limit: int = 200, wins_only: bool = False, losses_only: bool = False) -> list[dict]:
        """Get trade history with win/loss status."""
        if self._conn is None:
            return []
        where = ""
        if wins_only:
            where = "WHERE pnl > 0"
        elif losses_only:
            where = "WHERE pnl < 0"
        return self.query(
            f"SELECT ts, market_id, question, side, price, size_usdc, strategy, "
            f"paper, pnl, trade_type, exit_reason FROM trades {where} "
            f"ORDER BY ts DESC LIMIT ?",
            [limit],
        )

    def get_trade_stats(self) -> dict:
        """Get aggregate win/loss statistics."""
        if self._conn is None:
            return {}
        rows = self.query("""
            SELECT
                COUNT(*) as total_trades,
                COUNT(CASE WHEN pnl > 0 THEN 1 END) as wins,
                COUNT(CASE WHEN pnl < 0 THEN 1 END) as losses,
                COUNT(CASE WHEN pnl = 0 THEN 1 END) as breakeven,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 0) as gross_profit,
                COALESCE(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END), 0) as gross_loss,
                COALESCE(AVG(pnl), 0) as avg_pnl,
                COALESCE(MAX(pnl), 0) as best_trade,
                COALESCE(MIN(pnl), 0) as worst_trade
            FROM trades WHERE trade_type = 'close' OR pnl != 0
        """)
        return rows[0] if rows else {}

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        if self._conn is None:
            return []
        result = self._conn.execute(sql, params or [])
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def close(self) -> None:
        if self._conn:
            self._conn.close()


class SQLiteState:
    """Lightweight state storage using SQLite."""

    def __init__(self, db_path: Path = SQLITE_PATH) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_tables()
        logger.info(f"SQLite connected: {self.db_path}")

    def _init_tables(self) -> None:
        if self._conn is None:
            return
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                condition_id TEXT,
                question TEXT,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                size_usdc REAL NOT NULL,
                current_price REAL DEFAULT 0,
                strategy TEXT,
                opened_at TEXT DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                pnl REAL DEFAULT 0,
                active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS strategy_state (
                strategy TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS copy_targets (
                address TEXT PRIMARY KEY,
                display_name TEXT,
                auto_copy INTEGER DEFAULT 0,
                copy_ratio REAL DEFAULT 0.10,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS jet_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_name TEXT,
                tail_number TEXT,
                icao24 TEXT,
                from_location TEXT,
                to_poi TEXT,
                distance_nm REAL,
                signal_strength TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                market_tags TEXT
            );
        """)
        self._conn.commit()

    def get_active_positions(self) -> list[dict]:
        if self._conn is None:
            return []
        cursor = self._conn.execute("SELECT * FROM positions WHERE active = 1")
        return [dict(row) for row in cursor.fetchall()]

    def add_position(self, **kwargs: Any) -> int:
        if self._conn is None:
            return -1
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        cursor = self._conn.execute(
            f"INSERT INTO positions ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        self._conn.commit()
        return cursor.lastrowid or -1

    def close_position(self, position_id: int, pnl: float) -> None:
        if self._conn is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE positions SET active=0, closed_at=?, pnl=? WHERE id=?",
            (now, pnl, position_id),
        )
        self._conn.commit()

    def save_strategy_state(self, strategy: str, state: dict) -> None:
        if self._conn is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO strategy_state (strategy, state_json, updated_at) VALUES (?, ?, ?)",
            (strategy, json.dumps(state), now),
        )
        self._conn.commit()

    def load_strategy_state(self, strategy: str) -> Optional[dict]:
        if self._conn is None:
            return None
        cursor = self._conn.execute(
            "SELECT state_json FROM strategy_state WHERE strategy=?", (strategy,)
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row["state_json"])
        return None

    def close(self) -> None:
        if self._conn:
            self._conn.close()
