"""Dhan order execution and management."""

from __future__ import annotations

import logging
import time
from typing import Any

import config

logger = logging.getLogger(__name__)

# Dhan SDK constants
NQE_FNO = "NSE_FNO"
NQE_EQ = "NSE_EQ"
BUY = "BUY"
SELL = "SELL"
LIMIT = "LIMIT"
MARKET = "MARKET"
SL = "STOP_LOSS"
SLM = "STOP_LOSS_MARKET"
INTRA = "INTRADAY"
DAY = "DAY"


class OrderManager:
    """Handles all order placement, modification, and cancellation via Dhan API.

    For options the strategy always BUYS the ATM option to open and SELLS it to
    close (long premium). The traded security_id is resolved per entry and passed
    explicitly to each order call.
    """

    def __init__(self, dhan_client, paper_trading: bool = True):
        self.dhan = dhan_client
        self.paper_trading = paper_trading
        self._order_counter = 0
        # Default exchange segment for trading (NSE_FNO for options/futures)
        self._exchange_segment = self.dhan.get_trading_exchange() or config.OPTION_EXCHANGE_SEGMENT

    def _next_tag(self, prefix: str = "EMA") -> str:
        """Generate a unique order tag."""
        self._order_counter += 1
        return f"{prefix}_{int(time.time())}_{self._order_counter}"

    def _txn_for_open(self, direction: str) -> str:
        """Transaction type when opening a position.

        Direction: "LONG" -> ATM call (buy), "SHORT" -> ATM put (buy).
        Both are bought, so we always BUY to open.
        """
        return BUY

    def _txn_for_close(self, direction: str) -> str:
        """Transaction type when closing a position.

        Both calls and puts are sold to close (long premium).
        """
        return SELL

    def place_entry_order(
        self,
        direction: str,
        quantity: int,
        price: float,
        security_id: str | None = None,
        exchange_segment: str | None = None,
        product_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Place a limit entry order (BUY the ATM call/put).

        Args:
            direction: "LONG" (ATM call) or "SHORT" (ATM put)
            quantity: Number of contracts
            price: Limit price (option premium)
            security_id: The option security to buy (resolved per entry)
            exchange_segment: Override exchange segment
            product_type: Override product type

        Returns:
            Order response dict or None if failed/paper.
        """
        segment = exchange_segment or self._exchange_segment
        product = product_type or config.PRODUCT_TYPE
        sec_id = security_id or self.dhan.get_trading_security_id()
        txn_type = self._txn_for_open(direction)
        tag = self._next_tag(f"ENTRY_{direction}")

        logger.info(
            "Placing %s %s order: qty=%d price=%.2f segment=%s product=%s tag=%s security_id=%s",
            direction, txn_type, quantity, price, segment, product, tag, sec_id
        )

        if self.paper_trading:
            logger.info("[PAPER] Would place %s %s @ %.2f x %d (security_id=%s)",
                        txn_type, config.INSTRUMENT, price, quantity, sec_id)
            return {
                "status": "success",
                "data": {
                    "orderId": f"PAPER_{tag}",
                    "orderStatus": "PAPER_TRADE",
                    "tag": tag,
                }
            }

        try:
            response = self.dhan.client.place_order(
                security_id=sec_id,
                exchange_segment=segment,
                transaction_type=txn_type,
                quantity=quantity,
                order_type=LIMIT,
                product_type=product,
                price=price,
                validity=DAY,
                tag=tag,
            )

            if response.get("status") == "success":
                order_id = response["data"].get("orderId")
                logger.info("Entry order placed: orderId=%s tag=%s", order_id, tag)
                return response
            else:
                logger.error("Entry order failed: %s", response.get("remarks"))
                return None

        except Exception as e:
            logger.error("Exception placing entry order: %s", e)
            return None

    def place_exit_order(
        self,
        direction: str,
        quantity: int,
        price: float,
        reason: str = "EXIT",
        security_id: str | None = None,
        exchange_segment: str | None = None,
        product_type: str | None = None,
        order_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Place a limit exit order (SELL the ATM call/put to close).

        Args:
            direction: Current position "LONG" (call) or "SHORT" (put)
            quantity: Number of contracts to exit
            price: Limit price for exit
            reason: Exit reason for logging
            security_id: The option security to sell
            exchange_segment: Override exchange segment
            product_type: Override product type
            order_type: LIMIT (default) or MARKET. For MARKET exits a real
                        fill price is obtained; price is ignored for the order.

        Returns:
            Order response dict or None if failed/paper.
        """
        segment = exchange_segment or self._exchange_segment
        product = product_type or config.PRODUCT_TYPE
        sec_id = security_id or self.dhan.get_trading_security_id()
        txn_type = self._txn_for_close(direction)
        tag = self._next_tag(f"EXIT_{reason}")
        otype = order_type or LIMIT

        logger.info(
            "Placing %s exit order (%s): qty=%d price=%.2f type=%s tag=%s security_id=%s",
            direction, reason, quantity, price, otype, tag, sec_id
        )

        if self.paper_trading:
            logger.info("[PAPER] Would place %s %s @ %.2f x %d (reason=%s)",
                        txn_type, config.INSTRUMENT, price, quantity, reason)
            return {
                "status": "success",
                "data": {
                    "orderId": f"PAPER_{tag}",
                    "orderStatus": "PAPER_TRADE",
                    "tag": tag,
                }
            }

        try:
            response = self.dhan.client.place_order(
                security_id=sec_id,
                exchange_segment=segment,
                transaction_type=txn_type,
                quantity=quantity,
                order_type=otype,
                product_type=product,
                price=price,
                validity=DAY,
                tag=tag,
            )

            if response.get("status") == "success":
                order_id = response["data"].get("orderId")
                logger.info("Exit order placed: orderId=%s reason=%s", order_id, reason)
                return response
            else:
                logger.error("Exit order failed: %s", response.get("remarks"))
                return None

        except Exception as e:
            logger.error("Exception placing exit order: %s", e)
            return None

    def place_sl_order(
        self,
        position_direction: str,
        quantity: int,
        sl_price: float,
        security_id: str | None = None,
        exchange_segment: str | None = None,
        product_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Place a stop-loss order (SELL to close long option below entry).

        Args:
            position_direction: "LONG" or "SHORT" (both close = SELL)
            quantity: Number of contracts
            sl_price: Trigger price for SL (option premium)
            security_id: The option security
            exchange_segment: Override
            product_type: Override

        Returns:
            Order response dict or None.
        """
        segment = exchange_segment or self._exchange_segment
        product = product_type or config.PRODUCT_TYPE
        sec_id = security_id or self.dhan.get_trading_security_id()
        txn_type = SELL  # closing a long option
        tag = self._next_tag("SL")

        logger.info(
            "Placing SL order: %s %s @ trigger=%.2f qty=%d tag=%s security_id=%s",
            position_direction, txn_type, sl_price, quantity, tag, sec_id
        )

        if self.paper_trading:
            logger.info("[PAPER] Would place SL %s @ trigger=%.2f x %d", txn_type, sl_price, quantity)
            return {
                "status": "success",
                "data": {
                    "orderId": f"PAPER_{tag}",
                    "orderStatus": "PAPER_TRADE",
                    "tag": tag,
                }
            }

        try:
            response = self.dhan.client.place_order(
                security_id=sec_id,
                exchange_segment=segment,
                transaction_type=txn_type,
                quantity=quantity,
                order_type=SLM,  # Stop-loss market
                product_type=product,
                price=0,  # Market execution
                trigger_price=sl_price,
                validity=DAY,
                tag=tag,
            )

            if response.get("status") == "success":
                order_id = response["data"].get("orderId")
                logger.info("SL order placed: orderId=%s", order_id)
                return response
            else:
                logger.error("SL order failed: %s", response.get("remarks"))
                return None

        except Exception as e:
            logger.error("Exception placing SL order: %s", e)
            return None

    def place_target_order(
        self,
        position_direction: str,
        quantity: int,
        target_price: float,
        security_id: str | None = None,
        exchange_segment: str | None = None,
        product_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Place a target (take-profit) limit order (SELL to close above entry).

        Args:
            position_direction: "LONG" or "SHORT" (both close = SELL)
            quantity: Number of contracts
            target_price: Limit price for target (option premium)
            security_id: The option security
            exchange_segment: Override
            product_type: Override

        Returns:
            Order response dict or None.
        """
        segment = exchange_segment or self._exchange_segment
        product = product_type or config.PRODUCT_TYPE
        sec_id = security_id or self.dhan.get_trading_security_id()
        txn_type = SELL  # closing a long option
        tag = self._next_tag("TARGET")

        logger.info(
            "Placing target order: %s %s @ %.2f qty=%d tag=%s security_id=%s",
            position_direction, txn_type, target_price, quantity, tag, sec_id
        )

        if self.paper_trading:
            logger.info("[PAPER] Would place target %s @ %.2f x %d", txn_type, target_price, quantity)
            return {
                "status": "success",
                "data": {
                    "orderId": f"PAPER_{tag}",
                    "orderStatus": "PAPER_TRADE",
                    "tag": tag,
                }
            }

        try:
            response = self.dhan.client.place_order(
                security_id=sec_id,
                exchange_segment=segment,
                transaction_type=txn_type,
                quantity=quantity,
                order_type=LIMIT,
                product_type=product,
                price=target_price,
                validity=DAY,
                tag=tag,
            )

            if response.get("status") == "success":
                order_id = response["data"].get("orderId")
                logger.info("Target order placed: orderId=%s", order_id)
                return response
            else:
                logger.error("Target order failed: %s", response.get("remarks"))
                return None

        except Exception as e:
            logger.error("Exception placing target order: %s", e)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        if self.paper_trading:
            logger.info("[PAPER] Would cancel order %s", order_id)
            return True

        try:
            response = self.dhan.client.cancel_order(order_id=order_id)
            if response.get("status") == "success":
                logger.info("Order cancelled: %s", order_id)
                return True
            else:
                logger.error("Cancel failed for %s: %s", order_id, response.get("remarks"))
                return False
        except Exception as e:
            logger.error("Exception cancelling order %s: %s", order_id, e)
            return False

    def cancel_all_strategy_orders(self) -> None:
        """Cancel all orders tagged as strategy orders."""
        if self.paper_trading:
            logger.info("[PAPER] Would cancel all strategy orders")
            return

        try:
            response = self.dhan.client.get_order_list()
            if response.get("status") == "success":
                orders = response["data"]
                for order in orders:
                    tag = order.get("tag", "")
                    status = order.get("orderStatus", "")
                    if tag.startswith("EMA_") and status in ("PENDING", "PLACED", "PART_TRADED"):
                        self.cancel_order(str(order["orderId"]))
                        time.sleep(0.1)  # Rate limit
        except Exception as e:
            logger.error("Exception cancelling strategy orders: %s", e)

    def get_order_status(self, order_id: str) -> dict[str, Any] | None:
        """Get order status by order ID."""
        if self.paper_trading:
            return {
                "orderId": order_id,
                "orderStatus": "PAPER_TRADE",
                "filledQty": 0,
                "avgPrice": 0,
            }

        try:
            response = self.dhan.client.get_order_by_id(order_id)
            if response.get("status") == "success":
                return response["data"]
            else:
                logger.error("Failed to get order status for %s: %s", order_id, response.get("remarks"))
                return None
        except Exception as e:
            logger.error("Exception getting order status for %s: %s", order_id, e)
            return None

    def wait_for_fill(self, order_id: str, timeout: float = 30.0, poll_interval: float = 1.0) -> dict[str, Any] | None:
        """Wait for an order to be filled or reach a terminal state.

        Args:
            order_id: Order ID to monitor
            timeout: Maximum wait time in seconds
            poll_interval: Time between status checks

        Returns:
            Final order data or None if timeout.
        """
        if self.paper_trading:
            return {
                "orderId": order_id,
                "orderStatus": "PAPER_TRADE",
                "filledQty": config.QUANTITY * self.dhan.get_lot_size(),
                "avgPrice": 0,
            }

        start = time.time()
        while time.time() - start < timeout:
            order_data = self.get_order_status(order_id)
            if order_data is None:
                time.sleep(poll_interval)
                continue

            status = order_data.get("orderStatus", "")
            if status in ("TRADED", "CANCELLED", "REJECTED", "EXPIRED"):
                logger.info("Order %s reached terminal state: %s", order_id, status)
                return order_data

            time.sleep(poll_interval)

        logger.warning("Timeout waiting for order %s fill", order_id)
        return None

    def get_positions(self, security_id: str | None = None) -> list[dict[str, Any]]:
        """Get current positions from Dhan.

        Args:
            security_id: Optional filter; if given, only positions for this
                         security are returned. Otherwise returns all open positions.
        """
        if self.paper_trading:
            return []

        try:
            response = self.dhan.client.get_positions()
            if response.get("status") == "success":
                positions = response["data"]
                open_positions = [p for p in positions if p.get("netQty", 0) != 0]
                if security_id:
                    open_positions = [
                        p for p in open_positions
                        if str(p.get("securityId", "")) == str(security_id)
                    ]
                return open_positions
            return []
        except Exception as e:
            logger.error("Exception getting positions: %s", e)
            return []

    def get_today_trades(self) -> list[dict[str, Any]]:
        """Get today's trade history."""
        if self.paper_trading:
            return []

        try:
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            response = self.dhan.client.get_trade_history(today, today, page_number=0)
            if response.get("status") == "success":
                return response["data"] or []
            return []
        except Exception as e:
            logger.error("Exception getting trade history: %s", e)
            return []
