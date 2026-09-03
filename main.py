"""Main trading loop for NIFTY 50 EMA 5/20 Crossover Strategy."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime

import pytz

import config
from dhan_client import DhanClient
from market_data import MarketData
from strategy import Strategy, Signal
from order_manager import OrderManager
from risk_manager import RiskManager
from state_manager import StateManager
from trade_logger import TradeLogger
from excel_logger import ExcelLogger

IST = pytz.timezone("Asia/Kolkata")


def setup_logging() -> logging.Logger:
    """Configure logging to both file and console."""
    os.makedirs(config.LOG_DIR, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)s - %(message)s",
        datefmt="%H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logging.getLogger(__name__)


def emergency_square_off(order_manager: OrderManager, risk_manager: RiskManager) -> None:
    """Emergency function: close all positions and cancel all orders."""
    logger = logging.getLogger(__name__)
    logger.critical("EMERGENCY SQUARE-OFF INITIATED")

    # Cancel all pending orders
    order_manager.cancel_all_strategy_orders()

    # Close any open position
    if risk_manager.position.is_open:
        ltp = order_manager.dhan.get_current_ltp()
        if ltp:
            exit_price = ltp
        else:
            exit_price = risk_manager.position.entry_price

        response = order_manager.place_exit_order(
            direction=risk_manager.position.direction,
            quantity=risk_manager.position.quantity,
            price=exit_price,
            reason="EMERGENCY",
        )

        if response and response.get("status") == "success":
            if order_manager.paper_trading:
                logger.critical("[PAPER] Emergency square-off placed")
            else:
                order_id = response["data"].get("orderId")
                order_manager.wait_for_fill(order_id, timeout=60)

        risk_manager.position.reset()

    # Verify flat
    positions = order_manager.get_positions()
    if not positions:
        logger.critical("Account confirmed FLAT after emergency square-off")
    else:
        logger.critical("WARNING: Open positions remain after emergency square-off: %s", positions)


def run_strategy():
    """Main strategy execution loop."""
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("NIFTY 50 EMA 5/20 CROSSOVER STRATEGY — STARTING")
    logger.info("Mode: %s", "PAPER TRADING" if config.PAPER_TRADING else "LIVE TRADING")
    logger.info("=" * 60)

    # Initialize components
    dhan_client = DhanClient(paper_trading=config.PAPER_TRADING)
    market_data = MarketData(dhan_client)
    strategy = Strategy()
    order_manager = OrderManager(dhan_client, paper_trading=config.PAPER_TRADING)
    risk_manager = RiskManager()
    state_mgr = StateManager()
    trade_log = TradeLogger()
    excel_logger = ExcelLogger()

    # Resolve lot size
    lot_size = dhan_client.get_lot_size()
    logger.info("Lot size: %d", lot_size)

    # Load and reconcile state on startup
    saved_state = state_mgr.load_state()
    broker_positions = order_manager.get_positions()
    if saved_state:
        reconciled = state_mgr.reconcile_with_broker(
            saved_state, broker_positions,
            trading_security_id=dhan_client.get_trading_security_id()
        )
        risk_manager.consecutive_sl_count = reconciled.get("consecutive_sl_count", 0)
        risk_manager.daily_trade_count = reconciled.get("daily_trade_count", 0)
        risk_manager.session_active = reconciled.get("session_active", True)
        risk_manager._today_date = datetime.now(IST).strftime("%Y-%m-%d")

        if reconciled.get("direction") is not None:
            risk_manager.position.direction = reconciled["direction"]
            risk_manager.position.entry_price = reconciled.get("entry_price", 0)
            risk_manager.position.quantity = reconciled.get("quantity", 0)
            risk_manager.position.entry_time = reconciled.get("entry_time", "")
            risk_manager.position.sl_price = reconciled.get("sl_price", 0)
            risk_manager.position.target_price = reconciled.get("target_price", 0)
            risk_manager.position.entry_order_id = reconciled.get("entry_order_id", "")
            risk_manager.position.sl_order_id = reconciled.get("sl_order_id", "")
            risk_manager.position.target_order_id = reconciled.get("target_order_id", "")
            risk_manager.position.ema_fast_at_entry = reconciled.get("ema_fast_at_entry", 0)
            risk_manager.position.ema_slow_at_entry = reconciled.get("ema_slow_at_entry", 0)
            risk_manager.position.security_id = reconciled.get("security_id", "")
            risk_manager.position.option_type = reconciled.get("option_type", "")
            risk_manager.position.strike = reconciled.get("strike", 0)
            risk_manager.position.symbol = reconciled.get("symbol", "")
            risk_manager.position.expiry = reconciled.get("expiry", "")

        logger.info(
            "State recovered: direction=%s sl_count=%d session=%s",
            risk_manager.position.direction,
            risk_manager.consecutive_sl_count,
            risk_manager.session_active,
        )

    # Fetch historical candles for EMA warm-up
    logger.info("Fetching historical candles for EMA warm-up...")
    candles = market_data.fetch_historical_candles(days=5)
    if candles.empty:
        logger.error("Failed to fetch historical candles. Exiting.")
        return

    df = market_data.compute_emas()
    if df.empty or len(df) < config.SLOW_EMA:
        logger.error("Insufficient data for EMA calculation. Exiting.")
        return

    logger.info("EMA warm-up complete: %d candles loaded", len(df))

    # Track last processed candle to avoid duplicates
    last_processed_ts = None
    if saved_state and saved_state.get("last_processed_candle_ts"):
        last_processed_ts = saved_state["last_processed_candle_ts"]
        market_data._last_candle_ts = last_processed_ts

    # Main loop
    logger.info("Entering main trading loop...")
    logger.info("Trading hours: %s to %s IST", config.START_TIME, config.EXIT_TIME)

    try:
        while True:
            now = datetime.now(IST)
            today_str = now.strftime("%Y-%m-%d")

            # New day reset
            risk_manager.new_day()

            # Check if market is open
            market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
            market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

            if now < market_open:
                logger.info("Market not open yet. Waiting... (opens 09:15)")
                time.sleep(30)
                continue

            if now > market_close:
                logger.info("Market closed. Stopping for the day.")
                break

            # EOD square-off at EXIT_TIME
            exit_time = now.replace(
                hour=int(config.EXIT_TIME.split(":")[0]),
                minute=int(config.EXIT_TIME.split(":")[1]),
                second=0, microsecond=0
            )
            if now >= exit_time and risk_manager.position.is_open:
                logger.info("EXIT_TIME reached — squaring off open position")
                _handle_exit(
                    order_manager, risk_manager, market_data, strategy,
                    trade_log, excel_logger, reason="EOD_EXIT"
                )
                order_manager.cancel_all_strategy_orders()
                risk_manager.session_active = False
                logger.info("Trading session completed for the day")
                time.sleep(60)
                continue

            # Not yet in trading hours — collect data but don't trade
            start_time = now.replace(
                hour=int(config.START_TIME.split(":")[0]),
                minute=int(config.START_TIME.split(":")[1]),
                second=0, microsecond=0
            )
            in_trading_hours = now >= start_time

            # Fetch new completed candles
            new_candles = market_data.fetch_latest_candles()

            if not new_candles.empty:
                market_data.update_candles(new_candles)

                for _, candle in new_candles.iterrows():
                    candle_ts = candle["timestamp"]

                    # Skip if already processed
                    if candle_ts == last_processed_ts:
                        continue

                    # Get EMAs for the last two completed candles
                    ema_data = market_data.get_last_two_completed_emas()
                    if ema_data is None:
                        logger.debug("Insufficient EMA data for signal detection")
                        last_processed_ts = candle_ts
                        market_data.mark_candle_processed(candle_ts)
                        continue

                    prev_candle, curr_candle = ema_data

                    # Skip if this candle isn't the latest completed one
                    if curr_candle["timestamp"] != candle_ts:
                        last_processed_ts = candle_ts
                        market_data.mark_candle_processed(candle_ts)
                        continue

                    logger.info(
                        "[%s] EMA5=%.2f EMA20=%.2f Close=%.2f",
                        candle_ts, curr_candle["ema_fast"], curr_candle["ema_slow"], curr_candle["close"]
                    )

                    # Check if SL or target has been hit (using current option premium)
                    if risk_manager.position.is_open:
                        opt_ltp = market_data.get_current_ltp(risk_manager.position.security_id)
                        if opt_ltp is not None:
                            hit = risk_manager.check_sl_target_hit(opt_ltp)
                            if hit == "SL":
                                _handle_sl_hit(
                                    order_manager, risk_manager, market_data,
                                    trade_log, excel_logger, curr_candle, last_processed_ts
                                )
                                last_processed_ts = candle_ts
                                market_data.mark_candle_processed(candle_ts)
                                continue
                            elif hit == "TARGET":
                                _handle_target_hit(
                                    order_manager, risk_manager, market_data,
                                    trade_log, excel_logger, curr_candle, last_processed_ts
                                )
                                last_processed_ts = candle_ts
                                market_data.mark_candle_processed(candle_ts)
                                continue

                    # Check for crossover signals (only during trading hours)
                    if in_trading_hours and risk_manager.session_active:
                        signal = strategy.detect_crossover(prev_candle, curr_candle)

                        if signal.signal == Signal.LONG:
                            if risk_manager.position.direction == "SHORT":
                                # Exit SHORT and enter LONG
                                _handle_exit(
                                    order_manager, risk_manager, market_data,
                                    strategy, trade_log, excel_logger, curr_candle, reason="CROSSOVER"
                                )
                                if risk_manager.can_trade():
                                    _handle_entry(
                                        order_manager, risk_manager, market_data,
                                        strategy, trade_log, "LONG", curr_candle
                                    )
                            elif risk_manager.position.direction is None:
                                # No position — enter LONG
                                if risk_manager.can_trade():
                                    _handle_entry(
                                        order_manager, risk_manager, market_data,
                                        strategy, trade_log, "LONG", curr_candle
                                    )

                        elif signal.signal == Signal.SHORT:
                            if risk_manager.position.direction == "LONG":
                                # Exit LONG and enter SHORT
                                _handle_exit(
                                    order_manager, risk_manager, market_data,
                                    strategy, trade_log, excel_logger, curr_candle, reason="CROSSOVER"
                                )
                                if risk_manager.can_trade():
                                    _handle_entry(
                                        order_manager, risk_manager, market_data,
                                        strategy, trade_log, "SHORT", curr_candle
                                    )
                            elif risk_manager.position.direction is None:
                                # No position — enter SHORT
                                if risk_manager.can_trade():
                                    _handle_entry(
                                        order_manager, risk_manager, market_data,
                                        strategy, trade_log, "SHORT", curr_candle
                                    )

                    # Save state
                    state_mgr.save_state(state_mgr.build_state(
                        direction=risk_manager.position.direction,
                        entry_price=risk_manager.position.entry_price,
                        quantity=risk_manager.position.quantity,
                        entry_time=risk_manager.position.entry_time,
                        sl_price=risk_manager.position.sl_price,
                        target_price=risk_manager.position.target_price,
                        consecutive_sl_count=risk_manager.consecutive_sl_count,
                        daily_trade_count=risk_manager.daily_trade_count,
                        session_active=risk_manager.session_active,
                        last_processed_candle_ts=candle_ts,
                        entry_order_id=risk_manager.position.entry_order_id,
                        sl_order_id=risk_manager.position.sl_order_id,
                        target_order_id=risk_manager.position.target_order_id,
                        ema_fast_at_entry=risk_manager.position.ema_fast_at_entry,
                        ema_slow_at_entry=risk_manager.position.ema_slow_at_entry,
                        security_id=risk_manager.position.security_id,
                        option_type=risk_manager.position.option_type,
                        strike=risk_manager.position.strike,
                        symbol=risk_manager.position.symbol,
                        expiry=risk_manager.position.expiry,
                    ))

                    last_processed_ts = candle_ts
                    market_data.mark_candle_processed(candle_ts)

            time.sleep(config.POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received — shutting down")
    except Exception as e:
        logger.exception("Unexpected error in main loop: %s", e)
    finally:
        # End-of-day cleanup
        logger.info("Running end-of-day cleanup...")
        if risk_manager.position.is_open:
            _handle_exit(
                order_manager, risk_manager, market_data,
                strategy, trade_log, excel_logger, reason="EOD_CLEANUP"
            )
        order_manager.cancel_all_strategy_orders()

        # Print trade summary
        trade_log.print_today_summary()

        # Final state save
        state_mgr.save_state(state_mgr.build_state(
            direction=None,
            entry_price=0,
            quantity=0,
            entry_time="",
            sl_price=0,
            target_price=0,
            consecutive_sl_count=risk_manager.consecutive_sl_count,
            daily_trade_count=risk_manager.daily_trade_count,
            session_active=False,
            last_processed_candle_ts=last_processed_ts,
        ))

        logger.info("Strategy stopped")


def _handle_entry(
    order_manager: OrderManager,
    risk_manager: RiskManager,
    market_data: MarketData,
    strategy: Strategy,
    trade_log: TradeLogger,
    direction: str,
    candle: dict,
) -> None:
    """Handle entering a new option position.

    direction="LONG" buys the ATM call, direction="SHORT" buys the ATM put.
    Entry price is the current option premium (ask / last price).
    """
    logger = logging.getLogger(__name__)

    side = "CE" if direction == "LONG" else "PE"
    try:
        option = market_data.dhan.resolve_atm_option(side=side)
    except Exception as e:
        logger.error("Failed to resolve ATM %s option: %s — not entering", side, e)
        return

    security_id = option["security_id"]
    # Use ask if available, else last price (buy at a realistic price)
    entry_price = option["ask"] or option["last_price"] or candle["close"]
    if entry_price <= 0:
        logger.error("Invalid premium %.2f for %s — not entering", entry_price, side)
        return

    quantity = config.QUANTITY * option["lot_size"]
    sl_price, target_price = strategy.calculate_sl_target(direction, entry_price)

    logger.info(
        "Entering %s (%s %s): premium=%.2f qty=%d SL=%.2f Target=%.2f security_id=%s",
        direction, option["symbol"], side, entry_price, quantity,
        sl_price, target_price, security_id
    )

    response = order_manager.place_entry_order(
        direction=direction,
        quantity=quantity,
        price=entry_price,
        security_id=security_id,
    )

    if response is None:
        logger.error("Entry order failed for %s — not entering", direction)
        return

    order_id = response["data"].get("orderId", "")

    if order_manager.paper_trading:
        # In paper mode, assume immediate fill
        risk_manager.on_entry_filled(
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            entry_order_id=order_id,
            ema_fast=candle["ema_fast"],
            ema_slow=candle["ema_slow"],
            option=option,
        )
    else:
        # Wait for fill
        fill_data = order_manager.wait_for_fill(order_id)
        if fill_data and fill_data.get("orderStatus") == "TRADED":
            avg_price = float(fill_data.get("avgPrice", entry_price))
            filled_qty = int(fill_data.get("filledQty", quantity))

            risk_manager.on_entry_filled(
                direction=direction,
                entry_price=avg_price,
                quantity=filled_qty,
                entry_order_id=order_id,
                ema_fast=candle["ema_fast"],
                ema_slow=candle["ema_slow"],
                option=option,
            )

            # Place SL order
            sl_response = order_manager.place_sl_order(
                position_direction=direction,
                quantity=filled_qty,
                sl_price=risk_manager.position.sl_price,
                security_id=security_id,
            )
            if sl_response:
                risk_manager.position.sl_order_id = sl_response["data"].get("orderId", "")

            # Place target order
            target_response = order_manager.place_target_order(
                position_direction=direction,
                quantity=filled_qty,
                target_price=risk_manager.position.target_price,
                security_id=security_id,
            )
            if target_response:
                risk_manager.position.target_order_id = target_response["data"].get("orderId", "")
        else:
            logger.error("Entry order did not fill for %s", direction)


def _handle_exit(
    order_manager: OrderManager,
    risk_manager: RiskManager,
    market_data: MarketData,
    strategy: Strategy,
    trade_log: TradeLogger,
    excel_logger: ExcelLogger,
    candle: dict | None = None,
    reason: str = "EXIT",
) -> None:
    """Handle exiting the current position."""
    logger = logging.getLogger(__name__)

    if not risk_manager.position.is_open:
        return

    direction = risk_manager.position.direction
    entry_price = risk_manager.position.entry_price
    quantity = risk_manager.position.quantity
    entry_time = risk_manager.position.entry_time
    entry_order_id = risk_manager.position.entry_order_id
    ema_fast_entry = risk_manager.position.ema_fast_at_entry
    ema_slow_entry = risk_manager.position.ema_slow_at_entry
    sl_price = risk_manager.position.sl_price
    target_price = risk_manager.position.target_price

    # For options, exit at the current option premium; for futures/index use
    # the candle close. EMA exit values are only meaningful for the underlying
    # index signal and are captured separately at the call site.
    if config.TRADE_OPTIONS:
        opt_ltp = market_data.get_current_ltp(risk_manager.position.security_id)
        exit_price = opt_ltp or risk_manager.position.entry_price
        if candle:
            ema_fast_exit = candle["ema_fast"]
            ema_slow_exit = candle["ema_slow"]
        else:
            ema_fast_exit = 0
            ema_slow_exit = 0
    else:
        if candle:
            exit_price = candle["close"]
            ema_fast_exit = candle["ema_fast"]
            ema_slow_exit = candle["ema_slow"]
        else:
            exit_price = market_data.get_current_ltp() or entry_price
            ema_fast_exit = 0
            ema_slow_exit = 0

    logger.info(
        "Exiting %s: reason=%s exit_price=%.2f entry=%.2f",
        direction, reason, exit_price, entry_price
    )

    # Cancel SL and target orders
    if risk_manager.position.sl_order_id:
        order_manager.cancel_order(risk_manager.position.sl_order_id)
    if risk_manager.position.target_order_id:
        order_manager.cancel_order(risk_manager.position.target_order_id)

    # Place exit order
    response = order_manager.place_exit_order(
        direction=direction,
        quantity=quantity,
        price=exit_price,
        reason=reason,
        security_id=risk_manager.position.security_id or None,
    )

    if response is None:
        logger.error("Exit order failed for %s", direction)
        return

    order_id = response["data"].get("orderId", "")

    if order_manager.paper_trading:
        actual_exit_price = exit_price
    else:
        fill_data = order_manager.wait_for_fill(order_id)
        if fill_data and fill_data.get("orderStatus") == "TRADED":
            actual_exit_price = float(fill_data.get("avgPrice", exit_price))
        else:
            actual_exit_price = exit_price

    # Calculate P&L. For options the strategy is always long premium, so
    # P&L is always (exit - entry) * qty regardless of call/put side.
    if config.TRADE_OPTIONS:
        gross_pnl = (actual_exit_price - entry_price) * quantity
    elif direction == "LONG":
        gross_pnl = (actual_exit_price - entry_price) * quantity
    else:
        gross_pnl = (entry_price - actual_exit_price) * quantity

    exit_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    # Log the trade to SQLite journal
    trade_log.log_trade(
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=actual_exit_price,
        entry_time=entry_time,
        exit_time=exit_time,
        sl_price=sl_price,
        target_price=target_price,
        exit_reason=reason,
        gross_pnl=gross_pnl,
        consecutive_sl_count=risk_manager.consecutive_sl_count,
        ema_fast_at_entry=ema_fast_entry,
        ema_slow_at_entry=ema_slow_entry,
        ema_fast_at_exit=ema_fast_exit,
        ema_slow_at_exit=ema_slow_exit,
        entry_order_id=entry_order_id,
        exit_order_id=order_id,
        symbol=risk_manager.position.symbol,
        option_type=risk_manager.position.option_type,
        strike=risk_manager.position.strike,
        security_id=risk_manager.position.security_id,
    )

    # Write to Excel in real-time
    excel_logger.append_trade({
        "Date": datetime.now(IST).strftime("%Y-%m-%d"),
        "Entry Time": entry_time,
        "Exit Time": exit_time,
        "Direction": direction,
        "Symbol": risk_manager.position.symbol,
        "Option Type": risk_manager.position.option_type,
        "Strike": round(risk_manager.position.strike, 2),
        "Quantity": quantity,
        "Entry Price": round(entry_price, 2),
        "Exit Price": round(actual_exit_price, 2),
        "SL Price": round(sl_price, 2),
        "Target Price": round(target_price, 2),
        "Exit Reason": reason,
        "Gross P&L": round(gross_pnl, 2),
        "Net P&L": round(gross_pnl, 2),
        "Consecutive SL": risk_manager.consecutive_sl_count,
        "EMA5 Entry": round(ema_fast_entry, 2),
        "EMA20 Entry": round(ema_slow_entry, 2),
        "EMA5 Exit": round(ema_fast_exit, 2),
        "EMA20 Exit": round(ema_slow_exit, 2),
        "Entry Order ID": entry_order_id,
        "Exit Order ID": order_id,
        "Security ID": risk_manager.position.security_id,
    })

    # Mutate risk manager state LAST (after capturing all trade data)
    if reason == "SL":
        risk_manager.on_sl_hit()
    elif reason == "TARGET":
        risk_manager.on_target_hit()
    elif reason == "CROSSOVER":
        risk_manager.on_crossover_exit()
    else:
        risk_manager.position.reset()


def _handle_sl_hit(
    order_manager: OrderManager,
    risk_manager: RiskManager,
    market_data: MarketData,
    trade_log: TradeLogger,
    excel_logger: ExcelLogger,
    candle: dict,
    last_processed_ts: str | None,
) -> None:
    """Handle SL hit based on candle price checking SL level."""
    logger = logging.getLogger(__name__)

    direction = risk_manager.position.direction
    entry_price = risk_manager.position.entry_price
    quantity = risk_manager.position.quantity
    sl_price = risk_manager.position.sl_price
    entry_time = risk_manager.position.entry_time
    entry_order_id = risk_manager.position.entry_order_id
    ema_fast_entry = risk_manager.position.ema_fast_at_entry
    ema_slow_entry = risk_manager.position.ema_slow_at_entry
    target_price = risk_manager.position.target_price

    logger.warning("SL HIT DETECTED: %s entry=%.2f SL=%.2f close=%.2f",
                    direction, entry_price, sl_price, candle["close"])

    exit_price = sl_price  # Assume SL execution at trigger price
    exit_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    # Cancel target order
    if risk_manager.position.target_order_id:
        order_manager.cancel_order(risk_manager.position.target_order_id)

    # Calculate P&L (long premium for options)
    if config.TRADE_OPTIONS:
        gross_pnl = (exit_price - entry_price) * quantity
    elif direction == "LONG":
        gross_pnl = (exit_price - entry_price) * quantity
    else:
        gross_pnl = (entry_price - exit_price) * quantity

    # Log trade to SQLite journal
    trade_log.log_trade(
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_time=entry_time,
        exit_time=exit_time,
        sl_price=sl_price,
        target_price=target_price,
        exit_reason="SL",
        gross_pnl=gross_pnl,
        consecutive_sl_count=risk_manager.consecutive_sl_count,
        ema_fast_at_entry=ema_fast_entry,
        ema_slow_at_entry=ema_slow_entry,
        ema_fast_at_exit=candle.get("ema_fast", 0),
        ema_slow_at_exit=candle.get("ema_slow", 0),
        entry_order_id=entry_order_id,
        exit_order_id="",
        symbol=risk_manager.position.symbol,
        option_type=risk_manager.position.option_type,
        strike=risk_manager.position.strike,
        security_id=risk_manager.position.security_id,
    )

    # Write to Excel in real-time
    excel_logger.append_trade({
        "Date": datetime.now(IST).strftime("%Y-%m-%d"),
        "Entry Time": entry_time,
        "Exit Time": exit_time,
        "Direction": direction,
        "Symbol": risk_manager.position.symbol,
        "Option Type": risk_manager.position.option_type,
        "Strike": round(risk_manager.position.strike, 2),
        "Quantity": quantity,
        "Entry Price": round(entry_price, 2),
        "Exit Price": round(exit_price, 2),
        "SL Price": round(sl_price, 2),
        "Target Price": round(target_price, 2),
        "Exit Reason": "SL",
        "Gross P&L": round(gross_pnl, 2),
        "Net P&L": round(gross_pnl, 2),
        "Consecutive SL": risk_manager.consecutive_sl_count,
        "EMA5 Entry": round(ema_fast_entry, 2),
        "EMA20 Entry": round(ema_slow_entry, 2),
        "EMA5 Exit": round(candle.get("ema_fast", 0), 2),
        "EMA20 Exit": round(candle.get("ema_slow", 0), 2),
        "Entry Order ID": entry_order_id,
        "Exit Order ID": "",
        "Security ID": risk_manager.position.security_id,
    })

    risk_manager.on_sl_hit()


def _handle_target_hit(
    order_manager: OrderManager,
    risk_manager: RiskManager,
    market_data: MarketData,
    trade_log: TradeLogger,
    excel_logger: ExcelLogger,
    candle: dict,
    last_processed_ts: str | None,
) -> None:
    """Handle target hit based on candle price reaching target level."""
    logger = logging.getLogger(__name__)

    direction = risk_manager.position.direction
    entry_price = risk_manager.position.entry_price
    quantity = risk_manager.position.quantity
    target_price = risk_manager.position.target_price
    entry_time = risk_manager.position.entry_time
    entry_order_id = risk_manager.position.entry_order_id
    ema_fast_entry = risk_manager.position.ema_fast_at_entry
    ema_slow_entry = risk_manager.position.ema_slow_at_entry
    sl_price = risk_manager.position.sl_price

    logger.info("TARGET HIT DETECTED: %s entry=%.2f Target=%.2f close=%.2f",
                direction, entry_price, target_price, candle["close"])

    exit_price = target_price  # Assume target execution at trigger price
    exit_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    # Cancel SL order
    if risk_manager.position.sl_order_id:
        order_manager.cancel_order(risk_manager.position.sl_order_id)

    # Calculate P&L (long premium for options)
    if config.TRADE_OPTIONS:
        gross_pnl = (exit_price - entry_price) * quantity
    elif direction == "LONG":
        gross_pnl = (exit_price - entry_price) * quantity
    else:
        gross_pnl = (entry_price - exit_price) * quantity

    # Log trade to SQLite journal
    trade_log.log_trade(
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_time=entry_time,
        exit_time=exit_time,
        sl_price=sl_price,
        target_price=target_price,
        exit_reason="TARGET",
        gross_pnl=gross_pnl,
        consecutive_sl_count=risk_manager.consecutive_sl_count,
        ema_fast_at_entry=ema_fast_entry,
        ema_slow_at_entry=ema_slow_entry,
        ema_fast_at_exit=candle.get("ema_fast", 0),
        ema_slow_at_exit=candle.get("ema_slow", 0),
        entry_order_id=entry_order_id,
        exit_order_id="",
        symbol=risk_manager.position.symbol,
        option_type=risk_manager.position.option_type,
        strike=risk_manager.position.strike,
        security_id=risk_manager.position.security_id,
    )

    # Write to Excel in real-time
    excel_logger.append_trade({
        "Date": datetime.now(IST).strftime("%Y-%m-%d"),
        "Entry Time": entry_time,
        "Exit Time": exit_time,
        "Direction": direction,
        "Symbol": risk_manager.position.symbol,
        "Option Type": risk_manager.position.option_type,
        "Strike": round(risk_manager.position.strike, 2),
        "Quantity": quantity,
        "Entry Price": round(entry_price, 2),
        "Exit Price": round(exit_price, 2),
        "SL Price": round(sl_price, 2),
        "Target Price": round(target_price, 2),
        "Exit Reason": "TARGET",
        "Gross P&L": round(gross_pnl, 2),
        "Net P&L": round(gross_pnl, 2),
        "Consecutive SL": risk_manager.consecutive_sl_count,
        "EMA5 Entry": round(ema_fast_entry, 2),
        "EMA20 Entry": round(ema_slow_entry, 2),
        "EMA5 Exit": round(candle.get("ema_fast", 0), 2),
        "EMA20 Exit": round(candle.get("ema_slow", 0), 2),
        "Entry Order ID": entry_order_id,
        "Exit Order ID": "",
        "Security ID": risk_manager.position.security_id,
    })

    risk_manager.on_target_hit()


if __name__ == "__main__":
    run_strategy()
