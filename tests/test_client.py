"""
Tests for IBKRClient.

The canonical repo connects to an already-running IB Gateway/TWS using the
explicit host/port/client-id from config.json. The optional `mode` argument is
retained only as a safety/status label.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from ibkr_core.client import ConnectionError, IBKRClient, create_client
from ibkr_core.config import reset_config


@pytest.fixture(autouse=True)
def reset_config_fixture():
    """Reset config before each test."""
    reset_config()
    old_env = {}
    env_keys = [
        "IBKR_HOST",
        "IBKR_PORT",
        "IBKR_CLIENT_ID",
        "IBKR_ACCOUNT_ID",
        "TRADING_MODE",
        "ORDERS_ENABLED",
        "DRY_RUN",
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
# Unit Tests (no IBKR connection required)
# =============================================================================


class TestIBKRClientInit:
    """Test IBKRClient initialization."""

    def test_default_paper_mode(self):
        """Test that client defaults to paper mode."""
        client = IBKRClient()
        assert client.mode == "paper"
        assert client.port == 4002
        assert client.client_id == 1

    def test_explicit_paper_mode(self):
        """Test explicit paper mode."""
        client = IBKRClient(mode="paper")
        assert client.mode == "paper"
        assert client.port == 4002

    def test_explicit_live_mode(self):
        """Explicit live mode should not change the configured connection settings."""
        client = IBKRClient(mode="live")
        assert client.mode == "live"
        assert client.port == 4002
        assert client.client_id == 1

    def test_custom_client_id(self):
        """Test custom client ID."""
        client = IBKRClient(client_id=999)
        assert client.client_id == 999

    def test_invalid_mode_raises(self):
        """Test that invalid mode raises ValueError."""
        with pytest.raises(ValueError, match="Invalid mode"):
            IBKRClient(mode="invalid")

    def test_mode_case_insensitive(self):
        """Test that mode is case-insensitive."""
        client = IBKRClient(mode="PAPER")
        assert client.mode == "paper"

    def test_repr_disconnected(self):
        """Test string representation when disconnected."""
        client = IBKRClient()
        assert "disconnected" in repr(client)
        assert "paper" in repr(client)


class TestIBKRClientProperties:
    """Test IBKRClient properties."""

    def test_is_connected_false_initially(self):
        """Test that is_connected is False initially."""
        client = IBKRClient()
        assert client.is_connected is False

    def test_connection_time_none_initially(self):
        """Test that connection_time is None initially."""
        client = IBKRClient()
        assert client.connection_time is None

    def test_managed_accounts_empty_when_disconnected(self):
        """Test that managed_accounts returns empty list when disconnected."""
        client = IBKRClient()
        assert client.managed_accounts == []


class TestIBKRClientMocked:
    """Test IBKRClient with mocked IB connection."""

    @patch("ibkr_core.client.IB")
    def test_connect_success(self, mock_ib_class):
        """Test successful connection."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib.managedAccounts.return_value = ["DU12345"]
        mock_ib.reqCurrentTime.return_value = "2024-01-01 12:00:00"
        mock_ib_class.return_value = mock_ib

        client = IBKRClient()
        client.connect()

        mock_ib.connect.assert_called_once()
        assert client.is_connected is True
        assert client.connection_time is not None

    @patch("ibkr_core.client.IB")
    def test_connect_refused(self, mock_ib_class):
        """Test connection refused."""
        mock_ib = MagicMock()
        mock_ib.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_ib_class.return_value = mock_ib

        client = IBKRClient()
        with pytest.raises(ConnectionError, match="Connection refused"):
            client.connect()

    @patch("ibkr_core.client.IB")
    def test_connect_timeout(self, mock_ib_class):
        """Test connection timeout."""
        mock_ib = MagicMock()
        mock_ib.connect.side_effect = TimeoutError("Timeout")
        mock_ib_class.return_value = mock_ib

        client = IBKRClient()
        with pytest.raises(ConnectionError, match="timeout"):
            client.connect()

    @patch("ibkr_core.client.IB")
    def test_disconnect(self, mock_ib_class):
        """Test disconnect."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib_class.return_value = mock_ib

        client = IBKRClient()
        client._connected = True
        client.disconnect()

        mock_ib.disconnect.assert_called_once()
        assert client.is_connected is False

    @patch("ibkr_core.client.IB")
    def test_context_manager(self, mock_ib_class):
        """Test context manager usage."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib.managedAccounts.return_value = []
        mock_ib.reqCurrentTime.return_value = "2024-01-01 12:00:00"
        mock_ib_class.return_value = mock_ib

        with IBKRClient() as client:
            mock_ib.connect.assert_called_once()

        mock_ib.disconnect.assert_called_once()

    @patch("ibkr_core.client.IB")
    def test_ensure_connected_reconnects(self, mock_ib_class):
        """Test that ensure_connected reconnects if disconnected."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib.managedAccounts.return_value = []
        mock_ib.reqCurrentTime.return_value = "2024-01-01 12:00:00"
        mock_ib_class.return_value = mock_ib

        client = IBKRClient()
        client.ensure_connected()

        mock_ib.connect.assert_called_once()

    @patch("ibkr_core.client.IB")
    def test_get_server_time_raises_when_disconnected(self, mock_ib_class):
        """Test that get_server_time raises when not connected."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = False
        mock_ib_class.return_value = mock_ib

        client = IBKRClient()
        with pytest.raises(ConnectionError, match="Not connected"):
            client.get_server_time()


