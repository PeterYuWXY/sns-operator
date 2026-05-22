"""Scout: scrape trending topics from Reddit, RSS feeds, and X KOL timelines."""
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Dict

import feedparser
import requests

logger = logging.getLogger(__name__)

_SOURCES_PATH = Path(__file__).parent.parent / "config" / "sources.json"
_SOURCES = json.loads(_SOURCES_PATH.read_text())

_REDDIT_HEADERS = {"User-Agent": "sns-operator/1.0 (research bot)"}


def _reddit_hot(subreddit: str, limit: int = 10) -> List[Dict]:
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
    try:
        resp = requests.get(url, headers=_REDDIT_HEADERS, timeout=10)
        resp.raise_for_status()
        posts = resp.json()["data"]["children"]
        return [
            {
                "source": f"r/{subreddit}",
                "title": p["data"]["title"],
                "score": p["data"]["score"],
                "url": p["data"]["url"],
                "text": p["data"].get("selftext", "")[:300],
            }
            for p in posts
            if not p["data"].get("stickied")
        ]
    except Exception as e:
        logger.warning("Reddit %s failed: %s", subreddit, e)
        return []


def _rss_feed(name: str, url: str) -> List[Dict]:
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:8]:
            items.append(
                {
                    "source": name,
                    "title": entry.get("title", ""),
                    "summary": re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:300],
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                }
            )
        return items
    except Exception as e:
        logger.warning("RSS %s failed: %s", name, e)
        return []


def _nitter_kol(handle: str) -> List[Dict]:
    """Scrape recent tweets from a KOL via Nitter (no auth needed)."""
    instances = _SOURCES.get("nitter_instances", ["https://nitter.net"])
    for base in instances:
        try:
            url = f"{base}/{handle}/rss"
            feed = feedparser.parse(url)
            if not feed.entries:
                continue
            return [
                {
                    "source": f"@{handle}",
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                }
                for entry in feed.entries[:5]
            ]
        except Exception:
            continue
    logger.warning("Nitter scrape failed for @%s", handle)
    return []


def run(include_kols: bool = True) -> Dict[str, List[Dict]]:
    """Return dict of {source_category: [item, ...]}."""
    results: Dict[str, List[Dict]] = {
        "reddit": [],
        "rss": [],
        "kols_cn": [],
        "kols_en": [],
    }

    for cfg in _SOURCES.get("reddit", []):
        results["reddit"].extend(_reddit_hot(cfg["subreddit"], cfg.get("limit", 10)))
        time.sleep(0.5)

    for cfg in _SOURCES.get("rss_feeds", []):
        results["rss"].extend(_rss_feed(cfg["name"], cfg["url"]))

    if include_kols:
        for kol in _SOURCES.get("twitter_kols", {}).get("cn", []):
            results["kols_cn"].extend(_nitter_kol(kol["handle"]))
            time.sleep(0.3)
        for kol in _SOURCES.get("twitter_kols", {}).get("en", []):
            results["kols_en"].extend(_nitter_kol(kol["handle"]))
            time.sleep(0.3)

    total = sum(len(v) for v in results.values())
    logger.info("Scout done: %d items", total)
    return results


def top_topics(n: int = 5) -> List[str]:
    """Return top N topic strings suitable for passing to Writer."""
    data = run(include_kols=True)
    topics = []
    for category, items in data.items():
        for item in items[:3]:
            title = item.get("title", item.get("summary", ""))
            if title:
                topics.append(title)
    return topics[:n]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    topics = top_topics(8)
    print(json.dumps(topics, ensure_ascii=False, indent=2))
