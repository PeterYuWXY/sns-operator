"""Publisher: post tweets to X via Playwright (cookie-based auth, no API key needed)."""
import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_COOKIES_DIR = Path(__file__).parent.parent / "config" / "cookies"
_X_HOME = "https://x.com"
_X_COMPOSE = "https://x.com/compose/post"


def _load_cookies(account_key: str) -> List[dict]:
    path = _COOKIES_DIR / f"{account_key}_cookies.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Cookie file not found: {path}\n"
            "Export cookies from x.com using EditThisCookie browser extension."
        )
    return json.loads(path.read_text())


async def _human_delay(ms_min: int = 800, ms_max: int = 2200) -> None:
    await asyncio.sleep(random.uniform(ms_min / 1000, ms_max / 1000))


class XPublisher:
    def __init__(self, account_key: str, headless: bool = True):
        self.account_key = account_key
        self.headless = headless
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
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().__aenter__()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        cookies = _load_cookies(self.account_key)
        await self._context.add_cookies(cookies)
        self._page = await self._context.new_page()
        logger.info("Browser init done for %s", self.account_key)

    async def _verify_login(self) -> bool:
        await self._page.goto(_X_HOME, wait_until="domcontentloaded", timeout=15000)
        await _human_delay(1500, 3000)
        return "login" not in self._page.url

    async def post_tweet(self, text: str) -> bool:
        if not self._page:
            raise RuntimeError("Call init() first")

        if not await self._verify_login():
            logger.error("Not logged in for %s — check cookies", self.account_key)
            return False

        if len(text) > 280:
            logger.error("Tweet too long (%d chars)", len(text))
            return False

        try:
            await self._page.goto(_X_COMPOSE, wait_until="domcontentloaded", timeout=15000)
            await _human_delay(2000, 4000)

            # Click compose box
            box = self._page.locator('[data-testid="tweetTextarea_0"]').first
            await box.wait_for(state="visible", timeout=10000)
            await box.click()
            await _human_delay(500, 1000)

            # Type with human-like delay
            for char in text:
                await box.type(char, delay=random.uniform(30, 90))

            await _human_delay(1000, 2000)

            # Submit
            submit = self._page.locator('[data-testid="tweetButtonInline"]').first
            await submit.wait_for(state="visible", timeout=5000)
            await submit.click()
            await _human_delay(2000, 3500)

            logger.info("Tweet posted for %s: %s...", self.account_key, text[:40])
            return True

        except Exception as e:
            logger.error("Failed to post tweet for %s: %s", self.account_key, e)
            return False

    async def post_thread(self, tweets: List[str]) -> bool:
        """Post a thread (multiple connected tweets)."""
        if not self._page:
            raise RuntimeError("Call init() first")

        if not await self._verify_login():
            return False

        try:
            await self._page.goto(_X_COMPOSE, wait_until="domcontentloaded", timeout=15000)
            await _human_delay(2000, 4000)

            for i, text in enumerate(tweets):
                if i == 0:
                    box = self._page.locator('[data-testid="tweetTextarea_0"]').first
                else:
                    # Click "Add another tweet"
                    add_btn = self._page.locator('[data-testid="addButton"]').first
                    await add_btn.click()
                    await _human_delay(800, 1500)
                    box = self._page.locator(f'[data-testid="tweetTextarea_{i}"]').first

                await box.wait_for(state="visible", timeout=8000)
                await box.click()
                for char in text:
                    await box.type(char, delay=random.uniform(25, 75))
                await _human_delay(500, 1000)

            submit = self._page.locator('[data-testid="tweetButton"]').last
            await submit.click()
            await _human_delay(2000, 3500)
            logger.info("Thread posted for %s (%d tweets)", self.account_key, len(tweets))
            return True

        except Exception as e:
            logger.error("Thread post failed for %s: %s", self.account_key, e)
            return False

    async def post_reply(self, tweet_url: str, reply_text: str) -> bool:
        """Reply to a specific tweet."""
        if not await self._verify_login():
            return False

        try:
            await self._page.goto(tweet_url, wait_until="domcontentloaded", timeout=15000)
            await _human_delay(2000, 4000)

            reply_btn = self._page.locator('[data-testid="reply"]').first
            await reply_btn.click()
            await _human_delay(1000, 2000)

            box = self._page.locator('[data-testid="tweetTextarea_0"]').first
            await box.wait_for(state="visible", timeout=8000)
            await box.click()
            for char in reply_text:
                await box.type(char, delay=random.uniform(30, 80))

            await _human_delay(800, 1500)
            submit = self._page.locator('[data-testid="tweetButtonInline"]').first
            await submit.click()
            await _human_delay(2000, 3000)

            logger.info("Replied to %s", tweet_url)
            return True

        except Exception as e:
            logger.error("Reply failed: %s", e)
            return False

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw"):
            await self._pw.__aexit__(None, None, None)


async def post(account_key: str, text: str, headless: bool = True) -> bool:
    async with XPublisher(account_key, headless=headless) as pub:
        return await pub.post_tweet(text)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    account = sys.argv[1] if len(sys.argv) > 1 else "petery"
    text = sys.argv[2] if len(sys.argv) > 2 else "测试推文 — sns-operator"
    result = asyncio.run(post(account, text, headless=False))
    print("Posted:", result)
