"""
Tests for market data retrieval.

Two test modes:
  - Unit tests: Test normalization and mocked IBKR responses
  - Integration tests: Test against running IBKR Gateway

Run unit tests only:
    pytest tests/test_market_data.py -m "not integration"

Run integration tests:
    pytest tests/test_market_data.py -m integration

Run all:
    pytest tests/test_market_data.py
"""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ibkr_core.client import IBKRClient
from ibkr_core.config import reset_config
from ibkr_core.contracts import get_contract_cache
from ibkr_core.market_data import (
    BAR_SIZE_MAP,
    DURATION_MAP,
    MarketDataError,
    MarketDataPermissionError,
    MarketDataTimeoutError,
    NoMarketDataError,
    PacingViolationError,
    get_historical_bars,
    get_quote,
    get_quotes,
    normalize_bar_size,
    normalize_duration,
    normalize_what_to_show,
)
from ibkr_core.models import Bar, Quote, SymbolSpec


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
# Unit Tests - Normalization Helpers
# =============================================================================


class TestNormalizeBarSize:
    """Test bar size normalization."""

    def test_minute_aliases(self):
        """Test minute bar size aliases."""
        assert normalize_bar_size("1m") == "1 min"
        assert normalize_bar_size("1min") == "1 min"
        assert normalize_bar_size("1 min") == "1 min"
        assert normalize_bar_size("5m") == "5 mins"
        assert normalize_bar_size("5min") == "5 mins"
        assert normalize_bar_size("5 mins") == "5 mins"
        assert normalize_bar_size("15m") == "15 mins"
        assert normalize_bar_size("30m") == "30 mins"

    def test_hour_aliases(self):
        """Test hour bar size aliases."""
        assert normalize_bar_size("1h") == "1 hour"
        assert normalize_bar_size("1hr") == "1 hour"
        assert normalize_bar_size("1 hour") == "1 hour"
        assert normalize_bar_size("2h") == "2 hours"
        assert normalize_bar_size("4h") == "4 hours"

    def test_day_week_month_aliases(self):
        """Test day/week/month bar size aliases."""
        assert normalize_bar_size("1d") == "1 day"
        assert normalize_bar_size("1 day") == "1 day"
        assert normalize_bar_size("1w") == "1 week"
        assert normalize_bar_size("1mo") == "1 month"

    def test_second_aliases(self):
        """Test second bar size aliases."""
        assert normalize_bar_size("1s") == "1 secs"
        assert normalize_bar_size("5s") == "5 secs"
        assert normalize_bar_size("30s") == "30 secs"

    def test_case_insensitive(self):
        """Test case insensitivity."""
        assert normalize_bar_size("1M") == "1 min"
        assert normalize_bar_size("1D") == "1 day"
        assert normalize_bar_size("1H") == "1 hour"

    def test_whitespace_handling(self):
        """Test whitespace is stripped."""
        assert normalize_bar_size("  1m  ") == "1 min"
        assert normalize_bar_size(" 1 day ") == "1 day"

    def test_invalid_bar_size_raises(self):
        """Test invalid bar size raises ValueError."""
        with pytest.raises(ValueError, match="Invalid bar_size"):
            normalize_bar_size("invalid")

        with pytest.raises(ValueError, match="Invalid bar_size"):
            normalize_bar_size("2d")  # 2 days not supported

        with pytest.raises(ValueError, match="Invalid bar_size"):
            normalize_bar_size("")


class TestNormalizeDuration:
    """Test duration normalization."""

    def test_day_aliases(self):
        """Test day duration aliases."""
        assert normalize_duration("1d") == "1 D"
        assert normalize_duration("5d") == "5 D"
        assert normalize_duration("30d") == "30 D"

    def test_week_aliases(self):
        """Test week duration aliases."""
        assert normalize_duration("1w") == "1 W"
        assert normalize_duration("2w") == "2 W"
        assert normalize_duration("4w") == "4 W"

    def test_month_aliases(self):
        """Test month duration aliases."""
        assert normalize_duration("1mo") == "1 M"
        assert normalize_duration("3mo") == "3 M"
        assert normalize_duration("6mo") == "6 M"

    def test_year_aliases(self):
        """Test year duration aliases."""
        assert normalize_duration("1y") == "1 Y"
        assert normalize_duration("2y") == "2 Y"

    def test_second_aliases(self):
        """Test second duration aliases."""
        assert normalize_duration("60s") == "60 S"
        assert normalize_duration("300s") == "300 S"

    def test_ibkr_format_passthrough(self):
        """Test IBKR format strings are normalized correctly."""
        assert normalize_duration("5 D") == "5 D"
        assert normalize_duration("1 W") == "1 W"
        assert normalize_duration("1 M") == "1 M"
        assert normalize_duration("1 Y") == "1 Y"

    def test_case_insensitive(self):
        """Test case insensitivity."""
        assert normalize_duration("1D") == "1 D"
        assert normalize_duration("1w") == "1 W"
        assert normalize_duration("5 d") == "5 D"

    def test_invalid_duration_raises(self):
        """Test invalid duration raises ValueError."""
        with pytest.raises(ValueError, match="Invalid duration"):
            normalize_duration("invalid")

        with pytest.raises(ValueError, match="Invalid duration"):
            normalize_duration("")


