"""
Contract resolution for IBKR instruments.

Provides mapping from logical SymbolSpec to IBKR Contract objects with:
- Support for STK, ETF, FUT, OPT, IND security types
- In-memory caching to reduce API calls
- Smart defaults for common instruments
"""

import logging
from typing import Dict, Optional, Tuple

from ib_insync import Contract, Future, Index, Option, Stock

from ibkr_core.broker import get_broker_adapter
from ibkr_core.client import IBKRClient
from ibkr_core.models import ResolvedContract, SymbolSpec

logger = logging.getLogger(__name__)


class ContractResolutionError(Exception):
    """Raised when contract resolution fails."""

    pass


class ContractNotFoundError(ContractResolutionError):
    """Raised when contract cannot be found at IBKR."""

    pass


class AmbiguousContractError(ContractResolutionError):
    """Raised when contract specification matches multiple contracts."""

    pass


# Known defaults for common instruments
SYMBOL_DEFAULTS: Dict[str, Dict[str, str]] = {
    # US Equities - default to SMART routing, USD
    "AAPL": {"exchange": "SMART", "currency": "USD"},
    "MSFT": {"exchange": "SMART", "currency": "USD"},
    "GOOGL": {"exchange": "SMART", "currency": "USD"},
    "AMZN": {"exchange": "SMART", "currency": "USD"},
    "SPY": {"exchange": "SMART", "currency": "USD"},
    "QQQ": {"exchange": "SMART", "currency": "USD"},
    "IWM": {"exchange": "SMART", "currency": "USD"},
    # Micro E-mini Futures
    "MES": {"exchange": "CME", "currency": "USD"},
    "MNQ": {"exchange": "CME", "currency": "USD"},
    "M2K": {"exchange": "CME", "currency": "USD"},
    "MYM": {"exchange": "CME", "currency": "USD"},
    # E-mini Futures
    "ES": {"exchange": "CME", "currency": "USD"},
    "NQ": {"exchange": "CME", "currency": "USD"},
    "RTY": {"exchange": "CME", "currency": "USD"},
    "YM": {"exchange": "CME", "currency": "USD"},
    # Indices
    "SPX": {"exchange": "CBOE", "currency": "USD"},
    "NDX": {"exchange": "NASDAQ", "currency": "USD"},
    "VIX": {"exchange": "CBOE", "currency": "USD"},
}


class ContractCache:
    """
    In-memory cache for resolved contracts.

    Keyed by the full SymbolSpec shape.
    No TTL - cache persists for lifetime of the cache instance.
    """

    def __init__(self):
        self._cache: Dict[Tuple[str, str, str, str, str, str, str, str], Contract] = {}
        self._hits = 0
        self._misses = 0

    def _make_key(self, spec: SymbolSpec) -> Tuple[str, str, str, str, str, str, str, str]:
        """Create cache key from SymbolSpec."""
        return (
            spec.symbol,
            spec.securityType,
            spec.exchange or "",
            spec.currency or "",
            spec.expiry or "",
            str(spec.strike) if spec.strike is not None else "",
            spec.right or "",
            spec.multiplier or "",
        )

    def get(self, spec: SymbolSpec) -> Optional[Contract]:
        """Get contract from cache."""
        key = self._make_key(spec)
        contract = self._cache.get(key)
        if contract:
            self._hits += 1
            logger.debug(f"Cache hit for {spec.symbol}")
        else:
            self._misses += 1
            logger.debug(f"Cache miss for {spec.symbol}")
        return contract

    def put(self, spec: SymbolSpec, contract: Contract) -> None:
        """Store contract in cache."""
        key = self._make_key(spec)
        self._cache[key] = contract
        logger.debug(f"Cached contract for {spec.symbol}: conId={contract.conId}")

    def clear(self) -> None:
        """Clear all cached contracts."""
        self._cache.clear()
        logger.info("Contract cache cleared")

    @property
    def size(self) -> int:
        """Number of cached contracts."""
        return len(self._cache)

    @property
    def stats(self) -> Dict[str, int]:
        """Cache statistics."""
        return {
            "size": self._cache.__len__(),
            "hits": self._hits,
            "misses": self._misses,
        }


