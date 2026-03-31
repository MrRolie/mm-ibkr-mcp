"""
End-to-end API tests in simulation mode.

Verifies that the API works correctly when using the simulated
IBKR client, allowing full integration testing without a live gateway.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ibkr_core.contracts import get_contract_cache
from ibkr_core.market_data import (
    get_historical_bars,
    get_option_chain,
    get_option_snapshot,
    get_quote as get_core_quote,
)
from ibkr_core.models import OrderSpec, SymbolSpec
from ibkr_core import orders as orders_core
from ibkr_core.simulation import SimulatedIBKRClient, SimulatedQuote


class TestSimulationModeDetection:
    """Tests for simulation mode detection and configuration."""

    def test_get_ibkr_client_simulation_mode(self, monkeypatch):
        from ibkr_core.simulation import get_ibkr_client

        monkeypatch.setenv("IBKR_MODE", "simulation")
        client = get_ibkr_client()

        assert isinstance(client, SimulatedIBKRClient)

    def test_simulation_client_mode_property(self):
        client = SimulatedIBKRClient()
        assert client.mode == "simulation"

    def test_simulation_client_ignores_trading_mode(self):
        # SimulatedIBKRClient always reports mode as "simulation"
        client = SimulatedIBKRClient()
        assert client.mode == "simulation"


class TestSimulatedMarketData:
    """Tests for market data in simulation mode."""

    @pytest.fixture
    def client(self):
        client = SimulatedIBKRClient()
        client.connect()
        yield client
        client.disconnect()

    def test_quote_has_all_fields(self, client):
        quote = client.get_quote("AAPL")

        assert quote.symbol == "AAPL"
        assert isinstance(quote.bid, float)
        assert isinstance(quote.ask, float)
        assert isinstance(quote.last, float)
        assert isinstance(quote.bid_size, int)
        assert isinstance(quote.ask_size, int)
        assert quote.bid > 0
        assert quote.ask > quote.bid
        assert quote.last > 0

    def test_quote_for_common_symbols(self, client):
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "SPY"]

        for symbol in symbols:
            quote = client.get_quote(symbol)
            assert quote.symbol == symbol
            assert quote.bid > 0

    def test_quote_spread_is_narrow(self, client):
        quote = client.get_quote("SPY")
        spread_pct = (quote.ask - quote.bid) / quote.mid * 100

        # SPY should have very tight spread
        assert spread_pct < 0.1


class TestSimulatedOrderFlow:
    """Tests for complete order flow in simulation mode."""

    @pytest.fixture
    def client(self):
        client = SimulatedIBKRClient()
        client.connect()
        client.clear_orders()
        yield client
        client.disconnect()

    def test_full_market_order_lifecycle(self, client):
        # Submit order
        order = client.submit_order(
            symbol="AAPL",
            side="BUY",
            quantity=100,
            order_type="MKT",
        )

        # Verify filled
        assert order.status == "Filled"
        assert order.filled_quantity == 100
        assert order.fill_price > 0

        # Verify in registry
        retrieved = client.get_order(order.order_id)
        assert retrieved.status == "Filled"

    def test_limit_order_and_cancel(self, client):
        quote = client.get_quote("AAPL")

        # Submit limit order that won't fill
        order = client.submit_order(
            symbol="AAPL",
            side="BUY",
            quantity=50,
            order_type="LMT",
            limit_price=quote.bid * 0.50,  # Way below market
        )

        assert order.status == "Submitted"

        # Cancel it
        success = client.cancel_order(order.order_id)
        assert success is True

        # Verify cancelled
        updated = client.get_order(order.order_id)
        assert updated.status == "Cancelled"

    def test_multiple_orders_tracked(self, client):
        # Submit multiple orders
        order1 = client.submit_order("AAPL", "BUY", 10, "MKT")
        order2 = client.submit_order("MSFT", "BUY", 20, "MKT")
        order3 = client.submit_order("GOOGL", "SELL", 5, "MKT")

        all_orders = client.get_all_orders()
        assert len(all_orders) == 3

        symbols = {o.symbol for o in all_orders}
        assert symbols == {"AAPL", "MSFT", "GOOGL"}


class TestSimulatedAccountInfo:
    """Tests for account information in simulation mode."""

    def test_managed_accounts_when_connected(self):
        client = SimulatedIBKRClient(account_id="TEST123")
        client.connect()

        assert client.managed_accounts == ["TEST123"]
        client.disconnect()

    def test_managed_accounts_when_disconnected(self):
        client = SimulatedIBKRClient()
        assert client.managed_accounts == []

    def test_server_time_returns_current_time(self):
        client = SimulatedIBKRClient()
        client.connect()

        from datetime import datetime

        before = datetime.now()
        server_time = client.get_server_time()
        after = datetime.now()

        assert before <= server_time <= after
        client.disconnect()


class TestSimulatedIBInterface:
    """Tests for SimulatedIB compatibility interface."""

    @pytest.fixture
    def client(self):
        client = SimulatedIBKRClient()
        client.connect()
        yield client
        client.disconnect()

    def test_ib_is_connected(self, client):
        assert client.ib.isConnected() is True

    def test_ib_managed_accounts(self, client):
        accounts = client.ib.managedAccounts()
        assert accounts == ["SIM000001"]

    def test_ib_req_current_time(self, client):
        from datetime import datetime

        time = client.ib.reqCurrentTime()
        assert isinstance(time, datetime)

    def test_ib_disconnect(self, client):
        client.ib.disconnect()
        assert client.ib.isConnected() is False


class TestSimulationCoreAdapterFlows:
    """Tests for the refactored core paths against the simulation broker adapter."""

    @pytest.fixture(autouse=True)
    def clear_contract_cache(self):
        cache = get_contract_cache()
        cache.clear()
        yield
        cache.clear()

    @pytest.fixture
    def client(self):
        client = SimulatedIBKRClient()
        client.connect()
        yield client
        client.disconnect()

    def test_core_quote_and_history_use_simulation_broker(self, client):
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        quote = get_core_quote(spec, client, timeout_s=0.5)
        bars = get_historical_bars(spec, client, bar_size="1d", duration="5d", timeout_s=0.5)

        assert quote.symbol == "AAPL"
        assert quote.bid > 0
        assert quote.ask >= quote.bid
        assert len(bars) >= 5
        assert all(bar.symbol == "AAPL" for bar in bars)

    def test_core_historical_month_bars_use_month_spacing(self, client):
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        bars = get_historical_bars(spec, client, bar_size="1mo", duration="3mo", timeout_s=0.5)

        assert len(bars) >= 5
        assert all(bar.barSize == "1 month" for bar in bars)
        assert (bars[1].time - bars[0].time).days >= 28

    def test_core_historical_second_durations_do_not_expand_to_minutes(self, client):
        spec = SymbolSpec(symbol="AAPL", securityType="STK")

        bars = get_historical_bars(spec, client, bar_size="1m", duration="300s", timeout_s=0.5)

        assert len(bars) == 5
        assert all(bar.barSize == "1 min" for bar in bars)

    def test_core_option_chain_and_snapshot_use_simulation_broker(self, client):
        underlying = SymbolSpec(symbol="AAPL", securityType="STK")

        chain = get_option_chain(
            underlying,
            client,
            strike_count=3,
            max_candidates=4,
            timeout_s=0.5,
        )
        assert chain.candidateCount > 0

        candidate = chain.candidates[0]
        snapshot = get_option_snapshot(
            SymbolSpec(
                symbol=candidate.symbol,
                securityType="OPT",
                exchange=candidate.exchange,
                currency=candidate.currency,
                expiry=candidate.expiry,
                strike=candidate.strike,
                right=candidate.right,
                multiplier=candidate.multiplier,
            ),
            client,
            timeout_s=0.5,
        )

        assert snapshot.contract.securityType == "OPT"
        assert snapshot.greeks.model is not None
        assert snapshot.impliedVolatility is not None

    def test_core_place_retry_and_cancel_use_simulation_broker(self, client):
        order_spec = OrderSpec(
            instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=1.0,
            clientOrderId="sim-core-limit-1",
        )

        with (
            patch.object(
                orders_core,
                "get_config",
                return_value=SimpleNamespace(orders_enabled=True, trading_mode="simulation"),
            ),
            patch.object(orders_core, "save_order"),
            patch.object(orders_core, "record_audit_event"),
            patch.object(orders_core, "update_order_status"),
            patch.object(orders_core, "_order_registry", orders_core.OrderRegistry()),
        ):
            first = orders_core.place_order(client, order_spec, wait_for_status_s=0.0)
            retry = orders_core.place_order(client, order_spec, wait_for_status_s=0.0)
            open_orders = orders_core.get_open_orders(client)
            cancelled = orders_core.cancel_order(client, first.orderId, wait_for_cancel_s=0.0)

        assert first.status == "ACCEPTED"
        assert first.orderStatus is not None
        assert first.orderStatus.status == "SUBMITTED"
        assert retry.orderId == first.orderId
        assert len(open_orders) == 1
        assert open_orders[0]["client_order_id"] == "sim-core-limit-1"
        assert cancelled.status == "CANCELLED"


class TestSimulationMetrics:
    """Tests for metrics in simulation mode."""

    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        from ibkr_core.metrics import get_metrics

        metrics = get_metrics()
        metrics.reset()
        yield
        metrics.reset()

    def test_connect_records_metrics(self):
        from ibkr_core.metrics import get_metrics

        client = SimulatedIBKRClient()
        client.connect()

        metrics = get_metrics()
        all_data = metrics.get_all_metrics()

        # Should have connection metric
        assert "ibkr_operations_total" in all_data["counters"]

        client.disconnect()

    def test_connection_status_gauge_updated(self):
        from ibkr_core.metrics import get_metrics

        client = SimulatedIBKRClient()

        metrics = get_metrics()
        # Initially disconnected
        assert metrics.gauge_get("ibkr_connection_status", labels={"mode": "simulation"}) == 0.0

        client.connect()
        assert metrics.gauge_get("ibkr_connection_status", labels={"mode": "simulation"}) == 1.0

        client.disconnect()
        assert metrics.gauge_get("ibkr_connection_status", labels={"mode": "simulation"}) == 0.0


class TestSimulationThreadSafety:
    """Tests for thread safety in simulation mode."""

    def test_concurrent_order_submission(self):
        from concurrent.futures import ThreadPoolExecutor

        client = SimulatedIBKRClient()
        client.connect()
        client.clear_orders()

        def submit_order(i):
            return client.submit_order(
                symbol="AAPL",
                side="BUY",
                quantity=i + 1,
                order_type="MKT",
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            orders = list(executor.map(submit_order, range(20)))

        assert len(orders) == 20
        assert len(client.get_all_orders()) == 20

        # All should have unique IDs
        order_ids = {o.order_id for o in orders}
        assert len(order_ids) == 20

        client.disconnect()

    def test_concurrent_quote_requests(self):
        from concurrent.futures import ThreadPoolExecutor

        client = SimulatedIBKRClient()
        client.connect()

        def get_quote(symbol):
            return client.get_quote(symbol)

        symbols = ["AAPL", "MSFT", "GOOGL"] * 10

        with ThreadPoolExecutor(max_workers=10) as executor:
            quotes = list(executor.map(get_quote, symbols))

        assert len(quotes) == 30
        assert all(q.bid > 0 for q in quotes)

        client.disconnect()
