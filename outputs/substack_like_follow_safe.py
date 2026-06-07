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
DEFAULT_MAX_ACTIONS = 6
DEFAULT_MIN_WAIT = 20.0
DEFAULT_MAX_WAIT = 90.0
DEFAULT_SCROLLS = 12
DEFAULT_VIEW_MIN_WAIT = 4.0
DEFAULT_VIEW_MAX_WAIT = 14.0

POST_SELECTORS = (
    "article, "
    "div[data-testid='post-list-item'], "
    "div[data-testid*='post'], "
    "div[data-testid*='feed'], "
    ".feed-item, "
    "[class*='feed-item'], "
    "[class*='post-item']"
)
POST_XPATHS = [
    "//a[contains(@href, '/p/')]/ancestor::article[1]",
    "//a[contains(@href, '/p/')]/ancestor::div[1]",
    "//*[contains(normalize-space(.), '登録') and string-length(normalize-space(.)) > 80]/ancestor-or-self::div[1]",
    "//*[contains(normalize-space(.), 'いいね') and string-length(normalize-space(.)) > 80]/ancestor-or-self::div[1]",
    "//*[contains(normalize-space(.), '1d') and string-length(normalize-space(.)) > 80]/ancestor-or-self::div[1]",
    "//*[contains(normalize-space(.), 'h') and string-length(normalize-space(.)) > 120]/ancestor-or-self::div[1]",
    "//button[contains(@aria-label, 'Like') or contains(@aria-label, 'like') or contains(@aria-label, 'いいね')]/ancestor::article[1]",
    "//button[contains(@aria-label, 'Like') or contains(@aria-label, 'like') or contains(@aria-label, 'いいね')]/ancestor::div[1]",
    "//button[contains(normalize-space(.), 'Follow') or contains(normalize-space(.), 'フォロー')]/ancestor::article[1]",
    "//button[contains(normalize-space(.), 'Follow') or contains(normalize-space(.), 'フォロー')]/ancestor::div[1]",
]
LIKE_SELECTORS = [
    "button[aria-label*='Like']",
    "button[aria-label*='like']",
    "button[aria-label*='いいね']",
    "button[data-testid*='like']",
]
LIKE_BLOCK_WORDS = [
    "Follow",
    "Following",
    "Subscribe",
    "Subscribed",
    "フォロー",
    "登録",
    "購読",
    "コメント",
    "返信",
    "Comment",
    "Reply",
    "Restack",
    "Share",
    "シェア",
    "共有",
    "More",
    "more",
    "もっと",
    "メニュー",
    "閉じる",
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


def is_probably_login_page(text: str, url: str) -> bool:
    combined = f"{url}\n{text}".lower()
    login_markers = [
        "sign in",
        "log in",
        "login",
        "ログイン",
        "メールアドレス",
        "continue with",
        "magic link",
    ]
    return any(marker in combined for marker in login_markers)


def save_debug_snapshot(driver: webdriver.Chrome, label: str) -> None:
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "debug"
    html_path = LOG_DIR / f"substack_{safe_label}.html"
    png_path = LOG_DIR / f"substack_{safe_label}.png"

    try:
        html_path.write_text(driver.page_source, encoding="utf-8")
        driver.save_screenshot(str(png_path))
        logging.info("Saved debug HTML: %s", html_path)
        logging.info("Saved debug screenshot: %s", png_path)
    except WebDriverException as exc:
        logging.warning("Could not save debug snapshot: %s", exc)


def log_page_diagnostics(driver: webdriver.Chrome, label: str) -> None:
    try:
        body_text = driver.find_element(By.CSS_SELECTOR, "body").text
    except (NoSuchElementException, StaleElementReferenceException):
        body_text = ""

    like_buttons = driver.find_elements(
        By.XPATH,
        "//button[contains(@aria-label, 'Like') or contains(@aria-label, 'like') or contains(@aria-label, 'いいね')]",
    )
    follow_buttons = driver.find_elements(
        By.XPATH,
        "//button[contains(normalize-space(.), 'Follow') or contains(normalize-space(.), 'フォロー') or contains(normalize-space(.), 'Subscribe') or contains(normalize-space(.), '登録')]",
    )
    post_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/p/']")

    logging.info("Page URL: %s", driver.current_url)
    logging.info("Page title: %s", driver.title)
    logging.info(
        "Diagnostics %s: like_buttons=%s follow_or_subscribe_buttons=%s post_links=%s body_chars=%s",
        label,
        len(like_buttons),
        len(follow_buttons),
        len(post_links),
        len(body_text),
    )

    if is_probably_login_page(body_text, driver.current_url):
        logging.warning(
            "Substack login may be required. Run with --login-wait 180, log in in the Chrome window, then run again."
        )

    if body_text:
        sample = " ".join(body_text.split())[:500]
        logging.info("Body sample: %s", sample)

    save_debug_snapshot(driver, label)


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


def is_safe_like_button(button: WebElement) -> bool:
    text = (button.text or "").strip()
    aria_label = (button.get_attribute("aria-label") or "").strip()
    title = (button.get_attribute("title") or "").strip()
    combined = f"{text} {aria_label} {title}"

    if any(word in combined for word in LIKE_BLOCK_WORDS):
        return False
    if is_like_active(button):
        return False

    explicit_like_words = ["Like", "like", "いいね"]
    if any(word in combined for word in explicit_like_words):
        return True

    try:
        svgs = button.find_elements(By.CSS_SELECTOR, "svg")
    except StaleElementReferenceException:
        return False

    if not svgs:
        return False

    # Substack often renders the heart as an icon-only button with just a count.
    # In that layout, comment/share/restack buttons are excluded above and the
    # remaining small SVG button is the like button.
    if not text:
        return True
    return bool(re.fullmatch(r"[0-9,.\sKk万]+", text))


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
    logging.info("Waiting %.1f seconds before next possible action", wait_seconds)
    time.sleep(wait_seconds)


def sleep_between_views(args: argparse.Namespace) -> None:
    wait_seconds = random.uniform(args.view_min_wait, args.view_max_wait)
    logging.info("Browsing pause %.1f seconds", wait_seconds)
    time.sleep(wait_seconds)


def maybe_human_pause(args: argparse.Namespace) -> None:
    if not args.execute:
        return
    if random.random() < args.long_pause_chance:
        wait_seconds = random.uniform(args.long_pause_min, args.long_pause_max)
        logging.info("Long reading pause %.1f seconds", wait_seconds)
        time.sleep(wait_seconds)


def scroll_like_browsing(driver: webdriver.Chrome, args: argparse.Namespace) -> None:
    if args.execute:
        scroll_pixels = random.randint(args.scroll_min, args.scroll_max)
        if random.random() < 0.25:
            scroll_pixels = -random.randint(120, 360)
    else:
        scroll_pixels = 900
    logging.info("Scrolling by %s px", scroll_pixels)
    driver.execute_script("window.scrollBy(0, arguments[0]);", scroll_pixels)


def collect_like_buttons(post: WebElement) -> list[WebElement]:
    buttons: list[WebElement] = []
    for selector in LIKE_SELECTORS:
        try:
            buttons.extend(post.find_elements(By.CSS_SELECTOR, selector))
        except StaleElementReferenceException:
            return []

    try:
        buttons.extend(post.find_elements(By.CSS_SELECTOR, "button:has(svg)"))
    except StaleElementReferenceException:
        return []

    unique: list[WebElement] = []
    seen: set[str] = set()
    for button in buttons:
        if button.id in seen:
            continue
        seen.add(button.id)
        unique.append(button)
    return unique


def collect_post_elements(driver: webdriver.Chrome) -> list[WebElement]:
    elements: list[WebElement] = []
    seen: set[str] = set()

    def add_candidates(candidates: list[WebElement]) -> None:
        for element in candidates:
            if element.id in seen:
                continue
            seen.add(element.id)
            elements.append(element)

    try:
        add_candidates(driver.find_elements(By.CSS_SELECTOR, POST_SELECTORS))
    except NoSuchElementException:
        pass

    for xpath in POST_XPATHS:
        try:
            add_candidates(driver.find_elements(By.XPATH, xpath))
        except NoSuchElementException:
            continue

    filtered: list[WebElement] = []
    for element in elements:
        try:
            if not element.is_displayed():
                continue
            text = element.text.strip()
            rect = element.rect
        except StaleElementReferenceException:
            continue

        if not is_japanese(text):
            continue
        if len(text) < 40 or len(text) > 3000:
            continue
        if rect.get("width", 0) < 250 or rect.get("height", 0) < 40:
            continue
        filtered.append(element)

    return filtered


def process_post(
    driver: webdriver.Chrome,
    post: WebElement,
    state: dict[str, list[str]],
    counts: dict[str, int],
    args: argparse.Namespace,
) -> None:
    if counts["actions"] >= args.max_actions:
        return

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
                if not button.is_displayed() or not is_safe_like_button(button):
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
                counts["actions"] += 1
                state["liked_posts"].append(post_url)
                save_state(state)
                logging.info("Liked (%s/%s)", counts["likes"], args.max_likes)
                sleep_between_actions(args)
                if counts["actions"] >= args.max_actions:
                    return
            break

    if counts["actions"] >= args.max_actions:
        return

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
                counts["actions"] += 1
                state["followed_authors"].append(author)
                save_state(state)
                logging.info("Followed (%s/%s)", counts["follows"], args.max_follows)
                sleep_between_actions(args)
                if counts["actions"] >= args.max_actions:
                    return
            break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe Substack like/follow assistant")
    parser.add_argument("--execute", action="store_true", help="Actually click approved candidates")
    parser.add_argument("--yes", action="store_true", help="Do not ask per-click confirmation")
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode")
    parser.add_argument("--max-likes", type=int, default=DEFAULT_MAX_LIKES)
    parser.add_argument("--max-follows", type=int, default=DEFAULT_MAX_FOLLOWS)
    parser.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS)
    parser.add_argument("--min-wait", type=float, default=DEFAULT_MIN_WAIT)
    parser.add_argument("--max-wait", type=float, default=DEFAULT_MAX_WAIT)
    parser.add_argument("--view-min-wait", type=float, default=DEFAULT_VIEW_MIN_WAIT)
    parser.add_argument("--view-max-wait", type=float, default=DEFAULT_VIEW_MAX_WAIT)
    parser.add_argument("--scroll-min", type=int, default=350)
    parser.add_argument("--scroll-max", type=int, default=1200)
    parser.add_argument("--long-pause-chance", type=float, default=0.18)
    parser.add_argument("--long-pause-min", type=float, default=90.0)
    parser.add_argument("--long-pause-max", type=float, default=240.0)
    parser.add_argument(
        "--ordered",
        action="store_true",
        help="Do not shuffle candidate order. Default execute mode shuffles posts per view.",
    )
    parser.add_argument("--scrolls", type=int, default=DEFAULT_SCROLLS)
    parser.add_argument("--profile-dir", default="~/.substack_safe_profile")
    parser.add_argument("--url", default="https://substack.com/home")
    parser.add_argument(
        "--login-wait",
        type=int,
        default=0,
        help="Seconds to pause after opening Substack so you can log in manually.",
    )
    return parser.parse_args()


