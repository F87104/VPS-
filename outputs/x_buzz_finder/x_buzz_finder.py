#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find high-engagement X posts worth replying to.

This tool only reads visible X search results and produces candidate lists.
It does not post replies, like, follow, or send any action to X.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
from playwright.sync_api import BrowserContext, Page, sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "x_buzz_finder.log"
JST = timezone.utc
X_BASE = "https://x.com"


CSV_FIELDS = [
    "カテゴリ",
    "投稿者",
    "本文",
    "返信",
    "リポスト",
    "いいね",
    "表示",
    "投稿時刻",
    "URL",
    "スコア",
    "返信の切り口",
    "返信メモ",
    "警告",
]


DEFAULT_CONFIG: dict[str, Any] = {
    "storage_state": "/home/ubuntu/prometheus/x_storage_state.json",
    "headless": True,
    "max_posts_per_query": 12,
    "max_total_posts": 40,
    "max_scrolls": 3,
    "min_likes": 300,
    "min_reposts": 30,
    "min_replies": 20,
    "min_views": 20000,
    "max_age_hours": 48,
    "slack_notify": True,
    "slack_webhook_url": "",
    "queries": [],
    "exclude_words": [],
    "reply_templates": [],
}


def load_env_files(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class BuzzPost:
    label: str
    author: str
    text: str
    replies: int
    reposts: int
    likes: int
    views: int
    posted_at: str
    url: str
    score: int
    reply_angle: str
    reply_note: str
    warnings: list[str]

    def to_csv_row(self) -> dict[str, str]:
        return {
            "カテゴリ": self.label,
            "投稿者": self.author,
            "本文": self.text,
            "返信": str(self.replies),
            "リポスト": str(self.reposts),
            "いいね": str(self.likes),
            "表示": str(self.views),
            "投稿時刻": self.posted_at,
            "URL": self.url,
            "スコア": str(self.score),
            "返信の切り口": self.reply_angle,
            "返信メモ": self.reply_note,
            "警告": ", ".join(self.warnings),
        }


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    config = DEFAULT_CONFIG | raw
    env = load_env_files([BASE_DIR / ".env", BASE_DIR.parent / ".env"])
    config["slack_webhook_url"] = (
        os.getenv("SLACK_WEBHOOK_URL")
        or env.get("SLACK_WEBHOOK_URL", "")
        or config.get("slack_webhook_url", "")
    ).strip()
    return config


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_count(raw: str) -> int:
    text = normalize_space(str(raw)).replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(万|億|K|M|B|k|m|b)?", text)
    if not match:
        return 0
    value = float(match.group(1))
    suffix = match.group(2) or ""
    multipliers = {
        "万": 10_000,
        "億": 100_000_000,
        "K": 1_000,
        "k": 1_000,
        "M": 1_000_000,
        "m": 1_000_000,
        "B": 1_000_000_000,
        "b": 1_000_000_000,
    }
    return int(value * multipliers.get(suffix, 1))


def metric_from_labels(labels: list[str], needles: list[str]) -> int:
    best = 0
    for label in labels:
        lower = label.lower()
        if not any(needle.lower() in lower for needle in needles):
            continue
        parsed = metric_from_text(label, needles)
        if parsed:
            best = max(best, parsed)
            continue
        numbers = re.findall(r"[0-9][0-9,.]*\s*(?:万|億|K|M|B|k|m|b)?", label)
        if numbers:
            best = max(best, parse_count(numbers[0]))
    return best


def metric_from_text(text: str, words: list[str]) -> int:
    best = 0
    count_pattern = r"([0-9][0-9,.]*\s*(?:万|億|K|M|B|k|m|b)?)"
    for word in words:
        patterns = [
            rf"{count_pattern}\s*(?:件の)?\s*{re.escape(word)}",
            rf"{re.escape(word)}\s*{count_pattern}",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                best = max(best, parse_count(match.group(1)))
    return best


def parse_metrics(labels: list[str], text: str) -> dict[str, int]:
    return {
        "replies": metric_from_labels(labels, ["reply", "replies", "返信"])
        or metric_from_text(text, ["返信", "Replies", "Reply"]),
        "reposts": metric_from_labels(labels, ["repost", "retweet", "リポスト"])
        or metric_from_text(text, ["リポスト", "Reposts", "Repost", "Retweets", "Retweet"]),
        "likes": metric_from_labels(labels, ["like", "likes", "いいね"])
        or metric_from_text(text, ["いいね", "Likes", "Like"]),
        "views": metric_from_labels(labels, ["view", "views", "表示"])
        or metric_from_text(text, ["表示", "Views", "View"]),
    }


def post_age_hours(posted_at: str) -> float | None:
    if not posted_at:
        return None
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600)


def score_post(metrics: dict[str, int], posted_at: str) -> int:
    score = (
        metrics["likes"] * 1.0
        + metrics["reposts"] * 2.2
        + metrics["replies"] * 1.4
        + metrics["views"] * 0.025
    )
    age = post_age_hours(posted_at)
    if age is not None:
        if age <= 6:
            score *= 1.35
        elif age <= 24:
            score *= 1.15
        elif age > 72:
            score *= 0.65
    return int(score)


def warning_words(text: str, words: list[str]) -> list[str]:
    lower = text.lower()
    return [word for word in words if word.lower() in lower]


def should_keep(post: BuzzPost, config: dict[str, Any]) -> bool:
    if post.warnings:
        return False
    if not post.url or "/status/" not in post.url:
        return False
    if "返信先:" in post.text or "Replying to" in post.text:
        return False
    age = post_age_hours(post.posted_at)
    if age is not None and age > float(config.get("max_age_hours", 48)):
        return False
    return (
        post.likes >= int(config.get("min_likes", 300))
        or post.reposts >= int(config.get("min_reposts", 30))
        or post.replies >= int(config.get("min_replies", 20))
        or post.views >= int(config.get("min_views", 20000))
    )


def make_reply_note(text: str, angle: str, templates: list[str]) -> str:
    template = random.choice(templates) if templates else ""
    if not template:
        template = "事実を1つ拾って、読者が今日見直せる行動に落とす返信が合いそうです。"
    if len(text) > 90:
        text = text[:87].rstrip() + "..."
    return f"{template} 切り口: {angle}"


def build_search_url(query: str, mode: str = "top") -> str:
    return f"{X_BASE}/search?q={quote_plus(query)}&src=typed_query&f={mode}"


def extract_article(article: Any, label: str, reply_angle: str, config: dict[str, Any]) -> BuzzPost | None:
    data = article.evaluate(
        """(el) => {
            const text = el.innerText || "";
            const labels = Array.from(el.querySelectorAll("[aria-label]"))
                .map(node => node.getAttribute("aria-label") || "")
                .filter(Boolean);
            const links = Array.from(el.querySelectorAll('a[href*="/status/"]'))
                .map(a => a.href)
                .filter(Boolean);
            const timeNode = el.querySelector("time");
            const authorNode = el.querySelector('[data-testid="User-Name"]');
            return {
                text,
                labels,
                links,
                postedAt: timeNode ? timeNode.getAttribute("datetime") : "",
                author: authorNode ? authorNode.innerText : ""
            };
        }"""
    )
    text = normalize_space(data.get("text", ""))
    links = data.get("links", [])
    if not text or not links:
        return None
    url = next((link for link in links if "/status/" in link), links[0])
    metrics = parse_metrics(data.get("labels", []), text)
    warnings = warning_words(text, list(config.get("exclude_words", [])))
    reply_note = make_reply_note(
        text,
        reply_angle,
        list(config.get("reply_templates", [])),
    )
    return BuzzPost(
        label=label,
        author=normalize_space(data.get("author", "")).replace("\n", " "),
        text=text[:600],
        replies=metrics["replies"],
        reposts=metrics["reposts"],
        likes=metrics["likes"],
        views=metrics["views"],
        posted_at=data.get("postedAt", ""),
        url=url,
        score=score_post(metrics, data.get("postedAt", "")),
        reply_angle=reply_angle,
        reply_note=reply_note,
        warnings=warnings,
    )


def collect_posts_from_search(page: Page, query_conf: dict[str, Any], config: dict[str, Any]) -> list[BuzzPost]:
    label = str(query_conf.get("label", "未分類"))
    query = str(query_conf.get("query", ""))
    reply_angle = str(query_conf.get("reply_angle", "事実を1つ拾って、読者が考えやすい一言にする"))
    max_posts = int(config.get("max_posts_per_query", 12))
    max_scrolls = int(config.get("max_scrolls", 3))
    search_url = build_search_url(query, str(query_conf.get("mode", "top")))

    logging.info("Fetching X search label=%s url=%s", label, search_url)
    page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)

    posts: list[BuzzPost] = []
    seen: set[str] = set()
    for scroll_index in range(max_scrolls + 1):
        articles = page.locator('article[data-testid="tweet"]')
        count = min(articles.count(), 40)
        logging.info("label=%s scroll=%s visible_articles=%s", label, scroll_index, count)
        for index in range(count):
            try:
                post = extract_article(articles.nth(index), label, reply_angle, config)
            except Exception as exc:  # noqa: BLE001
                logging.warning("Failed to parse article label=%s index=%s error=%s", label, index, exc)
                continue
            if not post or post.url in seen:
                continue
            seen.add(post.url)
            if should_keep(post, config):
                posts.append(post)
            if len(posts) >= max_posts:
                break
        if len(posts) >= max_posts:
            break
        page.mouse.wheel(0, 2200)
        page.wait_for_timeout(random.randint(2500, 5500))
    return posts


