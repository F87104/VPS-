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
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlencode

import requests
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - VPS has openai, fallback keeps the tool usable.
    OpenAI = None  # type: ignore[assignment]
from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


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
    "返信案",
    "大喜利返信案",
    "警告",
]


DEFAULT_CONFIG: dict[str, Any] = {
    "storage_state": "/home/ubuntu/prometheus/x_storage_state.json",
    "headless": True,
    "max_posts_per_query": 12,
    "max_total_posts": 40,
    "max_scrolls": 3,
    "article_timeout_ms": 5000,
    "max_scan_articles_per_scroll": 18,
    "block_heavy_assets": True,
    "min_likes": 300,
    "min_reposts": 30,
    "min_replies": 20,
    "min_views": 20000,
    "max_age_hours": 48,
    "slack_notify": True,
    "slack_webhook_url": "",
    "reply_generation": "auto",
    "reply_ai_max_posts": 8,
    "openai_api_key": "",
    "openai_model": "",
    "queries": [],
    "exclude_words": [],
    "reply_templates": [],
}


KNOWN_TERMS = [
    "投資枠",
    "ドル円",
    "米金利",
    "日経平均",
    "NASDAQ",
    "ナスダック",
    "S&P500",
    "NYダウ",
    "GOLD",
    "ゴールド",
    "原油",
    "CPI",
    "PPI",
    "FOMC",
    "FRB",
    "ECB",
    "ISM",
    "雇用統計",
    "半導体",
    "AI",
    "生成AI",
    "ChatGPT",
    "NVIDIA",
    "エヌビディア",
    "Apple",
    "OpenAI",
    "予約電話",
    "店主",
    "アニメ",
    "漫画",
    "集英社",
    "翻訳",
    "イラン",
    "中東",
    "物価",
    "副業",
    "資産形成",
]

GENERIC_TERMS = {
    "news",
    "online",
    "https",
    "http",
    "www",
    "com",
    "jp",
    "official",
    "gyt",
}

