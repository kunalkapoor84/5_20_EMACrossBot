"""Market data fetching and EMA calculation for 5-minute candles."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import pytz

import config

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class MarketData:
    """Fetches 5-minute OHLC candles from Dhan and computes EMAs."""

    def __init__(self, dhan_client):
        self.dhan = dhan_client
        self.candles: pd.DataFrame = pd.DataFrame()
        self._last_candle_ts: str | None = None
        self._processed_timestamps: set[str] = set()
        # Data fetch parameters: use index for EMA signals, fallback to futures
        self._data_security_id = dhan_client.get_data_security_id()
        self._data_exchange = dhan_client.get_data_exchange()
        self._data_instrument = "INDEX"
        self._use_fallback_data = False

    def fetch_historical_candles(self, days: int = 5) -> pd.DataFrame:
        """Fetch recent 5-minute candles for EMA warm-up.

        Uses intraday_minute_data with interval=5.
        Returns a DataFrame with columns: timestamp, open, high, low, close, volume.
        """
        now = datetime.now(IST)
        from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d 09:15:00")
        to_date = now.strftime("%Y-%m-%d 15:30:00")

        logger.info("Fetching 5-min candles from %s to %s", from_date, to_date)

        response = self.dhan.client.intraday_minute_data(
            security_id=self._data_security_id,
            exchange_segment=self._data_exchange,
            instrument_type=self._data_instrument,
            from_date=from_date,
            to_date=to_date,
            interval=5,
            oi=False,
        )

        if response.get("status") != "success":
            logger.error("Failed to fetch historical candles from %s: %s",
                         self._data_exchange, response.get("remarks"))
            # Try fallback to NIFTY futures data if index data is unavailable
            if not self._use_fallback_data:
                logger.warning("Attempting fallback to NIFTY futures data")
                self._use_fallback_data = True
                self._data_security_id = self.dhan.get_trading_security_id()
                self._data_exchange = self.dhan.get_trading_exchange()
                self._data_instrument = "FUTIDX"
                response = self.dhan.client.intraday_minute_data(
                    security_id=self._data_security_id,
                    exchange_segment=self._data_exchange,
                    instrument_type=self._data_instrument,
                    from_date=from_date,
                    to_date=to_date,
                    interval=5,
                    oi=False,
                )
                if response.get("status") != "success":
                    logger.error("Fallback data fetch also failed: %s", response.get("remarks"))
                    return pd.DataFrame()
            else:
                return pd.DataFrame()

        data = response["data"]
        df = pd.DataFrame(data)

        if df.empty:
            logger.warning("No historical candles returned")
            return df

        # Convert epoch timestamps to IST datetime strings
        df["timestamp"] = df["timestamp"].apply(
            lambda ts: self._epoch_to_ist_str(ts)
        )
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(int)

        df = df.sort_values("timestamp").reset_index(drop=True)

        # Remove duplicates
        df = df.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)

        self.candles = df
        if not df.empty:
            self._last_candle_ts = df.iloc[-1]["timestamp"]
            logger.info("Loaded %d historical candles, last=%s", len(df), self._last_candle_ts)

        return df

    def fetch_latest_candles(self) -> pd.DataFrame:
        """Fetch the most recent candles to detect new completed candles.

        Returns only NEW completed candles that haven't been processed before.
        """
        now = datetime.now(IST)
        # Fetch last 2 hours of data to capture recent candles
        from_date = (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        to_date = now.strftime("%Y-%m-%d %H:%M:%S")

        response = self.dhan.client.intraday_minute_data(
            security_id=self._data_security_id,
            exchange_segment=self._data_exchange,
            instrument_type=self._data_instrument,
            from_date=from_date,
            to_date=to_date,
            interval=5,
            oi=False,
        )

        if response.get("status") != "success":
            logger.error("Failed to fetch latest candles from %s: %s",
                         self._data_exchange, response.get("remarks"))
            # Try fallback to NIFTY futures data if index data is unavailable
            if not self._use_fallback_data:
                logger.warning("Attempting fallback to NIFTY futures data")
                self._use_fallback_data = True
                self._data_security_id = self.dhan.get_trading_security_id()
                self._data_exchange = self.dhan.get_trading_exchange()
                self._data_instrument = "FUTIDX"
                response = self.dhan.client.intraday_minute_data(
                    security_id=self._data_security_id,
                    exchange_segment=self._data_exchange,
                    instrument_type=self._data_instrument,
                    from_date=from_date,
                    to_date=to_date,
                    interval=5,
                    oi=False,
                )
                if response.get("status") != "success":
                    logger.error("Fallback data fetch also failed: %s", response.get("remarks"))
                    return pd.DataFrame()
            else:
                return pd.DataFrame()

        data = response["data"]
        df = pd.DataFrame(data)

        if df.empty:
            return df

        df["timestamp"] = df["timestamp"].apply(
            lambda ts: self._epoch_to_ist_str(ts)
        )
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(int)

        df = df.sort_values("timestamp").reset_index(drop=True)
        df = df.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)

        # Filter for completed candles only (not the current candle)
        # NOTE: candle timestamps are IST-naive strings; use naive cutoff for comparison
        current_time = now.replace(second=0, microsecond=0)
        current_time = current_time.replace(tzinfo=None)
        # A candle is "completed" if its timestamp is at least 5 minutes before now
        completed_cutoff = current_time - timedelta(minutes=config.TIMEFRAME_MINUTES)

        df["ts_dt"] = pd.to_datetime(df["timestamp"])
        completed = df[df["ts_dt"] <= completed_cutoff].copy()
        completed = completed.drop(columns=["ts_dt"])

        # Return only new candles
        if not completed.empty:
            new = completed[~completed["timestamp"].isin(self._processed_timestamps)]
            return new

        return pd.DataFrame()

    def mark_candle_processed(self, timestamp: str) -> None:
        """Mark a candle timestamp as processed."""
        self._processed_timestamps.add(timestamp)
        self._last_candle_ts = timestamp

    def update_candles(self, new_candles: pd.DataFrame) -> None:
        """Append new candles to the main candle DataFrame."""
        if new_candles.empty:
            return

        self.candles = pd.concat(
            [self.candles, new_candles], ignore_index=True
        )
        self.candles = self.candles.drop_duplicates(
            subset=["timestamp"], keep="last"
        ).sort_values("timestamp").reset_index(drop=True)

    def compute_emas(self, df: pd.DataFrame | None = None) -> pd.DataFrame:
        """Compute EMA5 and EMA20 on the candle DataFrame.

        Returns the DataFrame with added ema_fast and ema_slow columns.
        """
        if df is None:
            df = self.candles

        if df.empty or len(df) < config.SLOW_EMA:
            logger.warning("Not enough candles for EMA calculation (need %d, have %d)",
                          config.SLOW_EMA, len(df))
            return df

        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=config.FAST_EMA, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=config.SLOW_EMA, adjust=False).mean()

        return df

    def get_last_two_completed_emas(self) -> tuple[dict, dict] | None:
        """Get EMA values for the last two completed candles.

        Returns (prev_candle_emas, curr_candle_emas) or None if not enough data.
        Each dict has keys: timestamp, close, ema_fast, ema_slow.
        """
        df = self.compute_emas()
        if df.empty or len(df) < 2:
            return None

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        return (
            {
                "timestamp": prev["timestamp"],
                "close": float(prev["close"]),
                "ema_fast": float(prev["ema_fast"]),
                "ema_slow": float(prev["ema_slow"]),
            },
            {
                "timestamp": curr["timestamp"],
                "close": float(curr["close"]),
                "ema_fast": float(curr["ema_fast"]),
                "ema_slow": float(curr["ema_slow"]),
            },
        )

    def get_current_ltp(self, security_id: str | None = None) -> float | None:
        """Get the current last traded price for a security.

        Args:
            security_id: security to fetch LTP for. Defaults to the currently
                         traded instrument (for options, the last resolved ATM option).

        Returns:
            LTP float or None on failure.
        """
        try:
            if not security_id:
                if config.TRADE_OPTIONS:
                    security_id = self.dhan.get_trading_security_id()
                else:
                    security_id = self.dhan.get_trading_security_id()
            if not security_id:
                return None
            exchange = self.dhan.get_trading_exchange() or config.OPTION_EXCHANGE_SEGMENT

            response = self.dhan.client.ticker_data({exchange: [int(security_id)]})
            if response.get("status") == "success":
                data = response["data"]
                ltp = float(data[exchange][security_id]["last_price"])
                return ltp
            else:
                logger.error("LTP fetch failed: %s", response.get("remarks"))
        except Exception as e:
            logger.error("Failed to fetch LTP: %s", e)
        return None

    def _epoch_to_ist_str(self, epoch: int | float) -> str:
        """Convert epoch timestamp to IST string format YYYY-MM-DD HH:MM:SS."""
        from datetime import timezone
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(IST)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def is_market_open(self) -> bool:
        """Check if the current time is within trading hours."""
        now = datetime.now(IST)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close

    def is_trading_time(self) -> bool:
        """Check if the current time is within strategy trading hours (09:30-15:15)."""
        now = datetime.now(IST)
        start = now.replace(
            hour=int(config.START_TIME.split(":")[0]),
            minute=int(config.START_TIME.split(":")[1]),
            second=0, microsecond=0
        )
        exit_ = now.replace(
            hour=int(config.EXIT_TIME.split(":")[0]),
            minute=int(config.EXIT_TIME.split(":")[1]),
            second=0, microsecond=0
        )
        return start <= now <= exit_
