#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools/x_buzz_finder"
ROOT_DIR="/home/ubuntu/f_tools"
LOG_DIR="$BASE_DIR/logs"
SCRIPT="$BASE_DIR/x_buzz_finder.py"
CONFIG="$BASE_DIR/config.json"
NOTIFIER="$ROOT_DIR/notify_slack_summary.py"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
STORAGE_STATE="${X_STORAGE_STATE:-/home/ubuntu/prometheus/x_storage_state.json}"
MASTER_LOG="$LOG_DIR/x_buzz_finder.log"
LAST_LOG="$LOG_DIR/x_buzz_finder_last.log"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="/usr/bin/python3"
fi

mkdir -p "$LOG_DIR"
cd "$BASE_DIR" || exit 1

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') X buzz finder start ===="
  echo "host=$(hostname)"
  echo "config=$CONFIG"
  if [ -f "$STORAGE_STATE" ]; then
    echo "storage_state=$STORAGE_STATE"
  else
    echo "storage_state=missing"
  fi
} > "$LAST_LOG"

"$PYTHON_BIN" "$SCRIPT" \
  --config "$CONFIG" \
  --headless \
  --storage-state "$STORAGE_STATE" \
  >> "$LAST_LOG" 2>&1

status=$?

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') X buzz finder exit status=$status ===="
  echo
} >> "$LAST_LOG"

cat "$LAST_LOG" >> "$MASTER_LOG"

"$PYTHON_BIN" "$NOTIFIER" \
  --title "Xバズ返信候補検索" \
  --status "$status" \
  --log "$LAST_LOG" \
  >> "$MASTER_LOG" 2>&1 || true

exit "$status"
