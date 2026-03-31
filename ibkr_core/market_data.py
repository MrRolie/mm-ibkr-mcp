"""
Market data retrieval for IBKR instruments.

Provides snapshot and streaming quotes and historical bars with:
- Snapshot quotes: One-time market data request (requires US Securities Snapshot subscription)
- Streaming quotes: Continuous real-time updates (requires US Equity & Options Add-On Streaming)
- Normalized input handling (bar sizes, durations, data types)
- Timeout-based polling with deadline enforcement
- Structured error handling for permissions, pacing, timeouts
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, Generator, List, Optional

from ib_insync import Contract, Option, Ticker

from ibkr_core.broker import get_broker_adapter
from ibkr_core.client import IBKRClient
from ibkr_core.contracts import (
    ContractResolutionError,
    contract_to_resolved_contract,
    resolve_contract,
)
from ibkr_core.models import (
    Bar,
    OptionChainResponse,
    OptionContractCandidate,
    OptionGreeks,
    OptionGreeksSet,
    OptionSnapshotResponse,
    Quote,
    SymbolSpec,
)


class QuoteMode(Enum):
    """Quote retrieval mode."""

    SNAPSHOT = "snapshot"  # One-time request (US Securities Snapshot)
    STREAMING = "streaming"  # Continuous updates (US Equity & Options Add-On Streaming)


logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class MarketDataError(Exception):
    """Base exception for market data errors."""

    pass


class MarketDataPermissionError(MarketDataError):
    """Raised when market data permissions are insufficient."""

    pass


class NoMarketDataError(MarketDataError):
    """Raised when no market data is available for the instrument."""

    pass


class PacingViolationError(MarketDataError):
    """Raised when IBKR pacing limits are exceeded."""

    pass


class MarketDataTimeoutError(MarketDataError):
    """Raised when market data request times out."""

    pass


# =============================================================================
# Normalization Helpers
# =============================================================================

# IBKR valid bar sizes - mapping from friendly aliases to IBKR format
BAR_SIZE_MAP: Dict[str, str] = {
    # Seconds
    "1s": "1 secs",
    "1 sec": "1 secs",
    "1 secs": "1 secs",
    "5s": "5 secs",
    "5 sec": "5 secs",
    "5 secs": "5 secs",
    "10s": "10 secs",
    "10 sec": "10 secs",
    "10 secs": "10 secs",
    "15s": "15 secs",
    "15 sec": "15 secs",
    "15 secs": "15 secs",
    "30s": "30 secs",
    "30 sec": "30 secs",
    "30 secs": "30 secs",
    # Minutes
    "1m": "1 min",
    "1min": "1 min",
    "1 min": "1 min",
    "2m": "2 mins",
    "2min": "2 mins",
    "2 min": "2 mins",
    "2 mins": "2 mins",
    "3m": "3 mins",
    "3min": "3 mins",
    "3 min": "3 mins",
    "3 mins": "3 mins",
    "5m": "5 mins",
    "5min": "5 mins",
    "5 min": "5 mins",
    "5 mins": "5 mins",
    "10m": "10 mins",
    "10min": "10 mins",
    "10 mins": "10 mins",
    "15m": "15 mins",
    "15min": "15 mins",
    "15 min": "15 mins",
    "15 mins": "15 mins",
    "20m": "20 mins",
    "20min": "20 mins",
    "20 mins": "20 mins",
    "30m": "30 mins",
    "30min": "30 mins",
    "30 min": "30 mins",
    "30 mins": "30 mins",
    # Hours
    "1h": "1 hour",
    "1hr": "1 hour",
    "1 hour": "1 hour",
    "2h": "2 hours",
    "2hr": "2 hours",
    "2 hours": "2 hours",
    "3h": "3 hours",
    "3hr": "3 hours",
    "3 hours": "3 hours",
    "4h": "4 hours",
    "4hr": "4 hours",
    "4 hours": "4 hours",
    "8h": "8 hours",
    "8hr": "8 hours",
    "8 hours": "8 hours",
    # Days/Weeks/Months
    "1d": "1 day",
    "1 day": "1 day",
    "1w": "1 week",
    "1 week": "1 week",
    "1mo": "1 month",
    "1 month": "1 month",
}

# IBKR duration format mapping
DURATION_MAP: Dict[str, str] = {
    # Seconds
    "60s": "60 S",
    "120s": "120 S",
    "300s": "300 S",
    "600s": "600 S",
    "1800s": "1800 S",
    # Days
    "1d": "1 D",
    "2d": "2 D",
    "3d": "3 D",
    "5d": "5 D",
    "7d": "7 D",
    "10d": "10 D",
    "14d": "14 D",
    "30d": "30 D",
    # Weeks
    "1w": "1 W",
    "2w": "2 W",
    "3w": "3 W",
    "4w": "4 W",
    # Months
    "1mo": "1 M",
    "2mo": "2 M",
    "3mo": "3 M",
    "6mo": "6 M",
    "12mo": "12 M",
    # Years
    "1y": "1 Y",
    "2y": "2 Y",
    "3y": "3 Y",
    "5y": "5 Y",
}

# Valid what_to_show values
WHAT_TO_SHOW_VALUES = {
    "TRADES",
    "MIDPOINT",
    "BID",
    "ASK",
    "BID_ASK",
    "HISTORICAL_VOLATILITY",
    "OPTION_IMPLIED_VOLATILITY",
    "ADJUSTED_LAST",  # For stock historical data
    "SCHEDULE",  # Trading schedule
}


def normalize_bar_size(bar_size: str) -> str:
    """
    Normalize bar size to IBKR-compatible format.

    Args:
        bar_size: Friendly bar size string (e.g., "1m", "5 min", "1d")

    Returns:
        IBKR-compatible bar size string (e.g., "1 min", "5 mins", "1 day")

    Raises:
        ValueError: If bar size is not recognized.
    """
    normalized = bar_size.lower().strip()

    if normalized in BAR_SIZE_MAP:
        return BAR_SIZE_MAP[normalized]

    # Try matching case-insensitively against values
    for key, value in BAR_SIZE_MAP.items():
        if key.lower() == normalized or value.lower() == normalized:
            return value

    raise ValueError(
        f"Invalid bar_size: '{bar_size}'. "
        f"Valid sizes include: 1s, 5s, 1m, 5m, 15m, 1h, 1d, 1w, 1mo"
    )


def normalize_duration(duration: str) -> str:
    """
    Normalize duration to IBKR-compatible format.

    Args:
        duration: Friendly duration string (e.g., "5d", "1w", "1mo", "1y")

    Returns:
        IBKR-compatible duration string (e.g., "5 D", "1 W", "1 M", "1 Y")

    Raises:
        ValueError: If duration is not recognized.
    """
    normalized = duration.lower().strip()

    if normalized in DURATION_MAP:
        return DURATION_MAP[normalized]

    # Check if already in IBKR format (e.g., "5 D", "1 W")
    parts = normalized.upper().split()
    if len(parts) == 2:
        try:
            num = int(parts[0])
            unit = parts[1]
            if unit in ("S", "D", "W", "M", "Y"):
                return f"{num} {unit}"
        except ValueError:
            pass

    raise ValueError(
        f"Invalid duration: '{duration}'. "
        f"Valid durations include: 60s, 1d, 5d, 1w, 2w, 1mo, 3mo, 1y"
    )


def normalize_what_to_show(what_to_show: str) -> str:
    """
    Normalize what_to_show to IBKR-compatible format.

    Args:
        what_to_show: Data type string (e.g., "TRADES", "trades", "midpoint")

    Returns:
        IBKR-compatible what_to_show string (uppercase)

    Raises:
        ValueError: If what_to_show is not valid.
    """
    normalized = what_to_show.upper().strip()

    if normalized in WHAT_TO_SHOW_VALUES:
        return normalized

    raise ValueError(
        f"Invalid what_to_show: '{what_to_show}'. "
        f"Valid values: {', '.join(sorted(WHAT_TO_SHOW_VALUES))}"
    )


# =============================================================================
# Market Data Functions
# =============================================================================

# Polling interval for market data (seconds)
POLL_INTERVAL_S = 0.1


def _ticker_has_data(ticker: Ticker) -> bool:
    """Check if ticker has meaningful market data."""
    # Check for any of bid, ask, last being valid
    # ib_insync uses nan for no data, not None
    import math

    if ticker.bid is not None and not math.isnan(ticker.bid) and ticker.bid > 0:
        return True
    if ticker.ask is not None and not math.isnan(ticker.ask) and ticker.ask > 0:
        return True
    if ticker.last is not None and not math.isnan(ticker.last) and ticker.last > 0:
        return True
    return False


def _check_ibkr_error_code(error_code: int, error_msg: str) -> Optional[Exception]:
    """
    Check IBKR error code and return appropriate exception if needed.

    Args:
        error_code: IBKR error code
        error_msg: Error message

    Returns:
        Exception to raise, or None if the error is ignorable.
    """
    # Permission/subscription errors
    if error_code in (10089, 10090, 354, 10167):
        return MarketDataPermissionError(f"[{error_code}] {error_msg}")

    # No data/no security definition
    if error_code in (200, 162):
        if "no data" in error_msg.lower():
            return NoMarketDataError(f"[{error_code}] {error_msg}")
        elif "no security" in error_msg.lower() or "ambiguous" in error_msg.lower():
            return ContractResolutionError(f"[{error_code}] {error_msg}")

    # Pacing violations
    if error_code in (162,) and "pacing" in error_msg.lower():
        return PacingViolationError(f"[{error_code}] {error_msg}")

    # Historical data errors with "pacing" in message
    if "pacing violation" in error_msg.lower() or "making identical request" in error_msg.lower():
        return PacingViolationError(f"[{error_code}] {error_msg}")

    return None


def _check_ibkr_error_message(msg: str) -> None:
    """
    Check IBKR error message and raise appropriate exception.

    Common error codes/messages:
    - 354: Requested market data is not subscribed
    - 162: Historical Market Data Service error
    - 200: No security definition has been found
    - 10167: Requested market data is not subscribed (paper trading)
    """
    msg_lower = msg.lower()

    if "not subscribed" in msg_lower or "no permission" in msg_lower:
        raise MarketDataPermissionError(msg)

    if "pacing violation" in msg_lower or "making identical request" in msg_lower:
        raise PacingViolationError(msg)

    if "no security definition" in msg_lower or "ambiguous" in msg_lower:
        raise ContractResolutionError(msg)

    if "no data" in msg_lower or "no market data" in msg_lower:
        raise NoMarketDataError(msg)


def get_quote(
    spec: SymbolSpec,
    client: IBKRClient,
    *,
    timeout_s: float = 5.0,
) -> Quote:
    """
    Get a snapshot quote for a single instrument.

    Args:
        spec: Symbol specification for the instrument.
        client: Connected IBKRClient instance.
        timeout_s: Maximum time to wait for data (default 5.0 seconds).

    Returns:
        Quote object with market data snapshot.

    Raises:
        ContractResolutionError: If contract cannot be resolved.
        MarketDataPermissionError: If market data permissions are insufficient.
        NoMarketDataError: If no data is available.
        MarketDataTimeoutError: If request times out.
        MarketDataError: For other market data errors.
    """
    import math

    # Ensure connected
    client.ensure_connected()
    broker = get_broker_adapter(client)

    logger.debug(f"Requesting quote for {spec.symbol} ({spec.securityType})")

    # Resolve contract
    try:
        contract = resolve_contract(spec, client)
    except ContractResolutionError:
        raise
    except Exception as e:
        raise ContractResolutionError(f"Failed to resolve {spec.symbol}: {e}") from e

    logger.debug(f"Resolved {spec.symbol} to conId={contract.conId}")

    # Track errors via error event
    errors: List[tuple] = []

    def on_error(reqId: int, errorCode: int, errorString: str, contract):
        errors.append((errorCode, errorString))

    # Register error handler
    broker.add_error_handler(on_error)

    try:
        # Request snapshot market data
        # Note: snapshot=True requests delayed/snapshot data without streaming subscription
        ticker = broker.request_market_data(contract, "", snapshot=True)

        deadline = time.time() + timeout_s

        # Poll until data arrives or timeout
        while time.time() < deadline:
            # Allow ib_insync to process events
            broker.sleep(POLL_INTERVAL_S)

            # Check for errors
            for error_code, error_msg in errors:
                exc = _check_ibkr_error_code(error_code, error_msg)
                if exc:
                    raise exc

            # Check if we have meaningful data
            if _ticker_has_data(ticker):
                logger.debug(
                    f"Quote received for {spec.symbol}: "
                    f"bid={ticker.bid}, ask={ticker.ask}, last={ticker.last}"
                )
                break
        else:
            # Timeout reached
            if errors:
                error_msgs = [f"[{c}] {m}" for c, m in errors]
                raise MarketDataTimeoutError(
                    f"Timeout waiting for {spec.symbol} quote after {timeout_s}s. "
                    f"Errors: {'; '.join(error_msgs)}"
                )
            else:
                raise MarketDataTimeoutError(
                    f"Timeout waiting for {spec.symbol} quote after {timeout_s}s"
                )

        # Build Quote object
        now = datetime.now(timezone.utc)

        def safe_float(val, default=0.0):
            if val is None:
                return default
            if isinstance(val, float) and math.isnan(val):
                return default
            return float(val) if val >= 0 else default

        return Quote(
            symbol=spec.symbol,
            conId=contract.conId,
            bid=safe_float(ticker.bid),
            ask=safe_float(ticker.ask),
            last=safe_float(ticker.last),
            bidSize=safe_float(ticker.bidSize),
            askSize=safe_float(ticker.askSize),
            lastSize=safe_float(ticker.lastSize),
            volume=safe_float(ticker.volume),
            timestamp=now,
            source="IBKR_SNAPSHOT",
        )
    finally:
        # Unregister error handler
        broker.remove_error_handler(on_error)
        # Cancel market data subscription
        broker.cancel_market_data(contract)


def get_quotes(
    specs: List[SymbolSpec],
    client: IBKRClient,
    *,
    timeout_s: float = 7.5,
) -> List[Quote]:
    """
    Get snapshot quotes for multiple instruments efficiently.

    Args:
        specs: List of symbol specifications.
        client: Connected IBKRClient instance.
        timeout_s: Maximum time to wait for all data (default 7.5 seconds).

    Returns:
        List of Quote objects in the same order as input specs.
        Quotes with no data will have 0.0 for price fields.

    Raises:
        ContractResolutionError: If any contract cannot be resolved.
        MarketDataTimeoutError: If request times out.
        MarketDataError: For other market data errors.
    """
    import math

    if not specs:
        return []

    # Ensure connected
    client.ensure_connected()
    broker = get_broker_adapter(client)

    logger.info(f"Requesting quotes for {len(specs)} instruments")

    # Resolve all contracts first
    contracts: Dict[str, Contract] = {}
    for spec in specs:
        try:
            contract = resolve_contract(spec, client)
            contracts[spec.symbol] = contract
        except ContractResolutionError:
            raise
        except Exception as e:
            raise ContractResolutionError(f"Failed to resolve {spec.symbol}: {e}") from e

    # Track errors via error event
    errors_by_symbol: Dict[str, List[tuple]] = {s.symbol: [] for s in specs}

    def on_error(reqId: int, errorCode: int, errorString: str, contract):
        # Try to match error to a symbol
        if contract:
            symbol = contract.symbol
            if symbol in errors_by_symbol:
                errors_by_symbol[symbol].append((errorCode, errorString))

    # Register error handler
    broker.add_error_handler(on_error)

    try:
        # Request market data for all contracts
        tickers: Dict[str, Ticker] = {}
        for spec in specs:
            contract = contracts[spec.symbol]
            ticker = broker.request_market_data(contract, "", snapshot=True)
            tickers[spec.symbol] = ticker

        deadline = time.time() + timeout_s
        pending = set(spec.symbol for spec in specs)
        errored = set()

        # Poll until all have data or timeout
        while pending and time.time() < deadline:
            broker.sleep(POLL_INTERVAL_S)

            # Check which tickers now have data
            for symbol in list(pending):
                ticker = tickers[symbol]

                # Check for errors - permission errors allow partial results
                for error_code, error_msg in errors_by_symbol[symbol]:
                    exc = _check_ibkr_error_code(error_code, error_msg)
                    if isinstance(exc, MarketDataPermissionError):
                        logger.warning(f"Permission error for {symbol}: {error_msg}")
                        errored.add(symbol)
                        pending.discard(symbol)
                        break

                if symbol not in pending:
                    continue

                if _ticker_has_data(ticker):
                    pending.discard(symbol)

        if pending:
            logger.warning(f"Timeout: {len(pending)} instruments did not receive data: {pending}")

        # Build Quote objects in order
        now = datetime.now(timezone.utc)
        quotes: List[Quote] = []

        def safe_float(val, default=0.0):
            if val is None:
                return default
            if isinstance(val, float) and math.isnan(val):
                return default
            return float(val) if val >= 0 else default

        for spec in specs:
            ticker = tickers[spec.symbol]
            contract = contracts[spec.symbol]

            quote = Quote(
                symbol=spec.symbol,
                conId=contract.conId,
                bid=safe_float(ticker.bid),
                ask=safe_float(ticker.ask),
                last=safe_float(ticker.last),
                bidSize=safe_float(ticker.bidSize),
                askSize=safe_float(ticker.askSize),
                lastSize=safe_float(ticker.lastSize),
                volume=safe_float(ticker.volume),
                timestamp=now,
                source="IBKR_SNAPSHOT",
            )
            quotes.append(quote)

        data_count = len(quotes) - len(pending) - len(errored)
        logger.info(
            f"Retrieved {len(quotes)} quotes, "
            f"{data_count} with data, {len(errored)} with errors"
        )

        return quotes

    finally:
        # Unregister error handler
        broker.remove_error_handler(on_error)
        # Cancel all market data subscriptions
        for spec in specs:
            contract = contracts[spec.symbol]
            broker.cancel_market_data(contract)


# =============================================================================
# Streaming Quote Functions
# =============================================================================


def _safe_float(val, default: float = 0.0) -> float:
    """Convert value to float, handling None and NaN."""
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return float(val) if val >= 0 else default


def _ticker_to_quote(
    ticker: Ticker,
    symbol: str,
    con_id: int,
    source: str = "IBKR_STREAMING",
) -> Quote:
    """Convert ib_insync Ticker to Quote model."""
    return Quote(
        symbol=symbol,
        conId=con_id,
        bid=_safe_float(ticker.bid),
        ask=_safe_float(ticker.ask),
        last=_safe_float(ticker.last),
        bidSize=_safe_float(ticker.bidSize),
        askSize=_safe_float(ticker.askSize),
        lastSize=_safe_float(ticker.lastSize),
        volume=_safe_float(ticker.volume),
        timestamp=datetime.now(timezone.utc),
        source=source,
    )


class StreamingQuote:
    """
    Streaming quote subscription for real-time market data.

    This class provides continuous real-time market data updates via the
    US Equity & Options Add-On Streaming subscription.

    Usage:
        # Context manager pattern (recommended)
        with StreamingQuote(spec, client) as stream:
            for quote in stream.updates(max_updates=10):
                print(f"Update: {quote.bid} / {quote.ask}")

        # Manual control
        stream = StreamingQuote(spec, client)
        stream.start()
        try:
            quote = stream.get_current()
            # ... do something with quote
        finally:
            stream.stop()
    """

    def __init__(
        self,
        spec: SymbolSpec,
        client: IBKRClient,
        *,
        timeout_s: float = 10.0,
    ):
        """
        Initialize streaming quote subscription.

        Args:
            spec: Symbol specification for the instrument.
            client: Connected IBKRClient instance.
            timeout_s: Timeout for initial data (default 10.0 seconds).
        """
        self._spec = spec
        self._client = client
        self._timeout_s = timeout_s
        self._contract: Optional[Contract] = None
        self._ticker: Optional[Ticker] = None
        self._is_active = False
        self._errors: List[tuple] = []
        self._on_error_handler: Optional[Callable] = None

    @property
    def is_active(self) -> bool:
        """Check if streaming is active."""
        return self._is_active

    @property
    def symbol(self) -> str:
        """Symbol being streamed."""
        return self._spec.symbol

    def _broker(self):
        return get_broker_adapter(self._client)

    def start(self) -> None:
        """
        Start streaming quotes.

        Raises:
            ContractResolutionError: If contract cannot be resolved.
            MarketDataPermissionError: If market data permissions are insufficient.
            MarketDataTimeoutError: If initial data not received in timeout.
        """
        if self._is_active:
            logger.debug(f"Streaming already active for {self._spec.symbol}")
            return

        self._client.ensure_connected()

        logger.info(f"Starting streaming quotes for {self._spec.symbol}")

        # Resolve contract
        try:
            self._contract = resolve_contract(self._spec, self._client)
        except ContractResolutionError:
            raise
        except Exception as e:
            raise ContractResolutionError(f"Failed to resolve {self._spec.symbol}: {e}") from e

        # Error handler
        self._errors = []
        broker = self._broker()

        def on_error(reqId: int, errorCode: int, errorString: str, contract):
            self._errors.append((errorCode, errorString))

        self._on_error_handler = on_error
        broker.add_error_handler(on_error)

        # Request streaming market data (snapshot=False for streaming)
        self._ticker = broker.request_market_data(self._contract, "", snapshot=False)

        # Wait for initial data
        deadline = time.time() + self._timeout_s
        while time.time() < deadline:
            broker.sleep(POLL_INTERVAL_S)

            # Check for errors
            for error_code, error_msg in self._errors:
                exc = _check_ibkr_error_code(error_code, error_msg)
                if exc:
                    self.stop()
                    raise exc

            if _ticker_has_data(self._ticker):
                logger.debug(
                    f"Streaming started for {self._spec.symbol}: "
                    f"bid={self._ticker.bid}, ask={self._ticker.ask}"
                )
                self._is_active = True
                return

        # Timeout
        self.stop()
        if self._errors:
            error_msgs = [f"[{c}] {m}" for c, m in self._errors]
            raise MarketDataTimeoutError(
                f"Timeout waiting for streaming data for {self._spec.symbol}. "
                f"Errors: {'; '.join(error_msgs)}"
            )
        else:
            raise MarketDataTimeoutError(
                f"Timeout waiting for streaming data for {self._spec.symbol}"
            )

    def stop(self) -> None:
        """Stop streaming quotes and clean up."""
        broker = self._broker()

        if self._on_error_handler:
            try:
                broker.remove_error_handler(self._on_error_handler)
            except Exception:
                pass
            self._on_error_handler = None

        if self._contract and self._client.is_connected:
            try:
                broker.cancel_market_data(self._contract)
            except Exception as e:
                logger.warning(f"Error canceling market data: {e}")

        self._is_active = False
        self._ticker = None
        logger.debug(f"Streaming stopped for {self._spec.symbol}")

    def get_current(self) -> Quote:
        """
        Get the current quote snapshot from the stream.

        Returns:
            Current Quote with latest data.

        Raises:
            MarketDataError: If streaming is not active.
        """
        if not self._is_active or not self._ticker:
            raise MarketDataError(
                f"Streaming not active for {self._spec.symbol}. Call start() first."
            )

        # Process any pending events
        self._broker().sleep(0)

        return _ticker_to_quote(
            self._ticker,
            self._spec.symbol,
            self._contract.conId,
            source="IBKR_STREAMING",
        )

    def updates(
        self,
        *,
        max_updates: Optional[int] = None,
        duration_s: Optional[float] = None,
        poll_interval_s: float = 0.1,
    ) -> Generator[Quote, None, None]:
        """
        Yield quote updates as they arrive.

        Args:
            max_updates: Maximum number of updates to yield (None = unlimited).
            duration_s: Maximum duration in seconds (None = unlimited).
            poll_interval_s: Polling interval for updates (default 0.1s).

        Yields:
            Quote objects with updated data.

        Note:
            At least one of max_updates or duration_s should be specified
            to avoid infinite iteration.
        """
        if not self._is_active or not self._ticker:
            raise MarketDataError(
                f"Streaming not active for {self._spec.symbol}. Call start() first."
            )

        count = 0
        start_time = time.time()
        last_quote: Optional[Quote] = None
        broker = self._broker()

        while True:
            # Check limits
            if max_updates is not None and count >= max_updates:
                break
            if duration_s is not None and (time.time() - start_time) >= duration_s:
                break

            # Process events
            broker.sleep(poll_interval_s)

            # Check for new errors
            for error_code, error_msg in self._errors:
                exc = _check_ibkr_error_code(error_code, error_msg)
                if exc:
                    raise exc
            self._errors.clear()

            # Get current quote
            quote = _ticker_to_quote(
                self._ticker,
                self._spec.symbol,
                self._contract.conId,
                source="IBKR_STREAMING",
            )

            # Only yield if data changed (avoid duplicate quotes)
            if last_quote is None or (
                quote.bid != last_quote.bid
                or quote.ask != last_quote.ask
                or quote.last != last_quote.last
                or quote.volume != last_quote.volume
            ):
                yield quote
                last_quote = quote
                count += 1

    def __enter__(self) -> "StreamingQuote":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()


def get_streaming_quote(
    spec: SymbolSpec,
    client: IBKRClient,
    *,
    timeout_s: float = 10.0,
) -> StreamingQuote:
    """
    Create a streaming quote subscription.

    This is a convenience factory function that returns a StreamingQuote
    object for real-time market data.

    Args:
        spec: Symbol specification for the instrument.
        client: Connected IBKRClient instance.
        timeout_s: Timeout for initial data (default 10.0 seconds).

    Returns:
        StreamingQuote instance (not yet started).

    Example:
        stream = get_streaming_quote(spec, client)
        with stream:
            for quote in stream.updates(max_updates=5):
                print(f"{quote.symbol}: {quote.bid}/{quote.ask}")
    """
    return StreamingQuote(spec, client, timeout_s=timeout_s)


def get_quote_with_mode(
    spec: SymbolSpec,
    client: IBKRClient,
    *,
    mode: QuoteMode = QuoteMode.SNAPSHOT,
    timeout_s: float = 5.0,
) -> Quote:
    """
    Get a quote using specified mode (snapshot or streaming).

    For snapshot mode: Returns immediately after receiving data.
    For streaming mode: Starts streaming, gets one quote, then stops.

    Args:
        spec: Symbol specification for the instrument.
        client: Connected IBKRClient instance.
        mode: Quote mode (SNAPSHOT or STREAMING).
        timeout_s: Maximum time to wait for data.

    Returns:
        Quote object with market data.

    Raises:
        ContractResolutionError: If contract cannot be resolved.
        MarketDataPermissionError: If market data permissions are insufficient.
        NoMarketDataError: If no data is available.
        MarketDataTimeoutError: If request times out.
    """
    if mode == QuoteMode.STREAMING:
        with StreamingQuote(spec, client, timeout_s=timeout_s) as stream:
            return stream.get_current()
    else:
        return get_quote(spec, client, timeout_s=timeout_s)


def get_historical_bars(
    spec: SymbolSpec,
    client: IBKRClient,
    bar_size: str,
    duration: str,
    what_to_show: str = "TRADES",
    rth_only: bool = True,
    *,
    timeout_s: float = 20.0,
) -> List[Bar]:
    """
    Get historical bars for an instrument.

    Args:
        spec: Symbol specification for the instrument.
        client: Connected IBKRClient instance.
        bar_size: Bar size (e.g., "1m", "5 min", "1h", "1d").
        duration: Duration to fetch (e.g., "5d", "1w", "1mo", "1y").
        what_to_show: Data type (default "TRADES"). Options include:
            TRADES, MIDPOINT, BID, ASK, BID_ASK, HISTORICAL_VOLATILITY
        rth_only: If True, only return data from regular trading hours.
        timeout_s: Maximum time to wait for data (default 20.0 seconds).

    Returns:
        List of Bar objects ordered by time (oldest first).

    Raises:
        ValueError: If bar_size, duration, or what_to_show is invalid.
        ContractResolutionError: If contract cannot be resolved.
        MarketDataPermissionError: If market data permissions are insufficient.
        NoMarketDataError: If no data is available.
        PacingViolationError: If IBKR pacing limits are exceeded.
        MarketDataTimeoutError: If request times out.
    """
    # Normalize inputs (raises ValueError on invalid)
    norm_bar_size = normalize_bar_size(bar_size)
    norm_duration = normalize_duration(duration)
    norm_what_to_show = normalize_what_to_show(what_to_show)

    # Ensure connected
    client.ensure_connected()
    broker = get_broker_adapter(client)

    logger.info(
        f"Requesting historical bars for {spec.symbol}: "
        f"bar_size={norm_bar_size}, duration={norm_duration}, "
        f"what_to_show={norm_what_to_show}, rth_only={rth_only}"
    )

    # Resolve contract
    try:
        contract = resolve_contract(spec, client)
    except ContractResolutionError:
        raise
    except Exception as e:
        raise ContractResolutionError(f"Failed to resolve {spec.symbol}: {e}") from e

    logger.debug(f"Resolved {spec.symbol} to conId={contract.conId}")

    # Track errors during the request
    errors: List[str] = []

    def on_error(reqId: int, errorCode: int, errorString: str, contract: Contract):
        """Handle error events during historical data request."""
        errors.append(f"[{errorCode}] {errorString}")

    # Register error handler temporarily
    broker.add_error_handler(on_error)

    try:
        # Request historical data
        # endDateTime="" means "now"
        bars = broker.request_historical_data(
            contract,
            end_date_time="",
            duration_str=norm_duration,
            bar_size_setting=norm_bar_size,
            what_to_show=norm_what_to_show,
            use_rth=rth_only,
            format_date=1,  # Use human-readable dates
            timeout=timeout_s,
        )

        # Check for errors
        for err in errors:
            _check_ibkr_error_message(err)

        if not bars:
            raise NoMarketDataError(
                f"No historical data returned for {spec.symbol} "
                f"({norm_duration}, {norm_bar_size})"
            )

        logger.info(f"Received {len(bars)} bars for {spec.symbol}")

        # Convert to our Bar model
        result: List[Bar] = []
        from datetime import date as date_type

        for ib_bar in bars:
            # Parse bar time - ib_insync returns datetime or date objects
            # For daily bars, it's a date; for intraday, it's a datetime
            bar_time = ib_bar.date

            if isinstance(bar_time, str):
                # Handle string format if needed
                try:
                    bar_time = datetime.fromisoformat(bar_time.replace(" ", "T"))
                except ValueError:
                    # Try alternative format
                    bar_time = datetime.strptime(bar_time, "%Y%m%d %H:%M:%S")
            elif isinstance(bar_time, date_type) and not isinstance(bar_time, datetime):
                # It's a date object (not datetime) - convert to datetime at midnight
                bar_time = datetime(
                    bar_time.year, bar_time.month, bar_time.day, tzinfo=timezone.utc
                )

            # Ensure timezone-aware (for datetime objects)
            if isinstance(bar_time, datetime) and bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)

            bar = Bar(
                symbol=spec.symbol,
                time=bar_time,
                open=float(ib_bar.open),
                high=float(ib_bar.high),
                low=float(ib_bar.low),
                close=float(ib_bar.close),
                volume=float(ib_bar.volume) if ib_bar.volume is not None else 0.0,
                barSize=norm_bar_size,
                source="IBKR_HISTORICAL",
            )
            result.append(bar)

        return result

    except asyncio.TimeoutError:
        raise MarketDataTimeoutError(
            f"Timeout waiting for historical data for {spec.symbol} " f"after {timeout_s}s"
        )
    except Exception as e:
        # Check if error message indicates specific issue
        err_str = str(e)
        _check_ibkr_error_message(err_str)

        if isinstance(e, MarketDataError):
            raise

        raise MarketDataError(f"Failed to get historical data for {spec.symbol}: {e}") from e

    finally:
        # Unregister error handler
        broker.remove_error_handler(on_error)


def _clean_optional_float(value) -> Optional[float]:
    """Convert IBKR numeric fields to float while treating missing values as None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_expiry(expiry: str) -> str:
    """Normalize IBKR expiry strings to YYYY-MM-DD when possible."""
    expiry = str(expiry)
    if len(expiry) == 8 and expiry.isdigit():
        return f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
    return expiry