def run_bot() -> int:
    args = parse_args()
    setup_logging()

    if args.yes and not args.execute:
        logging.warning("--yes is ignored without --execute")

    if args.min_wait > args.max_wait:
        logging.error("--min-wait must be smaller than --max-wait")
        return 2
    if args.view_min_wait > args.view_max_wait:
        logging.error("--view-min-wait must be smaller than --view-max-wait")
        return 2
    if args.scroll_min > args.scroll_max:
        logging.error("--scroll-min must be smaller than --scroll-max")
        return 2
    if args.long_pause_min > args.long_pause_max:
        logging.error("--long-pause-min must be smaller than --long-pause-max")
        return 2

    state = load_state()
    counts = {"likes": 0, "follows": 0, "actions": 0}

    logging.info(
        "Starting Substack assistant mode=%s max_likes=%s max_follows=%s max_actions=%s",
        "execute" if args.execute else "dry-run",
        args.max_likes,
        args.max_follows,
        args.max_actions,
    )
    if args.execute:
        logging.info(
            "Pacing enabled: action_wait=%.0f-%.0fs view_wait=%.0f-%.0fs long_pause_chance=%.2f",
            args.min_wait,
            args.max_wait,
            args.view_min_wait,
            args.view_max_wait,
            args.long_pause_chance,
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

        logging.info("Opened URL: %s", driver.current_url)
        logging.info("Page title: %s", driver.title)
        if args.login_wait > 0:
            logging.info("Login wait: %s seconds. Log in in the Chrome window if needed.", args.login_wait)
            time.sleep(args.login_wait)

        for scroll_count in range(args.scrolls):
            logging.info("Checking view %s/%s", scroll_count + 1, args.scrolls)

            posts = collect_post_elements(driver)
            if args.execute and not args.ordered:
                random.shuffle(posts)

            logging.info("Found %s post-like elements", len(posts))
            if scroll_count == 0 and not posts:
                log_page_diagnostics(driver, "no_posts_view_1")

            for post in posts:
                maybe_human_pause(args)
                process_post(driver, post, state, counts, args)
                if counts["actions"] >= args.max_actions:
                    logging.info("Action limit reached (%s/%s)", counts["actions"], args.max_actions)
                    return 0
                if counts["likes"] >= args.max_likes and counts["follows"] >= args.max_follows:
                    logging.info("Goal reached")
                    return 0

            scroll_like_browsing(driver, args)
            sleep_between_views(args)

        logging.info("Finished. Likes=%s Follows=%s", counts["likes"], counts["follows"])
        if counts["likes"] == 0 and counts["follows"] == 0:
            logging.warning(
                "No candidates found. Check login state, or try --login-wait 180 / --url https://substack.com/browse."
            )
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
