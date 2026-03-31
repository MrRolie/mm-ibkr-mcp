"""
Tests for advanced order types (Phase 4.5).

Tests validation and building of:
- Trailing stop orders (TRAIL, TRAIL_LIMIT)
- Bracket orders (entry + take profit + stop loss)
- Market-on-close (MOC) and Market-on-open (OPG)
- OCA (One-Cancels-All) groups
- cancel_order_set and get_order_set_status functions

Run these tests:
    pytest tests/test_orders_advanced.py -v
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from ibkr_core.models import (
    CancelResult,
    OrderLeg,
    OrderPreview,
    OrderResult,
    OrderSpec,
    OrderStatus,
    SymbolSpec,
)
from ibkr_core.orders import (
    OrderNotFoundError,
    OrderValidationError,
    _apply_oca,
    _build_bracket_orders,
    _build_ib_order,
    _get_opposite_side,
    cancel_order_set,
    get_order_set_status,
    place_order,
    validate_order_spec,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def valid_symbol_spec():
    """Create a valid SymbolSpec for testing."""
    return SymbolSpec(
        symbol="AAPL",
        securityType="STK",
        exchange="SMART",
        currency="USD",
    )


@pytest.fixture
def futures_symbol_spec():
    """Create a futures SymbolSpec for testing."""
    return SymbolSpec(
        symbol="MES",
        securityType="FUT",
        exchange="GLOBEX",
        currency="USD",
    )


# =============================================================================
# Trailing Stop Order Tests
# =============================================================================


class TestTrailingStopValidation:
    """Test validation for trailing stop orders."""

    def test_trail_with_amount_valid(self, valid_symbol_spec):
        """Test valid trailing stop with amount."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
            trailingAmount=2.50,
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_trail_with_percent_valid(self, valid_symbol_spec):
        """Test valid trailing stop with percentage."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
            trailingPercent=5.0,
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_trail_with_stop_price_valid(self, valid_symbol_spec):
        """Test valid trailing stop with initial stop price."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
            trailingAmount=2.50,
            trailStopPrice=195.00,
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_trail_missing_both_params(self, valid_symbol_spec):
        """Test trailing stop without amount or percent."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "trailingAmount OR trailingPercent" in errors[0]

    def test_trail_with_both_params(self, valid_symbol_spec):
        """Test trailing stop with both amount and percent (invalid)."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
            trailingAmount=2.50,
            trailingPercent=5.0,
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "cannot have both" in errors[0]


