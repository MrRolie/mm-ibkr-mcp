"""Portfolio-level risk metrics — computed from account summary and positions.

No additional IBKR API calls required.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def compute_portfolio_risk(
    account_data: Dict[str, Any],
    positions_data: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute portfolio-wide risk metrics.

    Args:
        account_data:   AccountSummary serialised as dict.
        positions_data: List of Position dicts.

    Returns:
        Dict ready to be serialised into PortfolioRiskResponse.
    """
    net_liq: float = float(account_data.get("netLiquidation", 0))
    buying_power: float = float(account_data.get("buyingPower", 0))
    maintenance_margin: float = float(account_data.get("maintenanceMargin", 0))
    initial_margin: float = float(account_data.get("initialMargin", 0))

    total_unrealised: float = sum(float(p.get("unrealizedPnl", 0)) for p in positions_data)
    total_realised: float = sum(float(p.get("realizedPnl", 0)) for p in positions_data)

    # Concentration by symbol (% of net liquidation by absolute market value)
    concentration: Dict[str, float] = {}
    if net_liq > 0:
        for pos in positions_data:
            sym = pos.get("symbol", "?")
            mv = abs(float(pos.get("marketValue", 0)))
            concentration[sym] = round((mv / net_liq) * 100, 2)

    # Largest single position
    largest_symbol: Optional[str] = None
    largest_pct: Optional[float] = None
    if concentration:
        largest_symbol, largest_pct = max(concentration.items(), key=lambda kv: kv[1])

    # Margin utilisation
    margin_utilisation_pct: Optional[float] = None
    if net_liq > 0:
        margin_utilisation_pct = round((maintenance_margin / net_liq) * 100, 2)

    # Buying power utilisation
    buying_power_used_pct: Optional[float] = None
    cash = float(account_data.get("cash", 0))
    if cash > 0:
        buying_power_used_pct = round(((cash - buying_power) / cash) * 100, 2)

    # Risk level classification
    risk_level = _classify_risk(margin_utilisation_pct, largest_pct)

    # Warnings
    warnings: List[str] = []
    if margin_utilisation_pct is not None and margin_utilisation_pct > 80:
        warnings.append(f"Margin utilisation is {margin_utilisation_pct:.1f}% — margin call risk")
    if largest_pct is not None and largest_pct > 30:
        warnings.append(
            f"Largest position ({largest_symbol}) is {largest_pct:.1f}% of portfolio"
        )
    if net_liq > 0 and total_unrealised < 0 and abs(total_unrealised) / net_liq > 0.05:
        loss_pct = abs(total_unrealised) / net_liq * 100
        warnings.append(f"Unrealised loss is {loss_pct:.1f}% of net liquidation")

    return {
        "netLiquidation": net_liq,
        "buyingPower": buying_power,
        "maintenanceMargin": maintenance_margin,
        "initialMargin": initial_margin,
        "totalUnrealisedPnl": round(total_unrealised, 2),
        "totalRealisedPnl": round(total_realised, 2),
        "positionCount": len(positions_data),
        "concentrationBySymbol": concentration,
        "largestPositionSymbol": largest_symbol,
        "largestPositionPct": largest_pct,
        "marginUtilisationPct": margin_utilisation_pct,
        "buyingPowerUsedPct": buying_power_used_pct,
        "riskLevel": risk_level,
        "warnings": warnings,
    }


def _classify_risk(
    margin_utilisation_pct: Optional[float],
    largest_pct: Optional[float],
) -> str:
    """Return a simple risk level: low / medium / high / critical."""
    score = 0
    if margin_utilisation_pct is not None:
        if margin_utilisation_pct > 80:
            score += 3
        elif margin_utilisation_pct > 60:
            score += 2
        elif margin_utilisation_pct > 40:
            score += 1
    if largest_pct is not None:
        if largest_pct > 40:
            score += 2
        elif largest_pct > 25:
            score += 1

    if score >= 4:
        return "critical"
    if score >= 3:
        return "high"
    if score >= 1:
        return "medium"
    return "low"
