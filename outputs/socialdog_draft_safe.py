#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SocialDog draft filler.

Safe default:
- Opens SocialDog in a visible browser.
- Fetches the latest morning check text from the VPS or reads a text file.
- Fills the post composer if it can find one.
- Does not click post, schedule, save, or confirm buttons.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "socialdog_draft_safe.log"
DEFAULT_PROFILE_DIR = BASE_DIR / ".socialdog_profile"
DEFAULT_URL = "https://web.social-dog.net/"
DEFAULT_VPS_KEY = Path("work/codex_vps_key")
DEFAULT_VPS_HOST = "49.212.137.39"
DEFAULT_VPS_USER = "ubuntu"
DEFAULT_VPS_COMMAND = "/usr/bin/python3 /home/ubuntu/f_tools/slack_test.py --dry-run"
DIAGNOSTIC_SCREENSHOT = LOG_DIR / "socialdog_last.png"
DIAGNOSTIC_TEXT = LOG_DIR / "socialdog_last_text.txt"


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


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_copy_block(text: str) -> str:
    match = re.search(r"📋 コピー用\s*```(?:text)?\s*(.*?)\s*```", text, re.S)
    if match:
        return normalize_text(match.group(1))

    marker = "📋 コピー用"
    if marker in text:
        return normalize_text(text.split(marker, 1)[1])

    # Fallback: strip the Slack header and automated notice.
    lines = [
        line
        for line in text.splitlines()
        if not line.startswith("🌅 朝イチ相場CHECK")
        and not line.startswith("※自動配信です")
    ]
    return normalize_text("\n".join(lines))


def fetch_text_from_vps(args: argparse.Namespace) -> str:
    key_path = Path(args.vps_key).expanduser()
    cmd = [
        "ssh",
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        f"{args.vps_user}@{args.vps_host}",
        args.vps_command,
    ]
    logging.info("Fetching morning check text from VPS: %s@%s", args.vps_user, args.vps_host)
    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        timeout=args.vps_timeout,
    )
    return extract_copy_block(result.stdout)


def load_post_text(args: argparse.Namespace) -> str:
    if args.text:
        return normalize_text(args.text)
    if args.text_file:
        return normalize_text(Path(args.text_file).read_text(encoding="utf-8"))
    if args.from_vps:
        return fetch_text_from_vps(args)
    raise RuntimeError("--from-vps、--text-file、--text のどれかを指定してください。")


def write_clipboard(text: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True, check=False)
        logging.info("Copied draft text to macOS clipboard.")


def visible_count(locator: Locator) -> int:
    count = locator.count()
    visible = 0
    for index in range(min(count, 30)):
        try:
            if locator.nth(index).is_visible(timeout=500):
                visible += 1
        except Exception:
            continue
    return visible


def likely_logged_in(page: Page) -> bool:
    markers = [
        "予約投稿",
        "投稿",
        "受信箱",
        "分析",
        "フォロワー",
        "ダッシュボード",
        "ツイート",
        "ポスト",
    ]
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception:
        return False
    return any(marker in body for marker in markers)


def wait_for_login(page: Page, seconds: int) -> None:
    deadline = time.time() + seconds
    logging.info("SocialDog login wait: %s seconds.", seconds)
    logging.info("If login is needed, log in in the opened Chrome window.")

    while time.time() < deadline:
        if likely_logged_in(page):
            logging.info("SocialDog looks logged in.")
            return
        time.sleep(3)

    logging.warning("Login wait ended. Continuing anyway.")


def click_first_visible(page: Page, patterns: list[str]) -> bool:
    for pattern in patterns:
        locators = [
            page.get_by_role("button", name=re.compile(pattern)),
            page.get_by_role("link", name=re.compile(pattern)),
            page.locator(f"text=/{pattern}/"),
        ]
        for locator in locators:
            count = locator.count()
            for index in range(min(count, 8)):
                item = locator.nth(index)
                try:
                    if not item.is_visible(timeout=800):
                        continue
                    item.click(timeout=3000)
                    logging.info("Clicked navigation item matching: %s", pattern)
                    page.wait_for_timeout(1500)
                    return True
                except Exception:
                    continue
    return False