class TestTrailingStopLimitValidation:
    """Test validation for trailing stop-limit orders."""

    def test_trail_limit_with_amount_valid(self, valid_symbol_spec):
        """Test valid trailing stop-limit with amount."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL_LIMIT",
            trailingAmount=2.50,
            limitPrice=0.50,  # Offset from stop
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_trail_limit_with_percent_valid(self, valid_symbol_spec):
        """Test valid trailing stop-limit with percentage."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL_LIMIT",
            trailingPercent=5.0,
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_trail_limit_missing_trail_params(self, valid_symbol_spec):
        """Test trailing stop-limit without trailing params."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL_LIMIT",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "trailingAmount OR trailingPercent" in errors[0]


class TestTrailingStopOrderBuilding:
    """Test building trailing stop IB orders."""

    def test_build_trail_order_with_amount(self, valid_symbol_spec):
        """Test building trailing stop with amount."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
            trailingAmount=2.50,
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.orderType == "TRAIL"
        assert ib_order.action == "SELL"
        assert ib_order.totalQuantity == 100
        assert ib_order.auxPrice == 2.50  # Trailing amount

    def test_build_trail_order_with_percent(self, valid_symbol_spec):
        """Test building trailing stop with percentage."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
            trailingPercent=5.0,
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.orderType == "TRAIL"
        assert ib_order.trailingPercent == 5.0

    def test_build_trail_order_with_stop_price(self, valid_symbol_spec):
        """Test building trailing stop with initial stop price."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL",
            trailingAmount=2.50,
            trailStopPrice=195.00,
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.trailStopPrice == 195.00

    def test_build_trail_limit_order(self, valid_symbol_spec):
        """Test building trailing stop-limit order."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="TRAIL_LIMIT",
            trailingAmount=2.50,
            limitPrice=0.50,
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.orderType == "TRAIL LIMIT"
        assert ib_order.auxPrice == 2.50
        assert ib_order.lmtPriceOffset == 0.50


# =============================================================================
# Bracket Order Tests
# =============================================================================


class TestBracketOrderValidation:
    """Test validation for bracket orders."""

    def test_bracket_order_valid_buy(self, valid_symbol_spec):
        """Test valid buy bracket order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=210.00,
            stopLossPrice=185.00,
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_bracket_order_valid_sell(self, valid_symbol_spec):
        """Test valid sell (short) bracket order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=180.00,  # Below entry for short
            stopLossPrice=205.00,  # Above entry for short
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_bracket_missing_entry_price(self, valid_symbol_spec):
        """Test bracket without entry limit price."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            takeProfitPrice=210.00,
            stopLossPrice=185.00,
        )
        errors = validate_order_spec(order)
        assert any("limit price for the entry" in e for e in errors)

    def test_bracket_missing_take_profit(self, valid_symbol_spec):
        """Test bracket without take profit price."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            stopLossPrice=185.00,
        )
        errors = validate_order_spec(order)
        assert any("take profit price" in e for e in errors)

    def test_bracket_missing_stop_loss(self, valid_symbol_spec):
        """Test bracket without stop loss price."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=210.00,
        )
        errors = validate_order_spec(order)
        assert any("stop loss price" in e for e in errors)

    def test_bracket_buy_invalid_take_profit(self, valid_symbol_spec):
        """Test buy bracket with take profit below entry."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=190.00,  # Below entry - invalid for long
            stopLossPrice=185.00,
        )
        errors = validate_order_spec(order)
        assert any("take profit price must be greater" in e for e in errors)

    def test_bracket_buy_invalid_stop_loss(self, valid_symbol_spec):
        """Test buy bracket with stop loss above entry."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=210.00,
            stopLossPrice=200.00,  # Above entry - invalid for long
        )
        errors = validate_order_spec(order)
        assert any("stop loss price must be less" in e for e in errors)

    def test_bracket_sell_invalid_take_profit(self, valid_symbol_spec):
        """Test sell bracket with take profit above entry."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=200.00,  # Above entry - invalid for short
            stopLossPrice=205.00,
        )
        errors = validate_order_spec(order)
        assert any("take profit price must be less" in e for e in errors)

    def test_bracket_sell_invalid_stop_loss(self, valid_symbol_spec):
        """Test sell bracket with stop loss below entry."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=180.00,
            stopLossPrice=190.00,  # Below entry - invalid for short
        )
        errors = validate_order_spec(order)
        assert any("stop loss price must be greater" in e for e in errors)


