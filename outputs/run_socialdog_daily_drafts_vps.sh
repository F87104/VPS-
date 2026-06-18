#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools"
LOG_DIR="$BASE_DIR/logs"
GENERATOR="$BASE_DIR/socialdog_generate_daily_posts.py"
DRAFT_FILLER="$BASE_DIR/socialdog_draft_safe.py"
NOTIFIER="$BASE_DIR/notify_slack_summary.py"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/.venv/bin/python}"
PROFILE_DIR="${SOCIALDOG_PROFILE_DIR:-/home/ubuntu/f_tools/.socialdog_profile}"
RUN_DATE="$(date '+%Y-%m-%d')"
OUT_DIR="$LOG_DIR/socialdog_drafts/$RUN_DATE"
MASTER_LOG="$LOG_DIR/socialdog_daily_drafts.log"
LAST_LOG="$LOG_DIR/socialdog_daily_drafts_last.log"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="/usr/bin/python3"
fi

mkdir -p "$LOG_DIR" "$OUT_DIR"
cd "$BASE_DIR" || exit 1

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') SocialDog daily drafts start ===="
  echo "host=$(hostname)"
  echo "profile_dir=$PROFILE_DIR"
  echo "out_dir=$OUT_DIR"
} > "$LAST_LOG"

"$PYTHON_BIN" "$GENERATOR" \
  --out-dir "$OUT_DIR" \
  --update-history \
  >> "$LAST_LOG" 2>&1

status=$?

if [ "$status" -eq 0 ]; then
  for file in "$OUT_DIR"/1_*.txt "$OUT_DIR"/2_*.txt "$OUT_DIR"/3_*.txt; do
    if [ ! -f "$file" ]; then
      echo "missing draft file: $file" >> "$LAST_LOG"
      status=1
      break
    fi

    echo "---- saving draft: $file ----" >> "$LAST_LOG"
    "$PYTHON_BIN" "$DRAFT_FILLER" \
      --text-file "$file" \
      --save-draft \
      --headless \
      --profile-dir "$PROFILE_DIR" \
      --login-wait-seconds 20 \
      --keep-open-seconds 0 \
      >> "$LAST_LOG" 2>&1

    step_status=$?
    if [ "$step_status" -ne 0 ]; then
      status="$step_status"
      break
    fi

    sleep 8
  done
fi

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') SocialDog daily drafts exit status=$status ===="
  echo
} >> "$LAST_LOG"

cat "$LAST_LOG" >> "$MASTER_LOG"

"$PYTHON_BIN" "$NOTIFIER" \
  --title "SocialDog朝昼夕下書き保存" \
  --status "$status" \
  --log "$LAST_LOG" \
  >> "$MASTER_LOG" 2>&1 || true

exit "$status"