def _build_option_greeks(computation) -> Optional[OptionGreeks]:
    """Convert ib_insync option computation data to our typed model."""
    if computation is None:
        return None

    values = {
        "impliedVol": _clean_optional_float(getattr(computation, "impliedVol", None)),
        "delta": _clean_optional_float(getattr(computation, "delta", None)),
        "optPrice": _clean_optional_float(getattr(computation, "optPrice", None)),
        "pvDividend": _clean_optional_float(getattr(computation, "pvDividend", None)),
        "gamma": _clean_optional_float(getattr(computation, "gamma", None)),
        "vega": _clean_optional_float(getattr(computation, "vega", None)),
        "theta": _clean_optional_float(getattr(computation, "theta", None)),
        "undPrice": _clean_optional_float(getattr(computation, "undPrice", None)),
    }
    if all(value is None for value in values.values()):
        return None
    return OptionGreeks(**values)


def _normalize_rights(rights: Optional[List[str]]) -> List[str]:
    """Normalize option rights to ['C', 'P']."""
    if not rights:
        return ["C", "P"]

    normalized: List[str] = []
    for right in rights:
        spec = SymbolSpec(symbol="TMP", securityType="OPT", expiry="2099-01-01", strike=1.0, right=right)
        if spec.right and spec.right not in normalized:
            normalized.append(spec.right)
    return normalized or ["C", "P"]


