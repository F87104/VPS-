#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/f_tools}"
REPO_DIR="${REPO_DIR:-$HOME/VPS-}"
REPO_URL="${REPO_URL:-https://github.com/F87104/VPS-.git}"

log() {
  printf '\n[setup] %s\n' "$1"
}

require_ubuntu() {
  if [ ! -f /etc/os-release ]; then
    echo "This script is intended for Ubuntu 24.04 VPS hosts."
    exit 1
  fi

  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ]; then
    echo "This script is intended for Ubuntu. Detected: ${ID:-unknown}"
    exit 1
  fi
}

install_packages() {
  log "Installing Ubuntu packages"
  sudo apt update
  sudo apt install -y \
    git curl ca-certificates jq build-essential \
    python3 python3-venv python3-pip \
    cron tmux ufw \
    nodejs npm \
    xvfb openbox x11vnc novnc websockify
}

sync_repo() {
  log "Preparing GitHub repository"
  if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull --ff-only
  else
    git clone "$REPO_URL" "$REPO_DIR"
  fi
}

sync_app_files() {
  log "Copying output files into $APP_DIR"
  mkdir -p "$APP_DIR/logs"

  if [ ! -d "$REPO_DIR/outputs" ]; then
    echo "Missing $REPO_DIR/outputs"
    exit 1
  fi

  cp -a "$REPO_DIR/outputs/." "$APP_DIR/"

  chmod +x "$APP_DIR"/run_*_vps.sh 2>/dev/null || true
  chmod +x "$APP_DIR"/notify_slack_summary.py 2>/dev/null || true
  chmod +x "$APP_DIR"/slack_test.py 2>/dev/null || true
  chmod +x "$APP_DIR"/socialdog_draft_safe.py 2>/dev/null || true
  chmod +x "$APP_DIR"/socialdog_generate_daily_posts.py 2>/dev/null || true
  chmod +x "$APP_DIR"/substack_like_follow_safe.py 2>/dev/null || true
  chmod +x "$APP_DIR"/note_suki_follow_safe.py 2>/dev/null || true
}

setup_python() {
  log "Creating Python virtual environment"
  python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip

  if [ -f "$APP_DIR/requirements.txt" ]; then
    "$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
  else
    echo "requirements.txt was not found in $APP_DIR"
    exit 1
  fi

  log "Installing Playwright Chromium browser"
  "$APP_DIR/.venv/bin/python" -m playwright install chromium || true
}

write_env_example() {
  log "Checking .env"
  if [ ! -f "$APP_DIR/.env.example" ]; then
    cat > "$APP_DIR/.env.example" <<'ENVEOF'
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxxxx
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-5.5
ERROR_NOTIFY_SLACK=true
ENVEOF
  fi

  if [ ! -f "$APP_DIR/.env" ]; then
    echo "$APP_DIR/.env is missing."
    echo "Create it with:"
    echo "  cp $APP_DIR/.env.example $APP_DIR/.env"
    echo "  nano $APP_DIR/.env"
  else
    echo "$APP_DIR/.env exists. Secret values were not printed."
  fi
}

check_codex() {
  log "Checking Codex CLI"
  if command -v codex >/dev/null 2>&1; then
    codex --version || true
  else
    cat <<'MSG'
Codex CLI is not installed on this VPS yet.

After installing Codex CLI with the current official OpenAI Codex quickstart,
authenticate on the VPS with:

  codex login --device-auth
  codex doctor

Do not commit ~/.codex/auth.json. Treat it like a password.
MSG
  fi
}

enable_cron() {
  log "Enabling cron"
  sudo systemctl enable --now cron
  systemctl is-active cron
}

show_next_steps() {
  cat <<EOF

Setup finished.

Next commands to run:

1. Edit secrets if .env is missing:
   nano $APP_DIR/.env

2. Test the morning Slack script without sending:
   $APP_DIR/.venv/bin/python $APP_DIR/slack_test.py --dry-run

3. Send one Slack test:
   $APP_DIR/.venv/bin/python $APP_DIR/slack_test.py

4. Install cron template if you want to replace the current crontab:
   crontab $REPO_DIR/outputs/cron_vps_template.txt
   crontab -l

EOF
}

main() {
  require_ubuntu
  install_packages
  sync_repo
  sync_app_files
  setup_python
  write_env_example
  check_codex
  enable_cron
  show_next_steps
}

main "$@"

