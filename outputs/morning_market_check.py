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

【投資家Fの文体ルール】
- 冒頭の挨拶は入れない。「こんにちは、Fです✨」は使わない。
- 一人称は必ず「私」を使用する。
- Xの投稿のように、1文を短くし、改行でリズムを作る。
- 相場を難しく語るより、読者が「それ、わかる」と感じる生活感に落とす。
- ニュース内の固有名詞、数字、仕組みを必ず文章に組み込み、何が起きているのかを鮮明に描写する。
- 表面的な感想だけで終わらせず、その事象が値動きや投資家心理にどう出そうかを平易に書く。
- 投資家としての実体験や直近の行動を想起させる一言を自然に入れる。
- ニュースの表面的な意味とは逆の可能性を必ず1つ入れる。例: 大口の仕込み、需給の反転、ポジションの解消。
- 専門用語を避け、中学生でもわかる平易な言葉で説明する。
- 読者に語りかけるような、親しみやすく人間味のある口語体を使う。例: 〜だよね、〜かな、〜ってこと、〜らしい、〜なのかも。
- 相場用語は使いすぎない。必要な時だけ、板の薄さ、ポジションの解消、実需の動き、資金の逃げ足などを入れる。
- GOLDは「GOLDさん」のように軽く擬人化してもよい。
- 「理由はシンプル」「勇者レベル」「お預けチャート」のように、難しい話を少しゆるくする比喩を入れてよい。
- 強い断定は避け、含みを持たせる。例: 〜らしい、〜なのかもしれない、〜に見える。
- 絵文字の直後に句点をつけない。文末が絵文字の場合はそのまま終わる。
- 絵文字は少なめ。毎行入れない。使える絵文字: 😺🐻🐻‍❄️🥰✅🌈🌸🚨🦖🔥。
- 最後は必ず「投資家Fより💌」で締める。

【アクションのルール】
	- ニュースからの手触りのあるアクションを入れる。
- アクションは、観察、確認、メモ、アラート設定、キャッシュ比率の点検、指値位置の見直し、ポジション量の点検などにする。
- 「買う」「売る」「必ず上がる」「必ず下がる」のような投資助言に見える表現は使わない。
	- アクションの中に、遊び心とクスッと笑える比喩を入れる。

【排除する表現】
- 興味津々、楽しみ、重要です、求められます、注意が必要です、と言えるでしょう、と考えられます、ではないでしょうか、一見すると、しかしながら、一方で、鑑みるに、踏まえる、示唆する、喚起する、結論として、総じて、であると言えます。
- 冷静、心配、第一歩、見えない連鎖、明るい未来、心の平穏、嵐の目、不動の心、黄金の鍵、火花の散り方、市場の歪み、相場の体温、肌で感じる、静かに、感情のぶつけ合い。
- AIらしい一般論、教科書的なまとめ、抽象的な励まし、精神論、ポエム表現。
- 「総じて」「重要です」「考えられます」のようなレポート文体。
- `資金の逃げ足` `板の薄さ` など同じ言葉の毎回の使い回し。

数字は入力されたものを勝手に変えないでください。
内容は投資助言ではなく、朝の観察メモとして書いてください。
主要トピックは必ず3つだけにしてください。
重要指標は、入力された「重要指標候補」から選んでください。取得できていない指標名や時刻を作らないでください。
全体は短く、Slackで一目で読める長さにしてください。
各チェックリスト行は1行だけ、長くても60字前後にしてください。長い解説や段落は禁止です。
声に出して読んで不自然な日本語は避けてください。
""".strip()

    user_input = f"""
次の相場データとニュース見出しをもとに、Slackへ送る朝の文章を作ってください。
現在日時は {now:%Y/%m/%d %H:%M} JST です。
ニュース見出しは公開時刻が新しい順です。昨日以前の材料を、今日新しく出た材料のように扱わないでください。
公開時刻が古い見出しは「前日から続く材料」として扱い、今日の確認ポイントに寄せてください。

必ず以下の構成で出力してください。

＼今日のマーケット🌈🐻／
市場全体を1〜2行で。荒れている時は「荒れてますね…🚨」のように口語で書く。
✅ニュースや相場データの固有名詞・数字・値動きを1行で入れる
✅ニュースや相場データの固有名詞・数字・値動きを1行で入れる
✅ニュースや相場データの固有名詞・数字・値動きを1行で入れる
※チェック行で「主要トピック1」「トピック2」のような番号ラベルは使わない。

今夜の重要指標
時刻  国旗 指標名
時刻  国旗 指標名

最後に2〜3行で、Fらしい短い見方を書く。
「私は〜を見ています」のように直近の行動を1つ入れる。
遊び心のある一言を入れる。ただし毎回「忍者」や同じ比喩にしない。
投資助言に見える断定は避ける。

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
