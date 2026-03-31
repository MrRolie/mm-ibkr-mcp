"""
Order management for IBKR trading.

Provides order lifecycle management with safety rails:
- Order preview (estimate impact without placing)
- Order placement (with safety checks)
- Order cancellation
- Order status tracking

Safety features:
- ORDERS_ENABLED=false prevents all real order placement
- TRADING_MODE=live requires explicit override file
- In-memory order registry for tracking
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ib_insync import LimitOrder, MarketOrder, Order, StopLimitOrder, StopOrder, Trade

from ibkr_core.broker import get_broker_adapter
from ibkr_core.client import IBKRClient
from ibkr_core.config import get_config
from ibkr_core.contracts import resolve_contract
from ibkr_core.market_data import get_quote
from ibkr_core.models import (
    CancelResult,
    OrderLeg,
    OrderPreview,
    OrderResult,
    OrderSpec,
    OrderStatus,
)
from ibkr_core.persistence import record_audit_event, save_order, update_order_status

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class OrderError(Exception):
    """Base exception for order-related errors."""

    pass


class OrderValidationError(OrderError):
    """Raised when order specification is invalid."""

    pass


class OrderPlacementError(OrderError):
    """Raised when order placement fails."""

    pass


class OrderCancelError(OrderError):
    """Raised when order cancellation fails."""

    pass


class OrderNotFoundError(OrderError):
    """Raised when order cannot be found."""

    pass


class OrderPreviewError(OrderError):
    """Raised when order preview fails."""

    pass


# =============================================================================
# Order Registry (In-Memory)
# =============================================================================


class OrderRegistry:
    """
    In-memory registry for tracking orders placed during this session.

    Limitations:
    - Process-local only; data is lost on restart
    - No persistence (Phase 8 will add database support)

    The registry maps our order_id strings to IBKR trade metadata.
    """

    def __init__(self):
        self._orders: Dict[str, Dict] = {}
        self._trades: Dict[str, Trade] = {}
        self._client_order_ids: Dict[str, str] = {}

    def register(self, trade: Trade, symbol: str, client_order_id: Optional[str] = None) -> str:
        """
        Register a trade and return our order_id.

        Args:
            trade: ib_insync Trade object from placeOrder
            symbol: Symbol string for reference
            client_order_id: Client-generated idempotency key, if present

        Returns:
            order_id: Our stable order identifier (string)
        """
        # Use permId as stable identifier; fall back to orderId
        perm_id = trade.order.permId if trade.order.permId else trade.order.orderId
        order_id = str(perm_id) if perm_id else f"ord_{trade.order.orderId}"

        self._orders[order_id] = {
            "order_id": order_id,
            "ib_order_id": trade.order.orderId,
            "perm_id": trade.order.permId,
            "client_id": trade.order.clientId,
            "client_order_id": client_order_id or getattr(trade.order, "orderRef", None),
            "con_id": trade.contract.conId if trade.contract else None,
            "symbol": symbol,
            "placed_at": datetime.now(timezone.utc).isoformat(),
            "side": trade.order.action,
            "quantity": trade.order.totalQuantity,
            "order_type": trade.order.orderType,
        }
        self._trades[order_id] = trade
        effective_client_order_id = client_order_id or getattr(trade.order, "orderRef", None)
        if effective_client_order_id:
            self._client_order_ids[effective_client_order_id] = order_id

        logger.debug(
            f"Registered order {order_id}: {symbol} {trade.order.action} "
            f"{trade.order.totalQuantity}"
        )
        return order_id

    def lookup(self, order_id: str) -> Optional[Trade]:
        """
        Lookup a trade by order_id.

        Args:
            order_id: Our order identifier

        Returns:
            Trade object or None if not found
        """
        return self._trades.get(order_id)

    def lookup_metadata(self, order_id: str) -> Optional[Dict]:
        """
        Lookup order metadata by order_id.

        Args:
            order_id: Our order identifier

        Returns:
            Metadata dict or None if not found
        """
        return self._orders.get(order_id)

    def lookup_order_id_by_client_order_id(self, client_order_id: str) -> Optional[str]:
        """Find an order id by client order id."""
        return self._client_order_ids.get(client_order_id)

    def lookup_by_client_order_id(self, client_order_id: str) -> Optional[Trade]:
        """Find a trade by client order id."""
        order_id = self.lookup_order_id_by_client_order_id(client_order_id)
        if order_id is None:
            return None
        return self.lookup(order_id)

    def all_orders(self) -> List[Dict]:
        """Return list of all registered order metadata."""
        return list(self._orders.values())

    def clear(self) -> None:
        """Clear all registered orders (for testing)."""
        self._orders.clear()
        self._trades.clear()
        self._client_order_ids.clear()

    @property
    def size(self) -> int:
        """Number of registered orders."""
        return len(self._orders)


# Global order registry
_order_registry = OrderRegistry()


def get_order_registry() -> OrderRegistry:
    """Get the global order registry."""
    return _order_registry


# =============================================================================
# Validation Helpers
# =============================================================================


def validate_order_spec(order_spec: OrderSpec) -> List[str]:
    """
    Validate an OrderSpec and return list of validation errors.

    Supports both basic orders (MKT, LMT, STP, STP_LMT) and advanced orders
    (TRAIL, TRAIL_LIMIT, BRACKET, MOC, OPG).

    Args:
        order_spec: The order specification to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Quantity must be positive (already enforced by Pydantic, but double-check)
    if order_spec.quantity <= 0:
        errors.append(f"Quantity must be positive, got {order_spec.quantity}")

    # Side validation (Pydantic allows BUY/SELL only)
    if order_spec.side.upper() not in ("BUY", "SELL"):
        errors.append(f"Side must be 'BUY' or 'SELL', got '{order_spec.side}'")

    order_type = order_spec.orderType.upper()

    # === Basic Order Types ===

    # MKT orders should not have limit price
    if order_type == "MKT" and order_spec.limitPrice is not None:
        errors.append("Market orders (MKT) cannot have a limit price")

    # LMT orders require limit price > 0
    if order_type == "LMT":
        if order_spec.limitPrice is None:
            errors.append("Limit orders (LMT) require a limit price")
        elif order_spec.limitPrice <= 0:
            errors.append(f"Limit price must be positive, got {order_spec.limitPrice}")

    # STP orders require stop price
    if order_type == "STP":
        if order_spec.stopPrice is None:
            errors.append("Stop orders (STP) require a stop price")
        elif order_spec.stopPrice <= 0:
            errors.append(f"Stop price must be positive, got {order_spec.stopPrice}")

    # STP_LMT orders require both prices
    if order_type == "STP_LMT":
        if order_spec.stopPrice is None:
            errors.append("Stop-limit orders (STP_LMT) require a stop price")
        elif order_spec.stopPrice <= 0:
            errors.append(f"Stop price must be positive, got {order_spec.stopPrice}")

        if order_spec.limitPrice is None:
            errors.append("Stop-limit orders (STP_LMT) require a limit price")
        elif order_spec.limitPrice <= 0:
            errors.append(f"Limit price must be positive, got {order_spec.limitPrice}")

    # === Advanced Order Types (Phase 4.5) ===

    # TRAIL orders require exactly one of trailingAmount or trailingPercent
    if order_type == "TRAIL":
        has_amount = order_spec.trailingAmount is not None
        has_percent = order_spec.trailingPercent is not None
        if not has_amount and not has_percent:
            errors.append("Trailing stop orders (TRAIL) require trailingAmount OR trailingPercent")
        elif has_amount and has_percent:
            errors.append(
                "Trailing stop orders (TRAIL) cannot have both trailingAmount AND trailingPercent"
            )

    # TRAIL_LIMIT orders require trailing params + limit offset
    if order_type == "TRAIL_LIMIT":
        has_amount = order_spec.trailingAmount is not None
        has_percent = order_spec.trailingPercent is not None
        if not has_amount and not has_percent:
            errors.append(
                "Trailing stop-limit orders (TRAIL_LIMIT) require trailingAmount OR trailingPercent"
            )
        elif has_amount and has_percent:
            errors.append(
                "Trailing stop-limit orders (TRAIL_LIMIT) cannot have both "
                "trailingAmount AND trailingPercent"
            )
        # Note: limitPrice is the offset from the stop for TRAIL_LIMIT in IB

    # BRACKET orders require take profit and stop loss prices
    if order_type == "BRACKET":
        if order_spec.limitPrice is None:
            errors.append("Bracket orders (BRACKET) require a limit price for the entry order")
        elif order_spec.limitPrice <= 0:
            errors.append(f"Entry limit price must be positive, got {order_spec.limitPrice}")

        if order_spec.takeProfitPrice is None:
            errors.append("Bracket orders (BRACKET) require a take profit price")
        elif order_spec.takeProfitPrice <= 0:
            errors.append(f"Take profit price must be positive, got {order_spec.takeProfitPrice}")

        if order_spec.stopLossPrice is None:
            errors.append("Bracket orders (BRACKET) require a stop loss price")
        elif order_spec.stopLossPrice <= 0:
            errors.append(f"Stop loss price must be positive, got {order_spec.stopLossPrice}")

        # Validate bracket price logic
        if order_spec.takeProfitPrice and order_spec.stopLossPrice and order_spec.limitPrice:
            side = order_spec.side.upper()
            if side == "BUY":
                # For buy: entry < take_profit and stop_loss < entry
                if order_spec.takeProfitPrice <= order_spec.limitPrice:
                    errors.append(
                        "For BUY bracket: take profit price must be greater than entry price"
                    )
                if order_spec.stopLossPrice >= order_spec.limitPrice:
                    errors.append("For BUY bracket: stop loss price must be less than entry price")
            else:  # SELL
                # For sell (short): entry > take_profit and stop_loss > entry
                if order_spec.takeProfitPrice >= order_spec.limitPrice:
                    errors.append(
                        "For SELL bracket: take profit price must be less than entry price"
                    )
                if order_spec.stopLossPrice <= order_spec.limitPrice:
                    errors.append(
                        "For SELL bracket: stop loss price must be greater than entry price"
                    )

    tif = order_spec.tif.upper()

    # MOC/OPG orders - no special price requirements, but TIF is constrained
    if order_type == "MOC":
        # IBKR expects MOC as orderType with DAY TIF
        if tif != "DAY":
            errors.append("MOC orders require tif='DAY'")
    elif order_type == "OPG":
        # Opening auction orders should use OPG TIF (or default DAY)
        if tif not in {"OPG", "DAY"}:
            errors.append("OPG orders require tif='OPG' (or default DAY)")
    else:
        # TIF validation (standard orders)
        valid_tif = {"DAY", "GTC", "IOC", "FOK"}
        if tif not in valid_tif:
            errors.append(f"TIF must be one of {valid_tif}, got '{order_spec.tif}'")

    return errors


