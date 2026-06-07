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
from datetime import datetime
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
    "ドル円 OR 為替 OR USDJPY OR GOLD OR NASDAQ OR S&P500 OR NYダウ OR FRB OR CPI",
)


NEWS_SOURCE_NAMES = (
    "日本経済新聞",
    "ブルームバーグ",
    "ロイター",
    "ウォール・ストリート・ジャーナル",
    "相場関連ニュース",
)


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


def fetch_news(max_items: int = 12) -> list[dict[str, str]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 morning-market-check/1.0 "
                "(RSS headline collector)"
            )
        }
    )

    articles: list[dict[str, str]] = []
    seen: set[str] = set()

    for query, source_name in zip(NEWS_QUERIES, NEWS_SOURCE_NAMES):
        encoded_query = quote_plus(query)
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

        for entry in feed.entries[:5]:
            title = str(entry.get("title", "")).strip()
            link = str(entry.get("link", "")).strip()
            source = ""
            if entry.get("source"):
                source = str(entry.source.get("title", "")).strip()
            source = source or source_name
            published = str(entry.get("published", "")).strip()

            key = link or title
            if not title or key in seen:
                continue

            seen.add(key)
            articles.append(
                {
                    "title": title,
                    "source": source,
                    "published": published,
                    "link": link,
                }
            )
            if len(articles) >= max_items:
                return articles

    return articles


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
        published = f' / {item["published"]}' if item.get("published") else ""
        lines.append(f'{index}. {item["title"]}{source}{published}')
    return "\n".join(lines)


def clean_forbidden_phrases(text: str) -> str:
    cleaned = text
    for forbidden, replacement in FORBIDDEN_REPLACEMENTS.items():
        cleaned = cleaned.replace(forbidden, replacement)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def generate_ai_sections(
    settings: dict[str, str],
    markets: list[dict[str, Any]],
    news: list[dict[str, str]],
) -> str:
    client = OpenAI(api_key=settings["openai_api_key"])
    market_lines = format_market_lines(markets)
    news_lines = format_news_for_prompt(news)

    instructions = """
あなたは「投資家F」です。朝の相場メモをSlack向けに書いてください。

【投資家Fの文体ルール】
- 冒頭は必ず「こんにちは、Fです✨」から始める。
- 一人称は必ず「私」を使用する。
- ニュース内の固有名詞、数字、仕組みを必ず文章に組み込み、何が起きているのかを鮮明に描写する。
- 表面的な感想を避け、その事象が相場の需給や投資家心理にどう影響するかを示す。
- 投資家としての実体験や直近の行動を想起させる一言を自然に入れる。
- ニュースの表面的な意味とは逆の可能性を必ず1つ入れる。例: 大口の仕込み、需給の反転、ポジションの解消。
- 専門用語を避け、中学生でもわかる平易な言葉で説明する。
- 読者に語りかけるような、親しみやすく人間味のある口語体を使う。例: 〜だよね、〜かな、〜ってこと、〜らしい、〜なのかも。
- 需給の動きや資金の質を表す具体的な言葉を使う。例: 板の薄さ、需給の偏り、資金の逃げ足、ポジションの解消、実需の動き、流動性の低下、情報の裏側、ボラティリティの質、資金の滞留状況。
- 強い断定は避け、含みを持たせる。例: 〜らしい、〜なのかもしれない、〜に見える。
- 絵文字の直後に句点をつけない。文末が絵文字の場合はそのまま終わる。
- 感情表現や絵文字は適度に使う。使える絵文字: 😺🐻🐻‍❄️🥰✅🌈🌸。
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

数字は入力されたものを勝手に変えないでください。
内容は投資助言ではなく、朝の観察メモとして書いてください。
""".strip()

    user_input = f"""
次の相場データとニュース見出しをもとに、Slackへ送る朝の文章を作ってください。

必ず以下の構成で出力してください。

こんにちは、Fです✨

📌 Fの朝イチ相場CHECK
- 3〜5行で、今日どこを見たい日かを書く。
- 私の直近の行動や観察を感じる一言を入れる。

📰 今日のニュース3選
1. ニュース名: 1行要約
	   Fアクション: 今日やる観察・確認を1つ。クスッとする言い回しを入れる
   逆説メモ: 表面的な意味とは逆の可能性を1つ
2. ニュース名: 1行要約
	   Fアクション: 今日やる観察・確認を1つ。クスッとする言い回しを入れる
   逆説メモ: 表面的な意味とは逆の可能性を1つ
3. ニュース名: 1行要約
	   Fアクション: 今日やる観察・確認を1つ。クスッとする言い回しを入れる
   逆説メモ: 表面的な意味とは逆の可能性を1つ

💌 Fの投稿案（3つのバリエーション）
案1
＼📣ニュースタイトル　絵文字🐻／
### 要約：今、何が起きているのか😺
### まとめ：なぜ、そして何が気になるのか🐻‍❄️
### 深掘り：Fが考える「今後」と「確認ポイント」🌈

案2
＼📣ニュースタイトル　絵文字🐻／
### 要約：今、何が起きているのか😺
### まとめ：なぜ、そして何が気になるのか🐻‍❄️
### 深掘り：Fが考える「今後」と「確認ポイント」🌈

案3
＼📣ニュースタイトル　絵文字🐻／
### 要約：今、何が起きているのか😺
### まとめ：なぜ、そして何が気になるのか🐻‍❄️
### 深掘り：Fが考える「今後」と「確認ポイント」🌈

投資家Fより💌

相場データ:
{market_lines}

ニュース見出し:
{news_lines}
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
    if not text.startswith("こんにちは、Fです✨"):
        text = "こんにちは、Fです✨\n\n" + text
    if not text.endswith("投資家Fより💌"):
        text = text.rstrip() + "\n\n投資家Fより💌"
    return text


def build_slack_message(settings: dict[str, str]) -> str:
    now = datetime.now(JST)
    markets = fetch_markets()
    news = fetch_news()
    ai_sections = generate_ai_sections(settings, markets, news)
    market_lines = format_market_lines(markets)

    logging.info(
        "Fetched markets=%s news_count=%s",
        [asdict(target)["label"] for target in MARKET_TARGETS],
        len(news),
    )

    return "\n\n".join(
        [
            f"🌅 朝イチ相場CHECK（{now:%Y/%m/%d %H:%M} JST）",
            market_lines,
            "※自動配信です。内容は投資助言ではなく、朝の観察メモです。",
            ai_sections,
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