class TestBracketOrderBuilding:
    """Test building bracket orders."""

    def test_build_bracket_orders_buy(self, valid_symbol_spec):
        """Test building buy bracket order set."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=210.00,
            stopLossPrice=185.00,
        )
        orders = _build_bracket_orders(order_spec)

        assert len(orders) == 3
        entry, tp, sl = orders

        # Entry order
        assert entry.action == "BUY"
        assert entry.totalQuantity == 100
        assert entry.lmtPrice == 195.00
        assert entry.transmit is False

        # Take profit
        assert tp.action == "SELL"
        assert tp.totalQuantity == 100
        assert tp.lmtPrice == 210.00
        assert tp.ocaGroup is not None

        # Stop loss
        assert sl.action == "SELL"
        assert sl.totalQuantity == 100
        assert sl.auxPrice == 185.00  # ib_insync uses auxPrice for stop price
        assert sl.ocaGroup == tp.ocaGroup

    def test_build_bracket_orders_sell(self, valid_symbol_spec):
        """Test building sell bracket order set."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=50,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=180.00,
            stopLossPrice=205.00,
        )
        orders = _build_bracket_orders(order_spec)

        assert len(orders) == 3
        entry, tp, sl = orders

        # Entry order
        assert entry.action == "SELL"
        assert entry.totalQuantity == 50
        assert entry.lmtPrice == 195.00

        # Take profit (opposite side = BUY)
        assert tp.action == "BUY"
        assert tp.lmtPrice == 180.00

        # Stop loss (opposite side = BUY)
        assert sl.action == "BUY"
        assert sl.auxPrice == 205.00  # ib_insync uses auxPrice for stop price

    def test_build_bracket_with_stop_limit(self, valid_symbol_spec):
        """Test building bracket with stop-limit for stop loss."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="BRACKET",
            limitPrice=195.00,
            takeProfitPrice=210.00,
            stopLossPrice=185.00,
            stopLossLimitPrice=184.00,
        )
        orders = _build_bracket_orders(order_spec)

        _, _, sl = orders
        assert sl.auxPrice == 185.00  # ib_insync uses auxPrice for stop price
        assert sl.lmtPrice == 184.00

    def test_build_bracket_invalid_order_type(self, valid_symbol_spec):
        """Test _build_bracket_orders rejects non-BRACKET orders."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="LMT",
            limitPrice=195.00,
        )
        with pytest.raises(OrderValidationError):
            _build_bracket_orders(order_spec)

    def test_place_bracket_order_persists_and_audits_all_legs(self, valid_symbol_spec):
        """Bracket placement should persist each leg and emit one submit audit event."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=2,
            orderType="BRACKET",
            limitPrice=195.0,
            takeProfitPrice=205.0,
            stopLossPrice=190.0,
            tif="DAY",
            clientOrderId="bracket-123",
            strategyId="deploy_verify",
        )

        mock_client = MagicMock()
        mock_client.ensure_connected = MagicMock()
        mock_client.managed_accounts = ["DU123456"]
        mock_client.ib = MagicMock()
        mock_client.ib.sleep = MagicMock()

        contract = MagicMock()
        contract.conId = 101
        contract.symbol = "AAPL"

        def _trade(order_id: int, order_type: str, status: str = "Submitted"):
            trade = MagicMock()
            trade.order.orderId = order_id
            trade.order.permId = 900000 + order_id
            trade.order.clientId = 1
            trade.order.action = "BUY" if order_id == 1 else "SELL"
            trade.order.totalQuantity = 2
            trade.order.orderType = order_type
            trade.contract = contract
            trade.orderStatus = MagicMock()
            trade.orderStatus.status = status
            trade.orderStatus.filled = 0
            trade.orderStatus.remaining = 2
            trade.orderStatus.avgFillPrice = 0
            trade.log = []
            return trade

        entry_trade = _trade(1, "LMT")
        tp_trade = _trade(2, "LMT")
        sl_trade = _trade(3, "STP")
        mock_client.ib.placeOrder.side_effect = [entry_trade, tp_trade, sl_trade]

        mock_quote = MagicMock()
        mock_quote.bid = 194.5
        mock_quote.ask = 195.5
        mock_quote.last = 195.0

        with patch("ibkr_core.orders.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(
                orders_enabled=True,
                trading_mode="paper",
            )
            with patch("ibkr_core.orders.resolve_contract", return_value=contract):
                with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                    with patch("ibkr_core.orders.save_order") as mock_save_order:
                        with patch("ibkr_core.orders.record_audit_event") as mock_record_audit:
                            result = place_order(mock_client, order_spec)

        assert result.status == "ACCEPTED"
        assert len(result.orderIds) == 3
        assert result.orderRoles["entry"] == result.orderId
        assert mock_save_order.call_count == 3

        submit_calls = [
            call.kwargs
            for call in mock_record_audit.call_args_list
            if call.kwargs.get("event_type") == "ORDER_SUBMIT"
        ]
        assert len(submit_calls) == 1
        submit_event = submit_calls[0]["event_data"]
        assert submit_event["order_ids"] == result.orderIds
        assert submit_event["order_roles"] == result.orderRoles


# =============================================================================
# MOC/OPG Order Tests
# =============================================================================


class TestMOCOrderValidation:
    """Test validation for market-on-close orders."""

    def test_moc_order_valid(self, valid_symbol_spec):
        """Test valid MOC order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="MOC",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_moc_order_building(self, valid_symbol_spec):
        """Test building MOC order."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="MOC",
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.orderType == "MOC"
        assert ib_order.tif == "DAY"
        assert ib_order.action == "BUY"
        assert ib_order.totalQuantity == 100


class TestOPGOrderValidation:
    """Test validation for market-on-open orders."""

    def test_opg_order_valid(self, valid_symbol_spec):
        """Test valid OPG order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=50,
            orderType="OPG",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_opg_order_building(self, valid_symbol_spec):
        """Test building OPG order."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=50,
            orderType="OPG",
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.orderType == "MKT"
        assert ib_order.tif == "OPG"
        assert ib_order.action == "SELL"
        assert ib_order.totalQuantity == 50


# =============================================================================
# OCA (One-Cancels-All) Tests
# =============================================================================


class TestOCAFunctionality:
    """Test OCA group functionality."""

    def test_apply_oca_to_order(self, valid_symbol_spec):
        """Test applying OCA settings to an order."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="LMT",
            limitPrice=195.00,
            ocaGroup="my_oca_group",
            ocaType=1,
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.ocaGroup == "my_oca_group"
        assert ib_order.ocaType == 1

    def test_apply_oca_default_type(self, valid_symbol_spec):
        """Test OCA defaults to type 1 if not specified."""
        order_spec = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=100,
            orderType="LMT",
            limitPrice=195.00,
            ocaGroup="my_oca_group",
        )
        ib_order = _build_ib_order(order_spec)

        assert ib_order.ocaGroup == "my_oca_group"
        assert ib_order.ocaType == 1

    def test_oca_type_validation(self):
        """Test OCA type must be 1, 2, or 3."""
        with pytest.raises(ValidationError):
            OrderSpec(
                instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
                side="BUY",
                quantity=100,
                orderType="LMT",
                limitPrice=195.00,
                ocaGroup="test",
                ocaType=4,  # Invalid
            )


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Test helper functions."""

    def test_get_opposite_side_buy(self):
        """Test getting opposite of BUY."""
        assert _get_opposite_side("BUY") == "SELL"

    def test_get_opposite_side_sell(self):
        """Test getting opposite of SELL."""
        assert _get_opposite_side("SELL") == "BUY"

    def test_get_opposite_side_lowercase(self):
        """Test case insensitivity."""
        assert _get_opposite_side("buy") == "SELL"
        assert _get_opposite_side("sell") == "BUY"


# =============================================================================
# Order Set Operations Tests (Mocked)
# =============================================================================


class TestCancelOrderSet:
    """Test cancel_order_set function."""

    def test_cancel_empty_list(self):
        """Test cancel with empty order ID list."""
        mock_client = MagicMock()
        result = cancel_order_set(mock_client, [])
        assert result.status == "NOT_FOUND"
        assert "No order IDs provided" in result.message

    @patch("ibkr_core.orders.cancel_order")
    def test_cancel_all_success(self, mock_cancel):
        """Test successful cancellation of all orders."""
        mock_client = MagicMock()
        mock_cancel.side_effect = [
            CancelResult(orderId="ord_1", status="CANCELLED", message="OK"),
            CancelResult(orderId="ord_2", status="CANCELLED", message="OK"),
        ]

        result = cancel_order_set(mock_client, ["ord_1", "ord_2"])

        assert result.status == "CANCELLED"
        assert "2 cancelled" in result.message
        assert mock_cancel.call_count == 2

    @patch("ibkr_core.orders.cancel_order")
    def test_cancel_some_already_filled(self, mock_cancel):
        """Test cancellation when some orders already filled."""
        mock_client = MagicMock()
        mock_cancel.side_effect = [
            CancelResult(orderId="ord_1", status="CANCELLED", message="OK"),
            CancelResult(orderId="ord_2", status="ALREADY_FILLED", message="Filled"),
        ]

        result = cancel_order_set(mock_client, ["ord_1", "ord_2"])

        assert result.status == "CANCELLED"
        assert "1 cancelled" in result.message
        assert "1 already filled" in result.message

    @patch("ibkr_core.orders.cancel_order")
    def test_cancel_all_not_found(self, mock_cancel):
        """Test cancellation when all orders not found."""
        mock_client = MagicMock()
        mock_cancel.side_effect = [
            CancelResult(orderId="ord_1", status="NOT_FOUND", message="Not found"),
            CancelResult(orderId="ord_2", status="NOT_FOUND", message="Not found"),
        ]

        result = cancel_order_set(mock_client, ["ord_1", "ord_2"])

        assert result.status == "NOT_FOUND"

    @patch("ibkr_core.orders.cancel_order")
    def test_cancel_partial_rejection(self, mock_cancel):
        """Test cancellation with partial rejection."""
        mock_client = MagicMock()
        mock_cancel.side_effect = [
            CancelResult(orderId="ord_1", status="CANCELLED", message="OK"),
            CancelResult(orderId="ord_2", status="REJECTED", message="Failed"),
        ]

        result = cancel_order_set(mock_client, ["ord_1", "ord_2"])

        assert result.status == "REJECTED"
        assert "1 cancelled" in result.message
        assert "1 failed" in result.message


class TestGetOrderSetStatus:
    """Test get_order_set_status function."""

    def test_status_empty_list(self):
        """Test status with empty order ID list."""
        mock_client = MagicMock()
        result = get_order_set_status(mock_client, [])
        assert result == []

    @patch("ibkr_core.orders.get_order_status")
    def test_status_all_found(self, mock_status):
        """Test status when all orders found."""
        mock_client = MagicMock()
        mock_client.ensure_connected = MagicMock()
        mock_client.ib = MagicMock()
        mock_client.ib.sleep = MagicMock()

        status1 = OrderStatus(
            orderId="ord_1",
            status="SUBMITTED",
            filledQuantity=0,
            remainingQuantity=100,
            avgFillPrice=0,
            lastUpdate=datetime.now(timezone.utc),
        )
        status2 = OrderStatus(
            orderId="ord_2",
            status="SUBMITTED",
            filledQuantity=0,
            remainingQuantity=100,
            avgFillPrice=0,
            lastUpdate=datetime.now(timezone.utc),
        )
        mock_status.side_effect = [status1, status2]

        result = get_order_set_status(mock_client, ["ord_1", "ord_2"])

        assert len(result) == 2
        assert result[0].orderId == "ord_1"
        assert result[1].orderId == "ord_2"

    @patch("ibkr_core.orders.get_order_status")
    def test_status_some_not_found(self, mock_status):
        """Test status when some orders not found."""
        mock_client = MagicMock()
        mock_client.ensure_connected = MagicMock()
        mock_client.ib = MagicMock()
        mock_client.ib.sleep = MagicMock()

        status1 = OrderStatus(
            orderId="ord_1",
            status="SUBMITTED",
            filledQuantity=0,
            remainingQuantity=100,
            avgFillPrice=0,
            lastUpdate=datetime.now(timezone.utc),
        )
        mock_status.side_effect = [status1, OrderNotFoundError("not found")]

        result = get_order_set_status(mock_client, ["ord_1", "ord_2"])

        assert len(result) == 1
        assert result[0].orderId == "ord_1"

    @patch("ibkr_core.orders.get_order_status")
    def test_status_all_not_found(self, mock_status):
        """Test status raises when all orders not found."""
        mock_client = MagicMock()
        mock_client.ensure_connected = MagicMock()
        mock_client.ib = MagicMock()
        mock_client.ib.sleep = MagicMock()

        mock_status.side_effect = [
            OrderNotFoundError("not found"),
            OrderNotFoundError("not found"),
        ]

        with pytest.raises(OrderNotFoundError):
            get_order_set_status(mock_client, ["ord_1", "ord_2"])


# =============================================================================
# OrderLeg Model Tests
# =============================================================================


class TestOrderLegModel:
    """Test OrderLeg model."""

    def test_order_leg_creation(self):
        """Test creating an OrderLeg."""
        leg = OrderLeg(
            role="entry",
            orderType="LMT",
            side="BUY",
            quantity=100,
            limitPrice=195.00,
            tif="DAY",
            estimatedPrice=195.00,
            estimatedNotional=19500.00,
        )
        assert leg.role == "entry"
        assert leg.orderType == "LMT"
        assert leg.side == "BUY"
        assert leg.quantity == 100
        assert leg.limitPrice == 195.00

    def test_order_leg_with_stop(self):
        """Test OrderLeg with stop price."""
        leg = OrderLeg(
            role="stop_loss",
            orderType="STP",
            side="SELL",
            quantity=100,
            stopPrice=185.00,
            tif="GTC",
        )
        assert leg.role == "stop_loss"
        assert leg.stopPrice == 185.00

    def test_order_leg_with_trailing(self):
        """Test OrderLeg with trailing params."""
        leg = OrderLeg(
            role="child",
            orderType="TRAIL",
            side="SELL",
            quantity=100,
            trailingPercent=5.0,
            tif="GTC",
        )
        assert leg.trailingPercent == 5.0


# =============================================================================
# OrderResult with Multi-leg Tests
# =============================================================================


class TestOrderResultMultiLeg:
    """Test OrderResult with multi-leg support."""

    def test_order_result_with_multiple_ids(self):
        """Test OrderResult with orderIds list."""
        result = OrderResult(
            orderId="ord_entry",
            status="ACCEPTED",
            orderIds=["ord_entry", "ord_tp", "ord_sl"],
            orderRoles={
                "entry": "ord_entry",
                "take_profit": "ord_tp",
                "stop_loss": "ord_sl",
            },
        )
        assert len(result.orderIds) == 3
        assert result.orderRoles["entry"] == "ord_entry"
        assert result.orderRoles["take_profit"] == "ord_tp"
        assert result.orderRoles["stop_loss"] == "ord_sl"

    def test_order_result_backward_compatible(self):
        """Test OrderResult works without multi-leg fields."""
        result = OrderResult(
            orderId="ord_1",
            status="ACCEPTED",
        )
        assert result.orderIds == []
        assert result.orderRoles == {}


# =============================================================================
# OrderPreview with Legs Tests
# =============================================================================


class TestOrderPreviewLegs:
    """Test OrderPreview with legs support."""

    def test_preview_with_legs(self, valid_symbol_spec):
        """Test OrderPreview with legs for bracket."""
        preview = OrderPreview(
            orderSpec=OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=100,
                orderType="BRACKET",
                limitPrice=195.00,
                takeProfitPrice=210.00,
                stopLossPrice=185.00,
            ),
            estimatedPrice=195.00,
            estimatedNotional=19500.00,
            legs=[
                OrderLeg(
                    role="entry",
                    orderType="LMT",
                    side="BUY",
                    quantity=100,
                    limitPrice=195.00,
                    estimatedNotional=19500.00,
                ),
                OrderLeg(
                    role="take_profit",
                    orderType="LMT",
                    side="SELL",
                    quantity=100,
                    limitPrice=210.00,
                    estimatedNotional=21000.00,
                ),
                OrderLeg(
                    role="stop_loss",
                    orderType="STP",
                    side="SELL",
                    quantity=100,
                    stopPrice=185.00,
                    estimatedNotional=18500.00,
                ),
            ],
            totalNotional=19500.00,
        )
        assert len(preview.legs) == 3
        assert preview.legs[0].role == "entry"
        assert preview.legs[1].role == "take_profit"
        assert preview.legs[2].role == "stop_loss"
        assert preview.totalNotional == 19500.00

    def test_preview_backward_compatible(self, valid_symbol_spec):
        """Test OrderPreview works without legs."""
        preview = OrderPreview(
            orderSpec=OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=100,
                orderType="LMT",
                limitPrice=195.00,
            ),
            estimatedPrice=195.00,
            estimatedNotional=19500.00,
        )
        assert preview.legs == []
        assert preview.totalNotional is None
