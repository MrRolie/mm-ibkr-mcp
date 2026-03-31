# Architecture

## System Components

```text
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   REST API      │     │   MCP Server    │     │      CLI        │
│  (FastAPI)      │     │(LLM Integration)│     │  (Click/Typer)  │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      ibkr_core          │
                    │  (Gateway Policy +      │
                    │   Business Logic)       │
                    │  - orders.py            │
                    │  - market_data.py       │
                    │  - account.py           │
                    └────────────┬────────────┘
                                  │
              ┌──────────────────┼──────────────────────┐
              │                  │                      │
    ┌─────────▼─────────┐ ┌──────▼──────┐ ┌─────────────▼─────────────┐
    │   Broker Adapter  │ │ Persistence │ │          Metrics           │
    │  (current backend │ │  (SQLite)   │ │        (In-memory)         │
    │   = ib_insync)    │ └─────────────┘ └───────────────────────────┘
    └─────────┬─────────┘
              │
    ┌─────────▼─────────┐
    │  IBKR Gateway/TWS │
    │   (External)      │
    └───────────────────┘
```

## Core Modules

### `ibkr_core/client.py` - IBKRClient

Connection wrapper that owns the active broker adapter providing:

- Dual mode support (paper/live)
- Connection lifecycle management
- Structured logging with correlation IDs
- Metrics recording
- Backend seam for future `ib_async` support

```python
from ibkr_core.client import IBKRClient

with IBKRClient(mode="paper") as client:
    # client.broker is the backend adapter
    accounts = client.managed_accounts
```

### `ibkr_core/broker.py` - Broker Adapter

Backend seam for broker-library integration:

- `IBInsyncBrokerAdapter` is the current implementation
- core modules now use the adapter for account, contracts, market data, and order flows
- streaming quotes also use the adapter surface; core business logic no longer needs direct `client.ib`
- a future `ib_async` backend can implement the same adapter surface
- actual `IBAsyncBrokerAdapter` implementation is intentionally deferred to the next milestone
- remaining raw `ib_insync` touchpoints are limited to backend ownership and compatibility layers such as `client.py`, `healthcheck.py`, and simulation internals

### `ibkr_core/models.py` - Domain Models

Pydantic models for all domain objects:

- `SymbolSpec` - Instrument specification (symbol, secType, exchange, currency)
- `OrderSpec` - Order parameters (instrument, side, quantity, orderType, limitPrice, etc.)
- `OrderResult` - Order placement result
- `OrderPreview` - Preview with estimated execution details
- `Quote` - Market quote (bid, ask, last, volume)
- `HistoricalBar` - OHLCV bar data

### `ibkr_core/orders.py` - Order Operations

```python
from ibkr_core.orders import preview_order, place_order, cancel_order

# Preview first
preview = preview_order(client, order_spec)

# Place if satisfied
result = place_order(client, order_spec)

# Cancel if needed
cancel_order(client, result.orderId)
```

### `ibkr_core/market_data.py` - Market Data

```python
from ibkr_core.market_data import get_quote, get_historical_bars

quote = get_quote(symbol_spec, client)
bars = get_historical_bars(symbol_spec, client, bar_size="1 hour", duration="1 D")
```

### `ibkr_core/persistence.py` - Audit Database

SQLite storage for:

- **audit_log**: All events with correlation IDs
- **order_history**: Complete order lifecycle

```python
from ibkr_core.persistence import record_audit_event, query_audit_log

record_audit_event("ORDER_SUBMIT", {"symbol": "AAPL", ...}, correlation_id="...")
events = query_audit_log(account_id="DU12345", limit=100)
```

### `ibkr_core/metrics.py` - Metrics Collection

In-memory metrics with:

- **Counters**: api_requests_total, ibkr_operations_total
- **Histograms**: api_request_duration_seconds (with p50, p90, p95, p99)
- **Gauges**: ibkr_connection_status, active_orders

```python
from ibkr_core.metrics import record_api_request, get_metrics

record_api_request("/health", "GET", 200, 0.05)
all_metrics = get_metrics().get_all_metrics()
```

### `ibkr_core/simulation.py` - Simulated Client

For testing without IBKR Gateway:

```python
from ibkr_core.simulation import SimulatedIBKRClient, get_ibkr_client

# Direct usage
client = SimulatedIBKRClient()

# Or via factory with IBKR_MODE=simulation
client = get_ibkr_client(mode="simulation")
```

## API Endpoints

| Method | Endpoint | Description |
| -------- | ---------- | ------------- |
| GET | `/health` | Health check with connection status |
| GET | `/metrics` | Application metrics (JSON) |
| POST | `/market-data/quote` | Get quote for symbol |
| POST | `/market-data/historical` | Get historical bars |
| GET | `/account/summary` | Account balances and margin |
| GET | `/account/positions` | Open positions |
| GET | `/account/pnl` | Profit/loss |
| POST | `/orders/preview` | Preview order without placing |
| POST | `/orders` | Place order |
| POST | `/orders/{id}/cancel` | Cancel order |
| GET | `/orders/{id}/status` | Get order status |
| GET | `/orders/open` | List open orders |

## Data Flow Example: Place Order

```text
1. API Request → POST /orders
   └─ CorrelationIdMiddleware assigns UUID

2. FastAPI validates request body → OrderRequest model

3. execute_ibkr_operation() runs in thread pool:
   └─ place_order(client, order_spec)
       ├─ preview_order() first (market data validation)
       ├─ Create IBKR contract and order objects
       ├─ broker.place_order(...)
       ├─ record_audit_event("ORDER_SUBMIT", ...)
       └─ save_order(...) to SQLite

4. Return OrderResult with orderId, status

5. Middleware records metrics:
   └─ record_api_request("/orders", "POST", 201, duration)
```

## Thread Safety

- **IBKRClient**: Single-threaded (ib_insync requirement)
- **API Server**: Async with thread pool executor for blocking IBKR calls
- **Metrics**: Thread-safe counters/gauges/histograms
- **Persistence**: SQLite with connection per operation
- **SimulatedIBKRClient**: Thread-safe order registry
