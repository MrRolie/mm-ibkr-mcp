# API Quick Reference

## REST API Endpoints

Base URL: `http://localhost:8000`

### Health & Metrics

```bash
# Health check
curl http://localhost:8000/health

# Application metrics
curl http://localhost:8000/metrics
```

### Market Data

```bash
# Get quote
curl -X POST http://localhost:8000/market-data/quote \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL", "securityType": "STK", "exchange": "SMART", "currency": "USD"}'

# Get historical bars
curl -X POST http://localhost:8000/market-data/historical \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "AAPL",
    "securityType": "STK",
    "exchange": "SMART",
    "currency": "USD",
    "barSize": "1 hour",
    "duration": "1 D"
  }'
```

### Account

```bash
# Account summary
curl http://localhost:8000/account/summary

# Positions
curl http://localhost:8000/account/positions

# P&L
curl http://localhost:8000/account/pnl
```

### Orders

```bash
# Preview order
curl -X POST http://localhost:8000/orders/preview \
  -H "Content-Type: application/json" \
  -d '{
    "instrument": {"symbol": "AAPL", "securityType": "STK", "exchange": "SMART", "currency": "USD"},
    "side": "BUY",
    "quantity": 10,
    "orderType": "LMT",
    "limitPrice": 150.00
  }'

# Place order
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "instrument": {"symbol": "AAPL", "securityType": "STK", "exchange": "SMART", "currency": "USD"},
    "side": "BUY",
    "quantity": 10,
    "orderType": "MKT"
  }'

# Cancel order
curl -X POST http://localhost:8000/orders/{order_id}/cancel

# Get order status
curl http://localhost:8000/orders/{order_id}/status

# List open orders
curl http://localhost:8000/orders/open
```

## CLI Commands

```bash
# Quotes
uv run python -m ibkr_core.cli quote AAPL
uv run python -m ibkr_core.cli quote AAPL --mode paper

# Historical data
uv run python -m ibkr_core.cli bars AAPL --duration "5 D" --bar-size "1 hour"

# Account
uv run python -m ibkr_core.cli account
uv run python -m ibkr_core.cli positions
uv run python -m ibkr_core.cli pnl

# Orders
uv run python -m ibkr_core.cli preview BUY 10 AAPL --order-type LMT --limit-price 150
uv run python -m ibkr_core.cli order BUY 10 AAPL --order-type MKT
uv run python -m ibkr_core.cli cancel <order_id>
uv run python -m ibkr_core.cli status <order_id>
```

## MCP Tools

Primary MCP transport choices:

- `stdio` for local or SSH-forced-command workflows
- `streamable-http` on `/mcp` for hosted clients
- bearer token required via `MCP_AUTH_TOKEN` only for HTTP mode

Recommended single-user remote pattern:

```bash
ssh -T ibkr-mcp@YOUR_IB_HOST
```

That SSH account should be restricted to `deploy/linux/scripts/run_mcp_stdio.sh`.

Canonical tool surface:

| Tool | Description |
| ------ | ------------- |
| `ibkr_health` | Gateway connectivity and runtime health |
| `ibkr_get_trading_status` | Trading-control state from `control.json` |
| `ibkr_get_schedule_status` | Current run-window status |
| `ibkr_resolve_contract` | Resolve a `SymbolSpec` into a qualified IBKR contract |
| `ibkr_get_quote` | Snapshot quote for a fully specified instrument |
| `ibkr_get_historical_bars` | Historical OHLCV bars |
| `ibkr_get_account_summary` | Account balances, buying power, margin |
| `ibkr_get_positions` | Open positions |
| `ibkr_get_pnl` | Account P&L with per-symbol breakdown |
| `ibkr_list_open_orders` | Open orders on the active connection |
| `ibkr_get_order_status` | Status for a single order id |
| `ibkr_get_order_set_status` | Aggregate status for related order ids |
| `ibkr_preview_order` | Order preview without placement |
| `ibkr_place_order` | Place an order; `clientOrderId` is required |
| `ibkr_cancel_order` | Cancel a single order |
| `ibkr_cancel_order_set` | Cancel a related set of orders |
| `ibkr_get_option_chain` | Discover single-leg option contracts |
| `ibkr_get_option_snapshot` | Quote + IV + greeks for a fully specified option |

Optional admin tools, disabled by default:

| Tool | Description |
| ------ | ------------- |
| `ibkr_admin_verify_gateway` | Verify gateway access by fetching account summary |
| `ibkr_admin_update_trading_control` | Compare-and-swap update for `control.json` |

Enable admin tools with `MCP_ENABLE_ADMIN_TOOLS=true`.
Enable legacy aliases with `MCP_ENABLE_LEGACY_ALIASES=true`.

## Claude Code Resources And Prompts

Resources:

- `ibkr://status/overview`
- `ibkr://account/default/summary`
- `ibkr://account/default/positions`
- `ibkr://orders/open`
- `ibkr://options/chain/{symbol}`

Prompts:

- `pre_trade_checklist`
- `option_contract_selection`
- `order_review`

These are useful in Claude Code. Anthropic's remote MCP connector currently relies on tool calls as the primary integration contract.

## Request/Response Models

### SymbolSpec

```json
{
  "symbol": "AAPL",
  "securityType": "STK",       // STK, OPT, FUT, CASH, etc.
  "exchange": "SMART",
  "currency": "USD",
  "expiry": null,              // For options/futures
  "strike": null,              // For options
  "right": null,               // "C" or "P" for options
  "multiplier": null
}
```

### OrderSpec

```json
{
  "instrument": { /* SymbolSpec */ },
  "side": "BUY",               // BUY or SELL
  "quantity": 100,
  "orderType": "LMT",          // MKT, LMT, STP, STP_LMT, TRAIL, etc.
  "limitPrice": 150.00,        // Required for LMT
  "stopPrice": null,           // For STP orders
  "accountId": null,           // Optional, uses default
  "clientOrderId": "retry-key-123"
}
```

### Quote Response

```json
{
  "symbol": "AAPL",
  "bid": 149.95,
  "ask": 150.05,
  "last": 150.00,
  "bidSize": 100,
  "askSize": 200,
  "lastSize": 50,
  "volume": 1000000,
  "timestamp": "2025-01-15T10:30:00"
}
```

### OrderResult

```json
{
  "orderId": "uuid-here",
  "clientOrderId": "retry-key-123",
  "status": "ACCEPTED",        // ACCEPTED, REJECTED, SIMULATED
  "orderStatus": {
    "status": "SUBMITTED",
    "filledQuantity": 0,
    "remainingQuantity": 100,
    "avgFillPrice": 0.0
  }
}
```

## Error Responses

```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "Invalid order type: INVALID",
  "details": { ... }
}
```

Error codes:

- `VALIDATION_ERROR` - Invalid request parameters
- `IBKR_ERROR` - IBKR Gateway error
- `TIMEOUT` - Operation timed out
- `NOT_FOUND` - Resource not found
- `PERMISSION_DENIED` - Orders disabled or auth failed
