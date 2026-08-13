"""Telegram bot runner for the human-in-the-loop approval layer.

The bot runs as a background asyncio task alongside the MCP server.
It uses long-polling (getUpdates) and handles inline keyboard callbacks
for Approve / Deny buttons on trade and live-trading-unlock requests.

Lifecycle (called from create_mcp_server lifespan):
    app = await start_bot(config)
    ...
    await stop_bot(app)
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, ContextTypes

from mcp_server.telegram.approval import update_approval_status
from mcp_server.telegram.config import TelegramConfig

logger = logging.getLogger(__name__)

_APPROVE_PREFIX = "approve:"
_DENY_PREFIX = "deny:"


async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline-button presses from Telegram."""
    query = update.callback_query
    if query is None:
        return

    await query.answer()
    data = query.data or ""

    if data.startswith(_APPROVE_PREFIX):
        approval_id = data[len(_APPROVE_PREFIX):]
        who = getattr(query.from_user, "username", None) or str(query.from_user.id)
        update_approval_status(
            approval_id,
            "approved",
            telegram_message_id=query.message.message_id if query.message else None,
            resolve_note=f"Approved by @{who}",
        )
        try:
            await query.edit_message_text(
                text=f"✅ *Approved* by @{who}\n\n`{approval_id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        logger.info("Approval %s approved via Telegram by %s", approval_id, who)

    elif data.startswith(_DENY_PREFIX):
        approval_id = data[len(_DENY_PREFIX):]
        who = getattr(query.from_user, "username", None) or str(query.from_user.id)
        update_approval_status(
            approval_id,
            "denied",
            telegram_message_id=query.message.message_id if query.message else None,
            resolve_note=f"Denied by @{who}",
        )
        try:
            await query.edit_message_text(
                text=f"❌ *Denied* by @{who}\n\n`{approval_id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        logger.info("Approval %s denied via Telegram by %s", approval_id, who)


def build_approval_keyboard(approval_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"{_APPROVE_PREFIX}{approval_id}"),
        InlineKeyboardButton("❌ Deny", callback_data=f"{_DENY_PREFIX}{approval_id}"),
    ]])


async def start_bot(config: TelegramConfig) -> Application:
    """Build, initialise, and start the bot's polling loop. Returns the Application."""
    app: Application = ApplicationBuilder().token(config.bot_token).build()
    app.add_handler(CallbackQueryHandler(_on_callback))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]
    logger.info("Telegram bot started, polling for updates (chat_id=%s)", config.chat_id)
    return app


async def stop_bot(app: Application) -> None:
    """Stop polling and shut down the bot cleanly."""
    try:
        if app.updater and app.updater.running:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Telegram bot stopped")
    except Exception as exc:
        logger.warning("Error stopping Telegram bot: %s", exc)


async def send_approval_request(
    app: Application,
    config: TelegramConfig,
    approval_id: str,
    message_text: str,
) -> Optional[int]:
    """Send a message with Approve / Deny buttons. Returns the Telegram message_id."""
    keyboard = build_approval_keyboard(approval_id)
    try:
        msg = await app.bot.send_message(
            chat_id=config.chat_id,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=keyboard,
        )
        return msg.message_id
    except Exception as exc:
        logger.error("Failed to send Telegram approval request: %s", exc)
        return None


async def send_notification(
    app: Application,
    config: TelegramConfig,
    message_text: str,
) -> Optional[int]:
    """Send a plain notification message (no buttons)."""
    try:
        msg = await app.bot.send_message(
            chat_id=config.chat_id,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return msg.message_id
    except Exception as exc:
        logger.error("Failed to send Telegram notification: %s", exc)
        return None
