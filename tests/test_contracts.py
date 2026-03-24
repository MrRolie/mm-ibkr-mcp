"""
Tests for contract resolution.

Two test modes:
  - Unit tests: Test contract building and cache without IBKR connection
  - Integration tests: Test against running IBKR Gateway

Run unit tests only:
    pytest tests/test_contracts.py -m "not integration"

Run integration tests:
    pytest tests/test_contracts.py -m integration

Run all:
    pytest tests/test_contracts.py
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from ibkr_core.client import IBKRClient
from ibkr_core.config import reset_config
from ibkr_core.contracts import (
    ContractCache,
    ContractNotFoundError,
    ContractResolutionError,
    _apply_defaults,
    _build_contract,
    get_contract_cache,
    get_front_month_expiry,
    resolve_contract,
    resolve_contracts,
)
from ibkr_core.models import SymbolSpec


@pytest.fixture(autouse=True)
def reset_config_and_cache():
    """Reset config and clear contract cache before each test."""
    reset_config()
    get_contract_cache().clear()

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
    get_contract_cache().clear()


# =============================================================================
# Unit Tests - ContractCache
# =============================================================================


class TestContractCache:
    """Test ContractCache functionality."""

    def test_cache_initially_empty(self):
        """Test that cache starts empty."""
        cache = ContractCache()
        assert cache.size == 0

    def test_put_and_get(self):
        """Test storing and retrieving from cache."""
        cache = ContractCache()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        mock_contract = MagicMock()
        mock_contract.conId = 12345

        cache.put(spec, mock_contract)
        assert cache.size == 1

        retrieved = cache.get(spec)
        assert retrieved is mock_contract

    def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = ContractCache()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        result = cache.get(spec)
        assert result is None

    def test_cache_key_includes_all_fields(self):
        """Test that cache key distinguishes by all spec fields."""
        cache = ContractCache()

        spec1 = SymbolSpec(symbol="AAPL", securityType="STK", exchange="SMART")
        spec2 = SymbolSpec(symbol="AAPL", securityType="STK", exchange="NYSE")

        mock1 = MagicMock()
        mock1.conId = 1
        mock2 = MagicMock()
        mock2.conId = 2

        cache.put(spec1, mock1)
        cache.put(spec2, mock2)

        assert cache.size == 2
        assert cache.get(spec1).conId == 1
        assert cache.get(spec2).conId == 2

    def test_cache_key_distinguishes_option_strike_and_right(self):
        """Options with different contract terms must not collide in cache."""
        cache = ContractCache()

        call_spec = SymbolSpec(
            symbol="AAPL",
            securityType="OPT",
            exchange="SMART",
            currency="USD",
            expiry="2026-04-17",
            strike=100.0,
            right="C",
        )
        put_spec = SymbolSpec(
            symbol="AAPL",
            securityType="OPT",
            exchange="SMART",
            currency="USD",
            expiry="2026-04-17",
            strike=105.0,
            right="P",
        )

        call_contract = MagicMock()
        call_contract.conId = 101
        put_contract = MagicMock()
        put_contract.conId = 202

        cache.put(call_spec, call_contract)
        cache.put(put_spec, put_contract)

        assert cache.get(call_spec).conId == 101
        assert cache.get(put_spec).conId == 202

    def test_clear(self):
        """Test clearing cache."""
        cache = ContractCache()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        cache.put(spec, MagicMock())
        assert cache.size == 1

        cache.clear()
        assert cache.size == 0

    def test_stats(self):
        """Test cache statistics."""
        cache = ContractCache()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        # Miss
        cache.get(spec)

        # Put and hit
        cache.put(spec, MagicMock())
        cache.get(spec)

        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1


# =============================================================================
# Unit Tests - Apply Defaults
# =============================================================================


class TestApplyDefaults:
    """Test default application for known symbols."""

    def test_aapl_defaults(self):
        """Test AAPL gets SMART exchange and USD currency."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")
        result = _apply_defaults(spec)

        assert result.exchange == "SMART"
        assert result.currency == "USD"

    def test_mes_defaults(self):
        """Test MES gets CME exchange and USD currency."""
        spec = SymbolSpec(symbol="MES", securityType="FUT")
        result = _apply_defaults(spec)

        assert result.exchange == "CME"
        assert result.currency == "USD"

    def test_spx_defaults(self):
        """Test SPX gets CBOE exchange."""
        spec = SymbolSpec(symbol="SPX", securityType="IND")
        result = _apply_defaults(spec)

        assert result.exchange == "CBOE"
        assert result.currency == "USD"

    def test_unknown_symbol_uses_provided(self):
        """Test unknown symbol keeps provided values."""
        spec = SymbolSpec(
            symbol="UNKNOWN",
            securityType="STK",
            exchange="NYSE",
            currency="EUR",
        )
        result = _apply_defaults(spec)

        assert result.exchange == "NYSE"
        assert result.currency == "EUR"

    def test_explicit_override_known_defaults(self):
        """Test explicit values override defaults."""
        spec = SymbolSpec(
            symbol="AAPL",
            securityType="STK",
            exchange="NYSE",  # Override default SMART
            currency="USD",
        )
        result = _apply_defaults(spec)

        assert result.exchange == "NYSE"  # Kept explicit value


