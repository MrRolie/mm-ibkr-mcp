"""Tests for the broker adapter seam."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ibkr_core.broker import IBInsyncBrokerAdapter, get_broker_adapter


def test_get_broker_adapter_prefers_explicit_broker() -> None:
    broker = MagicMock()
    client = SimpleNamespace(broker=broker)

    assert get_broker_adapter(client) is broker


def test_get_broker_adapter_wraps_ib_connection() -> None:
    ib = MagicMock()
    client = SimpleNamespace(ib=ib)

    adapter = get_broker_adapter(client)

    assert isinstance(adapter, IBInsyncBrokerAdapter)
    adapter.request_positions()
    ib.reqPositions.assert_called_once_with()


def test_ib_insync_broker_adapter_request_timeout_round_trip() -> None:
    ib = MagicMock()
    ib.RequestTimeout = 7.5
    adapter = IBInsyncBrokerAdapter(ib)

    assert adapter.get_request_timeout() == 7.5

    adapter.set_request_timeout(3.0)

    assert ib.RequestTimeout == 3.0


def test_ib_insync_broker_adapter_market_data_and_order_methods_proxy() -> None:
    ib = MagicMock()
    adapter = IBInsyncBrokerAdapter(ib)
    handler = MagicMock()

    adapter.add_error_handler(handler)
    adapter.remove_error_handler(handler)
    adapter.request_market_data("contract", "100", snapshot=True)
    adapter.cancel_market_data("contract")
    adapter.request_historical_data(
        "contract",
        end_date_time="",
        duration_str="5 D",
        bar_size_setting="1 day",
        what_to_show="TRADES",
        use_rth=True,
        format_date=1,
        timeout=5.0,
    )
    adapter.request_option_chain_params("AAPL", "", "STK", 123)
    adapter.place_order("contract", "order")
    adapter.cancel_order("order")
    adapter.open_trades()
    adapter.trades()
    adapter.sleep(0.25)

    ib.reqMktData.assert_called_once_with("contract", "100", snapshot=True)
    ib.cancelMktData.assert_called_once_with("contract")
    ib.reqHistoricalData.assert_called_once()
    ib.reqSecDefOptParams.assert_called_once_with("AAPL", "", "STK", 123)
    ib.placeOrder.assert_called_once_with("contract", "order")
    ib.cancelOrder.assert_called_once_with("order")
    ib.openTrades.assert_called_once_with()
    ib.trades.assert_called_once_with()
    ib.sleep.assert_called_once_with(0.25)
