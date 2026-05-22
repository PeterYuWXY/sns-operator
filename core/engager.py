"""Engager: discover KOL tweets and post high-EQ replies automatically."""
import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from core.fetcher_x import get_kol_tweets_sync
from core.publisher_x import XPublisher
from core.writer_kimi import _call_api, _load_accounts
from utils.ai_detector import detect as detect_ai
from utils.quality_gate import score as quality_score

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_RULES_PATH = _BASE / "config" / "rules.json"

# ── KOL Lists ─────────────────────────────────────────────────────────────────

_KOLS: Dict[str, List[Dict]] = {
    "cn": [
        {"handle": "Alpha_Sniper_CN",      "focus": "交易",   "style": "技术分析"},
        {"handle": "WuBlockchain",          "focus": "新闻",   "style": "客观报道"},
        {"handle": "Odaily_China",          "focus": "资讯",   "style": "行业分析"},
        {"handle": "BlockBeatsNews",        "focus": "项目",   "style": "深度研究"},
        {"handle": "TechFlowPost",          "focus": "DeFi",   "style": "数据驱动"},
    ],
    "en": [
        {"handle": "cz_binance",            "focus": "交易所", "style": "行业观点"},
        {"handle": "VitalikButerin",        "focus": "ETH",    "style": "技术哲学"},
        {"handle": "saylor",                "focus": "BTC",    "style": "宏观叙事"},
        {"handle": "DegenSpartan",          "focus": "DeFi",   "style": "交易洞察"},
        {"handle": "Route2FI",              "focus": "理财",   "style": "策略分享"},
    ],
}

# ── Quality Rules ─────────────────────────────────────────────────────────────

_FORBIDDEN = [
    "骂人", "傻逼", "垃圾", "骗子", "去死",
    "不行", "垃圾项目", "必死", "跑路", "rug",
]

_FILLER = ["gm", "好的", "不错", "赞", "学习了", "同意", "👍", "棒棒", "厉害"]

# Comment templates by topic type (fallback if LLM fails)
_TEMPLATES: Dict[str, List[str]] = {
    "trading": [
        "这个观点有意思。我补充一个数据：{obs}。{question}",
        "认同你的判断。我观察到{obs}，和你的分析一致。你怎么看后续走势？",
        "说得好。不过我觉得还要考虑{obs}，不然判断可能不完整。",
    ],
    "news": [
        "这条消息的关键点是{obs}。后续影响可能是{question}",
        "刚看到这个。我的第一反应：{obs}，因为类似的情况历史上出现过。",
        "细节：{obs}。这个信号值得持续跟踪。",
    ],
    "analysis": [
        "分析很扎实。我补充一个角度：{obs}。",
        "认同底层逻辑。不过{obs}这个因素可能也要考虑进去，你怎么看？",
        "这个框架不错。我用类似方法分析过，{obs}。",
    ],
    "philosophy": [
        "让我想到{obs}。本质上是同一个规律。",
        "说得好。不过现实中{obs}，执行层面有很多摩擦。",
        "认同。我观察到{obs}也符合这个规律。",
    ],
}

# ── Comment Generation ────────────────────────────────────────────────────────

_COMMENT_SYSTEM = """你是一个真实的 Crypto 从业者，在 X 上与同行互动。

评论要求：
- 真实自然，像朋友之间说话
- 必须有实质内容：补充数据、提出问题、分享经历、给出不同角度
- 禁止：空洞赞美（"说得好"、"精彩"、"同意"）、AI腔套话、贬低对方
- 长度：50-120字（中文）或 60-150 chars（英文）
- 结尾可以用问句引导对方回复
- 可以礼貌地提出不同看法，但要有理有据
- 不骑墙，表明你的真实判断"""


def _comment_prompt(tweet_text: str, account: Dict, lang: str = "zh") -> str:
    lang_hint = "用中文回复" if lang == "zh" else "Reply in English"
    return (
        f"KOL 推文：\n\"\"\"{tweet_text}\"\"\"\n\n"
        f"你的账号：{account['name']}（{account['persona']}）\n"
        f"{lang_hint}\n\n"
        "直接输出回复正文，不要任何解释。"
    )


