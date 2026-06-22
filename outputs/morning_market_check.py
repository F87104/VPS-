#!/usr/bin/env python3
"""
Morning market check sender for Slack.

It fetches market snapshots and RSS headlines, asks the OpenAI API to write
a short Japanese morning brief in an "investor F" tone, and posts it to Slack.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests
import yfinance as yf
from dotenv import load_dotenv
from openai import OpenAI
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "morning_market_check.log"
ENV_FILE = BASE_DIR / ".env"
JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class MarketTarget:
    label: str
    icon: str
    symbols: tuple[str, ...]
    decimals: int


MARKET_TARGETS = (
    MarketTarget("ドル円", "💹", ("JPY=X",), 3),
    MarketTarget("GOLD", "🪙", ("GC=F",), 2),
    MarketTarget("NASDAQ", "📈", ("^IXIC",), 2),
    MarketTarget("S&P500", "📈", ("^GSPC",), 2),
    MarketTarget("NYダウ", "📈", ("^DJI",), 2),
)


NEWS_QUERIES = (
    "site:nikkei.com/economy",
    "site:bloomberg.co.jp",
    "site:jp.reuters.com/business",
    "site:jp.wsj.com",
    "ドル円 OR 為替 OR USDJPY OR GOLD OR NASDAQ OR S&P500 OR NYダウ OR FRB OR CPI -site:finance.yahoo.co.jp -チャート -株価 -指数情報 -為替レート",
)


NEWS_SOURCE_NAMES = (
    "日本経済新聞",
    "ブルームバーグ",
    "ロイター",
    "ウォール・ストリート・ジャーナル",
    "相場関連ニュース",
)

INDICATORS_URL = "https://fx.minkabu.jp/indicators"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def lines(self) -> list[str]:
        return [part for part in self.parts if part]


BLOCKED_NEWS_PHRASES = (
    "指数情報・推移",
    "為替レート・相場",
    "リアルタイムチャート",
    "株価チャート",
    "株価情報",
    "Yahoo!ファイナンス",
    "Yahoo!フリマ",
    "PayPayフリマ",
    "REVITAL GOLD",
    "ランチ・シェイミング",
    "いじめ",
)

INDICATOR_COUNTRY_FLAGS = {
    "日本": "🇯🇵",
    "アメリカ": "🇺🇸",
    "ユーロ": "🇪🇺",
    "英国": "🇬🇧",
    "ドイツ": "🇩🇪",
    "カナダ": "🇨🇦",
    "中国": "🇨🇳",
    "豪": "🇦🇺",
    "NZ": "🇳🇿",
    "南ア": "🇿🇦",
}

MAJOR_INDICATOR_TERMS = (
    "政策金利",
    "日銀",
    "ECB",
    "FOMC",
    "FRB",
    "雇用統計",
    "失業率",
    "非農業部門雇用者数",
    "新規失業保険",
    "消費者物価指数",
    "CPI",
    "生産者物価指数",
    "PPI",
    "PCE",
    "小売売上高",
    "GDP",
    "ISM",
    "PMI",
    "ミシガン大学",
    "消費者信頼感",
    "住宅着工",
    "鉱工業生産",
    "ZEW",
)


def is_quote_or_market_page(title: str, source: str, link: str) -> bool:
    combined = f"{title}\n{source}\n{link}"
    return any(phrase in combined for phrase in BLOCKED_NEWS_PHRASES)


MARKET_RELEVANCE_TERMS = (
    "ドル",
    "円",
    "為替",
    "円安",
    "円高",
    "USDJPY",
    "GOLD",
    "金",
    "金利",
    "FRB",
    "FOMC",
    "CPI",
    "インフレ",
    "雇用",
    "GDP",
    "株",
    "米国株",
    "NASDAQ",
    "S&P",
    "NYダウ",
    "ダウ",
    "債券",
    "原油",
    "日銀",
    "財務相",
    "介入",
    "トランプ",
    "関税",
    "イラン",
    "イスラエル",
    "中国",
    "半導体",
    "AI",
)


def is_market_relevant(title: str, source: str) -> bool:
    combined = f"{title}\n{source}"
    return any(term in combined for term in MARKET_RELEVANCE_TERMS)


def normalize_news_key(title: str) -> str:
    normalized = re.sub(r"\s*[-－]\s*(Reuters|ロイター|WSJ|Yahoo!ニュース).*$", "", title)
    normalized = re.sub(r"[（(](ロイター|Reuters|ブルームバーグ|Bloomberg)[）)]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


FORBIDDEN_REPLACEMENTS = {
    "興味津々": "値動きの筋を見たい",
    "楽しみ": "見どころ",
    "重要です": "見たいところです",
    "重要": "注目",
    "必要です": "要るかもしれない",
    "注意が必要です": "見落としたくない",
    "と言えるでしょう": "と見ています",
    "と考えられます": "と見ています",
    "ではないでしょうか": "かもしれないね",
    "一見すると": "表だけ見ると",
    "しかしながら": "ただ",
    "一方で": "逆に",
    "踏まえる": "受ける",
    "示唆する": "にじませる",
    "結論として": "",
    "総じて": "",
    "冷静": "落ち着いて",
    "心配": "気になる",
    "第一歩": "入り口",
    "見えない連鎖": "注文のつながり",
    "明るい未来": "次の流れ",
    "心の平穏": "余白",
    "嵐の目": "中心",
    "不動の心": "余力",
    "黄金の鍵": "手がかり",
    "火花の散り方": "注文のぶつかり方",
    "市場の歪み": "需給の偏り",
    "相場の体温": "資金の流れ",
    "肌で感じる": "板で見る",
    "静かに": "淡々と",
    "感情のぶつけ合い": "注文のぶつかり合い",
    "かなり": "",
    "少しの": "小さな",
    "少しだけ": "軽く",
    "少し": "軽く",
    "非常に": "",
    "大変": "",
    "とても": "",
}

SURFACE_LEVEL_PHRASES = (
    "資金が逃げる先",
    "資金が「逃げる先」",
    "資金が残った",
    "資金が残る",
    "選び直している",
    "迷っている朝",
    "見たい朝",
    "拾いたい朝",
    "数字より",
    "市場全体",
    "リスク回避ムード",
    "警戒感が広がる",
    "慎重に見たい",
    "見守りたい",
)

CHECKLIST_OPINION_PHRASES = (
    "を見る",
    "見たい",
    "主役",
    "需給",
    "買い手",
    "売り手",
    "短期筋",
    "輸入勢",
    "本音",
    "ブレーキ",
    "残るか",
    "広がるか",
    "しやすい",
    "かもしれ",
    "なのかも",
)


def has_surface_level_phrase(text: str) -> bool:
    return any(phrase in text for phrase in SURFACE_LEVEL_PHRASES)


def extract_checklist_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("✅")]


def has_interpretive_checklist_line(text: str) -> bool:
    return any(
        phrase in line
        for line in extract_checklist_lines(text)
        for phrase in CHECKLIST_OPINION_PHRASES
    )


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def load_settings() -> dict[str, str]:
    load_dotenv(ENV_FILE)

    settings = {
        "slack_webhook_url": os.getenv("SLACK_WEBHOOK_URL", "").strip(),
        "openai_api_key": os.getenv("OPENAI_API_KEY", "").strip(),
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-5.5").strip(),
        "error_notify_slack": os.getenv("ERROR_NOTIFY_SLACK", "true").strip().lower(),
    }

    missing = [
        name
        for name, value in settings.items()
        if name in {"slack_webhook_url", "openai_api_key"} and not value
    ]
    if missing:
        raise RuntimeError(
            ".env に必要な設定がありません: "
            + ", ".join(missing)
            + "。SLACK_WEBHOOK_URL と OPENAI_API_KEY を確認してください。"
        )

    return settings


def pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100


def fetch_one_market(target: MarketTarget) -> dict[str, Any]:
    errors: list[str] = []

    for symbol in target.symbols:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="10d", interval="1d", auto_adjust=False)
            if hist.empty or "Close" not in hist.columns:
                errors.append(f"{symbol}: データが空でした")
                continue

            closes = hist["Close"].dropna()
            if len(closes) < 2:
                errors.append(f"{symbol}: 比較に必要な終値が足りません")
                continue

            current = float(closes.iloc[-1])
            previous = float(closes.iloc[-2])
            change = current - previous
            change_pct = pct_change(current, previous)
            last_date = closes.index[-1]
            if hasattr(last_date, "date"):
                date_text = last_date.date().isoformat()
            else:
                date_text = str(last_date)

            return {
                "ok": True,
                "label": target.label,
                "icon": target.icon,
                "symbol": symbol,
                "price": round(current, target.decimals),
                "change": round(change, target.decimals),
                "change_pct": round(change_pct, 2),
                "date": date_text,
            }
        except Exception as exc:  # noqa: BLE001 - log every upstream data issue
            errors.append(f"{symbol}: {exc}")

    logging.warning("Market fetch failed for %s: %s", target.label, " / ".join(errors))
    return {
        "ok": False,
        "label": target.label,
        "icon": target.icon,
        "symbol": ",".join(target.symbols),
        "error": " / ".join(errors) if errors else "unknown error",
    }


def fetch_markets() -> list[dict[str, Any]]:
    return [fetch_one_market(target) for target in MARKET_TARGETS]


def parse_entry_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def fetch_news(max_items: int = 12, max_age_hours: int = 36) -> list[dict[str, str]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 morning-market-check/1.0 "
                "(RSS headline collector)"
            ),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )

    articles: list[dict[str, str]] = []
    seen: set[str] = set()
    now = datetime.now(JST)
    cutoff = now - timedelta(hours=max_age_hours)

    for query, source_name in zip(NEWS_QUERIES, NEWS_SOURCE_NAMES):
        fresh_query = f"({query}) when:2d"
        encoded_query = quote_plus(fresh_query)
        url = (
            "https://news.google.com/rss/search"
            f"?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
        )

        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:  # noqa: BLE001
            logging.warning("News fetch failed for query %s: %s", query, exc)
            continue

        for entry in feed.entries[:10]:
            title = str(entry.get("title", "")).strip()
            link = str(entry.get("link", "")).strip()
            source = ""
            if entry.get("source"):
                source = str(entry.source.get("title", "")).strip()
            source = source or source_name
            published = str(entry.get("published", "")).strip()
            published_dt = parse_entry_datetime(published)
            if published_dt and published_dt < cutoff:
                continue
            if is_quote_or_market_page(title, source, link):
                continue
            if not is_market_relevant(title, source):
                continue

            key = link or title
            title_key = normalize_news_key(title)
            if not title or key in seen or title_key in seen:
                continue

            seen.add(key)
            seen.add(title_key)
            articles.append(
                {
                    "title": title,
                    "source": source,
                    "published": published,
                    "published_jst": (
                        published_dt.strftime("%Y/%m/%d %H:%M JST")
                        if published_dt
                        else "公開時刻不明"
                    ),
                    "published_sort": published_dt.isoformat() if published_dt else "",
                    "link": link,
                }
            )

    articles.sort(key=lambda item: item.get("published_sort", ""), reverse=True)
    return articles[:max_items]


def parse_indicator_date(line: str) -> datetime | None:
    match = re.search(r"(\d{4})年(\d{2})月(\d{2})日", line)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    return datetime(year, month, day, tzinfo=JST)


def parse_indicator_time(line: str) -> str | None:
    match = re.search(r"(\d{1,2}:\d{2}|未定)", line)
    return match.group(1) if match else None


def clean_indicator_title(line: str) -> str:
    title = re.sub(r"\s+[+-]?\d+(?:\.\d+)?pips.*$", "", line)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def indicator_country(title: str) -> str:
    if "・" not in title:
        return ""
    country = title.split("・", 1)[0]
    return country if country in INDICATOR_COUNTRY_FLAGS else ""


def indicator_display_name(title: str) -> str:
    country = indicator_country(title)
    if country:
        title = title.split("・", 1)[1]
    title = re.sub(r"\s+\[.+?\]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def indicator_dedupe_key(title: str, date_text: str, time_text: str) -> str:
    display = indicator_display_name(title)
    display = re.sub(r"\s+\d{2}/\d{2}.*$", "", display)
    display = re.sub(r"\s+\d{2}月.*$", "", display)
    return f"{date_text}|{time_text}|{indicator_country(title)}|{display}"


def indicator_score(title: str, event_dt: datetime, now: datetime) -> int:
    score = 0
    country = indicator_country(title)
    if country == "アメリカ":
        score += 35
    elif country in {"日本", "ユーロ"}:
        score += 25
    elif country in {"中国", "英国", "ドイツ", "カナダ"}:
        score += 15

    if any(term in title for term in MAJOR_INDICATOR_TERMS):
        score += 70
    if "政策金利" in title or "PPI" in title or "CPI" in title:
        score += 15
    if event_dt.date() == now.date() and event_dt.hour >= 18:
        score += 10
    return score


def fetch_indicators(max_items: int = 5) -> list[dict[str, str]]:
    now = datetime.now(JST)
    horizon = now + timedelta(hours=24)
    headers = {
        "User-Agent": "Mozilla/5.0 morning-market-check/1.0 (indicator collector)",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    try:
        response = requests.get(INDICATORS_URL, headers=headers, timeout=20)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logging.warning("Indicator fetch failed: %s", exc)
        return []

    parser = TextExtractor()
    parser.feed(response.text)

    current_date: datetime | None = None
    current_time = ""
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    for line in parser.lines():
        date_value = parse_indicator_date(line)
        if date_value:
            current_date = date_value
            current_time = parse_indicator_time(line) or ""
            continue

        time_value = parse_indicator_time(line)
        if time_value and re.fullmatch(r"\d{1,2}:\d{2}|未定", line):
            current_time = time_value
            continue

        if not current_date or not current_time or "・" not in line:
            continue

        title = clean_indicator_title(line)
        country = indicator_country(title)
        if not country:
            continue
        if "継続受給者数" in title:
            continue

        if current_time == "未定":
            event_dt = current_date.replace(hour=23, minute=59)
        else:
            hour, minute = map(int, current_time.split(":"))
            event_dt = current_date.replace(hour=hour, minute=minute)

        if not (now <= event_dt <= horizon):
            continue

        score = indicator_score(title, event_dt, now)
        if score < 35:
            continue

        date_text = event_dt.strftime("%Y/%m/%d")
        key = indicator_dedupe_key(title, date_text, current_time)
        if key in seen:
            continue
        seen.add(key)

        items.append(
            {
                "time": current_time,
                "country": country,
                "flag": INDICATOR_COUNTRY_FLAGS.get(country, ""),
                "title": indicator_display_name(title),
                "datetime": event_dt.isoformat(),
                "score": str(score),
            }
        )

    items.sort(key=lambda item: (-int(item["score"]), item["datetime"]))
    selected = items[:max_items]
    selected.sort(key=lambda item: item["datetime"])
    return selected


def format_market_lines(markets: list[dict[str, Any]]) -> str:
    lines = ["📊 主要指数", ""]

    for item in markets:
        if item.get("ok"):
            change = float(item["change"])
            change_pct = float(item["change_pct"])
            arrow = "▲" if change > 0 else "▼" if change < 0 else "→"
            lines.append(
                f'{item["icon"]} {item["label"]}: '
                f'{item["price"]} '
                f'({arrow} {change:+g} / {change_pct:+.2f}%) '
                f'[{item["date"]}]'
            )
        else:
            lines.append(f'{item["icon"]} {item["label"]}: 取得失敗')

    return "\n".join(lines)


def format_news_for_prompt(news: list[dict[str, str]]) -> str:
    if not news:
        return "ニュース見出しを取得できませんでした。"

    lines = []
    for index, item in enumerate(news, start=1):
        source = f' / {item["source"]}' if item.get("source") else ""
        published = (
            f' / {item["published_jst"]}'
            if item.get("published_jst")
            else f' / {item["published"]}'
            if item.get("published")
            else ""
        )
        lines.append(f'{index}. {item["title"]}{source}{published}')
    return "\n".join(lines)


def format_indicators_for_prompt(indicators: list[dict[str, str]]) -> str:
    if not indicators:
        return "今後24時間の大きめの経済指標予定を取得できませんでした。"

    lines = []
    for item in indicators:
        lines.append(f'{item["time"]} {item["flag"]}{item["title"]}')
    return "\n".join(lines)


def clean_forbidden_phrases(text: str) -> str:
    cleaned = text
    marker = "__IMPORTANT_INDICATOR__"
    cleaned = cleaned.replace("重要指標", marker)
    for forbidden, replacement in FORBIDDEN_REPLACEMENTS.items():
        cleaned = cleaned.replace(forbidden, replacement)
    cleaned = cleaned.replace(marker, "重要指標")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def remove_opening_greeting(text: str) -> str:
    patterns = (
        r"^こんにちは、?Fです[✨!！。\s]*",
        r"^こんにちは、?投資家Fです[✨!！。\s]*",
    )
    cleaned = text.strip()
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned).lstrip()
    return cleaned


def strip_code_fences(text: str) -> str:
    return text.replace("```", "'''").strip()


def generate_ai_sections(
    settings: dict[str, str],
    markets: list[dict[str, Any]],
    news: list[dict[str, str]],
    indicators: list[dict[str, str]],
) -> str:
    client = OpenAI(api_key=settings["openai_api_key"])
    market_lines = format_market_lines(markets)
    news_lines = format_news_for_prompt(news)
    indicator_lines = format_indicators_for_prompt(indicators)
    now = datetime.now(JST)

    instructions = """
