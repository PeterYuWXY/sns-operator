"""Orchestrator: main entry point for all pipeline modes."""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent.parent / "logs" / "orchestrator.log"),
    ],
)
logger = logging.getLogger("orchestrator")

_RULES_PATH = Path(__file__).parent.parent / "config" / "rules.json"
_ACTIVE_ACCOUNTS = os.getenv("ACTIVE_ACCOUNTS", "").split(",")


def _accounts() -> list:
    accts = [a.strip() for a in _ACTIVE_ACCOUNTS if a.strip()]
    if not accts:
        from core.writer_kimi import _load_accounts
        accts = list(_load_accounts().keys())
    return accts


# ── MODE: write ──────────────────────────────────────────────────────────────

async def mode_write(slot: str, dry_run: bool = False) -> None:
    from core.scout import top_topics
    from core.writer_kimi import generate
    from core.tg_publisher import send_sync
    from core.reporter import log_content
    from core.publisher_x import XPublisher

    rules = json.loads(_RULES_PATH.read_text())
    slot_cfg = rules["slots"].get(slot, {})
    min_q = rules["content"]["min_quality_score"]

    topics_raw = top_topics(5)
    accounts = _accounts()

    for account_key in accounts:
        for topic in topics_raw[:2]:  # 2 topics per slot per account
            result = generate(
                account_key=account_key,
                topic=topic,
                slot=slot,
                content_type=slot_cfg.get("format", "tweet"),
                min_quality=min_q,
            )

            best = result.get("best")
            if not best:
                logger.warning("No valid draft for %s / %s", account_key, topic[:30])
                continue

            if not result.get("approved"):
                logger.info("Draft below threshold for %s, sending to TG for review", account_key)

            # Push to TG for review
            msg = (
                f"📝 *新推文草稿* | @{result.get('handle')} | {slot}\n"
                f"话题: {topic[:40]}\n"
                f"质量: {best['quality']['score']}/100 (AI腔: {best['quality']['checks'].get('ai_tone',{}).get('score','?')})\n\n"
                f"```\n{best['text']}\n```"
            )
            send_sync(msg)
            log_content(account_key, best["text"], topic, best["quality"]["score"])

            if dry_run:
                logger.info("[DRY] Would post: %s...", best["text"][:60])
                continue

            if result.get("approved"):
                async with XPublisher(account_key) as pub:
                    ok = await pub.post_tweet(best["text"])
                    logger.info("Post result for %s: %s", account_key, ok)


# ── MODE: engage ─────────────────────────────────────────────────────────────

async def mode_engage(dry_run: bool = False) -> None:
    from core.engager import run as engage_run
    results = await engage_run(account_keys=_accounts(), dry_run=dry_run)
    for acct, items in results.items():
        published = sum(1 for r in items if r.get("published"))
        logger.info("Engage %s: %d/%d published", acct, published, len(items))


# ── MODE: report ─────────────────────────────────────────────────────────────

def mode_report() -> None:
    from core.reporter import run as report_run
    report_run(account_keys=_accounts())


# ── MODE: scout ──────────────────────────────────────────────────────────────

def mode_scout() -> None:
    from core.scout import run as scout_run
    import json
    data = scout_run()
    for category, items in data.items():
        print(f"\n── {category} ({len(items)} items) ──")
        for item in items[:3]:
            print(f"  • {item.get('title', item.get('text', ''))[:80]}")


# ── MODE: test ───────────────────────────────────────────────────────────────

def mode_test() -> None:
    """Quick smoke test of all modules."""
    print("=== SNS Operator smoke test ===\n")

    print("[1] Scout...")
    from core.scout import top_topics
    topics = top_topics(3)
    print(f"  Got {len(topics)} topics: {topics[:2]}")

    print("[2] AI Detector...")
    from utils.ai_detector import detect
    r = detect("值得注意的是，这是一个重要的测试，综上所述效果不错。")
    print(f"  Score: {r['score']}, Grade: {r['grade']}")

    print("[3] Quality Gate...")
    from utils.quality_gate import score
    r = score("BTC 今天突破 70K！这是历史上第三次在减半后6个月内创新高，你觉得这次能到多少？")
    print(f"  Score: {r['score']}, Grade: {r['grade']}, Pass: {r['pass']}")

    print("[4] Config check...")
    from core.writer_kimi import _load_accounts, _API_KEY
    accounts = _load_accounts()
    print(f"  Accounts: {list(accounts.keys())}")
    print(f"  API key set: {'Yes' if _API_KEY else 'No (set KIMI_API_KEY)'}")

    print("\n✅ Smoke test done.")


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SNS Operator Orchestrator")
    parser.add_argument(
        "--mode",
        choices=["write", "engage", "report", "scout", "test"],
        default="test",
        help="Pipeline mode to run",
    )
    parser.add_argument(
        "--slot",
        choices=["morning", "noon", "afternoon", "evening"],
        default="morning",
        help="Content slot (used with --mode write)",
    )
    parser.add_argument("--dry", action="store_true", help="Dry run — generate but don't publish")
    args = parser.parse_args()

    if args.mode == "test":
        mode_test()
    elif args.mode == "scout":
        mode_scout()
    elif args.mode == "report":
        mode_report()
    elif args.mode == "engage":
        asyncio.run(mode_engage(dry_run=args.dry))
    elif args.mode == "write":
        asyncio.run(mode_write(slot=args.slot, dry_run=args.dry))


if __name__ == "__main__":
    main()