# Global contract cache
_contract_cache = ContractCache()


def get_contract_cache() -> ContractCache:
    """Get the global contract cache."""
    return _contract_cache


def _apply_defaults(spec: SymbolSpec) -> SymbolSpec:
    """
    Apply known defaults to a SymbolSpec if exchange/currency not specified.

    Returns a new SymbolSpec with defaults applied.
    """
    defaults = SYMBOL_DEFAULTS.get(spec.symbol, {})

    exchange = spec.exchange or defaults.get("exchange")
    currency = spec.currency or defaults.get("currency", "USD")

    return SymbolSpec(
        symbol=spec.symbol,
        securityType=spec.securityType,
        exchange=exchange,
        currency=currency,
        expiry=spec.expiry,
        strike=spec.strike,
        right=spec.right,
        multiplier=spec.multiplier,
    )


def _build_contract(spec: SymbolSpec) -> Contract:
    """
    Build an ib_insync Contract object from a SymbolSpec.

    Does NOT qualify the contract with IBKR - just builds the local object.
    """
    sec_type = spec.securityType

    if sec_type in ("STK", "ETF"):
        contract = Stock(
            symbol=spec.symbol,
            exchange=spec.exchange or "SMART",
            currency=spec.currency or "USD",
        )

    elif sec_type == "FUT":
        # For futures, we need either an expiry or we fetch the front month
        contract = Future(
            symbol=spec.symbol,
            exchange=spec.exchange or "",
            currency=spec.currency or "USD",
        )
        if spec.expiry:
            # Convert YYYY-MM-DD to YYYYMMDD
            contract.lastTradeDateOrContractMonth = spec.expiry.replace("-", "")
        if spec.multiplier:
            contract.multiplier = spec.multiplier

    elif sec_type == "OPT":
        if not spec.expiry:
            raise ContractResolutionError("Options require expiry date")
        if spec.strike is None:
            raise ContractResolutionError("Options require strike price")
        if not spec.right:
            raise ContractResolutionError("Options require right (C or P)")

        contract = Option(
            symbol=spec.symbol,
            lastTradeDateOrContractMonth=spec.expiry.replace("-", ""),
            strike=spec.strike,
            right=spec.right,
            exchange=spec.exchange or "SMART",
            currency=spec.currency or "USD",
        )
        if spec.multiplier:
            contract.multiplier = spec.multiplier

    elif sec_type == "IND":
        contract = Index(
            symbol=spec.symbol,
            exchange=spec.exchange or "",
            currency=spec.currency or "USD",
        )

    else:
        # Generic contract for other types
        contract = Contract(
            symbol=spec.symbol,
            secType=sec_type,
            exchange=spec.exchange or "",
            currency=spec.currency or "USD",
        )

    return contract


