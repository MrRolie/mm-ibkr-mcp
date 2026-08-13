"""Typed request/response models for the MCP surface."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from ibkr_core.models import (
    Bar,
    OrderPreview,
    OrderSpec,
    OrderStatus,
    Position,
)


class HealthResponse(BaseModel):
    """High-level gateway health status."""

    status: str = Field(..., description="Overall health: ok or degraded.")
    ibkrConnected: bool = Field(..., description="Whether the gateway is connected.")
    serverTime: Optional[str] = Field(None, description="IBKR server time when available.")
    tradingMode: str = Field(..., description="Current trading mode from control.json.")
    ordersEnabled: bool = Field(..., description="Whether real order placement is enabled.")
    gatewayHost: Optional[str] = Field(None, description="Configured gateway host.")
    gatewayPort: Optional[int] = Field(None, description="Configured gateway port.")
    managedAccounts: List[str] = Field(
        default_factory=list,
        description="Managed accounts visible on the current connection.",
    )
    version: str = Field(default="0.1.0", description="Gateway package version.")


class TradingStatusResponse(BaseModel):
    """Trading control state from control.json."""

    tradingMode: str = Field(..., description="Current trading mode.")
    ordersEnabled: bool = Field(..., description="Whether orders are enabled.")
    dryRun: bool = Field(..., description="Configured dry-run flag.")
    effectiveDryRun: bool = Field(..., description="Effective dry-run status after safety rules.")
    blockReason: Optional[str] = Field(None, description="Optional operator-supplied block reason.")
    updatedAt: Optional[str] = Field(None, description="Last control update timestamp.")
    updatedBy: Optional[str] = Field(None, description="Who last updated control.json.")
    liveTradingOverrideFile: Optional[str] = Field(None, description="Live override file path.")
    overrideFileExists: Optional[bool] = Field(None, description="Whether the override file exists.")
    overrideFileMessage: Optional[str] = Field(None, description="Override-file validation detail.")
    isLiveTradingEnabled: bool = Field(..., description="Whether live trading is fully enabled.")
    validationErrors: List[str] = Field(
        default_factory=list,
        description="Validation errors for the control state.",
    )
    controlPath: str = Field(..., description="Absolute path to control.json.")


class ScheduleStatusResponse(BaseModel):
    """Current trading schedule status."""

    currentTime: str = Field(..., description="Current time in the schedule timezone.")
    timezone: str = Field(..., description="Configured schedule timezone.")
    inWindow: bool = Field(..., description="Whether the current time is inside the run window.")
    windowStart: str = Field(..., description="Configured start time.")
    windowEnd: str = Field(..., description="Configured end time.")
    activeDays: List[str] = Field(default_factory=list, description="Active weekdays.")
    nextWindowStart: Optional[str] = Field(None, description="Next scheduled window start.")
    nextWindowEnd: Optional[str] = Field(None, description="Current or next window end.")


class HistoricalBarsResponse(BaseModel):
    """Historical bar response wrapper."""

    symbol: str = Field(..., description="Symbol requested.")
    barCount: int = Field(..., ge=0, description="Number of bars returned.")
    bars: List[Bar] = Field(default_factory=list, description="Historical bars.")


class PositionsResponse(BaseModel):
    """Positions response wrapper."""

    accountId: str = Field(..., description="Account identifier.")
    positionCount: int = Field(..., ge=0, description="Number of positions.")
    positions: List[Position] = Field(default_factory=list, description="Open positions.")


class OpenOrderInfo(BaseModel):
    """Single open order snapshot."""

    orderId: str = Field(..., description="Primary order identifier.")
    clientOrderId: Optional[str] = Field(None, description="Client idempotency key.")
    symbol: str = Field(..., description="Instrument symbol.")
    side: str = Field(..., description="Order side.")
    quantity: float = Field(..., description="Order quantity.")
    orderType: str = Field(..., description="Broker order type.")
    status: str = Field(..., description="Current broker status.")
    filledQuantity: float = Field(..., description="Filled quantity.")
    remainingQuantity: float = Field(..., description="Remaining quantity.")


class OpenOrdersResponse(BaseModel):
    """Open orders response wrapper."""

    count: int = Field(..., ge=0, description="Number of open orders.")
    orders: List[OpenOrderInfo] = Field(default_factory=list, description="Open orders.")


class OrderSetStatusResponse(BaseModel):
    """Aggregated status for a set of order ids."""

    requestedOrderIds: List[str] = Field(default_factory=list, description="Requested order ids.")
    foundCount: int = Field(..., ge=0, description="Number of matching orders.")
    missingOrderIds: List[str] = Field(default_factory=list, description="Missing order ids.")
    foundOrders: List[OrderStatus] = Field(default_factory=list, description="Found order statuses.")


class GatewayVerificationResponse(BaseModel):
    """Gateway verification response."""

    success: bool = Field(..., description="Whether the gateway verification succeeded.")
    message: str = Field(..., description="Human-readable verification message.")
    verificationMode: str = Field(..., description="Verification strategy used.")
    accountId: str = Field(..., description="Account used for verification.")
    netLiquidation: float = Field(..., description="Net liquidation from the account summary.")
    currency: str = Field(..., description="Account currency.")
    summaryTimestamp: Optional[str] = Field(None, description="Summary timestamp.")


class TradingControlExpectation(BaseModel):
    """Expected current control state for compare-and-swap updates."""

    tradingMode: str = Field(
        default="paper",
        description="Legacy compatibility field mirrored from control.json status.",
    )
    ordersEnabled: bool = Field(..., description="Expected orders-enabled state.")
    dryRun: bool = Field(..., description="Expected dry-run state.")
    blockReason: Optional[str] = Field(None, description="Expected control block reason.")
    liveTradingOverrideFile: Optional[str] = Field(
        None,
        description="Expected live override file path.",
    )

    @field_validator("tradingMode")
    @classmethod
    def validate_trading_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "live"}:
            raise ValueError("tradingMode must be 'paper' or 'live'")
        return normalized


class TradingControlUpdateRequest(BaseModel):
    """Admin trading-control update request."""

    reason: str = Field(..., min_length=3, description="Why the control update is being made.")
    expectedCurrentState: TradingControlExpectation = Field(
        ...,
        description="Expected current control state; mismatches reject the update.",
    )
    tradingMode: Optional[str] = Field(
        None,
        description="Legacy field. Updates are no longer accepted for this field.",
    )
    ordersEnabled: Optional[bool] = Field(None, description="New orders-enabled state.")
    dryRun: Optional[bool] = Field(None, description="New dry-run value.")
    blockReason: Optional[str] = Field(None, description="New operator block reason.")
    liveTradingOverrideFile: Optional[str] = Field(
        None,
        description="Legacy field. Updates are no longer accepted for this field.",
    )
    liveEnableConfirmation: Optional[str] = Field(
        None,
        description="Legacy field. No longer used by the canonical control flow.",
    )

    @field_validator("tradingMode")
    @classmethod
    def validate_trading_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"paper", "live"}:
            raise ValueError("tradingMode must be 'paper' or 'live'")
        return normalized


class TradingControlUpdateResponse(BaseModel):
    """Admin trading-control update response."""

    success: bool = Field(..., description="Whether the update succeeded.")
    updatedFields: List[str] = Field(default_factory=list, description="Fields that were changed.")
    previousState: TradingStatusResponse = Field(..., description="State before the update.")
    currentState: TradingStatusResponse = Field(..., description="State after the update.")
    message: str = Field(..., description="Human-readable result message.")


# ---------------------------------------------------------------------------
# Telegram approval models
# ---------------------------------------------------------------------------


class ApprovalStatusResponse(BaseModel):
    """Status of a human-in-the-loop approval request."""

    approvalId: str = Field(..., description="Unique approval identifier.")
    approvalType: str = Field(
        ...,
        description="'trade', 'trade_intent', or 'live_trading'.",
    )
    status: str = Field(
        ...,
        description="Current status: pending | approved | denied | expired | used.",
    )
    requestedAt: str = Field(..., description="ISO 8601 timestamp when the request was created.")
    expiresAt: str = Field(..., description="ISO 8601 timestamp when the request expires.")
    resolvedAt: Optional[str] = Field(None, description="ISO 8601 timestamp of resolution.")
    resolveNote: Optional[str] = Field(None, description="Who approved or denied.")
    telegramMessageId: Optional[int] = Field(None, description="Telegram message ID if sent.")


class NotifyResponse(BaseModel):
    """Result of sending a Telegram notification."""

    sent: bool = Field(..., description="Whether the message was sent successfully.")
    telegramMessageId: Optional[int] = Field(None, description="Telegram message ID.")
    message: str = Field(..., description="Human-readable result.")


class BasketPreviewItem(BaseModel):
    """Per-order preview inside a basket preview call."""

    clientOrderId: str = Field(..., description="Client idempotency key.")
    symbol: str = Field(..., description="Instrument symbol.")
    preview: Optional[OrderPreview] = Field(None, description="Preview payload when available.")
    error: Optional[str] = Field(None, description="Preview error when one occurred.")


class OrderBasketPreviewResponse(BaseModel):
    """Preview response for a basket of explicit orders."""

    orderCount: int = Field(..., ge=0, description="Number of orders previewed.")
    previewedCount: int = Field(..., ge=0, description="Number of successful previews.")
    failedCount: int = Field(..., ge=0, description="Number of failed previews.")
    estimatedTotalNotional: Optional[float] = Field(
        None, description="Sum of estimated notionals across successful previews."
    )
    estimatedTotalCommission: Optional[float] = Field(
        None, description="Sum of estimated commissions across successful previews."
    )
    warnings: List[str] = Field(default_factory=list, description="Aggregated preview warnings.")
    items: List[BasketPreviewItem] = Field(default_factory=list, description="Per-order results.")


class TradeIntentOrderInfo(BaseModel):
    """Response model for a persisted order inside a trade intent."""

    intentOrderId: str = Field(..., description="Internal trade-intent order identifier.")
    sequenceNo: int = Field(..., ge=0, description="Stable order sequence in the basket.")
    clientOrderId: str = Field(..., description="Client idempotency key.")
    order: OrderSpec = Field(..., description="Original order payload.")
    preview: Optional[OrderPreview] = Field(None, description="Stored preview payload.")
    status: str = Field(..., description="Current order state.")
    orderId: Optional[str] = Field(None, description="Primary broker order identifier.")
    ibkrOrderId: Optional[str] = Field(None, description="IBKR order identifier.")
    submittedAt: Optional[str] = Field(None, description="Submission timestamp.")
    updatedAt: str = Field(..., description="Last order-state update timestamp.")
    lastError: Optional[str] = Field(None, description="Most recent order-level error.")


class TradeIntentResponse(BaseModel):
    """Response model for a basket-oriented trade intent."""

    intentId: str = Field(..., description="Stable trade-intent identifier.")
    intentKey: str = Field(..., description="Deterministic idempotency key for this basket.")
    accountId: Optional[str] = Field(None, description="Target IB account.")
    reason: str = Field(..., description="Operator-facing basket reason.")
    status: str = Field(..., description="Current trade-intent status.")
    approvalId: Optional[str] = Field(None, description="Attached approval record.")
    approvalStatus: Optional[str] = Field(None, description="Approval status for this intent.")
    dryRun: bool = Field(..., description="Whether the intent is running in dry-run mode.")
    orderCount: int = Field(..., ge=0, description="Number of orders in the basket.")
    ordersSubmitted: int = Field(..., ge=0)
    ordersFilled: int = Field(..., ge=0)
    ordersCancelled: int = Field(..., ge=0)
    ordersFailed: int = Field(..., ge=0)
    lastError: Optional[str] = Field(None, description="Most recent intent-level error.")
    createdAt: str = Field(..., description="Creation timestamp.")
    updatedAt: str = Field(..., description="Last update timestamp.")
    orders: List[TradeIntentOrderInfo] = Field(default_factory=list)


class TradeIntentListResponse(BaseModel):
    """List response for recent trade intents."""

    count: int = Field(..., ge=0, description="Number of intents returned.")
    intents: List[TradeIntentResponse] = Field(default_factory=list)


class CancelTradeIntentResponse(BaseModel):
    """Result of cancelling active orders for a trade intent."""

    intentId: str = Field(..., description="Trade-intent identifier.")
    cancelledOrderIds: List[str] = Field(default_factory=list, description="Cancelled broker order ids.")
    cancelledCount: int = Field(..., ge=0, description="Number of successfully cancelled orders.")
    failedCount: int = Field(..., ge=0, description="Number of cancellation failures.")
    intent: TradeIntentResponse = Field(..., description="Updated trade-intent state.")


# ---------------------------------------------------------------------------
# Risk and impact models
# ---------------------------------------------------------------------------


class OrderImpactResponse(BaseModel):
    """Pre-trade order impact assessment."""

    symbol: str = Field(..., description="Instrument symbol.")
    side: str = Field(..., description="Order side: BUY or SELL.")
    quantity: float = Field(..., description="Order quantity.")
    estimatedPrice: Optional[float] = Field(None, description="Estimated execution price.")
    estimatedNotional: Optional[float] = Field(None, description="Estimated order notional value.")
    estimatedCommission: Optional[float] = Field(None, description="Estimated commission.")
    existingPositionQty: float = Field(0.0, description="Current position quantity before this order.")
    newPositionQty: float = Field(0.0, description="Projected position quantity after this order.")
    concentrationBefore: Optional[float] = Field(
        None, description="Current position as % of net liquidation."
    )
    concentrationAfter: Optional[float] = Field(
        None, description="Projected position as % of net liquidation after order."
    )
    buyingPowerUsedPct: Optional[float] = Field(
        None, description="Order notional as % of available buying power."
    )
    marginUtilisationPct: Optional[float] = Field(
        None, description="Current maintenance margin as % of net liquidation."
    )
    estimatedMarginChange: Optional[float] = Field(
        None, description="Estimated change in maintenance margin from preview."
    )
    maxLossEstimate: Optional[float] = Field(
        None, description="Conservative max loss estimate for this position."
    )
    warnings: List[str] = Field(default_factory=list, description="Risk warnings.")


class PortfolioRiskResponse(BaseModel):
    """Portfolio-wide risk metrics."""

    netLiquidation: float = Field(..., description="Net liquidation value.")
    buyingPower: float = Field(..., description="Available buying power.")
    maintenanceMargin: float = Field(..., description="Current maintenance margin requirement.")
    initialMargin: float = Field(..., description="Current initial margin requirement.")
    totalUnrealisedPnl: float = Field(..., description="Total unrealised P&L across positions.")
    totalRealisedPnl: float = Field(..., description="Total realised P&L across positions.")
    positionCount: int = Field(..., description="Number of open positions.")
    concentrationBySymbol: Dict[str, float] = Field(
        default_factory=dict,
        description="Map of symbol → % of net liquidation by absolute market value.",
    )
    largestPositionSymbol: Optional[str] = Field(None, description="Symbol of the largest position.")
    largestPositionPct: Optional[float] = Field(
        None, description="Concentration % of the largest position."
    )
    marginUtilisationPct: Optional[float] = Field(
        None, description="Maintenance margin as % of net liquidation."
    )
    buyingPowerUsedPct: Optional[float] = Field(
        None, description="Approximate % of cash already committed."
    )
    riskLevel: str = Field(..., description="Overall risk level: low | medium | high | critical.")
    warnings: List[str] = Field(default_factory=list, description="Risk warnings.")


# ---------------------------------------------------------------------------
# Position-limits check model
# ---------------------------------------------------------------------------


class PositionLimitsCheckResponse(BaseModel):
    """Result of checking an order against position limits."""

    passed: bool = Field(..., description="True if all limit checks passed.")
    violations: List[str] = Field(default_factory=list, description="List of violated limits.")
    profileId: str = Field(..., description="Profile used for the check.")


# ---------------------------------------------------------------------------
# Agent profile models
# ---------------------------------------------------------------------------


class AgentProfileResponse(BaseModel):
    """An agent's trading profile / constraint set."""

    profileId: str = Field(..., description="Profile identifier.")
    description: Optional[str] = Field(None, description="Human-readable description.")
    allowedSecurityTypes: Optional[List[str]] = Field(None, description="Permitted security types.")
    allowedOrderTypes: Optional[List[str]] = Field(None, description="Permitted order types.")
    allowedSymbols: Optional[List[str]] = Field(None, description="Symbol allowlist (null = all).")
    blockedSymbols: List[str] = Field(default_factory=list, description="Blocked symbols.")
    maxPositionSizePct: Optional[float] = Field(
        None, description="Max position size as % of net liquidation."
    )
    maxPositionNotional: Optional[float] = Field(None, description="Max position notional in USD.")
    maxOrderQuantity: Optional[float] = Field(None, description="Max quantity per order.")
    maxDailyOrders: Optional[int] = Field(None, description="Max orders per day.")
    maxDailyLoss: Optional[float] = Field(None, description="Daily loss limit (negative value).")
    requireTradeApproval: bool = Field(
        True, description="Whether Telegram approval is required before placing trades."
    )
    requireLiveTradingApproval: bool = Field(
        True, description="Whether Telegram approval is required to unlock live trading."
    )
    allowOptions: bool = Field(True, description="Whether options trading is permitted.")
    allowShortSelling: bool = Field(True, description="Whether short selling is permitted.")
    notes: Optional[str] = Field(None, description="Human-readable notes.")
    source: Optional[str] = Field(None, description="File path or 'builtin_default'.")