def get_option_chain(
    underlying_spec: SymbolSpec,
    client: IBKRClient,
    *,
    expiries: Optional[List[str]] = None,
    expiry_start: Optional[str] = None,
    expiry_end: Optional[str] = None,
    min_strike: Optional[float] = None,
    max_strike: Optional[float] = None,
    strike_count: int = 10,
    max_candidates: int = 24,
    rights: Optional[List[str]] = None,
    option_exchange: Optional[str] = None,
    timeout_s: float = 10.0,
) -> OptionChainResponse:
    """
    Discover and qualify single-leg option contracts for an underlying.

    Returns filtered expirations/strikes plus a bounded list of qualified contracts
    suitable for LLM selection and downstream trading.
    """
    if underlying_spec.securityType == "OPT":
        raise ValueError("underlying_spec must describe the underlying instrument, not an option")

    if strike_count <= 0:
        raise ValueError("strike_count must be positive")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")

    client.ensure_connected()
    broker = get_broker_adapter(client)

    underlying_contract = resolve_contract(underlying_spec, client)

    underlying_quote = None
    try:
        underlying_quote = get_quote(underlying_spec, client, timeout_s=min(timeout_s, 5.0))
    except MarketDataError as exc:
        logger.info("Underlying quote unavailable for option chain %s: %s", underlying_spec.symbol, exc)

    underlying_last = None
    if underlying_quote is not None:
        for candidate in (underlying_quote.last, underlying_quote.ask, underlying_quote.bid):
            if candidate and candidate > 0:
                underlying_last = candidate
                break

    sec_def_exchange = ""
    if underlying_contract.secType == "FUT":
        sec_def_exchange = option_exchange or underlying_contract.exchange or ""

    option_chains = broker.request_option_chain_params(
        underlying_symbol=underlying_contract.symbol,
        fut_fop_exchange=sec_def_exchange,
        underlying_sec_type=underlying_contract.secType,
        underlying_con_id=underlying_contract.conId,
    )
    if not option_chains:
        raise NoMarketDataError(f"No option chain returned for {underlying_spec.symbol}")

    preferred_exchange = option_exchange or underlying_spec.exchange or "SMART"
    sorted_chains = sorted(
        option_chains,
        key=lambda chain: (
            0 if chain.exchange == preferred_exchange else 1,
            0 if chain.exchange == "SMART" else 1,
            chain.exchange,
        ),
    )
    selected_chain = sorted_chains[0]

    normalized_expiries = sorted(
        {_format_expiry(expiry) for expiry in selected_chain.expirations},
    )
    if expiries:
        expiry_filter = {_format_expiry(expiry) for expiry in expiries}
        normalized_expiries = [expiry for expiry in normalized_expiries if expiry in expiry_filter]
    if expiry_start:
        normalized_expiries = [expiry for expiry in normalized_expiries if expiry >= expiry_start]
    if expiry_end:
        normalized_expiries = [expiry for expiry in normalized_expiries if expiry <= expiry_end]

    normalized_strikes = sorted(float(strike) for strike in selected_chain.strikes)
    if min_strike is not None:
        normalized_strikes = [strike for strike in normalized_strikes if strike >= min_strike]
    if max_strike is not None:
        normalized_strikes = [strike for strike in normalized_strikes if strike <= max_strike]

    if underlying_last is not None and normalized_strikes:
        normalized_strikes = sorted(
            normalized_strikes,
            key=lambda strike: (abs(strike - underlying_last), strike),
        )[:strike_count]
        normalized_strikes.sort()
    else:
        normalized_strikes = normalized_strikes[:strike_count]

    normalized_rights = _normalize_rights(rights)
    expiries_for_candidates = normalized_expiries
    option_currency = underlying_contract.currency or underlying_spec.currency or "USD"
    option_exchange = option_exchange or selected_chain.exchange or underlying_spec.exchange or "SMART"

    candidate_contracts: List[Contract] = []
    for expiry in expiries_for_candidates:
        for strike in normalized_strikes:
            for right in normalized_rights:
                if len(candidate_contracts) >= max_candidates:
                    break
                option_contract = Option(
                    symbol=underlying_contract.symbol,
                    lastTradeDateOrContractMonth=expiry.replace("-", ""),
                    strike=strike,
                    right=right,
                    exchange=option_exchange,
                    currency=option_currency,
                )
                if selected_chain.multiplier:
                    option_contract.multiplier = selected_chain.multiplier
                if selected_chain.tradingClass:
                    option_contract.tradingClass = selected_chain.tradingClass
                candidate_contracts.append(option_contract)
            if len(candidate_contracts) >= max_candidates:
                break
        if len(candidate_contracts) >= max_candidates:
            break

    qualified_contracts = broker.qualify_contracts(*candidate_contracts) if candidate_contracts else []
    candidates = [
        OptionContractCandidate(
            symbol=contract.symbol,
            conId=contract.conId,
            exchange=contract.exchange or option_exchange,
            currency=contract.currency or option_currency,
            expiry=_format_expiry(contract.lastTradeDateOrContractMonth),
            strike=float(contract.strike),
            right=contract.right,
            multiplier=contract.multiplier or selected_chain.multiplier or None,
            localSymbol=getattr(contract, "localSymbol", None) or None,
            tradingClass=getattr(contract, "tradingClass", None) or None,
        )
        for contract in qualified_contracts
    ]

    return OptionChainResponse(
        underlying=contract_to_resolved_contract(underlying_contract),
        underlyingPrice=underlying_last,
        exchange=selected_chain.exchange or option_exchange,
        multiplier=selected_chain.multiplier or None,
        expirations=normalized_expiries,
        strikes=normalized_strikes,
        candidates=candidates,
        candidateCount=len(candidates),
    )


