"""Telegram Bot: review notifications, command handling (发/改/跳), and report push."""
import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

try:
    from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    _TG_OK = True
except ImportError:
    _TG_OK = False
    logger.warning("python-telegram-bot not installed — TG features disabled")


# ── Formatting ─────────────────────────────────────────────────────────────────

def _fmt_draft(result: Dict) -> str:
    best = result.get("best", {})
    if not best:
        return f"❌ <b>{result.get('account')}</b>: 生成失败"

    q = best.get("quality", {})
    score = q.get("score", 0)
    grade = q.get("grade", "?")
    ai_score = q.get("checks", {}).get("ai_tone", {}).get("score", "?")
    text = best.get("text", "")

    emoji = "🟢" if grade == "A" else "🟡" if grade == "B" else "🔴"
    tweets = result.get("tweets", [])
    preview_count = len(tweets)

    return (
        f"{emoji} <b>@{result.get('handle', result.get('account'))}</b> | "
        f"质量 {score}/100 | AI腔 {ai_score} | {preview_count}条推文\n"
        f"<b>话题：</b>{result.get('topic', '')[:50]}\n\n"
        f"<pre>{text[:400]}</pre>\n\n"
        f"回复 <b>发</b> 确认发布 | <b>改</b> 修改 | <b>跳</b> 跳过"
    )


def _fmt_report(report_text: str) -> str:
    return report_text


# ── Single message ─────────────────────────────────────────────────────────────

async def send_message(text: str) -> bool:
    if not _TG_OK:
        print(text)
        return True
    if not _TOKEN or not _CHAT_ID:
        logger.warning("TG not configured — printing to stdout")
        print(text)
        return True

    bot = Bot(token=_TOKEN)
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        try:
            await bot.send_message(chat_id=_CHAT_ID, text=chunk, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error("TG send failed: %s", e)
            return False
    return True


def send_sync(text: str) -> None:
    asyncio.run(send_message(text))


# ── Review batch ───────────────────────────────────────────────────────────────

async def send_review_batch(results: List[Dict]) -> int:
    """Push draft list to TG for review. Returns number sent."""
    sent = 0
    for result in results:
        msg = _fmt_draft(result)
        ok = await send_message(msg)
        if ok:
            sent += 1
        await asyncio.sleep(1)
    return sent


# ── Report ────────────────────────────────────────────────────────────────────

async def send_report(report_text: str) -> bool:
    return await send_message(report_text)


# ── Interactive Bot (发/改/跳 command handler) ─────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "SNS Operator Bot 已启动 ✅\n\n"
        "收到内容草稿后，回复：\n"
        "• <b>发</b> — 确认发布\n"
        "• <b>改</b> + 修改意见 — 重新生成\n"
        "• <b>跳</b> — 跳过这条",
        parse_mode=ParseMode.HTML,
    )


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check pipeline status."""
    from pathlib import Path
    queue = Path(__file__).parent.parent / "queue" / "pending"
    batch_count = len(list(queue.glob("kimi_batch_*.json")))
    scout_count = len(list(queue.glob("scout_*.json")))
    await update.message.reply_text(
        f"📊 <b>Pipeline 状态</b>\n"
        f"Scout 结果: {scout_count} 个\n"
        f"内容批次: {batch_count} 个",
        parse_mode=ParseMode.HTML,
    )


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    tl = text.lower()

    if tl == "发":
        await update.message.reply_text("✅ 已收到确认，将发布该内容")
        # Actual publish is triggered by orchestrator polling TG responses
    elif tl.startswith("改"):
        feedback = text[1:].strip()
        await update.message.reply_text(
            f"📝 收到修改意见{'：' + feedback if feedback else ''}，将重新生成"
        )
    elif tl == "跳":
        await update.message.reply_text("⏭ 已跳过")
    elif tl == "状态" or tl == "status":
        await _cmd_status(update, context)
    else:
        await update.message.reply_text("未知命令。回复 <b>发</b> / <b>改</b> / <b>跳</b>", parse_mode=ParseMode.HTML)


def run_bot() -> None:
    """Start interactive TG bot in its own process. Blocks until stopped."""
    if not _TG_OK:
        logger.error("python-telegram-bot not installed")
        return
    if not _TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    app = Application.builder().token(_TOKEN).build()
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    logger.info("TG Bot started, waiting for messages...")
    app.run_polling()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if "--bot" in sys.argv:
        run_bot()
    else:
        send_sync("SNS Operator 测试消息 ✅")
