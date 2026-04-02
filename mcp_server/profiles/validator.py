"""Validate a proposed order against an agent trading profile.

Returns a list of violation strings (empty = no violations).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def validate_order_against_profile(
    order_data: Dict[str, Any],
    profile: Dict[str, Any],
    account_data: Optional[Dict[str, Any]] = None,
    positions_data: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Check an OrderSpec dict against a profile dict.

    Returns a list of human-readable violation strings.
    An empty list means the order is within profile constraints.
    """
    violations: List[str] = []
    instrument = order_data.get("instrument", {})
    symbol: str = instrument.get("symbol", "?")
    sec_type: str = instrument.get("securityType", "STK")
    order_type: str = order_data.get("orderType", "MKT")
    side: str = order_data.get("side", "BUY")
    quantity: float = float(order_data.get("quantity", 0))
    limit_price: Optional[float] = order_data.get("limitPrice")

    # -- Security type check --
    allowed_sec_types: Optional[List[str]] = profile.get("allowed_security_types")
    if allowed_sec_types and sec_type not in allowed_sec_types:
        violations.append(
            f"Security type '{sec_type}' is not in allowed types: {allowed_sec_types}"
        )

    # -- Order type check --
    allowed_order_types: Optional[List[str]] = profile.get("allowed_order_types")
    if allowed_order_types and order_type not in allowed_order_types:
        violations.append(
            f"Order type '{order_type}' is not in allowed types: {allowed_order_types}"
        )

    # -- Symbol allowlist --
    allowed_symbols: Optional[List[str]] = profile.get("allowed_symbols")
    if allowed_symbols and symbol not in allowed_symbols:
        violations.append(f"Symbol '{symbol}' is not in the allowlist")

    # -- Symbol blocklist --
    blocked_symbols: List[str] = profile.get("blocked_symbols", [])
    if symbol in blocked_symbols:
        violations.append(f"Symbol '{symbol}' is blocked by this profile")

    # -- Options check --
    if not profile.get("allow_options", True) and sec_type == "OPT":
        violations.append("Options trading is not allowed by this profile")

    # -- Short selling check --
    if not profile.get("allow_short_selling", True) and side == "SELL":
        # Only flag if no existing long position (rough heuristic without position data)
        existing_qty = _existing_qty(symbol, positions_data)
        if existing_qty is not None and existing_qty <= 0:
            violations.append("Short selling is not allowed by this profile")

    # -- Quantity check --
    max_qty: Optional[float] = profile.get("max_order_quantity")
    if max_qty is not None and quantity > max_qty:
        violations.append(
            f"Order quantity {quantity} exceeds profile maximum {max_qty}"
        )

    # -- Notional check (requires price estimate) --
    max_notional: Optional[float] = profile.get("max_position_notional")
    if max_notional is not None and limit_price is not None:
        multiplier = float(instrument.get("multiplier") or 1)
        order_notional = quantity * limit_price * multiplier
        if order_notional > max_notional:
            violations.append(
                f"Estimated notional ${order_notional:,.2f} exceeds profile maximum "
                f"${max_notional:,.2f}"
            )

    # -- Position concentration check (requires account data) --
    max_concentration: Optional[float] = profile.get("max_position_size_pct")
    if (
        max_concentration is not None
        and account_data is not None
        and limit_price is not None
    ):
        net_liq = float(account_data.get("netLiquidation", 0))
        if net_liq > 0:
            multiplier = float(instrument.get("multiplier") or 1)
            existing_qty = _existing_qty(symbol, positions_data) or 0.0
            total_qty = existing_qty + (quantity if side == "BUY" else -quantity)
            new_value = abs(total_qty) * limit_price * multiplier
            concentration_pct = (new_value / net_liq) * 100
            if concentration_pct > max_concentration:
                violations.append(
                    f"Post-trade concentration {concentration_pct:.1f}% exceeds profile "
                    f"maximum {max_concentration:.1f}% of net liquidation"
                )

    return violations


def _existing_qty(symbol: str, positions_data: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    if not positions_data:
        return None
    for pos in positions_data:
        if pos.get("symbol") == symbol:
            return float(pos.get("quantity", 0))
    return 0.0
