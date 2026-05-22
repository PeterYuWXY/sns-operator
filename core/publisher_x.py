"""Publisher: post tweets to X via Playwright (cookie-based, stealth mode)."""
import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_COOKIES_DIR = _BASE / "config" / "cookies"

# Inject into every new page to suppress automation detection
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
window.chrome = { runtime: {} };
"""


def _load_cookies(account_key: str) -> List[dict]:
    path = _COOKIES_DIR / f"{account_key}_cookies.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Cookie file not found: {path}\n"
            "Export cookies from x.com using the EditThisCookie browser extension,\n"
            "then save to config/cookies/{account_key}_cookies.json"
        )
    return json.loads(path.read_text())


async def _human_delay(ms_min: int = 600, ms_max: int = 1800) -> None:
    await asyncio.sleep(random.uniform(ms_min / 1000, ms_max / 1000))


class XPublisher:
    def __init__(self, account_key: str, headless: bool = True):
        self.account_key = account_key
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def init(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "playwright not installed.\n"
                "Run: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().__aenter__()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        # Load saved cookies
        cookies = _load_cookies(self.account_key)
        await self._context.add_cookies(cookies)
        # Stealth script for every new page
        await self._context.add_init_script(_STEALTH_SCRIPT)
        self._page = await self._context.new_page()
        logger.info("Browser ready for %s", self.account_key)

    async def save_cookies(self) -> None:
        """Persist current session cookies back to file (keeps them fresh)."""
        if not self._context:
            return
        cookies = await self._context.cookies()
        path = _COOKIES_DIR / f"{self.account_key}_cookies.json"
        path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
        logger.info("Cookies refreshed for %s", self.account_key)

    async def _is_logged_in(self) -> bool:
        await self._page.goto("https://x.com", wait_until="domcontentloaded", timeout=15000)
        await _human_delay(1500, 2500)
        login_btn = await self._page.query_selector('[data-testid="loginButton"]')
        if login_btn:
            logger.error("Not logged in for %s — cookies may have expired", self.account_key)
            return False
        return True

    async def post_tweet(self, text: str) -> bool:
        if not self._page:
            raise RuntimeError("Call init() first or use async context manager")
        if len(text) > 280:
            logger.error("Tweet exceeds 280 chars (%d)", len(text))
            return False
        if not await self._is_logged_in():
            return False

        try:
            await self._page.goto(
                "https://x.com/compose/post", wait_until="domcontentloaded", timeout=15000
            )
            await _human_delay(2000, 3500)

            box = self._page.locator('[data-testid="tweetTextarea_0"]').first
            await box.wait_for(state="visible", timeout=10000)
            await box.click()
            await _human_delay(400, 800)

            # Use fill() for speed, then a quick check
            await box.fill(text)
            await _human_delay(800, 1500)

            submit = self._page.locator('[data-testid="tweetButtonInline"]').first
            is_disabled = await submit.get_attribute("disabled")
            if is_disabled:
                logger.error("Submit button disabled — text may be invalid")
                return False

            await submit.click()
            await _human_delay(2000, 3500)

            await self.save_cookies()
            logger.info("Tweet posted for %s: %s...", self.account_key, text[:50])
            return True

        except Exception as e:
            logger.error("Tweet post failed for %s: %s", self.account_key, e)
            return False

    async def post_thread(self, tweets: List[str]) -> bool:
        """Post a connected thread via X's compose UI."""
        if not await self._is_logged_in():
            return False

        try:
            await self._page.goto(
                "https://x.com/compose/post", wait_until="domcontentloaded", timeout=15000
            )
            await _human_delay(2000, 3500)

            for i, text in enumerate(tweets):
                if i == 0:
                    box = self._page.locator('[data-testid="tweetTextarea_0"]').first
                else:
                    add_btn = self._page.locator('[data-testid="addButton"]').first
                    await add_btn.click()
                    await _human_delay(600, 1200)
                    box = self._page.locator(f'[data-testid="tweetTextarea_{i}"]').first

                await box.wait_for(state="visible", timeout=8000)
                await box.click()
                await box.fill(text)
                await _human_delay(500, 1000)

            submit = self._page.locator('[data-testid="tweetButton"]').last
            await submit.click()
            await _human_delay(2500, 4000)

            await self.save_cookies()
            logger.info("Thread posted for %s (%d tweets)", self.account_key, len(tweets))
            return True

        except Exception as e:
            logger.error("Thread post failed for %s: %s", self.account_key, e)
            return False

    async def post_reply(self, tweet_url: str, reply_text: str) -> bool:
        """Reply to a specific tweet URL."""
        if not await self._is_logged_in():
            return False

        try:
            await self._page.goto(tweet_url, wait_until="domcontentloaded", timeout=15000)
            await _human_delay(2000, 3500)

            reply_btn = self._page.locator('[data-testid="reply"]').first
            await reply_btn.click()
            await _human_delay(1000, 1800)

            box = self._page.locator('[data-testid="tweetTextarea_0"]').first
            await box.wait_for(state="visible", timeout=8000)
            await box.click()

            # Character-by-character for replies (more natural feel in conversations)
            for char in reply_text:
                await box.type(char, delay=random.uniform(35, 95))

            await _human_delay(800, 1500)

            submit = self._page.locator('[data-testid="tweetButtonInline"]').first
            await submit.click()
            await _human_delay(2000, 3500)

            await self.save_cookies()
            logger.info("Replied to %s: %s...", tweet_url[-20:], reply_text[:40])
            return True

        except Exception as e:
            logger.error("Reply failed: %s", e)
            return False

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.__aexit__(None, None, None)


async def post(account_key: str, text: str, headless: bool = True) -> bool:
    async with XPublisher(account_key, headless=headless) as pub:
        return await pub.post_tweet(text)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    account = sys.argv[1] if len(sys.argv) > 1 else "account1"
    text = sys.argv[2] if len(sys.argv) > 2 else "测试推文 — sns-operator"
    print("Posted:", asyncio.run(post(account, text, headless=False)))
