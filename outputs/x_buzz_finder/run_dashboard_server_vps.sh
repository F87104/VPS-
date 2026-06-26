#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools/x_buzz_finder"
PUBLIC_DIR="$BASE_DIR/public"
LOG_DIR="$BASE_DIR/logs"
PID_FILE="$LOG_DIR/dashboard_server.pid"
PORT="${X_BUZZ_DASHBOARD_PORT:-8787}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

mkdir -p "$PUBLIC_DIR" "$LOG_DIR"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    exit 0
  fi
fi

cd "$PUBLIC_DIR" || exit 1
nohup "$PYTHON_BIN" -m http.server "$PORT" --bind 0.0.0.0 \
  >> "$LOG_DIR/dashboard_server.log" 2>&1 &
echo "$!" > "$PID_FILE"
