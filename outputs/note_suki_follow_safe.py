#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""note suki/follow assistant.

Default mode is dry-run: it lists candidates and never clicks.
Use --execute to allow clicks. Without --yes, each click asks for approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "note_suki_follow_safe.log"
STATE_FILE = BASE_DIR / "note_suki_follow_state.json"
DEFAULT_PROFILE_DIR = BASE_DIR / ".note_safe_profile"
DEFAULT_URLS = ["https://note.com/"]

DEFAULT_KEYWORDS = [
    "投資",
    "株",
    "FX",
    "為替",
    "相場",
    "金融",
    "NISA",
    "資産運用",
    "トレード",
    "マーケット",
    "AI",
    "生成AI",
    "ビジネス",
    "起業",
    "経済",
    "お金",
    "副業",
]
DEFAULT_EXCLUDE_KEYWORDS = [
    "PR",
    "広告",
    "勧誘",
    "必勝",
    "絶対",
    "保証",
    "無料プレゼント",
    "LINE登録",
]

LIKE_WORDS = ["スキ", "好き", "like", "Like"]
LIKE_DONE_WORDS = ["スキ済み", "Liked", "liked"]
FOLLOW_WORDS = ["フォロー", "Follow", "follow"]
FOLLOW_DONE_WORDS = ["フォロー中", "フォロー済み", "Following", "following"]
BLOCK_WORDS = [
    "購入",
    "サポート",
    "メンバーシップ",
    "コメント",
    "シェア",
    "共有",
    "もっと見る",
    "閉じる",
]
LOGIN_MARKERS = [
    "ログイン",
    "会員登録",
    "メールアドレス",
    "パスワード",
    "noteにログイン",
    "ログインしてください",
]


@dataclass(frozen=True)
class Candidate:
    action: str
    key: str
    title: str
    author: str
    url: str
    button_label: str
    text_preview: str
    index: int


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


def parse_csv(value: str | None, defaults: list[str]) -> list[str]:
    if value is None:
        return defaults
    if value.strip() == "":
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


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


def contains_any(text: str, words: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words if word)


