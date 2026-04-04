# mm-ibkr-mcp

`mm-ibkr-mcp` is the canonical Interactive Brokers MCP repo for agent-driven account monitoring and trade execution.

It assumes the user already has IB Gateway or TWS running locally. This repo does not manage the broker process. Its job is to connect, inspect account state, preview orders, place trades, persist execution state, and gate submissions through Telegram when required.

The older `mm-ibkr-gateway` repo remains public for now and will later narrow into gateway deployment and maintenance tooling. This repo is the canonical monitoring and trading MCP surface.

## Scope

Included:

- account health, balances, P&L, positions, open orders
- market data, contract resolution, options chain and snapshot tools
- single-order preview and placement
- durable basket execution through persisted trade intents
- SQLite-backed audit, approvals, trade intents, execution state, and position snapshots
- Telegram approval flow for single orders and baskets
- compare-and-swap admin control over `control.json`

Not included as part of the canonical workflow:

- starting or stopping IB Gateway
- web UI or REST admin as a first-class interface
- schedulers, signal ingestion, or separate OMS daemons

## Safety model

Two layers control execution:

1. `control.json`
   - `orders_enabled`
   - `dry_run`
   - `block_reason`
2. `MCP_ORDER_APPROVAL_MODE`
   - `telegram`: order submission requires Telegram approval
   - `yolo`: no approval gate

Safe defaults are:

- `orders_enabled=false`
- `dry_run=true`
- `MCP_ORDER_APPROVAL_MODE=telegram`

## Configuration

Copy `.env.example` to `.env` and set at minimum:

```bash
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=1
MCP_ORDER_APPROVAL_MODE=telegram
```

If using Telegram approval mode, also set:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Optional:

```bash
IBKR_ACCOUNT_ID=...
MM_IBKR_DATA_DIR=/path/to/data
MCP_ENABLE_ADMIN_TOOLS=true
```

## Running

Install dependencies:

```bash
uv sync --group dev
```

Start the MCP server over stdio:

```bash
uv run ibkr-mcp
```

Run over HTTP:

```bash
export MCP_TRANSPORT=streamable-http
export MCP_HOST=127.0.0.1
export MCP_PORT=8001
export MCP_AUTH_TOKEN=change-me
uv run ibkr-mcp
```

## Canonical tools

Core monitoring and execution:

- `ibkr_health`
- `ibkr_get_trading_status`
- `ibkr_get_schedule_status`
- `ibkr_get_account_summary`
- `ibkr_get_positions`
- `ibkr_get_pnl`
- `ibkr_list_open_orders`
- `ibkr_get_order_status`
- `ibkr_preview_order`
- `ibkr_place_order`
- `ibkr_cancel_order`

Basket execution:

- `ibkr_preview_order_basket`
- `ibkr_create_trade_intent`
- `ibkr_request_trade_intent_approval`
- `ibkr_submit_trade_intent`
- `ibkr_get_trade_intent`
- `ibkr_list_trade_intents`
- `ibkr_reconcile_trade_intent`
- `ibkr_cancel_trade_intent`

Approval and safety:

- `ibkr_request_trade_approval`
- `ibkr_request_live_trading_unlock`
- `ibkr_check_approval_status`
- `ibkr_emergency_stop`

## Expected workflow

Single order:

1. `ibkr_get_trading_status`
2. `ibkr_resolve_contract`
3. `ibkr_preview_order`
4. `ibkr_assess_order_impact`
5. `ibkr_validate_against_profile`
6. If `MCP_ORDER_APPROVAL_MODE=telegram`, request approval
7. `ibkr_place_order`

Basket:

1. `ibkr_preview_order_basket`
2. `ibkr_create_trade_intent`
3. If `MCP_ORDER_APPROVAL_MODE=telegram`, request approval
4. `ibkr_submit_trade_intent`
5. `ibkr_reconcile_trade_intent`

## Persistence

The MCP server uses one SQLite database for:

- `audit_log`
- `order_history`
- `approvals`
- `trade_intent`
- `intent_order`
- `execution_state`
- `position_snapshot`

This gives the agent one durable source of truth for approvals, order submission, reconciliation, and audit.
