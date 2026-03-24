"""Typed request/response models for the MCP surface."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from ibkr_core.models import (
    AccountSummary,
    Bar,
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


class AccountStatusResponse(BaseModel):
    """Legacy combined account status response."""

    summary: AccountSummary = Field(..., description="Account summary.")
    positions: List[Position] = Field(default_factory=list, description="Open positions.")
    positionCount: int = Field(..., ge=0, description="Number of positions.")


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

    tradingMode: str = Field(..., description="Expected trading mode.")
    ordersEnabled: bool = Field(..., description="Expected orders-enabled state.")
    dryRun: bool = Field(..., description="Expected dry-run state.")
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
    tradingMode: Optional[str] = Field(None, description="New trading mode.")
    ordersEnabled: Optional[bool] = Field(None, description="New orders-enabled state.")
    dryRun: Optional[bool] = Field(None, description="New dry-run value.")
    liveTradingOverrideFile: Optional[str] = Field(
        None,
        description="New live override file path; empty string clears it.",
    )
    liveEnableConfirmation: Optional[str] = Field(
        None,
        description="Exact confirmation string required when enabling live real-money trading.",
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
