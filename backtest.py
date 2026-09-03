"""Backtesting module for NIFTY 50 EMA 5/20 Crossover Strategy.

Simulates the strategy on historical 5-minute data with:
- EMA 5/20 crossover signals
- 15-point SL / 30-point target
- Crossover exits
- 3-consecutive-SL daily shutdown
- Trading hours 09:30-15:15
- Trade-by-trade P&L, win rate, drawdown, etc.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
import pytz

import config

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


@dataclass
class BacktestTrade:
    """A single backtested trade."""
    date: str
    direction: str
    quantity: int
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    sl_price: float
    target_price: float
    exit_reason: str  # "SL", "TARGET", "CROSSOVER", "EOD"
    gross_pnl: float = 0.0
    ema_fast_at_entry: float = 0.0
    ema_slow_at_entry: float = 0.0
    ema_fast_at_exit: float = 0.0
    ema_slow_at_exit: float = 0.0


@dataclass
class BacktestResult:
    """Summary of backtest results."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    target_wins: int = 0
    sl_losses: int = 0
    crossover_exits: int = 0
    eod_exits: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    average_pnl: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    days_stopped: int = 0
    total_days: int = 0
    trades_per_day: float = 0.0
    trades: list[BacktestTrade] = field(default_factory=list)
    daily_pnl: dict[str, float] = field(default_factory=dict)