FORBIDDEN_REPLY_TERMS = [
    "買い",
    "売り",
    "買う",
    "売る",
    "買います",
    "売ります",
    "利確",
    "損切り",
    "エントリー",
    "ポジション",
    "ロット",
    "指値",
    "投資助言",
    "重要です",
    "示唆",
    "考えられます",
    "と言えるでしょう",
    "注目ですね",
]


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
    reply_draft: str
    oogiri_reply: str
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
            "返信案": self.reply_draft,
            "大喜利返信案": self.oogiri_reply,
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
    config["openai_api_key"] = (
        os.getenv("OPENAI_API_KEY")
        or env.get("OPENAI_API_KEY", "")
        or config.get("openai_api_key", "")
    ).strip()
    config["openai_model"] = (
        os.getenv("OPENAI_MODEL")
        or env.get("OPENAI_MODEL", "")
        or config.get("openai_model", "")
        or "gpt-5.5"
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


def trim_reply(text: str, limit: int = 90) -> str:
    text = normalize_space(text)
    text = re.sub(r"^(返信案|案)\s*[:：]\s*", "", text)
    text = text.strip("「」『』\"'` ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("、。,. ") + "…"


def extract_specific_terms(text: str, limit: int = 4) -> list[str]:
    text = unicodedata.normalize("NFKC", text)
    seen: set[str] = set()
    terms: list[str] = []

    def add(term: str) -> None:
        term = normalize_space(unicodedata.normalize("NFKC", term)).strip("、。,.!！?？:：()（）[]【】「」#")
        if len(term) < 2 or term.lower() in seen:
            return
        if term.lower() in GENERIC_TERMS:
            return
        if re.fullmatch(r"[A-Za-z]{2,}", term) and term.lower() in GENERIC_TERMS:
            return
        seen.add(term.lower())
        terms.append(term)

    for quoted in re.findall(r"「([^」]{2,32})」|『([^』]{2,32})』", text):
        add(next((item for item in quoted if item), ""))

    lower_text = text.lower()
    known_matches: list[tuple[int, str]] = []
    for term in KNOWN_TERMS:
        start = lower_text.find(term.lower())
        if start >= 0:
            known_matches.append((start, term))
    for _, term in sorted(known_matches, key=lambda item: item[0]):
        add(term)

    for match in re.findall(r"#[0-9A-Za-zぁ-んァ-ヶ一-龥ー_]+", text):
        add(match)

    for match in re.findall(r"\$[A-Za-z]{1,8}|[A-Z][A-Z0-9&.]{1,10}", text):
        add(match)

    number_pattern = (
        r"[0-9][0-9,.]*"
        r"(?:円|ドル|%|％|万人|万|億|兆|件|年|月|日|時|分|bp|bps|ポイント|pt)?"
    )
    for match in re.findall(number_pattern, text):
        add(match)

    return terms[:limit]


def clean_reply_draft(text: str) -> str:
    text = normalize_space(text.replace("\n", " "))
    text = re.sub(r"^[-・\d.）) ]+", "", text)
    text = text.translate(str.maketrans("", "", "「」『』"))
    return trim_reply(text, 96)


def reply_has_forbidden_term(reply: str) -> bool:
    return any(term in reply for term in FORBIDDEN_REPLY_TERMS)


def reply_uses_post_term(reply: str, post_text: str) -> bool:
    terms = extract_specific_terms(post_text, limit=6)
    if not terms:
        return True
    return any(term in reply for term in terms)


def make_rule_reply(text: str, label: str, angle: str) -> str:
    terms = extract_specific_terms(text)
    term = terms[0] if terms else "この投稿"
    label_text = f"{label} {angle}"
    label_only = label

    if any(key in label_text for key in ["AI", "ChatGPT", "テック"]):
        candidates = [
            f"{term}は機能名だけで終わらせず、何の手間が減るのかまで見たいです😺",
            f"{term}の話、すごいで止めずに毎日の作業のどこが短くなるかまで見たいですね🌸",
            f"{term}に人が集まる時は、派手さより使う人の時間がどう浮くかを見たいです🐻",
        ]
    elif any(key in label_only for key in ["ドル円", "相場", "投資", "米国株", "半導体"]):
        candidates = [
            f"{term}に反応が集まる時は、見出しより先に動いた資金の向きを見たくなります🐻",
            f"{term}の話は、数字だけでなく反応が速かった場所まで拾いたいですね🌸",
            f"{term}で人が動く時って、値動きの前にどの材料が刺さったかが出やすいですね😺",
        ]
    elif any(key in label_only for key in ["お金", "働き方", "資産", "副業"]):
        candidates = [
            f"{term}の話、読むだけで終わらせず今日の手元で1つ変えるなら何かを考えたいです🥰",
            f"{term}は大きく見えるけど、家計や時間の置き方に落とすと急に現実味が出ますね🌸",
            f"{term}で人が集まる時は、誰がどんな行動を増やしたかまで見たいです🐻",
        ]
    elif any(key in label_only for key in ["暮らし", "物価"]):
        candidates = [
            f"{term}の話は、家計のどこにしわ寄せが出るかまで見ると残るものがあります🌸",
            f"{term}に反応が集まる時は、手に取る物より減らした物に本音が出そうですね😺",
            f"{term}はニュースより、生活の中で先に削られる場所まで見たいです🐻",
        ]
    else:
        candidates = [
            f"{term}に人が集まった理由を、見出しより反応の順番で見たいです🐻",
            f"{term}の話、何が人の行動を動かしたのかまで拾うと残りそうです🌸",
            f"{term}で伸びる投稿は、言葉よりその後に増えた行動を見たいですね😺",
        ]

    seed = sum(ord(char) for char in text[:80]) + len(label)
    return clean_reply_draft(candidates[seed % len(candidates)])


def make_reply_draft(text: str, angle: str, templates: list[str], label: str = "") -> str:
    return make_rule_reply(text, label, angle)


def make_oogiri_reply(text: str, label: str, angle: str) -> str:
    terms = extract_specific_terms(text)
    term = terms[0] if terms else "この話題"
    label_text = f"{label} {angle}"

    if "予約電話" in terms or "店主" in terms:
        candidates = [
            "AIの予約電話、店主さんの電話だけ先に未来へ強制アップデートされてますね😺",
            "予約電話が鳴り止まない店、もうAIだけで満席確認の朝礼してそうです🌸",
        ]
    elif any(key in label_text for key in ["AI", "ChatGPT", "テック"]):
        candidates = [
            f"{term}、便利すぎて人間側のあとでやるが先に絶滅しそうです😺",
            f"{term}の進化、そろそろ私の後回し癖にもアップデート配布してほしいです🐻",
            f"{term}、機能追加のたびに人間の言い訳フォルダが圧縮されていきますね🌸",
        ]
    elif any(key in label for key in ["ドル円", "相場", "投資", "米国株", "半導体"]):
        candidates = [
            f"{term}、名前の圧だけなら市場の朝礼で一番声が大きいタイプですね🐻",
            f"{term}に反応が集まる時、チャートより先にタイムラインが走り出しますね😺",
            f"{term}、見出しだけで市場の椅子取りゲームが始まりそうです🌸",
        ]
    elif any(key in label for key in ["お金", "働き方", "資産", "副業"]):
        candidates = [
            f"{term}、夢は大きいのに最初の一歩だけ毎回靴ひも結び直してますね🥰",
            f"{term}の話、やる気より先にメモ帳を開いた人から進みそうです🌸",
        ]
    else:
        candidates = [
            f"{term}、タイムラインの会議室で一番発言権を持ってる感じがしますね😺",
            f"{term}、伸びる理由がある投稿はコメント欄まで含めて本編ですね🌸",
        ]

    seed = sum(ord(char) for char in text[-80:]) + len(label) * 3
    return clean_reply_draft(candidates[seed % len(candidates)])


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
        }""",
        timeout=int(config.get("article_timeout_ms", 5000)),
    )
    text = normalize_space(data.get("text", ""))
    links = data.get("links", [])
    if not text or not links:
        return None
    url = next((link for link in links if "/status/" in link), links[0])
    metrics = parse_metrics(data.get("labels", []), text)
    warnings = warning_words(text, list(config.get("exclude_words", [])))
    reply_draft = make_reply_draft(
        text,
        reply_angle,
        list(config.get("reply_templates", [])),
        label,
    )
    oogiri_reply = make_oogiri_reply(text, label, reply_angle)
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
        reply_draft=reply_draft,
        oogiri_reply=oogiri_reply,
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
    for attempt in range(1, 3):
        try:
            page.goto(search_url, wait_until="commit", timeout=45_000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except PlaywrightTimeoutError:
                logging.warning("X search domcontentloaded timeout label=%s attempt=%s", label, attempt)
            break
        except PlaywrightTimeoutError as exc:
            logging.warning("X search navigation timeout label=%s attempt=%s error=%s", label, attempt, exc)
            if attempt >= 2:
                logging.error("Skip X search label=%s after repeated navigation timeout", label)
                return []
            try:
                page.goto("about:blank", wait_until="commit", timeout=10_000)
            except Exception:
                pass
            page.wait_for_timeout(5_000)
    page.wait_for_timeout(4_000)

    posts: list[BuzzPost] = []
    seen: set[str] = set()
    for scroll_index in range(max_scrolls + 1):
        articles = page.locator('article[data-testid="tweet"]')
        count = min(
            articles.count(),
            int(config.get("max_scan_articles_per_scroll", max_posts + 8)),
        )
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
    if bool(config.get("block_heavy_assets", True)):
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font"}
            else route.continue_(),
        )
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
                reply_draft=make_reply_draft(
                    row["text"],
                    row["reply_angle"],
                    list(config.get("reply_templates", [])),
                    row["label"],
                ),
                oogiri_reply=make_oogiri_reply(row["text"], row["label"], row["reply_angle"]),
                warnings=[],
            )
        )
    return posts


def parse_json_array(text: str) -> list[Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ValueError("OpenAI reply draft response was not a JSON array")
    return parsed


def build_reply_prompt_payload(posts: list[BuzzPost]) -> str:
    rows = []
    for index, post in enumerate(posts, 1):
        rows.append(
            {
                "index": index,
                "category": post.label,
                "author": post.author[:80],
                "text": post.text[:420],
                "reply_angle": post.reply_angle,
                "metrics": {
                    "replies": post.replies,
                    "reposts": post.reposts,
                    "likes": post.likes,
                    "views": post.views,
                },
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def apply_ai_reply_drafts(posts: list[BuzzPost], config: dict[str, Any]) -> None:
    mode = str(config.get("reply_generation", "auto")).lower()
    if mode in {"off", "false", "rule", "rules"}:
        logging.info("AI reply draft generation disabled mode=%s", mode)
        return
    api_key = str(config.get("openai_api_key", "")).strip()
    if not api_key:
        logging.info("OpenAI API key missing; keep rule-based reply drafts")
        return
    if OpenAI is None:
        logging.info("openai package missing; keep rule-based reply drafts")
        return

    max_posts = max(0, int(config.get("reply_ai_max_posts", 8)))
    target_posts = posts[:max_posts]
    if not target_posts:
        return

    instructions = """
あなたは投資家FのX返信案を作る編集者です。
目的は「バズっている投稿に手動で返信するための下書き」です。自動返信はしません。

必ずJSON配列だけを返してください。
形式:
[
  {"index": 1, "reply": "...", "oogiri_reply": "..."},
  {"index": 2, "reply": "...", "oogiri_reply": "..."}
]

返信案のルール:
- 1投稿につき返信案は1つ。
- 45〜95字。
- 元ポスト内の固有名詞・数字・テーマ語を必ず1つ入れる。
- ただの共感、きれいごと、汎用コメントにしない。
- 読んだ人の見方が1つ増える言葉にする。
- 投資助言、売買指示、F自身のポジション、注文の話は書かない。
- `買い` `売り` `利確` `損切り` `エントリー` `ポジション` `ロット` `指値` は使わない。
- `重要です` `示唆します` `考えられます` `と言えるでしょう` のAI文体は禁止。
- 口調は自然な日本語。少し親しみやすく、でも意味のある一言にする。
- 絵文字は0〜1個。使うなら 😺 🐻 🐻‍❄️ 🥰 🌈 🌸 から1つだけ。
- 文末が絵文字の場合、絵文字の直後に句点を置かない。
- 引用符の片側だけを残さない。迷ったら引用符は使わない。
- 返信先に失礼な言い方、断定、上から目線は避ける。

大喜利返信案のルール:
- `oogiri_reply` はクスッと笑える短文にする。
- 45〜90字。
- 元ポスト内の固有名詞・数字・テーマ語を必ず1つ入れる。
- 返信先や当事者を馬鹿にしない。皮肉を強くしない。
- ただのダジャレだけで終わらせない。ニュースや仕組みへの見方が1つ残る文にする。
- 投資助言、売買指示、F自身のポジション、注文の話は書かない。
- 禁止語と絵文字ルールは通常返信案と同じ。
""".strip()

    try:
        client = OpenAI(api_key=api_key)  # type: ignore[operator]
        response = client.responses.create(
            model=str(config.get("openai_model", "gpt-5.5")),
            instructions=instructions,
            input=build_reply_prompt_payload(target_posts),
            store=False,
        )
        rows = parse_json_array(response.output_text)
    except Exception as exc:  # noqa: BLE001
        logging.exception("OpenAI reply draft generation failed; keep rule-based drafts. error=%s", exc)
        return

    by_index: dict[int, str] = {}
    oogiri_by_index: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index", 0))
        except (TypeError, ValueError):
            continue
        reply = clean_reply_draft(str(row.get("reply", "")))
        if not reply:
            continue
        by_index[index] = reply
        oogiri_reply = clean_reply_draft(str(row.get("oogiri_reply", "")))
        if oogiri_reply:
            oogiri_by_index[index] = oogiri_reply

    updated = 0
    oogiri_updated = 0
    for index, post in enumerate(target_posts, 1):
        reply = by_index.get(index, "")
        if reply:
            if reply_has_forbidden_term(reply) or not reply_uses_post_term(reply, post.text):
                logging.info("Rejected weak AI reply draft index=%s reply=%s", index, reply)
            else:
                post.reply_draft = reply
                updated += 1

        oogiri_reply = oogiri_by_index.get(index, "")
        if oogiri_reply:
            if reply_has_forbidden_term(oogiri_reply) or not reply_uses_post_term(oogiri_reply, post.text):
                logging.info("Rejected weak AI oogiri reply index=%s reply=%s", index, oogiri_reply)
            else:
                post.oogiri_reply = oogiri_reply
                oogiri_updated += 1
    logging.info(
        "AI reply drafts applied count=%s oogiri_count=%s target=%s",
        updated,
        oogiri_updated,
        len(target_posts),
    )


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
                f"- 返信案: {post.reply_draft}",
                f"- 大喜利返信案: {post.oogiri_reply}",
                f"- 警告: {', '.join(post.warnings) if post.warnings else 'なし'}",
                "",
                "通常返信コピー用:",
                "```text",
                post.reply_draft,
                "```",
                "",
                "大喜利返信コピー用:",
                "```text",
                post.oogiri_reply,
                "```",
                "",
                "元ポスト:",
                "```text",
                post.text[:500],
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def tweet_id_from_url(url: str) -> str:
    match = re.search(r"/status/([0-9]+)", url)
    return match.group(1) if match else ""


def reply_intent_url(post: BuzzPost, reply_text: str) -> str:
    tweet_id = tweet_id_from_url(post.url)
    params = {"text": reply_text}
    if tweet_id:
        params["in_reply_to"] = tweet_id
    return f"https://twitter.com/intent/tweet?{urlencode(params)}"


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
                "通常案:",
                "```",
                post.reply_draft,
                "```",
                "大喜利案:",
                "```",
                post.oogiri_reply,
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def truncate_slack_text(text: str, limit: int = 260) -> str:
    text = normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def slack_blocks_payload(posts: list[BuzzPost], markdown_path: Path) -> dict[str, Any]:
    top_posts = posts[:6]
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Xバズ返信候補", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"候補数: *{len(posts)}*\n"
                    f"レポート: `{markdown_path}`\n"
                    "ボタンは投稿しません。Xの返信入力画面を開くだけです。"
                ),
            },
        },
    ]

    for index, post in enumerate(top_posts, 1):
        snippet = truncate_slack_text(post.text, 180)
        normal = truncate_slack_text(post.reply_draft, 220)
        oogiri = truncate_slack_text(post.oogiri_reply, 220)
        blocks.extend(
            [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{index}. [{post.label}] score={post.score}*\n"
                            f"返信/リポスト/いいね/表示: "
                            f"{post.replies}/{post.reposts}/{post.likes}/{post.views}\n"
                            f"<{post.url}|元ポストを開く>\n"
                            f"{snippet}"
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*通常案 コピー用*\n```{normal}```",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*大喜利案 コピー用*\n```{oogiri}```",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "通常で返信", "emoji": True},
                            "url": reply_intent_url(post, normal),
                            "action_id": f"reply_normal_{index}",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "大喜利で返信", "emoji": True},
                            "url": reply_intent_url(post, oogiri),
                            "action_id": f"reply_oogiri_{index}",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "元ポスト", "emoji": True},
                            "url": post.url,
                            "action_id": f"open_post_{index}",
                        },
                    ],
                },
            ]
        )

    return {"text": slack_message(posts, markdown_path), "blocks": blocks[:50]}


def send_slack(config: dict[str, Any], message: str | dict[str, Any]) -> None:
    webhook_url = str(config.get("slack_webhook_url", "")).strip()
    if not webhook_url:
        logging.info("Slack webhook missing; skip notification")
        return
    payload = message if isinstance(message, dict) else {"text": message}
    response = requests.post(webhook_url, json=payload, timeout=20)
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
            try:
                posts = collect_posts_from_search(page, query_conf, config)
            except Exception as exc:  # noqa: BLE001
                logging.exception(
                    "Failed query label=%s; continuing with next query. error=%s",
                    query_conf.get("label", ""),
                    exc,
                )
                continue
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
    parser.add_argument("--rule-replies", action="store_true", help="Use rule-based reply drafts without OpenAI")
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
    if args.rule_replies:
        config["reply_generation"] = "rule"

    if args.test_slack:
        send_slack(config, "Xバズ返信候補テスト: Slack通知OK")
        print("Slack test sent")
        return 0

    logging.info("Run started sample=%s dry_run=%s", args.sample, args.dry_run)
    posts = sample_posts(config) if args.sample else collect_live(config, args.query_label)
    posts.sort(key=lambda post: post.score, reverse=True)
    apply_ai_reply_drafts(posts, config)
    csv_path = write_csv(posts)
    markdown_path = write_markdown(posts)
    logging.info("Run finished posts=%s csv=%s md=%s", len(posts), csv_path, markdown_path)

    if not args.dry_run and bool(config.get("slack_notify", True)):
        send_slack(config, slack_blocks_payload(posts, markdown_path))
        logging.info("Slack notification sent")
    else:
        logging.info("Slack notification skipped")

    print(f"CSV saved: {csv_path}")
    print(f"Markdown saved: {markdown_path}")
    print(f"Candidates: {len(posts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