class TestNormalizeWhatToShow:
    """Test what_to_show normalization."""

    def test_valid_values(self):
        """Test valid what_to_show values."""
        assert normalize_what_to_show("TRADES") == "TRADES"
        assert normalize_what_to_show("MIDPOINT") == "MIDPOINT"
        assert normalize_what_to_show("BID") == "BID"
        assert normalize_what_to_show("ASK") == "ASK"
        assert normalize_what_to_show("BID_ASK") == "BID_ASK"
        assert normalize_what_to_show("HISTORICAL_VOLATILITY") == "HISTORICAL_VOLATILITY"
        assert normalize_what_to_show("OPTION_IMPLIED_VOLATILITY") == "OPTION_IMPLIED_VOLATILITY"

    def test_case_insensitive(self):
        """Test case insensitivity."""
        assert normalize_what_to_show("trades") == "TRADES"
        assert normalize_what_to_show("Trades") == "TRADES"
        assert normalize_what_to_show("midpoint") == "MIDPOINT"

    def test_whitespace_handling(self):
        """Test whitespace is stripped."""
        assert normalize_what_to_show("  TRADES  ") == "TRADES"

    def test_invalid_value_raises(self):
        """Test invalid value raises ValueError."""
        with pytest.raises(ValueError, match="Invalid what_to_show"):
            normalize_what_to_show("INVALID")

        with pytest.raises(ValueError, match="Invalid what_to_show"):
            normalize_what_to_show("")


# =============================================================================
# Unit Tests - Quote/Bar Models
# =============================================================================


class TestQuoteModel:
    """Test Quote model behavior."""

    def test_quote_serialization(self):
        """Test Quote serializes to dict correctly."""
        now = datetime.now(timezone.utc)
        quote = Quote(
            symbol="AAPL",
            conId=265598,
            bid=150.00,
            ask=150.05,
            last=150.02,
            bidSize=100,
            askSize=200,
            lastSize=50,
            volume=1000000,
            timestamp=now,
            source="IBKR_SNAPSHOT",
        )

        data = quote.model_dump()

        assert data["symbol"] == "AAPL"
        assert data["conId"] == 265598
        assert data["bid"] == 150.00
        assert data["ask"] == 150.05
        assert data["last"] == 150.02
        assert data["source"] == "IBKR_SNAPSHOT"

    def test_quote_defaults(self):
        """Test Quote default values."""
        now = datetime.now(timezone.utc)
        quote = Quote(
            symbol="TEST",
            conId=1,
            timestamp=now,
            source="TEST",
        )

        assert quote.bid == 0.0
        assert quote.ask == 0.0
        assert quote.last == 0.0
        assert quote.bidSize == 0.0
        assert quote.askSize == 0.0
        assert quote.lastSize == 0.0
        assert quote.volume == 0.0

    def test_quote_timestamp_required(self):
        """Test that timestamp is required."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            Quote(
                symbol="TEST",
                conId=1,
                source="TEST",
            )


class TestBarModel:
    """Test Bar model behavior."""

    def test_bar_serialization(self):
        """Test Bar serializes to dict correctly."""
        bar_time = datetime(2024, 12, 14, 10, 0, 0, tzinfo=timezone.utc)
        bar = Bar(
            symbol="AAPL",
            time=bar_time,
            open=150.00,
            high=151.00,
            low=149.50,
            close=150.50,
            volume=100000,
            barSize="1 day",
            source="IBKR_HISTORICAL",
        )

        data = bar.model_dump()

        assert data["symbol"] == "AAPL"
        assert data["open"] == 150.00
        assert data["high"] == 151.00
        assert data["low"] == 149.50
        assert data["close"] == 150.50
        assert data["volume"] == 100000
        assert data["barSize"] == "1 day"

    def test_bar_all_fields_required(self):
        """Test that all fields are required."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            Bar(
                symbol="TEST",
                time=datetime.now(timezone.utc),
                open=100.0,
                high=101.0,
                low=99.0,
                # Missing: close, volume, barSize, source
            )


# =============================================================================
# Unit Tests - get_quote (Mocked)
# =============================================================================


