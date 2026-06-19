#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find potentially underpriced Mercari listings.

This MVP only reads public search/detail pages. It does not log in, buy,
favorite, comment, or bypass access controls.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import random
import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
JST = timezone(timedelta(hours=9), "JST")
MERCARI_BASE_URL = "https://jp.mercari.com"
BODY_TEXT_MARKER = "\n<!-- MERCARI_BODY_TEXT_START -->\n"


CSV_FIELDS = [
    "検索キーワード",
    "商品名",
    "現在価格",
    "相場中央値",
    "相場比較件数",
    "相場比較語",
    "割安率",
    "状態",
    "送料",
    "出品日時",
    "いいね数",
    "商品URL",
    "警告ワード",
    "仕入れ候補スコア",
]


DEFAULT_CONFIG: dict[str, Any] = {
    "keywords": [],
    "min_discount_rate": 0.2,
    "min_score": 70,
    "max_current_items_per_keyword": 20,
    "max_sold_items_per_keyword": 40,
    "min_sold_samples": 5,
    "min_comparable_sold_samples": 3,
    "min_title_overlap_ratio": 0.34,
    "max_required_comparison_terms": 2,
    "max_detail_pages_per_keyword": 8,
    "fetch_detail_pages": True,
    "headless": True,
    "random_wait_seconds": {"min": 5, "max": 12},
    "page_timeout_ms": 45000,
    "slack_webhook_url": "",
    "slack_notify": True,
    "shipping_included_words": ["送料込み", "送料 出品者負担", "出品者負担"],
    "preferred_conditions": ["新品、未使用", "未使用に近い", "目立った傷や汚れなし"],
    "bonus_words": ["正規品", "タグ", "付属品", "箱", "保証書"],
    "warning_words": ["難あり", "ジャンク", "破れ", "汚れ", "偽物", "コピー"],
    "exclude_words": ["偽物", "コピー", "レプリカ", "ノベルティ", "ジャンク", "難あり"],
    "high_risk_brand_words": ["MONCLER", "モンクレール", "Tiffany", "ティファニー"],
    "keyword_include_words": {},
    "keyword_exclude_words": {},
    "comparison_terms": {},
}


@dataclass
class Listing:
    keyword: str
    title: str
    price: int
    url: str
    status: str
    item_id: str = ""
    condition: str = ""
    shipping: str = ""
    listed_at: str = ""
    likes: int | None = None
    description: str = ""
    image_count: int = 0


@dataclass
class Deal:
    keyword: str
    title: str
    current_price: int
    median_price: int
    comparable_count: int
    comparison_terms: list[str]
    discount_rate: float
    condition: str
    shipping: str
    listed_at: str
    likes: int | None
    url: str
    warning_words: list[str]
    exclude_words: list[str]
    bonus_words: list[str]
    score: int

    def to_csv_row(self) -> dict[str, str]:
        return {
            "検索キーワード": self.keyword,
            "商品名": self.title,
            "現在価格": str(self.current_price),
            "相場中央値": str(self.median_price),
            "相場比較件数": str(self.comparable_count),
            "相場比較語": ", ".join(self.comparison_terms),
            "割安率": f"{self.discount_rate:.1%}",
            "状態": self.condition,
            "送料": self.shipping,
            "出品日時": self.listed_at,
            "いいね数": "" if self.likes is None else str(self.likes),
            "商品URL": self.url,
            "警告ワード": ", ".join(self.warning_words),
            "仕入れ候補スコア": str(self.score),
        }


class MercariAccessError(RuntimeError):
    """Raised when Mercari blocks or changes the expected public page."""


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def load_config(path: Path) -> dict[str, Any]:
    config = DEFAULT_CONFIG | json.loads(path.read_text(encoding="utf-8"))
    config["slack_webhook_url"] = (
        os.getenv("SLACK_WEBHOOK_URL") or config.get("slack_webhook_url", "")
    ).strip()
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mercari underpriced item finder")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"))
    parser.add_argument("--keyword", action="append", help="Run only this keyword")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Slack")
    parser.add_argument("--sample", action="store_true", help="Use built-in sample data")
    parser.add_argument("--test-slack", action="store_true", help="Send Slack test only")
    return parser.parse_args()


