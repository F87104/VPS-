#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools"
LOG_DIR="$BASE_DIR/logs"
SCRIPT="$BASE_DIR/auto_like_v11_2_1_limited.py"
NOTIFIER="$BASE_DIR/notify_slack_summary.py"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/.venv/bin/python}"
CONFIG_PATH="${X_CONFIG:-/home/ubuntu/prometheus/config.ini}"
STORAGE_STATE="${X_STORAGE_STATE:-/home/ubuntu/prometheus/x_storage_state.json}"
MAX_ACTIONS="${X_MAX_ACTIONS:-24}"
MAX_LIKES="${X_MAX_LIKES:-20}"
MAX_FOLLOWS="${X_MAX_FOLLOWS:-4}"
MAX_UNFOLLOWS="${X_MAX_UNFOLLOWS:-0}"
MAX_RUNTIME_MINUTES="${X_MAX_RUNTIME_MINUTES:-55}"
MASTER_LOG="$LOG_DIR/x_daily.log"
LAST_LOG="$LOG_DIR/x_daily_last.log"

EXTRA_ARGS=()
if [ -f "$STORAGE_STATE" ]; then
  EXTRA_ARGS+=(--storage-state "$STORAGE_STATE")
fi

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="/usr/bin/python3"
fi

mkdir -p "$LOG_DIR"
cd "$BASE_DIR" || exit 1

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') X daily start ===="
  echo "host=$(hostname)"
  echo "mode=timeline headless=yes max_actions=$MAX_ACTIONS max_likes=$MAX_LIKES max_follows=$MAX_FOLLOWS max_unfollows=$MAX_UNFOLLOWS max_runtime_minutes=$MAX_RUNTIME_MINUTES"
  echo "config=$CONFIG_PATH"
  if [ -f "$STORAGE_STATE" ]; then
    echo "storage_state=$STORAGE_STATE"
  else
    echo "storage_state=missing"
  fi
} > "$LAST_LOG"

"$PYTHON_BIN" "$SCRIPT" \
  --timeline \
  --headless \
  --config "$CONFIG_PATH" \
  --max-actions "$MAX_ACTIONS" \
  --max-likes "$MAX_LIKES" \
  --max-follows "$MAX_FOLLOWS" \
  --max-unfollows "$MAX_UNFOLLOWS" \
  --max-runtime-minutes "$MAX_RUNTIME_MINUTES" \
  "${EXTRA_ARGS[@]}" \
  >> "$LAST_LOG" 2>&1

status=$?

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') X daily exit status=$status ===="
  echo
} >> "$LAST_LOG"

cat "$LAST_LOG" >> "$MASTER_LOG"

if [ "$status" -ne 0 ] || grep -Eiq "storage_state=missing|\\[ERROR\\]|Traceback|Fatal error|Timeout|ログインが必要|headless実行中にXログイン|storage_stateが見つかりません|No such file or directory" "$LAST_LOG"; then
  "$PYTHON_BIN" "$NOTIFIER" \
    --title "Xいいね・フォロー自動実行 異常検知" \
    --status "1" \
    --log "$LAST_LOG" \
    >> "$MASTER_LOG" 2>&1 || true
fi

exit "$status"
