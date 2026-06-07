#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Substack like/follow assistant.

Default mode is dry-run: it only lists candidates and never clicks.
Use --execute to allow clicks. Without --yes, every click still asks for approval.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "substack_like_follow_safe.log"
STATE_FILE = BASE_DIR / "substack_like_follow_state.json"

DEFAULT_MAX_LIKES = 10
DEFAULT_MAX_FOLLOWS = 5
DEFAULT_MIN_WAIT = 3.0
DEFAULT_MAX_WAIT = 8.0
DEFAULT_SCROLLS = 12

POST_SELECTORS = "div[data-testid='post-list-item'], article, .feed-item"
LIKE_SELECTORS = [
    "button[aria-label*='Like']",
    "button[aria-label*='like']",
    "button[aria-label*='いいね']",
    "button[data-testid*='like']",
]
FOLLOW_XPATH = (
    ".//button["
    "(contains(normalize-space(.), 'Follow') or contains(normalize-space(.), 'フォロー'))"
    " and not(contains(normalize-space(.), 'Following'))"
    " and not(contains(normalize-space(.), 'フォロー中'))"
    " and not(contains(normalize-space(.), 'フォロー済み'))"
    " and not(contains(normalize-space(.), 'Subscribe'))"
    " and not(contains(normalize-space(.), 'Subscribed'))"
    " and not(contains(normalize-space(.), '登録'))"
    " and not(contains(normalize-space(.), '購読'))"
    "]"
)


@dataclass(frozen=True)
class Candidate:
    action: str
    key: str
    title: str
    author: str
    url: str
    button_text: str


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )


def is_japanese(text: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ヶー一-龠]", text))


