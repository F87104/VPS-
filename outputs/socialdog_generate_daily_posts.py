#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate three daily SocialDog draft posts in the F style."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "socialdog_generate_daily_posts.log"
HISTORY_FILE = LOG_DIR / "socialdog_post_history.json"
STYLE_GUIDE_FILE = BASE_DIR / "F_STYLE_GUIDE.md"
STRATEGY_FILE = BASE_DIR / "SOCIALDOG_3POST_STRATEGY.md"
JST = None

EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"
)
SIGNATURE = "投資家Fより💌"
F_BANNED_PHRASES = [
    "重要です",
    "考えられます",
    "示唆",
    "一方で",
    "しかしながら",
    "総じて",
    "結論として",
    "こんにちはFです",
    "こんにちは、Fです",
]
SLOT_EMOJI_FALLBACKS = {
    "朝": {
        "title": "＼おはようございます🐻🌈／",
        "inline": "✅",
        "ending": "😺",
    },
    "昼": {
        "title": "＼お昼のひとこと😺🌸／",
        "inline": "🥰",
        "ending": "🐻",
    },
    "夕": {
        "title": "＼NY前の点検🐻‍❄️🌈／",
        "inline": "✅",
        "ending": "😺",
    },
}


def load_market_module() -> Any:
    module_path = BASE_DIR / "slack_test.py"
    if not module_path.exists():
        module_path = BASE_DIR / "morning_market_check.py"
    if not module_path.exists():
        raise RuntimeError("slack_test.py または morning_market_check.py が見つかりません。")

    spec = importlib.util.spec_from_file_location("morning_market_check_runtime", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Python moduleとして読み込めません: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MARKET = load_market_module()
JST = MARKET.JST


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def read_text_if_exists(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_history(items: list[dict[str, Any]]) -> None:
    HISTORY_FILE.write_text(
        json.dumps(items[-120:], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_trend_candidates(max_items: int = 8) -> list[str]:
    queries = [
        "急上昇 OR 話題 OR トレンド",
        "AI OR 生成AI OR 投資 OR 為替 OR 円安 OR GOLD",
        "日常 OR 生活 OR 天気 OR 梅雨 OR コーヒー",
    ]
    terms: list[str] = []
    seen: set[str] = set()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 socialdog-draft-generator/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )

    for query in queries:
        url = (
            "https://news.google.com/rss/search"
            f"?q={quote_plus(query + ' when:1d')}&hl=ja&gl=JP&ceid=JP:ja"
        )
        try:
            response = session.get(url, timeout=15)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Trend candidate fetch failed: %s", exc)
            continue

        for entry in feed.entries[:6]:
            title = str(entry.get("title", "")).strip()
            title = re.sub(r"\s*[-－]\s*[^-－]+$", "", title)
            title = re.sub(r"[「」『』【】]", "", title)
            chunks = re.split(r"[、。・：:／/ 　]", title)
            for chunk in chunks:
                chunk = chunk.strip()
                if 2 <= len(chunk) <= 14 and chunk not in seen:
                    if any(noise in chunk for noise in ("ニュース", "ライブ", "動画", "一覧")):
                        continue
                    seen.add(chunk)
                    terms.append(chunk)
                if len(terms) >= max_items:
                    return terms
    return terms


def format_json_for_prompt(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_posts_json(text: str) -> list[dict[str, str]]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)
    if match:
        text = match.group(0)
    data = json.loads(text)
    if not isinstance(data, list):
        raise RuntimeError("OpenAI response is not a list.")

    posts: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        slot = str(item.get("slot", "")).strip()
        theme = str(item.get("theme", "")).strip()
        text_value = str(item.get("text", "")).strip()
        if not slot or not text_value:
            continue
        text_value = normalize_signature(text_value)
        posts.append({"slot": slot, "theme": theme, "text": text_value})
    if len(posts) != 3:
        raise RuntimeError(f"3 posts expected, got {len(posts)}")
    return posts


def count_emoji(text: str) -> int:
    return len(EMOJI_RE.findall(text))


def normalize_signature(text: str) -> str:
    text = re.sub(r"投資家Fより\s*💌?", SIGNATURE, text.rstrip())
    if not text.endswith(SIGNATURE):
        text = text.rstrip() + f"\n\n{SIGNATURE}"
    return text


def remove_banned_phrases(text: str) -> str:
    replacements = {
        "重要です": "見ておきたいです",
        "考えられます": "かもしれません",
        "示唆": "サイン",
        "一方で": "ただ",
        "しかしながら": "ただ",
        "総じて": "ざっくり言うと",
        "結論として": "Fはこうします",
        "こんにちはFです": "",
        "こんにちは、Fです": "",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    return text.strip()


def ensure_slot_emojis(post: dict[str, str], min_emojis: int = 3) -> dict[str, str]:
    text = normalize_signature(remove_banned_phrases(post["text"]))
    fallback = SLOT_EMOJI_FALLBACKS.get(post["slot"], SLOT_EMOJI_FALLBACKS["朝"])

    if count_emoji(text) >= min_emojis:
        post["text"] = text
        return post

    lines = text.splitlines()
    first_line = next((line.strip() for line in lines if line.strip()), "")
    if not count_emoji(first_line):
        if first_line.startswith("＼") and first_line.endswith("／"):
            text = text.replace(first_line, fallback["title"], 1)
        else:
            text = f"{fallback['title']}\n\n{text}"

    if count_emoji(text) < min_emojis:
        body, signature = text.rsplit(SIGNATURE, 1)
        body = body.rstrip()
        if fallback["inline"] not in body:
            body = f"{body}\n{fallback['inline']} 今日はここだけメモしておきます"
        text = f"{body}\n\n{SIGNATURE}{signature}"

    if count_emoji(text) < min_emojis:
        body, signature = text.rsplit(SIGNATURE, 1)
        body = body.rstrip()
        body = re.sub(r"([。ます])$", rf"\1{fallback['ending']}", body)
        text = f"{body}\n\n{SIGNATURE}{signature}"

    post["text"] = normalize_signature(text)
    return post


def ensure_f_rhythm(post: dict[str, str]) -> dict[str, str]:
    text = normalize_signature(remove_banned_phrases(post["text"]))
    text = re.sub(r"。([^\n])", "。\n\\1", text)
    text = re.sub(r"、\s*", "、", text)
    text = re.sub(r"([🌈🐻🐻‍❄️😺🥰✅🌸💌])。", r"\1", text)

    if post["slot"] == "朝":
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if first_line and not (first_line.startswith("＼") and first_line.endswith("／")):
            text = f"{SLOT_EMOJI_FALLBACKS['朝']['title']}\n\n{text}"
    elif post["slot"] == "昼":
        if not text.lstrip().startswith("＼"):
            text = f"{SLOT_EMOJI_FALLBACKS['昼']['title']}\n\n{text}"
    elif post["slot"] == "夕":
        if not text.lstrip().startswith("＼"):
            text = f"{SLOT_EMOJI_FALLBACKS['夕']['title']}\n\n{text}"

    post["text"] = normalize_signature(text)
    return post


def compact_post_text(text: str, max_chars: int = 260) -> str:
    text = normalize_signature(text)
    text = re.sub(r"[ \t]+\n", "\n", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= max_chars:
        return text

    lines = [line for line in text.splitlines() if line.strip()]
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join(kept + [line])
        if len(candidate) > max_chars - 8:
            break
        kept.append(line)
    if kept and kept[-1] != SIGNATURE:
        candidate = "\n".join(kept + ["", SIGNATURE])
        if len(candidate) <= max_chars:
            return candidate
    return text[: max_chars - len(SIGNATURE) - 2].rstrip() + f"\n\n{SIGNATURE}"


def generate_posts() -> list[dict[str, str]]:
    settings = MARKET.load_settings()
    client = OpenAI(api_key=settings["openai_api_key"])
    now = datetime.now(JST)

    markets = MARKET.fetch_markets()
    news = MARKET.fetch_news(max_items=8)
    indicators = MARKET.fetch_indicators(max_items=4)
    history = load_history()
    recent_themes = [
        item.get("theme", "")
        for item in history
        if item.get("date", "") >= (now.replace(day=max(1, now.day - 14))).strftime("%Y-%m-%d")
    ][-30:]

    market_lines = MARKET.format_market_lines(markets)
    news_lines = MARKET.format_news_for_prompt(news)
    indicator_lines = MARKET.format_indicators_for_prompt(indicators)
    trend_terms = fetch_trend_candidates()
    style_guide = read_text_if_exists(STYLE_GUIDE_FILE)
    strategy = read_text_if_exists(STRATEGY_FILE)

    instructions = """
あなたは投資家F本人のX投稿案を作る編集者です。
文体ガイドを最優先し、朝・昼・夕の3本を作ってください。
目的は「投資家F本人がスマホで下書きしたように見える文章」です。

必ずJSON配列だけを返してください。Markdownや説明は不要です。
形式:
[
  {"slot":"朝","theme":"...","text":"..."},
  {"slot":"昼","theme":"...","text":"..."},
  {"slot":"夕","theme":"...","text":"..."}
]

ルール:
- 日本語を自然にする。声に出して不自然な文は避ける。
- 1投稿1テーマ。
- 1投稿は260字以内を目安にする。
- 朝は `＼おはようございます🐻🌈／` 型と `＼今日のマーケット🌈🐻／` 型を日替わりで使う。
- 朝のマーケット型だけ、主要トピックを✅で3つ並べる。おはよう型の日は✅を使わなくてよい。
- 朝は固有名詞か数字を必ず1つ以上入れる。
- 指標が自然に入る日は「今夜の重要指標」を入れる。無理に毎回入れない。
- 朝は後半に「Fは今日は、〜」または「〜しておこうと思います」という行動を1つ入れる。
- 昼はゆる日常。相場へ無理につなげない。
- 夕はNY前の点検。指値、アラート、ポジション量、見送りのどれかを入れる。
- 毎日同じ見え方にならないように、直近テーマを避ける。
- チェックリストを多用しない。
- `重要です` `考えられます` `示唆します` などのAI文体は禁止。
- 各投稿に絵文字を3〜6個入れる。ゼロは禁止。
- 使いやすい絵文字: 🌈 🐻 🐻‍❄️ 😺 🥰 ✅ 🌸 💌。
- 冒頭タイトルには絵文字を2個入れる。
- 文末が絵文字の場合、絵文字の直後に句点を置かない。
- 署名は必ず `投資家Fより💌`。
- `こんにちはFです` は絶対に使わない。
- 講義口調ではなく、スマホのメモっぽく短く改行する。
- かわいい比喩を1つ入れてよいが、相場投稿では最後に行動へ戻す。
- きれいなレポートより、Fの人間味を優先する。
- 自虐をかわいく入れる。例: 自分の起動で精一杯、失敗トレードのコレクター、冬眠します。
- 朝と昼は `今日もよろしくお願いします🥰` を入れてよい。
""".strip()

    user_input = f"""
現在日時: {now:%Y/%m/%d %H:%M} JST

文体ガイド:
{style_guide}

朝昼夕の方針:
{strategy}

直近で避けたいテーマ:
{format_json_for_prompt(recent_themes)}

相場データ:
{market_lines}

ニュース見出し:
{news_lines}

重要指標:
{indicator_lines}

トレンド候補:
{format_json_for_prompt(trend_terms)}
""".strip()

    response = client.responses.create(
        model=settings["openai_model"],
        instructions=instructions,
        input=user_input,
        store=False,
    )
    posts = parse_posts_json(response.output_text)
    for post in posts:
        post = ensure_f_rhythm(post)
        post = ensure_slot_emojis(post)
        post["text"] = compact_post_text(post["text"])
        post = ensure_f_rhythm(post)
        post = ensure_slot_emojis(post)
    return posts


def write_outputs(posts: list[dict[str, str]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "posts.json").write_text(
        json.dumps(posts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for index, post in enumerate(posts, start=1):
        slot = post["slot"]
        safe_slot = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥_-]+", "_", slot)
        (out_dir / f"{index}_{safe_slot}.txt").write_text(post["text"] + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="JSONだけを標準出力します。")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--update-history", action="store_true")
    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()
    posts = generate_posts()
    now = datetime.now(JST)

    out_dir = Path(args.out_dir) if args.out_dir else LOG_DIR / "socialdog_drafts" / now.strftime("%Y-%m-%d")
    write_outputs(posts, out_dir)

    if args.update_history:
        history = load_history()
        for post in posts:
            history.append(
                {
                    "date": now.strftime("%Y-%m-%d"),
                    "slot": post["slot"],
                    "theme": post.get("theme", ""),
                    "chars": len(post["text"]),
                }
            )
        save_history(history)

    logging.info("Generated SocialDog posts: %s", [post.get("theme", "") for post in posts])
    if args.json:
        print(json.dumps(posts, ensure_ascii=False, indent=2))
    else:
        for post in posts:
            print(f"--- {post['slot']} / {post.get('theme', '')} ---")
            print(post["text"])
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
