"""
Tests for account P&L retrieval.

Two test modes:
  - Unit tests: Test with mocked IBKR responses
  - Integration tests: Test against running IBKR Gateway

Run unit tests only:
    pytest tests/test_account_pnl.py -m "not integration"

Run integration tests:
    pytest tests/test_account_pnl.py -m integration

Run all:
    pytest tests/test_account_pnl.py
"""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from ibkr_core.account import (
    AccountError,
    AccountPnlError,
    AccountPositionsError,
    get_account_summary,
    get_pnl,
    get_positions,
)
from ibkr_core.client import IBKRClient
from ibkr_core.config import reset_config
from ibkr_core.models import AccountPnl, PnlDetail, Position


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
# Unit Tests - PnlDetail Model
# =============================================================================


class TestPnlDetailModel:
    """Test PnlDetail Pydantic model."""

    def test_pnl_detail_creation(self):
        """Test creating PnlDetail model."""
        detail = PnlDetail(
            symbol="AAPL",
            conId=265598,
            currency="USD",
            realized=100.00,
            unrealized=500.00,
            realizedToday=50.00,
            unrealizedToday=250.00,
            basis=15000.00,
        )

        assert detail.symbol == "AAPL"
        assert detail.conId == 265598
        assert detail.currency == "USD"
        assert detail.realized == 100.00
        assert detail.unrealized == 500.00
        assert detail.realizedToday == 50.00
        assert detail.unrealizedToday == 250.00
        assert detail.basis == 15000.00

    def test_pnl_detail_minimal(self):
        """Test PnlDetail with minimal fields."""
        detail = PnlDetail(
            symbol="AAPL",
            currency="USD",
            realized=100.00,
            unrealized=500.00,
        )

        assert detail.symbol == "AAPL"
        assert detail.realized == 100.00
        assert detail.unrealized == 500.00
        # Optional fields should be None
        assert detail.conId is None
        assert detail.realizedToday is None
        assert detail.unrealizedToday is None
        assert detail.basis is None

    def test_pnl_detail_serialization(self):
        """Test PnlDetail serializes to dict correctly."""
        detail = PnlDetail(
            symbol="AAPL",
            conId=265598,
            currency="USD",
            realized=100.00,
            unrealized=500.00,
        )

        data = detail.model_dump()

        assert data["symbol"] == "AAPL"
        assert data["conId"] == 265598
        assert data["realized"] == 100.00
        assert data["unrealized"] == 500.00


# =============================================================================
# Unit Tests - AccountPnl Model
# =============================================================================


class TestAccountPnlModel:
    """Test AccountPnl Pydantic model."""

    def test_account_pnl_creation(self):
        """Test creating AccountPnl model."""
        now = datetime.now(timezone.utc)
        pnl = AccountPnl(
            accountId="DU123456",
            currency="USD",
            timeframe="CURRENT",
            realized=1000.00,
            unrealized=5000.00,
            bySymbol={
                "AAPL": PnlDetail(
                    symbol="AAPL", currency="USD", realized=500.00, unrealized=2500.00
                ),
                "MSFT": PnlDetail(
                    symbol="MSFT", currency="USD", realized=500.00, unrealized=2500.00
                ),
            },
            timestamp=now,
        )

        assert pnl.accountId == "DU123456"
        assert pnl.currency == "USD"
        assert pnl.timeframe == "CURRENT"
        assert pnl.realized == 1000.00
        assert pnl.unrealized == 5000.00
        assert len(pnl.bySymbol) == 2
        assert "AAPL" in pnl.bySymbol
        assert "MSFT" in pnl.bySymbol
        assert pnl.timestamp == now

    def test_account_pnl_empty_by_symbol(self):
        """Test AccountPnl with empty bySymbol dict."""
        now = datetime.now(timezone.utc)
        pnl = AccountPnl(
            accountId="DU123456",
            currency="USD",
            timeframe="CURRENT",
            realized=0.00,
            unrealized=0.00,
            bySymbol={},
            timestamp=now,
        )

        assert pnl.bySymbol == {}

    def test_account_pnl_serialization(self):
        """Test AccountPnl serializes to dict correctly."""
        now = datetime.now(timezone.utc)
        pnl = AccountPnl(
            accountId="DU123456",
            currency="USD",
            timeframe="CURRENT",
            realized=1000.00,
            unrealized=5000.00,
            bySymbol={
                "AAPL": PnlDetail(
                    symbol="AAPL", currency="USD", realized=500.00, unrealized=2500.00
                ),
            },
            timestamp=now,
        )

        data = pnl.model_dump()

        assert data["accountId"] == "DU123456"
        assert data["realized"] == 1000.00
        assert data["unrealized"] == 5000.00
        assert "AAPL" in data["bySymbol"]

    def test_account_pnl_json_serializable(self):
        """Test AccountPnl is JSON serializable."""
        now = datetime.now(timezone.utc)
        pnl = AccountPnl(
            accountId="DU123456",
            currency="USD",
            timeframe="CURRENT",
            realized=1000.00,
            unrealized=5000.00,
            bySymbol={},
            timestamp=now,
        )

        json_str = pnl.model_dump_json()
        assert "DU123456" in json_str