class MockErrorEvent:
    """Mock for ib_insync error event that supports += and -= operators."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self


class FakeBroker:
    """Minimal explicit broker fake for seam-focused tests."""

    def __init__(self):
        self.error_handlers = []
        self.market_data_return_value = None
        self.historical_data_return_value = []
        self.place_order_return_value = None
        self.open_trades_return_value = []
        self.trades_return_value = []
        self.market_data_requests = []
        self.cancel_market_data_requests = []
        self.historical_requests = []
        self.place_order_requests = []
        self.cancel_order_requests = []
        self.sleep_calls = []
        self.sleep_hook = None

    def add_error_handler(self, handler):
        self.error_handlers.append(handler)

    def remove_error_handler(self, handler):
        if handler in self.error_handlers:
            self.error_handlers.remove(handler)

    def request_market_data(self, contract, generic_tick_list="", *, snapshot=False):
        self.market_data_requests.append((contract, generic_tick_list, snapshot))
        return self.market_data_return_value

    def cancel_market_data(self, contract):
        self.cancel_market_data_requests.append(contract)

    def request_historical_data(
        self,
        contract,
        *,
        end_date_time,
        duration_str,
        bar_size_setting,
        what_to_show,
        use_rth,
        format_date,
        timeout,
    ):
        self.historical_requests.append(
            {
                "contract": contract,
                "end_date_time": end_date_time,
                "duration_str": duration_str,
                "bar_size_setting": bar_size_setting,
                "what_to_show": what_to_show,
                "use_rth": use_rth,
                "format_date": format_date,
                "timeout": timeout,
            }
        )
        return self.historical_data_return_value

    def place_order(self, contract, order):
        self.place_order_requests.append((contract, order))
        return self.place_order_return_value

    def cancel_order(self, order):
        self.cancel_order_requests.append(order)

    def open_trades(self):
        return list(self.open_trades_return_value)

    def trades(self):
        return list(self.trades_return_value)

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        if self.sleep_hook is not None:
            self.sleep_hook(seconds)


def make_explicit_broker_client(broker):
    client = MagicMock()
    client.broker = broker
    client.is_connected = True
    client.ensure_connected = MagicMock()
    return client


class TestGetQuoteMocked:
    """Test get_quote with mocked IBKR."""

    def _setup_mock_error_event(self, mock_client):
        """Helper to setup mock error event."""
        mock_error_event = MockErrorEvent()
        mock_client.ib.errorEvent = mock_error_event
        return mock_error_event.handlers

    def test_get_quote_success(self):
        """Test successful quote retrieval."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        # Mock contract resolution
        mock_contract = MagicMock()
        mock_contract.conId = 265598

        # Mock ticker with data
        mock_ticker = MagicMock()
        mock_ticker.bid = 150.00
        mock_ticker.ask = 150.05
        mock_ticker.last = 150.02
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.lastSize = 50
        mock_ticker.volume = 1000000

        # Setup error event (no errors will fire)
        self._setup_mock_error_event(mock_client)

        mock_client.ib.reqMktData.return_value = mock_ticker
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelMktData = MagicMock()

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            quote = get_quote(spec, mock_client, timeout_s=1.0)

        assert quote.symbol == "AAPL"
        assert quote.conId == 265598
        assert quote.bid == 150.00
        assert quote.ask == 150.05
        assert quote.last == 150.02
        assert quote.source == "IBKR_SNAPSHOT"
        assert quote.timestamp is not None

    def test_get_quote_timeout(self):
        """Test quote timeout behavior."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        # Mock ticker with no data (nan values as ib_insync uses)
        mock_ticker = MagicMock()
        mock_ticker.bid = float("nan")
        mock_ticker.ask = float("nan")
        mock_ticker.last = float("nan")

        # Setup error event (no errors will fire)
        self._setup_mock_error_event(mock_client)

        mock_client.ib.reqMktData.return_value = mock_ticker
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelMktData = MagicMock()

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            with pytest.raises(MarketDataTimeoutError, match="Timeout"):
                get_quote(spec, mock_client, timeout_s=0.1)

    def test_get_quote_permission_error(self):
        """Test permission error handling."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_ticker = MagicMock()
        mock_ticker.bid = float("nan")
        mock_ticker.ask = float("nan")
        mock_ticker.last = float("nan")

        # Setup mock error event
        mock_error_event = MockErrorEvent()
        mock_client.ib.errorEvent = mock_error_event
        mock_client.ib.reqMktData.return_value = mock_ticker

        # Simulate error being fired during sleep
        def mock_sleep(duration):
            if mock_error_event.handlers:
                # Fire the permission error
                mock_error_event.handlers[0](
                    1, 10089, "not subscribed to market data", mock_contract
                )

        mock_client.ib.sleep = mock_sleep
        mock_client.ib.cancelMktData = MagicMock()

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            with pytest.raises(MarketDataPermissionError, match="10089"):
                get_quote(spec, mock_client, timeout_s=0.5)

    def test_get_quote_uses_explicit_broker(self):
        """Test quote retrieval through an explicit broker adapter."""
        broker = FakeBroker()
        client = make_explicit_broker_client(broker)

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_ticker = MagicMock()
        mock_ticker.bid = 150.00
        mock_ticker.ask = 150.05
        mock_ticker.last = 150.02
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.lastSize = 50
        mock_ticker.volume = 1000000
        broker.market_data_return_value = mock_ticker

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            quote = get_quote(spec, client, timeout_s=1.0)

        assert quote.symbol == "AAPL"
        assert broker.market_data_requests == [(mock_contract, "", True)]
        assert broker.cancel_market_data_requests == [mock_contract]
        assert broker.error_handlers == []


