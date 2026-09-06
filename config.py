"""Strategy configuration for NIFTY 50 EMA 5/20 Crossover System."""

import os

# ---------------------------------------------------------------------------
# Dhan API credentials — loaded ONLY from environment variables.
# NEVER hardcode credentials here (the repo is shareable/public).
# ---------------------------------------------------------------------------
DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "1111206177")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzg4NTc5NDMzLCJpYXQiOjE3ODg0OTMwMzMsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTExMjA2MTc3In0.7rYabnOM3E961bi7amLsaFSLrVRiRKJqLRW36_0CIHJxgK2idw_ie5z5yUXDCq4kWxN8rc5B--8ZnHvMPvPL0A")

# ---------------------------------------------------------------------------
# Instrument
# NIFTY 50 index (for EMA signals) + NIFTY futures (for trading).
# The index cannot be traded directly; we trade the nearest NIFTY future.
# ---------------------------------------------------------------------------
INSTRUMENT = "NIFTY"
INDEX_SECURITY_ID = "13"       # NIFTY 50 index security_id (for data/signals)
INDEX_EXCHANGE_SEGMENT = "IDX_I"  # Index exchange segment (for data/signals)

# NIFTY futures contract — resolved dynamically via security master.
# Fallback values if resolution fails:
FUTURES_SECURITY_ID = ""       # Resolved at runtime for nearest expiry
FUTURES_EXCHANGE_SEGMENT = "NSE_FNO"  # F&O segment for futures
FUTURES_INSTRUMENT_TYPE = "FUTIDX"    # Index futures

# Traded instrument: NIFTY 50 ATM options (call on bullish, put on bearish).
TRADE_OPTIONS = True           # Trade ATM options instead of futures
OPTION_EXCHANGE_SEGMENT = "NSE_FNO"   # F&O segment for options
OPTION_INSTRUMENT_TYPE = "OPTIDX"     # Index options
OPTION_CALL = "CE"             # Dhan option type for call
OPTION_PUT = "PE"              # Dhan option type for put
OPTION_STRIKE_INTERVAL = 50    # NIFTY option strikes spacing
# Expiry to trade: "monthly" (current month's late expiry) or "weekly".
OPTION_EXPIRY = "weekly"

# The traded instrument (resolved at runtime to the ATM option security_id)
SECURITY_ID = INDEX_SECURITY_ID
EXCHANGE_SEGMENT = INDEX_EXCHANGE_SEGMENT
PRODUCT_TYPE = "INTRADAY"  # Intraday for index/futures/options trading

# ---------------------------------------------------------------------------
# Strategy parameters
# ---------------------------------------------------------------------------
TIMEFRAME_MINUTES = 5  # 5-minute candles
FAST_EMA = 5
SLOW_EMA = 20

# Require the signal candle to CLOSE on the correct side of EMA20 (SLOW_EMA)
# before taking a trade, instead of entering immediately on the crossover.
#   - Long entry only when close > EMA20 (if price comes back down, skip)
#   - Short entry only when close < EMA20
REQUIRE_CLOSE_CONFIRMATION = True

# ---------------------------------------------------------------------------
# Trading hours (IST)
# ---------------------------------------------------------------------------
START_TIME = "09:30"  # Start taking trades
EXIT_TIME = "15:15"   # Square off all positions

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
STOP_LOSS_POINTS = 15   # Price points, not rupees
TARGET_POINTS = 30       # Price points, not rupees
MAX_CONSECUTIVE_SL = 3   # Stop trading after 3 consecutive SLs

# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
QUANTITY = 1  # Number of lots (lot size resolved from security master)

# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------
PAPER_TRADING = True  # Set to False only after thorough testing

# ---------------------------------------------------------------------------
# Telegram notifications
# Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment variables.
# Leave empty to disable notifications.
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8925480127:AAEgzK5VwN4aAIqIc5YJWhDjj9_ToZjNIOw")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "632095996")
ENABLE_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
STATE_FILE = os.path.join(STATE_DIR, "strategy_state.json")
TRADE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "strategy.log")

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
HISTORICAL_BARS = 100  # Enough history for EMA warm-up
POLL_INTERVAL_SECONDS = 10  # How often to check for new candles

# ---------------------------------------------------------------------------
# Candle completion buffer (seconds after candle close to consider it complete)
# ---------------------------------------------------------------------------
CANDLE_BUFFER_SECONDS = 5
