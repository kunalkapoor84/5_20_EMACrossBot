"""Telegram notification module for trade alerts and daily summary.

Uses urllib (stdlib) to call the Telegram Bot HTTP API — no extra dependencies.
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment variables.

Send a test message on import to verify credentials:
    python telegram_notifier.py
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime

import pytz

import config

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """Sends trade notifications and files to a Telegram chat via Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self.bot_token and self.chat_id)
        if not self._enabled:
            logger.warning(
                "Telegram notifications disabled — set TELEGRAM_BOT_TOKEN "
                "and TELEGRAM_CHAT_ID environment variables to enable"
            )

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------
    def _send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a text message. Returns True on success."""
        if not self._enabled:
            return False

        url = f"{TELEGRAM_API}/bot{self.bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    return True
                logger.error("Telegram sendMessage error: %s", data)
                return False
        except Exception as e:
            logger.error("Telegram sendMessage failed: %s", e)
            return False

    def _send_document(self, file_path: str, caption: str = "") -> bool:
        """Send a file (document) to the chat. Returns True on success."""
        if not self._enabled:
            return False

        import mimetypes

        url = f"{TELEGRAM_API}/bot{self.bot_token}/sendDocument"
        boundary = "----PythonBoundary"

        body = b""
        # chat_id field
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        body += f"{self.chat_id}\r\n".encode()

        # caption field
        if caption:
            body += f"--{boundary}\r\n".encode()
            body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
            body += f"{caption}\r\n".encode()

        # parse_mode
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'
        body += b"HTML\r\n"

        # document file
        filename = os.path.basename(file_path)
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            .encode()
        )
        body += f"Content-Type: {mime_type}\r\n\r\n".encode()
        with open(file_path, "rb") as f:
            body += f.read()
        body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()

        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    return True
                logger.error("Telegram sendDocument error: %s", data)
                return False
        except Exception as e:
            logger.error("Telegram sendDocument failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Trade notifications
    # ------------------------------------------------------------------
    def send_entry_alert(
        self,
        direction: str,
        symbol: str,
        option_type: str,
        strike: float,
        entry_price: float,
        quantity: int,
        sl_price: float,
        target_price: float,
        mode: str = "PAPER",
    ) -> bool:
        """Send a trade entry notification."""
        emoji = "\U0001f7e2" if direction == "LONG" else "\U0001f534"
        side_label = "Call (CE)" if option_type == "CE" else "Put (PE)"
        now = datetime.now(IST).strftime("%H:%M:%S")

        text = (
            f"{emoji} <b>TRADE ENTRY</b> [{mode}]\n\n"
            f"<b>Direction:</b> {direction}\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Option:</b> {side_label} | Strike: {strike:.0f}\n"
            f"<b>Entry Price:</b> {entry_price:.2f}\n"
            f"<b>Quantity:</b> {quantity}\n"
            f"<b>Stop Loss:</b> {sl_price:.2f}\n"
            f"<b>Target:</b> {target_price:.2f}\n\n"
            f"<i>Time: {now} IST</i>"
        )
        return self._send_message(text)

    def send_exit_alert(
        self,
        direction: str,
        symbol: str,
        option_type: str,
        strike: float,
        entry_price: float,
        exit_price: float,
        quantity: int,
        gross_pnl: float,
        reason: str,
        sl_count: int = 0,
        mode: str = "PAPER",
    ) -> bool:
        """Send a trade exit notification."""
        pnl_emoji = "\U0001f4b0" if gross_pnl >= 0 else "\U0001f4b8"
        reason_emoji = {
            "TARGET": "\U0001f3af",
            "SL": "\U0001f6ab",
            "CROSSOVER": "\U0001f504",
            "EOD_EXIT": "\U0001f305",
            "EOD_CLEANUP": "\U0001f305",
        }.get(reason, "\u2753")

        now = datetime.now(IST).strftime("%H:%M:%S")

        text = (
            f"<b>TRADE EXIT</b> [{mode}]\n\n"
            f"<b>Direction:</b> {direction}\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Strike:</b> {strike:.0f}\n"
            f"<b>Entry:</b> {entry_price:.2f} → <b>Exit:</b> {exit_price:.2f}\n"
            f"<b>Quantity:</b> {quantity}\n\n"
            f"{pnl_emoji} <b>P&amp;L: {gross_pnl:+.2f}</b>\n"
            f"{reason_emoji} <b>Reason:</b> {reason}\n"
            f"Consecutive SLs: {sl_count}/{config.MAX_CONSECUTIVE_SL}\n\n"
            f"<i>Time: {now} IST</i>"
        )
        return self._send_message(text)

    def send_startup_alert(self, mode: str = "PAPER") -> bool:
        """Send a strategy startup notification."""
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        text = (
            f"\u26a1 <b>Strategy Started</b>\n\n"
            f"<b>Mode:</b> {mode}\n"
            f"<b>Instrument:</b> NIFTY 50 EMA 5/20\n"
            f"<b>Trading:</b> ATM Options\n"
            f"<b>SL/Target:</b> {config.STOP_LOSS_POINTS}/{config.TARGET_POINTS} pts\n"
            f"<b>Qty:</b> {config.QUANTITY} lot(s)\n\n"
            f"<i>{now} IST</i>"
        )
        return self._send_message(text)

    def send_shutdown_alert(
        self, summary: dict | None = None, mode: str = "PAPER"
    ) -> bool:
        """Send a strategy shutdown notification with daily summary."""
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        if summary:
            text = (
                f"\U0001f6d1 <b>Strategy Stopped</b> [{mode}]\n\n"
                f"<b>Date:</b> {now}\n"
                f"<b>Total Trades:</b> {summary.get('total_trades', 0)}\n"
                f"<b>Wins:</b> {summary.get('wins', 0)} | "
                f"<b>Losses:</b> {summary.get('losses', 0)}\n"
                f"<b>Win Rate:</b> {summary.get('win_rate', 0):.1f}%\n"
                f"<b>Total P&amp;L:</b> {summary.get('total_pnl', 0):+.2f}\n"
            )
        else:
            text = (
                f"\U0001f6d1 <b>Strategy Stopped</b> [{mode}]\n\n"
                f"<i>{now} IST</i>"
            )
        return self._send_message(text)

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------
    def send_daily_summary_excel(
        self, excel_path: str, date_str: str | None = None
    ) -> bool:
        """Send the daily summary Excel file to the chat."""
        if not os.path.exists(excel_path):
            logger.warning("Excel file not found: %s", excel_path)
            return False

        date_label = date_str or datetime.now(IST).strftime("%Y-%m-%d")
        caption = (
            f"\U0001f4ca <b>Daily Summary — {date_label}</b>\n\n"
            f"Attached: {os.path.basename(excel_path)}"
        )
        return self._send_document(excel_path, caption=caption)

    # ------------------------------------------------------------------
    # Direct helper to build and send exit alert from position data
    # ------------------------------------------------------------------
    def send_exit_from_position(
        self,
        direction: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        gross_pnl: float,
        reason: str,
        sl_count: int,
        symbol: str = "",
        option_type: str = "",
        strike: float = 0.0,
        mode: str = "PAPER",
    ) -> bool:
        """Convenience wrapper used by main.py exit handlers."""
        return self.send_exit_alert(
            direction=direction,
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=quantity,
            gross_pnl=gross_pnl,
            reason=reason,
            sl_count=sl_count,
            mode=mode,
        )


# ------------------------------------------------------------------
# Quick self-test
# ------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    notifier = TelegramNotifier()
    if notifier._enabled:
        print("Sending test message...")
        ok = notifier._send_message(
            "<b>Telegram Notifier Test</b>\n\nConnection successful \u2705"
        )
        print("Result:", "OK" if ok else "FAILED")
    else:
        print(
            "Telegram not configured.\n"
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars."
        )
