"""Unit tests for option discovery and snapshot helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ib_insync import Contract, Option

from ibkr_core.market_data import get_option_chain, get_option_snapshot
from ibkr_core.models import Quote, SymbolSpec


class DummyEvent:
    """Minimal event helper for ib_insync-style += / -= handlers."""

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self


def make_mock_client() -> MagicMock:
    """Create a mock IBKR client with the subset of behavior we need."""
    client = MagicMock()
    client.is_connected = True
    client.ensure_connected = MagicMock()
    client.ib = MagicMock()
    client.ib.errorEvent = DummyEvent()
    client.ib.sleep = MagicMock(side_effect=lambda _: None)
    client.ib.cancelMktData = MagicMock()
    return client


def make_quote(symbol: str, con_id: int, last: float) -> Quote:
    """Create a quote fixture with sensible defaults."""
    return Quote(
        symbol=symbol,
        conId=con_id,
        bid=last - 0.1,
        ask=last + 0.1,
        last=last,
        timestamp=datetime.now(timezone.utc),
        source="IBKR_SNAPSHOT",
    )


def test_get_option_chain_filters_and_qualifies_candidates():
    """Option-chain discovery should filter expiries/strikes and return qualified candidates."""
    client = make_mock_client()
    underlying_spec = SymbolSpec(
        symbol="AAPL",
        securityType="STK",
        exchange="SMART",
        currency="USD",
    )
    underlying_contract = Contract(symbol="AAPL", secType="STK", exchange="SMART", currency="USD")
    underlying_contract.conId = 265598

    client.ib.reqSecDefOptParams.return_value = [
        SimpleNamespace(
            exchange="SMART",
            multiplier="100",
            tradingClass="AAPL",
            expirations={"20260417", "20260515"},
            strikes=[90, 95, 100, 105, 110],
        )
    ]

    def qualify_contracts(*contracts):
        qualified = []
        for index, contract in enumerate(contracts, start=1):
            contract.conId = 7000 + index
            contract.localSymbol = f"AAPL_OPT_{index}"
            contract.tradingClass = "AAPL"
            qualified.append(contract)
        return qualified

    client.ib.qualifyContracts.side_effect = qualify_contracts

    with patch("ibkr_core.market_data.resolve_contract", return_value=underlying_contract):
        with patch(
            "ibkr_core.market_data.get_quote",
            return_value=make_quote("AAPL", underlying_contract.conId, 101.0),
        ):
            chain = get_option_chain(
                underlying_spec,
                client,
                expiry_start="2026-04-01",
                expiry_end="2026-04-30",
                strike_count=3,
                rights=["CALL"],
                max_candidates=6,
            )

    assert chain.expirations == ["2026-04-17"]
    assert chain.strikes == [95.0, 100.0, 105.0]
    assert chain.candidateCount == 3
    assert all(candidate.right == "C" for candidate in chain.candidates)
    assert all(candidate.exchange == "SMART" for candidate in chain.candidates)


def test_get_option_snapshot_returns_partial_data_when_greeks_unavailable():
    """Option snapshots should still succeed when IV/greeks are unavailable."""
    client = make_mock_client()
    option_spec = SymbolSpec(
        symbol="AAPL",
        securityType="OPT",
        exchange="SMART",
        currency="USD",
        expiry="2026-04-17",
        strike=100.0,
        right="C",
    )
    option_contract = Option(
        symbol="AAPL",
        lastTradeDateOrContractMonth="20260417",
        strike=100.0,
        right="C",
        exchange="SMART",
        currency="USD",
    )
    option_contract.conId = 8123
    option_contract.localSymbol = "AAPL  260417C00100000"
    client.ib.reqMktData.return_value = SimpleNamespace(
        impliedVolatility=float("nan"),
        histVolatility=float("nan"),
        rtHistVolatility=None,
        modelGreeks=None,
        bidGreeks=None,
        askGreeks=None,
        lastGreeks=None,
    )

    with patch("ibkr_core.market_data.resolve_contract", return_value=option_contract):
        with patch(
            "ibkr_core.market_data.get_quote",
            return_value=make_quote("AAPL", option_contract.conId, 5.10),
        ):
            snapshot = get_option_snapshot(option_spec, client)

    assert snapshot.quote.last == 5.10
    assert snapshot.impliedVolatility is None
    assert snapshot.greeks.model is None
    assert snapshot.greeks.bid is None
    client.ib.cancelMktData.assert_called_once_with(option_contract)


def test_get_option_snapshot_returns_available_greeks():
    """Option snapshots should surface IV and greeks when IBKR provides them."""
    client = make_mock_client()
    option_spec = SymbolSpec(
        symbol="AAPL",
        securityType="OPT",
        exchange="SMART",
        currency="USD",
        expiry="2026-04-17",
        strike=100.0,
        right="C",
    )
    option_contract = Option(
        symbol="AAPL",
        lastTradeDateOrContractMonth="20260417",
        strike=100.0,
        right="C",
        exchange="SMART",
        currency="USD",
    )
    option_contract.conId = 8123
    option_contract.localSymbol = "AAPL  260417C00100000"
    client.ib.reqMktData.return_value = SimpleNamespace(
        impliedVolatility=0.26,
        histVolatility=0.21,
        rtHistVolatility=0.22,
        modelGreeks=SimpleNamespace(
            impliedVol=0.25,
            delta=0.55,
            optPrice=5.12,
            pvDividend=0.0,
            gamma=0.02,
            vega=0.11,
            theta=-0.04,
            undPrice=101.5,
        ),
        bidGreeks=None,
        askGreeks=None,
        lastGreeks=None,
    )

    with patch("ibkr_core.market_data.resolve_contract", return_value=option_contract):
        with patch(
            "ibkr_core.market_data.get_quote",
            return_value=make_quote("AAPL", option_contract.conId, 5.10),
        ):
            snapshot = get_option_snapshot(option_spec, client)

    assert snapshot.impliedVolatility == 0.26
    assert snapshot.histVolatility == 0.21
    assert snapshot.rtHistVolatility == 0.22
    assert snapshot.greeks.model is not None
    assert snapshot.greeks.model.delta == 0.55
    assert snapshot.underlyingLastPrice == 101.5
