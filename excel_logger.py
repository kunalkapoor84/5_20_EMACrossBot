"""Real-time Excel export of the trade journal and daily summary.

Creates one Excel workbook per trading day under excel_daily/, and also a
cumulative 'all_trades.xlsx'. Each trade row is appended as soon as it closes,
and a summary sheet is kept up to date for the current day.

Imperative: openpyxl must be installed (included in requirements.txt).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import pytz
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import config

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
EXCEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excel_daily")

# Headers for the trades sheet
TRADE_HEADERS = [
    "Date", "Entry Time", "Exit Time", "Direction", "Symbol",
    "Option Type", "Strike", "Quantity",
    "Entry Price", "Exit Price", "SL Price", "Target Price",
    "Exit Reason", "Gross P&L", "Net P&L",
    "Consecutive SL", "EMA5 Entry", "EMA20 Entry", "EMA5 Exit", "EMA20 Exit",
    "Entry Order ID", "Exit Order ID", "Security ID",
]

# Headers for the summary sheet
SUMMARY_HEADERS = [
    "Date", "Trades", "Wins", "Target Wins", "SL Losses", "Crossover Exits",
    "Win Rate %", "Gross P&L", "Net P&L", "Avg Win", "Avg Loss",
    "Max Win", "Max Loss", "Max Drawdown", "Profit Factor",
    "Consecutive SL Count", "Status",
]

HEADER_FILL = PatternFill("solid", fgColor="4472C4")
HEADER_FONT = Font(color="FFFFFF", bold=True)
CENTER = Alignment(horizontal="center")


class ExcelLogger:
    """Writes trade rows and day summary to Excel, one workbook per day."""

    def __init__(self, excel_dir: str | None = None):
        self.excel_dir = excel_dir or EXCEL_DIR
        os.makedirs(self.excel_dir, exist_ok=True)
        self._day_workbook: openpyxl.Workbook | None = None
        self._day_path = ""
        self._day_date = ""
        self._summary_row: list | None = None
        self._summary_row_num = 0

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def day_path(self, date_str: str) -> str:
        return os.path.join(self.excel_dir, f"trades_{date_str}.xlsx")

    def all_path(self) -> str:
        return os.path.join(self.excel_dir, "all_trades.xlsx")

    # ------------------------------------------------------------------
    # Daily workbook management
    # ------------------------------------------------------------------
    def _ensure_day(self, date_str: str) -> None:
        """Open (or create) today's workbook if not already open."""
        if self._day_date == date_str and self._day_workbook is not None:
            return

        self._day_date = date_str
        self._day_path = self.day_path(date_str)
        wb = openpyxl.Workbook()

        if os.path.exists(self._day_path):
            wb = openpyxl.load_workbook(self._day_path)

        # Trades sheet
        if "Trades" not in wb.sheetnames:
            ws = wb.create_sheet("Trades")
            self._write_header(ws, TRADE_HEADERS)
        # Summary sheet
        if "Daily Summary" not in wb.sheetnames:
            ssum = wb.create_sheet("Daily Summary")
            self._write_header(ssum, SUMMARY_HEADERS)

        self._day_workbook = wb
        self._summary_row = None
        self._summary_row_num = 0

    @staticmethod
    def _write_header(ws, headers: list[str]) -> None:
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = CENTER
        ws.freeze_panes = "A2"

    @staticmethod
    def _auto_width(ws) -> None:
        for col in range(1, ws.max_column + 1):
            letter = get_column_letter(col)
            max_len = 0
            for row in ws.iter_rows(min_col=col, max_col=col):
                for cell in row:
                    if cell.value is not None:
                        max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[letter].width = min(max_len + 3, 40)

    # ------------------------------------------------------------------
    # Append a trade row (called in real-time as each trade closes)
    # ------------------------------------------------------------------
    def append_trade(self, trade: dict) -> None:
        """Append a trade to today's workbook and the cumulative workbook.

        Args:
            trade: dict with keys matching TRADE_HEADERS (Date through Exit Order ID).
        """
        date_str = trade.get("Date") or datetime.now(IST).strftime("%Y-%m-%d")
        self._ensure_day(date_str)

        # --- Daily workbook: Trades sheet ---
        ws = self._day_workbook["Trades"]
        row_num = ws.max_row + 1
        values = [trade.get(h, "") for h in TRADE_HEADERS]
        for col, v in enumerate(values, start=1):
            ws.cell(row=row_num, column=col, value=v)
        self._auto_width(ws)

        self._day_workbook.save(self._day_path)

        # --- Daily workbook: keep summary up to date ---
        today_summary = self._recompute_day_summary()
        self._update_summary_sheet(today_summary, date_str)
        self._day_workbook.save(self._day_path)

        # --- Cumulative workbook ---
        self._append_all_trades(trade)

        logger.info("Excel trade row written: %s (%s)", self._day_path, date_str)

    # ------------------------------------------------------------------
    # Summary computation
    # ------------------------------------------------------------------
    def _read_day_trades(self) -> list[list]:
        ws = self._day_workbook["Trades"]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and row[0] is not None:
                rows.append(list(row))
        return rows

    def _recompute_day_summary(self) -> list:
        """Compute latest daily summary row from the Trades sheet."""
        trades = self._read_day_trades()
        n = len(trades)
        if n == 0:
            return [datetime.now(IST).strftime("%Y-%m-%d"), 0, 0, 0, 0, 0, 0.0,
                    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, "Running"]

        gross_wins = [t[10] for t in trades if isinstance(t[10], (int, float)) and t[10] > 0]
        gross_losses = [t[10] for t in trades if isinstance(t[10], (int, float)) and t[10] < 0]
        dates = {t[0] for t in trades}
        date_str = sorted(dates)[0] if dates else datetime.now(IST).strftime("%Y-%m-%d")

        reasons = [t[9] for t in trades]
        target_wins = reasons.count("TARGET")
        sl_losses = reasons.count("SL")
        crossover_exits = reasons.count("CROSSOVER")

        gross = sum(t[10] for t in trades if isinstance(t[10], (int, float)))
        net = sum(t[11] if isinstance(t[11], (int, float)) else t[10] for t in trades)

        wins = len(gross_wins)
        losses = len(gross_losses)
        win_rate = (wins / n * 100) if n else 0
        avg_win = sum(gross_wins) / len(gross_wins) if gross_wins else 0
        avg_loss = sum(gross_losses) / len(gross_losses) if gross_losses else 0
        max_win = max(gross_wins) if gross_wins else 0
        max_loss = min(gross_losses) if gross_losses else 0

        # Drawdown / profit factor across day's trades in order
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        gross_profit = 0.0
        gross_loss_abs = 0.0
        for t in trades:
            pnl = t[10] if isinstance(t[10], (int, float)) else 0
            cum += pnl
            if pnl > 0:
                gross_profit += pnl
            elif pnl < 0:
                gross_loss_abs += abs(pnl)
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)

        pf = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else (gross_profit if gross_profit > 0 else 0.0)

        consec = self._last_consecutive_sl(trades)
        status = "Stopped (3 SL)" if consec >= config.MAX_CONSECUTIVE_SL else "Running"

        return [date_str, n, wins, target_wins, sl_losses, crossover_exits,
                round(win_rate, 2), round(gross, 2), round(net, 2),
                round(avg_win, 2), round(avg_loss, 2),
                round(max_win, 2), round(max_loss, 2), round(max_dd, 2),
                round(pf, 3), consec, status]

    @staticmethod
    def _last_consecutive_sl(trades: list[list]) -> int:
        # Use the Consecutive SL column stored on the last trade (index 12)
        for t in reversed(trades):
            if isinstance(t[12], (int, float)):
                return int(t[12])
        return 0

    def _update_summary_sheet(self, summary_row: list, date_str: str) -> None:
        ssum = self._day_workbook["Daily Summary"]
        # find existing row for this date (col1)
        target_row = None
        for row in ssum.iter_rows(min_row=2, max_col=1):
            cell = row[0]
            if cell.value == date_str:
                target_row = cell.row
                break
        if target_row is None:
            target_row = ssum.max_row + 1

        for col, v in enumerate(summary_row, start=1):
            ssum.cell(row=target_row, column=col, value=v)
        self._auto_width(ssum)

    # ------------------------------------------------------------------
    # Cumulative workbook
    # ------------------------------------------------------------------
    def _append_all_trades(self, trade: dict) -> None:
        all_path = self.all_path()
        exists = os.path.exists(all_path)
        if exists:
            wb = openpyxl.load_workbook(all_path)
            ws = wb["Trades"]
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Trades"
            self._write_header(ws, TRADE_HEADERS)

        row_num = ws.max_row + 1
        values = [trade.get(h, "") for h in TRADE_HEADERS]
        for col, v in enumerate(values, start=1):
            ws.cell(row=row_num, column=col, value=v)
        self._auto_width(ws)
        wb.save(all_path)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    def finalize_day(self) -> None:
        """Finalize the current day summary (status->Completed, save+close)."""
        if not self._day_workbook:
            self._ensure_day(datetime.now(IST).strftime("%Y-%m-%d"))

        summary_rows = list(self._day_workbook["Daily Summary"].iter_rows(min_row=2, values_only=True))
        # update the last row's status to Completed
        ssum = self._day_workbook["Daily Summary"]
        last_row = ssum.max_row
        if last_row >= 2:
            ssum.cell(row=last_row, column=len(SUMMARY_HEADERS), value="Completed")
        self._day_workbook.save(self._day_path)
        logger.info("Excel day finalized: %s", self._day_path)

    def current_summary(self) -> dict:
        """Return today's summary values as a dict for printing/logging."""
        if not self._day_workbook:
            self._ensure_day(datetime.now(IST).strftime("%Y-%m-%d"))
        row = self._recompute_day_summary()
        return dict(zip(SUMMARY_HEADERS, row))
