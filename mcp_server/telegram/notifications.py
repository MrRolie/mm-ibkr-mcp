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


def format_trade_intent_approval(
    approval_id: str,
    intent_id: str,
    reason: str,
    orders_data: List[Dict[str, Any]],
) -> str:
    """Format a basket-intent approval request."""
    lines: List[str] = [
        "🔔 *TRADE INTENT APPROVAL REQUEST*",
        "",
        f"*Intent ID:* `{intent_id}`",
        f"*Order Count:* {len(orders_data)}",
        f"*Reason:* {_escape(reason)}",
        f"*Approval ID:* `{approval_id}`",
        "",
        "*Orders:*",
    ]

    for order in orders_data[:8]:
        instrument = order.get("instrument", {})
        symbol = instrument.get("symbol", "?")
        side = order.get("side", "?")
        qty = order.get("quantity", "?")
        order_type = order.get("orderType", "MKT")
        limit_price = order.get("limitPrice")
        client_order_id = order.get("clientOrderId")
        price_str = f" @ ${limit_price:,.4f}" if limit_price else ""
        line = f"• `{symbol}` {side} {qty} × {order_type}{price_str}"
        if client_order_id:
            line += f" \\(`{client_order_id}`\\)"
        lines.append(line)

    if len(orders_data) > 8:
        lines.append(f"• \\+{len(orders_data) - 8} more orders")

    lines += [
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


def format_environment_change(approval_id: str, target_env: str, reason: str, port: int) -> str:
    """Format an environment change approval request."""
    return "\n".join([
        "🔄 *ENVIRONMENT CHANGE REQUEST*",
        "",
        f"An agent is requesting to switch the IBKR connection to *{target_env.upper()}* \\(Port {port}\\)\\.",
        "",
        f"*Reason:* {_escape(reason)}",
        f"*Approval ID:* `{approval_id}`",
        "",
        "⚠️ *Safety lock will be applied: orders will be disabled and dry-run enabled\\.*",
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
