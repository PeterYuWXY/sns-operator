"""Writer: generate SNS content via Kimi API (Anthropic Messages format)."""
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

from utils.quality_gate import score as quality_score

logger = logging.getLogger(__name__)

_ACCOUNTS_PATH = Path(__file__).parent.parent / "config" / "accounts.json"
_RULES_PATH = Path(__file__).parent.parent / "config" / "rules.json"

_API_BASE = os.getenv("KIMI_API_BASE", "https://api.moonshot.cn/v1")
_API_KEY = os.getenv("KIMI_API_KEY", "")
_MODEL = os.getenv("KIMI_MODEL", "moonshot-v1-8k")


def _load_accounts() -> Dict:
    if _ACCOUNTS_PATH.exists():
        return json.loads(_ACCOUNTS_PATH.read_text())
    example = _ACCOUNTS_PATH.parent / "accounts.json.example"
    return json.loads(example.read_text())


def _load_rules() -> Dict:
    return json.loads(_RULES_PATH.read_text())


def _system_prompt(account: Dict) -> str:
    lang_hint = "用中文撰写。" if account.get("language", "zh") == "zh" else "Write in English."
    return f"""你是 {account['name']}，X 平台上的 {account['persona']}。

人设风格：{account['style']}
内容方向：{', '.join(account['topics'])}
语气基调：{account['tone']}
{lang_hint}

写作要求（严格遵守）：
- 像真人说话，禁止 AI 腔（禁用：值得注意的是、综上所述、不得不说、首先其次最后等模板语）
- 有自己的观点，不中立不模糊
- 用具体数字、案例、个人经历，不用空泛描述
- 推文字数 180-260 字符（中文约 60-85 个汉字）
- Hashtag ≤ 3 个
- 结尾可以用提问来引导互动
- 禁止：赞美词（非常棒、厉害了）、过度标点、列表式格式"""


def _user_prompt(topic: str, context: str, slot: str, content_type: str) -> str:
    slot_guide = {
        "morning":   "早上8点，市场刚开盘，发一条关于昨夜/早间动态的推文",
        "noon":      "中午12点，发一条投资洞察或数据分析，可以是 thread 第一条",
        "afternoon": "下午4点，发一条关于产品/链上数据/工具的推文",
        "evening":   "晚上8点，发一条互动性强的推文，适合引发评论",
    }.get(slot, "发一条高质量推文")

    return f"""话题：{topic}
背景信息：{context}
场景：{slot_guide}
内容类型：{content_type}

请生成 3 个版本的推文，每个版本用 ---SPLIT--- 分隔。
只输出推文正文，不要加序号、解释、标题。"""


def _call_api(system: str, user: str, max_tokens: int = 800) -> Optional[str]:
    if not _API_KEY:
        raise ValueError("KIMI_API_KEY not set")

    # Try Anthropic Messages format first (Kimi Coding API)
    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.85,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }

    # Kimi Coding uses /messages endpoint; standard Moonshot uses /chat/completions
    for endpoint, fmt in [
        (f"{_API_BASE}/messages", "anthropic"),
        (f"{_API_BASE}/chat/completions", "openai"),
    ]:
        try:
            if fmt == "openai":
                payload = {
                    "model": _MODEL,
                    "max_tokens": max_tokens,
                    "temperature": 0.85,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                }
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            # Parse response (handle both formats)
            if fmt == "anthropic":
                return data["content"][0]["text"]
            else:
                return data["choices"][0]["message"]["content"]
        except requests.HTTPError as e:
            logger.warning("API call to %s failed: %s", endpoint, e)
            continue
        except Exception as e:
            logger.error("Unexpected error calling %s: %s", endpoint, e)
            break

    return None


def generate(
    account_key: str,
    topic: str,
    context: str = "",
    slot: str = "morning",
    content_type: str = "tweet",
    min_quality: int = 65,
) -> Dict:
    accounts = _load_accounts()
    account = accounts.get(account_key)
    if not account:
        raise ValueError(f"Account '{account_key}' not found in accounts.json")

    system = _system_prompt(account)
    user = _user_prompt(topic, context, slot, content_type)

    raw = _call_api(system, user)
    if not raw:
        return {"account": account_key, "error": "API call failed", "drafts": []}

    drafts = [d.strip() for d in raw.split("---SPLIT---") if d.strip()]

    scored = []
    for draft in drafts:
        q = quality_score(draft, platform="x")
        scored.append({"text": draft, "quality": q})

    # Sort by quality score descending
    scored.sort(key=lambda x: x["quality"]["score"], reverse=True)

    best = scored[0] if scored else None
    return {
        "account": account_key,
        "handle": account.get("handle", ""),
        "topic": topic,
        "slot": slot,
        "drafts": scored,
        "best": best,
        "approved": best and best["quality"]["score"] >= min_quality,
    }


def generate_batch(
    topics: List[Dict],
    account_keys: Optional[List[str]] = None,
    slot: str = "morning",
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
            time.sleep(1)  # rate limit
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = generate(
        account_key="petery",
        topic="Bitcoin 减半已完成，历史上减半后3个月内价格走势分析",
        context="2024年4月，第四次减半完成，矿工收入减半",
        slot="morning",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
