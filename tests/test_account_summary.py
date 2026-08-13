"""
Tests for account summary retrieval.

Two test modes:
  - Unit tests: Test with mocked IBKR responses
  - Integration tests: Test against running IBKR Gateway

Run unit tests only:
    pytest tests/test_account_summary.py -m "not integration"

Run integration tests:
    pytest tests/test_account_summary.py -m integration

Run all:
    pytest tests/test_account_summary.py
"""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock

import pytest

from ibkr_core.account import (
    AccountError,
    AccountSummaryError,
    _get_default_account_id,
    get_account_summary,
    list_managed_accounts,
)
from ibkr_core.client import IBKRClient
from ibkr_core.config import reset_config
from ibkr_core.models import AccountSummary


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
# Unit Tests - AccountSummary Model
# =============================================================================


class TestAccountSummaryModel:
    """Test AccountSummary Pydantic model."""

    def test_account_summary_creation(self):
        """Test creating AccountSummary model."""
        now = datetime.now(timezone.utc)
        summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            cash=50000.0,
            buyingPower=200000.0,
            marginExcess=75000.0,
            maintenanceMargin=25000.0,
            initialMargin=30000.0,
            timestamp=now,
        )

        assert summary.accountId == "DU123456"
        assert summary.currency == "USD"
        assert summary.netLiquidation == 100000.0
        assert summary.cash == 50000.0
        assert summary.buyingPower == 200000.0
        assert summary.marginExcess == 75000.0
        assert summary.maintenanceMargin == 25000.0
        assert summary.initialMargin == 30000.0
        assert summary.timestamp == now

    def test_account_summary_serialization(self):
        """Test AccountSummary serializes to dict correctly."""
        now = datetime.now(timezone.utc)
        summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            cash=50000.0,
            buyingPower=200000.0,
            marginExcess=75000.0,
            maintenanceMargin=25000.0,
            initialMargin=30000.0,
            timestamp=now,
        )

        data = summary.model_dump()

        assert data["accountId"] == "DU123456"
        assert data["netLiquidation"] == 100000.0
        assert "timestamp" in data

    def test_account_summary_defaults(self):
        """Test AccountSummary default values."""
        now = datetime.now(timezone.utc)
        summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            timestamp=now,
        )

        # Defaults should be 0.0
        assert summary.cash == 0.0
        assert summary.buyingPower == 0.0
        assert summary.marginExcess == 0.0
        assert summary.maintenanceMargin == 0.0
        assert summary.initialMargin == 0.0

    def test_account_summary_json_serializable(self):
        """Test AccountSummary is JSON serializable."""
        now = datetime.now(timezone.utc)
        summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            timestamp=now,
        )

        # Should not raise
        json_str = summary.model_dump_json()
        assert "DU123456" in json_str


# =============================================================================
# Unit Tests - get_account_summary (Mocked)
# =============================================================================


class MockAccountValue:
    """Mock for IBKR AccountValue object."""

    def __init__(self, account: str, tag: str, value: str, currency: str = "USD"):
        self.account = account
        self.tag = tag
        self.value = value
        self.currency = currency


