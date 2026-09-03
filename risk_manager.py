"""Risk management: SL tracking, target tracking, consecutive SL counter."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import pytz

import config

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


@dataclass
class Position:
    """Tracks the current open position.

    For options the strategy holds a long ATM option: "LONG" = ATM call bought,
    "SHORT" = ATM put bought. SL and Target are always on the premium
    (SL below entry, Target above entry).
    """
    direction: str | None = None  # "LONG" (call) or "SHORT" (put) or None
    entry_price: float = 0.0
    quantity: int = 0
    entry_time: str = ""
    sl_price: float = 0.0
    target_price: float = 0.0
    entry_order_id: str = ""
    sl_order_id: str = ""
    target_order_id: str = ""
    ema_fast_at_entry: float = 0.0
    ema_slow_at_entry: float = 0.0
    security_id: str = ""
    option_type: str = ""     # "CE" or "PE"
    strike: float = 0.0
    symbol: str = ""
    expiry: str = ""

    @property
    def is_open(self) -> bool:
        return self.direction is not None

    @property
    def option_side(self) -> str:
        """ATM option type held: 'CE' for LONG, 'PE' for SHORT."""
        if self.direction == "LONG":
            return "CE"
        if self.direction == "SHORT":
            return "PE"
        return ""

    def reset(self) -> None:
        """Clear the position."""
        self.direction = None
        self.entry_price = 0.0
        self.quantity = 0
        self.entry_time = ""
        self.sl_price = 0.0
        self.target_price = 0.0
        self.entry_order_id = ""
        self.sl_order_id = ""
        self.target_order_id = ""
        self.ema_fast_at_entry = 0.0
        self.ema_slow_at_entry = 0.0
        self.security_id = ""
        self.option_type = ""
        self.strike = 0.0
        self.symbol = ""
        self.expiry = ""


class RiskManager:
    """Manages SL, target, and consecutive SL tracking."""

    def __init__(self):
        self.position = Position()
        self.consecutive_sl_count: int = 0
        self.daily_trade_count: int = 0
        self.session_active: bool = False
        self.sl_hit: bool = False
        self.target_hit: bool = False
        self._today_date: str = ""

    def new_day(self) -> None:
        """Reset daily counters for a new trading day."""
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if self._today_date != today:
            self._today_date = today
            self.consecutive_sl_count = 0
            self.daily_trade_count = 0
            self.session_active = True
            self.position.reset()
            logger.info("New trading day: %s — session active", today)

    def can_trade(self) -> bool:
        """Check if new entries are allowed."""
        if not self.session_active:
            return False
        if self.consecutive_sl_count >= config.MAX_CONSECUTIVE_SL:
            logger.warning("Cannot trade: %d consecutive SLs hit", self.consecutive_sl_count)
            return False
        return True

    def on_entry_filled(
        self,
        direction: str,
        entry_price: float,
        quantity: int,
        entry_order_id: str,
        ema_fast: float,
        ema_slow: float,
        option: dict | None = None,
    ) -> None:
        """Record a filled entry.

        Args:
            direction: "LONG" (ATM call) or "SHORT" (ATM put)
            entry_price: filled entry premium
            quantity: number of contracts
            entry_order_id: entry order id
            ema_fast / ema_slow: EMA values at entry
            option: resolved option dict (security_id, option_type, strike,
                    symbol, expiry, ...) if trading options
        """
        from strategy import Strategy
        strategy = Strategy()
        sl_price, target_price = strategy.calculate_sl_target(direction, entry_price)

        now = datetime.now(IST)

        self.position = Position(
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            sl_price=sl_price,
            target_price=target_price,
            entry_order_id=entry_order_id,
            ema_fast_at_entry=ema_fast,
            ema_slow_at_entry=ema_slow,
            security_id=(option or {}).get("security_id", ""),
            option_type=(option or {}).get("option_type", self.position.option_side),
            strike=(option or {}).get("strike", 0.0),
            symbol=(option or {}).get("symbol", ""),
            expiry=(option or {}).get("expiry", ""),
        )
        self.daily_trade_count += 1
        self.sl_hit = False
        self.target_hit = False

        logger.info(
            "Position opened: %s %s @ %.2f qty=%d SL=%.2f Target=%.2f (SL count=%d)",
            direction, self.position.option_side, entry_price, quantity,
            sl_price, target_price, self.consecutive_sl_count
        )

    def on_sl_hit(self) -> None:
        """Handle a stop-loss execution."""
        self.consecutive_sl_count += 1
        self.sl_hit = True
        logger.warning(
            "SL HIT: %s @ %.2f — consecutive SL count: %d/%d",
            self.position.direction, self.position.entry_price,
            self.consecutive_sl_count, config.MAX_CONSECUTIVE_SL
        )

        if self.consecutive_sl_count >= config.MAX_CONSECUTIVE_SL:
            logger.warning(
                "MAX CONSECUTIVE SLs (%d) REACHED — stopping trading for the day",
                config.MAX_CONSECUTIVE_SL
            )
            self.session_active = False

        self.position.reset()

    def on_target_hit(self) -> None:
        """Handle a target (take-profit) execution."""
        self.target_hit = True
        logger.info(
            "TARGET HIT: %s @ %.2f — resetting consecutive SL counter from %d to 0",
            self.position.direction, self.position.entry_price, self.consecutive_sl_count
        )
        self.consecutive_sl_count = 0
        self.position.reset()

    def on_crossover_exit(self) -> None:
        """Handle an exit due to opposite crossover.

        A crossover exit should NOT count as an SL.
        """
        logger.info(
            "Crossover exit: %s @ entry=%.2f — SL count unchanged at %d",
            self.position.direction, self.position.entry_price, self.consecutive_sl_count
        )
        self.position.reset()

    def check_sl_target_hit(self, current_ltp: float) -> str | None:
        """Check if SL or target has been hit based on current option premium.

        The strategy always holds a long option (call or put bought), so the
        premium SL is below entry and the target is above entry for either side.

        Args:
            current_ltp: current option premium

        Returns:
            "SL" or "TARGET" or None
        """
        if not self.position.is_open:
            return None

        if current_ltp <= self.position.sl_price:
            return "SL"
        if current_ltp >= self.position.target_price:
            return "TARGET"

        return None

    def get_status(self) -> dict:
        """Get current risk manager status."""
        return {
            "direction": self.position.direction,
            "entry_price": self.position.entry_price,
            "quantity": self.position.quantity,
            "sl_price": self.position.sl_price,
            "target_price": self.position.target_price,
            "consecutive_sl_count": self.consecutive_sl_count,
            "daily_trade_count": self.daily_trade_count,
            "session_active": self.session_active,
        }