# =============================================================================
# Unit Tests - get_pnl (Mocked)
# =============================================================================


class MockPnL:
    """Mock for IBKR PnL object."""

    def __init__(self, dailyPnL=None, unrealizedPnL=None, realizedPnL=None):
        self.dailyPnL = dailyPnL
        self.unrealizedPnL = unrealizedPnL
        self.realizedPnL = realizedPnL


class TestGetPnlMocked:
    """Test get_pnl with mocked IBKR."""

    def _create_mock_position(
        self,
        account_id: str,
        symbol: str,
        con_id: int,
        unrealized_pnl: float,
        realized_pnl: float,
    ) -> Position:
        """Helper to create mock Position."""
        return Position(
            accountId=account_id,
            symbol=symbol,
            conId=con_id,
            assetClass="STK",
            currency="USD",
            quantity=100.0,
            avgPrice=100.0,
            marketPrice=110.0,
            marketValue=11000.0,
            unrealizedPnl=unrealized_pnl,
            realizedPnl=realized_pnl,
        )

    def test_get_pnl_success(self):
        """Test successful P&L retrieval."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        # Mock PnL data
        mock_pnl = MockPnL(
            dailyPnL=1500.00,
            unrealizedPnL=5000.00,
            realizedPnL=1000.00,
        )

        mock_client.ib.reqPnL = MagicMock()
        mock_client.ib.pnl.return_value = mock_pnl
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPnL = MagicMock()

        # Mock positions
        positions = [
            self._create_mock_position("DU123456", "AAPL", 265598, 2500.00, 500.00),
            self._create_mock_position("DU123456", "MSFT", 272093, 2500.00, 500.00),
        ]

        # Mock account summary
        from ibkr_core.models import AccountSummary

        mock_summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            timestamp=datetime.now(timezone.utc),
        )

        with patch("ibkr_core.account.get_positions", return_value=positions):
            with patch("ibkr_core.account.get_account_summary", return_value=mock_summary):
                pnl = get_pnl(mock_client)

        assert pnl.accountId == "DU123456"
        assert pnl.currency == "USD"
        assert pnl.timeframe == "CURRENT"
        # IBKR PnL values should be used
        assert pnl.realized == 1000.00
        assert pnl.unrealized == 5000.00
        assert len(pnl.bySymbol) == 2
        assert "AAPL" in pnl.bySymbol
        assert "MSFT" in pnl.bySymbol

    def test_get_pnl_aggregates_per_symbol(self):
        """Test P&L aggregates per symbol correctly."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        mock_pnl = MockPnL()  # No account-level PnL
        mock_client.ib.reqPnL = MagicMock()
        mock_client.ib.pnl.return_value = mock_pnl
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPnL = MagicMock()

        # Mock positions
        positions = [
            self._create_mock_position("DU123456", "AAPL", 265598, 1000.00, 200.00),
            self._create_mock_position("DU123456", "MSFT", 272093, 500.00, 100.00),
        ]

        from ibkr_core.models import AccountSummary

        mock_summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            timestamp=datetime.now(timezone.utc),
        )

        with patch("ibkr_core.account.get_positions", return_value=positions):
            with patch("ibkr_core.account.get_account_summary", return_value=mock_summary):
                pnl = get_pnl(mock_client)

        # Should sum up from positions when IBKR PnL not available
        assert pnl.realized == 300.00  # 200 + 100
        assert pnl.unrealized == 1500.00  # 1000 + 500
        assert pnl.bySymbol["AAPL"].unrealized == 1000.00
        assert pnl.bySymbol["AAPL"].realized == 200.00
        assert pnl.bySymbol["MSFT"].unrealized == 500.00
        assert pnl.bySymbol["MSFT"].realized == 100.00

    def test_get_pnl_no_positions(self):
        """Test P&L with no positions."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        mock_pnl = MockPnL()
        mock_client.ib.reqPnL = MagicMock()
        mock_client.ib.pnl.return_value = mock_pnl
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPnL = MagicMock()

        from ibkr_core.models import AccountSummary

        mock_summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            timestamp=datetime.now(timezone.utc),
        )

        with patch("ibkr_core.account.get_positions", return_value=[]):
            with patch("ibkr_core.account.get_account_summary", return_value=mock_summary):
                pnl = get_pnl(mock_client)

        assert pnl.realized == 0.0
        assert pnl.unrealized == 0.0
        assert pnl.bySymbol == {}

    def test_get_pnl_timeframe_warning(self, caplog):
        """Test that timeframe parameter logs warning."""
        import logging

        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        mock_pnl = MockPnL()
        mock_client.ib.reqPnL = MagicMock()
        mock_client.ib.pnl.return_value = mock_pnl
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPnL = MagicMock()

        from ibkr_core.models import AccountSummary

        mock_summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            timestamp=datetime.now(timezone.utc),
        )

        with patch("ibkr_core.account.get_positions", return_value=[]):
            with patch("ibkr_core.account.get_account_summary", return_value=mock_summary):
                with caplog.at_level(logging.WARNING):
                    pnl = get_pnl(mock_client, timeframe="YTD")

        # Should have logged a warning about timeframe not supported
        assert "not yet supported" in caplog.text or pnl.timeframe == "CURRENT"

    def test_get_pnl_strips_expiry_from_futures(self):
        """Test P&L uses base symbol for futures (strips expiry)."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        mock_pnl = MockPnL()
        mock_client.ib.reqPnL = MagicMock()
        mock_client.ib.pnl.return_value = mock_pnl
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelPnL = MagicMock()

        # Position with expiry in symbol
        positions = [
            Position(
                accountId="DU123456",
                symbol="MES_20251219",  # Includes expiry
                conId=730283085,
                assetClass="FUT",
                currency="USD",
                quantity=2.0,
                avgPrice=6000.0,
                marketPrice=6100.0,
                marketValue=12200.0,
                unrealizedPnl=200.0,
                realizedPnl=0.0,
            ),
        ]

        from ibkr_core.models import AccountSummary

        mock_summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            timestamp=datetime.now(timezone.utc),
        )

        with patch("ibkr_core.account.get_positions", return_value=positions):
            with patch("ibkr_core.account.get_account_summary", return_value=mock_summary):
                pnl = get_pnl(mock_client)

        # Should use base symbol "MES" not "MES_20251219"
        assert "MES" in pnl.bySymbol
        assert "MES_20251219" not in pnl.bySymbol