# =============================================================================
# Unit Tests - Build Contract
# =============================================================================


class TestBuildContract:
    """Test contract building from SymbolSpec."""

    def test_build_stock_contract(self):
        """Test building a stock contract."""
        spec = SymbolSpec(
            symbol="AAPL",
            securityType="STK",
            exchange="SMART",
            currency="USD",
        )
        contract = _build_contract(spec)

        assert contract.symbol == "AAPL"
        assert contract.secType == "STK"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"

    def test_build_future_contract(self):
        """Test building a futures contract."""
        spec = SymbolSpec(
            symbol="MES",
            securityType="FUT",
            exchange="CME",
            currency="USD",
        )
        contract = _build_contract(spec)

        assert contract.symbol == "MES"
        assert contract.secType == "FUT"
        assert contract.exchange == "CME"

    def test_build_future_with_expiry(self):
        """Test building a futures contract with specific expiry."""
        spec = SymbolSpec(
            symbol="MES",
            securityType="FUT",
            exchange="CME",
            currency="USD",
            expiry="2024-03-15",
        )
        contract = _build_contract(spec)

        assert contract.lastTradeDateOrContractMonth == "20240315"

    def test_build_option_contract(self):
        """Test building an option contract."""
        spec = SymbolSpec(
            symbol="AAPL",
            securityType="OPT",
            exchange="SMART",
            currency="USD",
            expiry="2024-03-15",
            strike=150.0,
            right="C",
        )
        contract = _build_contract(spec)

        assert contract.symbol == "AAPL"
        assert contract.secType == "OPT"
        assert contract.strike == 150.0
        assert contract.right == "C"
        assert contract.lastTradeDateOrContractMonth == "20240315"

    def test_build_option_missing_expiry_raises(self):
        """Test that option without expiry raises error."""
        spec = SymbolSpec(
            symbol="AAPL",
            securityType="OPT",
            strike=150.0,
            right="C",
        )
        with pytest.raises(ContractResolutionError, match="expiry"):
            _build_contract(spec)

    def test_build_option_missing_strike_raises(self):
        """Test that option without strike raises error."""
        spec = SymbolSpec(
            symbol="AAPL",
            securityType="OPT",
            expiry="2024-03-15",
            right="C",
        )
        with pytest.raises(ContractResolutionError, match="strike"):
            _build_contract(spec)

    def test_build_option_missing_right_raises(self):
        """Test that option without right raises error."""
        spec = SymbolSpec(
            symbol="AAPL",
            securityType="OPT",
            expiry="2024-03-15",
            strike=150.0,
        )
        with pytest.raises(ContractResolutionError, match="right"):
            _build_contract(spec)

    def test_build_index_contract(self):
        """Test building an index contract."""
        spec = SymbolSpec(
            symbol="SPX",
            securityType="IND",
            exchange="CBOE",
            currency="USD",
        )
        contract = _build_contract(spec)

        assert contract.symbol == "SPX"
        assert contract.secType == "IND"


# =============================================================================
# Unit Tests - Resolve Contract (Mocked)
# =============================================================================