あなたは「投資家F」です。朝の相場メモをSlack向けに書いてください。
狙いは、Xにもコピーしやすい「短く、自然で、検索されやすい固有名詞と数字が入った朝メモ」です。

【投資家Fの文体ルール】
- 冒頭は必ず「＼おはようございます🌈🐻／」にする。
- 2行目は必ず「今日の気になるメモ📝」にする。
- 一人称は使わなくてよい。使う場合は必ず「私」にする。
- Xの投稿のように、1文を短くし、改行でリズムを作る。
- 難しい相場解説は入れず、朝にそのまま読める自然な日本語にする。
- ニュース内の固有名詞、数字、仕組みをできるだけ入れる。例: ドル円、円安、イラン、原油、GOLD、NASDAQ、マイクロン、HBM、フィジカルAI、370兆円。
- インプレッションを狙うため、検索されやすい固有名詞と数字を優先する。
- ただし、見出しにない固有名詞や数字を作らない。
- 読者に語りかけるような、親しみやすく人間味のある口語体を使う。
- 強い断定は避ける。例: 〜らしい、〜なのかも、〜かな。
- 絵文字の直後に句点をつけない。文末が絵文字の場合はそのまま終わる。
- 絵文字は自然に使う。使える絵文字: 😺🐻🐻‍❄️🥰✅🌈🌸🚨📝✨。
- 締めは必ず「今日も宜しくです🥰✨」を入れる。
- 最後は必ず「投資家Fより💌」で締める。

