# NIFTY 50 EMA 5/20 Crossover Trading System (Dhan)

A production-ready Python automated trading system that executes an **EMA 5 / EMA 20 crossover** strategy on **NIFTY 50 ATM options** using the **DhanHQ API**.

## Strategy Summary

- **Signal source**: NIFTY 50 index 5-minute candles
- **Traded instrument**: NIFTY 50 **ATM options** (indices cannot be traded directly)
  - **Bullish cross** → **BUY ATM Call**
  - **Bearish cross** → **BUY ATM Put**
  - Opposite cross → close the current option, buy a fresh ATM option in the new direction
- **Entry confirmation**: after a crossover, the bot only buys once a candle **CLOSES** on the right side of EMA20 — close **above** EMA20 to buy a call (LONG), close **below** EMA20 to buy a put (SHORT). Entry happens on the **first** confirmed candle after the cross (not necessarily the cross candle). If price comes back through EMA20, the entry is skipped/kept pending.
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

### 3. Set Telegram credentials (optional)

The bot sends **trade entry/exit alerts**, a **startup alert**, and the **daily summary Excel file** to a Telegram chat.

1. Create a bot via [@BotFather](https://t.me/BotFather) to get a **bot token**.
2. Message your bot once, then find your **chat ID** (e.g. via `https://api.telegram.org/bot<TOKEN>/getUpdates` → `message.chat.id`).
3. Set these environment variables (leave empty to disable notifications):

```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

Test the connection:
```bash
python telegram_notifier.py
```

You should receive a test message ("🔔 Telegram Notifier Test") in the chat.

### 4. Verify Dhan account access

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

## Running on a Google Cloud VM (Background / 24/7)

To run the bot continuously on a GCP VM (so it trades automatically every market open):

```bash
# 1. Clone + setup
git clone https://github.com/kunalkapoor84/5_20_EMACrossBot.git
cd 5_20_EMACrossBot
sudo apt install -y python3-venv
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt

# 2. Set credentials (each shell session)
export DHAN_CLIENT_ID="your_client_id"
export DHAN_ACCESS_TOKEN="your_access_token"
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"

# 3. Start in the background
nohup venv/bin/python main.py > nohup.out 2>&1 &
```

Check it's running / watching output:
```bash
ps aux | grep main.py
tail -f nohup.out
tail -f logs/strategy.log
```

Stop it:
```bash
pkill -f main.py
```

**The bot keeps running on its own** — it waits for market open, trades 09:30–15:15 IST, squares off, and resumes the next open automatically. No need to restart daily **except for the Dhan token** (below).

### Daily Dhan token refresh (tokens expire every 24h)

Dhan access tokens expire every 24 hours, so each morning you must refresh the token and restart the bot:

```bash
export DHAN_ACCESS_TOKEN="NEW_TOKEN_FROM_PORTAL"
pkill -f main.py
nohup venv/bin/python main.py > nohup.out 2>&1 &
```

> `export` alone does **not** update an already-running process — you must also **restart** for the new token to take effect.

### Systemd (survives VM reboots — recommended)

For a true "set and forget" setup that auto-starts on VM boot:

```bash
sudo tee /etc/systemd/system/emacross.service > /dev/null <<'EOF'
[Unit]
Description=EMACross Trading Bot
After=network.target

[Service]
User=$USER
WorkingDirectory=/home/$USER/5_20_EMACrossBot
ExecStart=/home/$USER/5_20_EMACrossBot/venv/bin/python main.py
Environment=DHAN_CLIENT_ID=your_client_id
Environment=DHAN_ACCESS_TOKEN=your_access_token
Environment=TELEGRAM_BOT_TOKEN=your_bot_token
Environment=TELEGRAM_CHAT_ID=your_chat_id
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable emacross
sudo systemctl start emacross
sudo systemctl status emacross
```

Restart the service after refreshing the token:
```bash
sudo systemctl restart emacross
```


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
| `REQUIRE_CLOSE_CONFIRMATION` | True | Enter only once a candle closes above/below EMA20 after a cross |
| `PAPER_TRADING` | True | Paper vs live |
| `TELEGRAM_BOT_TOKEN` | env | Telegram bot token (via env var) |
| `TELEGRAM_CHAT_ID` | env | Telegram chat ID (via env var) |

## Key Behaviors

- **ATM options**: bullish cross buys the ATM **Call**, bearish cross buys the ATM **Put** (always long premium)
- **Opposite crossover exit**: closes the current call/put, then buys a fresh ATM option in the new direction
- **Completed candles only**: signals are only generated on fully closed 5-minute candles
- **Entry confirmation (armed)**: a crossover only "arms" a pending entry — the call/put is bought on the **first candle that closes above (long) / below (short) EMA20**. If the cross candle itself closes on the wrong side, the bot waits for a later candle; a reverse crossover re-arms the opposite direction.
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
- Dhan access tokens expire every 24 hours — refresh the token and restart the bot each morning
- Telegram notifications are disabled until `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set
- Run extensive paper/backtest validation before switching `PAPER_TRADING` to `False`
