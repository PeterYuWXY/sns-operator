"""Orchestrator: unified entry point for all pipeline modes."""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_BASE = Path(__file__).parent.parent
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_BASE / "logs" / "orchestrator.log"),
    ],
)
logger = logging.getLogger("orchestrator")

_ACTIVE_ACCOUNTS = [a.strip() for a in os.getenv("ACTIVE_ACCOUNTS", "").split(",") if a.strip()]


def _accounts() -> list:
    if _ACTIVE_ACCOUNTS:
        return _ACTIVE_ACCOUNTS
    from core.writer_kimi import _load_accounts
    return list(_load_accounts().keys())


# ── MODE: scout ───────────────────────────────────────────────────────────────

def mode_scout() -> None:
    from core.scout import run as scout_run
    results = scout_run(save_queue=True)
    print(f"\nScout: {len(results)} candidates")
    for r in results[:5]:
        print(f"  [{r['score']:5.1f}] [{r['freshness_label']}] {r['source']}: {r['topic'][:65]}")


# ── MODE: write ───────────────────────────────────────────────────────────────

async def mode_write(slot: str, dry_run: bool = False) -> None:
    from core.scout import top_topics
    from core.writer_kimi import generate_batch
    from core.tg_publisher import send_review_batch, send_sync
    from core.reporter import log_content
    from core.publisher_x import XPublisher

    rules = json.loads((_BASE / "config" / "rules.json").read_text())
    min_q = rules["content"]["min_quality_score"]

    # Get topics (prefer cached queue, fall back to live scrape)
    topics_raw = top_topics(4, from_queue=True)
    if not topics_raw:
        logger.warning("No scout data in queue; running live scout...")
        from core.scout import run as scout_run
        candidates = scout_run(save_queue=True)
        topics_raw = [c["topic"] for c in candidates[:4]]

    topics = [{"topic": t, "context": ""} for t in topics_raw[:2]]
    accounts = _accounts()

    results = generate_batch(topics, account_keys=accounts, slot=slot, save_queue=True)
    logger.info("Generated %d drafts", len(results))

    # Push to TG for review
    sent = await send_review_batch(results)
    logger.info("Sent %d drafts to TG", sent)

    if dry_run:
        logger.info("[DRY RUN] Skipping publish")
        return

    # Auto-publish approved drafts
    for result in results:
        best = result.get("best")
        if not best or best["quality"]["score"] < min_q:
            continue
        account_key = result["account"]
        text = best["text"]
        async with XPublisher(account_key) as pub:
            ok = await pub.post_tweet(text)
            if ok:
                log_content(account_key, text, result.get("topic", ""), best["quality"]["score"])
                logger.info("Published for %s", account_key)


# ── MODE: engage ─────────────────────────────────────────────────────────────

async def mode_engage(dry_run: bool = False) -> None:
    from core.engager import run as engage_run
    from core.tg_publisher import send_sync

    results = await engage_run(account_keys=_accounts(), dry_run=dry_run)
    for acct, items in results.items():
        pub_count = sum(1 for r in items if r.get("published"))
        msg = f"🤝 <b>{acct}</b> 互动完成：{pub_count}/{len(items)} 条评论发布"
        send_sync(msg)


# ── MODE: report ─────────────────────────────────────────────────────────────

def mode_report() -> None:
    from core.reporter import run as report_run
    report_run(account_keys=_accounts())


# ── MODE: bot ─────────────────────────────────────────────────────────────────

def mode_bot() -> None:
    """Start interactive TG bot (blocking)."""
    from core.tg_publisher import run_bot
    run_bot()


# ── MODE: test ────────────────────────────────────────────────────────────────

def mode_test() -> None:
    print("=== SNS Operator smoke test ===\n")

    print("[1/4] AI Detector...")
    from utils.ai_detector import detect
    r = detect("值得注意的是，这是一个重要测试，综上所述效果很好。")
    print(f"  Score: {r['score']} Grade: {r['grade']} Issues: {len(r['issues'])}")

    print("[2/4] Quality Gate...")
    from utils.quality_gate import score
    r = score("BTC 今天突破 70K！这是减半后6个月内第三次创新高，历史上每次都这样。你觉得这次能到多少？")
    print(f"  Score: {r['score']} Grade: {r['grade']} Pass: {r['pass']}")

    print("[3/4] Scout (quick)...")
    from core.scout import top_topics
    topics = top_topics(3, from_queue=False)
    # Scout may fail on network; show partial results
    print(f"  Got {len(topics)} topics")
    for t in topics[:2]:
        print(f"  • {t[:70]}")

    print("[4/4] Config check...")
    from core.writer_kimi import _load_accounts, _KIMI_KEY, _OR_KEY
    accounts = _load_accounts()
    print(f"  Accounts: {list(accounts.keys())}")
    print(f"  Kimi key: {'✅' if _KIMI_KEY else '❌ (set KIMI_API_KEY)'}")
    print(f"  OpenRouter key: {'✅' if _OR_KEY else '— (optional)'}")

    print("\n✅ Smoke test done.")


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SNS Operator Pipeline")
    parser.add_argument(
        "--mode",
        choices=["scout", "write", "engage", "report", "bot", "test"],
        default="test",
    )
    parser.add_argument(
        "--slot",
        choices=["morning", "noon", "afternoon", "evening"],
        default="morning",
        help="Content slot (used with --mode write)",
    )
    parser.add_argument("--dry", action="store_true", help="Dry run — no actual publishing")
    args = parser.parse_args()

    if args.mode == "test":
        mode_test()
    elif args.mode == "scout":
        mode_scout()
    elif args.mode == "report":
        mode_report()
    elif args.mode == "bot":
        mode_bot()
    elif args.mode == "engage":
        asyncio.run(mode_engage(dry_run=args.dry))
    elif args.mode == "write":
        asyncio.run(mode_write(slot=args.slot, dry_run=args.dry))


if __name__ == "__main__":
    main()
