"""
Tests for account positions retrieval.

Two test modes:
  - Unit tests: Test with mocked IBKR responses
  - Integration tests: Test against running IBKR Gateway

Run unit tests only:
    pytest tests/test_account_positions.py -m "not integration"

Run integration tests:
    pytest tests/test_account_positions.py -m integration

Run all:
    pytest tests/test_account_positions.py
"""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock

import pytest

from ibkr_core.account import AccountError, AccountPositionsError, get_account_status, get_positions
from ibkr_core.client import IBKRClient
from ibkr_core.config import reset_config
from ibkr_core.models import Position


@pytest.fixture(autouse=True)
def reset_environment():
    """Reset config before each test."""
    reset_config()

    old_env = {}
    env_keys = [
        "IBKR_GATEWAY_HOST",
        "PAPER_GATEWAY_PORT",
        "PAPER_CLIENT_ID",
        "LIVE_GATEWAY_PORT",
        "LIVE_CLIENT_ID",
        "TRADING_MODE",
        "ORDERS_ENABLED",
    ]
    for key in env_keys:
        old_env[key] = os.environ.get(key)
        if key in os.environ:
            del os.environ[key]

    yield

    for key, value in old_env.items():
        if value is not None:
            os.environ[key] = value
        elif key in os.environ:
            del os.environ[key]
    reset_config()


# =============================================================================
# Unit Tests - Position Model
# =============================================================================


