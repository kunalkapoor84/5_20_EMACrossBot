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
VENV_PYTHON="venv/bin/python"
MAIN_SCRIPT="main.py"
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
if [ ! -x "$VENV_PYTHON" ] && [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# --- Stop any running instance -------------------------------------------
echo "Stopping running strategy instance (if any)..."
pkill -f "$MAIN_SCRIPT" 2>/dev/null || true
# give it a moment to run its cleanup (square-off, save state, EOD Excel)
sleep 3

# --- Restart --------------------------------------------------------------
echo "Starting strategy in background..."
nohup "$VENV_PYTHON" "$MAIN_SCRIPT" > nohup.out 2>&1 &

echo "Restart done."
echo "Token in use: ${DHAN_ACCESS_TOKEN:0:12}... (client ${DHAN_CLIENT_ID})"
echo "Logs: tail -f nohup.out   |   tail -f logs/strategy.log"
