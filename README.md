# NIFTY 50 EMA 5/20 Crossover Trading System (Dhan)

A production-ready Python automated trading system that executes an **EMA 5 / EMA 20 crossover** strategy on **NIFTY 50 ATM options** using the **DhanHQ API**.

## Strategy Summary

- **Signal source**: NIFTY 50 index 5-minute candles
- **Traded instrument**: NIFTY 50 **ATM options** (indices cannot be traded directly)
  - **Bullish cross** → **BUY ATM Call**
  - **Bearish cross** → **BUY ATM Put**
  - Opposite cross → close the current option, buy a fresh ATM option in the new direction
- **ATM strike**: nearest strike to live NIFTY spot, current-month expiry (resolved from the live option chain)
- **Stop Loss**: 15 **premium points** from entry (below entry — long premium)
- **Target**: 30 **premium points** from entry (above entry — long premium, 1:2 risk/reward)
- **Exit on opposite crossover**: close current call/put, buy fresh option
- **Consecutive SL limit**: stop after 3 consecutive SLs for the day (hard exit)
- **Trading hours**: 09:30–15:15 IST
- **Position sizing**: 1 lot (configurable), lot size = 65 resolved from security master
- **Default**: PAPER TRADING (no live orders placed)

## Architecture

```
EMACross/
├── config.py          # All strategy parameters (single configuration file)
├── dhan_client.py     # Dhan SDK init + ATM option resolution
├── market_data.py     # 5-min candle fetching + EMA computation
├── strategy.py        # EMA crossover signal detection
├── order_manager.py   # Dhan order placement/status/cancel/positions
├── risk_manager.py    # SL/Target tracking + consecutive SL counter
├── state_manager.py   # Persistent state for crash recovery
├── trade_logger.py    # SQLite trade journal
├── excel_logger.py    # Real-time Excel export (per-day + cumulative)
├── main.py            # Main trading loop
├── backtest.py        # Independent backtesting module
├── requirements.txt   # Python dependencies
└── trades.db          # Created at runtime — trade history
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Dhan credentials

Set these **environment variables** (never commit credentials):

**Windows (PowerShell):**
```powershell
$env:DHAN_CLIENT_ID = "your_client_id"
$env:DHAN_ACCESS_TOKEN = "your_access_token"
```

**Linux/macOS:**
```bash
export DHAN_CLIENT_ID="your_client_id"
export DHAN_ACCESS_TOKEN="your_access_token"
```

You can also put them in your shell profile or a `.env` (if you use `python-dotenv`).

### 3. Verify Dhan account access

Before live use, verify:
- **Data Plan** is active (required for candles/quotes/feed)
- **Static IP** is whitelisted (required for order placement/cancellation)
- The **NSE F&O segment** is activated

Check via the Dhan web dashboard or run:
```python
python -c "from dhan_client import DhanClient; c = DhanClient(paper_trading=True); print(c.get_profile())"
```

## Running

### Live/paper strategy

```bash
python main.py
```

Default mode is **paper trading** (`PAPER_TRADING = True` in `config.py`). In paper mode, orders are simulated — the strategy resolves the live ATM option (call or put based on the EMA cross), buys it at the current premium, and SL/target are detected from the **option's own premium** (SL 15 points below, target 30 points above). Opposite crossovers close the current option and buy a fresh one.

To enable live trading, edit `config.py`:

```python
PAPER_TRADING = False   # Only after thorough backtesting and testing
```

### Backtesting

```bash
# Backtest last 30 days with default settings
python backtest.py

# Backtest a specific range
python backtest.py --from-date 2026-01-01 --to-date 2026-08-01
```

The backtest is **fully independent** of live execution and produces:
- Trade-by-trade log
- Win rate, average win/loss, max drawdown
- Profit factor
- Number of days stopped after 3 consecutive SLs
- Daily P&L breakdown
- CSV export of all trades

### Emergency square-off

`emergency_square_off()` is exported from `main.py`:

```python
from main import emergency_square_off
# Requires order_manager and risk_manager instances
```

It closes any open strategy position, cancels pending orders, and verifies the account is flat.

## Configuration

All strategy parameters live in `config.py`:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `FAST_EMA` | 5 | Fast EMA period |
| `SLOW_EMA` | 20 | Slow EMA period |
| `TIMEFRAME_MINUTES` | 5 | Candle timeframe |
| `START_TIME` | 09:30 | Start taking trades |
| `EXIT_TIME` | 15:15 | Square off / stop |
| `STOP_LOSS_POINTS` | 15 | SL (option premium points) |
| `TARGET_POINTS` | 30 | Target (option premium points) |
| `MAX_CONSECUTIVE_SL` | 3 | Stop after this many SLs |
| `QUANTITY` | 1 | Lots traded |
| `TRADE_OPTIONS` | True | Trade ATM options (vs futures) |
| `OPTION_EXPIRY` | monthly | Option expiry to trade |
| `PAPER_TRADING` | True | Paper vs live |

## Key Behaviors

- **ATM options**: bullish cross buys the ATM **Call**, bearish cross buys the ATM **Put** (always long premium)
- **Opposite crossover exit**: closes the current call/put, then buys a fresh ATM option in the new direction
- **Completed candles only**: signals are only generated on fully closed 5-minute candles
- **No duplicates**: each candle is processed exactly once
- **Broker authority**: on restart, the Dhan broker position takes precedence over local state
- **Protective orders**: after entry fill, SL and target orders are placed (paper mode simulates them)
- **Partial-fill handling**: uses actual average fill price and filled quantity from Dhan
- **Error resilience**: waits for fills, cancels protective orders on manual exits, retries transient API errors
- **Consecutive SL logic**: only actual SL executions increment the counter; targets and crossover exits do not; 3 → stop for the day

## Output Files

- `logs/strategy.log` — detailed rotating log
- `state/strategy_state.json` — persisted state (for crash recovery)
- `trades.db` — SQLite trade journal
- `excel_daily/trades_<YYYY-MM-DD>.xlsx` — per-day Excel workbook written in **real time**
- `excel_daily/all_trades.xlsx` — cumulative Excel workbook across all days
- `backtest_trades_*.csv` — backtest trade export

## Real-Time Excel Output

`excel_logger.py` mirrors every closed trade into Excel the moment it happens, so you can watch the file fill up while the bot runs.

- **Per-day workbook** `excel_daily/trades_2026-09-03.xlsx` contains two sheets:
  - **Trades** — one row per closed trade (incl. option symbol, type CE/PE, strike)
  - **Daily Summary** — live stats (trades, wins, SL losses, turnover by reason, win rate, P&L, avg/max win & loss, max drawdown, profit factor, consecutive-SL count, status)
- **Cumulative** `excel_daily/all_trades.xlsx` — every trade from all days appended.
- Summary status stays `Running` all day; it shows `Stopped (3 SL)` after 3 consecutive SLs stops trading, and the program flips it to `Completed` on shutdown.
- Each run of `python main.py` auto-opens/reuses today's workbook, so re-runs keep appending.
- Requires `openpyxl` (added to `requirements.txt`).

## Safety Notes

- In live mode, orders require a whitelisted static IP
- Order/data/exchange rate limits follow Dhan's documentation
- Index data (`IDX_I`) has no tradable contract — the system trades **ATM index options** on NSE_FNO
- Option premium leads/moves faster than the index; the 15/30 point SL/target are on the **premium** and may trigger on normal intraday swings
- Never commit your Dhan credentials to version control
- Run extensive paper/backtest validation before switching `PAPER_TRADING` to `False`