class ProfileValidationResponse(BaseModel):
    """Result of validating a proposed order against an agent profile."""

    passed: bool = Field(..., description="True if the order satisfies all profile constraints.")
    violations: List[str] = Field(default_factory=list, description="Constraint violations found.")
    profileId: str = Field(..., description="Profile used for validation.")
    symbol: str = Field(..., description="Symbol from the proposed order.")
    side: str = Field(..., description="Side from the proposed order.")
    quantity: float = Field(..., description="Quantity from the proposed order.")


# ---------------------------------------------------------------------------
# Session activity and audit log models
# ---------------------------------------------------------------------------


class SessionOrderSummary(BaseModel):
    """Brief summary of a session order record."""

    orderId: str = Field(..., description="Internal order identifier.")
    symbol: str = Field(..., description="Instrument symbol.")
    side: str = Field(..., description="BUY or SELL.")
    quantity: float = Field(..., description="Order quantity.")
    orderType: str = Field(..., description="Order type.")
    status: str = Field(..., description="Current order status.")
    placedAt: str = Field(..., description="ISO 8601 timestamp of placement.")
    ibkrOrderId: Optional[str] = Field(None, description="IBKR-assigned order ID.")


class SessionActivityResponse(BaseModel):
    """Summary of trading activity for the current session / today."""

    sessionDate: str = Field(..., description="Session date (UTC, YYYY-MM-DD).")
    ordersPlaced: int = Field(..., description="Orders placed in this session.")
    ordersFilled: int = Field(..., description="Orders filled in this session.")
    ordersCancelled: int = Field(..., description="Orders cancelled in this session.")
    ordersPending: int = Field(..., description="Orders still open.")
    recentOrders: List[SessionOrderSummary] = Field(
        default_factory=list, description="Most recent orders (up to 20)."
    )


