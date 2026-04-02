"""Telegram message formatters for trade notifications and approval requests."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_trade_approval(
    approval_id: str,
    order_data: Dict[str, Any],
    preview_data: Optional[Dict[str, Any]],
    reason: str,
) -> str:
    """Format an inline-keyboard approval request for a proposed order."""
    instrument = order_data.get("instrument", {})
    symbol = instrument.get("symbol", "?")
    sec_type = instrument.get("securityType", "")
    side = order_data.get("side", "?")
    qty = order_data.get("quantity", "?")
    order_type = order_data.get("orderType", "MKT")
    limit_price = order_data.get("limitPrice")
    client_order_id = order_data.get("clientOrderId", "")

    price_str = f" @ ${limit_price:,.4f}" if limit_price else ""
    lines: List[str] = [
        "🔔 *TRADE APPROVAL REQUEST*",
        "",
        f"*Instrument:* `{symbol}` \\({sec_type}\\)",
        f"*Action:* {side} {qty} × {order_type}{price_str}",
    ]

    if client_order_id:
        lines.append(f"*Order ID:* `{client_order_id}`")

    if preview_data:
        notional = preview_data.get("estimatedNotional")
        commission = preview_data.get("estimatedCommission")
        warnings: List[str] = preview_data.get("warnings", [])

        if notional:
            lines.append(f"*Est\\. Notional:* ${notional:,.2f}")
        if commission:
            lines.append(f"*Est\\. Commission:* ${commission:.2f}")
        if warnings:
            lines.append("")
            lines.append("⚠️ *Warnings:*")
            for w in warnings[:3]:
                lines.append(f"  • {w}")

    lines += [
        "",
        f"*Reason:* {_escape(reason)}",
        f"*Approval ID:* `{approval_id}`",
        "",
        "Tap a button to respond:",
    ]
    return "\n".join(lines)


def format_live_trading_unlock(approval_id: str, reason: str) -> str:
    """Format a live-trading-unlock approval request."""
    return "\n".join([
        "🔴 *LIVE TRADING UNLOCK REQUEST*",
        "",
        "An agent is requesting to enable *LIVE* \\(real\\-money\\) trading\\.",
        "",
        f"*Reason:* {_escape(reason)}",
        f"*Approval ID:* `{approval_id}`",
        "",
        "⚠️ *Approving will allow REAL orders with REAL funds\\.*",
        "",
        "Tap a button to respond:",
    ])


def format_notification(title: str, body: str, level: str = "info") -> str:
    """Format a plain informational notification (no buttons)."""
    icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "success": "✅"}.get(level, "ℹ️")
    return "\n".join([
        f"{icon} *{_escape(title)}*",
        "",
        _escape(body),
    ])


def format_emergency_stop(orders_cancelled: int, account_id: str) -> str:
    """Format an emergency-stop notification."""
    return "\n".join([
        "🛑 *EMERGENCY STOP EXECUTED*",
        "",
        f"*Account:* `{account_id}`",
        f"*Orders cancelled:* {orders_cancelled}",
        "*Trading:* ❌ Orders disabled",
        "",
        "The gateway is halted\\. Re\\-enable trading manually after reviewing positions\\.",
    ])


def _escape(text: str) -> str:
    """Escape MarkdownV2 special characters in user-supplied strings."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))