def resolve_contract(
    spec: SymbolSpec,
    client: IBKRClient,
    use_cache: bool = True,
) -> Contract:
    """
    Resolve a SymbolSpec to a fully qualified IBKR Contract.

    Args:
        spec: The symbol specification to resolve.
        client: Connected IBKRClient instance.
        use_cache: Whether to use the contract cache.

    Returns:
        Fully qualified Contract with conId populated.

    Raises:
        ContractResolutionError: If resolution fails.
        ContractNotFoundError: If no matching contract found.
        AmbiguousContractError: If multiple contracts match.
    """
    # Apply defaults
    spec = _apply_defaults(spec)

    # Check cache first
    if use_cache:
        cached = _contract_cache.get(spec)
        if cached:
            return cached

    # Ensure connected
    if not client.is_connected:
        raise ContractResolutionError("Client is not connected to IBKR")

    broker = get_broker_adapter(client)

    # Build the contract
    contract = _build_contract(spec)

    logger.debug(f"Resolving contract: {spec.symbol} ({spec.securityType})")

    try:
        # For futures without expiry, we need to get contract details first
        # to find the front month
        if spec.securityType == "FUT" and not spec.expiry:
            # Get all available contracts for this future
            details = broker.request_contract_details(contract)
            if not details:
                raise ContractNotFoundError(
                    f"No contract found for {spec.symbol} ({spec.securityType}) "
                    f"on {spec.exchange or 'any exchange'}"
                )

            # Sort by expiry and pick the front month (earliest expiry)
            sorted_details = sorted(details, key=lambda d: d.contract.lastTradeDateOrContractMonth)
            qualified = sorted_details[0].contract

            logger.info(
                f"Resolved {spec.symbol} to front month: "
                f"{qualified.lastTradeDateOrContractMonth}"
            )
        else:
            # Qualify with IBKR - this fills in conId and other fields
            qualified_contracts = broker.qualify_contracts(contract)

            if not qualified_contracts:
                raise ContractNotFoundError(
                    f"No contract found for {spec.symbol} ({spec.securityType}) "
                    f"on {spec.exchange or 'any exchange'}"
                )

            qualified = qualified_contracts[0]

        logger.debug(
            f"Resolved {spec.symbol}: conId={qualified.conId}, "
            f"exchange={qualified.exchange}, currency={qualified.currency}"
        )

        # Cache the result
        if use_cache:
            _contract_cache.put(spec, qualified)

        return qualified

    except Exception as e:
        if isinstance(e, ContractResolutionError):
            raise
        raise ContractResolutionError(f"Failed to resolve contract for {spec.symbol}: {e}") from e


def contract_to_resolved_contract(contract: Contract) -> ResolvedContract:
    """Convert an IBKR contract object to the typed ResolvedContract model."""
    expiry = getattr(contract, "lastTradeDateOrContractMonth", None)
    if expiry:
        expiry = str(expiry)
        if len(expiry) == 8 and expiry.isdigit():
            expiry = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"

    right = getattr(contract, "right", None)
    if right:
        right = str(right).upper()

    strike = getattr(contract, "strike", None)
    strike_value = float(strike) if strike not in (None, "") else None

    con_id = getattr(contract, "conId", None) or 0

    return ResolvedContract(
        symbol=contract.symbol,
        securityType=contract.secType,
        conId=int(con_id),
        exchange=getattr(contract, "exchange", None) or None,
        primaryExchange=getattr(contract, "primaryExchange", None) or None,
        currency=getattr(contract, "currency", None) or None,
        localSymbol=getattr(contract, "localSymbol", None) or None,
        tradingClass=getattr(contract, "tradingClass", None) or None,
        expiry=expiry,
        strike=strike_value,
        right=right,
        multiplier=getattr(contract, "multiplier", None) or None,
    )


def resolve_contracts(
    specs: list[SymbolSpec],
    client: IBKRClient,
    use_cache: bool = True,
) -> Dict[str, Contract]:
    """
    Resolve multiple SymbolSpecs to Contracts.

    Args:
        specs: List of symbol specifications to resolve.
        client: Connected IBKRClient instance.
        use_cache: Whether to use the contract cache.

    Returns:
        Dict mapping symbol to Contract.

    Raises:
        ContractResolutionError: If any resolution fails.
    """
    results: Dict[str, Contract] = {}

    for spec in specs:
        contract = resolve_contract(spec, client, use_cache)
        results[spec.symbol] = contract

    return results


def get_front_month_expiry(
    symbol: str,
    client: IBKRClient,
) -> Optional[str]:
    """
    Get the front month expiry for a futures symbol.

    Args:
        symbol: Futures symbol (e.g., 'MES', 'ES').
        client: Connected IBKRClient instance.

    Returns:
        Expiry date in YYYY-MM-DD format, or None if not found.
    """
    spec = SymbolSpec(symbol=symbol, securityType="FUT")

    try:
        contract = resolve_contract(spec, client, use_cache=False)
        expiry = contract.lastTradeDateOrContractMonth

        # Convert YYYYMMDD to YYYY-MM-DD
        if expiry and len(expiry) == 8:
            return f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
        return expiry

    except ContractResolutionError:
        return None
