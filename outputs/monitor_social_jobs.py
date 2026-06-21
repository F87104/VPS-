#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monitor X/note automation health and notify Slack only on anomalies."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
ENV_FILE = BASE_DIR / ".env"
STATE_FILE = LOG_DIR / "social_jobs_monitor_state.json"


BAD_PATTERNS = [
    "storage_state=missing",
    "[ERROR]",
    "ERROR",
    "Traceback",
    "Fatal error",
    "Timeout",
    "No such file or directory",
    "No module named",
    "ログインが必要",
    "ログインしてください",
    "headless実行中にXログイン",
    "storage_stateが見つかりません",
    "storage_state not found",
    "note login prompt",
]


@dataclass(frozen=True)
class Job:
    key: str
    name: str
    last_log: Path
    storage_state: Path
    schedule: str
    max_age_hours: float = 14.0


JOBS = [
    Job(
        key="x",
        name="Xいいね・フォロー",
        last_log=BASE_DIR / "logs" / "x_daily_last.log",
        storage_state=Path("/home/ubuntu/prometheus/x_storage_state.json"),
        schedule="7:00 / 12:30 / 19:00",
    ),
    Job(
        key="note",
        name="noteスキ・フォロー",
        last_log=BASE_DIR / "logs" / "note_daily_last.log",
        storage_state=Path("/home/ubuntu/prometheus/note_storage_state.json"),
        schedule="8:00 / 13:00 / 20:00",
    ),
]


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


def tail_text(path: Path, lines: int = 18) -> str:
    if not path.exists():
        return "(log file not found)"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:]) if content else "(log file is empty)"


def file_age_hours(path: Path) -> float | None:
    if not path.exists():
        return None
    modified = datetime.fromtimestamp(path.stat().st_mtime)
    return max(0.0, (datetime.now() - modified).total_seconds() / 3600)


def load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict[str, str]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def check_job(job: Job) -> list[str]:
    issues: list[str] = []
    if not job.storage_state.exists():
        issues.append(f"{job.name}: storage_stateがありません: {job.storage_state}")

    age = file_age_hours(job.last_log)
    if age is None:
        issues.append(f"{job.name}: last logがありません: {job.last_log}")
        return issues
    if age > job.max_age_hours:
        issues.append(
            f"{job.name}: last logが古いです。最終更新から{age:.1f}時間経過 / 想定 {job.schedule}"
        )

    text = job.last_log.read_text(encoding="utf-8", errors="replace")
    if "exit status=0" not in text:
        issues.append(f"{job.name}: last logに exit status=0 がありません")
    for pattern in BAD_PATTERNS:
        if pattern.lower() in text.lower():
            issues.append(f"{job.name}: 異常らしい文言を検出: {pattern}")
            break
    return issues


def make_fingerprint(issues: list[str]) -> str:
    payload = "\n".join(sorted(issues))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def send_slack(webhook_url: str, issues: list[str]) -> None:
    tails = []
    for job in JOBS:
        tails.append(f"--- {job.name}: {job.last_log} ---\n{tail_text(job.last_log)}")
    message = (
        "⚠️ X/note自動実行 監視アラート\n"
        "通常のいいね結果通知は止めています。異常候補だけ通知しています。\n\n"
        "検出内容:\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + "\n\n```text\n"
        + "\n\n".join(tails)
        + "\n```"
    )
    if len(message) > 3600:
        message = message[:3500] + "\n...（長いため省略）"
    response = requests.post(webhook_url, json={"text": message}, timeout=20)
    response.raise_for_status()
    if response.text.strip() != "ok":
        raise RuntimeError(f"Slack response was not ok: {response.text}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor X/note automation health")
    parser.add_argument("--dry-run", action="store_true", help="Print result without Slack")
    parser.add_argument("--force", action="store_true", help="Send even if same alert was sent before")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    issues: list[str] = []
    for job in JOBS:
        issues.extend(check_job(job))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = load_state()
    if not issues:
        state.pop("active_fingerprint", None)
        state["last_ok_at"] = now
        save_state(state)
        print(f"{now} OK: monitored jobs healthy")
        return 0

    fingerprint = make_fingerprint(issues)
    print(f"{now} ALERT: {len(issues)} issue(s)")
    for issue in issues:
        print(f"- {issue}")

    if args.dry_run:
        return 1

    webhook_url = load_env(ENV_FILE).get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL is missing; cannot notify")
        return 2

    if args.force or state.get("active_fingerprint") != fingerprint:
        send_slack(webhook_url, issues)
        state["active_fingerprint"] = fingerprint
        state["last_alert_at"] = now
        save_state(state)
        print("Slack anomaly notification sent")
    else:
        print("Same anomaly already notified; skip Slack")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
