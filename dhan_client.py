"""Dhan SDK initialization and client management."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd
import pytz
from dhanhq import DhanContext, dhanhq

import config

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class DhanClient:
    """Wrapper around DhanHQ SDK for NIFTY EMA crossover strategy.

    Resolves the nearest NIFTY futures contract for trading while using
    the NIFTY 50 index for EMA signal calculation.
    """

    def __init__(self, paper_trading: bool = True):
        self.paper_trading = paper_trading
        self._client: dhanhq | None = None
        self._context: DhanContext | None = None
        self._lot_size: int | None = None
        self._trading_security_id: str = ""
        self._trading_exchange: str = config.OPTION_EXCHANGE_SEGMENT
        self._futures_security_id: str = ""
        self._futures_exchange: str = config.FUTURES_EXCHANGE_SEGMENT
        # Cached last resolved option contract
        self._option_cache: dict = {}
        self._connect()

    def _connect(self) -> None:
        """Initialize the Dhan SDK connection."""
        client_id = config.DHAN_CLIENT_ID
        access_token = config.DHAN_ACCESS_TOKEN

        if not client_id or not access_token:
            raise ValueError(
                "Dhan credentials not found. Set DHAN_CLIENT_ID and "
                "DHAN_ACCESS_TOKEN environment variables."
            )

        self._context = DhanContext(client_id, access_token)
        self._client = dhanhq(self._context)
        logger.info("Dhan client connected (paper_trading=%s)", self.paper_trading)

    @property
    def client(self) -> dhanhq:
        if self._client is None:
            raise RuntimeError("Dhan client not connected")
        return self._client

    @property
    def context(self) -> DhanContext:
        if self._context is None:
            raise RuntimeError("Dhan context not connected")
        return self._context

    def resolve_nifty_futures(self) -> str:
        """Resolve the nearest NIFTY 50 futures contract security_id.

        Uses the security master to find the current month's NIFTY 50 future.
        The symbol prefix "NIFTY-" uniquely identifies the NIFTY 50 index
        future (excludes NIFTYFPI, NIFTYNXT50, BANKNIFTY, etc.).
        Returns the security_id string.
        """
        if self._futures_security_id:
            return self._futures_security_id

        try:
            df = dhanhq.fetch_security_list("compact")

            # Find NIFTY 50 futures (FUTIDX) on NSE
            # SEM_TRADING_SYMBOL looks like "NIFTY-Sep2026-FUT"
            nifty_futs = df[
                (df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == "NSE")
                & (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == "FUTIDX")
                & (df["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.startswith("NIFTY-"))
            ].copy()

            if nifty_futs.empty:
                logger.warning("No NIFTY 50 futures found in security master, using fallback")
                self._futures_security_id = config.FUTURES_SECURITY_ID or "52437"
                self._lot_size = 75
                return self._futures_security_id

            # Parse expiry dates and find the nearest future (>= today)
            nifty_futs["expiry_dt"] = pd.to_datetime(nifty_futs["SEM_EXPIRY_DATE"], errors="coerce")
            nifty_futs = nifty_futs.dropna(subset=["expiry_dt"])
            today_naive = pd.Timestamp(datetime.now(IST).date())
            upcoming = nifty_futs[nifty_futs["expiry_dt"] >= today_naive].sort_values("expiry_dt")

            if upcoming.empty:
                logger.warning("No upcoming NIFTY 50 futures found, using nearest expiry")
                upcoming = nifty_futs.sort_values("expiry_dt", ascending=False)

            if upcoming.empty:
                self._futures_security_id = config.FUTURES_SECURITY_ID or "52437"
                self._lot_size = 75
            else:
                row = upcoming.iloc[0]
                self._futures_security_id = str(row["SEM_SMST_SECURITY_ID"])
                self._lot_size = int(row["SEM_LOT_UNITS"])

            self._futures_exchange = config.FUTURES_EXCHANGE_SEGMENT
            logger.info(
                "Resolved NIFTY 50 futures: security_id=%s lot=%s symbol=%s expiry=%s",
                self._futures_security_id, self._lot_size,
                upcoming.iloc[0].get("SEM_TRADING_SYMBOL", "?") if not upcoming.empty else "?",
                upcoming.iloc[0].get("SEM_EXPIRY_DATE", "?") if not upcoming.empty else "?"
            )
            return self._futures_security_id

        except Exception as e:
            logger.error("Failed to resolve NIFTY futures: %s - using fallback", e)
            self._futures_security_id = config.FUTURES_SECURITY_ID or "52437"
            self._lot_size = 75
            return self._futures_security_id

    def get_lot_size(self) -> int:
        """Resolve NIFTY lot size from security master. Caches after first call."""
        if self._lot_size is not None:
            return self._lot_size

        try:
            df = dhanhq.fetch_security_list("compact")
            nifty_opts = df[
                (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == "OPTIDX")
                & (df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == "NSE")
                & (df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper().str.startswith("NIFTY"))
                & (~df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper().str.startswith("NIFTYF"))
            ]
            if not nifty_opts.empty:
                self._lot_size = int(nifty_opts.iloc[0]["SEM_LOT_UNITS"])
                return self._lot_size
        except Exception as e:
            logger.error("Failed to resolve NIFTY lot size: %s", e)

        self._lot_size = self._lot_size or 75
        return self._lot_size

    # ------------------------------------------------------------------
    # NIFTY ATM option resolution
    # ------------------------------------------------------------------
    def resolve_atm_option(self, side: str, spot: float | None = None) -> dict:
        """Resolve the ATM NIFTY option contract for a given side.

        Args:
            side: "CE" (call, buy on bullish) or "PE" (put, buy on bearish)
            spot: current NIFTY index spot. Fetched live if not provided.

        Returns:
            dict with keys: security_id, symbol, option_type, strike,
            lot_size, expiry, last_price, bid, ask, delta.
        """
        expiry = self._resolve_option_expiry()
        chain = self._fetch_option_chain(expiry)
        if chain is None:
            raise RuntimeError("Failed to fetch NIFTY option chain")

        spot = spot or float(chain["last_price"])
        oc = chain["oc"]

        # nearest strike to spot
        best_key = min(oc.keys(), key=lambda k: abs(float(k) - spot))
        best_strike = float(best_key)
        leg = oc[best_key].get(side.lower())
        if leg is None or not leg.get("security_id"):
            raise RuntimeError(f"No {side} leg found for strike {best_strike}")

        option = {
            "security_id": str(leg["security_id"]),
            "option_type": side,
            "strike": best_strike,
            "lot_size": self.get_lot_size(),
            "expiry": expiry,
            "last_price": float(leg.get("last_price", 0) or 0),
            "bid": float(leg.get("top_bid_price", 0) or 0),
            "ask": float(leg.get("top_ask_price", 0) or 0),
            "delta": float(leg.get("greeks", {}).get("delta", 0) or 0),
            "symbol": self._option_symbol(side, best_strike, expiry),
        }
        if self._lot_size is None:
            self._lot_size = option["lot_size"]

        self._option_cache = option
        self._trading_security_id = option["security_id"]
        self._trading_exchange = config.OPTION_EXCHANGE_SEGMENT

        logger.info(
            "Resolved NIFTY ATM %s: security_id=%s strike=%.0f premium=%.2f lot=%d expiry=%s",
            side, option["security_id"], best_strike, option["last_price"],
            option["lot_size"], expiry
        )
        return option

    def _resolve_option_expiry(self) -> str:
        """Return the option expiry date string (YYYY-MM-DD) to trade.

        monthly: the latest expiry in the current calendar month (>= today).
        weekly: the nearest expiry >= today.
        """
        today = datetime.now(IST).date()
        try:
            df = dhanhq.fetch_security_list("compact")
            nifty_opts = df[
                (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == "OPTIDX")
                & (df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == "NSE")
                & (df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper().str.startswith("NIFTY"))
                & (~df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper().str.startswith("NIFTYF"))
                & (df["SEM_OPTION_TYPE"].astype(str).str.upper() == "CE")
            ].copy()
            exp = pd.to_datetime(nifty_opts["SEM_EXPIRY_DATE"], errors="coerce")
            nifty_opts["_exp_dt"] = exp
            nifty_opts = nifty_opts.dropna(subset=["_exp_dt"])
            # convert to date objects for comparison
            nifty_opts["_exp"] = nifty_opts["_exp_dt"].dt.date
            future = nifty_opts[nifty_opts["_exp"] >= today]

            if config.OPTION_EXPIRY == "weekly":
                if not future.empty:
                    return str(future["_exp"].min())
            # monthly: latest expiry in the current calendar month (>= today)
            current_month = future[future["_exp"].apply(lambda d: d.year == today.year and d.month == today.month)]
            if not current_month.empty:
                return str(current_month["_exp"].max())
            if not future.empty:
                return str(future["_exp"].min())
            if not nifty_opts.empty:
                return str(nifty_opts["_exp"].max())
        except Exception as e:
            logger.error("Failed to resolve option expiry: %s", e)
        return str(today)

    def _fetch_option_chain(self, expiry: str) -> dict | None:
        """Fetch the NIFTY option chain for the given expiry, with retries.

        The option-chain endpoint intermittently fails (rate limits / transient
        errors), so retry a few times with backoff before giving up.
        """
        import time

        for attempt in range(4):
            try:
                response = self.client.option_chain(
                    under_security_id=int(config.INDEX_SECURITY_ID),
                    under_exchange_segment=config.INDEX_EXCHANGE_SEGMENT,
                    expiry=expiry,
                )
                if response.get("status") == "success":
                    return response["data"]["data"]
                logger.warning(
                    "Option chain attempt %d/4 failed: %s",
                    attempt + 1, response.get("remarks")
                )
            except Exception as e:
                logger.warning("Option chain attempt %d/4 exception: %s", attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
        logger.error("Option chain fetch failed after retries for expiry %s", expiry)
        return None

    @staticmethod
    def _option_symbol(side: str, strike: float, expiry: str) -> str:
        """Build a readable option symbol like NIFTY-Sep2026-23900-CE."""
        year = pd.to_datetime(expiry).strftime("%Y")
        month = pd.to_datetime(expiry).strftime("%b")
        return f"NIFTY-{month}{year}-{int(strike)}-{side}"

    # ------------------------------------------------------------------
    # Trading / data instrument getters
    # ------------------------------------------------------------------
    def get_trading_security_id(self) -> str:
        """Get the security_id used for trading.

        For options this is the last resolved ATM option contract; for
        futures it is resolved from the security master.
        """
        if config.TRADE_OPTIONS:
            return self._trading_security_id or ""
        if not self._futures_security_id:
            self.resolve_nifty_futures()
        return self._futures_security_id

    def get_trading_exchange(self) -> str:
        """Get the exchange segment used for trading."""
        if config.TRADE_OPTIONS:
            return self._trading_exchange or config.OPTION_EXCHANGE_SEGMENT
        return self._futures_exchange

    def get_data_security_id(self) -> str:
        """Get the security_id used for market data (NIFTY index)."""
        return config.INDEX_SECURITY_ID

    def get_data_exchange(self) -> str:
        """Get the exchange segment used for market data."""
        return config.INDEX_EXCHANGE_SEGMENT

    def get_profile(self) -> dict[str, Any] | None:
        """Fetch user profile to verify access."""
        try:
            from dhanhq import DhanLogin
            dhan_login = DhanLogin(config.DHAN_CLIENT_ID)
            profile = dhan_login.user_profile(config.DHAN_ACCESS_TOKEN)
            if isinstance(profile, dict) and profile.get("status") == "success":
                return profile.get("data")
            return profile
        except Exception as e:
            logger.error("Failed to fetch profile: %s", e)
            return None
