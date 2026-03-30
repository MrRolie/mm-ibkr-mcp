from __future__ import annotations

"""
Simulated IBKR client for testing without a live connection.

Provides a SimulatedIBKRClient that implements the same interface as IBKRClient
but operates entirely in-memory with simulated responses.

Usage:
    client = SimulatedIBKRClient()
    client.connect()  # Always succeeds
    # Use same API as real client...
"""

import copy
import hashlib
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from ib_insync import Contract

from ibkr_core.client import IBKRClient
from ibkr_core.config import Config, get_config
from ibkr_core.metrics import record_ibkr_operation, set_connection_status


@dataclass
class SimulatedQuote:
    """Simulated quote data."""

    symbol: str
    bid: float
    ask: float
    last: float
    bid_size: int = 100
    ask_size: int = 100
    last_size: int = 50
    volume: int = 1000000
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def mid(self) -> float:
        """Mid price."""
        return (self.bid + self.ask) / 2


@dataclass
class SimulatedOrder:
    """Simulated order tracking."""

    order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    limit_price: Optional[float]
    status: str
    account_id: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    filled_quantity: float = 0.0
    fill_price: Optional[float] = None
    ibkr_order_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "status": self.status,
            "account_id": self.account_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "filled_quantity": self.filled_quantity,
            "fill_price": self.fill_price,
            "ibkr_order_id": self.ibkr_order_id,
        }


@dataclass
class SimulatedAccountValue:
    account: str
    tag: str
    value: str
    currency: str


@dataclass
class SimulatedContractDetails:
    contract: Contract


@dataclass
class SimulatedHistoricalBar:
    date: datetime | date | str
    open: float
    high: float
    low: float
    close: float
    volume: float
    barCount: int = 0
    average: float = 0.0


@dataclass
class SimulatedOptionChain:
    exchange: str
    tradingClass: str
    multiplier: str
    expirations: List[str]
    strikes: List[float]


class SimulatedEvent:
    """Minimal event object that matches ib_insync's += / -= handler pattern."""

    def __init__(self):
        self._handlers: List[Any] = []

    def __iadd__(self, handler):
        if handler not in self._handlers:
            self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self

    def emit(self, *args, **kwargs) -> None:
        for handler in list(self._handlers):
            handler(*args, **kwargs)