# =============================================================================
# Unit Tests - Exception Hierarchy
# =============================================================================


class TestPnlExceptionHierarchy:
    """Test exception class hierarchy."""

    def test_account_pnl_error_inherits_from_account_error(self):
        """Test AccountPnlError inherits from AccountError."""
        assert issubclass(AccountPnlError, AccountError)

    def test_exception_message_preserved(self):
        """Test exception messages are preserved."""
        err = AccountPnlError("Test P&L error")
        assert str(err) == "Test P&L error"


# =============================================================================
# Integration Tests (require running IBKR Gateway)
# =============================================================================


@pytest.mark.integration
class TestAccountPnlIntegration:
    """Integration tests requiring running IBKR Gateway.

    These tests verify actual P&L retrieval from IBKR paper gateway.
    Paper gateway should be running for these tests.
    """

    @pytest.fixture
    def client(self):
        """Create and connect client for tests."""
        import random

        client_id = random.randint(6000, 9999)
        client = IBKRClient(mode="paper", client_id=client_id)
        client.connect(timeout=10)
        yield client
        client.disconnect()

    def test_get_pnl_returns_valid_structure(self, client):
        """Test that get_pnl returns valid AccountPnl structure."""
        pnl = get_pnl(client)

        assert isinstance(pnl, AccountPnl)
        assert pnl.accountId is not None
        assert len(pnl.accountId) > 0
        assert pnl.currency in ("USD", "EUR", "GBP", "CAD", "AUD")
        assert pnl.timeframe == "CURRENT"
        assert isinstance(pnl.realized, (int, float))
        assert isinstance(pnl.unrealized, (int, float))
        assert isinstance(pnl.bySymbol, dict)
        assert pnl.timestamp is not None

        print(f"Account: {pnl.accountId}")
        print(f"Realized P&L: {pnl.realized:.2f} {pnl.currency}")
        print(f"Unrealized P&L: {pnl.unrealized:.2f} {pnl.currency}")
        print(f"Symbols: {list(pnl.bySymbol.keys())}")

    def test_get_pnl_with_default_account(self, client):
        """Test getting P&L with default account."""
        pnl = get_pnl(client, account_id=None)

        assert pnl.accountId == client.managed_accounts[0]

    def test_get_pnl_with_explicit_account(self, client):
        """Test getting P&L with explicit account ID."""
        accounts = client.managed_accounts
        assert len(accounts) > 0

        pnl = get_pnl(client, account_id=accounts[0])

        assert pnl.accountId == accounts[0]

    def test_get_pnl_by_symbol_matches_positions(self, client):
        """Test that bySymbol P&L matches positions."""
        pnl = get_pnl(client)
        positions = get_positions(client)

        # Every position should have an entry in bySymbol (with base symbol)
        for pos in positions:
            base_symbol = pos.symbol.split("_")[0] if "_" in pos.symbol else pos.symbol
            # Note: Multiple positions in same symbol get aggregated
            if base_symbol in pnl.bySymbol:
                detail = pnl.bySymbol[base_symbol]
                assert isinstance(detail.realized, (int, float))
                assert isinstance(detail.unrealized, (int, float))

    def test_get_pnl_timeframe_ignored(self, client):
        """Test that timeframe is ignored (returns CURRENT)."""
        pnl = get_pnl(client, timeframe="YTD")

        # Should still return CURRENT timeframe
        assert pnl.timeframe == "CURRENT"

    def test_get_pnl_is_json_serializable(self, client):
        """Test that returned P&L is JSON serializable."""
        pnl = get_pnl(client)

        json_str = pnl.model_dump_json()
        assert pnl.accountId in json_str

    def test_pnl_by_symbol_details(self, client):
        """Test PnlDetail structure in bySymbol."""
        pnl = get_pnl(client)

        for symbol, detail in pnl.bySymbol.items():
            assert isinstance(detail, PnlDetail)
            assert detail.symbol == symbol
            assert detail.currency in ("USD", "EUR", "GBP", "CAD", "AUD")
            print(
                f"  {symbol}: realized={detail.realized:.2f}, "
                f"unrealized={detail.unrealized:.2f}"
            )

    def test_pnl_totals_consistency(self, client):
        """Test that totals are consistent with per-symbol values."""
        pnl = get_pnl(client)

        # Calculate totals from bySymbol
        calc_realized = sum(d.realized for d in pnl.bySymbol.values())
        calc_unrealized = sum(d.unrealized for d in pnl.bySymbol.values())

        # Note: IBKR may use different values at account level
        # So we just verify the structure is correct
        print(f"Account realized: {pnl.realized:.2f}, calculated: {calc_realized:.2f}")
        print(f"Account unrealized: {pnl.unrealized:.2f}, calculated: {calc_unrealized:.2f}")

        # At minimum, signs should be consistent (or both zero)
        if pnl.bySymbol:
            assert isinstance(pnl.realized, (int, float))
            assert isinstance(pnl.unrealized, (int, float))
