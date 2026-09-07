#!/usr/bin/env bash
# restart.sh — refresh the Dhan access token and restart the EMA crossover bot.
#
# Dhan access tokens expire every 24 hours. Each morning:
#   1. Get a fresh token from the Dhan Developer portal.
#   2. Update the token below (or set DHAN_ACCESS_TOKEN before running this).
#   3. Run:  ./restart.sh
#
# If DHAN_ACCESS_TOKEN is already set in the environment (e.g. via systemd or
# ~/.bashrc), it is used and there is no need to edit the file.

set -e

cd "$(dirname "$0")"

# --- Config -------------------------------------------------------------
REPO_ROOT="$(pwd)"
PID_FILE="$REPO_ROOT/.bot.pid"
VENV_PYTHON="$REPO_ROOT/venv/bin/python"
MAIN_SCRIPT="$REPO_ROOT/main.py"
# Optionally override the token here (only used if the env var is empty):
DEFAULT_DHAN_ACCESS_TOKEN="${DHAN_ACCESS_TOKEN:-}"

# --- Load defaults if env vars are not already set ----------------------
export DHAN_CLIENT_ID="${DHAN_CLIENT_ID:-1111206177}"
if [ -n "$DEFAULT_DHAN_ACCESS_TOKEN" ]; then
    export DHAN_ACCESS_TOKEN="$DEFAULT_DHAN_ACCESS_TOKEN"
fi
export TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
export TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# --- Pre-flight checks ---------------------------------------------------
if [ -z "$DHAN_ACCESS_TOKEN" ]; then
    echo "ERROR: DHAN_ACCESS_TOKEN is not set."
    echo "Set it in your environment or edit DEFAULT_DHAN_ACCESS_TOKEN in this script."
    exit 1
fi
if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# --- Stop our own instance only ------------------------------------------
# Never use a loose `pkill -f main.py` here: other projects on the same VM
# (e.g. /opt/scanner/main.py) match that pattern and would get killed.
# We track OUR exact process via a PID file written on start.
if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing strategy instance (pid $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        # give it a moment to run its cleanup (square-off, save state, EOD Excel)
        for _ in 1 2 3 4 5; do
            kill -0 "$OLD_PID" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "Force-killing $OLD_PID..."
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
    fi
    rm -f "$PID_FILE"
fi

# Also stop any manually started instance of THIS repo (absolute path only).
for pid in $(pgrep -f "$REPO_ROOT/main.py" 2>/dev/null || true); do
    kill "$pid" 2>/dev/null || true
done

# --- Restart --------------------------------------------------------------
echo "Starting strategy in background..."
nohup "$VENV_PYTHON" "$MAIN_SCRIPT" > nohup.out 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > "$PID_FILE"

echo "Restart done (pid $BOT_PID)."
echo "Token in use: ${DHAN_ACCESS_TOKEN:0:12}... (client ${DHAN_CLIENT_ID})"
echo "Logs: tail -f nohup.out   |   tail -f logs/strategy.log"
