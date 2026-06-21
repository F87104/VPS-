#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools"
LOG_DIR="$BASE_DIR/logs"
SCRIPT="$BASE_DIR/note_suki_follow_safe.py"
NOTIFIER="$BASE_DIR/notify_slack_summary.py"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/.venv/bin/python}"
STORAGE_STATE="${NOTE_STORAGE_STATE:-/home/ubuntu/prometheus/note_storage_state.json}"
NOTE_URL="${NOTE_URL:-https://note.com/}"
NOTE_KEYWORDS="${NOTE_KEYWORDS:-}"
NOTE_EXCLUDE_KEYWORDS="${NOTE_EXCLUDE_KEYWORDS:-PR,広告,勧誘,必勝,絶対,保証,無料プレゼント,LINE登録}"
MAX_ACTIONS="${NOTE_MAX_ACTIONS:-15}"
MAX_LIKES="${NOTE_MAX_LIKES:-12}"
MAX_FOLLOWS="${NOTE_MAX_FOLLOWS:-3}"
MASTER_LOG="$LOG_DIR/note_daily.log"
LAST_LOG="$LOG_DIR/note_daily_last.log"

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
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') note daily start ===="
  echo "host=$(hostname)"
  echo "url=$NOTE_URL"
  echo "keywords=$NOTE_KEYWORDS"
  echo "mode=headless execute=yes max_actions=$MAX_ACTIONS max_likes=$MAX_LIKES max_follows=$MAX_FOLLOWS"
  if [ -f "$STORAGE_STATE" ]; then
    echo "storage_state=$STORAGE_STATE"
  else
    echo "storage_state=missing"
  fi
} > "$LAST_LOG"

"$PYTHON_BIN" "$SCRIPT" \
  --headless \
  --execute \
  --yes \
  --url "$NOTE_URL" \
  --keywords "$NOTE_KEYWORDS" \
  --exclude-keywords "$NOTE_EXCLUDE_KEYWORDS" \
  --max-actions "$MAX_ACTIONS" \
  --max-likes "$MAX_LIKES" \
  --max-follows "$MAX_FOLLOWS" \
  --min-wait 20 \
  --max-wait 80 \
  --view-min-wait 3 \
  --view-max-wait 9 \
  --scrolls 8 \
  "${EXTRA_ARGS[@]}" \
  >> "$LAST_LOG" 2>&1

status=$?

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') note daily exit status=$status ===="
  echo
} >> "$LAST_LOG"

cat "$LAST_LOG" >> "$MASTER_LOG"

if [ "$status" -ne 0 ] || grep -Eiq "storage_state=missing|ERROR|Traceback|Fatal error|Timeout|note login prompt|ログインしてください|storage_state not found|No such file or directory" "$LAST_LOG"; then
  "$PYTHON_BIN" "$NOTIFIER" \
    --title "noteスキ・フォロー自動実行 異常検知" \
    --status "1" \
    --log "$LAST_LOG" \
    >> "$MASTER_LOG" 2>&1 || true
fi

exit "$status"
