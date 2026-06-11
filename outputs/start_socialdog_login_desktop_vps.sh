#!/bin/bash
set -u

BASE_DIR="/home/ubuntu/f_tools"
LOG_DIR="$BASE_DIR/logs"
PROFILE_DIR="${SOCIALDOG_PROFILE_DIR:-/home/ubuntu/f_tools/.socialdog_profile}"
DISPLAY_ID="${SOCIALDOG_DISPLAY:-:99}"
VNC_PORT="${SOCIALDOG_VNC_PORT:-5900}"
NOVNC_PORT="${SOCIALDOG_NOVNC_PORT:-6080}"
LOG_FILE="$LOG_DIR/socialdog_login_desktop.log"

mkdir -p "$LOG_DIR" "$PROFILE_DIR"
cd "$BASE_DIR" || exit 1

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') SocialDog login desktop start ===="
  echo "display=$DISPLAY_ID"
  echo "profile_dir=$PROFILE_DIR"
  echo "novnc=http://127.0.0.1:$NOVNC_PORT/vnc.html"
} >> "$LOG_FILE"

pkill -f "Xvfb $DISPLAY_ID" 2>/dev/null || true
pkill -f "x11vnc.*$VNC_PORT" 2>/dev/null || true
pkill -f "websockify.*$NOVNC_PORT" 2>/dev/null || true

Xvfb "$DISPLAY_ID" -screen 0 1440x1000x24 >> "$LOG_FILE" 2>&1 &
sleep 1

DISPLAY="$DISPLAY_ID" openbox >> "$LOG_FILE" 2>&1 &
sleep 1

x11vnc \
  -display "$DISPLAY_ID" \
  -localhost \
  -nopw \
  -forever \
  -shared \
  -rfbport "$VNC_PORT" \
  >> "$LOG_FILE" 2>&1 &

websockify \
  --web=/usr/share/novnc \
  "127.0.0.1:$NOVNC_PORT" \
  "127.0.0.1:$VNC_PORT" \
  >> "$LOG_FILE" 2>&1 &

echo "Open this on your Mac while SSH tunnel is active:"
echo "http://127.0.0.1:$NOVNC_PORT/vnc.html"

DISPLAY="$DISPLAY_ID" /usr/bin/python3 "$BASE_DIR/socialdog_draft_safe.py" \
  --login-only \
  --profile-dir "$PROFILE_DIR" \
  --login-wait-seconds 1800 \
  --keep-open-seconds 1800 \
  >> "$LOG_FILE" 2>&1

status=$?

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') SocialDog login desktop exit status=$status ===="
  echo
} >> "$LOG_FILE"

exit "$status"
