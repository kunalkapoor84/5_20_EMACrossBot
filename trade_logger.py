"""SQLite-based trade journal for recording all strategy trades."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime

import pytz

import config

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class TradeLogger:
    """Logs every trade to an SQLite database."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.TRADE_DB
        self._init_db()

    def _init_db(self) -> None:
        """Create the trades table if it doesn't exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    entry_time TEXT,
                    exit_time TEXT,
                    direction TEXT,
                    quantity INTEGER,
                    entry_price REAL,
                    exit_price REAL,
                    sl_price REAL,
                    target_price REAL,
                    exit_reason TEXT,
                    gross_pnl REAL,
                    charges REAL,
                    net_pnl REAL,
                    consecutive_sl_count INTEGER,
                    ema_fast_at_entry REAL,
                    ema_slow_at_entry REAL,
                    ema_fast_at_exit REAL,
                    ema_slow_at_exit REAL,
                    entry_order_id TEXT,
                    exit_order_id TEXT,
                    symbol TEXT,
                    option_type TEXT,
                    strike REAL,
                    security_id TEXT,
                    created_at TEXT
                )
            """)
            # Ensure new columns exist on older databases
            existing = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
            for col in ("symbol", "option_type", "strike", "security_id"):
                if col not in existing:
                    ctype = "REAL" if col == "strike" else "TEXT"
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {ctype}")
            conn.commit()
            logger.debug("Trade journal database initialized")
        finally:
            conn.close()

    def log_trade(
        self,
        direction: str,
        quantity: int,
        entry_price: float,
        exit_price: float,
        entry_time: str,
        exit_time: str,
        sl_price: float,
        target_price: float,
        exit_reason: str,
        gross_pnl: float,
        charges: float = 0.0,
        net_pnl: float = 0.0,
        consecutive_sl_count: int = 0,
        ema_fast_at_entry: float = 0.0,
        ema_slow_at_entry: float = 0.0,
        ema_fast_at_exit: float = 0.0,
        ema_slow_at_exit: float = 0.0,
        entry_order_id: str = "",
        exit_order_id: str = "",
        symbol: str = "",
        option_type: str = "",
        strike: float = 0.0,
        security_id: str = "",
    ) -> None:
        """Record a completed trade in the journal."""
        date = datetime.now(IST).strftime("%Y-%m-%d")
        created_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

        # Calculate net P&L if not provided
        if net_pnl == 0.0:
            net_pnl = gross_pnl - charges

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO trades (
                    date, entry_time, exit_time, direction, quantity,
                    entry_price, exit_price, sl_price, target_price,
                    exit_reason, gross_pnl, charges, net_pnl,
                    consecutive_sl_count,
                    ema_fast_at_entry, ema_slow_at_entry,
                    ema_fast_at_exit, ema_slow_at_exit,
                    entry_order_id, exit_order_id,
                    symbol, option_type, strike, security_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date, entry_time, exit_time, direction, quantity,
                entry_price, exit_price, sl_price, target_price,
                exit_reason, gross_pnl, charges, net_pnl,
                consecutive_sl_count,
                ema_fast_at_entry, ema_slow_at_entry,
                ema_fast_at_exit, ema_slow_at_exit,
                entry_order_id, exit_order_id,
                symbol, option_type, strike, security_id, created_at,
            ))
            conn.commit()
            logger.info(
                "Trade logged: %s %d @ %.2f -> %.2f reason=%s P&L=%.2f",
                direction, quantity, entry_price, exit_price, exit_reason, net_pnl
            )
        finally:
            conn.close()

    def get_today_trades(self) -> list[dict]:
        """Get all trades for today."""
        date = datetime.now(IST).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM trades WHERE date = ? ORDER BY entry_time",
                (date,)
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_trade_summary(self, days: int = 1) -> dict:
        """Get a summary of recent trading activity."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN exit_reason = 'TARGET' THEN 1 ELSE 0 END) as target_wins,
                    SUM(CASE WHEN exit_reason = 'SL' THEN 1 ELSE 0 END) as sl_losses,
                    SUM(CASE WHEN exit_reason = 'CROSSOVER' THEN 1 ELSE 0 END) as crossover_exits,
                    SUM(net_pnl) as total_pnl,
                    AVG(CASE WHEN net_pnl > 0 THEN net_pnl END) as avg_win,
                    AVG(CASE WHEN net_pnl < 0 THEN net_pnl END) as avg_loss,
                    MIN(net_pnl) as max_loss,
                    MAX(net_pnl) as max_win
                FROM trades
                WHERE date >= date('now', ? || ' days')
            """, (f"-{days}",))

            row = cursor.fetchone()
            if row is None:
                return {}

            result = dict(row)
            total = result.get("total_trades", 0) or 0
            wins = result.get("wins", 0) or 0
            result["win_rate"] = (wins / total * 100) if total > 0 else 0
            return result
        finally:
            conn.close()

    def get_all_trades(self) -> list[dict]:
        """Get all trades in the journal."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM trades ORDER BY date, entry_time")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def print_today_summary(self) -> None:
        """Print a human-readable summary of today's trades."""
        trades = self.get_today_trades()
        if not trades:
            print("No trades today.")
            return

        print(f"\n{'='*60}")
        print(f"  TRADE SUMMARY - {datetime.now(IST).strftime('%Y-%m-%d')}")
        print(f"{'='*60}")
        total_pnl = 0
        for i, t in enumerate(trades, 1):
            pnl = t.get("net_pnl", 0)
            total_pnl += pnl
            print(
                f"  #{i} {t['direction']:5s} qty={t['quantity']} "
                f"entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} "
                f"P&L={pnl:+.2f} reason={t['exit_reason']}"
            )
        print(f"{'-'*60}")
        print(f"  Total P&L: {total_pnl:+.2f} | Trades: {len(trades)}")
        print(f"{'='*60}\n")