class Backtester:
    """Backtest the EMA 5/20 crossover strategy on historical 5-minute data."""

    def __init__(self, quantity: int = 75):
        self.quantity = quantity

    def run(
        self,
        candles_df: pd.DataFrame,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BacktestResult:
        """Run backtest on historical 5-minute candle data.

        Args:
            candles_df: DataFrame with columns: timestamp, open, high, low, close
                       (timestamp in IST YYYY-MM-DD HH:MM:SS format)
            start_date: Filter start date (YYYY-MM-DD)
            end_date: Filter filter end date (YYYY-MM-DD)

        Returns:
            BacktestResult with detailed statistics
        """
        df = candles_df.copy()
        df["ts_dt"] = pd.to_datetime(df["timestamp"])

        if start_date:
            df = df[df["ts_dt"] >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df["ts_dt"] <= pd.Timestamp(end_date) + timedelta(days=1)]

        df = df.sort_values("ts_dt").reset_index(drop=True)

        if len(df) < config.SLOW_EMA + 1:
            logger.warning("Insufficient data for backtest (need at least %d candles)", config.SLOW_EMA + 1)
            return BacktestResult()

        # Compute EMAs
        df["ema_fast"] = df["close"].ewm(span=config.FAST_EMA, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=config.SLOW_EMA, adjust=False).mean()

        trades: list[BacktestTrade] = []
        daily_pnl: dict[str, float] = {}
        days_stopped = 0
        total_days = 0

        # Group by trading day
        df["date"] = df["ts_dt"].dt.date.astype(str)
        trading_days = df["date"].unique()

        for day in trading_days:
            day_df = df[df["date"] == day].copy()
            total_days += 1
            day_pnl = 0.0

            # Filter for trading hours (09:30 to 15:15) - timestamps are IST-naive
            start_time = pd.Timestamp(f"{day} 09:30:00")
            exit_time = pd.Timestamp(f"{day} 15:15:00")
            trading_df = day_df[(day_df["ts_dt"] >= start_time) & (day_df["ts_dt"] <= exit_time)].copy()

            if trading_df.empty:
                continue

            # State
            position_direction = None
            armed_direction = None
            entry_price = 0.0
            entry_time = ""
            sl_price = 0.0
            target_price = 0.0
            consecutive_sl = 0
            ema_fast_entry = 0.0
            ema_slow_entry = 0.0

            for i in range(1, len(trading_df)):
                prev = trading_df.iloc[i - 1]
                curr = trading_df.iloc[i]

                prev_ema5 = prev["ema_fast"]
                prev_ema20 = prev["ema_slow"]
                curr_ema5 = curr["ema_fast"]
                curr_ema20 = curr["ema_slow"]
                curr_close = curr["close"]
                curr_time = curr["ts_dt"].strftime("%H:%M:%S")

                # Check SL/Target if in position (options are long premium:
                # SL is below entry, target is above entry for both call/put)
                if position_direction is not None:
                    sl_hit = False
                    target_hit = False

                    if curr["low"] <= sl_price:
                        sl_hit = True
                    elif curr["high"] >= target_price:
                        target_hit = True

                    if sl_hit:
                        pnl = self._calculate_pnl(position_direction, entry_price, sl_price, self.quantity)
                        trade = BacktestTrade(
                            date=day, direction=position_direction, quantity=self.quantity,
                            entry_time=entry_time, entry_price=entry_price,
                            exit_time=curr_time, exit_price=sl_price,
                            sl_price=sl_price, target_price=target_price,
                            exit_reason="SL", gross_pnl=pnl,
                            ema_fast_at_entry=ema_fast_entry, ema_slow_at_entry=ema_slow_entry,
                            ema_fast_at_exit=curr_ema5, ema_slow_at_exit=curr_ema20,
                        )
                        trades.append(trade)
                        day_pnl += pnl
                        consecutive_sl += 1
                        position_direction = None

                        if consecutive_sl >= config.MAX_CONSECUTIVE_SL:
                            days_stopped += 1
                            logger.info("Day %s: Stopped after %d consecutive SLs", day, consecutive_sl)
                            break
                        continue

                    if target_hit:
                        pnl = self._calculate_pnl(position_direction, entry_price, target_price, self.quantity)
                        trade = BacktestTrade(
                            date=day, direction=position_direction, quantity=self.quantity,
                            entry_time=entry_time, entry_price=entry_price,
                            exit_time=curr_time, exit_price=target_price,
                            sl_price=sl_price, target_price=target_price,
                            exit_reason="TARGET", gross_pnl=pnl,
                            ema_fast_at_entry=ema_fast_entry, ema_slow_at_entry=ema_slow_entry,
                            ema_fast_at_exit=curr_ema5, ema_slow_at_exit=curr_ema20,
                        )
                        trades.append(trade)
                        day_pnl += pnl
                        consecutive_sl = 0  # Reset on target
                        position_direction = None
                        continue

                # Detect crossover
                bullish_cross = (prev_ema5 <= prev_ema20) and (curr_ema5 > curr_ema20)
                bearish_cross = (prev_ema5 >= prev_ema20) and (curr_ema5 < curr_ema20)

                # Handle crossover exit + re-arm the opposite direction
                if position_direction is not None:
                    if (position_direction == "LONG" and bearish_cross) or \
                       (position_direction == "SHORT" and bullish_cross):
                        pnl = self._calculate_pnl(position_direction, entry_price, curr_close, self.quantity)
                        trade = BacktestTrade(
                            date=day, direction=position_direction, quantity=self.quantity,
                            entry_time=entry_time, entry_price=entry_price,
                            exit_time=curr_time, exit_price=curr_close,
                            sl_price=sl_price, target_price=target_price,
                            exit_reason="CROSSOVER", gross_pnl=pnl,
                            ema_fast_at_entry=ema_fast_entry, ema_slow_at_entry=ema_slow_entry,
                            ema_fast_at_exit=curr_ema5, ema_slow_at_exit=curr_ema20,
                        )
                        trades.append(trade)
                        day_pnl += pnl
                        position_direction = None
                        # Arm the new direction: on an opposite cross we exit and then
                        # wait for the new direction's close confirmation before entering.
                        armed_direction = "SHORT" if bearish_cross else "LONG"
                        # Don't reset consecutive_sl on crossover exit

                # Update armed direction from a fresh crossover while flat
                if position_direction is None:
                    if bullish_cross:
                        armed_direction = "LONG"
                    elif bearish_cross:
                        armed_direction = "SHORT"

                    # Enter once the candle CLOSES on the confirmed side of EMA20
                    def _close_confirms(direction: str) -> bool:
                        if not config.REQUIRE_CLOSE_CONFIRMATION:
                            return True
                        return (curr_close > curr_ema20) if direction == "LONG" else (curr_close < curr_ema20)

                    if (armed_direction is not None
                            and consecutive_sl < config.MAX_CONSECUTIVE_SL
                            and _close_confirms(armed_direction)):
                        position_direction = armed_direction
                        entry_price = curr_close
                        entry_time = curr_time
                        # Options are always long premium: SL below, target above
                        sl_price = entry_price - config.STOP_LOSS_POINTS
                        target_price = entry_price + config.TARGET_POINTS
                        ema_fast_entry = curr_ema5
                        ema_slow_entry = curr_ema20
                        armed_direction = None

            # End-of-day square-off
            if position_direction is not None:
                last_close = trading_df.iloc[-1]["close"]
                pnl = self._calculate_pnl(position_direction, entry_price, last_close, self.quantity)
                trade = BacktestTrade(
                    date=day, direction=position_direction, quantity=self.quantity,
                    entry_time=entry_time, entry_price=entry_price,
                    exit_time="15:15:00", exit_price=last_close,
                    sl_price=sl_price, target_price=target_price,
                    exit_reason="EOD", gross_pnl=pnl,
                    ema_fast_at_entry=ema_fast_entry, ema_slow_at_entry=ema_slow_entry,
                    ema_fast_at_exit=trading_df.iloc[-1]["ema_fast"],
                    ema_slow_at_exit=trading_df.iloc[-1]["ema_slow"],
                )
                trades.append(trade)
                day_pnl += pnl

            daily_pnl[day] = day_pnl

        # Compute statistics
        result = self._compute_statistics(trades, daily_pnl, total_days, days_stopped)
        return result

    def _calculate_pnl(self, direction: str, entry: float, exit: float, quantity: int) -> float:
        """Calculate P&L for a trade.

        For the options strategy the position is always long premium (bought
        call on bullish, bought put on bearish), so P&L is always
        (exit - entry) * quantity for either side.
        """
        return (exit - entry) * quantity

    def _compute_statistics(
        self,
        trades: list[BacktestTrade],
        daily_pnl: dict[str, float],
        total_days: int,
        days_stopped: int,
    ) -> BacktestResult:
        """Compute comprehensive backtest statistics."""
        if not trades:
            return BacktestResult(total_days=total_days, days_stopped=days_stopped)

        result = BacktestResult()
        result.trades = trades
        result.total_trades = len(trades)
        result.total_days = total_days
        result.days_stopped = days_stopped
        result.daily_pnl = daily_pnl

        for t in trades:
            if t.gross_pnl > 0:
                result.wins += 1
            elif t.gross_pnl < 0:
                result.losses += 1

            if t.exit_reason == "TARGET":
                result.target_wins += 1
            elif t.exit_reason == "SL":
                result.sl_losses += 1
            elif t.exit_reason == "CROSSOVER":
                result.crossover_exits += 1
            elif t.exit_reason == "EOD":
                result.eod_exits += 1

            result.total_pnl += t.gross_pnl

        result.win_rate = (result.wins / result.total_trades * 100) if result.total_trades > 0 else 0
        result.average_pnl = result.total_pnl / result.total_trades if result.total_trades > 0 else 0

        wins = [t.gross_pnl for t in trades if t.gross_pnl > 0]
        losses = [t.gross_pnl for t in trades if t.gross_pnl < 0]

        result.average_win = sum(wins) / len(wins) if wins else 0
        result.average_loss = sum(losses) / len(losses) if losses else 0
        result.max_win = max(wins) if wins else 0
        result.max_loss = min(losses) if losses else 0
        result.trades_per_day = result.total_trades / total_days if total_days > 0 else 0

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Maximum drawdown
        cumulative_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cumulative_pnl += t.gross_pnl
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd

        return result

    def save_trades_csv(self, result: BacktestResult, filepath: str) -> None:
        """Save backtest trades to CSV."""
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Date", "Direction", "Quantity", "Entry Time", "Entry Price",
                "Exit Time", "Exit Price", "SL Price", "Target Price",
                "Exit Reason", "Gross P&L",
                "EMA5 Entry", "EMA20 Entry", "EMA5 Exit", "EMA20 Exit",
            ])
            for t in result.trades:
                writer.writerow([
                    t.date, t.direction, t.quantity, t.entry_time, t.entry_price,
                    t.exit_time, t.exit_price, t.sl_price, t.target_price,
                    t.exit_reason, t.gross_pnl,
                    t.ema_fast_at_entry, t.ema_slow_at_entry,
                    t.ema_fast_at_exit, t.ema_slow_at_exit,
                ])

        logger.info("Trades saved to %s", filepath)

    def print_report(self, result: BacktestResult) -> None:
        """Print a detailed backtest report."""
        print("\n" + "=" * 70)
        print("  NIFTY 50 EMA 5/20 CROSSOVER - BACKTEST REPORT")
        print("=" * 70)

        print(f"\n  Period:       {result.total_days} trading days")
        print(f"  Days stopped: {result.days_stopped} (after {config.MAX_CONSECUTIVE_SL} consecutive SLs)")

        print(f"\n  {'-'*60}")
        print(f"  TRADE STATISTICS")
        print(f"  {'-'*60}")
        print(f"  Total trades:    {result.total_trades}")
        print(f"  Trades/day:      {result.trades_per_day:.1f}")
        print(f"  Wins:            {result.wins}")
        print(f"  Losses:          {result.losses}")
        print(f"  Win rate:        {result.win_rate:.1f}%")

        print(f"\n  {'-'*60}")
        print(f"  EXIT REASONS")
        print(f"  {'-'*60}")
        print(f"  Target hits:     {result.target_wins}")
        print(f"  SL hits:         {result.sl_losses}")
        print(f"  Crossover exits: {result.crossover_exits}")
        print(f"  EOD exits:       {result.eod_exits}")

        print(f"\n  {'-'*60}")
        print(f"  P&L")
        print(f"  {'-'*60}")
        print(f"  Total P&L:       {result.total_pnl:+.2f}")
        print(f"  Average P&L:     {result.average_pnl:+.2f}")
        print(f"  Average win:     {result.average_win:+.2f}")
        print(f"  Average loss:    {result.average_loss:+.2f}")
        print(f"  Max win:         {result.max_win:+.2f}")
        print(f"  Max loss:        {result.max_loss:+.2f}")
        print(f"  Max drawdown:    {result.max_drawdown:.2f}")
        print(f"  Profit factor:   {result.profit_factor:.2f}")

        # Daily P&L breakdown
        if result.daily_pnl:
            print(f"\n  {'-'*60}")
            print(f"  DAILY P&L")
            print(f"  {'-'*60}")
            for date, pnl in sorted(result.daily_pnl.items()):
                print(f"  {date}: {pnl:+.2f}")

        print("\n" + "=" * 70)