class TestGetAccountSummaryMocked:
    """Test get_account_summary with mocked IBKR."""

    def test_get_account_summary_success(self):
        """Test successful account summary retrieval."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        # Mock managed accounts
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        # Mock account summary values
        mock_values = [
            MockAccountValue("DU123456", "NetLiquidation", "100000.50", "USD"),
            MockAccountValue("DU123456", "TotalCashValue", "50000.25", "USD"),
            MockAccountValue("DU123456", "BuyingPower", "200000.00", "USD"),
            MockAccountValue("DU123456", "ExcessLiquidity", "75000.00", "USD"),
            MockAccountValue("DU123456", "MaintMarginReq", "25000.00", "USD"),
            MockAccountValue("DU123456", "InitMarginReq", "30000.00", "USD"),
        ]

        mock_client.ib.reqAccountSummary = MagicMock()
        mock_client.ib.accountSummary.return_value = mock_values
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelAccountSummary = MagicMock()

        summary = get_account_summary(mock_client)

        assert summary.accountId == "DU123456"
        assert summary.currency == "USD"
        assert summary.netLiquidation == 100000.50
        assert summary.cash == 50000.25
        assert summary.buyingPower == 200000.00
        assert summary.marginExcess == 75000.00
        assert summary.maintenanceMargin == 25000.00
        assert summary.initialMargin == 30000.00
        assert summary.timestamp is not None

    def test_get_account_summary_with_specific_account(self):
        """Test getting summary for specific account ID."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        # Mock values for two accounts
        mock_values = [
            MockAccountValue("DU111111", "NetLiquidation", "50000.00", "USD"),
            MockAccountValue("DU222222", "NetLiquidation", "100000.00", "USD"),
            MockAccountValue("DU111111", "TotalCashValue", "25000.00", "USD"),
            MockAccountValue("DU222222", "TotalCashValue", "50000.00", "USD"),
        ]

        mock_client.ib.reqAccountSummary = MagicMock()
        mock_client.ib.accountSummary.return_value = mock_values
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelAccountSummary = MagicMock()

        # Request specific account
        summary = get_account_summary(mock_client, account_id="DU222222")

        assert summary.accountId == "DU222222"
        assert summary.netLiquidation == 100000.00
        assert summary.cash == 50000.00

    def test_get_account_summary_filters_by_account(self):
        """Test that summary correctly filters by account when multiple exist."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU111111"])

        # Mock values for multiple accounts - should only use matching
        mock_values = [
            MockAccountValue("DU111111", "NetLiquidation", "50000.00", "USD"),
            MockAccountValue("DU222222", "NetLiquidation", "100000.00", "USD"),
        ]

        mock_client.ib.reqAccountSummary = MagicMock()
        mock_client.ib.accountSummary.return_value = mock_values
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelAccountSummary = MagicMock()

        summary = get_account_summary(mock_client)

        # Should use DU111111 (the default)
        assert summary.accountId == "DU111111"
        assert summary.netLiquidation == 50000.00

    def test_get_account_summary_no_data_raises(self):
        """Test that no data raises AccountSummaryError."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        mock_client.ib.reqAccountSummary = MagicMock()
        mock_client.ib.accountSummary.return_value = []
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelAccountSummary = MagicMock()

        with pytest.raises(AccountSummaryError, match="No account summary data"):
            get_account_summary(mock_client)

    def test_get_account_summary_no_matching_account_raises(self):
        """Test that no matching account raises AccountSummaryError."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        # Values exist but for different account
        mock_values = [
            MockAccountValue("DU999999", "NetLiquidation", "100000.00", "USD"),
        ]

        mock_client.ib.reqAccountSummary = MagicMock()
        mock_client.ib.accountSummary.return_value = mock_values
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelAccountSummary = MagicMock()

        with pytest.raises(AccountSummaryError, match="No account summary values found"):
            get_account_summary(mock_client, account_id="DU123456")

    def test_get_account_summary_handles_parse_error(self):
        """Test that invalid values are handled gracefully."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU123456"])

        # Include invalid value that can't be parsed
        mock_values = [
            MockAccountValue("DU123456", "NetLiquidation", "100000.00", "USD"),
            MockAccountValue("DU123456", "TotalCashValue", "INVALID", "USD"),  # Bad value
        ]

        mock_client.ib.reqAccountSummary = MagicMock()
        mock_client.ib.accountSummary.return_value = mock_values
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelAccountSummary = MagicMock()

        # Should succeed, using 0.0 for unparseable values
        summary = get_account_summary(mock_client)
        assert summary.netLiquidation == 100000.00
        # cash should be 0.0 since parse failed
        assert summary.cash == 0.0


# =============================================================================
# Unit Tests - _get_default_account_id
# =============================================================================


