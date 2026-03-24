"""Pydantic models matching IBKR Core API Schema."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SymbolSpec(BaseModel):
    """Logical description of an instrument to be resolved into an IBKR contract."""

    symbol: str = Field(..., description="Base symbol or ticker, e.g. 'AAPL', 'MES', 'SPX'.")
    securityType: str = Field(..., description="IBKR security type code.")
    exchange: Optional[str] = Field(
        None, description="Preferred exchange or routing, e.g. 'SMART', 'GLOBEX'."
    )
    currency: Optional[str] = Field(None, description="Currency code, e.g. 'USD'.")
    expiry: Optional[str] = Field(
        None, description="Contract expiry in YYYY-MM-DD format for derivatives."
    )
    strike: Optional[float] = Field(None, ge=0, description="Strike price for options.")
    right: Optional[str] = Field(None, description="Option right: Call or Put.")
    multiplier: Optional[str] = Field(
        None, description="Contract multiplier as string (IBKR-style)."
    )

    @field_validator("securityType")
    @classmethod
    def validate_security_type(cls, v: str) -> str:
        """Validate security type against allowed enum."""
        v = v.upper().strip()
        allowed = {"STK", "ETF", "FUT", "OPT", "IND", "CASH", "CFD", "BOND", "FUND", "CRYPTO"}
        if v not in allowed:
            raise ValueError(f"securityType must be one of {allowed}, got {v}")
        return v

    @field_validator("right")
    @classmethod
    def validate_right(cls, v: Optional[str]) -> Optional[str]:
        """Validate option right."""
        if v is None:
            return None

        normalized = v.upper().strip()
        if normalized in {"CALL", "CALLS"}:
            return "C"
        if normalized in {"PUT", "PUTS"}:
            return "P"
        if normalized not in {"C", "P"}:
            raise ValueError(f"right must be 'C' or 'P', got {v}")
        return normalized

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"symbol": "AAPL", "securityType": "STK"},
                {"symbol": "MES", "securityType": "FUT"},
            ]
        }
    )


class Quote(BaseModel):
    """Snapshot of current market data for a single instrument."""

    symbol: str = Field(..., description="Logical symbol identifier used in the request.")
    conId: int = Field(..., description="IBKR contract identifier.")
    bid: float = Field(0.0, ge=0, description="Best bid price.")
    ask: float = Field(0.0, ge=0, description="Best ask price.")
    last: float = Field(0.0, ge=0, description="Last traded price.")
    bidSize: float = Field(0.0, ge=0, description="Bid size in contracts or shares.")
    askSize: float = Field(0.0, ge=0, description="Ask size in contracts or shares.")
    lastSize: float = Field(0.0, ge=0, description="Last traded size.")
    volume: float = Field(0.0, ge=0, description="Session volume.")
    timestamp: datetime = Field(..., description="Timestamp of the quote in ISO 8601 format.")
    source: str = Field(..., description="Source or feed identifier, e.g. 'IBKR_REALTIME'.")


class ResolvedContract(BaseModel):
    """Fully resolved IBKR contract details."""

    symbol: str = Field(..., description="Resolved symbol.")
    securityType: str = Field(..., description="IBKR security type code.")
    conId: int = Field(..., description="IBKR contract identifier.")
    exchange: Optional[str] = Field(None, description="Resolved exchange.")
    primaryExchange: Optional[str] = Field(None, description="Primary exchange when available.")
    currency: Optional[str] = Field(None, description="Resolved currency.")
    localSymbol: Optional[str] = Field(None, description="IBKR local symbol.")
    tradingClass: Optional[str] = Field(None, description="IBKR trading class.")
    expiry: Optional[str] = Field(None, description="Expiry date in YYYY-MM-DD format.")
    strike: Optional[float] = Field(None, ge=0, description="Strike price for options.")
    right: Optional[str] = Field(None, description="Option right: C or P.")
    multiplier: Optional[str] = Field(None, description="Contract multiplier.")


class OptionGreeks(BaseModel):
    """Single set of option greeks from IBKR."""

    impliedVol: Optional[float] = Field(None, description="Implied volatility.")
    delta: Optional[float] = Field(None, description="Option delta.")
    optPrice: Optional[float] = Field(None, description="Theoretical option price.")
    pvDividend: Optional[float] = Field(None, description="Present value of dividends.")
    gamma: Optional[float] = Field(None, description="Option gamma.")
    vega: Optional[float] = Field(None, description="Option vega.")
    theta: Optional[float] = Field(None, description="Option theta.")
    undPrice: Optional[float] = Field(None, description="Underlying price from the option model.")


class OptionGreeksSet(BaseModel):
    """Grouped option greeks from the various IBKR calculation buckets."""

    model: Optional[OptionGreeks] = Field(None, description="Model greeks.")
    bid: Optional[OptionGreeks] = Field(None, description="Bid greeks.")
    ask: Optional[OptionGreeks] = Field(None, description="Ask greeks.")
    last: Optional[OptionGreeks] = Field(None, description="Last-trade greeks.")


class Bar(BaseModel):
    """Single OHLCV bar of historical or intraday data."""

    symbol: str = Field(..., description="Logical symbol identifier.")
    time: datetime = Field(..., description="Bar timestamp in ISO 8601 format.")
    open: float = Field(..., ge=0, description="Open price.")
    high: float = Field(..., ge=0, description="High price.")
    low: float = Field(..., ge=0, description="Low price.")
    close: float = Field(..., ge=0, description="Close price.")
    volume: float = Field(..., ge=0, description="Traded volume in this bar.")
    barSize: str = Field(
        ..., description="Bar size as human string, e.g. '1 min', '5 mins', '1 day'."
    )
    source: str = Field(..., description="Data source, e.g. 'IBKR_HISTORICAL'.")


class OptionContractCandidate(BaseModel):
    """Qualified single-leg option contract candidate."""

    symbol: str = Field(..., description="Underlying symbol.")
    conId: int = Field(..., description="IBKR contract identifier.")
    exchange: str = Field(..., description="Resolved exchange.")
    currency: str = Field(..., description="Trading currency.")
    expiry: str = Field(..., description="Expiry in YYYY-MM-DD format.")
    strike: float = Field(..., ge=0, description="Strike price.")
    right: str = Field(..., description="Option right: C or P.")
    multiplier: Optional[str] = Field(None, description="Contract multiplier.")
    localSymbol: Optional[str] = Field(None, description="IBKR local symbol.")
    tradingClass: Optional[str] = Field(None, description="IBKR trading class.")


class OptionChainResponse(BaseModel):
    """Filtered option chain for an underlying instrument."""

    underlying: ResolvedContract = Field(..., description="Resolved underlying contract.")
    underlyingPrice: Optional[float] = Field(
        None, description="Underlying last price when snapshot data is available."
    )
    exchange: Optional[str] = Field(None, description="Primary option exchange used.")
    multiplier: Optional[str] = Field(None, description="Option contract multiplier.")
    expirations: List[str] = Field(default_factory=list, description="Available expirations.")
    strikes: List[float] = Field(default_factory=list, description="Available strikes.")
    candidates: List[OptionContractCandidate] = Field(
        default_factory=list,
        description="Qualified contracts matching the requested filters.",
    )
    candidateCount: int = Field(..., ge=0, description="Number of candidates returned.")


class OptionSnapshotResponse(BaseModel):
    """Option quote plus greeks and volatility fields."""

    contract: ResolvedContract = Field(..., description="Resolved option contract.")
    quote: Quote = Field(..., description="Option quote snapshot.")
    underlyingLastPrice: Optional[float] = Field(
        None, description="Underlying last price when available."
    )
    impliedVolatility: Optional[float] = Field(
        None, description="IBKR implied volatility field when available."
    )
    histVolatility: Optional[float] = Field(
        None, description="IBKR historical volatility field when available."
    )
    rtHistVolatility: Optional[float] = Field(
        None, description="IBKR real-time historical volatility field when available."
    )
    greeks: OptionGreeksSet = Field(
        default_factory=OptionGreeksSet,
        description="Grouped greek snapshots from IBKR.",
    )


class AccountSummary(BaseModel):
    """High-level snapshot of account status."""

    accountId: str = Field(..., description="IBKR account identifier.")
    currency: str = Field(..., description="Base reporting currency, e.g. 'USD'.")
    netLiquidation: float = Field(..., ge=0, description="Net liquidation value in base currency.")
    cash: float = Field(0.0, ge=0, description="Cash balance in base currency.")
    buyingPower: float = Field(0.0, ge=0, description="Available buying power in base currency.")
    marginExcess: float = Field(0.0, description="Margin excess or deficit (can be negative).")
    maintenanceMargin: float = Field(
        0.0, ge=0, description="Current maintenance margin requirement."
    )
    initialMargin: float = Field(0.0, ge=0, description="Current initial margin requirement.")
    timestamp: datetime = Field(..., description="Timestamp when the snapshot was taken, ISO 8601.")


class Position(BaseModel):
    """Single open or recently closed position in the portfolio."""

    accountId: str = Field(..., description="IBKR account identifier.")
    symbol: str = Field(..., description="Logical symbol, e.g. 'MES', 'AAPL'.")
    conId: int = Field(..., description="IBKR contract identifier.")
    assetClass: str = Field(..., description="Instrument class.")
    currency: str = Field(..., description="Trading currency, e.g. 'USD'.")
    quantity: float = Field(
        ..., description="Position size (positive for long, negative for short)."
    )
    avgPrice: float = Field(..., ge=0, description="Average cost price.")
    marketPrice: float = Field(..., ge=0, description="Current market price.")
    marketValue: float = Field(
        ..., description="Current market value (can be negative for short positions)."
    )
    unrealizedPnl: float = Field(..., description="Unrealized P&L for this position.")
    realizedPnl: float = Field(..., description="Cumulative realized P&L for this position.")

    @field_validator("assetClass")
    @classmethod
    def validate_asset_class(cls, v: str) -> str:
        """Validate asset class against allowed enum."""
        allowed = {"STK", "ETF", "FUT", "OPT", "FX", "CFD", "IND", "BOND", "FUND", "CRYPTO"}
        if v not in allowed:
            raise ValueError(f"assetClass must be one of {allowed}, got {v}")
        return v


class PnlDetail(BaseModel):
    """Detailed P&L breakdown for a symbol or contract."""

    symbol: str = Field(..., description="Symbol associated with this P&L bucket.")
    conId: Optional[int] = Field(None, description="IBKR contract identifier.")
    currency: str = Field(..., description="Currency for these P&L values.")
    realized: float = Field(..., description="Total realized P&L.")
    unrealized: float = Field(..., description="Total unrealized P&L.")
    realizedToday: Optional[float] = Field(
        None, description="Realized P&L for the current session/timeframe."
    )
    unrealizedToday: Optional[float] = Field(
        None, description="Unrealized P&L change for the current session/timeframe."
    )
    basis: Optional[float] = Field(None, description="Cost basis if available.")


class AccountPnl(BaseModel):
    """Aggregated account-level P&L."""

    accountId: str = Field(..., description="IBKR account identifier.")
    currency: str = Field(..., description="Reporting currency.")
    timeframe: str = Field(
        ..., description="Requested timeframe, e.g. 'INTRADAY', '1D', 'MTD', 'YTD'."
    )
    realized: float = Field(..., description="Total realized P&L in this timeframe.")
    unrealized: float = Field(..., description="Current unrealized P&L.")
    bySymbol: Dict[str, PnlDetail] = Field(
        default_factory=dict, description="Map of symbol → PnlDetail."
    )
    timestamp: datetime = Field(..., description="Timestamp of this P&L snapshot, ISO 8601.")


class OrderSpec(BaseModel):
    """Client-side specification of an order to be placed.

    Supports basic orders (MKT, LMT, STP, STP_LMT) and advanced orders:
    - TRAIL: Trailing stop (requires trailingAmount OR trailingPercent)
    - TRAIL_LIMIT: Trailing stop-limit (requires trailing params + limitPrice offset)
    - BRACKET: Entry + take profit + stop loss (requires takeProfitPrice + stopLossPrice)
    - MOC: Market-on-close
    - OPG: Market-on-open (opening auction)
    """

    accountId: Optional[str] = Field(None, description="Target account for the order.")
    strategyId: Optional[str] = Field(
        None, description="Strategy identifier for virtual subaccount tracking."
    )
    virtualSubaccountId: Optional[str] = Field(
        None, description="Virtual subaccount identifier for allocation tracking."
    )
    instrument: SymbolSpec = Field(..., description="Instrument to trade.")
    side: str = Field(..., description="Order side.")
    quantity: float = Field(
        ..., gt=0, description="Absolute quantity (units, shares, contracts). Must be positive."
    )
    orderType: str = Field(..., description="Order type.")
    limitPrice: Optional[float] = Field(
        None, ge=0, description="Limit price, required for LMT and STP_LMT."
    )
    stopPrice: Optional[float] = Field(
        None, ge=0, description="Stop trigger price, required for STP and STP_LMT."
    )
    tif: str = Field("DAY", description="Time-in-force.")
    outsideRth: bool = Field(False, description="Allow execution outside regular trading hours.")
    clientOrderId: Optional[str] = Field(None, description="Client-generated idempotency key.")
    transmit: bool = Field(
        True, description="Whether to transmit the order immediately once accepted by IBKR."
    )

    # Trailing stop parameters (Phase 4.5)
    trailingAmount: Optional[float] = Field(
        None, gt=0, description="Trailing amount in price units (for TRAIL/TRAIL_LIMIT)."
    )
    trailingPercent: Optional[float] = Field(
        None, gt=0, le=100, description="Trailing percentage (for TRAIL/TRAIL_LIMIT)."
    )
    trailStopPrice: Optional[float] = Field(
        None, ge=0, description="Initial stop price for trailing orders."
    )

    # Bracket order parameters (Phase 4.5)
    takeProfitPrice: Optional[float] = Field(
        None, ge=0, description="Take profit limit price for bracket orders."
    )
    stopLossPrice: Optional[float] = Field(
        None, ge=0, description="Stop loss trigger price for bracket orders."
    )
    stopLossLimitPrice: Optional[float] = Field(
        None, ge=0, description="Stop loss limit price (for stop-limit child in bracket)."
    )
    bracketTransmit: bool = Field(
        True, description="Whether to transmit all bracket legs (final child transmits)."
    )

    # OCA (One-Cancels-All) parameters (Phase 4.5)
    ocaGroup: Optional[str] = Field(None, description="OCA group name for linked orders.")
    ocaType: Optional[int] = Field(
        None,
        ge=1,
        le=3,
        description="OCA type: 1=cancel with block, 2=reduce with block, 3=reduce without block.",
    )

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str) -> str:
        """Validate order side."""
        v = v.upper().strip()
        if v not in {"BUY", "SELL"}:
            raise ValueError(f"side must be 'BUY' or 'SELL', got {v}")
        return v

    @field_validator("orderType")
    @classmethod
    def validate_order_type(cls, v: str) -> str:
        """Validate order type."""
        v = v.upper().strip()
        allowed = {"MKT", "LMT", "STP", "STP_LMT", "TRAIL", "TRAIL_LIMIT", "BRACKET", "MOC", "OPG"}
        if v not in allowed:
            raise ValueError(f"orderType must be one of {allowed}, got {v}")
        return v

    @field_validator("tif")
    @classmethod
    def validate_tif(cls, v: str) -> str:
        """Validate time-in-force."""
        v = v.upper().strip()
        # MOC uses orderType with DAY TIF; OPG uses OPG TIF
        allowed = {"DAY", "GTC", "IOC", "FOK", "OPG"}
        if v not in allowed:
            raise ValueError(f"tif must be one of {allowed}, got {v}")
        return v


class OrderLeg(BaseModel):
    """A single leg of a multi-leg order (bracket, OCA, etc.)."""

    role: str = Field(
        ..., description="Role of this leg: 'entry', 'take_profit', 'stop_loss', or 'child'."
    )
    orderType: str = Field(..., description="Order type for this leg (MKT, LMT, STP, etc.).")
    side: str = Field(..., description="Order side (BUY or SELL).")
    quantity: float = Field(..., gt=0, description="Quantity for this leg.")
    limitPrice: Optional[float] = Field(None, description="Limit price if applicable.")
    stopPrice: Optional[float] = Field(None, description="Stop price if applicable.")
    trailingAmount: Optional[float] = Field(None, description="Trailing amount if applicable.")
    trailingPercent: Optional[float] = Field(None, description="Trailing percent if applicable.")
    tif: str = Field("DAY", description="Time-in-force for this leg.")
    estimatedPrice: Optional[float] = Field(None, description="Estimated execution price.")
    estimatedNotional: Optional[float] = Field(None, description="Estimated notional for this leg.")


class OrderPreview(BaseModel):
    """Estimated impact and characteristics of an order, without sending it."""

    orderSpec: OrderSpec = Field(..., description="The original order specification.")
    estimatedPrice: Optional[float] = Field(None, ge=0, description="Estimated execution price.")
    estimatedNotional: Optional[float] = Field(
        None, ge=0, description="Estimated notional value in account currency."
    )
    estimatedCommission: Optional[float] = Field(
        None, ge=0, description="Estimated commission and fees."
    )
    estimatedInitialMarginChange: Optional[float] = Field(
        None, description="Estimated change in initial margin requirement."
    )
    estimatedMaintenanceMarginChange: Optional[float] = Field(
        None, description="Estimated change in maintenance margin requirement."
    )
    warnings: List[str] = Field(default_factory=list, description="Human-readable warnings.")

    # Multi-leg support (Phase 4.5)
    legs: List[OrderLeg] = Field(
        default_factory=list, description="Order legs for bracket/OCA orders."
    )
    totalNotional: Optional[float] = Field(
        None, description="Total worst-case notional across all legs."
    )


class OrderStatus(BaseModel):
    """Current status of an order at IBKR."""

    orderId: str = Field(..., description="Broker order identifier.")
    clientOrderId: Optional[str] = Field(None, description="Client-provided id, if any.")
    status: str = Field(..., description="Order lifecycle status.")
    filledQuantity: float = Field(..., ge=0, description="Total filled quantity.")
    remainingQuantity: float = Field(..., ge=0, description="Remaining open quantity.")
    avgFillPrice: float = Field(..., ge=0, description="Average fill price across fills.")
    lastUpdate: datetime = Field(..., description="Timestamp of last status update, ISO 8601.")
    warnings: List[str] = Field(
        default_factory=list, description="Any broker or system warnings tied to this order."
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Validate order status."""
        allowed = {
            "PENDING_SUBMIT",
            "PENDING_CANCEL",
            "SUBMITTED",
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCELLED",
            "REJECTED",
            "EXPIRED",
        }
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}, got {v}")
        return v