def make_context(config: dict[str, Any]) -> BrowserContext:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=bool(config.get("headless", True)))
    storage_state = str(config.get("storage_state", ""))
    kwargs: dict[str, Any] = {
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
        "viewport": {"width": 1440, "height": 1200},
    }
    if storage_state and Path(storage_state).exists():
        kwargs["storage_state"] = storage_state
    context = browser.new_context(**kwargs)
    context._x_buzz_playwright = playwright  # type: ignore[attr-defined]
    return context


def close_context(context: BrowserContext) -> None:
    browser = context.browser
    playwright = getattr(context, "_x_buzz_playwright", None)
    context.close()
    browser.close()
    if playwright:
        playwright.stop()


def sample_posts(config: dict[str, Any]) -> list[BuzzPost]:
    rows = [
        {
            "label": "相場・投資",
            "author": "sample_user",
            "text": "ドル円と米金利が同時に動いていて、株クラの反応がかなり速いです。",
            "replies": 68,
            "reposts": 420,
            "likes": 3200,
            "views": 280000,
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "url": "https://x.com/sample/status/1000000000000000000",
            "reply_angle": "数字より資金がどこへ動いたかを見る",
        },
        {
            "label": "AI・テック",
            "author": "sample_ai",
            "text": "生成AIの新機能で、資料作成の時間が一気に短くなりそうという話題。",
            "replies": 44,
            "reposts": 280,
            "likes": 1900,
            "views": 180000,
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "url": "https://x.com/sample/status/1000000000000000001",
            "reply_angle": "技術より仕事の手順がどう短くなるかを見る",
        },
    ]
    posts: list[BuzzPost] = []
    for row in rows:
        metrics = {
            "replies": row["replies"],
            "reposts": row["reposts"],
            "likes": row["likes"],
            "views": row["views"],
        }
        posts.append(
            BuzzPost(
                label=row["label"],
                author=row["author"],
                text=row["text"],
                replies=row["replies"],
                reposts=row["reposts"],
                likes=row["likes"],
                views=row["views"],
                posted_at=row["posted_at"],
                url=row["url"],
                score=score_post(metrics, row["posted_at"]),
                reply_angle=row["reply_angle"],
                reply_note=make_reply_note(row["text"], row["reply_angle"], list(config.get("reply_templates", []))),
                warnings=[],
            )
        )
    return posts