def dismiss_blocking_dialogs(page: Page) -> None:
    """Close SocialDog modals that block navigation, such as reconnect notices."""
    button_patterns = [
        r"今はしない",
        r"閉じる",
        r"キャンセル",
        r"あとで",
        r"後で",
    ]
    for pattern in button_patterns:
        locators = [
            page.get_by_role("button", name=re.compile(pattern)),
            page.locator(f"text=/{pattern}/"),
        ]
        for locator in locators:
            for index in range(min(locator.count(), 6)):
                item = locator.nth(index)
                try:
                    if not item.is_visible(timeout=500):
                        continue
                    item.click(timeout=2500)
                    logging.info("Dismissed blocking dialog using: %s", pattern)
                    page.wait_for_timeout(1200)
                    return
                except Exception:
                    continue


def find_composer(page: Page) -> Locator | None:
    selectors = [
        ".ProseMirror",
        "[data-slate-editor='true']",
        "[data-lexical-editor='true']",
        "textarea[placeholder*='投稿']",
        "textarea[placeholder*='ツイート']",
        "textarea[placeholder*='ポスト']",
        "textarea",
        "[contenteditable='true'][role='textbox']",
        "[contenteditable='true']",
        "div[role='textbox']",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        count = locator.count()
        for index in range(min(count, 20)):
            item = locator.nth(index)
            try:
                if item.is_visible(timeout=800):
                    return item
            except Exception:
                continue
    return None


def fill_composer(page: Page, text: str) -> bool:
    composer = find_composer(page)
    if composer is None:
        return False

    try:
        composer.click(timeout=3000)
        composer.fill(text, timeout=5000)
        logging.info("Filled composer with %s characters.", len(text))
        return True
    except Exception:
        try:
            composer.click(timeout=3000)
            page.keyboard.press("Meta+A")
            page.keyboard.type(text, delay=5)
            logging.info("Typed composer text with keyboard fallback.")
            return True
        except Exception as exc:  # noqa: BLE001
            logging.warning("Composer fill failed: %s", exc)
    return False


def click_top_right_close(page: Page) -> bool:
    viewport = page.viewport_size or {"width": 1440, "height": 1000}
    candidates = page.locator("button, [role='button']")
    best: tuple[float, Locator] | None = None

    for index in range(min(candidates.count(), 80)):
        item = candidates.nth(index)
        try:
            if not item.is_visible(timeout=500):
                continue
            box = item.bounding_box(timeout=500)
        except Exception:
            continue
        if not box:
            continue

        is_top_right = box["x"] > viewport["width"] - 180 and box["y"] < 170
        is_small = box["width"] <= 80 and box["height"] <= 80
        if not (is_top_right and is_small):
            continue

        score = box["x"] - box["y"]
        if best is None or score > best[0]:
            best = (score, item)

    if best is None:
        return False

    try:
        box = best[1].bounding_box(timeout=1000)
        if not box:
            return False
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        logging.info("Clicked top-right close button.")
        page.wait_for_timeout(1200)
        return True
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to click close button: %s", exc)
        return False


def save_as_draft(page: Page) -> bool:
    if not click_top_right_close(page):
        logging.warning("Could not find the top-right close button.")
        save_diagnostics(page)
        return False

    try:
        save_button = page.get_by_role("button", name=re.compile("下書きとして保存"))
        save_button.wait_for(state="visible", timeout=8000)
        save_button.click(timeout=5000)
        logging.info("Clicked '下書きとして保存'.")
        page.wait_for_timeout(3000)
        return True
    except Exception as exc:  # noqa: BLE001
        logging.warning("Could not click '下書きとして保存': %s", exc)
        save_diagnostics(page)
        return False


def try_open_composer(page: Page) -> bool:
    dismiss_blocking_dialogs(page)

    create_patterns = [
        "新しい投稿",
        "新規投稿",
        "投稿作成",
        "投稿を作成",
        "投稿する",
        "ツイート作成",
        "ツイート",
        "ポスト",
        "作成",
    ]
    if click_first_visible(page, create_patterns):
        dismiss_blocking_dialogs(page)
        return True

    navigation_patterns = [
        "予約投稿",
        "下書き",
        "投稿予定",
        "投稿",
    ]
    if click_first_visible(page, navigation_patterns):
        page.wait_for_timeout(2500)
        dismiss_blocking_dialogs(page)
        if click_first_visible(page, create_patterns):
            dismiss_blocking_dialogs(page)
            return True

    return False


def save_diagnostics(page: Page) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(DIAGNOSTIC_SCREENSHOT), full_page=True)
        logging.info("Saved diagnostic screenshot: %s", DIAGNOSTIC_SCREENSHOT)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to save screenshot: %s", exc)

    try:
        body = page.locator("body").inner_text(timeout=3000)
        DIAGNOSTIC_TEXT.write_text(body, encoding="utf-8")
        logging.info("Saved diagnostic text: %s", DIAGNOSTIC_TEXT)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to save page text: %s", exc)