def deep_merge_config(config: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_CONFIG.copy()
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merged[key] | value
        else:
            merged[key] = value
    return merged


def parse_price(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    text = str(value)
    match = re.search(r"(?:¥|￥)?\s*([0-9][0-9,]{2,})", text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clean_title(title: str) -> str:
    title = normalize_space(title)
    return re.sub(r"のサムネイル$", "", title).strip()


def normalize_url(url: str) -> str:
    if not url:
        return ""
    return urljoin(MERCARI_BASE_URL, url)


def extract_body_text(html: str) -> str:
    if BODY_TEXT_MARKER in html:
        return html.split(BODY_TEXT_MARKER, 1)[1]
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n", strip=True)


def extract_item_id(url_or_id: str) -> str:
    text = str(url_or_id)
    match = re.search(r"(m\d{6,}|[A-Za-z0-9_-]{8,})", text)
    return match.group(1) if match else ""


def is_negated_warning(text: str, word: str) -> bool:
    negated_phrases = {
        "汚れ": ["汚れなし", "汚れ無し", "傷や汚れなし", "傷や汚れ無し", "汚れはありません"],
        "破れ": ["破れなし", "破れ無し", "破れはありません"],
    }
    return any(phrase in text for phrase in negated_phrases.get(word, []))


def text_contains_any(text: str, words: list[str]) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for word in words:
        if word.lower() not in lower:
            continue
        if is_negated_warning(text, word):
            continue
        found.append(word)
    return found


def walk_json(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def first_string(obj: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_space(value)
        if isinstance(value, dict):
            nested = first_string(value, keys)
            if nested:
                return nested
    return ""


def first_int(obj: dict[str, Any], keys: list[str]) -> int | None:
    for key in keys:
        if key in obj:
            parsed = parse_price(obj.get(key))
            if parsed is not None:
                return parsed
    return None


def listing_from_json_obj(obj: dict[str, Any], keyword: str, status: str) -> Listing | None:
    title = clean_title(first_string(obj, ["name", "title", "itemName", "item_name"]))
    price = first_int(obj, ["price", "itemPrice", "item_price", "priceValue", "value"])
    raw_url = first_string(obj, ["url", "itemUrl", "item_url", "link"])
    raw_id = first_string(obj, ["id", "itemId", "item_id", "productId"])

    item_id = extract_item_id(raw_url) or extract_item_id(raw_id)
    if not raw_url and item_id:
        raw_url = f"/item/{item_id}"

    url = normalize_url(raw_url)
    if not title or not price or not url or "/item/" not in url:
        return None

    likes = first_int(obj, ["numLikes", "likeCount", "likes", "like_count"])
    image_count = 0
    images = obj.get("images") or obj.get("photos") or obj.get("thumbnails")
    if isinstance(images, list):
        image_count = len(images)

    return Listing(
        keyword=keyword,
        title=title,
        price=price,
        url=url,
        status=status,
        item_id=item_id,
        condition=first_string(obj, ["condition", "itemCondition", "item_condition"]),
        shipping=first_string(obj, ["shipping", "shippingFee", "shippingPayer", "shipping_fee"]),
        listed_at=first_string(obj, ["created", "createdAt", "created_at", "updatedAt"]),
        likes=likes,
        description=first_string(obj, ["description", "itemDescription", "item_description"]),
        image_count=image_count,
    )


def parse_json_scripts(html: str) -> list[Any]:
    soup = BeautifulSoup(html, "html.parser")
    values: list[Any] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text("", strip=True)
        if not text:
            continue
        if script.get("id") == "__NEXT_DATA__" or script.get("type") in {
            "application/json",
            "application/ld+json",
        }:
            try:
                values.append(json.loads(text))
            except json.JSONDecodeError:
                continue
    return values


def parse_listings_from_json(html: str, keyword: str, status: str) -> list[Listing]:
    listings: list[Listing] = []
    seen: set[str] = set()
    for payload in parse_json_scripts(html):
        for obj in walk_json(payload):
            listing = listing_from_json_obj(obj, keyword, status)
            if not listing:
                continue
            key = listing.item_id or listing.url
            if key in seen:
                continue
            seen.add(key)
            listings.append(listing)
    return listings


def parse_listings_from_dom(html: str, keyword: str, status: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if "/item/" not in href:
            continue
        url = normalize_url(href)
        if url in seen:
            continue
        seen.add(url)

        card = anchor
        for _ in range(4):
            if card.parent is None:
                break
            card = card.parent
            if parse_price(card.get_text(" ", strip=True)):
                break

        card_text = normalize_space(card.get_text(" ", strip=True))
        price = parse_price(card_text)
        if not price:
            continue

        title = anchor.get("aria-label") or ""
        img = anchor.find("img")
        if not title and img:
            title = img.get("alt") or ""
        if not title:
            title = re.sub(r"(¥|￥)\s*[0-9,]+", "", card_text).strip()
        title = clean_title(title)
        if not title:
            continue

        listings.append(
            Listing(
                keyword=keyword,
                title=title[:160],
                price=price,
                url=url,
                status=status,
                item_id=extract_item_id(url),
                image_count=1 if img else 0,
            )
        )
    return listings


def title_matches_keyword(title: str, keyword: str) -> bool:
    title_lower = title.lower()
    tokens = [token for token in re.split(r"[\s　]+", keyword.lower()) if len(token) >= 2]
    return not tokens or any(token in title_lower for token in tokens)


def looks_like_listing_title(title: str, keyword: str) -> bool:
    title = clean_title(title)
    if len(title) < 4 or len(title) > 180:
        return False
    if re.fullmatch(r"[¥￥]?\s*[0-9,]+", title):
        return False
    ui_words = [
        "メルカリ",
        "ログイン",
        "会員登録",
        "検索",
        "カテゴリー",
        "ブランド",
        "価格",
        "商品の状態",
        "配送料",
        "販売状況",
        "新しい順",
        "おすすめ順",
        "保存した検索条件",
    ]
    if any(word in title for word in ui_words):
        return False
    return title_matches_keyword(title, keyword)


def parse_text_price_title(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(?:現在|売り切れ|SOLD)?\s*[¥￥]\s*([0-9][0-9,]{2,})\s+(.+)$", line)
    if not match:
        return None
    price = parse_price(match.group(1))
    title = clean_title(match.group(2))
    if price is None or not title:
        return None
    return price, title


def parse_listings_from_text(html: str, keyword: str, status: str, search_url: str) -> list[Listing]:
    body_text = extract_body_text(html)
    lines = [normalize_space(line) for line in body_text.splitlines()]
    lines = [line for line in lines if line]
    listings: list[Listing] = []
    seen: set[tuple[str, int]] = set()

    index = 0
    while index < len(lines):
        line = lines[index]
        price: int | None = None
        title = ""

        parsed_inline = parse_text_price_title(line)
        if parsed_inline:
            price, title = parsed_inline
        elif line in {"現在", "売り切れ", "SOLD"} and index + 2 < len(lines):
            price = parse_price(lines[index + 1])
            title = lines[index + 2]
            index += 2
        elif ("¥" in line or "￥" in line) and index + 1 < len(lines):
            price = parse_price(line)
            title = lines[index + 1]
            index += 1

        index += 1
        title = clean_title(title)
        if price is None or not looks_like_listing_title(title, keyword):
            continue

        key = (title, price)
        if key in seen:
            continue
        seen.add(key)
        listings.append(
            Listing(
                keyword=keyword,
                title=title[:160],
                price=price,
                url=f"{search_url}#text-{len(listings) + 1}",
                status=status,
            )
        )

    return listings


def merge_listings(primary: list[Listing], fallback: list[Listing]) -> list[Listing]:
    merged: list[Listing] = []
    by_key: dict[str, Listing] = {}
    for listing in primary + fallback:
        key = listing.item_id or listing.url
        if key in by_key:
            current = by_key[key]
            if len(listing.description) > len(current.description):
                current.description = listing.description
            current.condition = current.condition or listing.condition
            current.shipping = current.shipping or listing.shipping
            current.listed_at = current.listed_at or listing.listed_at
            current.likes = current.likes if current.likes is not None else listing.likes
            current.image_count = max(current.image_count, listing.image_count)
            continue
        by_key[key] = listing
        merged.append(listing)
    return merged


def keyword_exclude_words(keyword: str, config: dict[str, Any]) -> list[str]:
    rules = config.get("keyword_exclude_words", {})
    if not isinstance(rules, dict):
        return []
    keyword_lower = keyword.lower()
    words: list[str] = []
    for rule_keyword, rule_words in rules.items():
        if not isinstance(rule_words, list):
            continue
        rule_lower = str(rule_keyword).lower()
        if rule_lower == keyword_lower or rule_lower in keyword_lower or keyword_lower in rule_lower:
            words.extend(str(word) for word in rule_words)
    return words


def keyword_include_words(keyword: str, config: dict[str, Any]) -> list[str]:
    rules = config.get("keyword_include_words", {})
    if not isinstance(rules, dict):
        return []
    keyword_lower = keyword.lower()
    words: list[str] = []
    for rule_keyword, rule_words in rules.items():
        if not isinstance(rule_words, list):
            continue
        rule_lower = str(rule_keyword).lower()
        if rule_lower == keyword_lower or rule_lower in keyword_lower or keyword_lower in rule_lower:
            words.extend(str(word) for word in rule_words)
    return words


def filter_keyword_inclusions(
    listings: list[Listing],
    keyword: str,
    config: dict[str, Any],
) -> tuple[list[Listing], int]:
    words = keyword_include_words(keyword, config)
    if not words:
        kept = [listing for listing in listings if title_matches_keyword(listing.title, keyword)]
        return kept, len(listings) - len(kept)

    kept: list[Listing] = []
    removed = 0
    for listing in listings:
        title_lower = listing.title.lower()
        if any(word.lower() in title_lower for word in words):
            kept.append(listing)
            continue
        removed += 1
    return kept, removed


def filter_keyword_exclusions(
    listings: list[Listing],
    keyword: str,
    config: dict[str, Any],
) -> tuple[list[Listing], int]:
    words = keyword_exclude_words(keyword, config)
    if not words:
        return listings, 0

    kept: list[Listing] = []
    removed = 0
    for listing in listings:
        text = "\n".join([listing.title, listing.description])
        if text_contains_any(text, words):
            removed += 1
            continue
        kept.append(listing)
    return kept, removed


def normalize_compare_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[【】\[\]（）()♡★☆✨⭐︎■・,，/／|｜]+", " ", text)
    return normalize_space(text)


def comparison_terms_for_keyword(keyword: str, config: dict[str, Any]) -> list[str]:
    rules = config.get("comparison_terms", {})
    if not isinstance(rules, dict):
        return []
    keyword_lower = keyword.lower()
    terms: list[str] = []
    for rule_keyword, rule_terms in rules.items():
        if not isinstance(rule_terms, list):
            continue
        rule_lower = str(rule_keyword).lower()
        if rule_lower == keyword_lower or rule_lower in keyword_lower or keyword_lower in rule_lower:
            terms.extend(str(term) for term in rule_terms)
    return terms


def compare_term_matches_text(term: str, text: str) -> bool:
    normalized = normalize_compare_text(term)
    if not normalized:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9\s.\-]*[a-z0-9]", normalized):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None
    return normalized in text


def configured_terms_in_title(title: str, keyword: str, config: dict[str, Any]) -> list[str]:
    text = normalize_compare_text(title)
    found: list[str] = []
    for term in comparison_terms_for_keyword(keyword, config):
        if compare_term_matches_text(term, text):
            found.append(term)
    return dedupe_preserve_order(found)


def title_tokens(title: str, keyword: str, config: dict[str, Any]) -> set[str]:
    text = normalize_compare_text(title)
    stop_words = {
        "美品",
        "新品",
        "未使用",
        "送料込",
        "送料無料",
        "レディース",
        "メンズ",
        "サイズ",
        "mercari",
        "apple",
        "nintendo",
        "switch",
        "ソフト",
        "ゲーム",
    }
    for word in keyword_include_words(keyword, config):
        normalized_word = normalize_compare_text(word)
        stop_words.add(normalized_word)
        stop_words.update(token for token in re.split(r"[\s　]+", normalized_word) if token)
    for token in re.split(r"[\s　]+", normalize_compare_text(keyword)):
        if token:
            stop_words.add(token)

    tokens = set()
    for token in re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)?|[ぁ-んァ-ヶー一-龥0-9]{2,}", text):
        token = normalize_compare_text(token)
        if not token or token in stop_words:
            continue
        if token.isdigit():
            continue
        tokens.add(token)
    return tokens


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = normalize_compare_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def inferred_comparison_terms(listing: Listing, keyword: str, config: dict[str, Any]) -> list[str]:
    terms = configured_terms_in_title(listing.title, keyword, config)
    text = normalize_compare_text(listing.title)

    patterns = [
        r"series\s*\d+",
        r"\bse\b",
        r"\bultra\b",
        r"\d{2}(?:\.\d)?\s*(?:mm|インチ)",
        r"\d{2,4}\s*gb",
        r"第\s*\d+\s*世代",
        r"wi-?fi",
        r"gps",
        r"セルラー",
    ]
    for pattern in patterns:
        terms.extend(match.group(0) for match in re.finditer(pattern, text))

    return dedupe_preserve_order(terms)


def title_overlap_ratio(current: Listing, sold: Listing, keyword: str, config: dict[str, Any]) -> float:
    current_tokens = title_tokens(current.title, keyword, config)
    sold_tokens = title_tokens(sold.title, keyword, config)
    if not current_tokens or not sold_tokens:
        return 0.0
    return len(current_tokens & sold_tokens) / len(current_tokens)


def comparable_sold_listings(
    listing: Listing,
    sold_listings: list[Listing],
    keyword: str,
    config: dict[str, Any],
) -> tuple[list[Listing], list[str]]:
    terms = inferred_comparison_terms(listing, keyword, config)
    normalized_terms = [normalize_compare_text(term) for term in terms]
    min_overlap = float(config.get("min_title_overlap_ratio", 0.34))
    comparable: list[Listing] = []

    for sold in sold_listings:
        sold_text = normalize_compare_text(sold.title)
        matched_terms = [term for term in normalized_terms if compare_term_matches_text(term, sold_text)]
        required_terms = min(
            len(normalized_terms),
            int(config.get("max_required_comparison_terms", 2)),
        )
        term_ok = bool(terms) and len(matched_terms) >= max(1, required_terms)
        overlap_ok = title_overlap_ratio(listing, sold, keyword, config) >= min_overlap

        if terms and term_ok:
            comparable.append(sold)
        elif not terms and overlap_ok:
            comparable.append(sold)

    return comparable, terms


def detect_blocked_page(html: str) -> bool:
    needles = [
        "私はロボットではありません",
        "reCAPTCHA で保護されています",
        "アクセスが集中",
        "しばらくしてからアクセス",
        "Access Denied",
        "Too Many Requests",
        "Request blocked",
    ]
    lower = html.lower()
    return any(needle.lower() in lower for needle in needles)


def condition_rank(condition: str, preferred: list[str]) -> int:
    if not condition:
        return 0
    for index, preferred_condition in enumerate(preferred):
        if preferred_condition in condition:
            return max(1, len(preferred) - index)
    if "やや傷" in condition:
        return -1
    if "傷や汚れあり" in condition or "全体的に状態が悪い" in condition:
        return -2
    return 0


def is_shipping_included(shipping: str, config: dict[str, Any]) -> bool:
    words = config.get("shipping_included_words", [])
    return any(word in shipping for word in words)


def listed_within_24h(listed_at: str) -> bool:
    text = listed_at.strip()
    if not text:
        return False
    if re.search(r"\d+\s*(秒|分|時間)前", text):
        return True
    day_match = re.search(r"(\d+)\s*日前", text)
    if day_match:
        return int(day_match.group(1)) < 1
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - parsed.astimezone(timezone.utc) <= timedelta(hours=24)


def score_deal(
    listing: Listing,
    median_price: int,
    config: dict[str, Any],
    comparable_count: int,
    comparison_terms: list[str],
) -> Deal:
    search_text = "\n".join([listing.title, listing.description, listing.condition, listing.shipping])
    warning_words = text_contains_any(search_text, config.get("warning_words", []))
    exclude_words = text_contains_any(search_text, config.get("exclude_words", []))
    bonus_words = text_contains_any(search_text, config.get("bonus_words", []))
    high_risk_words = text_contains_any(search_text, config.get("high_risk_brand_words", []))

    discount_rate = (median_price - listing.price) / median_price if median_price else 0.0
    score = 0
    score += min(45, max(0, int(discount_rate * 150)))

    rank = condition_rank(listing.condition, config.get("preferred_conditions", []))
    if rank > 0:
        score += 15
    elif rank < 0:
        score -= 15

    if is_shipping_included(listing.shipping, config):
        score += 10
    if listed_within_24h(listing.listed_at):
        score += 10
    if listing.image_count >= 3:
        score += 10
    elif listing.image_count == 0:
        score -= 5

    score += min(10, len(bonus_words) * 4)
    score -= min(30, len(warning_words) * 10)
    if high_risk_words and not text_contains_any(search_text, ["正規品", "保証書", "箱", "レシート", "鑑定"]):
        warning_words.extend([f"高リスクブランド:{word}" for word in high_risk_words])
        score -= 10
    if exclude_words:
        score = 0

    return Deal(
        keyword=listing.keyword,
        title=listing.title,
        current_price=listing.price,
        median_price=median_price,
        comparable_count=comparable_count,
        comparison_terms=comparison_terms,
        discount_rate=discount_rate,
        condition=listing.condition,
        shipping=listing.shipping,
        listed_at=listing.listed_at,
        likes=listing.likes,
        url=listing.url,
        warning_words=sorted(set(warning_words)),
        exclude_words=sorted(set(exclude_words)),
        bonus_words=sorted(set(bonus_words)),
        score=max(0, min(100, score)),
    )


class MercariFetcher:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.playwright = None
        self.browser: Browser | None = None

    async def __aenter__(self) -> "MercariFetcher":
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=bool(self.config.get("headless", True)))
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    def search_url(self, keyword: str, status: str) -> str:
        query = quote_plus(keyword)
        # status values are public web search parameters used by Mercari's web UI.
        return (
            f"{MERCARI_BASE_URL}/search?"
            f"keyword={query}&status={status}&sort=created_time&order=desc"
        )

    async def polite_wait(self) -> None:
        wait_conf = self.config.get("random_wait_seconds", {})
        min_wait = float(wait_conf.get("min", 5))
        max_wait = float(wait_conf.get("max", 12))
        await asyncio.sleep(random.uniform(min_wait, max_wait))

    async def new_page(self) -> Page:
        if not self.browser:
            raise RuntimeError("Browser is not started")
        context = await self.browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1365, "height": 900},
        )
        return await context.new_page()

    async def fetch_html(self, url: str) -> str:
        page = await self.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=int(self.config.get("page_timeout_ms", 45000)))
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)
            for _ in range(3):
                await page.mouse.wheel(0, 900)
                await page.wait_for_timeout(700)
            html = await page.content()
            try:
                body_text = await page.locator("body").inner_text(timeout=5000)
            except Exception:
                body_text = ""
            payload = html + BODY_TEXT_MARKER + body_text if body_text else html
            if detect_blocked_page(payload):
                raise MercariAccessError("Mercari returned an access check or blocked page")
            return payload
        finally:
            await page.context.close()

    async def fetch_search(self, keyword: str, status: str, limit: int) -> list[Listing]:
        url = self.search_url(keyword, status)
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            await self.polite_wait()
            logging.info(
                "Fetching search keyword=%s status=%s attempt=%s url=%s",
                keyword,
                status,
                attempt,
                url,
            )
            html = await self.fetch_html(url)
            json_listings = parse_listings_from_json(html, keyword, status)
            dom_listings = parse_listings_from_dom(html, keyword, status)
            text_listings = parse_listings_from_text(html, keyword, status, url)
            listings = merge_listings(merge_listings(json_listings, dom_listings), text_listings)
            listings, keyword_included = filter_keyword_inclusions(listings, keyword, self.config)
            listings, keyword_excluded = filter_keyword_exclusions(listings, keyword, self.config)
            logging.info(
                "Found %s listings for keyword=%s status=%s json=%s dom=%s text=%s keyword_include_removed=%s keyword_excluded=%s",
                len(listings),
                keyword,
                status,
                len(json_listings),
                len(dom_listings),
                len(text_listings),
                keyword_included,
                keyword_excluded,
            )
            if listings or attempt == max_attempts:
                return listings[:limit]
            logging.warning("No listings found; retry keyword=%s status=%s", keyword, status)
        return []

    async def enrich_listing(self, listing: Listing) -> Listing:
        await self.polite_wait()
        logging.info("Fetching detail url=%s", listing.url)
        try:
            html = await self.fetch_html(listing.url)
        except MercariAccessError:
            logging.warning("Detail page blocked url=%s", listing.url)
            return listing

        json_listings = parse_listings_from_json(html, listing.keyword, listing.status)
        for candidate in json_listings:
            if candidate.item_id and listing.item_id and candidate.item_id != listing.item_id:
                continue
            listing.description = listing.description or candidate.description
            listing.condition = listing.condition or candidate.condition
            listing.shipping = listing.shipping or candidate.shipping
            listing.listed_at = listing.listed_at or candidate.listed_at
            listing.likes = listing.likes if listing.likes is not None else candidate.likes
            listing.image_count = max(listing.image_count, candidate.image_count)
            break

        soup = BeautifulSoup(html, "html.parser")
        listing.description = listing.description or meta_content(soup, "description")
        listing.image_count = max(listing.image_count, len(soup.find_all("img")))
        page_text = normalize_space(soup.get_text(" ", strip=True))
        listing.condition = listing.condition or find_nearby_value(page_text, ["商品の状態", "状態"])
        listing.shipping = listing.shipping or find_nearby_value(page_text, ["送料", "配送料"])
        listing.listed_at = listing.listed_at or find_relative_time(page_text)
        if listing.likes is None:
            listing.likes = find_likes(page_text)
        return listing