【チェックリストのルール】
- ✅の行は4〜5行にする。
- ✅はニュースや相場データのピックアップ事実だけを書く。
- ✅には、固有名詞、数字、日付、時刻、発表内容、上昇・下落など、確認できる事実だけを入れる。
- ✅には、Fの見方、需給の読み、読者への行動、比喩を入れない。
- ✅で使ってよい文末は「反応」「推移」「上昇」「下落」「発表」「報道」「検討」「通過」「更新」「関連」などの短い表現だけ。
- ✅で「〜を見る」「〜かを見る」「主役」「需給」「買い手」「売り手」「本音」は使わない。

【禁止すること】
- 深い考察、長い解説、ポジションの話、投資助言は入れない。
- F自身のポジション、注文、指値、ロット、エントリー、利確、損切り、キャッシュ比率は書かない。
- 「買う」「売る」「必ず上がる」「必ず下がる」のような投資助言に見える表現は使わない。

【排除する表現】
- 興味津々、楽しみ、重要です、求められます、注意が必要です、と言えるでしょう、と考えられます、ではないでしょうか、一見すると、しかしながら、一方で、鑑みるに、踏まえる、示唆する、喚起する、結論として、総じて、であると言えます。
- 冷静、心配、第一歩、見えない連鎖、明るい未来、心の平穏、嵐の目、不動の心、黄金の鍵、火花の散り方、市場の歪み、相場の体温、肌で感じる、静かに、感情のぶつけ合い。
- AIらしい一般論、教科書的なまとめ、抽象的な励まし、精神論、ポエム表現、軽い実況だけの文章。
- 「総じて」「重要です」「考えられます」のようなレポート文体。
- `資金の逃げ足` `板の薄さ` など同じ言葉の毎回の使い回し。
- `荒れてますね` `注意深く見守る` `焦らずいきましょう` `勇者レベル` `お預けチャート` `資金が逃げる先` `資金が残った` `数字より` のような、意味が薄く軽く見える締め。