def run(args: argparse.Namespace) -> int:
    setup_logging()
    post_text = ""
    if not args.login_only:
        post_text = load_post_text(args)
        write_clipboard(post_text)
        logging.info("Draft text ready. chars=%s", len(post_text))

    with sync_playwright() as p:
        browser = None
        if args.storage_state:
            browser = p.chromium.launch(
                headless=args.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                storage_state=str(Path(args.storage_state).expanduser()),
                viewport={"width": 1440, "height": 1000},
                locale="ja-JP",
            )
            page = context.new_page()
        else:
            context = p.chromium.launch_persistent_context(
                str(Path(args.profile_dir).expanduser()),
                headless=args.headless,
                viewport={"width": 1440, "height": 1000},
                locale="ja-JP",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            logging.info("Opened SocialDog: %s", args.url)

            wait_for_login(page, args.login_wait_seconds)
            dismiss_blocking_dialogs(page)

            if args.login_only:
                logging.info("Login-only mode. Keeping browser open for %s seconds.", args.keep_open_seconds)
                if args.keep_open_seconds > 0:
                    time.sleep(args.keep_open_seconds)
                return 0

            filled = fill_composer(page, post_text)
            if filled:
                logging.info("Text is in the current composer.")
            else:
                logging.info("Composer not found on current page. Trying to open a post composer.")
                try_open_composer(page)
                page.wait_for_timeout(2500)
                filled = fill_composer(page, post_text)
                if filled:
                    logging.info("Text is in the composer.")

            if filled and args.save_draft:
                if save_as_draft(page):
                    logging.info("Done. Draft was saved in SocialDog.")
                else:
                    logging.warning("Text was filled, but draft save failed.")
                    return 1
            elif filled:
                logging.info("Done. Text is in the composer. No submit/save button was clicked.")
            else:
                if not filled:
                    save_diagnostics(page)
                    logging.warning(
                        "Composer was not found. The draft text is copied to clipboard, so paste it manually."
                    )
                    if args.save_draft:
                        return 1

            logging.info("Keeping browser open for %s seconds.", args.keep_open_seconds)
            if args.keep_open_seconds > 0:
                time.sleep(args.keep_open_seconds)
        finally:
            context.close()
            if browser is not None:
                browser.close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely fill SocialDog composer.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--storage-state")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--login-wait-seconds", type=int, default=300)
    parser.add_argument("--keep-open-seconds", type=int, default=600)
    parser.add_argument("--text-file")
    parser.add_argument("--text")
    parser.add_argument("--from-vps", action="store_true")
    parser.add_argument("--save-draft", action="store_true")
    parser.add_argument("--login-only", action="store_true")
    parser.add_argument("--vps-host", default=DEFAULT_VPS_HOST)
    parser.add_argument("--vps-user", default=DEFAULT_VPS_USER)
    parser.add_argument("--vps-key", default=str(DEFAULT_VPS_KEY))
    parser.add_argument("--vps-command", default=DEFAULT_VPS_COMMAND)
    parser.add_argument("--vps-timeout", type=int, default=90)
    return parser.parse_args()


def main() -> int:
    try:
        return run(parse_args())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        logging.exception("SocialDog draft filler failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
