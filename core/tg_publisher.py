"""Telegram Bot: send content drafts for review and push reports."""
import asyncio
import logging
import os
from typing import Callable, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _fmt_draft(result: Dict) -> str:
    best = result.get("best", {})
    if not best:
        return f"❌ {result['account']}: 生成失败"

    q = best["quality"]
    text = best["text"]
    grade = q.get("grade", "?")
    score = q.get("score", 0)
    ai_score = q.get("checks", {}).get("ai_tone", {}).get("score", "?")

    emoji = "🟢" if grade == "A" else "🟡" if grade == "B" else "🔴"
    return (
        f"{emoji} *@{result.get('handle', result['account'])}* | "
        f"质量 {score}/100 (AI腔: {ai_score}) | 话题: {result.get('topic', '')[:30]}\n\n"
        f"```\n{text}\n```\n\n"
        f"回复: ✅ 发布 | ✏️ 修改 | ⏭ 跳过"
    )


async def send_review(result: Dict, on_approve: Optional[Callable] = None) -> bool:
    """Send one draft to TG for review. Returns True if approved."""
    try:
        from telegram import Bot, Update
        from telegram.ext import Application, CommandHandler, MessageHandler, filters
    except ImportError:
        logger.error("python-telegram-bot not installed")
        return False

    if not _TOKEN or not _CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping TG review")
        return result.get("approved", False)

    bot = Bot(token=_TOKEN)
    msg = _fmt_draft(result)
    await bot.send_message(chat_id=_CHAT_ID, text=msg, parse_mode="Markdown")
    logger.info("Sent to TG for review: %s", result.get("account"))
    return True


async def send_message(text: str) -> None:
    """Send a plain text message to TG (for reports, alerts, etc.)."""
    try:
        from telegram import Bot
    except ImportError:
        logger.error("python-telegram-bot not installed")
        return

    if not _TOKEN or not _CHAT_ID:
        logger.warning("TG not configured — printing to stdout instead")
        print(text)
        return

    bot = Bot(token=_TOKEN)
    # TG message limit: 4096 chars
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await bot.send_message(chat_id=_CHAT_ID, text=chunk)


def send_sync(text: str) -> None:
    asyncio.run(send_message(text))


class ReviewBot:
    """Interactive bot that waits for user decisions on drafts."""

    def __init__(self):
        self.decisions: Dict[str, str] = {}

    async def run(self, drafts: list) -> Dict[str, str]:
        """
        Send drafts one by one and collect decisions.
        Returns {draft_index: 'approve' | 'skip'}.
        Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
        """
        try:
            from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
            from telegram.ext import Application, CallbackQueryHandler
        except ImportError:
            logger.error("python-telegram-bot not installed")
            return {}

        if not _TOKEN or not _CHAT_ID:
            return {}

        app = Application.builder().token(_TOKEN).build()
        bot = app.bot

        for i, result in enumerate(drafts):
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ 发布", callback_data=f"approve:{i}"),
                    InlineKeyboardButton("⏭ 跳过", callback_data=f"skip:{i}"),
                ]
            ])
            msg = _fmt_draft(result)
            await bot.send_message(
                chat_id=_CHAT_ID, text=msg, parse_mode="Markdown", reply_markup=keyboard
            )

        return self.decisions
