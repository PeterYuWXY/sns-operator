"""Reporter: generate half-week performance reports and push to Telegram."""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from core.fetcher_x import get_profile_stats_sync
from core.tg_publisher import send_sync
from core.writer_kimi import _call_api, _load_accounts

logger = logging.getLogger(__name__)

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

_STATS_FILE = _LOGS_DIR / "stats_history.jsonl"
_CONTENT_LOG = _LOGS_DIR / "content_log.jsonl"


def _load_stats_history(account_key: str, days: int = 4) -> List[Dict]:
    if not _STATS_FILE.exists():
        return []
    cutoff = datetime.utcnow() - timedelta(days=days)
    records = []
    for line in _STATS_FILE.read_text().splitlines():
        try:
            r = json.loads(line)
            if r["account"] == account_key:
                ts = datetime.fromisoformat(r["timestamp"])
                if ts >= cutoff:
                    records.append(r)
        except Exception:
            continue
    return records


def _save_snapshot(account_key: str, stats: Dict) -> None:
    entry = {
        "account": account_key,
        "timestamp": datetime.utcnow().isoformat(),
        **stats,
    }
    with _STATS_FILE.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_content_log(account_key: str, days: int = 4) -> List[Dict]:
    if not _CONTENT_LOG.exists():
        return []
    cutoff = datetime.utcnow() - timedelta(days=days)
    records = []
    for line in _CONTENT_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
            if r.get("account") == account_key:
                ts = datetime.fromisoformat(r["timestamp"])
                if ts >= cutoff:
                    records.append(r)
        except Exception:
            continue
    return records


def log_content(account_key: str, text: str, topic: str, quality_score: int) -> None:
    entry = {
        "account": account_key,
        "timestamp": datetime.utcnow().isoformat(),
        "topic": topic,
        "text": text[:200],
        "quality_score": quality_score,
    }
    with _CONTENT_LOG.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _growth_summary(history: List[Dict]) -> str:
    if len(history) < 2:
        return "（数据不足，首次运行）"
    oldest = history[0]
    latest = history[-1]
    delta = (latest.get("followers", 0) or 0) - (oldest.get("followers", 0) or 0)
    pct = delta / max(oldest.get("followers", 1), 1) * 100
    direction = "▲" if delta >= 0 else "▼"
    return f"{direction} {abs(delta)} 粉丝（{pct:+.1f}%）｜当前 {latest.get('followers', '?')} 粉"


def _content_summary(content_log: List[Dict]) -> str:
    if not content_log:
        return "（本期无发布记录）"
    avg_q = sum(r.get("quality_score", 0) for r in content_log) / len(content_log)
    topics = list({r.get("topic", "")[:20] for r in content_log if r.get("topic")})[:5]
    return (
        f"发布 {len(content_log)} 条推文 | 平均质量分 {avg_q:.0f}/100\n"
        f"话题：{', '.join(topics)}"
    )


def _ai_insights(account: Dict, stats: Dict, content_log: List[Dict]) -> str:
    ctx = (
        f"账号 @{account.get('handle')}，当前粉丝 {stats.get('followers', '?')}。"
        f"本期发布 {len(content_log)} 条，话题：{', '.join([r.get('topic','')[:15] for r in content_log[:3]])}。"
    )
    prompt = (
        f"{ctx}\n\n"
        "请给出 3 条具体的下期内容优化建议，每条一句话，不超过30字。"
        "直接输出建议，不要编号，用换行分隔。"
    )
    system = "你是资深的社交媒体运营顾问，专注于 Crypto 垂类账号增长。"
    raw = _call_api(system, prompt, max_tokens=300)
    return raw.strip() if raw else "（AI洞察生成失败）"


def generate_report(account_key: str) -> str:
    accounts = _load_accounts()
    account = accounts.get(account_key, {})
    handle = account.get("handle", account_key)

    # Fetch current stats
    stats = get_profile_stats_sync(handle, account_key)
    _save_snapshot(account_key, stats)

    history = _load_stats_history(account_key, days=4)
    content_log = _load_content_log(account_key, days=4)

    growth = _growth_summary(history)
    content = _content_summary(content_log)
    insights = _ai_insights(account, stats, content_log)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report = f"""📊 *SNS 运营半周报* — @{handle}
🕐 {now}

━━━━━━ 粉丝增长 ━━━━━━
{growth}

━━━━━━ 内容表现 ━━━━━━
{content}

━━━━━━ AI 优化建议 ━━━━━━
{insights}

━━━━━━━━━━━━━━━━━━"""
    return report


def run(account_keys: Optional[List[str]] = None) -> None:
    accounts = _load_accounts()
    if not account_keys:
        account_keys = list(accounts.keys())

    for key in account_keys:
        try:
            report = generate_report(key)
            logger.info("Report generated for %s", key)
            send_sync(report)
        except Exception as e:
            logger.error("Report failed for %s: %s", key, e)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    keys = sys.argv[1:] if len(sys.argv) > 1 else None
    run(keys)