class SimulatedIBKRClient:
    """
    Simulated IBKR client for testing without a live connection.

    Implements the same interface as IBKRClient but operates entirely
    in-memory. Useful for:
    - Unit testing without IBKR Gateway
    - Development without market data subscriptions
    - CI/CD pipelines

    Features:
    - Simulated connection (always succeeds)
    - Synthetic quote generation
    - Order lifecycle simulation with state transitions
    - Thread-safe order registry
    """

    # Base prices for common symbols (simulated)
    BASE_PRICES: Dict[str, float] = {
        "AAPL": 250.00,
        "MSFT": 400.00,
        "GOOGL": 175.00,
        "AMZN": 200.00,
        "META": 550.00,
        "NVDA": 140.00,
        "TSLA": 250.00,
        "SPY": 600.00,
        "QQQ": 525.00,
        "IWM": 225.00,
    }

    DEFAULT_PRICE = 100.00

    def __init__(
        self,
        config: Optional[Config] = None,
        mode: str = "simulation",
        client_id: Optional[int] = None,
        account_id: str = "SIM000001",
    ):
        """
        Initialize SimulatedIBKRClient.

        Args:
            config: Configuration object (optional, for compatibility)
            mode: Always 'simulation' for this client
            client_id: Simulated client ID (default: 999)
            account_id: Simulated account ID (default: SIM000001)
        """
        self._config = config or get_config()
        self._mode = "simulation"
        self._client_id = client_id or 999
        self._account_id = account_id
        self._connected = False
        self._connection_time: Optional[datetime] = None

        # Order registry (thread-safe)
        self._orders: Dict[str, SimulatedOrder] = {}
        self._order_lock = threading.Lock()
        self._next_ibkr_order_id = 1000

        # Quote cache for consistency
        self._quote_cache: Dict[str, SimulatedQuote] = {}
        self._quote_lock = threading.Lock()
        self._request_timeout = 10.0
        self._error_event = SimulatedEvent()
        self._broker_trades: List[Any] = []
        self._broker = SimulatedBrokerAdapter(self)
        self._ib = SimulatedIB(self)

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected

    @property
    def mode(self) -> str:
        """Current trading mode (always 'simulation')."""
        return self._mode

    @property
    def host(self) -> str:
        """Simulated gateway host."""
        return "simulation"

    @property
    def port(self) -> int:
        """Simulated gateway port."""
        return 0

    @property
    def client_id(self) -> int:
        """Client ID."""
        return self._client_id

    @property
    def connection_time(self) -> Optional[datetime]:
        """Time when connection was established."""
        return self._connection_time

    @property
    def managed_accounts(self) -> List[str]:
        """List of managed accounts."""
        if not self.is_connected:
            return []
        return [self._account_id]

    @property
    def ib(self) -> "SimulatedIB":
        """Simulated IB interface for compatibility."""
        return self._ib

    @property
    def broker(self) -> "SimulatedBrokerAdapter":
        """Adapter-backed broker surface used by the refactored core layer."""
        return self._broker

    def connect(self, timeout: int = 10, readonly: bool = False) -> None:
        """
        Simulate connection to IBKR.

        Always succeeds after a small delay to simulate network latency.
        """
        if self._connected:
            return

        start_time = time.time()

        # Simulate connection delay
        time.sleep(0.05)

        self._connected = True
        self._connection_time = datetime.now()

        elapsed_seconds = time.time() - start_time
        record_ibkr_operation("connect", "success", elapsed_seconds)
        set_connection_status(self._mode, connected=True)

    def disconnect(self) -> None:
        """Simulate disconnection."""
        if self._connected:
            set_connection_status(self._mode, connected=False)

        self._connected = False
        self._connection_time = None

    def ensure_connected(self, timeout: int = 10) -> None:
        """Ensure connection is active."""
        if not self._connected:
            self.connect(timeout=timeout)

    def get_server_time(self, timeout_s: Optional[float] = None) -> datetime:
        """Get simulated server time."""
        if not self._connected:
            raise RuntimeError("Not connected")
        return datetime.now()

    def get_quote(self, symbol: str) -> SimulatedQuote:
        """
        Get a simulated quote for a symbol.

        Generates consistent synthetic quotes with realistic bid/ask spreads.
        """
        if not self._connected:
            raise RuntimeError("Not connected")

        with self._quote_lock:
            if symbol not in self._quote_cache:
                self._quote_cache[symbol] = self._generate_quote(symbol)
            else:
                # Update with small price movement
                self._quote_cache[symbol] = self._update_quote(self._quote_cache[symbol])
            return self._quote_cache[symbol]

    def _generate_quote(self, symbol: str) -> SimulatedQuote:
        """Generate initial quote for a symbol."""
        base_price = self.BASE_PRICES.get(symbol.upper(), self.DEFAULT_PRICE)

        # Add some randomness
        variation = base_price * random.uniform(-0.02, 0.02)
        mid_price = base_price + variation

        # Typical spread of 0.01-0.05%
        spread = mid_price * random.uniform(0.0001, 0.0005)

        return SimulatedQuote(
            symbol=symbol.upper(),
            bid=round(mid_price - spread / 2, 2),
            ask=round(mid_price + spread / 2, 2),
            last=round(mid_price + random.uniform(-spread, spread), 2),
        )

    def _update_quote(self, quote: SimulatedQuote) -> SimulatedQuote:
        """Update quote with small price movement."""
        # Small random movement (±0.1%)
        movement = quote.mid * random.uniform(-0.001, 0.001)
        new_mid = quote.mid + movement
        spread = quote.ask - quote.bid

        return SimulatedQuote(
            symbol=quote.symbol,
            bid=round(new_mid - spread / 2, 2),
            ask=round(new_mid + spread / 2, 2),
            last=round(new_mid + random.uniform(-spread / 2, spread / 2), 2),
            timestamp=datetime.now(),
        )

    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float] = None,
        account_id: Optional[str] = None,
    ) -> SimulatedOrder:
        """
        Submit a simulated order.

        Validates the order and simulates state transitions.
        Market orders fill immediately; limit orders check price.
        """
        if not self._connected:
            raise RuntimeError("Not connected")

        # Validate order
        if side.upper() not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side}")
        if quantity <= 0:
            raise ValueError(f"Invalid quantity: {quantity}")
        if order_type.upper() not in ("MKT", "LMT"):
            raise ValueError(f"Invalid order type: {order_type}")
        if order_type.upper() == "LMT" and limit_price is None:
            raise ValueError("Limit price required for LMT orders")

        order_id = str(uuid.uuid4())

        with self._order_lock:
            ibkr_order_id = self._next_ibkr_order_id
            self._next_ibkr_order_id += 1

            order = SimulatedOrder(
                order_id=order_id,
                symbol=symbol.upper(),
                side=side.upper(),
                quantity=quantity,
                order_type=order_type.upper(),
                limit_price=limit_price,
                status="PendingSubmit",
                account_id=account_id or self._account_id,
                ibkr_order_id=ibkr_order_id,
            )

            self._orders[order_id] = order

        # Simulate async state transitions
        self._process_order(order_id)

        return order

    def _process_order(self, order_id: str) -> None:
        """Process order through state machine."""
        with self._order_lock:
            order = self._orders.get(order_id)
            if not order:
                return

            # Transition: PendingSubmit -> Submitted
            order.status = "Submitted"
            order.updated_at = datetime.now()

            # Get current quote
            quote = self.get_quote(order.symbol)

            # Determine if order should fill
            should_fill = False
            fill_price = None

            if order.order_type == "MKT":
                # Market orders always fill immediately
                should_fill = True
                fill_price = quote.ask if order.side == "BUY" else quote.bid
            elif order.order_type == "LMT":
                # Limit orders fill if price is favorable
                if order.side == "BUY" and order.limit_price >= quote.ask:
                    should_fill = True
                    fill_price = min(order.limit_price, quote.ask)
                elif order.side == "SELL" and order.limit_price <= quote.bid:
                    should_fill = True
                    fill_price = max(order.limit_price, quote.bid)

            if should_fill:
                order.status = "Filled"
                order.filled_quantity = order.quantity
                order.fill_price = fill_price
                order.updated_at = datetime.now()

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a simulated order.

        Returns True if order was cancelled, False if not found or already filled.
        """
        with self._order_lock:
            order = self._orders.get(order_id)
            if not order:
                return False

            if order.status in ("Filled", "Cancelled"):
                return False

            order.status = "Cancelled"
            order.updated_at = datetime.now()
            return True

    def get_order(self, order_id: str) -> Optional[SimulatedOrder]:
        """Get order by ID."""
        with self._order_lock:
            return self._orders.get(order_id)

    def get_open_orders(self) -> List[SimulatedOrder]:
        """Get all open (non-filled, non-cancelled) orders."""
        with self._order_lock:
            return [o for o in self._orders.values() if o.status not in ("Filled", "Cancelled")]

    def get_all_orders(self) -> List[SimulatedOrder]:
        """Get all orders in the registry."""
        with self._order_lock:
            return list(self._orders.values())

    def clear_orders(self) -> None:
        """Clear order registry (for testing)."""
        with self._order_lock:
            self._orders.clear()

    def __enter__(self) -> "SimulatedIBKRClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.disconnect()

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return f"SimulatedIBKRClient(account={self._account_id}, {status})"


class SimulatedIB:
    """
    Simulated ib_insync.IB interface for compatibility.

    Provides the minimal interface needed by existing code that
    accesses client.ib directly.
    """

    def __init__(self, client: SimulatedIBKRClient):
        self._client = client

    @property
    def errorEvent(self) -> SimulatedEvent:
        return self._client._error_event

    @property
    def RequestTimeout(self) -> float:
        return self._client._request_timeout

    @RequestTimeout.setter
    def RequestTimeout(self, value: float) -> None:
        self._client._request_timeout = float(value)

    def isConnected(self) -> bool:
        """Check connection status."""
        return self._client.is_connected

    def managedAccounts(self) -> List[str]:
        """Get managed accounts."""
        return self._client.managed_accounts

    def reqCurrentTime(self) -> datetime:
        """Get current server time."""
        return self._client.get_server_time()

    def disconnect(self) -> None:
        """Disconnect."""
        self._client.disconnect()

    def reqContractDetails(self, contract: Contract) -> List[SimulatedContractDetails]:
        return self._client.broker.request_contract_details(contract)

    def qualifyContracts(self, *contracts: Contract) -> List[Contract]:
        return self._client.broker.qualify_contracts(*contracts)

    def reqMktData(
        self,
        contract: Contract,
        genericTickList: str = "",
        snapshot: bool = False,
    ) -> Any:
        return self._client.broker.request_market_data(
            contract,
            genericTickList,
            snapshot=snapshot,
        )

    def cancelMktData(self, contract: Contract) -> None:
        self._client.broker.cancel_market_data(contract)

    def reqHistoricalData(
        self,
        contract: Contract,
        endDateTime: str,
        durationStr: str,
        barSizeSetting: str,
        whatToShow: str,
        useRTH: bool,
        formatDate: int,
        timeout: float,
    ) -> List[SimulatedHistoricalBar]:
        return self._client.broker.request_historical_data(
            contract,
            end_date_time=endDateTime,
            duration_str=durationStr,
            bar_size_setting=barSizeSetting,
            what_to_show=whatToShow,
            use_rth=useRTH,
            format_date=formatDate,
            timeout=timeout,
        )

    def reqSecDefOptParams(
        self,
        underlyingSymbol: str,
        futFopExchange: str,
        underlyingSecType: str,
        underlyingConId: int,
    ) -> List[SimulatedOptionChain]:
        return self._client.broker.request_option_chain_params(
            underlying_symbol=underlyingSymbol,
            fut_fop_exchange=futFopExchange,
            underlying_sec_type=underlyingSecType,
            underlying_con_id=underlyingConId,
        )

    def accountSummary(self, account: Optional[str] = None) -> List[SimulatedAccountValue]:
        return self._client.broker.account_summary(account)

    def reqPositions(self) -> None:
        self._client.broker.request_positions()

    def positions(self) -> List[Any]:
        return self._client.broker.positions()

    def portfolio(self, account: str) -> List[Any]:
        return self._client.broker.portfolio(account)

    def cancelPositions(self) -> None:
        self._client.broker.cancel_positions()

    def reqPnL(self, account: str) -> None:
        self._client.broker.request_pnl(account)

    def pnl(self, account: str) -> Any:
        return self._client.broker.pnl(account)

    def cancelPnL(self, account: str) -> None:
        self._client.broker.cancel_pnl(account)

    def placeOrder(self, contract: Contract, order: Any) -> Any:
        return self._client.broker.place_order(contract, order)

    def cancelOrder(self, order: Any) -> None:
        self._client.broker.cancel_order(order)

    def openTrades(self) -> List[Any]:
        return self._client.broker.open_trades()

    def trades(self) -> List[Any]:
        return self._client.broker.trades()

    def sleep(self, seconds: float) -> None:
        self._client.broker.sleep(seconds)


class SimulatedBrokerAdapter:
    """Adapter-backed fake broker that keeps simulation compatible with core seams."""

    def __init__(self, client: SimulatedIBKRClient):
        self._client = client

    def is_connected(self) -> bool:
        return self._client.is_connected

    def managed_accounts(self) -> list[str]:
        return self._client.managed_accounts

    def request_current_time(self) -> datetime:
        return self._client.get_server_time()

    async def request_current_time_async(self) -> datetime:
        return self._client.get_server_time()

    def request_contract_details(self, contract: Contract) -> list[SimulatedContractDetails]:
        qualified = self._qualify_contract(copy.copy(contract))
        if qualified.secType != "FUT" or getattr(qualified, "lastTradeDateOrContractMonth", None):
            return [SimulatedContractDetails(contract=qualified)]

        now = datetime.now(timezone.utc)
        expiries: List[str] = []
        for offset in range(4):
            month_index = now.month + offset
            year = now.year + (month_index - 1) // 12
            month = ((month_index - 1) % 12) + 1
            expiries.append(f"{year:04d}{month:02d}")

        details = []
        for expiry in expiries:
            future_contract = copy.copy(qualified)
            future_contract.lastTradeDateOrContractMonth = expiry
            future_contract.localSymbol = f"{future_contract.symbol}{expiry}"
            future_contract.conId = self._stable_int(
                f"{future_contract.symbol}:{future_contract.secType}:{expiry}"
            )
            details.append(SimulatedContractDetails(contract=future_contract))
        return details

    def qualify_contracts(self, *contracts: Contract) -> list[Contract]:
        return [self._qualify_contract(copy.copy(contract)) for contract in contracts]

    def add_error_handler(self, handler: Any) -> None:
        self._client._error_event += handler

    def remove_error_handler(self, handler: Any) -> None:
        self._client._error_event -= handler

    def request_market_data(
        self,
        contract: Contract,
        generic_tick_list: str = "",
        *,
        snapshot: bool = False,
    ) -> Any:
        qualified = self._qualify_contract(copy.copy(contract))
        if qualified.secType == "OPT":
            return self._build_option_ticker(qualified)

        quote = self._client.get_quote(qualified.symbol)
        return SimpleNamespace(
            contract=qualified,
            bid=quote.bid,
            ask=quote.ask,
            last=quote.last,
            bidSize=quote.bid_size,
            askSize=quote.ask_size,
            lastSize=quote.last_size,
            volume=quote.volume,
            close=quote.last,
            modelGreeks=None,
            bidGreeks=None,
            askGreeks=None,
            lastGreeks=None,
            impliedVolatility=None,
            histVolatility=None,
            rtHistVolatility=None,
        )

    def cancel_market_data(self, contract: Contract) -> None:
        return None

    def request_historical_data(
        self,
        contract: Contract,
        *,
        end_date_time: str,
        duration_str: str,
        bar_size_setting: str,
        what_to_show: str,
        use_rth: bool,
        format_date: int,
        timeout: float,
    ) -> list[SimulatedHistoricalBar]:
        qualified = self._qualify_contract(copy.copy(contract))
        reference_quote = self.request_market_data(qualified, snapshot=True)
        step = self._bar_delta(bar_size_setting)
        count = self._bar_count(duration_str, step)
        anchor = datetime.now(timezone.utc)
        price = float(reference_quote.last or reference_quote.bid or reference_quote.ask or 100.0)

        bars: List[SimulatedHistoricalBar] = []
        for idx in range(count):
            timestamp = anchor - step * (count - idx - 1)
            drift = (idx - count / 2) * 0.05
            open_price = max(0.01, price + drift)
            close_price = max(0.01, open_price + ((idx % 3) - 1) * 0.08)
            high_price = max(open_price, close_price) + 0.12
            low_price = max(0.01, min(open_price, close_price) - 0.12)
            bar_date: datetime | date = timestamp
            if step >= timedelta(days=1):
                bar_date = timestamp.date()
            bars.append(
                SimulatedHistoricalBar(
                    date=bar_date,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(low_price, 2),
                    close=round(close_price, 2),
                    volume=1000 + idx * 10,
                    barCount=1,
                    average=round((open_price + close_price) / 2, 2),
                )
            )
        return bars

    def request_option_chain_params(
        self,
        underlying_symbol: str,
        fut_fop_exchange: str,
        underlying_sec_type: str,
        underlying_con_id: int,
    ) -> list[SimulatedOptionChain]:
        underlying_quote = self._client.get_quote(underlying_symbol)
        center = underlying_quote.last or underlying_quote.mid
        base_strike = round(center / 5) * 5
        strikes = [float(base_strike + step) for step in (-10, -5, 0, 5, 10)]

        next_friday = datetime.now(timezone.utc)
        while next_friday.weekday() != 4:
            next_friday += timedelta(days=1)
        expirations = [
            (next_friday + timedelta(days=7 * idx)).strftime("%Y%m%d")
            for idx in range(4)
        ]

        exchange = fut_fop_exchange or "SMART"
        trading_class = underlying_symbol if underlying_sec_type != "FUT" else f"{underlying_symbol}OPT"
        return [
            SimulatedOptionChain(
                exchange=exchange,
                tradingClass=trading_class,
                multiplier="100",
                expirations=expirations,
                strikes=strikes,
            )
        ]

    def get_request_timeout(self) -> Optional[float]:
        return self._client._request_timeout

    def set_request_timeout(self, timeout: float) -> None:
        self._client._request_timeout = float(timeout)

    def account_summary(self, account_id: Optional[str] = None) -> list[SimulatedAccountValue]:
        account = account_id or self.managed_accounts()[0]
        portfolio_items = self.portfolio(account)
        unrealized = sum(float(item.unrealizedPNL) for item in portfolio_items)
        cash = 100000.0
        net_liquidation = cash + unrealized
        buying_power = max(0.0, cash * 2.0)
        return [
            SimulatedAccountValue(account, "NetLiquidation", f"{net_liquidation:.2f}", "USD"),
            SimulatedAccountValue(account, "TotalCashValue", f"{cash:.2f}", "USD"),
            SimulatedAccountValue(account, "BuyingPower", f"{buying_power:.2f}", "USD"),
            SimulatedAccountValue(account, "ExcessLiquidity", f"{cash + unrealized:.2f}", "USD"),
            SimulatedAccountValue(account, "MaintMarginReq", "0.00", "USD"),
            SimulatedAccountValue(account, "InitMarginReq", "0.00", "USD"),
        ]

    def request_positions(self) -> None:
        return None

    def positions(self) -> list[Any]:
        account = self.managed_accounts()[0]
        positions: Dict[int, Dict[str, Any]] = {}
        for trade in self._client._broker_trades:
            if trade.orderStatus.status != "Filled":
                continue
            qty = float(trade.orderStatus.filled)
            if qty <= 0:
                continue
            signed_qty = qty if trade.order.action == "BUY" else -qty
            con_id = int(getattr(trade.contract, "conId", 0) or 0)
            bucket = positions.setdefault(
                con_id,
                {
                    "account": account,
                    "contract": trade.contract,
                    "position": 0.0,
                    "cost": 0.0,
                },
            )
            bucket["position"] += signed_qty
            bucket["cost"] += abs(signed_qty) * float(trade.orderStatus.avgFillPrice or 0.0)

        result = []
        for bucket in positions.values():
            if bucket["position"] == 0:
                continue
            avg_fill = bucket["cost"] / abs(bucket["position"]) if bucket["position"] else 0.0
            result.append(
                SimpleNamespace(
                    account=bucket["account"],
                    contract=bucket["contract"],
                    position=bucket["position"],
                    avgCost=avg_fill * abs(bucket["position"]),
                )
            )
        return result

    def portfolio(self, account_id: str) -> list[Any]:
        items = []
        for pos in self.positions():
            if pos.account != account_id:
                continue
            ticker = self.request_market_data(pos.contract, snapshot=True)
            market_price = float(ticker.last or ticker.bid or ticker.ask or 0.0)
            market_value = pos.position * market_price
            avg_price = pos.avgCost / abs(pos.position) if pos.position else 0.0
            unrealized = (market_price - avg_price) * pos.position
            items.append(
                SimpleNamespace(
                    contract=pos.contract,
                    marketPrice=market_price,
                    marketValue=market_value,
                    unrealizedPNL=unrealized,
                    realizedPNL=0.0,
                )
            )
        return items

    def cancel_positions(self) -> None:
        return None

    def request_pnl(self, account_id: str) -> None:
        return None

    def pnl(self, account_id: str) -> Any:
        portfolio_items = self.portfolio(account_id)
        unrealized = sum(float(item.unrealizedPNL) for item in portfolio_items)
        return SimpleNamespace(dailyPnL=unrealized, unrealizedPnL=unrealized, realizedPnL=0.0)

    def cancel_pnl(self, account_id: str) -> None:
        return None

    def place_order(self, contract: Any, order: Any) -> Any:
        if not self._client.is_connected:
            raise RuntimeError("Not connected")

        qualified_contract = self._qualify_contract(copy.copy(contract))
        order_id = getattr(order, "orderId", None) or self._allocate_order_id()
        order.orderId = order_id
        order.permId = getattr(order, "permId", None) or self._stable_int(f"perm:{order_id}")

        quantity = float(getattr(order, "totalQuantity", 0) or 0)
        side = str(getattr(order, "action", "BUY")).upper()
        ticker = self.request_market_data(qualified_contract, snapshot=True)
        status, filled, avg_fill_price = self._initial_order_status(order, ticker, side, quantity)

        trade = SimpleNamespace(
            contract=qualified_contract,
            order=order,
            orderStatus=SimpleNamespace(
                status=status,
                filled=filled,
                remaining=max(0.0, quantity - filled),
                avgFillPrice=avg_fill_price,
            ),
            log=[],
        )
        if status == "Filled":
            trade.log.append(SimpleNamespace(message="Simulated fill"))
        self._client._broker_trades.append(trade)
        return trade

    def cancel_order(self, order: Any) -> None:
        target_order_id = getattr(order, "orderId", None)
        for trade in self._client._broker_trades:
            if getattr(trade.order, "orderId", None) != target_order_id:
                continue
            if trade.orderStatus.status == "Filled":
                return
            trade.orderStatus.status = "Cancelled"
            trade.orderStatus.remaining = max(
                0.0,
                float(getattr(trade.order, "totalQuantity", 0) or 0) - float(trade.orderStatus.filled),
            )
            trade.log.append(SimpleNamespace(message="Simulated cancel"))
            return

    def open_trades(self) -> list[Any]:
        return [
            trade
            for trade in self._client._broker_trades
            if trade.orderStatus.status not in {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
        ]

    def trades(self) -> list[Any]:
        return list(self._client._broker_trades)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(min(seconds, 0.01))

    def _allocate_order_id(self) -> int:
        with self._client._order_lock:
            order_id = self._client._next_ibkr_order_id
            self._client._next_ibkr_order_id += 1
        return order_id

    def _qualify_contract(self, contract: Contract) -> Contract:
        contract.secType = getattr(contract, "secType", None) or "STK"
        contract.currency = getattr(contract, "currency", None) or "USD"
        contract.exchange = getattr(contract, "exchange", None) or "SMART"
        if not getattr(contract, "conId", None):
            contract.conId = self._stable_int(self._contract_key(contract))
        if contract.secType == "FUT" and not getattr(contract, "lastTradeDateOrContractMonth", None):
            contract.lastTradeDateOrContractMonth = datetime.now(timezone.utc).strftime(
                "%Y%m"
            )
        if contract.secType == "OPT":
            contract.exchange = getattr(contract, "exchange", None) or "SMART"
            contract.multiplier = getattr(contract, "multiplier", None) or "100"
            contract.tradingClass = getattr(contract, "tradingClass", None) or contract.symbol
            expiry = getattr(contract, "lastTradeDateOrContractMonth", "") or ""
            right = getattr(contract, "right", "") or ""
            strike = getattr(contract, "strike", "") or ""
            contract.localSymbol = (
                getattr(contract, "localSymbol", None)
                or f"{contract.symbol} {expiry} {right}{strike}"
            )
        return contract

    def _build_option_ticker(self, contract: Contract) -> Any:
        underlying_quote = self._client.get_quote(contract.symbol)
        underlying_price = underlying_quote.last or underlying_quote.mid
        strike = float(getattr(contract, "strike", 0.0) or 0.0)
        right = str(getattr(contract, "right", "C") or "C").upper()
        intrinsic = max(0.0, underlying_price - strike) if right == "C" else max(0.0, strike - underlying_price)
        mid = max(0.25, intrinsic + underlying_price * 0.01)
        bid = round(max(0.01, mid - 0.05), 2)
        ask = round(mid + 0.05, 2)
        last = round(mid, 2)
        delta = 0.55 if right == "C" else -0.45
        greeks = SimpleNamespace(
            impliedVol=0.25,
            delta=delta,
            optPrice=last,
            pvDividend=0.0,
            gamma=0.03,
            vega=0.12,
            theta=-0.05,
            undPrice=underlying_price,
        )
        return SimpleNamespace(
            contract=contract,
            bid=bid,
            ask=ask,
            last=last,
            bidSize=10,
            askSize=10,
            lastSize=1,
            volume=100,
            close=last,
            modelGreeks=greeks,
            bidGreeks=greeks,
            askGreeks=greeks,
            lastGreeks=greeks,
            impliedVolatility=0.25,
            histVolatility=0.2,
            rtHistVolatility=0.22,
        )

    def _initial_order_status(
        self,
        order: Any,
        ticker: Any,
        side: str,
        quantity: float,
    ) -> tuple[str, float, float]:
        order_type = str(getattr(order, "orderType", "MKT")).upper()
        bid = float(getattr(ticker, "bid", 0.0) or 0.0)
        ask = float(getattr(ticker, "ask", 0.0) or 0.0)
        last = float(getattr(ticker, "last", 0.0) or 0.0)

        if order_type in {"MKT", "MOC", "OPG"}:
            fill_price = ask if side == "BUY" and ask > 0 else bid if bid > 0 else last
            return "Filled", quantity, float(fill_price or 0.0)

        if order_type == "LMT":
            limit_price = float(getattr(order, "lmtPrice", 0.0) or 0.0)
            can_fill = (side == "BUY" and ask > 0 and limit_price >= ask) or (
                side == "SELL" and bid > 0 and limit_price <= bid
            )
            fill_price = ask if side == "BUY" else bid
            if can_fill:
                return "Filled", quantity, float(fill_price or limit_price or 0.0)

        return "Submitted", 0.0, 0.0

    def _bar_delta(self, bar_size_setting: str) -> timedelta:
        normalized = bar_size_setting.lower()
        amount = int(normalized.split()[0])
        if "sec" in normalized:
            return timedelta(seconds=amount)
        if "hour" in normalized:
            return timedelta(hours=amount)
        if "day" in normalized:
            return timedelta(days=amount)
        if "week" in normalized:
            return timedelta(weeks=amount)
        if "month" in normalized:
            return timedelta(days=30 * amount)
        return timedelta(minutes=amount)

    def _bar_count(self, duration_str: str, step: timedelta) -> int:
        normalized = duration_str.lower()
        amount = int(normalized.split()[0])
        if "week" in normalized or normalized.endswith(" w"):
            total = timedelta(weeks=amount)
        elif "month" in normalized or normalized.endswith(" m"):
            total = timedelta(days=30 * amount)
        elif "year" in normalized or normalized.endswith(" y"):
            total = timedelta(days=365 * amount)
        elif "day" in normalized or normalized.endswith(" d"):
            total = timedelta(days=amount)
        elif "hour" in normalized or normalized.endswith(" h"):
            total = timedelta(hours=amount)
        elif "sec" in normalized or normalized.endswith(" s"):
            total = timedelta(seconds=amount)
        else:
            total = timedelta(minutes=amount)
        return max(5, min(60, int(total / step) if step.total_seconds() > 0 else 20))

    def _contract_key(self, contract: Contract) -> str:
        return ":".join(
            [
                str(getattr(contract, "symbol", "") or ""),
                str(getattr(contract, "secType", "") or ""),
                str(getattr(contract, "exchange", "") or ""),
                str(getattr(contract, "currency", "") or ""),
                str(getattr(contract, "lastTradeDateOrContractMonth", "") or ""),
                str(getattr(contract, "strike", "") or ""),
                str(getattr(contract, "right", "") or ""),
                str(getattr(contract, "multiplier", "") or ""),
            ]
        )

    def _stable_int(self, value: str) -> int:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)


# Factory function to get appropriate client
def get_ibkr_client(
    mode: Optional[str] = None,
    client_id: Optional[int] = None,
) -> IBKRClient | SimulatedIBKRClient:
    """
    Get an IBKR client based on mode.

    Args:
        mode: Trading mode ('paper', 'live', or 'simulation').
              If None, reads from IBKR_MODE env var, defaulting to config.

    Returns:
        IBKRClient for paper/live modes, SimulatedIBKRClient for simulation.
    """
    import os

    if mode is None:
        mode = os.environ.get("IBKR_MODE", "").lower()

    if not mode:
        # Fall back to config trading_mode
        config = get_config()
        mode = config.trading_mode

    if mode == "simulation":
        return SimulatedIBKRClient(client_id=client_id)
    else:
        return IBKRClient(mode=mode, client_id=client_id)
