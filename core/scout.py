"""Scout: scrape trending topics with freshness scoring and keyword matching."""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import feedparser
import requests

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_SOURCES_PATH = _BASE / "config" / "sources.json"
_QUEUE_DIR = _BASE / "queue" / "pending"
_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

_REDDIT_HEADERS = {"User-Agent": "sns-operator/1.0 (research bot)"}


def _load_sources() -> Dict:
    return json.loads(_SOURCES_PATH.read_text())


# ── Scoring ───────────────────────────────────────────────────────────────────

def _freshness(published: datetime) -> Tuple[float, str]:
    """Return (score 0-1, label) based on age."""
    age = datetime.utcnow() - published
    if age <= timedelta(hours=6):
        return 1.0, "爆发期"
    elif age <= timedelta(hours=24):
        return 0.8, "热议期"
    elif age <= timedelta(hours=48):
        return 0.5, "冷却期"
    else:
        return 0.1, "过期"


def _topic_score(source_weight: int, freshness: float, text: str, hit_keywords: List[str]) -> float:
    """Composite score: source quality + freshness + keyword bonus."""
    source_score = (source_weight ** 1.3) * 0.22
    freshness_score = freshness * 100 * 0.20
    # Engagement placeholder — real impl would parse retweet/like counts
    engagement_score = 50 * 0.25
    keyword_bonus = sum(8 for kw in hit_keywords if kw.lower() in text.lower())
    return round(source_score + freshness_score + engagement_score + keyword_bonus, 2)


# ── Sources ───────────────────────────────────────────────────────────────────

def _reddit_hot(subreddit: str, weight: int, hit_keywords: List[str]) -> List[Dict]:
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=10"
    try:
        resp = requests.get(url, headers=_REDDIT_HEADERS, timeout=10)
        resp.raise_for_status()
        posts = resp.json()["data"]["children"]
        items = []
        for p in posts:
            if p["data"].get("stickied"):
                continue
            title = p["data"]["title"]
            freshness, label = _freshness(
                datetime.utcfromtimestamp(p["data"].get("created_utc", 0))
            )
            score = _topic_score(weight, freshness, title, hit_keywords)
            if score > 30:
                items.append({
                    "topic": title,
                    "score": score,
                    "source": f"r/{subreddit}",
                    "source_category": "reddit",
                    "freshness_label": label,
                    "url": p["data"].get("url", ""),
                    "type": "reddit",
                })
        return items
    except Exception as e:
        logger.warning("Reddit r/%s failed: %s", subreddit, e)
        return []


def _rss_feed(name: str, url: str, weight: int, hit_keywords: List[str]) -> List[Dict]:
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:8]:
            title = entry.get("title", "")
            if not title:
                continue
            pub_struct = entry.get("published_parsed")
            published = datetime(*pub_struct[:6]) if pub_struct else datetime.utcnow()
            freshness, label = _freshness(published)
            score = _topic_score(weight, freshness, title, hit_keywords)
            if score > 25:
                items.append({
                    "topic": title,
                    "score": score,
                    "source": name,
                    "source_category": "rss",
                    "freshness_label": label,
                    "url": entry.get("link", ""),
                    "type": "rss",
                })
        return items
    except Exception as e:
        logger.warning("RSS %s failed: %s", name, e)
        return []


def _nitter_kol(handle: str, weight: int, hit_keywords: List[str], nitter_instances: List[str]) -> List[Dict]:
    for base in nitter_instances:
        try:
            feed = feedparser.parse(f"{base}/{handle}/rss")
            if not feed.entries:
                continue
            items = []
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                pub_struct = entry.get("published_parsed")
                published = datetime(*pub_struct[:6]) if pub_struct else datetime.utcnow()
                freshness, label = _freshness(published)
                score = _topic_score(weight, freshness, title, hit_keywords)
                if score > 20:
                    items.append({
                        "topic": title[:200],
                        "score": score,
                        "source": f"@{handle}",
                        "source_category": "kol",
                        "freshness_label": label,
                        "url": entry.get("link", ""),
                        "type": "twitter",
                    })
            return items
        except Exception:
            continue
    logger.warning("Nitter failed for @%s", handle)
    return []


# ── Main ──────────────────────────────────────────────────────────────────────

def run(save_queue: bool = True) -> List[Dict]:
    """
    Run full scout. Returns sorted list of topic candidates.
    If save_queue=True, writes results to queue/pending/scout_*.json.
    """
    sources = _load_sources()
    hit_keywords = sources.get("hit_keywords", [])
    nitter_instances = sources.get("nitter_instances", ["https://nitter.net"])

    candidates: List[Dict] = []

    # Reddit
    for cfg in sources.get("reddit_subs", []):
        candidates.extend(_reddit_hot(cfg["name"], cfg.get("weight", 20), hit_keywords))
        time.sleep(0.5)

    # RSS
    for cfg in sources.get("rss_feeds", []):
        name = cfg.get("name", cfg.get("url", "rss"))
        candidates.extend(_rss_feed(name, cfg["url"], cfg.get("weight", 20), hit_keywords))

    # KOL (Twitter via Nitter)
    for kol in sources.get("twitter_kols", []):
        candidates.extend(
            _nitter_kol(kol["handle"], kol.get("weight", 20), hit_keywords, nitter_instances)
        )
        time.sleep(0.5)

    # Sort by score, deduplicate similar topics
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:20]

    if save_queue:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = _QUEUE_DIR / f"scout_{ts}.json"
        out.write_text(
            json.dumps(
                {"generated_at": datetime.now().isoformat(), "total": len(top), "candidates": top},
                ensure_ascii=False,
                indent=2,
            )
        )
        logger.info("Scout: saved %d candidates → %s", len(top), out.name)

    return top


def top_topics(n: int = 5, from_queue: bool = True) -> List[str]:
    """
    Return top N topic strings for passing to Writer.
    Prefers latest queue file; falls back to live scrape.
    """
    if from_queue:
        queue_files = sorted(_QUEUE_DIR.glob("scout_*.json"), reverse=True)
        if queue_files:
            data = json.loads(queue_files[0].read_text())
            topics = [c["topic"] for c in data.get("candidates", [])[:n]]
            if topics:
                return topics

    candidates = run(save_queue=False)
    return [c["topic"] for c in candidates[:n]]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = run()
    for r in results[:5]:
        print(f"[{r['score']:5.1f}] [{r['freshness_label']}] {r['source']}: {r['topic'][:70]}")
