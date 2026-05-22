"""Writer: generate SNS content via Kimi API with OpenRouter fallback."""
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

from utils.quality_gate import score as quality_score

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_ACCOUNTS_PATH = _BASE / "config" / "accounts.json"
_QUEUE_DIR = _BASE / "queue" / "pending"
_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

# Kimi Coding API (Anthropic Messages format)
_KIMI_KEY = os.getenv("KIMI_API_KEY", "")
_KIMI_BASE = os.getenv("KIMI_API_BASE", "https://api.kimi.com/coding/v1")
_KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-for-coding")

# OpenRouter fallback (optional)
_OR_KEY = os.getenv("OPENROUTER_API_KEY", "")
_OR_BASE = "https://openrouter.ai/api/v1"
_OR_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-5")


def _load_accounts() -> Dict:
    if _ACCOUNTS_PATH.exists():
        return json.loads(_ACCOUNTS_PATH.read_text())
    example = _ACCOUNTS_PATH.parent / "accounts.json.example"
    return json.loads(example.read_text())


def _call_api(system: str, user: str, max_tokens: int = 2000) -> Optional[str]:
    """Try Kimi Coding API (Anthropic format), then Moonshot (OpenAI format), then OpenRouter."""
    attempts = []

    if _KIMI_KEY:
        attempts.append(
            ("kimi_anthropic", _KIMI_KEY, f"{_KIMI_BASE}/messages", _KIMI_MODEL, "anthropic")
        )
        attempts.append(
            ("kimi_openai", _KIMI_KEY, f"{_KIMI_BASE}/chat/completions", _KIMI_MODEL, "openai")
        )

    if _OR_KEY:
        attempts.append(
            ("openrouter", _OR_KEY, f"{_OR_BASE}/chat/completions", _OR_MODEL, "openai")
        )

    if not attempts:
        raise ValueError("No API key configured. Set KIMI_API_KEY or OPENROUTER_API_KEY in .env")

    for name, key, url, model, fmt in attempts:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/PeterYuWXY/sns-operator"

        if fmt == "anthropic":
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.85,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        else:
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.85,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code in (404, 400) and name == "kimi_anthropic":
                logger.debug("Kimi Anthropic format not supported, trying next")
                continue
            resp.raise_for_status()
            data = resp.json()
            if fmt == "anthropic":
                text = data["content"][0]["text"]
            else:
                text = data["choices"][0]["message"]["content"]
            logger.info("API call success via %s", name)
            return text
        except Exception as e:
            logger.warning("API attempt %s failed: %s", name, e)
            continue

    return None


def _parse_thread(raw: str) -> List[str]:
    """Parse AI output into a list of individual tweets."""
    # Try [推文N] markers first
    parts = re.split(r"\[推文\d+\]|\n---+\n|---SPLIT---", raw)
    tweets = [p.strip() for p in parts if p.strip() and len(p.strip()) > 20]

    # Fallback: split by blank lines
    if not tweets:
        tweets = [p.strip() for p in raw.split("\n\n") if p.strip() and len(p.strip()) > 20]

    return tweets[:8]  # X threads cap at 8


def _system_prompt(account: Dict) -> str:
    lang_hint = "用中文写作，专业术语可用英文。" if account.get("language", "zh") == "zh" else "Write in English."
    return f"""你是 {account['name']}，{account['persona']}。

写作风格：{account['style']}
内容方向：{', '.join(account.get('topics', []))}
语气基调：{account['tone']}
{lang_hint}

绝对禁止（AI腔检测会扣分）：
- "值得注意的是"、"总的来说"、"综上所述"、"我们不禁要问"、"毋庸置疑" 等套话
- "不仅...而且..."、"一方面...另一方面..." 等机械句式
- "显著提升"、"充分利用"、"旨在"、"致力于" 等书面词汇
- 空洞总结，没有具体数据
- 对任何项目/人贬低攻击
- 模糊表达（可能、也许、大概）超过3处

必须做到：
- 给出具体数字或案例
- 表达明确观点，不骑墙
- 口语化，像真人发推
- 结尾用问句引导互动（+分项）"""


def _user_prompt(topic: str, context: str, slot: str) -> str:
    slot_guide = {
        "morning":   "早上8点，市场刚开，写一条关于昨夜/早间动态的推文或 Thread",
        "noon":      "中午12点，写一条投资洞察或数据分析 Thread（3-5条）",
        "afternoon": "下午4点，写一条关于产品/链上数据/工具推荐的推文",
        "evening":   "晚上8点，写一条互动性强的推文，引发评论",
    }.get(slot, "写一条高质量推文")

    return f"""话题：{topic}
背景信息：{context}
场景：{slot_guide}

输出格式（Thread 模式）：
[推文1] 必须抓眼球（冲突、数据、反直觉）
[推文2] 核心论点
[推文3] 数据/案例支撑
[推文4] 结论或延伸
[推文5] 提问引导互动（可选）

每条推文独立可读，中文约60-85字。只输出推文正文，不要解释。"""


def generate(
    account_key: str,
    topic: str,
    context: str = "",
    slot: str = "morning",
    min_quality: int = 65,
) -> Dict:
    accounts = _load_accounts()
    account = accounts.get(account_key)
    if not account:
        raise ValueError(f"Account '{account_key}' not in accounts.json")

    system = _system_prompt(account)
    user = _user_prompt(topic, context, slot)

    raw = _call_api(system, user)
    if not raw:
        return {"account": account_key, "error": "API call failed", "tweets": [], "raw": ""}

    tweets = _parse_thread(raw)
    scored_tweets = []
    for t in tweets:
        q = quality_score(t, platform="x")
        scored_tweets.append({"text": t, "quality": q})

    scored_tweets.sort(key=lambda x: x["quality"]["score"], reverse=True)
    best = scored_tweets[0] if scored_tweets else None

    return {
        "account": account_key,
        "account_name": account.get("name", ""),
        "handle": account.get("handle", ""),
        "topic": topic,
        "slot": slot,
        "tweets": scored_tweets,
        "best": best,
        "approved": bool(best and best["quality"]["score"] >= min_quality),
        "generated_at": datetime.now().isoformat(),
    }


def generate_batch(
    topics: List[Dict],
    account_keys: Optional[List[str]] = None,
    slot: str = "morning",
    save_queue: bool = True,
) -> List[Dict]:
    accounts = _load_accounts()
    if not account_keys:
        account_keys = list(accounts.keys())

    results = []
    for account_key in account_keys:
        for item in topics:
            topic = item.get("topic", item) if isinstance(item, dict) else item
            context = item.get("context", "") if isinstance(item, dict) else ""
            result = generate(account_key, topic, context, slot)
            results.append(result)
            logger.info(
                "Generated for %s / %s... | approved=%s",
                account_key, topic[:30], result.get("approved")
            )
            time.sleep(1.5)

    if save_queue and results:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = _QUEUE_DIR / f"kimi_batch_{ts}.json"
        out.write_text(
            json.dumps(
                {"generated_at": datetime.now().isoformat(), "total": len(results), "contents": results},
                ensure_ascii=False,
                indent=2,
            )
        )
        logger.info("Saved %d items to %s", len(results), out.name)

    return results


def load_latest_batch() -> Optional[Dict]:
    """Load most recent queue batch file."""
    files = sorted(_QUEUE_DIR.glob("kimi_batch_*.json"), reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = generate(
        account_key="account1",
        topic="Bitcoin 减半后6个月历史走势对比分析",
        context="第四次减半已于2024年4月完成",
        slot="morning",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
