"""
Tests for order safety mechanisms.

Verifies that ORDERS_ENABLED=false prevents actual order placement.
Uses mocks to ensure no real IBKR calls are made.

Run these tests:
    pytest tests/test_orders_safety.py -v
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from ibkr_core.config import TradingDisabledError, reset_config
from ibkr_core.control import ControlState, get_control_path, write_control
from ibkr_core.models import OrderSpec, Quote, SymbolSpec
from ibkr_core.orders import (
    OrderRegistry,
    OrderResult,
    get_order_registry,
    place_order,
    preview_order,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_environment():
    """Reset config and environment before each test."""
    reset_config()

    # Save and clear relevant env vars
    old_env = {}
    env_keys = [
        "IBKR_GATEWAY_HOST",
        "PAPER_GATEWAY_PORT",
        "PAPER_CLIENT_ID",
        "LIVE_GATEWAY_PORT",
        "LIVE_CLIENT_ID",
        "TRADING_MODE",
        "ORDERS_ENABLED",
        "LIVE_TRADING_OVERRIDE_FILE",
    ]
    for key in env_keys:
        old_env[key] = os.environ.get(key)
        if key in os.environ:
            del os.environ[key]

    yield

    # Restore environment
    for key, value in old_env.items():
        if value is not None:
            os.environ[key] = value
        elif key in os.environ:
            del os.environ[key]
    reset_config()


def set_control_state(
    *,
    trading_mode: str = "paper",
    orders_enabled: bool = False,
    live_trading_override_file: str | None = None,
) -> None:
    """Write control.json for tests and reset config."""
    write_control(
        ControlState(
            trading_mode=trading_mode,
            orders_enabled=orders_enabled,
            live_trading_override_file=live_trading_override_file,
        ),
        base_dir=get_control_path().parent,
    )
    reset_config()


def write_raw_control(trading_mode: str, orders_enabled) -> None:
    """Write raw control.json for parsing tests."""
    control_path = get_control_path()
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text(
        json.dumps(
            {
                "trading_mode": trading_mode,
                "orders_enabled": orders_enabled,
                "dry_run": True,
                "live_trading_override_file": None,
            }
        ),
        encoding="utf-8",
    )
    reset_config()


@pytest.fixture
def mock_client():
    """Create a mock IBKRClient."""
    client = MagicMock()
    client.is_connected = True
    client.ensure_connected = MagicMock()
    client.ib = MagicMock()
    type(client).managed_accounts = PropertyMock(return_value=["DU123456"])
    return client


@pytest.fixture
def valid_symbol_spec():
    """Create a valid SymbolSpec."""
    return SymbolSpec(
        symbol="AAPL",
        securityType="STK",
        exchange="SMART",
        currency="USD",
    )


@pytest.fixture
def valid_order_spec(valid_symbol_spec):
    """Create a valid OrderSpec."""
    return OrderSpec(
        instrument=valid_symbol_spec,
        side="BUY",
        quantity=1,
        orderType="LMT",
        limitPrice=150.00,
        tif="DAY",
    )


@pytest.fixture
def mock_quote():
    """Create a mock Quote."""
    return Quote(
        symbol="AAPL",
        conId=265598,
        bid=249.50,
        ask=250.00,
        last=249.75,
        bidSize=100,
        askSize=200,
        lastSize=50,
        volume=1000000,
        timestamp=datetime.now(timezone.utc),
        source="IBKR_SNAPSHOT",
    )


@pytest.fixture
def mock_contract():
    """Create a mock Contract."""
    contract = MagicMock()
    contract.conId = 265598
    contract.symbol = "AAPL"
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.multiplier = "1"
    return contract


# =============================================================================
# Safety Tests - ORDERS_ENABLED=false
# =============================================================================


class TestOrdersDisabled:
    """Test that ORDERS_ENABLED=false prevents order placement."""

    def test_place_order_returns_simulated_when_disabled(
        self, mock_client, valid_order_spec, mock_quote, mock_contract
    ):
        """Test that place_order returns SIMULATED status when ORDERS_ENABLED=false."""
        set_control_state(trading_mode="paper", orders_enabled=False)

        # Mock the resolve_contract and get_quote functions
        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                result = place_order(mock_client, valid_order_spec)

        # Verify result
        assert result.status == "SIMULATED"
        assert result.orderId is None
        assert len(result.errors) > 0
        assert "orders_enabled=false" in result.errors[0].lower()

    def test_place_order_does_not_call_placeOrder_when_disabled(
        self, mock_client, valid_order_spec, mock_quote, mock_contract
    ):
        """Test that IBKR placeOrder is never called when ORDERS_ENABLED=false."""
        set_control_state(trading_mode="paper", orders_enabled=False)

        # Mock the resolve_contract and get_quote functions
        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                result = place_order(mock_client, valid_order_spec)

        # Verify placeOrder was NOT called
        mock_client.ib.placeOrder.assert_not_called()

        # Verify result is simulated
        assert result.status == "SIMULATED"

    def test_preview_order_works_when_disabled(
        self, mock_client, valid_order_spec, mock_quote, mock_contract
    ):
        """Test that preview_order still works when ORDERS_ENABLED=false."""
        set_control_state(trading_mode="paper", orders_enabled=False)

        # Mock the resolve_contract and get_quote functions
        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                preview = preview_order(mock_client, valid_order_spec)

        # Preview should work - it doesn't place orders
        assert preview is not None
        assert preview.orderSpec == valid_order_spec
        assert preview.estimatedPrice is not None

    def test_simulated_result_has_correct_structure(
        self, mock_client, valid_order_spec, mock_quote, mock_contract
    ):
        """Test that simulated OrderResult has correct structure."""
        set_control_state(trading_mode="paper", orders_enabled=False)

        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                result = place_order(mock_client, valid_order_spec)

        # Check structure
        assert isinstance(result, OrderResult)
        assert result.status == "SIMULATED"
        assert result.orderId is None
        assert result.orderStatus is None
        assert isinstance(result.errors, list)
        assert len(result.errors) == 1

    def test_multiple_orders_all_simulated_when_disabled(
        self, mock_client, valid_symbol_spec, mock_quote, mock_contract
    ):
        """Test that multiple orders all return SIMULATED when disabled."""
        set_control_state(trading_mode="paper", orders_enabled=False)

        orders = [
            OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=1,
                orderType="MKT",
                tif="DAY",
            ),
            OrderSpec(
                instrument=valid_symbol_spec,
                side="SELL",
                quantity=5,
                orderType="LMT",
                limitPrice=300.00,
                tif="GTC",
            ),
        ]

        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                results = [place_order(mock_client, o) for o in orders]

        # All should be simulated
        for result in results:
            assert result.status == "SIMULATED"
            mock_client.ib.placeOrder.assert_not_called()


# =============================================================================
# Safety Tests - ORDERS_ENABLED=true
# =============================================================================


class TestOrdersEnabled:
    """Test that ORDERS_ENABLED=true allows order placement."""

    def test_place_order_calls_placeOrder_when_enabled(
        self, mock_client, valid_order_spec, mock_quote, mock_contract
    ):
        """Test that IBKR placeOrder is called when ORDERS_ENABLED=true."""
        set_control_state(trading_mode="paper", orders_enabled=True)

        # Create a mock trade
        mock_trade = MagicMock()
        mock_trade.order.permId = 123456789
        mock_trade.order.orderId = 1
        mock_trade.order.clientId = 1
        mock_trade.order.action = "BUY"
        mock_trade.order.totalQuantity = 1
        mock_trade.order.orderType = "LMT"
        mock_trade.contract = mock_contract
        mock_trade.orderStatus = MagicMock()
        mock_trade.orderStatus.status = "Submitted"
        mock_trade.orderStatus.filled = 0
        mock_trade.orderStatus.remaining = 1
        mock_trade.orderStatus.avgFillPrice = 0
        mock_trade.log = []

        mock_client.ib.placeOrder.return_value = mock_trade

        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                result = place_order(mock_client, valid_order_spec)

        # Verify placeOrder WAS called
        mock_client.ib.placeOrder.assert_called_once()

        # Verify result is accepted
        assert result.status == "ACCEPTED"
        assert result.orderId is not None

    def test_place_order_returns_accepted_status(
        self, mock_client, valid_order_spec, mock_quote, mock_contract
    ):
        """Test that successful order placement returns ACCEPTED status."""
        set_control_state(trading_mode="paper", orders_enabled=True)

        # Create a mock trade
        mock_trade = MagicMock()
        mock_trade.order.permId = 123456789
        mock_trade.order.orderId = 1
        mock_trade.order.clientId = 1
        mock_trade.order.action = "BUY"
        mock_trade.order.totalQuantity = 1
        mock_trade.order.orderType = "LMT"
        mock_trade.contract = mock_contract
        mock_trade.orderStatus = MagicMock()
        mock_trade.orderStatus.status = "Submitted"
        mock_trade.orderStatus.filled = 0
        mock_trade.orderStatus.remaining = 1
        mock_trade.orderStatus.avgFillPrice = 0
        mock_trade.log = []

        mock_client.ib.placeOrder.return_value = mock_trade

        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                result = place_order(mock_client, valid_order_spec)

        assert result.status == "ACCEPTED"
        assert result.orderId is not None
        assert result.orderStatus is not None
        assert result.errors == []


# =============================================================================
# Order Registry Tests
# =============================================================================


class TestOrderRegistry:
    """Test the in-memory order registry."""

    def test_registry_register_and_lookup(self):
        """Test registering and looking up orders."""
        registry = OrderRegistry()

        # Create a mock trade
        mock_trade = MagicMock()
        mock_trade.order.permId = 123456789
        mock_trade.order.orderId = 1
        mock_trade.order.clientId = 1
        mock_trade.contract.conId = 265598

        # Register
        order_id = registry.register(mock_trade, "AAPL")

        # Lookup
        found_trade = registry.lookup(order_id)
        assert found_trade is mock_trade

        # Lookup metadata
        metadata = registry.lookup_metadata(order_id)
        assert metadata is not None
        assert metadata["symbol"] == "AAPL"
        assert metadata["perm_id"] == 123456789

    def test_registry_tracks_client_order_id(self):
        """Test client_order_id lookup for idempotent retries."""
        registry = OrderRegistry()

        mock_trade = MagicMock()
        mock_trade.order.permId = 123456789
        mock_trade.order.orderId = 1
        mock_trade.order.clientId = 1
        mock_trade.order.orderRef = "retry-123"
        mock_trade.contract.conId = 265598

        order_id = registry.register(mock_trade, "AAPL", client_order_id="retry-123")

        assert registry.lookup_order_id_by_client_order_id("retry-123") == order_id
        assert registry.lookup_by_client_order_id("retry-123") is mock_trade

    def test_registry_lookup_not_found(self):
        """Test that lookup returns None for unknown order."""
        registry = OrderRegistry()
        result = registry.lookup("unknown-order-id")
        assert result is None

    def test_registry_size(self):
        """Test registry size tracking."""
        registry = OrderRegistry()
        assert registry.size == 0

        mock_trade = MagicMock()
        mock_trade.order.permId = 1
        mock_trade.order.orderId = 1
        mock_trade.order.clientId = 1
        mock_trade.contract.conId = 1

        registry.register(mock_trade, "AAPL")
        assert registry.size == 1

        mock_trade2 = MagicMock()
        mock_trade2.order.permId = 2
        mock_trade2.order.orderId = 2
        mock_trade2.order.clientId = 1
        mock_trade2.contract.conId = 1

        registry.register(mock_trade2, "MSFT")
        assert registry.size == 2

    def test_registry_clear(self):
        """Test registry clear."""
        registry = OrderRegistry()

        mock_trade = MagicMock()
        mock_trade.order.permId = 1
        mock_trade.order.orderId = 1
        mock_trade.order.clientId = 1
        mock_trade.contract.conId = 1

        registry.register(mock_trade, "AAPL")
        assert registry.size == 1

        registry.clear()
        assert registry.size == 0

    def test_registry_all_orders(self):
        """Test getting all orders from registry."""
        registry = OrderRegistry()

        for i in range(3):
            mock_trade = MagicMock()
            mock_trade.order.permId = i + 1
            mock_trade.order.orderId = i + 1
            mock_trade.order.clientId = 1
            mock_trade.contract.conId = i + 1
            registry.register(mock_trade, f"SYM{i}")

        all_orders = registry.all_orders()
        assert len(all_orders) == 3

    def test_global_registry_singleton(self):
        """Test that get_order_registry returns consistent instance."""
        reg1 = get_order_registry()
        reg2 = get_order_registry()
        assert reg1 is reg2


# =============================================================================
# Validation Rejection Tests
# =============================================================================


class TestValidationRejection:
    """Test that invalid orders are rejected without calling IBKR."""

    def test_invalid_order_rejected_without_ibkr_call(self, mock_client, valid_symbol_spec):
        """Test that validation errors prevent IBKR call."""
        set_control_state(trading_mode="paper", orders_enabled=True)

        # Create invalid order - MKT with limit price
        invalid_order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="MKT",
            limitPrice=150.00,  # Invalid for MKT
            tif="DAY",
        )

        result = place_order(mock_client, invalid_order)

        # Should be rejected
        assert result.status == "REJECTED"
        assert len(result.errors) > 0
        assert "limit price" in result.errors[0].lower()

        # Should NOT have called placeOrder
        mock_client.ib.placeOrder.assert_not_called()

    def test_missing_limit_price_rejected(self, mock_client, valid_symbol_spec):
        """Test that LMT order without limit price is rejected."""
        set_control_state(trading_mode="paper", orders_enabled=True)

        invalid_order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=None,  # Required for LMT
            tif="DAY",
        )

        result = place_order(mock_client, invalid_order)

        assert result.status == "REJECTED"
        assert len(result.errors) > 0
        mock_client.ib.placeOrder.assert_not_called()


# =============================================================================
# Environment Variable Parsing Tests
# =============================================================================


class TestControlParsing:
    """Test parsing of orders_enabled values in control.json."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("yes", True),
            ("YES", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("no", False),
            ("NO", False),
            ("0", False),
            ("", False),
            ("invalid", False),
        ],
    )
    def test_orders_enabled_parsing(
        self, value, expected, mock_client, valid_order_spec, mock_quote, mock_contract
    ):
        """Test various orders_enabled values."""
        write_raw_control("paper", value)

        # Setup mock for enabled case
        if expected:
            mock_trade = MagicMock()
            mock_trade.order.permId = 123
            mock_trade.order.orderId = 1
            mock_trade.order.clientId = 1
            mock_trade.order.action = "BUY"
            mock_trade.order.totalQuantity = 1
            mock_trade.order.orderType = "LMT"
            mock_trade.contract = mock_contract
            mock_trade.orderStatus = MagicMock()
            mock_trade.orderStatus.status = "Submitted"
            mock_trade.orderStatus.filled = 0
            mock_trade.orderStatus.remaining = 1
            mock_trade.orderStatus.avgFillPrice = 0
            mock_trade.log = []
            mock_client.ib.placeOrder.return_value = mock_trade

        with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
            with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                result = place_order(mock_client, valid_order_spec)

        if expected:
            # Orders enabled - should attempt placement
            assert result.status in ("ACCEPTED", "REJECTED")
            mock_client.ib.placeOrder.assert_called()
        else:
            # Orders disabled - should be simulated
            assert result.status == "SIMULATED"


# =============================================================================
# Logging Tests
# =============================================================================


class TestSafetyLogging:
    """Test that safety events are logged appropriately."""

    def test_simulated_order_logged(
        self, mock_client, valid_order_spec, mock_quote, mock_contract, caplog
    ):
        """Test that simulated orders are logged."""
        import logging

        set_control_state(trading_mode="paper", orders_enabled=False)

        with caplog.at_level(logging.INFO):
            with patch("ibkr_core.orders.resolve_contract", return_value=mock_contract):
                with patch("ibkr_core.orders.get_quote", return_value=mock_quote):
                    place_order(mock_client, valid_order_spec)

        # Check log messages
        log_text = caplog.text.lower()
        assert "orders_enabled=false" in log_text or "simulated" in log_text
