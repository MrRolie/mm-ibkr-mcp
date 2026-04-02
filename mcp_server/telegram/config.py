"""Telegram bot configuration for the human-in-the-loop approval layer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class TelegramConfig:
    """Runtime configuration for the Telegram approval bot."""

    bot_token: str
    chat_id: str
    # How long (seconds) a trade approval request waits for a human response.
    approval_timeout_seconds: int = 300
    # How long (seconds) a live-trading-unlock request waits.
    live_unlock_timeout_seconds: int = 120

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


def get_telegram_config() -> Optional[TelegramConfig]:
    """Load Telegram config from environment variables.

    Returns None when TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are absent,
    which disables all Telegram features gracefully.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        return None

    return TelegramConfig(
        bot_token=token,
        chat_id=chat_id,
        approval_timeout_seconds=_parse_int(
            os.environ.get("TELEGRAM_APPROVAL_TIMEOUT_SECONDS"), 300
        ),
        live_unlock_timeout_seconds=_parse_int(
            os.environ.get("TELEGRAM_LIVE_UNLOCK_TIMEOUT_SECONDS"), 120
        ),
    )


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