class AuditLogEntry(BaseModel):
    """Single audit log entry."""

    id: int = Field(..., description="Row ID.")
    correlationId: Optional[str] = Field(None, description="Correlation ID.")
    timestamp: str = Field(..., description="ISO 8601 event timestamp.")
    eventType: str = Field(..., description="Event type string.")
    eventData: Dict[str, Any] = Field(default_factory=dict, description="Event payload.")
    accountId: Optional[str] = Field(None, description="Account associated with the event.")


class AuditLogResponse(BaseModel):
    """Paginated audit log query result."""

    totalReturned: int = Field(..., description="Number of entries in this response.")
    entries: List[AuditLogEntry] = Field(default_factory=list, description="Audit log entries.")
    queryFilters: Dict[str, Any] = Field(
        default_factory=dict, description="Filters applied to this query."
    )


# ---------------------------------------------------------------------------
# Emergency stop model
# ---------------------------------------------------------------------------


class EmergencyStopResponse(BaseModel):
    """Result of executing an emergency stop."""

    success: bool = Field(..., description="Whether the emergency stop completed.")
    ordersCancelled: int = Field(..., description="Number of orders cancelled.")
    cancelledOrderIds: List[str] = Field(
        default_factory=list, description="IDs of cancelled orders."
    )
    tradingDisabled: bool = Field(..., description="Whether trading was disabled in control.json.")
    telegramNotified: bool = Field(..., description="Whether a Telegram notification was sent.")
    message: str = Field(..., description="Human-readable summary.")
