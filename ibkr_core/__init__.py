"""IBKR Core Integration Module."""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import TYPE_CHECKING


def _ensure_event_loop() -> None:
    try:
        asyncio.get_running_loop()
        return
    except RuntimeError:
        pass

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


_ensure_event_loop()

if TYPE_CHECKING:
    from ibkr_core.broker import BrokerAdapter, IBInsyncBrokerAdapter, get_broker_adapter
    from ibkr_core.client import ConnectionError, IBKRClient, create_client
    from ibkr_core.config import (
        Config,
        InvalidConfigError,
        TradingDisabledError,
        get_config,
        load_config,
        reset_config,
    )
    from ibkr_core.contracts import (
        AmbiguousContractError,
        ContractCache,
        ContractNotFoundError,
        ContractResolutionError,
        get_contract_cache,
        get_front_month_expiry,
        resolve_contract,
        resolve_contracts,
    )
    from ibkr_core.market_data import (
        MarketDataError,
        MarketDataPermissionError,
        MarketDataTimeoutError,
        NoMarketDataError,
        PacingViolationError,
        QuoteMode,
        StreamingQuote,
        get_historical_bars,
        get_quote,
        get_quote_with_mode,
        get_quotes,
        get_streaming_quote,
        normalize_bar_size,
        normalize_duration,
        normalize_what_to_show,
    )
    from ibkr_core.models import (
        AccountPnl,
        AccountSummary,
        Bar,
        CancelResult,
        OrderPreview,
        OrderResult,
        OrderSpec,
        OrderStatus,
        PnlDetail,
        Position,
        Quote,
        SymbolSpec,
    )

__all__ = [
    # Config
    "Config",
    "InvalidConfigError",
    "TradingDisabledError",
    "get_config",
    "load_config",
    "reset_config",
    # Broker
    "BrokerAdapter",
    "IBInsyncBrokerAdapter",
    "get_broker_adapter",
    # Client
    "IBKRClient",
    "ConnectionError",
    "create_client",
    # Contracts
    "ContractCache",
    "ContractResolutionError",
    "ContractNotFoundError",
    "AmbiguousContractError",
    "get_contract_cache",
    "resolve_contract",
    "resolve_contracts",
    "get_front_month_expiry",
    # Market Data
    "MarketDataError",
    "MarketDataPermissionError",
    "MarketDataTimeoutError",
    "NoMarketDataError",
    "PacingViolationError",
    "QuoteMode",
    "StreamingQuote",
    "get_quote",
    "get_quote_with_mode",
    "get_quotes",
    "get_streaming_quote",
    "get_historical_bars",
    "normalize_bar_size",
    "normalize_duration",
    "normalize_what_to_show",
    # Models
    "SymbolSpec",
    "Quote",
    "Bar",
    "AccountSummary",
    "Position",
    "PnlDetail",
    "AccountPnl",
    "OrderSpec",
    "OrderPreview",
    "OrderStatus",
    "OrderResult",
    "CancelResult",
]

_LAZY_ATTRS = {
    # Config
    "Config": "ibkr_core.config",
    "InvalidConfigError": "ibkr_core.config",
    "TradingDisabledError": "ibkr_core.config",
    "get_config": "ibkr_core.config",
    "load_config": "ibkr_core.config",
    "reset_config": "ibkr_core.config",
    # Broker
    "BrokerAdapter": "ibkr_core.broker",
    "IBInsyncBrokerAdapter": "ibkr_core.broker",
    "get_broker_adapter": "ibkr_core.broker",
    # Client
    "IBKRClient": "ibkr_core.client",
    "ConnectionError": "ibkr_core.client",
    "create_client": "ibkr_core.client",
    # Contracts
    "ContractCache": "ibkr_core.contracts",
    "ContractResolutionError": "ibkr_core.contracts",
    "ContractNotFoundError": "ibkr_core.contracts",
    "AmbiguousContractError": "ibkr_core.contracts",
    "get_contract_cache": "ibkr_core.contracts",
    "resolve_contract": "ibkr_core.contracts",
    "resolve_contracts": "ibkr_core.contracts",
    "get_front_month_expiry": "ibkr_core.contracts",
    # Market Data
    "MarketDataError": "ibkr_core.market_data",
    "MarketDataPermissionError": "ibkr_core.market_data",
    "MarketDataTimeoutError": "ibkr_core.market_data",
    "NoMarketDataError": "ibkr_core.market_data",
    "PacingViolationError": "ibkr_core.market_data",
    "QuoteMode": "ibkr_core.market_data",
    "StreamingQuote": "ibkr_core.market_data",
    "get_quote": "ibkr_core.market_data",
    "get_quote_with_mode": "ibkr_core.market_data",
    "get_quotes": "ibkr_core.market_data",
    "get_streaming_quote": "ibkr_core.market_data",
    "get_historical_bars": "ibkr_core.market_data",
    "normalize_bar_size": "ibkr_core.market_data",
    "normalize_duration": "ibkr_core.market_data",
    "normalize_what_to_show": "ibkr_core.market_data",
    # Models
    "SymbolSpec": "ibkr_core.models",
    "Quote": "ibkr_core.models",
    "Bar": "ibkr_core.models",
    "AccountSummary": "ibkr_core.models",
    "Position": "ibkr_core.models",
    "PnlDetail": "ibkr_core.models",
    "AccountPnl": "ibkr_core.models",
    "OrderSpec": "ibkr_core.models",
    "OrderPreview": "ibkr_core.models",
    "OrderStatus": "ibkr_core.models",
    "OrderResult": "ibkr_core.models",
    "CancelResult": "ibkr_core.models",
}


def __getattr__(name: str):
    module_path = _LAZY_ATTRS.get(name)
    if not module_path:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(_LAZY_ATTRS.keys()))