class TestResolveContractMocked:
    """Test resolve_contract with mocked client."""

    def test_resolve_uses_cache(self):
        """Test that resolve uses cache on second call."""
        mock_client = MagicMock()
        mock_client.is_connected = True

        mock_qualified = MagicMock()
        mock_qualified.conId = 12345
        mock_qualified.exchange = "SMART"
        mock_qualified.currency = "USD"
        mock_client.ib.qualifyContracts.return_value = [mock_qualified]

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        # First call - should hit IBKR
        result1 = resolve_contract(spec, mock_client, use_cache=True)
        assert mock_client.ib.qualifyContracts.call_count == 1

        # Second call - should use cache
        result2 = resolve_contract(spec, mock_client, use_cache=True)
        assert mock_client.ib.qualifyContracts.call_count == 1  # Still 1

        assert result1 is result2

    def test_resolve_raises_when_not_connected(self):
        """Test that resolve raises when client not connected."""
        mock_client = MagicMock()
        mock_client.is_connected = False

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with pytest.raises(ContractResolutionError, match="not connected"):
            resolve_contract(spec, mock_client)

    def test_resolve_raises_when_no_contract_found(self):
        """Test that resolve raises when no contract matches."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ib.qualifyContracts.return_value = []

        spec = SymbolSpec(symbol="INVALIDXYZ", securityType="STK")

        with pytest.raises(ContractNotFoundError):
            resolve_contract(spec, mock_client)

    def test_resolve_contracts_multiple(self):
        """Test resolving multiple contracts."""
        mock_client = MagicMock()
        mock_client.is_connected = True

        def mock_qualify(contract):
            mock = MagicMock()
            mock.conId = hash(contract.symbol) % 100000
            mock.exchange = "SMART"
            mock.currency = "USD"
            return [mock]

        mock_client.ib.qualifyContracts.side_effect = mock_qualify

        specs = [
            SymbolSpec(symbol="AAPL", securityType="STK"),
            SymbolSpec(symbol="MSFT", securityType="STK"),
        ]

        results = resolve_contracts(specs, mock_client)

        assert "AAPL" in results
        assert "MSFT" in results
        assert results["AAPL"].conId != results["MSFT"].conId


# =============================================================================
# Integration Tests (require running IBKR Gateway)
# =============================================================================


@pytest.mark.integration
class TestContractResolutionIntegration:
    """Integration tests requiring running IBKR Gateway.

    Uses a shared client fixture with a unique client ID to avoid connection conflicts.
    """

    @pytest.fixture
    def client(self):
        """Create and connect client for tests."""
        import random

        client_id = random.randint(2000, 9999)
        client = IBKRClient(mode="paper", client_id=client_id)
        client.connect(timeout=10)
        yield client
        client.disconnect()

    def test_resolve_aapl_stock(self, client):
        """Test resolving AAPL stock."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")
        contract = resolve_contract(spec, client)

        assert contract.conId > 0
        assert contract.symbol == "AAPL"
        print(f"AAPL conId: {contract.conId}")

    def test_resolve_spy_etf(self, client):
        """Test resolving SPY ETF."""
        spec = SymbolSpec(symbol="SPY", securityType="ETF")
        contract = resolve_contract(spec, client)

        assert contract.conId > 0
        assert contract.symbol == "SPY"
        print(f"SPY conId: {contract.conId}")

    def test_resolve_mes_future(self, client):
        """Test resolving MES micro futures (front month)."""
        spec = SymbolSpec(symbol="MES", securityType="FUT")
        contract = resolve_contract(spec, client)

        assert contract.conId > 0
        assert contract.symbol == "MES"
        assert contract.lastTradeDateOrContractMonth is not None
        print(f"MES conId: {contract.conId}, expiry: {contract.lastTradeDateOrContractMonth}")

    def test_resolve_es_future(self, client):
        """Test resolving ES e-mini futures."""
        spec = SymbolSpec(symbol="ES", securityType="FUT")
        contract = resolve_contract(spec, client)

        assert contract.conId > 0
        assert contract.symbol == "ES"
        print(f"ES conId: {contract.conId}, expiry: {contract.lastTradeDateOrContractMonth}")

    def test_resolve_spx_index(self, client):
        """Test resolving SPX index."""
        spec = SymbolSpec(symbol="SPX", securityType="IND")
        contract = resolve_contract(spec, client)

        assert contract.conId > 0
        assert contract.symbol == "SPX"
        print(f"SPX conId: {contract.conId}")

    def test_resolve_multiple_symbols(self, client):
        """Test resolving multiple symbols at once."""
        specs = [
            SymbolSpec(symbol="AAPL", securityType="STK"),
            SymbolSpec(symbol="MSFT", securityType="STK"),
            SymbolSpec(symbol="GOOGL", securityType="STK"),
        ]

        results = resolve_contracts(specs, client)

        assert len(results) == 3
        for symbol, contract in results.items():
            assert contract.conId > 0
            print(f"{symbol}: conId={contract.conId}")

    def test_cache_hit_on_repeat(self, client):
        """Test that cache is used on repeat resolution."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        cache = get_contract_cache()
        initial_misses = cache.stats["misses"]

        # First call - cache miss
        contract1 = resolve_contract(spec, client)
        assert cache.stats["misses"] == initial_misses + 1

        initial_hits = cache.stats["hits"]

        # Second call - cache hit
        contract2 = resolve_contract(spec, client)
        assert cache.stats["hits"] == initial_hits + 1
        assert contract1.conId == contract2.conId

    def test_get_front_month_expiry(self, client):
        """Test getting front month expiry for futures."""
        expiry = get_front_month_expiry("MES", client)

        assert expiry is not None
        assert "-" in expiry  # Format: YYYY-MM-DD
        print(f"MES front month expiry: {expiry}")

    def test_invalid_symbol_raises(self, client):
        """Test that invalid symbol raises ContractNotFoundError."""
        spec = SymbolSpec(symbol="INVALIDXYZ123", securityType="STK")

        with pytest.raises((ContractNotFoundError, ContractResolutionError)):
            resolve_contract(spec, client)
