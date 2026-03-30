"""Broker adapter seam for IBKR backends."""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class BrokerAdapter(Protocol):
    """Minimal broker operations used by the core layer."""

    def is_connected(self) -> bool:
        ...

    def managed_accounts(self) -> list[str]:
        ...

    def request_current_time(self) -> Any:
        ...

    async def request_current_time_async(self) -> Any:
        ...

    def request_contract_details(self, contract: Any) -> list[Any]:
        ...

    def qualify_contracts(self, *contracts: Any) -> list[Any]:
        ...

    def get_request_timeout(self) -> Optional[float]:
        ...

    def set_request_timeout(self, timeout: float) -> None:
        ...

    def account_summary(self, account_id: Optional[str] = None) -> list[Any]:
        ...

    def request_positions(self) -> None:
        ...

    def positions(self) -> list[Any]:
        ...

    def portfolio(self, account_id: str) -> list[Any]:
        ...

    def cancel_positions(self) -> None:
        ...

    def request_pnl(self, account_id: str) -> None:
        ...

    def pnl(self, account_id: str) -> Any:
        ...

    def cancel_pnl(self, account_id: str) -> None:
        ...

    def sleep(self, seconds: float) -> None:
        ...


class IBInsyncBrokerAdapter:
    """Current broker adapter backed by an `ib_insync.IB`-style object."""

    def __init__(self, ib: Any):
        self._ib = ib

    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    def managed_accounts(self) -> list[str]:
        return self._ib.managedAccounts() or []

    def request_current_time(self) -> Any:
        return self._ib.reqCurrentTime()

    async def request_current_time_async(self) -> Any:
        return await self._ib.reqCurrentTimeAsync()

    def request_contract_details(self, contract: Any) -> list[Any]:
        return self._ib.reqContractDetails(contract)

    def qualify_contracts(self, *contracts: Any) -> list[Any]:
        return self._ib.qualifyContracts(*contracts)

    def get_request_timeout(self) -> Optional[float]:
        value = getattr(self._ib, "RequestTimeout", None)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def set_request_timeout(self, timeout: float) -> None:
        self._ib.RequestTimeout = timeout

    def account_summary(self, account_id: Optional[str] = None) -> list[Any]:
        return self._ib.accountSummary(account_id)

    def request_positions(self) -> None:
        self._ib.reqPositions()

    def positions(self) -> list[Any]:
        return self._ib.positions()

    def portfolio(self, account_id: str) -> list[Any]:
        return self._ib.portfolio(account_id)

    def cancel_positions(self) -> None:
        self._ib.cancelPositions()

    def request_pnl(self, account_id: str) -> None:
        self._ib.reqPnL(account_id)

    def pnl(self, account_id: str) -> Any:
        return self._ib.pnl(account_id)

    def cancel_pnl(self, account_id: str) -> None:
        self._ib.cancelPnL(account_id)

    def sleep(self, seconds: float) -> None:
        self._ib.sleep(seconds)


def _has_explicit_attr(obj: Any, name: str) -> bool:
    return hasattr(type(obj), name) or name in getattr(obj, "__dict__", {})


def get_broker_adapter(client: Any) -> BrokerAdapter:
    """Return a broker adapter from a client, keeping `.ib` compatibility during refactor."""

    if _has_explicit_attr(client, "broker"):
        broker = getattr(client, "broker")
        if broker is not None:
            return broker

    ib = getattr(client, "ib", None)
    if ib is not None:
        return IBInsyncBrokerAdapter(ib)

    raise TypeError("Client does not expose a broker adapter or ib connection")
