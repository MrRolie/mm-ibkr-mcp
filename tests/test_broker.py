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
