"""Order impact assessment — computed purely from existing account / market data.

No additional IBKR API calls are made; all inputs come from
ibkr_get_account_summary, ibkr_get_positions, and ibkr_get_quote.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def assess_order_impact(
    order_data: Dict[str, Any],
    preview_data: Optional[Dict[str, Any]],
    account_data: Dict[str, Any],
    positions_data: List[Dict[str, Any]],
    quote_data: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute portfolio-level impact of a proposed order.

    Args:
        order_data:     OrderSpec serialised as dict.
        preview_data:   OrderPreview serialised as dict (may be None).
        account_data:   AccountSummary serialised as dict.
        positions_data: List of Position dicts.
        quote_data:     Quote dict for the instrument (may be None).

    Returns:
        Dict with impact fields ready to be serialised into OrderImpactResponse.
    """
    symbol: str = order_data.get("instrument", {}).get("symbol", "UNKNOWN")
    side: str = order_data.get("side", "BUY")
    quantity: float = float(order_data.get("quantity", 0))
    multiplier: float = float(order_data.get("instrument", {}).get("multiplier") or 1)

    net_liq: float = float(account_data.get("netLiquidation", 0))
    buying_power: float = float(account_data.get("buyingPower", 0))
    maintenance_margin: float = float(account_data.get("maintenanceMargin", 0))

    # Estimate execution price
    est_price: Optional[float] = None
    if preview_data:
        est_price = preview_data.get("estimatedPrice")
    if est_price is None and quote_data:
        mid = _mid(quote_data)
        est_price = mid
    if est_price is None:
        limit_price = order_data.get("limitPrice")
        est_price = float(limit_price) if limit_price else None

    # Notional value
    estimated_notional: Optional[float] = None
    if est_price is not None:
        estimated_notional = quantity * est_price * multiplier
    elif preview_data:
        estimated_notional = preview_data.get("estimatedNotional")

    # Existing position in this symbol
    existing_qty: float = 0.0
    existing_value: float = 0.0
    for pos in positions_data:
        if pos.get("symbol") == symbol:
            existing_qty = float(pos.get("quantity", 0))
            existing_value = abs(float(pos.get("marketValue", 0)))
            break

    # Signed quantity change
    qty_delta = quantity if side == "BUY" else -quantity
    new_qty = existing_qty + qty_delta

    # Concentration metrics (% of net liquidation)
    concentration_before: Optional[float] = None
    concentration_after: Optional[float] = None
    if net_liq > 0:
        concentration_before = (existing_value / net_liq) * 100
        new_value = abs(new_qty) * (est_price or 0) * multiplier
        concentration_after = (new_value / net_liq) * 100

    # Buying power usage
    buying_power_used_pct: Optional[float] = None
    if buying_power > 0 and estimated_notional is not None:
        buying_power_used_pct = (estimated_notional / buying_power) * 100

    # Margin utilisation
    margin_utilisation_pct: Optional[float] = None
    margin_change: Optional[float] = None
    if net_liq > 0:
        margin_utilisation_pct = (maintenance_margin / net_liq) * 100
    if preview_data:
        margin_change = preview_data.get("estimatedMaintenanceMarginChange")

    # Max loss estimate (conservative)
    max_loss_estimate: Optional[float] = None
    sec_type = order_data.get("instrument", {}).get("securityType", "")
    if sec_type in {"OPT", "FUT"} and estimated_notional is not None and side == "BUY":
        # For long options/futures: max loss = premium paid (notional)
        max_loss_estimate = estimated_notional
    elif estimated_notional is not None and side == "BUY":
        # For equities: worst case is full notional (stock goes to zero)
        max_loss_estimate = estimated_notional

    # Warnings
    warnings: List[str] = []
    if concentration_after is not None and concentration_after > 25:
        warnings.append(
            f"Position concentration would be {concentration_after:.1f}% of portfolio"
        )
    if buying_power_used_pct is not None and buying_power_used_pct > 50:
        warnings.append(
            f"Order uses {buying_power_used_pct:.1f}% of available buying power"
        )
    if margin_utilisation_pct is not None and margin_utilisation_pct > 80:
        warnings.append(
            f"Margin utilisation is {margin_utilisation_pct:.1f}% — near margin call"
        )
    if preview_data:
        for w in preview_data.get("warnings", []):
            if w not in warnings:
                warnings.append(w)

    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "estimatedPrice": est_price,
        "estimatedNotional": estimated_notional,
        "estimatedCommission": preview_data.get("estimatedCommission") if preview_data else None,
        "existingPositionQty": existing_qty,
        "newPositionQty": new_qty,
        "concentrationBefore": _round2(concentration_before),
        "concentrationAfter": _round2(concentration_after),
        "buyingPowerUsedPct": _round2(buying_power_used_pct),
        "marginUtilisationPct": _round2(margin_utilisation_pct),
        "estimatedMarginChange": margin_change,
        "maxLossEstimate": _round2(max_loss_estimate),
        "warnings": warnings,
    }


def _mid(quote_data: Dict[str, Any]) -> Optional[float]:
    bid = quote_data.get("bid", 0.0)
    ask = quote_data.get("ask", 0.0)
    if bid and ask:
        return (float(bid) + float(ask)) / 2
    last = quote_data.get("last", 0.0)
    return float(last) if last else None


def _round2(value: Optional[float]) -> Optional[float]:
    return round(value, 2) if value is not None else None