def write_csv(posts: list[BuzzPost]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"x_buzz_candidates_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for post in posts:
            writer.writerow(post.to_csv_row())
    return path


def write_markdown(posts: list[BuzzPost]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"x_buzz_candidates_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    lines = [
        "# Xバズ返信候補",
        "",
        f"- 作成時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 候補数: {len(posts)}",
        "",
    ]
    for index, post in enumerate(posts, 1):
        lines.extend(
            [
                f"## {index}. {post.label}",
                "",
                f"- 投稿者: {post.author or '未取得'}",
                f"- スコア: {post.score}",
                f"- 返信/リポスト/いいね/表示: {post.replies} / {post.reposts} / {post.likes} / {post.views}",
                f"- 投稿時刻: {post.posted_at or '未取得'}",
                f"- URL: [Xで開く]({post.url})",
                f"- 返信の切り口: {post.reply_angle}",
                f"- 返信メモ: {post.reply_note}",
                f"- 警告: {', '.join(post.warnings) if post.warnings else 'なし'}",
                "",
                "```text",
                post.text[:500],
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def slack_message(posts: list[BuzzPost], markdown_path: Path) -> str:
    top_posts = posts[:8]
    lines = [
        "📣 Xバズ返信候補",
        f"候補数: {len(posts)}",
        f"レポート: {markdown_path}",
        "",
    ]
    for index, post in enumerate(top_posts, 1):
        text = post.text.replace("\n", " ")
        if len(text) > 90:
            text = text[:87] + "..."
        lines.extend(
            [
                f"{index}. [{post.label}] score={post.score}",
                f"返信/リポスト/いいね/表示: {post.replies}/{post.reposts}/{post.likes}/{post.views}",
                f"{text}",
                post.url,
                "",
            ]
        )
    return "\n".join(lines)


def send_slack(config: dict[str, Any], message: str) -> None:
    webhook_url = str(config.get("slack_webhook_url", "")).strip()
    if not webhook_url:
        logging.info("Slack webhook missing; skip notification")
        return
    response = requests.post(webhook_url, json={"text": message}, timeout=20)
    response.raise_for_status()
    if response.text.strip() != "ok":
        raise RuntimeError(f"Slack response was not ok: {response.text}")


def collect_live(config: dict[str, Any], only_query: str | None = None) -> list[BuzzPost]:
    queries = list(config.get("queries", []))
    if only_query:
        queries = [query for query in queries if str(query.get("label", "")) == only_query]
    context = make_context(config)
    page = context.new_page()
    all_posts: list[BuzzPost] = []
    seen: set[str] = set()
    try:
        for query_conf in queries:
            posts = collect_posts_from_search(page, query_conf, config)
            for post in posts:
                if post.url in seen:
                    continue
                seen.add(post.url)
                all_posts.append(post)
            if len(all_posts) >= int(config.get("max_total_posts", 40)):
                break
            time.sleep(random.uniform(4, 9))
    finally:
        close_context(context)
    all_posts.sort(key=lambda post: post.score, reverse=True)
    return all_posts[: int(config.get("max_total_posts", 40))]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="X buzz reply candidate finder")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"))
    parser.add_argument("--sample", action="store_true", help="Use sample posts without opening X")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Slack")
    parser.add_argument("--headless", action="store_true", help="Force headless browser")
    parser.add_argument("--headed", action="store_true", help="Force visible browser")
    parser.add_argument("--query-label", default=None, help="Run only one query label from config")
    parser.add_argument("--storage-state", default=None, help="Override Playwright storage_state path")
    parser.add_argument("--test-slack", action="store_true", help="Send a Slack test message")
    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()
    config = load_config(Path(args.config))
    if args.headless:
        config["headless"] = True
    if args.headed:
        config["headless"] = False
    if args.storage_state:
        config["storage_state"] = args.storage_state

    if args.test_slack:
        send_slack(config, "Xバズ返信候補テスト: Slack通知OK")
        print("Slack test sent")
        return 0

    logging.info("Run started sample=%s dry_run=%s", args.sample, args.dry_run)
    posts = sample_posts(config) if args.sample else collect_live(config, args.query_label)
    posts.sort(key=lambda post: post.score, reverse=True)
    csv_path = write_csv(posts)
    markdown_path = write_markdown(posts)
    logging.info("Run finished posts=%s csv=%s md=%s", len(posts), csv_path, markdown_path)

    if not args.dry_run and bool(config.get("slack_notify", True)):
        send_slack(config, slack_message(posts, markdown_path))
        logging.info("Slack notification sent")
    else:
        logging.info("Slack notification skipped")

    print(f"CSV saved: {csv_path}")
    print(f"Markdown saved: {markdown_path}")
    print(f"Candidates: {len(posts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