class TestPositionModel:
    """Test Position Pydantic model."""

    def test_position_creation(self):
        """Test creating Position model."""
        position = Position(
            accountId="DU123456",
            symbol="AAPL",
            conId=265598,
            assetClass="STK",
            currency="USD",
            quantity=100.0,
            avgPrice=150.00,
            marketPrice=155.00,
            marketValue=15500.00,
            unrealizedPnl=500.00,
            realizedPnl=0.00,
        )

        assert position.accountId == "DU123456"
        assert position.symbol == "AAPL"
        assert position.conId == 265598
        assert position.assetClass == "STK"
        assert position.currency == "USD"
        assert position.quantity == 100.0
        assert position.avgPrice == 150.00
        assert position.marketPrice == 155.00
        assert position.marketValue == 15500.00
        assert position.unrealizedPnl == 500.00
        assert position.realizedPnl == 0.00

    def test_position_short(self):
        """Test Position with short quantity."""
        position = Position(
            accountId="DU123456",
            symbol="TSLA",
            conId=12345,
            assetClass="STK",
            currency="USD",
            quantity=-50.0,  # Short position
            avgPrice=200.00,
            marketPrice=190.00,
            marketValue=-9500.00,  # Negative for short
            unrealizedPnl=500.00,  # Profit on short
            realizedPnl=0.00,
        )

        assert position.quantity == -50.0
        assert position.marketValue == -9500.00

    def test_position_serialization(self):
        """Test Position serializes to dict correctly."""
        position = Position(
            accountId="DU123456",
            symbol="AAPL",
            conId=265598,
            assetClass="STK",
            currency="USD",
            quantity=100.0,
            avgPrice=150.00,
            marketPrice=155.00,
            marketValue=15500.00,
            unrealizedPnl=500.00,
            realizedPnl=0.00,
        )

        data = position.model_dump()

        assert data["accountId"] == "DU123456"
        assert data["symbol"] == "AAPL"
        assert data["conId"] == 265598
        assert data["quantity"] == 100.0

    def test_position_asset_class_validation(self):
        """Test Position asset class validation."""
        # Valid asset classes
        for asset_class in ["STK", "ETF", "FUT", "OPT", "FX", "CFD", "IND", "BOND"]:
            position = Position(
                accountId="DU123456",
                symbol="TEST",
                conId=1,
                assetClass=asset_class,
                currency="USD",
                quantity=1.0,
                avgPrice=100.0,
                marketPrice=100.0,
                marketValue=100.0,
                unrealizedPnl=0.0,
                realizedPnl=0.0,
            )
            assert position.assetClass == asset_class

    def test_position_invalid_asset_class_raises(self):
        """Test Position rejects invalid asset class."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            Position(
                accountId="DU123456",
                symbol="TEST",
                conId=1,
                assetClass="INVALID",
                currency="USD",
                quantity=1.0,
                avgPrice=100.0,
                marketPrice=100.0,
                marketValue=100.0,
                unrealizedPnl=0.0,
                realizedPnl=0.0,
            )

    def test_position_json_serializable(self):
        """Test Position is JSON serializable."""
        position = Position(
            accountId="DU123456",
            symbol="AAPL",
            conId=265598,
            assetClass="STK",
            currency="USD",
            quantity=100.0,
            avgPrice=150.00,
            marketPrice=155.00,
            marketValue=15500.00,
            unrealizedPnl=500.00,
            realizedPnl=0.00,
        )

        json_str = position.model_dump_json()
        assert "AAPL" in json_str
        assert "265598" in json_str


# =============================================================================
# Unit Tests - get_positions (Mocked)
# =============================================================================


class MockContract:
    """Mock for IBKR Contract object."""

    def __init__(
        self,
        symbol: str,
        conId: int,
        secType: str = "STK",
        currency: str = "USD",
        lastTradeDateOrContractMonth: str = "",
    ):
        self.symbol = symbol
        self.conId = conId
        self.secType = secType
        self.currency = currency
        self.lastTradeDateOrContractMonth = lastTradeDateOrContractMonth


class MockPosition:
    """Mock for IBKR Position object."""

    def __init__(self, account: str, contract: MockContract, position: float, avgCost: float):
        self.account = account
        self.contract = contract
        self.position = position
        self.avgCost = avgCost


class MockPortfolioItem:
    """Mock for IBKR PortfolioItem object."""

    def __init__(
        self,
        contract: MockContract,
        position: float,
        marketPrice: float,
        marketValue: float,
        averageCost: float,
        unrealizedPNL: float,
        realizedPNL: float,
    ):
        self.contract = contract
        self.position = position
        self.marketPrice = marketPrice
        self.marketValue = marketValue
        self.averageCost = averageCost
        self.unrealizedPNL = unrealizedPNL
        self.realizedPNL = realizedPNL


class TestGetPositionsMocked:
    """Test get_positions with mocked IBKR."""

    def test_get_positions_success(self):
        """Test successful positions retrieval."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        # Mock contract and position
        contract = MockContract("AAPL", 265598, "STK", "USD")
        mock_positions = [
            MockPosition("DU123456", contract, 100.0, 15000.0),
        ]

        # Mock portfolio item for market values
        portfolio_items = [
            MockPortfolioItem(contract, 100.0, 155.00, 15500.00, 150.00, 500.00, 0.00),
        ]

        mock_client.ib.reqPositions = MagicMock()
        mock_client.ib.positions.return_value = mock_positions
        mock_client.ib.portfolio.return_value = portfolio_items
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPositions = MagicMock()

        positions = get_positions(mock_client)

        assert len(positions) == 1
        pos = positions[0]
        assert pos.accountId == "DU123456"
        assert pos.symbol == "AAPL"
        assert pos.conId == 265598
        assert pos.assetClass == "STK"
        assert pos.quantity == 100.0
        assert pos.marketPrice == 155.00
        assert pos.unrealizedPnl == 500.00

    def test_get_positions_multiple(self):
        """Test retrieving multiple positions."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        # Multiple positions
        contracts = [
            MockContract("AAPL", 265598, "STK", "USD"),
            MockContract("MSFT", 272093, "STK", "USD"),
            MockContract("MES", 730283085, "FUT", "USD", "20251219"),
        ]

        mock_positions = [
            MockPosition("DU123456", contracts[0], 100.0, 15000.0),
            MockPosition("DU123456", contracts[1], 50.0, 20000.0),
            MockPosition("DU123456", contracts[2], 2.0, 24000.0),
        ]

        portfolio_items = [
            MockPortfolioItem(contracts[0], 100.0, 155.00, 15500.00, 150.00, 500.00, 0.00),
            MockPortfolioItem(contracts[1], 50.0, 410.00, 20500.00, 400.00, 500.00, 100.00),
            MockPortfolioItem(contracts[2], 2.0, 6100.00, 12200.00, 6000.00, 200.00, 0.00),
        ]

        mock_client.ib.reqPositions = MagicMock()
        mock_client.ib.positions.return_value = mock_positions
        mock_client.ib.portfolio.return_value = portfolio_items
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPositions = MagicMock()

        positions = get_positions(mock_client)

        assert len(positions) == 3
        symbols = [p.symbol for p in positions]
        assert "AAPL" in symbols
        assert "MSFT" in symbols
        # Futures should include expiry
        assert "MES_20251219" in symbols

    def test_get_positions_filters_by_account(self):
        """Test positions are filtered by account."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU111111"])

        # Positions from different accounts
        contract1 = MockContract("AAPL", 265598, "STK", "USD")
        contract2 = MockContract("MSFT", 272093, "STK", "USD")

        mock_positions = [
            MockPosition("DU111111", contract1, 100.0, 15000.0),  # Match
            MockPosition("DU222222", contract2, 50.0, 20000.0),  # Different account
        ]

        portfolio_items = [
            MockPortfolioItem(contract1, 100.0, 155.00, 15500.00, 150.00, 500.00, 0.00),
        ]

        mock_client.ib.reqPositions = MagicMock()
        mock_client.ib.positions.return_value = mock_positions
        mock_client.ib.portfolio.return_value = portfolio_items
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPositions = MagicMock()

        positions = get_positions(mock_client)

        # Should only have one position (filtered)
        assert len(positions) == 1
        assert positions[0].accountId == "DU111111"
        assert positions[0].symbol == "AAPL"

    def test_get_positions_empty(self):
        """Test empty positions list."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        mock_client.ib.reqPositions = MagicMock()
        mock_client.ib.positions.return_value = []
        mock_client.ib.portfolio.return_value = []
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPositions = MagicMock()

        positions = get_positions(mock_client)

        assert positions == []

    def test_get_positions_futures_include_expiry(self):
        """Test futures positions include expiry in symbol."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        contract = MockContract("MES", 730283085, "FUT", "USD", "20251219")
        mock_positions = [
            MockPosition("DU123456", contract, 2.0, 24000.0),
        ]

        portfolio_items = [
            MockPortfolioItem(contract, 2.0, 6100.00, 12200.00, 6000.00, 200.00, 0.00),
        ]

        mock_client.ib.reqPositions = MagicMock()
        mock_client.ib.positions.return_value = mock_positions
        mock_client.ib.portfolio.return_value = portfolio_items
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPositions = MagicMock()

        positions = get_positions(mock_client)

        assert len(positions) == 1
        assert positions[0].symbol == "MES_20251219"
        assert positions[0].assetClass == "FUT"

    def test_get_positions_with_explicit_account(self):
        """Test getting positions for explicit account."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        contract = MockContract("AAPL", 265598, "STK", "USD")
        mock_positions = [
            MockPosition("DU222222", contract, 50.0, 7500.0),
        ]

        portfolio_items = [
            MockPortfolioItem(contract, 50.0, 155.00, 7750.00, 150.00, 250.00, 0.00),
        ]

        mock_client.ib.reqPositions = MagicMock()
        mock_client.ib.positions.return_value = mock_positions
        mock_client.ib.portfolio.return_value = portfolio_items
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPositions = MagicMock()

        positions = get_positions(mock_client, account_id="DU222222")

        assert len(positions) == 1
        assert positions[0].accountId == "DU222222"


# =============================================================================
# Unit Tests - get_account_status
# =============================================================================


class TestGetAccountStatusMocked:
    """Test get_account_status convenience function."""

    def test_get_account_status_returns_summary_and_positions(self):
        """Test that account status includes both summary and positions."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        # Mock account summary
        from tests.test_account_summary import MockAccountValue

        mock_summary_values = [
            MockAccountValue("DU123456", "NetLiquidation", "100000.00", "USD"),
            MockAccountValue("DU123456", "TotalCashValue", "50000.00", "USD"),
        ]

        # Mock positions
        contract = MockContract("AAPL", 265598, "STK", "USD")
        mock_positions = [
            MockPosition("DU123456", contract, 100.0, 15000.0),
        ]
        portfolio_items = [
            MockPortfolioItem(contract, 100.0, 155.00, 15500.00, 150.00, 500.00, 0.00),
        ]

        mock_client.ib.reqAccountSummary = MagicMock()
        mock_client.ib.accountSummary.return_value = mock_summary_values
        mock_client.ib.reqPositions = MagicMock()
        mock_client.ib.positions.return_value = mock_positions
        mock_client.ib.portfolio.return_value = portfolio_items
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelAccountSummary = MagicMock()
        mock_client.ib.cancelPositions = MagicMock()

        status = get_account_status(mock_client)

        assert "summary" in status
        assert "positions" in status
        assert status["summary"].accountId == "DU123456"
        assert len(status["positions"]) == 1