def get_option_snapshot(
    option_spec: SymbolSpec,
    client: IBKRClient,
    *,
    timeout_s: float = 5.0,
) -> OptionSnapshotResponse:
    """
    Get a single-leg option snapshot including quote and greeks when available.

    The quote is required; greeks and volatility fields are returned opportunistically.
    """
    if option_spec.securityType != "OPT":
        raise ValueError("option_spec.securityType must be 'OPT'")
    if not option_spec.expiry or option_spec.strike is None or not option_spec.right:
        raise ValueError("Fully specified option contracts require expiry, strike, and right")

    client.ensure_connected()
    broker = get_broker_adapter(client)

    contract = resolve_contract(option_spec, client)
    quote = get_quote(option_spec, client, timeout_s=timeout_s)

    errors: List[tuple[int, str]] = []

    def on_error(reqId: int, errorCode: int, errorString: str, contract):
        errors.append((errorCode, errorString))

    broker.add_error_handler(on_error)
    try:
        ticker = broker.request_market_data(contract, "100,101,104,106", snapshot=True)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            broker.sleep(POLL_INTERVAL_S)
            if (
                _build_option_greeks(getattr(ticker, "modelGreeks", None))
                or _clean_optional_float(getattr(ticker, "impliedVolatility", None)) is not None
            ):
                break
        for error_code, error_msg in errors:
            exc = _check_ibkr_error_code(error_code, error_msg)
            if isinstance(exc, (ContractResolutionError, PacingViolationError)):
                raise exc
            if exc:
                logger.info("Option snapshot returned partial data for %s: %s", option_spec.symbol, exc)
                break

        greek_sets = OptionGreeksSet(
            model=_build_option_greeks(getattr(ticker, "modelGreeks", None)),
            bid=_build_option_greeks(getattr(ticker, "bidGreeks", None)),
            ask=_build_option_greeks(getattr(ticker, "askGreeks", None)),
            last=_build_option_greeks(getattr(ticker, "lastGreeks", None)),
        )

        underlying_last = None
        for greek_bucket in (greek_sets.model, greek_sets.last, greek_sets.bid, greek_sets.ask):
            if greek_bucket and greek_bucket.undPrice is not None:
                underlying_last = greek_bucket.undPrice
                break

        return OptionSnapshotResponse(
            contract=contract_to_resolved_contract(contract),
            quote=quote,
            underlyingLastPrice=underlying_last,
            impliedVolatility=_clean_optional_float(getattr(ticker, "impliedVolatility", None)),
            histVolatility=_clean_optional_float(getattr(ticker, "histVolatility", None)),
            rtHistVolatility=_clean_optional_float(getattr(ticker, "rtHistVolatility", None)),
            greeks=greek_sets,
        )
    finally:
        broker.remove_error_handler(on_error)
        broker.cancel_market_data(contract)
