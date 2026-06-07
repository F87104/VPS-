#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools"
LOG_DIR="$BASE_DIR/logs"
SCRIPT="$BASE_DIR/substack_like_follow_safe.py"
NOTIFIER="$BASE_DIR/notify_slack_summary.py"
MASTER_LOG="$LOG_DIR/substack_daily.log"
LAST_LOG="$LOG_DIR/substack_daily_last.log"

mkdir -p "$LOG_DIR"
cd "$BASE_DIR" || exit 1

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') substack daily start ===="
  echo "host=$(hostname)"
  echo "mode=headless execute=yes max_actions=6 max_likes=6 max_follows=2"
} > "$LAST_LOG"

/usr/bin/python3 "$SCRIPT" \
  --headless \
  --execute \
  --yes \
  --max-actions 6 \
  --max-likes 6 \
  --max-follows 2 \
  --min-wait 30 \
  --max-wait 120 \
  --view-min-wait 8 \
  --view-max-wait 24 \
  --scrolls 8 \
  >> "$LAST_LOG" 2>&1

status=$?

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') substack daily exit status=$status ===="
  echo
} >> "$LAST_LOG"

cat "$LAST_LOG" >> "$MASTER_LOG"

/usr/bin/python3 "$NOTIFIER" \
  --title "Substackいいね・フォロー自動実行" \
  --status "$status" \
  --log "$LAST_LOG" \
  >> "$MASTER_LOG" 2>&1 || true

exit "$status"