# =============================================================================
# Unit Tests - Exception Hierarchy
# =============================================================================


class TestPositionsExceptionHierarchy:
    """Test exception class hierarchy."""

    def test_account_positions_error_inherits_from_account_error(self):
        """Test AccountPositionsError inherits from AccountError."""
        assert issubclass(AccountPositionsError, AccountError)

    def test_exception_message_preserved(self):
        """Test exception messages are preserved."""
        err = AccountPositionsError("Test positions error")
        assert str(err) == "Test positions error"


# =============================================================================
# Integration Tests (require running IBKR Gateway)
# =============================================================================


@pytest.mark.integration
class TestAccountPositionsIntegration:
    """Integration tests requiring running IBKR Gateway.

    These tests verify actual positions retrieval from IBKR paper gateway.
    Paper gateway should be running for these tests.
    """

    @pytest.fixture
    def client(self):
        """Create and connect client for tests."""
        import random

        client_id = random.randint(5000, 9999)
        client = IBKRClient(mode="paper", client_id=client_id)
        client.connect(timeout=10)
        yield client
        client.disconnect()

    def test_get_positions_returns_list(self, client):
        """Test that get_positions returns a list."""
        positions = get_positions(client)

        assert isinstance(positions, list)
        # List may be empty if no positions in paper account
        for pos in positions:
            assert isinstance(pos, Position)
            assert pos.accountId is not None
            assert pos.symbol is not None
            assert pos.conId > 0

        print(f"Found {len(positions)} positions")
        for pos in positions:
            print(
                f"  {pos.symbol}: {pos.quantity} @ {pos.avgPrice:.2f} "
                f"(PnL: {pos.unrealizedPnl:.2f})"
            )

    def test_get_positions_with_default_account(self, client):
        """Test getting positions with default account."""
        positions = get_positions(client, account_id=None)

        # Should succeed (may return empty list)
        assert isinstance(positions, list)

    def test_get_positions_with_explicit_account(self, client):
        """Test getting positions with explicit account ID."""
        accounts = client.managed_accounts
        assert len(accounts) > 0

        positions = get_positions(client, account_id=accounts[0])

        assert isinstance(positions, list)
        for pos in positions:
            assert pos.accountId == accounts[0]

    def test_positions_have_valid_asset_class(self, client):
        """Test all positions have valid asset class."""
        positions = get_positions(client)

        valid_classes = {"STK", "ETF", "FUT", "OPT", "FX", "CFD", "IND", "BOND", "FUND", "CRYPTO"}
        for pos in positions:
            assert pos.assetClass in valid_classes

    def test_positions_are_json_serializable(self, client):
        """Test that returned positions are JSON serializable."""
        positions = get_positions(client)

        for pos in positions:
            json_str = pos.model_dump_json()
            assert pos.symbol in json_str

    def test_get_account_status_returns_both(self, client):
        """Test get_account_status returns summary and positions."""
        status = get_account_status(client)

        assert "summary" in status
        assert "positions" in status
        assert status["summary"].accountId is not None
        assert isinstance(status["positions"], list)

        print(f"Account: {status['summary'].accountId}")
        print(f"NLV: {status['summary'].netLiquidation:.2f}")
        print(f"Positions: {len(status['positions'])}")

    def test_position_quantities_are_numeric(self, client):
        """Test position quantities are numeric."""
        positions = get_positions(client)

        for pos in positions:
            assert isinstance(pos.quantity, (int, float))
            assert isinstance(pos.avgPrice, (int, float))
            assert isinstance(pos.marketPrice, (int, float))
            assert isinstance(pos.marketValue, (int, float))