def has_target_keyword(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    return contains_any(text, keywords)


def get_button_label(button: Locator) -> str:
    parts: list[str] = []
    for attr in ("aria-label", "title"):
        try:
            value = button.get_attribute(attr, timeout=1000)
        except Exception:
            value = None
        if value:
            parts.append(value)
    try:
        text = button.inner_text(timeout=1000)
    except Exception:
        text = ""
    if text:
        parts.append(text)
    return normalize_space(" ".join(parts))


def is_pressed(button: Locator) -> bool:
    for attr in ("aria-pressed", "data-active", "aria-selected"):
        try:
            value = button.get_attribute(attr, timeout=1000)
        except Exception:
            value = None
        if value and value.lower() in {"true", "1"}:
            return True
    return False


def action_from_button(button: Locator) -> str | None:
    label = get_button_label(button)
    if not label:
        return None
    if contains_any(label, BLOCK_WORDS):
        return None
    if contains_any(label, FOLLOW_WORDS) and not contains_any(label, FOLLOW_DONE_WORDS):
        return "follow"
    if contains_any(label, LIKE_WORDS) and not contains_any(label, LIKE_DONE_WORDS) and not is_pressed(button):
        return "like"
    return None


def context_info(button: Locator) -> dict[str, str]:
    return button.evaluate(
        """(btn) => {
            const abs = (href) => {
                try { return new URL(href, location.href).href; } catch { return ""; }
            };
            let cur = btn;
            let best = btn;
            for (let i = 0; i < 9 && cur; i++) {
                const text = (cur.innerText || "").trim();
                const links = Array.from(cur.querySelectorAll("a[href]")).map(a => abs(a.getAttribute("href")));
                const postUrl = links.find(h => /note\\.com\\/[^/]+\\/n\\//.test(h))
                    || links.find(h => /note\\.com\\/n\\//.test(h))
                    || links.find(h => h.includes("note.com"));
                if ((postUrl && text.length > 20) || text.length > 120) {
                    best = cur;
                    break;
                }
                cur = cur.parentElement;
            }
            const text = (best.innerText || "").trim();
            const links = Array.from(best.querySelectorAll("a[href]")).map(a => abs(a.getAttribute("href")));
            const postUrl = links.find(h => /note\\.com\\/[^/]+\\/n\\//.test(h))
                || links.find(h => /note\\.com\\/n\\//.test(h))
                || "";
            const authorUrl = links.find(h => /note\\.com\\/[^/?#]+\\/?$/.test(h) && !h.includes("/n/")) || "";
            const lines = text.split(/\\n+/).map(s => s.trim()).filter(Boolean);
            return {
                text,
                title: lines[0] || "",
                author: lines[1] || "",
                postUrl,
                authorUrl,
            };
        }"""
    )


def make_candidate(button: Locator, action: str, index: int) -> Candidate | None:
    label = get_button_label(button)
    try:
        info = context_info(button)
    except Exception as exc:
        logging.debug("Could not read button context: %s", exc)
        return None

    text = normalize_space(info.get("text", ""))
    if not text and not label:
        return None

    title = normalize_space(info.get("title", ""))[:80]
    author = normalize_space(info.get("author", ""))[:80]
    post_url = info.get("postUrl", "") or ""
    author_url = info.get("authorUrl", "") or ""
    if action == "like":
        key_source = post_url or text or f"{label}:{index}"
        key = f"like:{key_source}"
        url = post_url
    else:
        key_source = author_url or author or text or f"{label}:{index}"
        key = f"follow:{key_source}"
        url = author_url or post_url

    return Candidate(
        action=action,
        key=key,
        title=title or "(titleなし)",
        author=author or "(author不明)",
        url=url,
        button_label=label,
        text_preview=text[:180],
        index=index,
    )


def is_probably_login_prompt(page: Page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return False
    return contains_any(text, LOGIN_MARKERS)


def save_debug_snapshot(page: Page, label: str) -> None:
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "debug"
    html_path = LOG_DIR / f"note_{safe_label}.html"
    png_path = LOG_DIR / f"note_{safe_label}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(png_path), full_page=False)
        logging.info("Saved debug HTML: %s", html_path)
        logging.info("Saved debug screenshot: %s", png_path)
    except PlaywrightError as exc:
        logging.warning("Could not save debug snapshot: %s", exc)


def scroll_feed(page: Page, scrolls: int, min_wait: float, max_wait: float) -> None:
    for idx in range(scrolls):
        page.mouse.wheel(0, random.randint(500, 1200))
        wait = random.uniform(min_wait, max_wait)
        logging.info("Scroll %s/%s wait %.1fs", idx + 1, scrolls, wait)
        time.sleep(wait)


def collect_candidates(
    page: Page,
    keywords: list[str],
    exclude_keywords: list[str],
    state: dict[str, list[str]],
) -> tuple[list[tuple[Candidate, Locator]], dict[str, int]]:
    buttons = page.locator("button").all()
    candidates: list[tuple[Candidate, Locator]] = []
    seen: set[str] = set()
    stats = {
        "buttons": len(buttons),
        "keyword_skip": 0,
        "exclude_skip": 0,
        "duplicate_skip": 0,
        "state_skip": 0,
    }

    for idx, button in enumerate(buttons):
        try:
            action = action_from_button(button)
            if not action:
                continue
            candidate = make_candidate(button, action, idx)
            if not candidate:
                continue
            text_for_filter = f"{candidate.title}\n{candidate.author}\n{candidate.text_preview}"
            if not has_target_keyword(text_for_filter, keywords):
                stats["keyword_skip"] += 1
                continue
            if contains_any(text_for_filter, exclude_keywords):
                stats["exclude_skip"] += 1
                continue
            if candidate.key in seen:
                stats["duplicate_skip"] += 1
                continue
            if action == "like" and candidate.key in state["liked_posts"]:
                stats["state_skip"] += 1
                continue
            if action == "follow" and candidate.key in state["followed_authors"]:
                stats["state_skip"] += 1
                continue
            seen.add(candidate.key)
            candidates.append((candidate, button))
        except Exception as exc:
            logging.debug("Candidate scan skipped one button: %s", exc)

    return candidates, stats


def should_execute(args: argparse.Namespace, candidate: Candidate) -> bool:
    if not args.execute:
        return False
    if args.yes:
        return True
    answer = input(
        f"{candidate.action.upper()}しますか？ {candidate.title} / {candidate.author} [y/N]: "
    ).strip().lower()
    return answer in {"y", "yes"}


def click_candidate(page: Page, button: Locator, candidate: Candidate, args: argparse.Namespace) -> bool:
    if not should_execute(args, candidate):
        logging.info(
            "[DRY-RUN] %s | %s | %s | %s | label=%s | preview=%s",
            candidate.action.upper(),
            candidate.title,
            candidate.author,
            candidate.url,
            candidate.button_label,
            candidate.text_preview[:80],
        )
        return False

    try:
        button.scroll_into_view_if_needed(timeout=5000)
        time.sleep(random.uniform(0.8, 1.8))
        button.click(timeout=7000)
        time.sleep(random.uniform(args.min_wait, args.max_wait))
        if is_probably_login_prompt(page):
            logging.error("note login prompt appeared after click. Stop executing.")
            return False
        logging.info(
            "[EXECUTED] %s | %s | %s | %s | label=%s | preview=%s",
            candidate.action.upper(),
            candidate.title,
            candidate.author,
            candidate.url,
            candidate.button_label,
            candidate.text_preview[:80],
        )
        return True
    except Exception as exc:
        logging.warning("Could not click %s candidate: %s", candidate.action, exc)
        return False


def open_context(args: argparse.Namespace) -> tuple[Browser | None, BrowserContext]:
    profile_dir = Path(args.profile_dir).expanduser()
    launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    playwright = sync_playwright().start()
    if args.storage_state:
        storage_state = Path(args.storage_state).expanduser()
        if not storage_state.exists():
            raise FileNotFoundError(f"storage_state not found: {storage_state}")
        browser = playwright.chromium.launch(headless=args.headless, args=launch_args)
        context = browser.new_context(
            storage_state=str(storage_state),
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        context._note_playwright = playwright  # type: ignore[attr-defined]
        return browser, context

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=args.headless,
        args=launch_args,
        viewport={"width": 1280, "height": 900},
        locale="ja-JP",
    )
    context._note_playwright = playwright  # type: ignore[attr-defined]
    return None, context


def close_context(browser: Browser | None, context: BrowserContext) -> None:
    playwright = getattr(context, "_note_playwright", None)
    context.close()
    if browser:
        browser.close()
    if playwright:
        playwright.stop()


def run(args: argparse.Namespace) -> int:
    setup_logging()
    state = load_state()
    keywords = parse_csv(args.keywords, DEFAULT_KEYWORDS)
    exclude_keywords = parse_csv(args.exclude_keywords, DEFAULT_EXCLUDE_KEYWORDS)

    logging.info("--- note suki/follow assistant ---")
    logging.info(
        "mode=%s max_actions=%s max_likes=%s max_follows=%s urls=%s",
        "execute" if args.execute else "dry-run",
        args.max_actions,
        args.max_likes,
        args.max_follows,
        args.url,
    )

    browser = None
    context = None
    counts = {"like": 0, "follow": 0, "total": 0}
    try:
        browser, context = open_context(args)
        page = context.pages[0] if context.pages else context.new_page()

        for url in args.url:
            if args.max_actions > 0 and counts["total"] >= args.max_actions:
                break
            logging.info("Open: %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(random.uniform(args.view_min_wait, args.view_max_wait))

            if args.login_wait_seconds and not args.headless:
                logging.info("Manual login wait: please log in within %s seconds.", args.login_wait_seconds)
                time.sleep(args.login_wait_seconds)

            scroll_feed(page, args.scrolls, args.view_min_wait, args.view_max_wait)
            candidates, stats = collect_candidates(page, keywords, exclude_keywords, state)
            random.shuffle(candidates)
            logging.info("Scan stats: %s", stats)
            logging.info("Candidates: %s", len(candidates))

            if not candidates:
                save_debug_snapshot(page, "no_candidates")
                continue

            for candidate, button in candidates:
                if counts["total"] >= args.max_actions:
                    logging.info("Reached max_actions=%s", args.max_actions)
                    break
                if candidate.action == "like" and counts["like"] >= args.max_likes:
                    continue
                if candidate.action == "follow" and counts["follow"] >= args.max_follows:
                    continue

                executed = click_candidate(page, button, candidate, args)
                if not executed:
                    continue
                if candidate.action == "like":
                    state["liked_posts"].append(candidate.key)
                    counts["like"] += 1
                elif candidate.action == "follow":
                    state["followed_authors"].append(candidate.key)
                    counts["follow"] += 1
                counts["total"] += 1
                save_state(state)

        if args.save_storage_state:
            storage_path = Path(args.save_storage_state).expanduser()
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(storage_path))
            logging.info("Saved storage_state: %s", storage_path)

        screenshot_path = LOG_DIR / "note_last.png"
        page.screenshot(path=str(screenshot_path), full_page=False)
        logging.info("Saved screenshot: %s", screenshot_path)
        logging.info(
            "Summary: total=%s likes=%s follows=%s",
            counts["total"],
            counts["like"],
            counts["follow"],
        )
        return 0
    except PlaywrightTimeoutError as exc:
        logging.error("Timeout: %s", exc)
        return 2
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        return 1
    finally:
        if context:
            close_context(browser, context)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="note suki/follow assistant")
    parser.add_argument("--url", action="append", default=None, help="巡回するnote URL。複数指定可")
    parser.add_argument("--execute", action="store_true", help="クリックを許可する")
    parser.add_argument("--yes", action="store_true", help="確認なしでクリックする")
    parser.add_argument("--headless", action="store_true", help="画面なしで実行する")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="ローカルログイン保存先")
    parser.add_argument("--storage-state", default=None, help="VPS用storage_state JSON")
    parser.add_argument("--save-storage-state", default=None, help="ログイン状態をJSON保存する")
    parser.add_argument("--login-wait-seconds", type=int, default=0, help="手動ログイン待ち秒数")
    parser.add_argument("--max-actions", type=int, default=6, help="合計アクション上限")
    parser.add_argument("--max-likes", type=int, default=5, help="スキ上限")
    parser.add_argument("--max-follows", type=int, default=1, help="フォロー上限")
    parser.add_argument("--scrolls", type=int, default=8, help="スクロール回数")
    parser.add_argument("--min-wait", type=float, default=20.0, help="実行後の最短待機秒")
    parser.add_argument("--max-wait", type=float, default=80.0, help="実行後の最長待機秒")
    parser.add_argument("--view-min-wait", type=float, default=3.0, help="閲覧時の最短待機秒")
    parser.add_argument("--view-max-wait", type=float, default=9.0, help="閲覧時の最長待機秒")
    parser.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS), help="対象キーワードCSV。空文字なら無効")
    parser.add_argument("--exclude-keywords", default=",".join(DEFAULT_EXCLUDE_KEYWORDS), help="除外キーワードCSV")
    args = parser.parse_args()
    args.url = args.url or DEFAULT_URLS
    args.max_actions = max(0, args.max_actions)
    args.max_likes = max(0, args.max_likes)
    args.max_follows = max(0, args.max_follows)
    if args.min_wait > args.max_wait:
        args.min_wait, args.max_wait = args.max_wait, args.min_wait
    if args.view_min_wait > args.view_max_wait:
        args.view_min_wait, args.view_max_wait = args.view_max_wait, args.view_min_wait
    return args


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