class TestGetQuotesMocked:
    """Test get_quotes (batch) with mocked IBKR."""

    def _setup_mock_error_event(self, mock_client):
        """Helper to setup mock error event."""
        mock_error_event = MockErrorEvent()
        mock_client.ib.errorEvent = mock_error_event
        return mock_error_event.handlers

    def test_get_quotes_empty_list(self):
        """Test empty specs list returns empty."""
        mock_client = MagicMock()
        quotes = get_quotes([], mock_client)
        assert quotes == []

    def test_get_quotes_preserves_order(self):
        """Test quotes are returned in same order as specs."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        # Mock contracts
        contracts = {
            "AAPL": MagicMock(conId=1, symbol="AAPL"),
            "MSFT": MagicMock(conId=2, symbol="MSFT"),
            "GOOGL": MagicMock(conId=3, symbol="GOOGL"),
        }

        def mock_resolve(spec, client, use_cache=True):
            return contracts[spec.symbol]

        # Mock tickers
        tickers = {}
        for symbol in ["AAPL", "MSFT", "GOOGL"]:
            ticker = MagicMock()
            ticker.bid = 100.0 + hash(symbol) % 100
            ticker.ask = ticker.bid + 0.05
            ticker.last = ticker.bid + 0.02
            ticker.bidSize = 100
            ticker.askSize = 200
            ticker.lastSize = 50
            ticker.volume = 1000000
            tickers[symbol] = ticker

        def mock_req_mkt_data(contract, genericTickList, snapshot):
            for symbol, c in contracts.items():
                if c == contract:
                    return tickers[symbol]
            return MagicMock()

        # Setup error event (no errors will fire)
        self._setup_mock_error_event(mock_client)

        mock_client.ib.reqMktData.side_effect = mock_req_mkt_data
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelMktData = MagicMock()

        specs = [
            SymbolSpec(symbol="AAPL", securityType="STK"),
            SymbolSpec(symbol="MSFT", securityType="STK"),
            SymbolSpec(symbol="GOOGL", securityType="STK"),
        ]

        with patch("ibkr_core.market_data.resolve_contract", side_effect=mock_resolve):
            quotes = get_quotes(specs, mock_client, timeout_s=0.5)

        assert len(quotes) == 3
        assert quotes[0].symbol == "AAPL"
        assert quotes[1].symbol == "MSFT"
        assert quotes[2].symbol == "GOOGL"


# =============================================================================
# Unit Tests - get_historical_bars (Mocked)
# =============================================================================


class TestGetHistoricalBarsMocked:
    """Test get_historical_bars with mocked IBKR."""

    def test_get_historical_bars_success(self):
        """Test successful historical bars retrieval."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        # Create mock bars
        mock_bars = []
        for i in range(5):
            mock_bar = MagicMock()
            mock_bar.date = datetime(2024, 12, 10 + i, 16, 0, 0, tzinfo=timezone.utc)
            mock_bar.open = 150.0 + i
            mock_bar.high = 151.0 + i
            mock_bar.low = 149.0 + i
            mock_bar.close = 150.5 + i
            mock_bar.volume = 100000 * (i + 1)
            mock_bars.append(mock_bar)

        mock_client.ib.reqHistoricalData.return_value = mock_bars
        mock_client.ib.errorEvent = MagicMock()
        mock_client.ib.errorEvent.__iadd__ = MagicMock()
        mock_client.ib.errorEvent.__isub__ = MagicMock()

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            bars = get_historical_bars(
                spec,
                mock_client,
                bar_size="1d",
                duration="5d",
                what_to_show="TRADES",
                timeout_s=5.0,
            )

        assert len(bars) == 5
        assert bars[0].symbol == "AAPL"
        assert bars[0].barSize == "1 day"
        assert bars[0].source == "IBKR_HISTORICAL"
        assert bars[0].open == 150.0

    def test_get_historical_bars_no_data(self):
        """Test no data raises NoMarketDataError."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_client.ib.reqHistoricalData.return_value = []
        mock_client.ib.errorEvent = MagicMock()
        mock_client.ib.errorEvent.__iadd__ = MagicMock()
        mock_client.ib.errorEvent.__isub__ = MagicMock()

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            with pytest.raises(NoMarketDataError, match="No historical data"):
                get_historical_bars(
                    spec,
                    mock_client,
                    bar_size="1d",
                    duration="5d",
                )

    def test_get_historical_bars_invalid_bar_size(self):
        """Test invalid bar size raises ValueError."""
        mock_client = MagicMock()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with pytest.raises(ValueError, match="Invalid bar_size"):
            get_historical_bars(
                spec,
                mock_client,
                bar_size="invalid",
                duration="5d",
            )

    def test_get_historical_bars_invalid_duration(self):
        """Test invalid duration raises ValueError."""
        mock_client = MagicMock()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with pytest.raises(ValueError, match="Invalid duration"):
            get_historical_bars(
                spec,
                mock_client,
                bar_size="1d",
                duration="invalid",
            )

    def test_get_historical_bars_invalid_what_to_show(self):
        """Test invalid what_to_show raises ValueError."""
        mock_client = MagicMock()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with pytest.raises(ValueError, match="Invalid what_to_show"):
            get_historical_bars(
                spec,
                mock_client,
                bar_size="1d",
                duration="5d",
                what_to_show="INVALID",
            )

    def test_get_historical_bars_uses_explicit_broker(self):
        """Test historical bars retrieval through an explicit broker adapter."""
        broker = FakeBroker()
        client = make_explicit_broker_client(broker)

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_bar = MagicMock()
        mock_bar.date = datetime(2024, 12, 10, 16, 0, 0, tzinfo=timezone.utc)
        mock_bar.open = 150.0
        mock_bar.high = 151.0
        mock_bar.low = 149.0
        mock_bar.close = 150.5
        mock_bar.volume = 100000
        broker.historical_data_return_value = [mock_bar]

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            bars = get_historical_bars(
                spec,
                client,
                bar_size="1d",
                duration="5d",
                what_to_show="TRADES",
                timeout_s=5.0,
            )

        assert len(bars) == 1
        assert bars[0].symbol == "AAPL"
        assert broker.historical_requests[0]["contract"] is mock_contract
        assert broker.error_handlers == []


# =============================================================================
# Unit Tests - Error Handling
# =============================================================================


class TestErrorHandling:
    """Test error detection and exception raising."""

    def test_exception_hierarchy(self):
        """Test exception inheritance."""
        assert issubclass(MarketDataPermissionError, MarketDataError)
        assert issubclass(NoMarketDataError, MarketDataError)
        assert issubclass(PacingViolationError, MarketDataError)
        assert issubclass(MarketDataTimeoutError, MarketDataError)

    def test_market_data_error_message(self):
        """Test exception messages are preserved."""
        err = MarketDataPermissionError("Test permission error")
        assert str(err) == "Test permission error"

        err = PacingViolationError("Test pacing error")
        assert str(err) == "Test pacing error"


# =============================================================================
# Unit Tests - QuoteMode and Streaming
# =============================================================================

from ibkr_core.market_data import (
    QuoteMode,
    StreamingQuote,
    get_quote_with_mode,
    get_streaming_quote,
)


class TestQuoteMode:
    """Test QuoteMode enum."""

    def test_quote_mode_values(self):
        """Test QuoteMode enum values."""
        assert QuoteMode.SNAPSHOT.value == "snapshot"
        assert QuoteMode.STREAMING.value == "streaming"

    def test_quote_mode_membership(self):
        """Test QuoteMode membership."""
        assert QuoteMode.SNAPSHOT in QuoteMode
        assert QuoteMode.STREAMING in QuoteMode


class TestStreamingQuoteMocked:
    """Test StreamingQuote with mocked IBKR."""

    @staticmethod
    def _make_streaming_client():
        broker = FakeBroker()
        client = make_explicit_broker_client(broker)
        return client, broker

    def test_streaming_quote_init(self):
        """Test StreamingQuote initialization."""
        mock_client = MagicMock()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        stream = StreamingQuote(spec, mock_client, timeout_s=5.0)

        assert stream.symbol == "AAPL"
        assert not stream.is_active

    def test_streaming_quote_start_success(self):
        """Test successful streaming start."""
        mock_client, broker = self._make_streaming_client()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_ticker = MagicMock()
        mock_ticker.bid = 150.00
        mock_ticker.ask = 150.05
        mock_ticker.last = 150.02
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.lastSize = 50
        mock_ticker.volume = 1000000

        broker.market_data_return_value = mock_ticker

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            stream = StreamingQuote(spec, mock_client, timeout_s=1.0)
            stream.start()

            assert stream.is_active

            # Get current quote
            quote = stream.get_current()
            assert quote.symbol == "AAPL"
            assert quote.source == "IBKR_STREAMING"

            stream.stop()
            assert not stream.is_active
            assert broker.market_data_requests == [(mock_contract, "", False)]
            assert broker.cancel_market_data_requests == [mock_contract]
            assert broker.error_handlers == []

    def test_streaming_quote_context_manager(self):
        """Test StreamingQuote as context manager."""
        mock_client, broker = self._make_streaming_client()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_ticker = MagicMock()
        mock_ticker.bid = 150.00
        mock_ticker.ask = 150.05
        mock_ticker.last = 150.02
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.lastSize = 50
        mock_ticker.volume = 1000000

        broker.market_data_return_value = mock_ticker

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            with StreamingQuote(spec, mock_client, timeout_s=1.0) as stream:
                assert stream.is_active
                quote = stream.get_current()
                assert quote.symbol == "AAPL"

            # After exiting context, should be stopped
            assert not stream.is_active
            assert broker.cancel_market_data_requests == [mock_contract]

    def test_streaming_quote_updates_generator(self):
        """Test streaming quote updates generator."""
        mock_client, broker = self._make_streaming_client()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_ticker = MagicMock()
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.lastSize = 50
        mock_ticker.volume = 1000000

        # Simulate changing prices - start with different initial values
        price_sequence = [
            (150.01, 150.06, 150.03),  # First change
            (150.02, 150.07, 150.04),  # Second change
            (150.03, 150.08, 150.05),  # Third change
        ]
        call_count = [0]

        def mock_sleep(duration):
            if call_count[0] < len(price_sequence):
                bid, ask, last = price_sequence[call_count[0]]
                mock_ticker.bid = bid
                mock_ticker.ask = ask
                mock_ticker.last = last
                call_count[0] += 1

        broker.sleep_hook = mock_sleep
        # Set initial values DIFFERENT from price_sequence to trigger changes
        mock_ticker.bid = 150.00
        mock_ticker.ask = 150.05
        mock_ticker.last = 150.02

        broker.market_data_return_value = mock_ticker

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            with StreamingQuote(spec, mock_client, timeout_s=1.0) as stream:
                # Use duration_s as safeguard to prevent infinite loop
                updates = list(stream.updates(max_updates=3, duration_s=2.0))
                assert len(updates) <= 3
                for quote in updates:
                    assert quote.symbol == "AAPL"
                    assert quote.source == "IBKR_STREAMING"
            assert broker.cancel_market_data_requests == [mock_contract]

    def test_streaming_quote_timeout(self):
        """Test streaming quote timeout."""
        mock_client, broker = self._make_streaming_client()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        # Ticker with no data
        mock_ticker = MagicMock()
        mock_ticker.bid = float("nan")
        mock_ticker.ask = float("nan")
        mock_ticker.last = float("nan")

        broker.market_data_return_value = mock_ticker

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            stream = StreamingQuote(spec, mock_client, timeout_s=0.1)
            with pytest.raises(MarketDataTimeoutError):
                stream.start()
        assert broker.cancel_market_data_requests == [mock_contract]

    def test_get_current_without_start_raises(self):
        """Test get_current raises when not started."""
        mock_client = MagicMock()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        stream = StreamingQuote(spec, mock_client)
        with pytest.raises(MarketDataError, match="not active"):
            stream.get_current()


class TestGetQuoteWithModeMocked:
    """Test get_quote_with_mode function."""

    def _setup_mock_error_event(self, mock_client):
        """Helper to setup mock error event."""
        mock_error_event = MockErrorEvent()
        mock_client.ib.errorEvent = mock_error_event
        return mock_error_event

    def test_snapshot_mode(self):
        """Test snapshot mode."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.ensure_connected = MagicMock()

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_ticker = MagicMock()
        mock_ticker.bid = 150.00
        mock_ticker.ask = 150.05
        mock_ticker.last = 150.02
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.lastSize = 50
        mock_ticker.volume = 1000000

        self._setup_mock_error_event(mock_client)
        mock_client.ib.reqMktData.return_value = mock_ticker
        mock_client.ib.sleep = MagicMock()
        mock_client.ib.cancelMktData = MagicMock()

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            quote = get_quote_with_mode(spec, mock_client, mode=QuoteMode.SNAPSHOT, timeout_s=1.0)

        assert quote.symbol == "AAPL"
        assert quote.source == "IBKR_SNAPSHOT"

    def test_streaming_mode(self):
        """Test streaming mode."""
        broker = FakeBroker()
        mock_client = make_explicit_broker_client(broker)

        mock_contract = MagicMock()
        mock_contract.conId = 265598

        mock_ticker = MagicMock()
        mock_ticker.bid = 150.00
        mock_ticker.ask = 150.05
        mock_ticker.last = 150.02
        mock_ticker.bidSize = 100
        mock_ticker.askSize = 200
        mock_ticker.lastSize = 50
        mock_ticker.volume = 1000000

        broker.market_data_return_value = mock_ticker

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        with patch("ibkr_core.market_data.resolve_contract", return_value=mock_contract):
            quote = get_quote_with_mode(spec, mock_client, mode=QuoteMode.STREAMING, timeout_s=1.0)

        assert quote.symbol == "AAPL"
        assert quote.source == "IBKR_STREAMING"
        assert broker.cancel_market_data_requests == [mock_contract]