def meta_content(soup: BeautifulSoup, name: str) -> str:
    meta = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": f"og:{name}"})
    if not meta:
        return ""
    return normalize_space(meta.get("content", ""))


def find_nearby_value(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*([^\s]{{2,30}})"
        match = re.search(pattern, text)
        if match:
            return normalize_space(match.group(1))
    return ""


def find_relative_time(text: str) -> str:
    match = re.search(r"\d+\s*(秒|分|時間|日)前", text)
    return match.group(0) if match else ""


def find_likes(text: str) -> int | None:
    patterns = [r"いいね[！!]?\s*(\d+)", r"(\d+)\s*いいね"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def median_price(listings: list[Listing]) -> int | None:
    prices = [listing.price for listing in listings if listing.price > 0]
    if not prices:
        return None
    return int(statistics.median(prices))


def deal_passes_notification(deal: Deal, config: dict[str, Any]) -> bool:
    return (
        deal.discount_rate >= float(config.get("min_discount_rate", 0.2))
        and deal.score >= int(config.get("min_score", 70))
        and not deal.exclude_words
    )


def csv_path_for_today() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(JST).strftime("%Y%m%d")
    return DATA_DIR / f"mercari_deals_{today}.csv"


def markdown_path_for_today() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(JST).strftime("%Y%m%d")
    return DATA_DIR / f"mercari_deals_{today}.md"


def save_csv(deals: list[Deal]) -> Path:
    path = csv_path_for_today()
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for deal in deals:
            writer.writerow(deal.to_csv_row())
    return path


def save_markdown(deals: list[Deal], notify_deals: list[Deal]) -> Path:
    path = markdown_path_for_today()
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    lines = [
        "# メルカリ割安候補",
        "",
        f"- 作成時刻: {now}",
        f"- 候補数: {len(deals)}",
        f"- Slack通知条件候補: {len(notify_deals)}",
        "",
    ]

    if notify_deals:
        lines.extend(["## Slack通知条件候補", ""])
        for index, deal in enumerate(notify_deals, start=1):
            lines.extend(markdown_deal_lines(index, deal))

    lines.extend(["## 全候補", ""])
    if not deals:
        lines.append("候補はありませんでした。")
    for index, deal in enumerate(deals, start=1):
        lines.extend(markdown_deal_lines(index, deal))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def markdown_deal_lines(index: int, deal: Deal) -> list[str]:
    warning = ", ".join(deal.warning_words) if deal.warning_words else "なし"
    condition = deal.condition or "未取得"
    shipping = deal.shipping or "未取得"
    return [
        f"### {index}. {deal.keyword}",
        "",
        f"- 商品名: {deal.title}",
        f"- 価格: {deal.current_price:,}円",
        f"- 相場中央値: {deal.median_price:,}円",
        f"- 相場比較件数: {deal.comparable_count}",
        f"- 相場比較語: {', '.join(deal.comparison_terms) or '未取得'}",
        f"- 割安率: {deal.discount_rate:.1%}",
        f"- スコア: {deal.score}",
        f"- 状態: {condition}",
        f"- 送料: {shipping}",
        f"- 警告: {warning}",
        f"- 商品リンク: [メルカリで開く]({deal.url})",
        "",
    ]


def slack_message_for_deals(deals: list[Deal]) -> str:
    chunks = ["【メルカリ割安候補】"]
    for index, deal in enumerate(deals[:10], start=1):
        notes = []
        if deal.warning_words:
            notes.append("警告: " + ", ".join(deal.warning_words))
        if deal.bonus_words:
            notes.append("加点: " + ", ".join(deal.bonus_words[:5]))
        note_text = " / ".join(notes) if notes else "特になし"
        chunks.append(
            "\n".join(
                [
                    f"\n#{index}",
                    f"キーワード：{deal.keyword}",
                    f"商品名：{deal.title}",
                    f"価格：{deal.current_price:,}円",
                    f"相場中央値：{deal.median_price:,}円",
                    f"相場比較件数：{deal.comparable_count}",
                    f"相場比較語：{', '.join(deal.comparison_terms) or '未取得'}",
                    f"割安率：{deal.discount_rate:.1%}",
                    f"状態：{deal.condition or '未取得'}",
                    f"URL：{deal.url}",
                    f"注意点：{note_text}",
                ]
            )
        )
    if len(deals) > 10:
        chunks.append(f"\nほか {len(deals) - 10} 件あります。CSVを確認してください。")
    return "\n".join(chunks)


def send_slack(webhook_url: str, text: str) -> None:
    response = requests.post(webhook_url, json={"text": text}, timeout=20)
    response.raise_for_status()
    if response.text.strip() != "ok":
        raise RuntimeError(f"Slack response was not ok: {response.text}")


def sample_listings() -> tuple[dict[str, list[Listing]], dict[str, list[Listing]]]:
    current = {
        "Apple Watch": [
            Listing(
                keyword="Apple Watch",
                title="Apple Watch SE 40mm GPS 箱付き 美品",
                price=15800,
                url="https://jp.mercari.com/item/m12345678900",
                status="on_sale",
                condition="目立った傷や汚れなし",
                shipping="送料込み",
                listed_at="3時間前",
                likes=4,
                description="正規品 箱 付属品あり。動作確認済み。",
                image_count=5,
            ),
            Listing(
                keyword="Apple Watch",
                title="Apple Watch ジャンク 画面割れ",
                price=9000,
                url="https://jp.mercari.com/item/m12345678901",
                status="on_sale",
                condition="全体的に状態が悪い",
                shipping="着払い",
                listed_at="1時間前",
                likes=1,
                description="ジャンク 画面割れ 難あり。",
                image_count=2,
            ),
        ]
    }
    sold = {
        "Apple Watch": [
            Listing("Apple Watch", "Apple Watch SE 40mm", 22000, "https://jp.mercari.com/item/m1", "sold_out"),
            Listing("Apple Watch", "Apple Watch SE 40mm", 23500, "https://jp.mercari.com/item/m2", "sold_out"),
            Listing("Apple Watch", "Apple Watch SE 40mm", 21000, "https://jp.mercari.com/item/m3", "sold_out"),
            Listing("Apple Watch", "Apple Watch SE 40mm", 24000, "https://jp.mercari.com/item/m4", "sold_out"),
            Listing("Apple Watch", "Apple Watch SE 40mm", 22500, "https://jp.mercari.com/item/m5", "sold_out"),
        ]
    }
    return current, sold


async def analyze_keyword(
    keyword: str,
    config: dict[str, Any],
    fetcher: MercariFetcher | None,
    sample_current: dict[str, list[Listing]] | None = None,
    sample_sold: dict[str, list[Listing]] | None = None,
) -> list[Deal]:
    logging.info("Start keyword=%s", keyword)
    try:
        if sample_current is not None and sample_sold is not None:
            current_listings = sample_current.get(keyword, [])
            sold_listings = sample_sold.get(keyword, [])
        else:
            if fetcher is None:
                raise RuntimeError("fetcher is required for live run")
            current_listings = await fetcher.fetch_search(
                keyword,
                "on_sale",
                int(config.get("max_current_items_per_keyword", 20)),
            )
            sold_listings = await fetcher.fetch_search(
                keyword,
                "sold_out",
                int(config.get("max_sold_items_per_keyword", 40)),
            )

        min_sold_samples = int(config.get("min_sold_samples", 5))
        if len(sold_listings) < min_sold_samples:
            logging.warning("Skip keyword=%s: sold samples=%s", keyword, len(sold_listings))
            return []

        if fetcher and config.get("fetch_detail_pages", True):
            detail_limit = int(config.get("max_detail_pages_per_keyword", 8))
            enriched: list[Listing] = []
            for listing in current_listings[:detail_limit]:
                enriched.append(await fetcher.enrich_listing(listing))
            current_listings = enriched + current_listings[detail_limit:]

        min_comparable_samples = int(config.get("min_comparable_sold_samples", 3))
        deals: list[Deal] = []
        skipped_no_comparable = 0
        for listing in current_listings:
            comparable_sold, comparison_terms = comparable_sold_listings(
                listing,
                sold_listings,
                keyword,
                config,
            )
            if len(comparable_sold) < min_comparable_samples:
                skipped_no_comparable += 1
                logging.info(
                    "Skip listing: comparable sold samples too few keyword=%s title=%s comparable=%s terms=%s",
                    keyword,
                    listing.title,
                    len(comparable_sold),
                    ",".join(comparison_terms),
                )
                continue

            item_median = median_price(comparable_sold)
            if not item_median:
                skipped_no_comparable += 1
                continue
            deals.append(
                score_deal(
                    listing,
                    item_median,
                    config,
                    comparable_count=len(comparable_sold),
                    comparison_terms=comparison_terms,
                )
            )

        qualified = [
            deal
            for deal in deals
            if deal.discount_rate >= float(config.get("min_discount_rate", 0.2))
        ]
        qualified.sort(key=lambda deal: (deal.score, deal.discount_rate), reverse=True)
        logging.info(
            "Keyword=%s qualified=%s skipped_no_comparable=%s",
            keyword,
            len(qualified),
            skipped_no_comparable,
        )
        return qualified
    except Exception as exc:
        logging.exception("Keyword failed: %s error=%s", keyword, exc)
        return []


async def run(config: dict[str, Any], keywords: list[str], use_sample: bool) -> list[Deal]:
    if use_sample:
        sample_current, sample_sold = sample_listings()
        sample_keywords = keywords or list(sample_current.keys())
        all_deals: list[Deal] = []
        for keyword in sample_keywords:
            all_deals.extend(
                await analyze_keyword(keyword, config, None, sample_current, sample_sold)
            )
        return all_deals

    all_deals = []
    async with MercariFetcher(config) as fetcher:
        for keyword in keywords:
            all_deals.extend(await analyze_keyword(keyword, config, fetcher))
    return all_deals


def test_slack(config: dict[str, Any]) -> int:
    webhook_url = config.get("slack_webhook_url", "")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL or config.slack_webhook_url is missing")
        return 1
    send_slack(webhook_url, "メルカリ割安検出テスト: Slack通知は動いています。")
    print("Slack test sent")
    return 0


def main() -> int:
    args = parse_args()
    setup_logging()

    config_path = Path(args.config)
    config = deep_merge_config(load_config(config_path))

    if args.test_slack:
        return test_slack(config)

    keywords = args.keyword or config.get("keywords", [])
    if args.sample and not args.keyword:
        keywords = ["Apple Watch"]
    if not keywords:
        print("No keywords configured")
        return 1

    logging.info("Run started sample=%s dry_run=%s keywords=%s", args.sample, args.dry_run, keywords)
    deals = asyncio.run(run(config, keywords, args.sample))
    csv_path = save_csv(deals)

    notify_deals = [deal for deal in deals if deal_passes_notification(deal, config)]
    markdown_path = save_markdown(deals, notify_deals)
    print(f"CSV saved: {csv_path}")
    print(f"Markdown saved: {markdown_path}")
    print(f"Deals: {len(deals)} / Slack candidates: {len(notify_deals)}")

    if notify_deals and config.get("slack_notify", True) and not args.dry_run:
        webhook_url = config.get("slack_webhook_url", "")
        if not webhook_url:
            logging.warning("Slack webhook missing; notification skipped")
            print("Slack webhook missing; notification skipped")
        else:
            send_slack(webhook_url, slack_message_for_deals(notify_deals))
            print("Slack notification sent")
    else:
        logging.info("Slack notification skipped")

    logging.info("Run finished deals=%s notify=%s csv=%s", len(deals), len(notify_deals), csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
