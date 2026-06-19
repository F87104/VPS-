#!/bin/bash
set -u

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG_PATH="${CONFIG_PATH:-$BASE_DIR/config.json}"

cd "$BASE_DIR" || exit 1

"$PYTHON_BIN" mercari_deal_finder.py \
  --config "$CONFIG_PATH" \
  "$@"