def check_safety_guards(
    order_spec: OrderSpec,
    estimated_notional: Optional[float] = None,
    max_notional: Optional[float] = None,
    max_quantity: Optional[float] = None,
) -> List[str]:
    """
    Check additional safety guards for order placement.

    Args:
        order_spec: The order specification
        estimated_notional: Estimated notional value
        max_notional: Maximum allowed notional (optional guard)
        max_quantity: Maximum allowed quantity (optional guard)

    Returns:
        List of warning messages (not errors, but advisories)
    """
    warnings = []

    if max_notional is not None and estimated_notional is not None:
        if estimated_notional > max_notional:
            warnings.append(
                f"Estimated notional ${estimated_notional:,.2f} exceeds max ${max_notional:,.2f}"
            )

    if max_quantity is not None and order_spec.quantity > max_quantity:
        warnings.append(f"Quantity {order_spec.quantity} exceeds max {max_quantity}")

    return warnings


# =============================================================================
# Order Building
# =============================================================================


def _build_ib_order(order_spec: OrderSpec) -> Order:
    """
    Build an ib_insync Order object from OrderSpec.

    For simple orders, returns a single Order.
    For BRACKET orders, use _build_bracket_orders() instead.

    Args:
        order_spec: Our order specification

    Returns:
        ib_insync Order object
    """
    order_type = order_spec.orderType.upper()
    action = order_spec.side.upper()
    quantity = order_spec.quantity

    if order_type == "MKT":
        order = MarketOrder(action=action, totalQuantity=quantity)

    elif order_type == "LMT":
        order = LimitOrder(
            action=action,
            totalQuantity=quantity,
            lmtPrice=order_spec.limitPrice,
        )

    elif order_type == "STP":
        order = StopOrder(
            action=action,
            totalQuantity=quantity,
            stopPrice=order_spec.stopPrice,
        )

    elif order_type == "STP_LMT":
        order = StopLimitOrder(
            action=action,
            totalQuantity=quantity,
            stopPrice=order_spec.stopPrice,
            lmtPrice=order_spec.limitPrice,
        )

    elif order_type == "TRAIL":
        # Trailing stop order
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = "TRAIL"

        if order_spec.trailingPercent is not None:
            order.trailingPercent = order_spec.trailingPercent
        elif order_spec.trailingAmount is not None:
            order.auxPrice = order_spec.trailingAmount

        # Set initial trail stop price if provided
        if order_spec.trailStopPrice is not None:
            order.trailStopPrice = order_spec.trailStopPrice

    elif order_type == "TRAIL_LIMIT":
        # Trailing stop-limit order
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = "TRAIL LIMIT"

        if order_spec.trailingPercent is not None:
            order.trailingPercent = order_spec.trailingPercent
        elif order_spec.trailingAmount is not None:
            order.auxPrice = order_spec.trailingAmount

        # Limit price offset (distance from stop to limit)
        if order_spec.limitPrice is not None:
            order.lmtPriceOffset = order_spec.limitPrice

        # Set initial trail stop price if provided
        if order_spec.trailStopPrice is not None:
            order.trailStopPrice = order_spec.trailStopPrice

    elif order_type == "MOC":
        # Market-on-close order (IBKR expects orderType=MOC with DAY TIF)
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = "MOC"
        order.tif = "DAY"
        # Return early to skip setting tif below
        order.outsideRth = order_spec.outsideRth
        order.transmit = order_spec.transmit
        _apply_oca(order, order_spec)
        return order

    elif order_type == "OPG":
        # Market-on-open (opening auction) order
        order = MarketOrder(action=action, totalQuantity=quantity)
        order.tif = "OPG"
        # Return early to skip setting tif below
        order.outsideRth = order_spec.outsideRth
        order.transmit = order_spec.transmit
        _apply_oca(order, order_spec)
        return order

    elif order_type == "BRACKET":
        # Bracket orders are handled by _build_bracket_orders()
        raise OrderValidationError(
            "BRACKET orders must be built with _build_bracket_orders(), not _build_ib_order()"
        )

    else:
        raise OrderValidationError(f"Unsupported order type: {order_type}")

    # Set time in force (for non-MOC/OPG orders)
    order.tif = order_spec.tif.upper()

    # Set outsideRth
    order.outsideRth = order_spec.outsideRth

    # Set transmit flag
    order.transmit = order_spec.transmit

    # Preserve client idempotency key at the broker layer when provided.
    if order_spec.clientOrderId:
        order.orderRef = order_spec.clientOrderId

    # Apply OCA settings if present
    _apply_oca(order, order_spec)

    return order