数字は入力されたものを勝手に変えないでください。
内容は投資助言ではなく、朝の観察メモとして書いてください。
主要トピックは4〜5行にしてください。
主要トピックは、必ずニュースや相場データの事実ピックアップだけにしてください。
重要指標は入れてもよいですが、入力された「重要指標候補」から最大2つだけにしてください。取得できていない指標名や時刻を作らないでください。
全体は短く、Slackで一目で読める長さにしてください。
各チェックリスト行は1行だけ、長くても45字前後にしてください。長い解説や段落は禁止です。
軽く、自然で、朝の投稿としてコピーしやすい文にしてください。
声に出して読んで不自然な日本語は避けてください。
""".strip()

    user_input = f"""
次の相場データとニュース見出しをもとに、Slackへ送る朝の文章を作ってください。
現在日時は {now:%Y/%m/%d %H:%M} JST です。
ニュース見出しは公開時刻が新しい順です。昨日以前の材料を、今日新しく出た材料のように扱わないでください。
公開時刻が古い見出しは「前日から続く材料」として扱い、今日の確認ポイントに寄せてください。

必ず以下の構成で出力してください。

＼おはようございます🌈🐻／
今日の気になるメモ📝

✅ニュースや相場データから拾った事実だけを1行で入れる
✅ニュースや相場データから拾った事実だけを1行で入れる
✅ニュースや相場データから拾った事実だけを1行で入れる
✅ニュースや相場データから拾った事実だけを1行で入れる
✅ニュースや相場データから拾った事実だけを1行で入れる
※チェック行で「主要トピック1」「トピック2」のような番号ラベルは使わない。
※チェック行では「〜を見る」「〜かを見る」「主役」「需給」「買い手」「売り手」を使わない。