class TestGetStreamingQuote:
    """Test get_streaming_quote factory function."""

    def test_returns_streaming_quote_instance(self):
        """Test factory returns StreamingQuote."""
        mock_client = MagicMock()
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        stream = get_streaming_quote(spec, mock_client, timeout_s=5.0)

        assert isinstance(stream, StreamingQuote)
        assert stream.symbol == "AAPL"
        assert not stream.is_active


# =============================================================================
# Integration Tests (require running IBKR Gateway)
# =============================================================================


@pytest.mark.integration
class TestMarketDataIntegration:
    """Integration tests requiring running IBKR Gateway.

    These tests verify actual market data retrieval from IBKR paper gateway.
    """

    @pytest.fixture
    def client(self):
        """Create and connect client for tests."""
        import random

        client_id = random.randint(3000, 9999)
        client = IBKRClient(mode="paper", client_id=client_id)
        client.connect(timeout=10)
        yield client
        client.disconnect()

    def test_get_quote_aapl(self, client):
        """Test getting quote for AAPL stock."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            quote = get_quote(spec, client, timeout_s=10.0)

            assert quote.symbol == "AAPL"
            assert quote.conId > 0
            assert quote.timestamp is not None
            assert quote.source == "IBKR_SNAPSHOT"

            print(f"AAPL Quote: bid={quote.bid}, ask={quote.ask}, last={quote.last}")

            # At least one price should be available (unless market closed and no permissions)
            # We don't assert > 0 because permissions might limit data

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_get_quote_spy_etf(self, client):
        """Test getting quote for SPY ETF."""
        spec = SymbolSpec(symbol="SPY", securityType="ETF")

        try:
            quote = get_quote(spec, client, timeout_s=10.0)

            assert quote.symbol == "SPY"
            assert quote.conId > 0
            print(f"SPY Quote: bid={quote.bid}, ask={quote.ask}, last={quote.last}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_get_quote_mes_future(self, client):
        """Test getting quote for MES micro futures."""
        spec = SymbolSpec(symbol="MES", securityType="FUT")

        try:
            quote = get_quote(spec, client, timeout_s=10.0)

            assert quote.symbol == "MES"
            assert quote.conId > 0
            print(f"MES Quote: bid={quote.bid}, ask={quote.ask}, last={quote.last}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_get_quotes_batch(self, client):
        """Test batch quote retrieval."""
        specs = [
            SymbolSpec(symbol="AAPL", securityType="STK"),
            SymbolSpec(symbol="MSFT", securityType="STK"),
        ]

        try:
            quotes = get_quotes(specs, client, timeout_s=15.0)

            assert len(quotes) == 2
            assert quotes[0].symbol == "AAPL"
            assert quotes[1].symbol == "MSFT"

            for q in quotes:
                print(f"{q.symbol} Quote: bid={q.bid}, ask={q.ask}, last={q.last}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_get_historical_bars_aapl(self, client):
        """Test getting historical bars for AAPL."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            bars = get_historical_bars(
                spec,
                client,
                bar_size="1d",
                duration="5d",
                what_to_show="TRADES",
                rth_only=True,
                timeout_s=30.0,
            )

            assert len(bars) > 0
            assert bars[0].symbol == "AAPL"
            assert bars[0].barSize == "1 day"
            assert bars[0].source == "IBKR_HISTORICAL"

            # Verify OHLC values are reasonable
            for bar in bars:
                assert bar.open > 0
                assert bar.high >= bar.open
                assert bar.high >= bar.close
                assert bar.low <= bar.open
                assert bar.low <= bar.close
                assert bar.close > 0

            print(f"Retrieved {len(bars)} bars for AAPL")
            for bar in bars[:3]:  # Print first 3
                print(
                    f"  {bar.time}: O={bar.open}, H={bar.high}, L={bar.low}, C={bar.close}, V={bar.volume}"
                )

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except NoMarketDataError as e:
            pytest.skip(f"Skipped due to no data: {e}")

    def test_get_historical_bars_intraday(self, client):
        """Test getting intraday bars."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            bars = get_historical_bars(
                spec,
                client,
                bar_size="5m",
                duration="1d",
                what_to_show="TRADES",
                rth_only=True,
                timeout_s=30.0,
            )

            assert len(bars) > 0
            assert bars[0].barSize == "5 mins"

            print(f"Retrieved {len(bars)} intraday bars for AAPL")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except NoMarketDataError as e:
            pytest.skip(f"Skipped due to no data: {e}")

    def test_get_historical_bars_spy_etf(self, client):
        """Test getting historical bars for SPY ETF."""
        spec = SymbolSpec(symbol="SPY", securityType="ETF")

        try:
            bars = get_historical_bars(
                spec,
                client,
                bar_size="1d",
                duration="1w",
                what_to_show="TRADES",
                rth_only=True,
                timeout_s=30.0,
            )

            assert len(bars) > 0
            print(f"Retrieved {len(bars)} bars for SPY")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except NoMarketDataError as e:
            pytest.skip(f"Skipped due to no data: {e}")

    def test_get_historical_bars_mes_future(self, client):
        """Test getting historical bars for MES futures."""
        spec = SymbolSpec(symbol="MES", securityType="FUT")

        try:
            bars = get_historical_bars(
                spec,
                client,
                bar_size="1d",
                duration="5d",
                what_to_show="TRADES",
                rth_only=False,  # Futures trade nearly 24h
                timeout_s=30.0,
            )

            assert len(bars) > 0
            print(f"Retrieved {len(bars)} bars for MES")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except NoMarketDataError as e:
            pytest.skip(f"Skipped due to no data: {e}")

    def test_invalid_symbol_raises(self, client):
        """Test that invalid symbol raises appropriate error."""
        spec = SymbolSpec(symbol="INVALIDXYZ999", securityType="STK")

        from ibkr_core.contracts import ContractResolutionError

        with pytest.raises((ContractResolutionError, MarketDataError)):
            get_quote(spec, client, timeout_s=5.0)

    def test_quotes_preserve_order(self, client):
        """Test that batch quotes preserve input order."""
        specs = [
            SymbolSpec(symbol="MSFT", securityType="STK"),
            SymbolSpec(symbol="AAPL", securityType="STK"),  # Reversed from alphabetical
        ]

        try:
            quotes = get_quotes(specs, client, timeout_s=15.0)

            assert len(quotes) == 2
            assert quotes[0].symbol == "MSFT"  # First in input
            assert quotes[1].symbol == "AAPL"  # Second in input

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")

    # =========================================================================
    # Streaming Quote Integration Tests
    # =========================================================================

    def test_streaming_quote_aapl(self, client):
        """Test streaming quote for AAPL stock."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            with StreamingQuote(spec, client, timeout_s=15.0) as stream:
                assert stream.is_active

                # Get current quote
                quote = stream.get_current()
                assert quote.symbol == "AAPL"
                assert quote.conId > 0
                assert quote.source == "IBKR_STREAMING"

                print(f"AAPL Streaming Quote: bid={quote.bid}, ask={quote.ask}, last={quote.last}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_streaming_quote_updates(self, client):
        """Test streaming quote updates generator."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            with StreamingQuote(spec, client, timeout_s=15.0) as stream:
                # Get a few updates
                updates = []
                for quote in stream.updates(max_updates=3, poll_interval_s=0.5):
                    updates.append(quote)
                    print(f"  Update {len(updates)}: bid={quote.bid}, ask={quote.ask}")

                # Should have received up to 3 updates
                assert len(updates) <= 3
                for quote in updates:
                    assert quote.symbol == "AAPL"
                    assert quote.source == "IBKR_STREAMING"

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_streaming_quote_duration_limit(self, client):
        """Test streaming quote with duration limit."""
        import time

        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            with StreamingQuote(spec, client, timeout_s=15.0) as stream:
                start = time.time()

                # Get updates for max 2 seconds
                updates = list(stream.updates(duration_s=2.0, poll_interval_s=0.3))
                elapsed = time.time() - start

                print(f"Received {len(updates)} updates in {elapsed:.2f}s")
                # Should stop after ~2 seconds
                assert elapsed < 3.0

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_get_quote_with_mode_snapshot(self, client):
        """Test get_quote_with_mode in snapshot mode."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            quote = get_quote_with_mode(spec, client, mode=QuoteMode.SNAPSHOT, timeout_s=10.0)

            assert quote.symbol == "AAPL"
            assert quote.source == "IBKR_SNAPSHOT"
            print(f"Snapshot Quote: bid={quote.bid}, ask={quote.ask}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_get_quote_with_mode_streaming(self, client):
        """Test get_quote_with_mode in streaming mode."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        try:
            quote = get_quote_with_mode(spec, client, mode=QuoteMode.STREAMING, timeout_s=15.0)

            assert quote.symbol == "AAPL"
            assert quote.source == "IBKR_STREAMING"
            print(f"Streaming Quote: bid={quote.bid}, ask={quote.ask}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_streaming_quote_spy_etf(self, client):
        """Test streaming quote for SPY ETF."""
        spec = SymbolSpec(symbol="SPY", securityType="ETF")

        try:
            with StreamingQuote(spec, client, timeout_s=15.0) as stream:
                quote = stream.get_current()
                assert quote.symbol == "SPY"
                assert quote.source == "IBKR_STREAMING"
                print(f"SPY Streaming Quote: bid={quote.bid}, ask={quote.ask}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")

    def test_streaming_quote_mes_future(self, client):
        """Test streaming quote for MES micro futures."""
        spec = SymbolSpec(symbol="MES", securityType="FUT")

        try:
            with StreamingQuote(spec, client, timeout_s=15.0) as stream:
                quote = stream.get_current()
                assert quote.symbol == "MES"
                assert quote.source == "IBKR_STREAMING"
                print(f"MES Streaming Quote: bid={quote.bid}, ask={quote.ask}")

        except MarketDataPermissionError as e:
            pytest.skip(f"Skipped due to permissions: {e}")
        except MarketDataTimeoutError as e:
            pytest.skip(f"Skipped due to timeout: {e}")