class TestGetDefaultAccountId:
    """Test default account ID retrieval."""

    def test_returns_first_account(self):
        """Test returns first managed account."""
        mock_client = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU111111", "DU222222"])

        account_id = _get_default_account_id(mock_client)
        assert account_id == "DU111111"

    def test_no_accounts_raises(self):
        """Test raises when no accounts available."""
        mock_client = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=[])

        with pytest.raises(AccountError, match="No managed accounts"):
            _get_default_account_id(mock_client)


# =============================================================================
# Unit Tests - list_managed_accounts
# =============================================================================


class TestListManagedAccounts:
    """Test listing managed accounts."""

    def test_returns_accounts(self):
        """Test returns list of accounts."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=["DU111111", "DU222222"])

        accounts = list_managed_accounts(mock_client)
        assert accounts == ["DU111111", "DU222222"]

    def test_returns_empty_when_no_accounts(self):
        """Test returns empty list when no accounts."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()
        type(mock_client).managed_accounts = PropertyMock(return_value=[])

        accounts = list_managed_accounts(mock_client)
        assert accounts == []


# =============================================================================
# Unit Tests - Exception Hierarchy
# =============================================================================


class TestExceptionHierarchy:
    """Test exception class hierarchy."""

    def test_account_summary_error_inherits_from_account_error(self):
        """Test AccountSummaryError inherits from AccountError."""
        assert issubclass(AccountSummaryError, AccountError)

    def test_exception_message_preserved(self):
        """Test exception messages are preserved."""
        err = AccountSummaryError("Test error message")
        assert str(err) == "Test error message"


# =============================================================================
# Integration Tests (require running IBKR Gateway)
# =============================================================================


@pytest.mark.integration
class TestAccountSummaryIntegration:
    """Integration tests requiring running IBKR Gateway.

    These tests verify actual account summary retrieval from IBKR paper gateway.
    Paper gateway should be running for these tests.
    """

    @pytest.fixture
    def client(self):
        """Create and connect client for tests."""
        import random

        client_id = random.randint(4000, 9999)
        client = IBKRClient(mode="paper", client_id=client_id)
        client.connect(timeout=10)
        yield client
        client.disconnect()

    def test_get_account_summary_returns_valid_data(self, client):
        """Test that account summary returns valid numeric data."""
        summary = get_account_summary(client)

        assert summary.accountId is not None
        assert len(summary.accountId) > 0
        assert summary.currency in ("USD", "EUR", "GBP", "CAD", "AUD")
        assert summary.netLiquidation >= 0  # Should have some NLV
        assert summary.timestamp is not None

        print(f"Account: {summary.accountId}")
        print(f"Net Liquidation: {summary.netLiquidation:.2f} {summary.currency}")
        print(f"Cash: {summary.cash:.2f}")
        print(f"Buying Power: {summary.buyingPower:.2f}")
        print(f"Margin Excess: {summary.marginExcess:.2f}")

    def test_get_account_summary_with_default_account(self, client):
        """Test getting summary with default account."""
        summary = get_account_summary(client, account_id=None)

        # Default account should work
        assert summary.accountId == client.managed_accounts[0]
        assert summary.netLiquidation >= 0

    def test_get_account_summary_with_explicit_account(self, client):
        """Test getting summary with explicit account ID."""
        # Get the first managed account
        accounts = client.managed_accounts
        assert len(accounts) > 0

        account_id = accounts[0]
        summary = get_account_summary(client, account_id=account_id)

        assert summary.accountId == account_id

    def test_list_managed_accounts(self, client):
        """Test listing managed accounts."""
        accounts = list_managed_accounts(client)

        assert len(accounts) >= 1
        for acc in accounts:
            assert isinstance(acc, str)
            assert len(acc) > 0

        print(f"Managed accounts: {accounts}")

    def test_account_summary_is_json_serializable(self, client):
        """Test that returned summary is JSON serializable."""
        summary = get_account_summary(client)

        # Should not raise
        json_str = summary.model_dump_json()
        assert summary.accountId in json_str

    def test_invalid_account_id_raises(self, client):
        """Test that invalid account ID raises error."""
        # Using an account ID that doesn't exist
        with pytest.raises(AccountSummaryError):
            get_account_summary(client, account_id="INVALID_ACCOUNT_12345")
