"""Reporter: half-week growth & performance reports, pushed to Telegram."""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from core.fetcher_x import get_profile_stats_sync
from core.tg_publisher import send_sync
from core.writer_kimi import _call_api, _load_accounts

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_LOGS_DIR = _BASE / "logs"
_REPORTS_DIR = _BASE / "reports"
_LOGS_DIR.mkdir(exist_ok=True)
_REPORTS_DIR.mkdir(exist_ok=True)

_STATS_LOG = _LOGS_DIR / "stats_history.jsonl"
_CONTENT_LOG = _LOGS_DIR / "content_log.jsonl"

# Project start date (for progress countdown)
_PROJECT_START = datetime(2026, 5, 14)
_PROJECT_DAYS = 30


# ── Logging helpers ───────────────────────────────────────────────────────────

def log_content(account_key: str, text: str, topic: str, quality_score: int) -> None:
    entry = {
        "account": account_key,
        "timestamp": datetime.utcnow().isoformat(),
        "topic": topic[:100],
        "text": text[:200],
        "quality_score": quality_score,
    }
    with _CONTENT_LOG.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _save_snapshot(account_key: str, stats: Dict) -> None:
    entry = {"account": account_key, "timestamp": datetime.utcnow().isoformat(), **stats}
    with _STATS_LOG.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Data readers ──────────────────────────────────────────────────────────────

def _load_stats_history(account_key: str, days: int = 4) -> List[Dict]:
    if not _STATS_LOG.exists():
        return []
    cutoff = datetime.utcnow() - timedelta(days=days)
    records = []
    for line in _STATS_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
            if r["account"] == account_key and datetime.fromisoformat(r["timestamp"]) >= cutoff:
                records.append(r)
        except Exception:
            continue
    return records


def _load_content_log(account_key: str, days: int = 4) -> List[Dict]:
    if not _CONTENT_LOG.exists():
        return []
    cutoff = datetime.utcnow() - timedelta(days=days)
    records = []
    for line in _CONTENT_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
            if r.get("account") == account_key and datetime.fromisoformat(r["timestamp"]) >= cutoff:
                records.append(r)
        except Exception:
            continue
    return records


# ── Progress calculation ───────────────────────────────────────────────────────

def _progress_summary(account_key: str, account: Dict, history: List[Dict]) -> Dict:
    current_followers = history[-1].get("followers") if history else None
    start_followers = history[0].get("followers") if len(history) > 1 else None
    target = account.get("target_followers", 1000)

    days_elapsed = (datetime.now() - _PROJECT_START).days
    days_remaining = max(1, _PROJECT_DAYS - days_elapsed)

    current = current_followers or 0
    gained = (current - start_followers) if start_followers is not None else 0
    needed = max(0, target - current)
    daily_needed = round(needed / days_remaining, 1)
    progress_pct = round((current / target) * 100, 1) if target > 0 else 0

    if progress_pct >= 50:
        status = "✅ 进度良好"
    elif progress_pct >= 25:
        status = "⚠️ 需要加速"
    else:
        status = "🔴 严重滞后"

    return {
        "handle": account.get("handle", account_key),
        "name": account.get("name", account_key),
        "current": current,
        "target": target,
        "gained": gained,
        "needed": needed,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "daily_needed": daily_needed,
        "progress_pct": progress_pct,
        "status": status,
    }


# ── Report sections ────────────────────────────────────────────────────────────

def _fmt_growth(prog: Dict) -> str:
    return (
        f"\n@{prog['handle']} ({prog['name']})\n"
        f"  当前 {prog['current']} | 目标 {prog['target']} | 进度 {prog['progress_pct']}%\n"
        f"  已增长 {prog['gained']} | 还需 {prog['needed']}\n"
        f"  剩余 {prog['days_remaining']} 天 | 日增需 {prog['daily_needed']}\n"
        f"  状态：{prog['status']}"
    )


def _fmt_content(content_log: List[Dict]) -> str:
    if not content_log:
        return "  暂无发布记录"
    avg_q = sum(r.get("quality_score", 0) for r in content_log) / len(content_log)
    topics = list({r.get("topic", "")[:25] for r in content_log if r.get("topic")})[:4]
    return (
        f"  发布 {len(content_log)} 条 | 平均质量分 {avg_q:.0f}/100\n"
        f"  话题：{', '.join(topics) if topics else '暂无'}"
    )


def _ai_suggestions(account: Dict, prog: Dict, content_log: List[Dict]) -> str:
    ctx = (
        f"账号 @{prog['handle']}，粉丝 {prog['current']}，目标 {prog['target']}，"
        f"还需日增 {prog['daily_needed']}。"
        f"本期发布 {len(content_log)} 条。"
    )
    prompt = (
        f"{ctx}\n\n给出 3 条具体的下期内容优化建议，每条一句话（不超过30字），换行分隔，不要编号。"
    )
    system = "你是资深的社交媒体运营顾问，专注 Crypto 垂类账号增长。"
    raw = _call_api(system, prompt, max_tokens=250)
    if raw:
        lines = [l.strip() for l in raw.strip().splitlines() if l.strip()][:3]
        return "\n".join(f"  • {l}" for l in lines)
    return "  • 增加互动提问收尾\n  • 补充具体数据与案例\n  • 聚焦高频关键词话题"


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_report(account_key: str) -> str:
    accounts = _load_accounts()
    account = accounts.get(account_key, {})
    handle = account.get("handle", account_key)

    # Fetch live stats and snapshot
    stats = get_profile_stats_sync(handle, account_key)
    _save_snapshot(account_key, stats)

    history = _load_stats_history(account_key, days=4)
    content_log = _load_content_log(account_key, days=4)
    prog = _progress_summary(account_key, account, history)
    suggestions = _ai_suggestions(account, prog, content_log)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_lines = [
        f"📊 <b>SNS 运营半周报</b> — @{handle}",
        f"🕐 {now}",
        "",
        "━━━━━━ 粉丝增长 ━━━━━━",
        _fmt_growth(prog),
        "",
        "━━━━━━ 内容表现 ━━━━━━",
        _fmt_content(content_log),
        "",
        "━━━━━━ AI 优化建议 ━━━━━━",
        suggestions,
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    report = "\n".join(report_lines)

    # Save to file
    ts = datetime.now().strftime("%Y%m%d")
    (_REPORTS_DIR / f"report_{ts}_{account_key}.txt").write_text(report)

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
    run(sys.argv[1:] if len(sys.argv) > 1 else None)
