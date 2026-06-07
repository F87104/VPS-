#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools"
LOG_DIR="$BASE_DIR/logs"
SCRIPT="$BASE_DIR/auto_like_v11_2_1_limited.py"
NOTIFIER="$BASE_DIR/notify_slack_summary.py"
CONFIG_PATH="${X_CONFIG:-/home/ubuntu/prometheus/config.ini}"
STORAGE_STATE="${X_STORAGE_STATE:-/home/ubuntu/prometheus/x_storage_state.json}"
MASTER_LOG="$LOG_DIR/x_daily.log"
LAST_LOG="$LOG_DIR/x_daily_last.log"

EXTRA_ARGS=()
if [ -f "$STORAGE_STATE" ]; then
  EXTRA_ARGS+=(--storage-state "$STORAGE_STATE")
fi

mkdir -p "$LOG_DIR"
cd "$BASE_DIR" || exit 1

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') X daily start ===="
  echo "host=$(hostname)"
  echo "mode=timeline headless=yes max_actions=12 max_likes=10 max_follows=2 max_unfollows=0 max_runtime_minutes=45"
  echo "config=$CONFIG_PATH"
  if [ -f "$STORAGE_STATE" ]; then
    echo "storage_state=$STORAGE_STATE"
  else
    echo "storage_state=missing"
  fi
} > "$LAST_LOG"

/usr/bin/python3 "$SCRIPT" \
  --timeline \
  --headless \
  --config "$CONFIG_PATH" \
  --max-actions 12 \
  --max-likes 10 \
  --max-follows 2 \
  --max-unfollows 0 \
  --max-runtime-minutes 45 \
  "${EXTRA_ARGS[@]}" \
  >> "$LAST_LOG" 2>&1

status=$?

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') X daily exit status=$status ===="
  echo
} >> "$LAST_LOG"

cat "$LAST_LOG" >> "$MASTER_LOG"

/usr/bin/python3 "$NOTIFIER" \
  --title "Xいいね・フォロー自動実行" \
  --status "$status" \
  --log "$LAST_LOG" \
  >> "$MASTER_LOG" 2>&1 || true

exit "$status"
