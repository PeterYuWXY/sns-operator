"""Fetcher: scrape X account stats and KOL timelines (cookie-based, no API key)."""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_COOKIES_DIR = Path(__file__).parent.parent / "config" / "cookies"


def _load_cookies(account_key: str) -> List[dict]:
    path = _COOKIES_DIR / f"{account_key}_cookies.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


async def _get_page(url: str, cookies: List[dict], timeout: int = 15000):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        )
        if cookies:
            await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        content = await page.content()
        await browser.close()
        return content


def _parse_count(text: str) -> int:
    """Parse '12.3K' or '1,234' into integer."""
    text = text.strip().replace(",", "")
    if text.endswith("K"):
        return int(float(text[:-1]) * 1000)
    if text.endswith("M"):
        return int(float(text[:-1]) * 1_000_000)
    try:
        return int(text)
    except ValueError:
        return 0


async def get_profile_stats(handle: str, account_key: str = "petery") -> Dict:
    """Return follower count and following count for a handle."""
    cookies = _load_cookies(account_key)
    url = f"https://x.com/{handle}"
    try:
        html = await _get_page(url, cookies)
        # X renders counts in JSON-LD or data attributes
        followers = re.search(r'"followers_count":(\d+)', html)
        following = re.search(r'"friends_count":(\d+)', html)
        return {
            "handle": handle,
            "followers": int(followers.group(1)) if followers else None,
            "following": int(following.group(1)) if following else None,
        }
    except Exception as e:
        logger.warning("Failed to fetch profile for @%s: %s", handle, e)
        return {"handle": handle, "followers": None, "following": None}


async def get_kol_tweets(
    handle: str, account_key: str = "petery", limit: int = 10
) -> List[Dict]:
    """Fetch recent tweets from a KOL (requires logged-in cookies)."""
    cookies = _load_cookies(account_key)
    url = f"https://x.com/{handle}"
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context()
            if cookies:
                await ctx.add_cookies(cookies)
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=20000)

            tweet_elements = await page.locator('[data-testid="tweet"]').all()
            tweets = []
            for el in tweet_elements[:limit]:
                try:
                    text_el = el.locator('[data-testid="tweetText"]')
                    link_el = el.locator('a[href*="/status/"]')
                    text = await text_el.inner_text() if await text_el.count() else ""
                    href = await link_el.first.get_attribute("href") if await link_el.count() else ""
                    tweet_url = f"https://x.com{href}" if href else ""
                    if text:
                        tweets.append({"handle": handle, "text": text, "url": tweet_url})
                except Exception:
                    continue

            await browser.close()
            return tweets
    except Exception as e:
        logger.warning("Failed to fetch tweets for @%s: %s", handle, e)
        return []


def get_profile_stats_sync(handle: str, account_key: str = "petery") -> Dict:
    return asyncio.run(get_profile_stats(handle, account_key))


def get_kol_tweets_sync(handle: str, account_key: str = "petery", limit: int = 10) -> List[Dict]:
    return asyncio.run(get_kol_tweets(handle, account_key, limit))
