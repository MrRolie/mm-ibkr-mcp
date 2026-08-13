"""Internal trade-intent models shared by the MCP trading workflow."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ibkr_core.models import OrderPreview, OrderSpec


class TradeIntentStatus(str, Enum):
    """Lifecycle state for a basket-style trade intent."""

    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    SIMULATED = "SIMULATED"


class TradeIntentOrderStatus(str, Enum):
    """Lifecycle state for a single order within a trade intent."""

    PLANNED = "PLANNED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SIMULATED = "SIMULATED"


class TradeIntentOrderRecord(BaseModel):
    """Persisted order state inside a trade intent."""

    intent_order_id: str = Field(..., description="Internal intent-order identifier.")
    sequence_no: int = Field(..., ge=0, description="Stable order within the basket.")
    client_order_id: str = Field(..., description="Client idempotency key.")
    order: OrderSpec = Field(..., description="Original order payload.")
    preview: Optional[OrderPreview] = Field(None, description="Optional preview payload.")
    status: TradeIntentOrderStatus = Field(..., description="Current order state.")
    order_id: Optional[str] = Field(None, description="Primary broker order identifier.")
    ibkr_order_id: Optional[str] = Field(None, description="IBKR order id when distinct.")
    submitted_at: Optional[datetime] = Field(None, description="Submission timestamp.")
    updated_at: datetime = Field(..., description="Last state update timestamp.")
    last_error: Optional[str] = Field(None, description="Last known error.")


class TradeIntentRecord(BaseModel):
    """Persisted trade-intent state."""

    intent_id: str = Field(..., description="Stable internal trade-intent identifier.")
    intent_key: str = Field(..., description="Deterministic idempotency key.")
    account_id: Optional[str] = Field(None, description="Target IB account.")
    reason: str = Field(..., description="Operator-facing reason for the basket.")
    status: TradeIntentStatus = Field(..., description="Current intent status.")
    approval_id: Optional[str] = Field(None, description="Attached approval record id.")
    approval_status: Optional[str] = Field(None, description="Approval status when relevant.")
    dry_run: bool = Field(..., description="Whether the intent was created in dry-run mode.")
    order_count: int = Field(..., ge=0, description="Number of orders in the basket.")
    orders_submitted: int = Field(..., ge=0)
    orders_filled: int = Field(..., ge=0)
    orders_cancelled: int = Field(..., ge=0)
    orders_failed: int = Field(..., ge=0)
    last_error: Optional[str] = Field(None, description="Most recent intent-level error.")
    created_at: datetime = Field(..., description="Creation timestamp.")
    updated_at: datetime = Field(..., description="Last update timestamp.")
    orders: list[TradeIntentOrderRecord] = Field(default_factory=list)