def _apply_oca(order: Order, order_spec: OrderSpec) -> None:
    """
    Apply OCA (One-Cancels-All) settings to an order.

    OCA Types:
        1 = Cancel with block - cancel remaining orders when one fills
        2 = Reduce with block - reduce remaining quantities proportionally
        3 = Reduce without block - reduce without blocking

    Args:
        order: The ib_insync Order to modify
        order_spec: Our order specification with OCA settings
    """
    if order_spec.ocaGroup:
        order.ocaGroup = order_spec.ocaGroup
        order.ocaType = order_spec.ocaType if order_spec.ocaType else 1


def _build_bracket_orders(order_spec: OrderSpec) -> List[Order]:
    """
    Build a bracket order set (entry + take profit + stop loss).

    A bracket order consists of:
    1. Entry order (limit order)
    2. Take profit order (limit order, opposite side)
    3. Stop loss order (stop or stop-limit order, opposite side)

    The take profit and stop loss are linked via OCA group and will cancel
    each other when one fills.

    Args:
        order_spec: Order specification with bracket parameters

    Returns:
        List of [entry_order, take_profit_order, stop_loss_order]
    """
    if order_spec.orderType.upper() != "BRACKET":
        raise OrderValidationError("_build_bracket_orders() requires orderType=BRACKET")

    action = order_spec.side.upper()
    opposite_action = "SELL" if action == "BUY" else "BUY"
    quantity = order_spec.quantity

    # Generate OCA group name
    oca_group = (
        order_spec.ocaGroup or f"bracket_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    )

    # 1. Entry order (limit)
    entry_order = LimitOrder(
        action=action,
        totalQuantity=quantity,
        lmtPrice=order_spec.limitPrice,
    )
    entry_order.tif = order_spec.tif.upper()
    entry_order.outsideRth = order_spec.outsideRth
    entry_order.transmit = False  # Don't transmit until children are attached
    if order_spec.clientOrderId:
        entry_order.orderRef = order_spec.clientOrderId

    # 2. Take profit order (limit on opposite side)
    tp_order = LimitOrder(
        action=opposite_action,
        totalQuantity=quantity,
        lmtPrice=order_spec.takeProfitPrice,
    )
    tp_order.tif = "GTC"  # Take profit stays until filled
    tp_order.outsideRth = order_spec.outsideRth
    tp_order.ocaGroup = oca_group
    tp_order.ocaType = 1  # Cancel with block
    tp_order.transmit = False  # Don't transmit until stop loss is set
    if order_spec.clientOrderId:
        tp_order.orderRef = order_spec.clientOrderId

    # 3. Stop loss order (stop or stop-limit on opposite side)
    if order_spec.stopLossLimitPrice is not None:
        # Stop-limit for stop loss
        sl_order = StopLimitOrder(
            action=opposite_action,
            totalQuantity=quantity,
            stopPrice=order_spec.stopLossPrice,
            lmtPrice=order_spec.stopLossLimitPrice,
        )
    else:
        # Simple stop for stop loss
        sl_order = StopOrder(
            action=opposite_action,
            totalQuantity=quantity,
            stopPrice=order_spec.stopLossPrice,
        )

    sl_order.tif = "GTC"  # Stop loss stays until triggered
    sl_order.outsideRth = order_spec.outsideRth
    sl_order.ocaGroup = oca_group
    sl_order.ocaType = 1  # Cancel with block
    sl_order.transmit = order_spec.bracketTransmit  # Final order transmits all
    if order_spec.clientOrderId:
        sl_order.orderRef = order_spec.clientOrderId

    return [entry_order, tp_order, sl_order]


def _get_opposite_side(side: str) -> str:
    """Get the opposite order side."""
    return "SELL" if side.upper() == "BUY" else "BUY"


def _map_ibkr_status_to_model(ib_status: str) -> str:
    """
    Map IBKR order status to our OrderStatus model status.

    IBKR statuses: PendingSubmit, PendingCancel, PreSubmitted, Submitted,
                   ApiCancelled, Cancelled, Filled, Inactive
    Our statuses:  PENDING_SUBMIT, PENDING_CANCEL, SUBMITTED, PARTIALLY_FILLED,
                   FILLED, CANCELLED, REJECTED, EXPIRED
    """
    status_map = {
        "PendingSubmit": "PENDING_SUBMIT",
        "PendingCancel": "PENDING_CANCEL",
        "PreSubmitted": "SUBMITTED",
        "Submitted": "SUBMITTED",
        "ApiCancelled": "CANCELLED",
        "Cancelled": "CANCELLED",
        "Filled": "FILLED",
        "Inactive": "CANCELLED",  # Often means rejected or error
    }
    return status_map.get(ib_status, "SUBMITTED")


def _trade_to_order_status(trade: Trade, order_id: str) -> OrderStatus:
    """
    Convert an ib_insync Trade to our OrderStatus model.

    Args:
        trade: ib_insync Trade object
        order_id: Our order identifier

    Returns:
        OrderStatus model
    """
    order_status = trade.orderStatus
    order = trade.order

    # Map status
    ib_status = order_status.status if order_status else "PendingSubmit"
    mapped_status = _map_ibkr_status_to_model(ib_status)

    # Handle partial fills
    filled = order_status.filled if order_status else 0
    remaining = order_status.remaining if order_status else order.totalQuantity
    if filled > 0 and remaining > 0:
        mapped_status = "PARTIALLY_FILLED"

    # Build warnings from any log entries
    warnings = []
    if trade.log:
        for log_entry in trade.log[-5:]:  # Last 5 log entries
            if hasattr(log_entry, "message") and log_entry.message:
                warnings.append(log_entry.message)

    # Convert clientId to string if present
    client_order_id = getattr(order, "orderRef", None)
    if client_order_id is not None and not isinstance(client_order_id, str):
        client_order_id = None

    return OrderStatus(
        orderId=order_id,
        clientOrderId=client_order_id,
        status=mapped_status,
        filledQuantity=filled if order_status else 0.0,
        remainingQuantity=remaining if order_status else order.totalQuantity,
        avgFillPrice=order_status.avgFillPrice if order_status else 0.0,
        lastUpdate=datetime.now(timezone.utc),
        warnings=warnings,
    )


# =============================================================================
# Order Functions
# =============================================================================


def preview_order(
    client: IBKRClient,
    order_spec: OrderSpec,
    timeout_s: float = 5.0,
) -> OrderPreview:
    """
    Preview an order without placing it.

    Gets current market data and estimates execution price and notional value.
    Does NOT send any order to IBKR.

    Supports all order types including advanced orders (TRAIL, BRACKET, MOC, OPG).

    Args:
        client: Connected IBKRClient instance.
        order_spec: The order specification to preview.
        timeout_s: Timeout for market data retrieval.

    Returns:
        OrderPreview with estimated values and any warnings.
        For BRACKET orders, includes legs with individual estimates.

    Raises:
        OrderValidationError: If order_spec is invalid.
        OrderPreviewError: If preview fails.
    """
    logger.info(
        f"Previewing order: {order_spec.side} {order_spec.quantity} {order_spec.instrument.symbol}"
    )

    # Validate order spec
    validation_errors = validate_order_spec(order_spec)
    if validation_errors:
        raise OrderValidationError("; ".join(validation_errors))

    client.ensure_connected()

    try:
        # Resolve contract to verify it exists
        contract = resolve_contract(order_spec.instrument, client)

        # Get current quote for price estimation
        quote = get_quote(order_spec.instrument, client, timeout_s=timeout_s)

        # Get multiplier for futures
        multiplier = 1.0
        if hasattr(contract, "multiplier") and contract.multiplier:
            try:
                multiplier = float(contract.multiplier)
            except (ValueError, TypeError):
                multiplier = 1.0

        # Estimate execution price based on order type
        order_type = order_spec.orderType.upper()
        side = order_spec.side.upper()

        if order_type == "LMT":
            estimated_price = order_spec.limitPrice
        elif order_type in ("MKT", "MOC", "OPG"):
            # Use ask for buys, bid for sells
            if side == "BUY":
                estimated_price = quote.ask if quote.ask > 0 else quote.last
            else:
                estimated_price = quote.bid if quote.bid > 0 else quote.last
        elif order_type == "STP":
            estimated_price = order_spec.stopPrice
        elif order_type == "STP_LMT":
            estimated_price = order_spec.limitPrice
        elif order_type in ("TRAIL", "TRAIL_LIMIT"):
            # For trailing, use stop price if set, otherwise current market
            if order_spec.trailStopPrice:
                estimated_price = order_spec.trailStopPrice
            else:
                # Estimate initial stop based on current price and trail amount/percent
                if order_spec.trailingPercent:
                    if side == "SELL":
                        estimated_price = quote.last * (1 - order_spec.trailingPercent / 100)
                    else:
                        estimated_price = quote.last * (1 + order_spec.trailingPercent / 100)
                elif order_spec.trailingAmount:
                    if side == "SELL":
                        estimated_price = quote.last - order_spec.trailingAmount
                    else:
                        estimated_price = quote.last + order_spec.trailingAmount
                else:
                    estimated_price = quote.last
        elif order_type == "BRACKET":
            # For bracket, entry is the limit price
            estimated_price = order_spec.limitPrice
        else:
            estimated_price = quote.last

        # Handle zero price
        if not estimated_price or estimated_price <= 0:
            estimated_price = quote.last if quote.last > 0 else None

        # Calculate notional
        estimated_notional = None
        if estimated_price:
            estimated_notional = order_spec.quantity * estimated_price * multiplier

        # Build warnings
        warnings = []

        # Add warning if market is closed (no bid/ask)
        if quote.bid == 0 and quote.ask == 0:
            warnings.append("Market appears closed - bid/ask not available")

        # Add warning for large orders
        if estimated_notional and estimated_notional > 100000:
            warnings.append(f"Large order: estimated notional ${estimated_notional:,.2f}")

        # Add order-type specific warnings
        if order_type == "MOC":
            warnings.append("Market-on-close order will execute at market close")
        elif order_type == "OPG":
            warnings.append("Market-on-open order will execute at market open")
        elif order_type in ("TRAIL", "TRAIL_LIMIT"):
            if order_spec.trailingPercent:
                warnings.append(f"Trailing stop: {order_spec.trailingPercent}% from high/low")
            elif order_spec.trailingAmount:
                warnings.append(f"Trailing stop: ${order_spec.trailingAmount:.2f} from high/low")

        # Add simulated preview note
        warnings.append("Preview is simulated using current market data")

        # Build legs for bracket orders
        legs = []
        total_notional = estimated_notional

        if order_type == "BRACKET":
            opposite_side = _get_opposite_side(side)

            # Entry leg
            entry_notional = (
                order_spec.quantity * order_spec.limitPrice * multiplier
                if order_spec.limitPrice
                else None
            )
            legs.append(
                OrderLeg(
                    role="entry",
                    orderType="LMT",
                    side=side,
                    quantity=order_spec.quantity,
                    limitPrice=order_spec.limitPrice,
                    tif=order_spec.tif,
                    estimatedPrice=order_spec.limitPrice,
                    estimatedNotional=entry_notional,
                )
            )

            # Take profit leg
            tp_notional = (
                order_spec.quantity * order_spec.takeProfitPrice * multiplier
                if order_spec.takeProfitPrice
                else None
            )
            legs.append(
                OrderLeg(
                    role="take_profit",
                    orderType="LMT",
                    side=opposite_side,
                    quantity=order_spec.quantity,
                    limitPrice=order_spec.takeProfitPrice,
                    tif="GTC",
                    estimatedPrice=order_spec.takeProfitPrice,
                    estimatedNotional=tp_notional,
                )
            )

            # Stop loss leg
            sl_type = "STP_LMT" if order_spec.stopLossLimitPrice else "STP"
            sl_notional = (
                order_spec.quantity * order_spec.stopLossPrice * multiplier
                if order_spec.stopLossPrice
                else None
            )
            legs.append(
                OrderLeg(
                    role="stop_loss",
                    orderType=sl_type,
                    side=opposite_side,
                    quantity=order_spec.quantity,
                    stopPrice=order_spec.stopLossPrice,
                    limitPrice=order_spec.stopLossLimitPrice,
                    tif="GTC",
                    estimatedPrice=order_spec.stopLossPrice,
                    estimatedNotional=sl_notional,
                )
            )

            # Total notional is worst case (entry notional)
            total_notional = entry_notional

            warnings.append("Bracket order with 3 legs: entry, take profit, stop loss")

        logger.info(
            f"Preview complete: estimated price={estimated_price}, "
            f"notional={estimated_notional}"
        )

        preview_result = OrderPreview(
            orderSpec=order_spec,
            estimatedPrice=estimated_price,
            estimatedNotional=estimated_notional,
            estimatedCommission=None,  # Not available in preview
            estimatedInitialMarginChange=None,
            estimatedMaintenanceMarginChange=None,
            warnings=warnings,
            legs=legs,
            totalNotional=total_notional,
        )

        # Record audit event for order preview
        try:
            record_audit_event(
                event_type="ORDER_PREVIEW",
                event_data={
                    "symbol": order_spec.instrument.symbol,
                    "side": order_spec.side,
                    "quantity": order_spec.quantity,
                    "order_type": order_spec.orderType,
                    "estimated_price": estimated_price,
                    "estimated_notional": estimated_notional,
                    "legs_count": len(legs) if legs else 0,
                    "strategy_id": order_spec.strategyId,
                    "virtual_subaccount_id": order_spec.virtualSubaccountId
                    or order_spec.strategyId,
                },
                account_id=order_spec.accountId,
                strategy_id=order_spec.strategyId,
                virtual_subaccount_id=order_spec.virtualSubaccountId,
            )
        except Exception as e:
            logger.warning(f"Failed to record preview audit event: {e}")

        return preview_result

    except OrderValidationError:
        raise
    except Exception as e:
        raise OrderPreviewError(f"Failed to preview order: {e}") from e


def place_order(
    client: IBKRClient,
    order_spec: OrderSpec,
    max_notional: Optional[float] = None,
    max_quantity: Optional[float] = None,
    wait_for_status_s: float = 2.0,
) -> OrderResult:
    """
    Place an order with safety checks.

    If ORDERS_ENABLED=false, returns a simulated result without placing.
    If ORDERS_ENABLED=true, places the order via IBKR API.

    Supports all order types including advanced orders (TRAIL, BRACKET, MOC, OPG).
    For BRACKET orders, places 3 linked orders (entry + take profit + stop loss).

    Args:
        client: Connected IBKRClient instance.
        order_spec: The order specification to place.
        max_notional: Optional max notional guard (raises warning if exceeded).
        max_quantity: Optional max quantity guard (raises warning if exceeded).
        wait_for_status_s: Time to wait for initial order status.

    Returns:
        OrderResult with status and order details.
        For BRACKET orders, includes orderIds list and orderRoles mapping.

    Raises:
        OrderValidationError: If order_spec is invalid.
        OrderPlacementError: If order placement fails.
        TradingDisabledError: Only if code has a bug (should return SIMULATED instead).
    """
    config = get_config()
    symbol = order_spec.instrument.symbol

    logger.info(
        f"Place order request: {order_spec.side} {order_spec.quantity} {symbol} "
        f"({order_spec.orderType})"
    )

    # Validate order spec
    validation_errors = validate_order_spec(order_spec)
    if validation_errors:
        logger.warning(f"Order validation failed: {validation_errors}")
        return OrderResult(
            orderId=None,
            clientOrderId=order_spec.clientOrderId,
            status="REJECTED",
            orderStatus=None,
            errors=validation_errors,
        )

    # Safety check: orders_enabled (loaded from control.json via config)
    if not config.orders_enabled:
        logger.info(
            f"orders_enabled=false in control.json - returning simulated result for "
            f"{order_spec.side} {order_spec.quantity} {symbol}"
        )
        return OrderResult(
            orderId=None,
            clientOrderId=order_spec.clientOrderId,
            status="SIMULATED",
            orderStatus=None,
            errors=["Order not placed: orders_enabled=false in control.json"],
        )

    # If we reach here, orders are enabled - proceed with placement
    client.ensure_connected()
    broker = get_broker_adapter(client)

    try:
        if order_spec.clientOrderId:
            existing_order_id = _order_registry.lookup_order_id_by_client_order_id(
                order_spec.clientOrderId
            )
            existing_trade = _order_registry.lookup_by_client_order_id(order_spec.clientOrderId)
            if existing_trade is None:
                existing_trade = _find_trade_by_client_order_id(client, order_spec.clientOrderId)
            if existing_trade is not None and existing_order_id is None:
                perm_id = existing_trade.order.permId if existing_trade.order.permId else None
                existing_order_id = (
                    str(perm_id)
                    if perm_id
                    else f"ord_{existing_trade.order.orderId}"
                )
            if existing_trade and existing_order_id:
                existing_status = _trade_to_order_status(existing_trade, existing_order_id)
                logger.info(
                    "Returning existing order for idempotent retry: clientOrderId=%s orderId=%s",
                    order_spec.clientOrderId,
                    existing_order_id,
                )
                return OrderResult(
                    orderId=existing_order_id,
                    clientOrderId=order_spec.clientOrderId,
                    status=(
                        "REJECTED"
                        if existing_status.status in {"REJECTED", "CANCELLED"}
                        else "ACCEPTED"
                    ),
                    orderStatus=existing_status,
                    errors=[],
                )

        # Resolve contract
        contract = resolve_contract(order_spec.instrument, client)

        # Preview for safety guards
        preview = preview_order(client, order_spec)

        # Check safety guards
        guard_warnings = check_safety_guards(
            order_spec,
            estimated_notional=preview.estimatedNotional,
            max_notional=max_notional,
            max_quantity=max_quantity,
        )

        # Handle BRACKET orders specially (multiple linked orders)
        if order_spec.orderType.upper() == "BRACKET":
            return _place_bracket_order(
                client,
                contract,
                order_spec,
                symbol,
                guard_warnings,
                wait_for_status_s,
                preview,
            )

        # Build the order for non-bracket types
        ib_order = _build_ib_order(order_spec)

        # Set account if specified
        if order_spec.accountId:
            ib_order.account = order_spec.accountId

        logger.info(
            f"Placing order: {ib_order.action} {ib_order.totalQuantity} {symbol} "
            f"@ {order_spec.orderType}"
        )

        # Place the order
        trade = broker.place_order(contract, ib_order)

        # Register in our registry
        order_id = _order_registry.register(trade, symbol, order_spec.clientOrderId)

        # Wait briefly for initial status
        broker.sleep(wait_for_status_s)

        # Get current status
        order_status = _trade_to_order_status(trade, order_id)

        # Add guard warnings to status
        order_status.warnings.extend(guard_warnings)

        # Determine result status
        if order_status.status in ("REJECTED", "CANCELLED"):
            result_status = "REJECTED"
        else:
            result_status = "ACCEPTED"

        logger.info(f"Order placed: order_id={order_id}, status={order_status.status}")

        # Record audit event and save to order history
        try:
            # Get configuration snapshot
            config = get_config()
            config_snapshot = {
                "trading_mode": config.trading_mode,
                "orders_enabled": config.orders_enabled,
            }

            # Save to order history database
            save_order(
                order_id=order_id,
                account_id=order_spec.accountId or client.managed_accounts[0]
                if client.managed_accounts
                else "UNKNOWN",
                symbol=symbol,
                side=order_spec.side,
                quantity=order_spec.quantity,
                order_type=order_spec.orderType,
                status=order_status.status,
                ibkr_order_id=str(trade.order.orderId) if trade.order.orderId else None,
                preview_data=preview.model_dump() if preview else None,
                config_snapshot=config_snapshot,
                strategy_id=order_spec.strategyId,
                virtual_subaccount_id=order_spec.virtualSubaccountId,
            )

            # Record audit event
            record_audit_event(
                event_type="ORDER_SUBMIT",
                event_data={
                    "order_id": order_id,
                    "ibkr_order_id": str(trade.order.orderId) if trade.order.orderId else None,
                    "symbol": symbol,
                    "side": order_spec.side,
                    "quantity": order_spec.quantity,
                    "order_type": order_spec.orderType,
                    "status": order_status.status,
                    "result_status": result_status,
                    "strategy_id": order_spec.strategyId,
                    "virtual_subaccount_id": order_spec.virtualSubaccountId
                    or order_spec.strategyId,
                },
                account_id=order_spec.accountId or client.managed_accounts[0]
                if client.managed_accounts
                else None,
                strategy_id=order_spec.strategyId,
                virtual_subaccount_id=order_spec.virtualSubaccountId,
            )
        except Exception as e:
            logger.warning(f"Failed to record order placement in audit: {e}")

        return OrderResult(
            orderId=order_id,
            clientOrderId=order_spec.clientOrderId,
            status=result_status,
            orderStatus=order_status,
            errors=[],
        )

    except OrderValidationError:
        raise
    except Exception as e:
        logger.error(f"Order placement failed: {e}")
        raise OrderPlacementError(f"Failed to place order: {e}") from e


def _place_bracket_order(
    client: IBKRClient,
    contract,
    order_spec: OrderSpec,
    symbol: str,
    guard_warnings: List[str],
    wait_for_status_s: float,
    preview: OrderPreview,
) -> OrderResult:
    """
    Place a bracket order (entry + take profit + stop loss).

    Uses IBKR's parent-child order mechanism where child orders
    reference the parent's orderId.

    Args:
        client: Connected IBKRClient instance.
        contract: Resolved IBKR contract.
        order_spec: The bracket order specification.
        symbol: Symbol string for logging.
        guard_warnings: Any safety guard warnings.
        wait_for_status_s: Time to wait for initial status.

    Returns:
        OrderResult with orderIds list and orderRoles mapping.
    """
    logger.info(f"Placing BRACKET order for {symbol}")
    broker = get_broker_adapter(client)

    # Build the 3 bracket orders
    bracket_orders = _build_bracket_orders(order_spec)
    entry_order, tp_order, sl_order = bracket_orders

    # Set account if specified
    if order_spec.accountId:
        entry_order.account = order_spec.accountId
        tp_order.account = order_spec.accountId
        sl_order.account = order_spec.accountId

    # Place entry order first
    logger.info(
        f"Placing entry order: {entry_order.action} {entry_order.totalQuantity} @ "
        f"LMT {entry_order.lmtPrice}"
    )
    entry_trade = broker.place_order(contract, entry_order)

    # Brief wait to get the orderId
    broker.sleep(0.5)

    # Get the parent orderId for child orders
    parent_id = entry_trade.order.orderId
    if not parent_id:
        raise OrderPlacementError("Failed to get entry order ID for bracket")

    logger.info(f"Entry order placed with orderId={parent_id}")

    # Set parent ID on child orders
    tp_order.parentId = parent_id
    sl_order.parentId = parent_id

    # Place take profit order
    logger.info(
        f"Placing take profit order: {tp_order.action} {tp_order.totalQuantity} @ "
        f"LMT {tp_order.lmtPrice}"
    )
    tp_trade = broker.place_order(contract, tp_order)

    # Place stop loss order
    sl_price = sl_order.stopPrice if hasattr(sl_order, "stopPrice") else sl_order.auxPrice
    logger.info(
        f"Placing stop loss order: {sl_order.action} {sl_order.totalQuantity} @ STP {sl_price}"
    )
    sl_trade = broker.place_order(contract, sl_order)

    # Wait for status updates
    broker.sleep(wait_for_status_s)

    # Register all orders in our registry
    entry_id = _order_registry.register(entry_trade, f"{symbol}_entry", order_spec.clientOrderId)
    tp_id = _order_registry.register(tp_trade, f"{symbol}_tp")
    sl_id = _order_registry.register(sl_trade, f"{symbol}_sl")

    # Get entry order status
    entry_status = _trade_to_order_status(entry_trade, entry_id)
    entry_status.warnings.extend(guard_warnings)

    # Collect all order IDs
    all_order_ids = [entry_id, tp_id, sl_id]
    order_roles = {
        "entry": entry_id,
        "take_profit": tp_id,
        "stop_loss": sl_id,
    }

    # Determine result status based on entry order
    if entry_status.status in ("REJECTED", "CANCELLED"):
        result_status = "REJECTED"
    else:
        result_status = "ACCEPTED"

    logger.info(
        f"Bracket order placed: entry={entry_id}, tp={tp_id}, sl={sl_id}, "
        f"status={entry_status.status}"
    )

    try:
        config = get_config()
        config_snapshot = {
            "trading_mode": config.trading_mode,
            "orders_enabled": config.orders_enabled,
        }
        account_id = (
            order_spec.accountId
            or client.managed_accounts[0]
            if client.managed_accounts
            else "UNKNOWN"
        )

        bracket_trades = [
            ("entry", entry_id, entry_trade),
            ("take_profit", tp_id, tp_trade),
            ("stop_loss", sl_id, sl_trade),
        ]
        for role, leg_order_id, leg_trade in bracket_trades:
            leg_status = _trade_to_order_status(leg_trade, leg_order_id)
            save_order(
                order_id=leg_order_id,
                account_id=account_id,
                symbol=symbol,
                side=leg_trade.order.action,
                quantity=float(leg_trade.order.totalQuantity),
                order_type=leg_trade.order.orderType,
                status=leg_status.status,
                ibkr_order_id=str(leg_trade.order.orderId) if leg_trade.order.orderId else None,
                preview_data=preview.model_dump() if role == "entry" else None,
                config_snapshot=config_snapshot,
                strategy_id=order_spec.strategyId,
                virtual_subaccount_id=order_spec.virtualSubaccountId,
            )

        record_audit_event(
            event_type="ORDER_SUBMIT",
            event_data={
                "order_id": entry_id,
                "ibkr_order_id": str(entry_trade.order.orderId) if entry_trade.order.orderId else None,
                "symbol": symbol,
                "side": order_spec.side,
                "quantity": order_spec.quantity,
                "order_type": order_spec.orderType,
                "status": entry_status.status,
                "result_status": result_status,
                "strategy_id": order_spec.strategyId,
                "virtual_subaccount_id": order_spec.virtualSubaccountId
                or order_spec.strategyId,
                "order_ids": all_order_ids,
                "order_roles": order_roles,
            },
            account_id=account_id,
            strategy_id=order_spec.strategyId,
            virtual_subaccount_id=order_spec.virtualSubaccountId,
        )
    except Exception as e:
        logger.warning(f"Failed to record bracket order placement in audit: {e}")

    return OrderResult(
        orderId=entry_id,
        clientOrderId=order_spec.clientOrderId,
        status=result_status,
        orderStatus=entry_status,
        errors=[],
        orderIds=all_order_ids,
        orderRoles=order_roles,
    )


def cancel_order(
    client: IBKRClient,
    order_id: str,
    wait_for_cancel_s: float = 3.0,
) -> CancelResult:
    """
    Cancel an order by order_id.

    Args:
        client: Connected IBKRClient instance.
        order_id: Our order identifier from place_order.
        wait_for_cancel_s: Time to wait for cancellation confirmation.

    Returns:
        CancelResult with status.

    Raises:
        OrderNotFoundError: If order cannot be found.
        OrderCancelError: If cancellation fails.
    """
    logger.info(f"Cancel request for order {order_id}")

    client.ensure_connected()
    broker = get_broker_adapter(client)

    # Look up trade in our registry
    trade = _order_registry.lookup(order_id)

    if trade is None:
        # Try to find in IBKR's open trades
        trade = _find_trade_in_ibkr(client, order_id)

    if trade is None:
        logger.warning(f"Order {order_id} not found")
        return CancelResult(
            orderId=order_id,
            status="NOT_FOUND",
            message=f"Order {order_id} not found in registry or open trades",
        )

    # Check if already filled
    if trade.orderStatus and trade.orderStatus.status == "Filled":
        logger.info(f"Order {order_id} is already filled")
        return CancelResult(
            orderId=order_id,
            status="ALREADY_FILLED",
            message="Order has already been filled",
        )

    # Check if already cancelled
    if trade.orderStatus and trade.orderStatus.status in ("Cancelled", "ApiCancelled", "Inactive"):
        logger.info(f"Order {order_id} is already cancelled")
        return CancelResult(
            orderId=order_id,
            status="CANCELLED",
            message="Order was already cancelled",
        )

    try:
        # Cancel the order
        broker.cancel_order(trade.order)

        # Wait for cancellation
        broker.sleep(wait_for_cancel_s)

        # Check final status
        final_status = trade.orderStatus.status if trade.orderStatus else "Unknown"

        if final_status in ("Cancelled", "ApiCancelled", "Inactive"):
            logger.info(f"Order {order_id} cancelled successfully")

            # Update order status in database and record audit event
            try:
                update_order_status(order_id, "CANCELLED")
                record_audit_event(
                    event_type="ORDER_CANCELLED",
                    event_data={
                        "order_id": order_id,
                        "ibkr_status": final_status,
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to record order cancellation in audit: {e}")

            return CancelResult(
                orderId=order_id,
                status="CANCELLED",
                message=f"Order cancelled (IBKR status: {final_status})",
            )
        else:
            logger.warning(f"Order {order_id} cancel requested but status is {final_status}")
            return CancelResult(
                orderId=order_id,
                status="REJECTED",
                message=f"Cancel may have failed - current status: {final_status}",
            )

    except Exception as e:
        logger.error(f"Cancel failed for order {order_id}: {e}")
        raise OrderCancelError(f"Failed to cancel order {order_id}: {e}") from e


def get_order_status(
    client: IBKRClient,
    order_id: str,
) -> OrderStatus:
    """
    Get current status of an order.

    Args:
        client: Connected IBKRClient instance.
        order_id: Our order identifier from place_order.

    Returns:
        OrderStatus with current state.

    Raises:
        OrderNotFoundError: If order cannot be found.
    """
    logger.debug(f"Getting status for order {order_id}")

    client.ensure_connected()
    broker = get_broker_adapter(client)

    # Allow event loop to process updates
    broker.sleep(0.1)

    # Look up trade in our registry
    trade = _order_registry.lookup(order_id)

    if trade is None:
        # Try to find in IBKR's trades
        trade = _find_trade_in_ibkr(client, order_id)

    if trade is None:
        raise OrderNotFoundError(f"Order {order_id} not found")

    return _trade_to_order_status(trade, order_id)


def _find_trade_in_ibkr(client: IBKRClient, order_id: str) -> Optional[Trade]:
    """
    Search IBKR's trades for an order by our order_id.

    Args:
        client: Connected IBKRClient
        order_id: Our order identifier

    Returns:
        Trade if found, None otherwise
    """
    broker = get_broker_adapter(client)

    # Check open trades first
    for trade in broker.open_trades():
        perm_id = str(trade.order.permId) if trade.order.permId else ""
        ord_id = str(trade.order.orderId) if trade.order.orderId else ""
        if order_id == perm_id or order_id == ord_id or order_id == f"ord_{ord_id}":
            return trade

    # Check all trades (includes completed)
    for trade in broker.trades():
        perm_id = str(trade.order.permId) if trade.order.permId else ""
        ord_id = str(trade.order.orderId) if trade.order.orderId else ""
        if order_id == perm_id or order_id == ord_id or order_id == f"ord_{ord_id}":
            return trade

    return None


def _find_trade_by_client_order_id(client: IBKRClient, client_order_id: str) -> Optional[Trade]:
    """Search IBKR trades for a matching orderRef."""
    broker = get_broker_adapter(client)

    for trade in broker.open_trades():
        if getattr(trade.order, "orderRef", None) == client_order_id:
            return trade

    for trade in broker.trades():
        if getattr(trade.order, "orderRef", None) == client_order_id:
            return trade

    return None


def get_open_orders(client: IBKRClient) -> List[Dict]:
    """
    Get all open orders for this session.

    Args:
        client: Connected IBKRClient instance.

    Returns:
        List of order metadata dicts.
    """
    client.ensure_connected()
    broker = get_broker_adapter(client)
    broker.sleep(0.1)

    orders = []
    for trade in broker.open_trades():
        perm_id = str(trade.order.permId) if trade.order.permId else None
        order_id = perm_id or f"ord_{trade.order.orderId}"

        orders.append(
            {
                "order_id": order_id,
                "client_order_id": getattr(trade.order, "orderRef", None),
                "symbol": trade.contract.symbol if trade.contract else "Unknown",
                "side": trade.order.action,
                "quantity": trade.order.totalQuantity,
                "order_type": trade.order.orderType,
                "status": trade.orderStatus.status if trade.orderStatus else "Unknown",
                "filled": trade.orderStatus.filled if trade.orderStatus else 0,
                "remaining": trade.orderStatus.remaining
                if trade.orderStatus
                else trade.order.totalQuantity,
            }
        )

    return orders


def cancel_order_set(
    client: IBKRClient,
    order_ids: List[str],
    wait_for_cancel_s: float = 3.0,
) -> CancelResult:
    """
    Cancel multiple orders as a set (e.g., all legs of a bracket order).

    Attempts to cancel all specified orders. Returns success if all
    cancellable orders are cancelled (already filled orders don't fail).

    Args:
        client: Connected IBKRClient instance.
        order_ids: List of order identifiers to cancel.
        wait_for_cancel_s: Time to wait for cancellation confirmation.

    Returns:
        CancelResult with aggregate status:
        - CANCELLED: All cancellable orders cancelled
        - REJECTED: At least one cancel failed (not counting already filled)
        - NOT_FOUND: No orders found

    Raises:
        OrderCancelError: If cancellation fails unexpectedly.
    """
    if not order_ids:
        return CancelResult(
            orderId="",
            status="NOT_FOUND",
            message="No order IDs provided",
        )

    logger.info(f"Cancel request for order set: {order_ids}")

    client.ensure_connected()

    results = []
    all_not_found = True
    any_rejected = False

    for order_id in order_ids:
        try:
            result = cancel_order(client, order_id, wait_for_cancel_s=wait_for_cancel_s)
            results.append(result)

            if result.status != "NOT_FOUND":
                all_not_found = False

            if result.status == "REJECTED":
                any_rejected = True

        except OrderCancelError as e:
            logger.warning(f"Cancel failed for {order_id}: {e}")
            results.append(
                CancelResult(
                    orderId=order_id,
                    status="REJECTED",
                    message=str(e),
                )
            )
            any_rejected = True
            all_not_found = False

    # Determine aggregate status
    if all_not_found:
        status = "NOT_FOUND"
        message = "No orders found in set"
    elif any_rejected:
        status = "REJECTED"
        cancelled = sum(1 for r in results if r.status == "CANCELLED")
        rejected = sum(1 for r in results if r.status == "REJECTED")
        message = f"Partially cancelled: {cancelled} cancelled, {rejected} failed"
    else:
        status = "CANCELLED"
        cancelled = sum(1 for r in results if r.status == "CANCELLED")
        filled = sum(1 for r in results if r.status == "ALREADY_FILLED")
        message = f"Order set cancelled: {cancelled} cancelled, {filled} already filled"

    # Use first order_id as primary identifier
    primary_order_id = order_ids[0] if order_ids else ""

    logger.info(f"Order set cancel complete: {status} - {message}")

    return CancelResult(
        orderId=primary_order_id,
        status=status,
        message=message,
    )


def get_order_set_status(
    client: IBKRClient,
    order_ids: List[str],
) -> List[OrderStatus]:
    """
    Get status of multiple orders (e.g., all legs of a bracket order).

    Args:
        client: Connected IBKRClient instance.
        order_ids: List of order identifiers.

    Returns:
        List of OrderStatus for each order found.
        Orders not found are omitted (check list length).

    Raises:
        OrderNotFoundError: Only if ALL orders not found.
    """
    if not order_ids:
        return []

    logger.debug(f"Getting status for order set: {order_ids}")

    client.ensure_connected()
    broker = get_broker_adapter(client)
    broker.sleep(0.1)

    statuses = []
    not_found = []

    for order_id in order_ids:
        try:
            status = get_order_status(client, order_id)
            statuses.append(status)
        except OrderNotFoundError:
            not_found.append(order_id)
            logger.debug(f"Order {order_id} not found in set status query")

    # If ALL orders not found, raise exception
    if len(not_found) == len(order_ids):
        raise OrderNotFoundError(f"None of the orders found: {order_ids}")

    # Log partial not found as debug (normal for filled/cancelled bracket legs)
    if not_found:
        logger.debug(f"Some orders not found: {not_found}")

    return statuses