def clean_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def load_state() -> dict[str, list[str]]:
    if not STATE_FILE.exists():
        return {"liked_posts": [], "followed_authors": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("State file is broken. Starting with empty state: %s", STATE_FILE)
        return {"liked_posts": [], "followed_authors": []}
    return {
        "liked_posts": list(data.get("liked_posts", [])),
        "followed_authors": list(data.get("followed_authors", [])),
    }


def save_state(state: dict[str, list[str]]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_driver_options(args: argparse.Namespace) -> Options:
    chrome_options = Options()
    profile_dir = Path(args.profile_dir).expanduser()
    chrome_options.add_argument(f"user-data-dir={profile_dir}")
    chrome_options.add_argument("--window-size=1440,1100")

    if args.headless or platform.system() == "Linux":
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

    return chrome_options


def first_text_line(text: str, max_len: int = 90) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:max_len]
    return "(no title)"


def find_first_href(root: WebElement, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            links = root.find_elements(By.CSS_SELECTOR, selector)
        except StaleElementReferenceException:
            return ""
        for link in links:
            href = link.get_attribute("href")
            if href:
                return clean_url(href)
    return ""


def post_key(post: WebElement) -> str:
    url = find_first_href(
        post,
        [
            "a[href*='/p/']",
            "a[href*='substack.com/p/']",
            "a[href]",
        ],
    )
    if url:
        return url
    return str(abs(hash(post.text[:300])))


def author_key(post: WebElement) -> str:
    url = find_first_href(
        post,
        [
            "a[href*='substack.com/@']",
            "a[href*='/@']",
            "a[href]",
        ],
    )
    if url:
        return url
    return first_text_line(post.text, max_len=60)


def is_like_active(button: WebElement) -> bool:
    aria_pressed = (button.get_attribute("aria-pressed") or "").lower()
    aria_label = (button.get_attribute("aria-label") or "").lower()
    class_name = (button.get_attribute("class") or "").lower()
    text = (button.text or "").strip().lower()

    active_markers = [
        aria_pressed == "true",
        "unlike" in aria_label,
        "liked" in aria_label,
        "いいね済み" in aria_label,
        "active" in class_name,
        "liked" in class_name,
        "いいね済み" in text,
    ]
    return any(active_markers)


def is_safe_follow_button(button: WebElement) -> bool:
    text = (button.text or "").strip()
    aria_label = (button.get_attribute("aria-label") or "").strip()
    combined = f"{text} {aria_label}"

    blocked_words = [
        "Subscribe",
        "Subscribed",
        "登録",
        "購読",
        "Following",
        "フォロー中",
        "フォロー済み",
        "済み",
    ]
    if any(word in combined for word in blocked_words):
        return False

    allowed_words = ["Follow", "フォロー"]
    return any(word in combined for word in allowed_words)


def summarize_candidate(candidate: Candidate) -> str:
    return (
        f"{candidate.action.upper()} | {candidate.author} | "
        f"{candidate.title} | button='{candidate.button_text}' | {candidate.url}"
    )


def should_click(candidate: Candidate, args: argparse.Namespace) -> bool:
    if not args.execute:
        logging.info("[DRY-RUN] %s", summarize_candidate(candidate))
        return False

    logging.info("[CANDIDATE] %s", summarize_candidate(candidate))
    if args.yes:
        return True

    answer = input("この候補をクリックしますか？ [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def safe_click(driver: webdriver.Chrome, button: WebElement, candidate: Candidate) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(button))
        button.click()
        return True
    except (ElementClickInterceptedException, StaleElementReferenceException, TimeoutException, WebDriverException) as exc:
        logging.warning("Click skipped: %s | %s", summarize_candidate(candidate), exc)
        return False


def sleep_between_actions(args: argparse.Namespace) -> None:
    wait_seconds = random.uniform(args.min_wait, args.max_wait)
    logging.info("Waiting %.1f seconds", wait_seconds)
    time.sleep(wait_seconds)


def collect_like_buttons(post: WebElement) -> list[WebElement]:
    buttons: list[WebElement] = []
    for selector in LIKE_SELECTORS:
        try:
            buttons.extend(post.find_elements(By.CSS_SELECTOR, selector))
        except StaleElementReferenceException:
            return []
    return buttons


def process_post(
    driver: webdriver.Chrome,
    post: WebElement,
    state: dict[str, list[str]],
    counts: dict[str, int],
    args: argparse.Namespace,
) -> None:
    try:
        content_text = post.text
    except StaleElementReferenceException:
        return

    if not content_text or not is_japanese(content_text):
        return

    title = first_text_line(content_text)
    post_url = post_key(post)
    author = author_key(post)

    if counts["likes"] < args.max_likes and post_url not in state["liked_posts"]:
        for button in collect_like_buttons(post):
            try:
                if not button.is_displayed() or is_like_active(button):
                    continue
            except StaleElementReferenceException:
                continue

            candidate = Candidate(
                action="like",
                key=post_url,
                title=title,
                author=author,
                url=post_url,
                button_text=(button.text or button.get_attribute("aria-label") or "").strip(),
            )
            if should_click(candidate, args) and safe_click(driver, button, candidate):
                counts["likes"] += 1
                state["liked_posts"].append(post_url)
                save_state(state)
                logging.info("Liked (%s/%s)", counts["likes"], args.max_likes)
                sleep_between_actions(args)
            break

    if counts["follows"] < args.max_follows and author not in state["followed_authors"]:
        try:
            buttons = post.find_elements(By.XPATH, FOLLOW_XPATH)
        except StaleElementReferenceException:
            return

        for button in buttons:
            try:
                if not button.is_displayed() or not is_safe_follow_button(button):
                    continue
            except StaleElementReferenceException:
                continue

            candidate = Candidate(
                action="follow",
                key=author,
                title=title,
                author=author,
                url=post_url,
                button_text=(button.text or button.get_attribute("aria-label") or "").strip(),
            )
            if should_click(candidate, args) and safe_click(driver, button, candidate):
                counts["follows"] += 1
                state["followed_authors"].append(author)
                save_state(state)
                logging.info("Followed (%s/%s)", counts["follows"], args.max_follows)
                sleep_between_actions(args)
            break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe Substack like/follow assistant")
    parser.add_argument("--execute", action="store_true", help="Actually click approved candidates")
    parser.add_argument("--yes", action="store_true", help="Do not ask per-click confirmation")
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode")
    parser.add_argument("--max-likes", type=int, default=DEFAULT_MAX_LIKES)
    parser.add_argument("--max-follows", type=int, default=DEFAULT_MAX_FOLLOWS)
    parser.add_argument("--min-wait", type=float, default=DEFAULT_MIN_WAIT)
    parser.add_argument("--max-wait", type=float, default=DEFAULT_MAX_WAIT)
    parser.add_argument("--scrolls", type=int, default=DEFAULT_SCROLLS)
    parser.add_argument("--profile-dir", default="~/.substack_safe_profile")
    parser.add_argument("--url", default="https://substack.com/home")
    return parser.parse_args()


def run_bot() -> int:
    args = parse_args()
    setup_logging()

    if args.yes and not args.execute:
        logging.warning("--yes is ignored without --execute")

    if args.min_wait > args.max_wait:
        logging.error("--min-wait must be smaller than --max-wait")
        return 2

    state = load_state()
    counts = {"likes": 0, "follows": 0}

    logging.info(
        "Starting Substack assistant mode=%s max_likes=%s max_follows=%s",
        "execute" if args.execute else "dry-run",
        args.max_likes,
        args.max_follows,
    )

    driver: webdriver.Chrome | None = None
    try:
        options = get_driver_options(args)
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        wait = WebDriverWait(driver, 25)

        driver.get(args.url)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
        time.sleep(4)

        for scroll_count in range(args.scrolls):
            logging.info("Checking view %s/%s", scroll_count + 1, args.scrolls)

            try:
                posts = driver.find_elements(By.CSS_SELECTOR, POST_SELECTORS)
            except NoSuchElementException:
                posts = []

            logging.info("Found %s post-like elements", len(posts))
            for post in posts:
                process_post(driver, post, state, counts, args)
                if counts["likes"] >= args.max_likes and counts["follows"] >= args.max_follows:
                    logging.info("Goal reached")
                    return 0

            driver.execute_script("window.scrollBy(0, 900);")
            time.sleep(random.uniform(2.5, 5.0))

        logging.info("Finished. Likes=%s Follows=%s", counts["likes"], counts["follows"])
        if not args.execute:
            logging.info("Dry-run only. Add --execute to click, and --yes to skip prompts.")
        return 0

    except KeyboardInterrupt:
        logging.info("Stopped by user")
        return 130
    except WebDriverException as exc:
        logging.exception("Browser error: %s", exc)
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(run_bot())
