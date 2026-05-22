"""Engager: discover KOL tweets and post high-EQ replies automatically."""
import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

from core.fetcher_x import get_kol_tweets_sync
from core.publisher_x import XPublisher
from core.writer_kimi import _call_api, _load_accounts
from utils.ai_detector import detect as detect_ai
from utils.quality_gate import score as quality_score

logger = logging.getLogger(__name__)

_SOURCES_PATH = Path(__file__).parent.parent / "config" / "sources.json"
_RULES_PATH = Path(__file__).parent.parent / "config" / "rules.json"

COMMENT_SYSTEM = """你是一个真实的 Crypto 从业者，正在 X 上与同行互动。

评论写作要求：
- 真实自然，像朋友之间交流
- 有实质内容：补充数据、提问、分享亲身经历、表达不同角度
- 禁止：空洞赞美（"说得好"、"精彩"、"同意"）、AI腔（值得注意的是、综上所述）、贬低对方
- 长度：50-120字（中文），或 50-150 chars（英文）
- 结尾可以用问句引导对方回复
- 可以礼貌地提出不同看法，但要有理有据

风格：有见地、真诚、有温度"""


def _comment_prompt(kol_tweet: str, account: Dict, lang: str = "zh") -> str:
    lang_hint = "用中文回复" if lang == "zh" else "Reply in English"
    return f"""KOL 推文内容：
\"\"\"{kol_tweet}\"\"\"

你的账号：{account['name']}（{account['persona']}）
{lang_hint}

写一条高质量回复，不要加任何解释，直接输出回复正文。"""


def _generate_comment(kol_tweet: str, account: Dict, lang: str = "zh") -> Optional[str]:
    user = _comment_prompt(kol_tweet, account, lang)
    raw = _call_api(COMMENT_SYSTEM, user, max_tokens=300)
    if not raw:
        return None
    comment = raw.strip().strip('"').strip("'")
    return comment if len(comment) > 20 else None


def _passes_quality(comment: str, min_score: int = 65) -> bool:
    ai = detect_ai(comment)
    if ai["score"] > 40:
        logger.debug("Comment rejected — AI tone score %d", ai["score"])
        return False
    q = quality_score(comment, platform="x")
    return q["score"] >= min_score


def _get_kol_list(account: Dict, sources: Dict) -> List[Dict]:
    """Return a mixed CN/EN KOL list per configured ratio."""
    rules = json.loads(_RULES_PATH.read_text())
    cn_ratio = rules["engagement"]["kol_ratio"]["cn"]  # 0.7

    kols_cn = sources.get("twitter_kols", {}).get("cn", [])
    kols_en = sources.get("twitter_kols", {}).get("en", [])

    # Account-specific overrides
    specific = account.get("kol_list", [])
    if specific:
        return [{"handle": h, "lang": "zh"} for h in specific]

    target_cn = round(cn_ratio * len(kols_cn))
    selected_cn = random.sample(kols_cn, min(target_cn, len(kols_cn)))
    selected_en = random.sample(kols_en, min(len(kols_cn) - target_cn, len(kols_en)))

    return (
        [{"handle": k["handle"], "lang": "zh"} for k in selected_cn]
        + [{"handle": k["handle"], "lang": "en"} for k in selected_en]
    )


async def run_for_account(account_key: str, dry_run: bool = False) -> List[Dict]:
    accounts = _load_accounts()
    account = accounts.get(account_key)
    if not account:
        raise ValueError(f"Account '{account_key}' not found")

    sources = json.loads(_SOURCES_PATH.read_text())
    rules = json.loads(_RULES_PATH.read_text())

    daily_min = rules["engagement"]["daily_comment_target"]["min"]
    daily_max = rules["engagement"]["daily_comment_target"]["max"]
    target = random.randint(daily_min, daily_max)

    kol_list = _get_kol_list(account, sources)
    random.shuffle(kol_list)

    results = []
    async with XPublisher(account_key, headless=True) as pub:
        for kol in kol_list:
            if len(results) >= target:
                break

            tweets = get_kol_tweets_sync(kol["handle"], account_key, limit=5)
            if not tweets:
                continue

            # Pick one tweet to reply to (prefer one with some engagement potential)
            tweet = random.choice(tweets)
            if not tweet.get("text") or len(tweet["text"]) < 30:
                continue

            comment = _generate_comment(tweet["text"], account, lang=kol["lang"])
            if not comment or not _passes_quality(comment):
                logger.debug("Skipping low-quality comment for @%s", kol["handle"])
                continue

            result = {
                "account": account_key,
                "kol": kol["handle"],
                "kol_tweet": tweet["text"][:100],
                "comment": comment,
                "tweet_url": tweet.get("url", ""),
                "published": False,
            }

            if not dry_run and tweet.get("url"):
                ok = await pub.post_reply(tweet["url"], comment)
                result["published"] = ok
                if ok:
                    logger.info("Replied to @%s: %s...", kol["handle"], comment[:40])
                    time.sleep(random.uniform(30, 90))  # space out comments

            results.append(result)

    logger.info("Engager done for %s: %d comments", account_key, len(results))
    return results


async def run(account_keys: Optional[List[str]] = None, dry_run: bool = False) -> Dict:
    accounts = _load_accounts()
    if not account_keys:
        account_keys = list(accounts.keys())

    all_results = {}
    for key in account_keys:
        all_results[key] = await run_for_account(key, dry_run=dry_run)
    return all_results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    dry = "--dry" in sys.argv
    results = asyncio.run(run(dry_run=dry))
    for acct, items in results.items():
        print(f"\n{acct}: {len(items)} comments")
        for r in items:
            status = "✅" if r["published"] else "🔍 (dry)"
            print(f"  {status} @{r['kol']}: {r['comment'][:60]}...")
