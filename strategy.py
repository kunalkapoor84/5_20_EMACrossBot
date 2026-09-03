"""EMA crossover signal generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Signal(Enum):
    """Trading signal types."""
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"


@dataclass
class CrossoverResult:
    """Result of EMA crossover analysis."""
    signal: Signal
    ema_fast: float
    ema_slow: float
    close: float
    timestamp: str
    is_bullish_cross: bool = False
    is_bearish_cross: bool = False
    reason: str = ""


class Strategy:
    """Detects EMA 5/20 crossovers on completed 5-minute candles."""

    def __init__(self):
        self._last_ema_fast: float | None = None
        self._last_ema_slow: float | None = None
        self._last_processed_ts: str | None = None

    def detect_crossover(
        self,
        prev_candle: dict,
        curr_candle: dict,
    ) -> CrossoverResult:
        """Detect EMA crossover between two consecutive completed candles.

        Args:
            prev_candle: Previous completed candle with ema_fast, ema_slow, timestamp, close
            curr_candle: Current completed candle with ema_fast, ema_slow, timestamp, close

        Returns:
            CrossoverResult with the detected signal.
        """
        prev_ema5 = prev_candle["ema_fast"]
        prev_ema20 = prev_candle["ema_slow"]
        curr_ema5 = curr_candle["ema_fast"]
        curr_ema20 = curr_candle["ema_slow"]

        # Detect bullish crossover: EMA5 crosses above EMA20
        # Previous: EMA5 <= EMA20, Current: EMA5 > EMA20
        bullish_cross = (prev_ema5 <= prev_ema20) and (curr_ema5 > curr_ema20)

        # Detect bearish crossover: EMA5 crosses below EMA20
        # Previous: EMA5 >= EMA20, Current: EMA5 < EMA20
        bearish_cross = (prev_ema5 >= prev_ema20) and (curr_ema5 < curr_ema20)

        if bullish_cross:
            logger.info(
                "[%s] Bullish EMA crossover detected: EMA5=%.2f > EMA20=%.2f (prev: EMA5=%.2f <= EMA20=%.2f)",
                curr_candle["timestamp"], curr_ema5, curr_ema20, prev_ema5, prev_ema20
            )
            return CrossoverResult(
                signal=Signal.LONG,
                ema_fast=curr_ema5,
                ema_slow=curr_ema20,
                close=curr_candle["close"],
                timestamp=curr_candle["timestamp"],
                is_bullish_cross=True,
                reason=f"Bullish crossover: EMA5({curr_ema5:.2f}) crossed above EMA20({curr_ema20:.2f})",
            )

        if bearish_cross:
            logger.info(
                "[%s] Bearish EMA crossover detected: EMA5=%.2f < EMA20=%.2f (prev: EMA5=%.2f >= EMA20=%.2f)",
                curr_candle["timestamp"], curr_ema5, curr_ema20, prev_ema5, prev_ema20
            )
            return CrossoverResult(
                signal=Signal.SHORT,
                ema_fast=curr_ema5,
                ema_slow=curr_ema20,
                close=curr_candle["close"],
                timestamp=curr_candle["timestamp"],
                is_bearish_cross=True,
                reason=f"Bearish crossover: EMA5({curr_ema5:.2f}) crossed below EMA20({curr_ema20:.2f})",
            )

        # No crossover
        return CrossoverResult(
            signal=Signal.HOLD,
            ema_fast=curr_ema5,
            ema_slow=curr_ema20,
            close=curr_candle["close"],
            timestamp=curr_candle["timestamp"],
            reason="No crossover detected",
        )

    def should_exit_for_crossover(
        self,
        current_direction: str | None,
        prev_candle: dict,
        curr_candle: dict,
    ) -> tuple[bool, str]:
        """Check if the opposite crossover requires exiting the current position.

        Args:
            current_direction: "LONG" or "SHORT" or None
            prev_candle: Previous candle EMA data
            curr_candle: Current candle EMA data

        Returns:
            (should_exit, reason)
        """
        if current_direction is None:
            return False, ""

        result = self.detect_crossover(prev_candle, curr_candle)

        if current_direction == "LONG" and result.signal == Signal.SHORT:
            return True, result.reason

        if current_direction == "SHORT" and result.signal == Signal.LONG:
            return True, result.reason

        return False, ""

    def calculate_sl_target(self, direction: str, entry_price: float) -> tuple[float, float]:
        """Calculate stop loss and target prices based on entry premium.

        The strategy always BUYS options (long premium of an ATM call or put),
        so SL and Target are always expressed on the premium: SL = entry - SL points,
        Target = entry + target points, regardless of the call/put side.

        Args:
            direction: "LONG" (ATM call) or "SHORT" (ATM put)
            entry_price: Actual filled entry premium

        Returns:
            (sl_price, target_price) both relative to premium.
        """
        import config

        sl_price = entry_price - config.STOP_LOSS_POINTS
        target_price = entry_price + config.TARGET_POINTS
        return sl_price, target_price
