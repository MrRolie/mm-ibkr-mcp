"""
Tests for Telegram MarkdownV2 message formatters.

Verifies that:
- Estimated notional and commission render with thousands separators
- MarkdownV2 special characters in generated numbers are escaped
- Optional preview fields are omitted when absent
"""

from __future__ import annotations

from mcp_server.telegram.notifications import (
    format_trade_approval,
    format_trade_intent_approval,
)


def _order_data() -> dict:
    return {
        "instrument": {"symbol": "AAPL", "securityType": "STK"},
        "side": "BUY",
        "quantity": 10,
        "orderType": "LMT",
        "limitPrice": 150.25,
        "clientOrderId": "coid-9",
    }


def test_trade_approval_escapes_notional_and_commission() -> None:
    """The separator and decimal point of formatted amounts must be escaped."""
    text = format_trade_approval(
        "appr-1",
        _order_data(),
        {"estimatedNotional": 1502.5, "estimatedCommission": 1.05},
        "unit test",
    )

    assert r"*Est\. Notional:* $1,502\.50" in text
    assert r"*Est\. Commission:* $1\.05" in text


def test_trade_approval_omits_absent_preview_fields() -> None:
    """No estimate lines are emitted when the preview carries no amounts."""
    text = format_trade_approval("appr-2", _order_data(), None, "unit test")

    assert "Notional" not in text
    assert "Commission" not in text
    assert "`appr-2`" in text


def test_trade_approval_includes_warnings() -> None:
    """Warnings are surfaced, capped at three entries."""
    text = format_trade_approval(
        "appr-3",
        _order_data(),
        {"estimatedNotional": 100.0, "warnings": ["w1", "w2", "w3", "w4"]},
        "unit test",
    )

    assert "w1" in text and "w3" in text
    assert "w4" not in text


def test_trade_intent_approval_truncates_order_list() -> None:
    """At most eight orders are listed, with an escaped overflow count."""
    orders = [
        {
            "instrument": {"symbol": f"SYM{i}"},
            "side": "BUY",
            "quantity": 1,
            "orderType": "MKT",
        }
        for i in range(10)
    ]
    text = format_trade_intent_approval("appr-4", "intent-1", "unit test", orders)

    assert "`SYM7`" in text
    assert "`SYM8`" not in text
    assert r"\+2 more orders" in text