必要なら以下を入れる。候補が弱い場合は入れない。
今夜の重要指標
時刻  国旗 指標名
時刻  国旗 指標名

今日も宜しくです🥰✨

投資家Fより💌

相場データ:
{market_lines}

ニュース見出し:
{news_lines}

重要指標候補:
{indicator_lines}
""".strip()

    response = client.responses.create(
        model=settings["openai_model"],
        instructions=instructions,
        input=user_input,
        store=False,
    )

    text = response.output_text.strip()
    if not text:
        raise RuntimeError("OpenAI APIから空の応答が返りました。")
    text = clean_forbidden_phrases(text)
    text = remove_opening_greeting(text)

    if has_surface_level_phrase(text) or has_interpretive_checklist_line(text):
        retry_input = (
            user_input
            + "\n\n前回の出力は条件に合いませんでした。"
            + " 解説や抽象語を削り、"
            + "✅はニュースと相場データのピックアップ事実だけにしてください。"
            + " ✅では `〜を見る` `主役` `需給` `買い手` `売り手` を使わず、"
            + "朝のX投稿としてコピーしやすい短い形式で書き直してください。"
        )
        response = client.responses.create(
            model=settings["openai_model"],
            instructions=instructions,
            input=retry_input,
            store=False,
        )
        text = response.output_text.strip()
        if not text:
            raise RuntimeError("OpenAI APIから空の応答が返りました。")
        text = clean_forbidden_phrases(text)
        text = remove_opening_greeting(text)

    if not text.endswith("投資家Fより💌"):
        text = text.rstrip() + "\n\n投資家Fより💌"
    return text


def build_slack_message(settings: dict[str, str]) -> str:
    now = datetime.now(JST)
    markets = fetch_markets()
    news = fetch_news()
    indicators = fetch_indicators()
    ai_sections = generate_ai_sections(settings, markets, news, indicators)
    copy_text = strip_code_fences(ai_sections)

    logging.info(
        "Fetched markets=%s news_count=%s indicator_count=%s",
        [asdict(target)["label"] for target in MARKET_TARGETS],
        len(news),
        len(indicators),
    )
    for index, item in enumerate(news[:5], start=1):
        logging.info(
            "Selected news %s: %s / %s / %s",
            index,
            item.get("title", ""),
            item.get("source", ""),
            item.get("published_jst", item.get("published", "")),
        )
    for index, item in enumerate(indicators, start=1):
        logging.info(
            "Selected indicator %s: %s %s%s",
            index,
            item.get("time", ""),
            item.get("flag", ""),
            item.get("title", ""),
        )

    return "\n\n".join(
        [
            f"🌅 朝イチ相場CHECK（{now:%Y/%m/%d %H:%M} JST）",
            "※自動配信です。内容は投資助言ではなく、朝の観察メモです。",
            ai_sections,
            f"📋 コピー用\n```\n{copy_text}\n```",
        ]
    )


def send_to_slack(webhook_url: str, text: str) -> None:
    response = requests.post(webhook_url, json={"text": text}, timeout=20)
    body = response.text.strip()
    if response.status_code != 200 or body != "ok":
        raise RuntimeError(
            f"Slack送信失敗: status={response.status_code}, response={body}"
        )


def should_notify_error(settings: dict[str, str]) -> bool:
    return settings["error_notify_slack"] in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Slackへ送らず、生成した文章を画面に表示します。",
    )
    parser.add_argument(
        "--test-slack",
        action="store_true",
        help="API取得をせず、Slackへの疎通テストだけを行います。",
    )
    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()

    try:
        settings = load_settings()

        if args.test_slack:
            now = datetime.now(JST)
            send_to_slack(
                settings["slack_webhook_url"],
                f"✅ Slackテスト成功: VPSからPython送信できています（{now:%Y/%m/%d %H:%M} JST）",
            )
            print("Slackテスト送信に成功しました。Slackにメッセージが届いていればOKです。")
            logging.info("Slack test succeeded")
            return 0

        message = build_slack_message(settings)

        if args.dry_run:
            print(message)
            logging.info("Dry run succeeded")
            return 0

        send_to_slack(settings["slack_webhook_url"], message)
        print("Slack送信に成功しました。")
        logging.info("Morning market check sent successfully")
        return 0

    except Exception as exc:  # noqa: BLE001
        logging.exception("Morning market check failed")
        print(f"エラー: {exc}", file=sys.stderr)

        try:
            settings = load_settings()
            if should_notify_error(settings):
                send_to_slack(
                    settings["slack_webhook_url"],
                    "⚠️ 朝イチ相場CHECKの自動送信でエラーが出ました。\n"
                    f"VPSでログを確認してください: {LOG_FILE}\n"
                    f"エラー: {exc}",
                )
        except Exception:
            logging.exception("Failed to notify Slack about the error")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
