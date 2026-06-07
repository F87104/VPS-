#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send a short job completion summary to Slack."""

from __future__ import annotations

import argparse
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def tail_text(path: Path, lines: int) -> str:
    if not path.exists():
        return "(log file not found)"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:]) if content else "(log file is empty)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="Substack自動実行")
    parser.add_argument("--status", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--tail-lines", type=int, default=25)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env(ENV_FILE)
    webhook_url = env.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL is missing in .env")
        return 1

    status = int(args.status)
    icon = "✅" if status == 0 else "⚠️"
    result = "成功" if status == 0 else "失敗"
    log_path = Path(args.log)
    log_tail = tail_text(log_path, args.tail_lines)
    if len(log_tail) > 2800:
        log_tail = log_tail[-2800:]

    message = (
        f"{icon} {args.title} 終了: {result}\n"
        f"exit_status: {status}\n"
        f"log: {log_path}\n\n"
        "```text\n"
        f"{log_tail}\n"
        "```"
    )

    response = requests.post(webhook_url, json={"text": message}, timeout=20)
    response.raise_for_status()
    if response.text.strip() != "ok":
        raise RuntimeError(f"Slack response was not ok: {response.text}")
    print("Slack completion notification sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