def _fallback_comment(kol: Dict, tweet_text: str) -> str:
    focus = kol.get("focus", "")
    style = kol.get("style", "")
    obs = "近期链上数据显示大户在持续增持"
    question = "这个指标在熊市末期是否同样有效？"

    if focus in ("交易", "DeFi", "理财"):
        pool = _TEMPLATES["trading"]
    elif focus in ("新闻", "资讯"):
        pool = _TEMPLATES["news"]
    elif style in ("深度研究", "行业分析", "数据驱动"):
        pool = _TEMPLATES["analysis"]
    else:
        pool = _TEMPLATES["philosophy"]

    return random.choice(pool).format(obs=obs, question=question)


def _generate_comment(tweet_text: str, account: Dict, kol: Dict) -> Optional[str]:
    lang = "zh" if kol.get("lang", "cn") == "cn" else "en"
    raw = _call_api(_COMMENT_SYSTEM, _comment_prompt(tweet_text, account, lang), max_tokens=300)
    if raw:
        comment = raw.strip().strip('"').strip("'")
        if len(comment) >= 30:
            return comment
    # Fallback to template
    logger.debug("LLM failed, using template fallback")
    return _fallback_comment(kol, tweet_text)


def _passes_quality(comment: str) -> bool:
    # Forbidden words
    if any(w in comment for w in _FORBIDDEN):
        logger.debug("Comment blocked: forbidden word")
        return False
    # Filler check
    if any(comment.strip().lower() == f for f in _FILLER):
        logger.debug("Comment blocked: filler")
        return False
    # Minimum length
    if len(comment) < 25:
        logger.debug("Comment too short: %d chars", len(comment))
        return False
    # AI tone
    ai = detect_ai(comment)
    if ai["score"] > 40:
        logger.debug("Comment blocked: AI tone score %d", ai["score"])
        return False
    return True


def _select_kols(cn_ratio: float = 0.7) -> List[Dict]:
    """Return a randomized KOL list respecting the CN/EN ratio."""
    cn_kols = [dict(k, lang="cn") for k in _KOLS["cn"]]
    en_kols = [dict(k, lang="en") for k in _KOLS["en"]]
    random.shuffle(cn_kols)
    random.shuffle(en_kols)
    n_cn = max(1, round(cn_ratio * (len(cn_kols) + len(en_kols))))
    n_en = len(cn_kols) + len(en_kols) - n_cn
    return cn_kols[:n_cn] + en_kols[:n_en]


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_for_account(account_key: str, dry_run: bool = False) -> List[Dict]:
    accounts = _load_accounts()
    account = accounts.get(account_key)
    if not account:
        raise ValueError(f"Account '{account_key}' not found")

    rules = json.loads(_RULES_PATH.read_text())
    eng_rules = rules.get("engagement", {})
    target_min = eng_rules.get("daily_comment_target", {}).get("min", 5)
    target_max = eng_rules.get("daily_comment_target", {}).get("max", 10)
    target = random.randint(target_min, target_max)
    cn_ratio = eng_rules.get("kol_ratio", {}).get("cn", 0.7)

    kol_list = _select_kols(cn_ratio)
    results: List[Dict] = []

    async with XPublisher(account_key, headless=True) as pub:
        for kol in kol_list:
            if len(results) >= target:
                break

            tweets = get_kol_tweets_sync(kol["handle"], account_key, limit=5)
            if not tweets:
                continue

            tweet = random.choice(tweets)
            if not tweet.get("text") or len(tweet["text"]) < 30:
                continue

            comment = _generate_comment(tweet["text"], account, kol)
            if not comment or not _passes_quality(comment):
                continue

            result: Dict = {
                "account": account_key,
                "kol": kol["handle"],
                "kol_tweet": tweet["text"][:120],
                "comment": comment,
                "tweet_url": tweet.get("url", ""),
                "published": False,
            }

            if not dry_run and tweet.get("url"):
                ok = await pub.post_reply(tweet["url"], comment)
                result["published"] = ok
                if ok:
                    logger.info("Replied to @%s: %s...", kol["handle"], comment[:50])
                    time.sleep(random.uniform(45, 120))  # human-like spacing

            results.append(result)

    logger.info("Engager %s: %d/%d comments published", account_key, sum(r["published"] for r in results), len(results))
    return results


async def run(account_keys: Optional[List[str]] = None, dry_run: bool = False) -> Dict:
    accounts = _load_accounts()
    if not account_keys:
        account_keys = list(accounts.keys())

    all_results: Dict[str, List[Dict]] = {}
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
            s = "✅" if r["published"] else "🔍 (dry)"
            print(f"  {s} @{r['kol']}: {r['comment'][:60]}...")