class OrderResult(BaseModel):
    """Result of an attempt to place an order."""

    orderId: Optional[str] = Field(
        None, description="Broker order identifier, if accepted (primary/entry order)."
    )
    clientOrderId: Optional[str] = Field(None, description="Client-provided id, if any.")
    status: str = Field(..., description="High-level result status.")
    orderStatus: Optional[OrderStatus] = Field(
        None, description="Current order status if available."
    )
    errors: List[str] = Field(
        default_factory=list, description="Errors returned from broker or validation."
    )

    # Multi-leg support (Phase 4.5)
    orderIds: List[str] = Field(
        default_factory=list, description="All order IDs for multi-leg orders."
    )
    orderRoles: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of role -> order_id (entry, take_profit, stop_loss).",
    )

    @field_validator("status")
    @classmethod
    def validate_result_status(cls, v: str) -> str:
        """Validate result status."""
        if v not in {"ACCEPTED", "REJECTED", "SIMULATED"}:
            raise ValueError(f"status must be 'ACCEPTED', 'REJECTED', or 'SIMULATED', got {v}")
        return v


class CancelResult(BaseModel):
    """Result of a cancel order request."""

    orderId: str = Field(..., description="Order identifier that was requested to be cancelled.")
    status: str = Field(..., description="Outcome of the cancel request.")
    message: Optional[str] = Field(None, description="Human-readable message with more detail.")

    @field_validator("status")
    @classmethod
    def validate_cancel_status(cls, v: str) -> str:
        """Validate cancel status."""
        if v not in {"CANCELLED", "ALREADY_FILLED", "NOT_FOUND", "REJECTED"}:
            raise ValueError(
                f"status must be one of {{CANCELLED, ALREADY_FILLED, NOT_FOUND, REJECTED}}, got {v}"
            )
        return v