def fetch_and_backtest(
    dhan_client=None,
    from_date: str = "",
    to_date: str = "",
    quantity: int = 75,
) -> BacktestResult:
    """Fetch historical data from Dhan and run backtest.

    Args:
        dhan_client: Initialized DhanClient (optional, can fetch from config)
        from_date: Start date YYYY-MM-DD
        to_date: End date YYYY-MM-DD
        quantity: Lot size for P&L calculation

    Returns:
        BacktestResult
    """
    if dhan_client is None:
        from dhan_client import DhanClient
        dhan_client = DhanClient(paper_trading=True)

    if not from_date:
        from_date = (datetime.now(IST) - timedelta(days=30)).strftime("%Y-%m-%d")
    if not to_date:
        to_date = datetime.now(IST).strftime("%Y-%m-%d")

    logger.info("Fetching 5-min data from %s to %s", from_date, to_date)

    response = dhan_client.client.intraday_minute_data(
        security_id=config.SECURITY_ID,
        exchange_segment=config.EXCHANGE_SEGMENT,
        instrument_type="INDEX",
        from_date=f"{from_date} 09:15:00",
        to_date=f"{to_date} 15:30:00",
        interval=5,
        oi=False,
    )

    if response.get("status") != "success":
        logger.error("Failed to fetch data: %s", response.get("remarks"))
        return BacktestResult()

    data = response["data"]
    df = pd.DataFrame(data)

    if df.empty:
        logger.error("No data returned")
        return BacktestResult()

    # Convert timestamps
    df["timestamp"] = df["timestamp"].apply(
        lambda ts: datetime.fromtimestamp(ts, tz=pytz.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    )
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

    lot_size = dhan_client.get_lot_size()

    backtester = Backtester(quantity=lot_size)
    result = backtester.run(df, start_date=from_date, end_date=to_date)

    backtester.print_report(result)
    backtester.save_trades_csv(result, f"backtest_trades_{from_date}_to_{to_date}.csv")

    return result


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Backtest NIFTY EMA 5/20 Crossover Strategy")
    parser.add_argument("--from-date", type=str, default="", help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", type=str, default="", help="End date YYYY-MM-DD")
    parser.add_argument("--quantity", type=int, default=75, help="Lot size")
    args = parser.parse_args()

    result = fetch_and_backtest(
        from_date=args.from_date,
        to_date=args.to_date,
        quantity=args.quantity,
    )
