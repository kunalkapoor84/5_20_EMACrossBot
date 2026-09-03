"""Persistent state management for strategy recovery across restarts."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import pytz

import config

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class StateManager:
    """Saves and loads strategy state to/from disk."""

    def __init__(self):
        os.makedirs(config.STATE_DIR, exist_ok=True)

    def save_state(self, state: dict) -> None:
        """Persist strategy state to disk."""
        state["saved_at"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(config.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            logger.debug("State saved to %s", config.STATE_FILE)
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def load_state(self) -> dict | None:
        """Load strategy state from disk."""
        if not os.path.exists(config.STATE_FILE):
            logger.info("No saved state found")
            return None

        try:
            with open(config.STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            logger.info("State loaded from %s (saved at %s)", config.STATE_FILE, state.get("saved_at"))
            return state
        except Exception as e:
            logger.error("Failed to load state: %s", e)
            return None

    def clear_state(self) -> None:
        """Remove the saved state file."""
        if os.path.exists(config.STATE_FILE):
            os.remove(config.STATE_FILE)
            logger.info("State file cleared")

    def build_state(
        self,
        direction: str | None,
        entry_price: float,
        quantity: int,
        entry_time: str,
        sl_price: float,
        target_price: float,
        consecutive_sl_count: int,
        daily_trade_count: int,
        session_active: bool,
        last_processed_candle_ts: str | None,
        entry_order_id: str = "",
        sl_order_id: str = "",
        target_order_id: str = "",
        ema_fast_at_entry: float = 0.0,
        ema_slow_at_entry: float = 0.0,
        security_id: str = "",
        option_type: str = "",
        strike: float = 0.0,
        symbol: str = "",
        expiry: str = "",
    ) -> dict:
        """Build a state dictionary for persistence."""
        return {
            "direction": direction,
            "entry_price": entry_price,
            "quantity": quantity,
            "entry_time": entry_time,
            "sl_price": sl_price,
            "target_price": target_price,
            "entry_order_id": entry_order_id,
            "sl_order_id": sl_order_id,
            "target_order_id": target_order_id,
            "ema_fast_at_entry": ema_fast_at_entry,
            "ema_slow_at_entry": ema_slow_at_entry,
            "consecutive_sl_count": consecutive_sl_count,
            "daily_trade_count": daily_trade_count,
            "session_active": session_active,
            "last_processed_candle_ts": last_processed_candle_ts,
            "security_id": security_id,
            "option_type": option_type,
            "strike": strike,
            "symbol": symbol,
            "expiry": expiry,
        }

    def reconcile_with_broker(self, state: dict | None, broker_positions: list[dict], trading_security_id: str | None = None) -> dict:
        """Reconcile local state with actual broker positions.

        The broker state takes precedence over local state.

        Args:
            state: Local saved state
            broker_positions: List of open positions from Dhan API
            trading_security_id: The security_id used for trading (NIFTY futures)

        Returns:
            Reconciled state dict
        """
        traded_sid = trading_security_id or config.SECURITY_ID

        if state is None:
            state = self.build_state(
                direction=None,
                entry_price=0,
                quantity=0,
                entry_time="",
                sl_price=0,
                target_price=0,
                consecutive_sl_count=0,
                daily_trade_count=0,
                session_active=True,
                last_processed_candle_ts=None,
            )

        # Find our instrument in broker positions
        our_position = None
        for pos in broker_positions:
            if str(pos.get("securityId", "")) == traded_sid:
                net_qty = pos.get("netQty", 0)
                if net_qty != 0:
                    our_position = pos
                    break

        if our_position is None:
            # No broker position — clear local position if any
            if state.get("direction") is not None:
                logger.warning(
                    "Broker has no position for %s but local state says %s — reconciling to flat",
                    traded_sid, state.get("direction")
                )
                state["direction"] = None
                state["entry_price"] = 0
                state["quantity"] = 0
                state["sl_price"] = 0
                state["target_price"] = 0
                state["entry_order_id"] = ""
                state["sl_order_id"] = ""
                state["target_order_id"] = ""
                state["security_id"] = ""
                state["option_type"] = ""
                state["strike"] = 0
                state["symbol"] = ""
                state["expiry"] = ""
        else:
            # Broker has a position — use broker data as truth
            net_qty = int(our_position.get("netQty", 0))
            if net_qty > 0:
                broker_direction = "LONG"
            elif net_qty < 0:
                broker_direction = "SHORT"
            else:
                broker_direction = None

            if broker_direction != state.get("direction"):
                logger.warning(
                    "Broker direction (%s) != local direction (%s) — using broker",
                    broker_direction, state.get("direction")
                )
                state["direction"] = broker_direction

            # Use broker average price as entry
            buy_avg = float(our_position.get("buyAvg", 0))
            sell_avg = float(our_position.get("sellAvg", 0))
            if broker_direction == "LONG" and buy_avg > 0:
                state["entry_price"] = buy_avg
            elif broker_direction == "SHORT" and sell_avg > 0:
                state["entry_price"] = sell_avg

            state["quantity"] = abs(net_qty)

        return state