class TestCreateClientFunction:
    """Test the create_client convenience function."""

    def test_create_client_default(self):
        """Test create_client with defaults."""
        client = create_client()
        assert client.mode == "paper"

    def test_create_client_with_mode(self):
        """Test create_client with explicit mode."""
        client = create_client(mode="live")
        assert client.mode == "live"

    def test_create_client_with_client_id(self):
        """Test create_client with custom client_id."""
        client = create_client(client_id=42)
        assert client.client_id == 42


# =============================================================================
# Integration Tests (require running IBKR Gateway)
# =============================================================================


@pytest.mark.integration
class TestIBKRClientIntegration:
    """Integration tests requiring running IBKR Gateway.

    Note: Each test uses a unique client_id to avoid IBKR connection conflicts
    that occur when the same client_id is reused too quickly.
    """

    def test_connect_paper_gateway(self):
        """Test connection to paper gateway."""
        import random

        client_id = random.randint(1000, 9999)
        client = IBKRClient(mode="paper", client_id=client_id)
        try:
            client.connect(timeout=10)
            assert client.is_connected is True
            assert client.connection_time is not None
            print(f"Connected! Accounts: {client.managed_accounts}")
        finally:
            client.disconnect()

    def test_get_server_time(self):
        """Test getting server time from IBKR."""
        import random

        client_id = random.randint(1000, 9999)
        with IBKRClient(mode="paper", client_id=client_id) as client:
            server_time = client.get_server_time()
            assert server_time is not None
            print(f"Server time: {server_time}")

    def test_reconnect(self):
        """Test reconnection after disconnect."""
        import random
        import time

        client_id = random.randint(1000, 9999)
        client = IBKRClient(mode="paper", client_id=client_id)

        # First connection
        client.connect()
        assert client.is_connected

        # Disconnect
        client.disconnect()
        assert not client.is_connected

        # Brief pause to allow IBKR to release the connection
        time.sleep(1)

        # Reconnect via ensure_connected
        client.ensure_connected()
        assert client.is_connected

        client.disconnect()

    def test_connection_with_different_client_ids(self):
        """Test that different client IDs work."""
        import random

        base_id = random.randint(1000, 8000)
        client1 = IBKRClient(mode="paper", client_id=base_id)
        client2 = IBKRClient(mode="paper", client_id=base_id + 1)

        try:
            client1.connect()
            assert client1.is_connected

            client2.connect()
            assert client2.is_connected
        finally:
            client1.disconnect()
            client2.disconnect()
