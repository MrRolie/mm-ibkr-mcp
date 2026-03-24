# IBKR Gateway

[![CI](https://github.com/MrRolie/mm-ibkr-gateway/workflows/CI/badge.svg)](https://github.com/MrRolie/mm-ibkr-gateway/actions)
[![codecov](https://codecov.io/gh/MrRolie/mm-ibkr-gateway/branch/main/graph/badge.svg)](https://codecov.io/gh/MrRolie/mm-ibkr-gateway)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A complete, modular integration of Interactive Brokers (IBKR) with multiple interfaces: direct Python, REST API, and Claude MCP integration.

Querying Claude for Available Tools
![Claude MCP Integration](images/claude_mcp_integrated.png)

## MCP Server

The MCP server is now a direct `ibkr_core` server instead of a REST proxy. It supports:

- `stdio` transport for local Claude Code / MCP desktop workflows
- `streamable-http` at `/mcp` for hosted or remote MCP clients
- bearer-token auth for HTTP mode
- canonical `ibkr_*` tool names with structured output schemas and annotations
- options v1 support for single-leg contract discovery and greeks snapshots
- Claude Code resources and prompts in addition to tools

### Launching MCP

Local stdio:

```bash
uv sync
uv run ibkr-mcp
```

Hosted Streamable HTTP:

```bash
export MCP_TRANSPORT=streamable-http
export MCP_HOST=127.0.0.1
export MCP_PORT=8001
export MCP_AUTH_TOKEN=change-me
export MCP_PUBLIC_BASE_URL=http://127.0.0.1:8001
uv run ibkr-mcp
```

Remote Claude Code over SSH stdio:

```bash
cd deploy/linux
sudo ./scripts/setup_mcp_ssh_user.sh \
  --user ibkr-mcp \
  --public-key-file /path/to/remote-machine.pub
```

Then point the remote MCP client at:

```json
{
  "mcpServers": {
    "ibkr-gateway": {
      "command": "ssh",
      "args": ["-T", "ibkr-mcp@YOUR_IB_HOST"]
    }
  }
}
```

This keeps IB Gateway local on the host and uses SSH stdio as the only remote control plane.

If you connect across Tailscale, disable Tailscale SSH on the IB host with `sudo tailscale set --ssh=false` so the connection reaches normal `sshd` and the forced-command `ibkr-mcp` account.

Primary canonical tools:

- `ibkr_health`
- `ibkr_get_trading_status`
- `ibkr_get_schedule_status`
- `ibkr_resolve_contract`
- `ibkr_get_quote`
- `ibkr_get_historical_bars`
- `ibkr_get_account_summary`
- `ibkr_get_positions`
- `ibkr_get_pnl`
- `ibkr_list_open_orders`
- `ibkr_get_order_status`
- `ibkr_get_order_set_status`
- `ibkr_preview_order`
- `ibkr_place_order`
- `ibkr_cancel_order`
- `ibkr_cancel_order_set`
- `ibkr_get_option_chain`
- `ibkr_get_option_snapshot`

Admin tools are disabled by default and appear only when `MCP_ENABLE_ADMIN_TOOLS=true`.
Legacy non-namespaced aliases are also opt-in via `MCP_ENABLE_LEGACY_ALIASES=true`.

Claude Code resources:

- `ibkr://status/overview`
- `ibkr://account/default/summary`
- `ibkr://account/default/positions`
- `ibkr://orders/open`
- `ibkr://options/chain/{symbol}`

Claude Code prompts:

- `pre_trade_checklist`
- `option_contract_selection`
- `order_review`

## ⚠️ SAFETY FIRST ⚠️

This system is **SAFE BY DEFAULT**:

✅ **Paper trading mode**: System defaults to paper trading (`trading_mode=paper` in `control.json`)
✅ **Orders disabled**: Order placement is disabled by default (`orders_enabled=false` in `control.json`)
✅ **Dual-toggle protection**: Live trading requires `trading_mode=live`, `orders_enabled=true`, and an override file in `control.json`
✅ **Preview before place**: MCP tools require explicit confirmation for all orders

### Default Behavior

- **Market data**: ✅ Available (read-only)
- **Account status**: ✅ Available (read-only)
- **Order preview**: ✅ Available (simulation)
- **Order placement**: ❌ Disabled (returns SIMULATED status)

### How to Enable Live Trading (⚠️ USE WITH EXTREME CAUTION)

We **strongly recommend** staying in paper mode. If you must enable live trading:

1. Create an override file: `New-Item -Path "C:\ProgramData\mm-ibkr-gateway\live_override.txt" -ItemType File`
2. Update `control.json` via the operator UI or /admin/control:
   ```powershell
   # Example API call from the same machine
   curl -X PUT http://localhost:8000/admin/control `
     -H "X-Admin-Token: YOUR_TOKEN" `
     -H "Content-Type: application/json" `
     -d '{"reason":"Enable live trading","trading_mode":"live","orders_enabled":true,"dry_run":false,"live_trading_override_file":"C:\\ProgramData\\mm-ibkr-gateway\\live_override.txt"}'
   ```

**WARNING**: Once enabled, the system can place REAL orders with REAL money.

### Testing Live Trading Setup

Test with SMALL orders first. Use limit orders FAR from market price to avoid fills.
Monitor your IBKR account portal during testing.

---

## Quick Demo (5 Minutes)

Want to see the system in action? Run our interactive demo to explore market data and account features.

### Prerequisites

- IBKR account with paper trading enabled
- IBKR Gateway or TWS running in paper mode (port 4002)
- Market data subscriptions (paper accounts have free delayed data)

### Run the Demo

```bash
# 1. (Optional) copy secrets template
cp .env.example .env

# 2. Ensure control.json is in paper mode (default)

# 3. Install dependencies
uv sync

# 4. Run the demo
python -m ibkr_core.demo
# or
ibkr-demo
```

### What the Demo Shows

The demo showcases three core capabilities:

1. **Market Data**
   - Real-time quote for AAPL (stock)
   - Historical bars for SPY (ETF) - last 5 days

2. **Account Status**
   - Account summary (balance, buying power, P&L)
   - Current positions (if any)

### Docker Demo

You can also run the demo in Docker:

```bash
docker-compose -f docker-compose.demo.yml up
```

**Note**: The Docker container connects to IBKR Gateway running on your host machine via `host.docker.internal`.

### Production Deployment

For a production deployment with IB Gateway managed by Docker (using IBC for auto-login and 2FA handling):

```bash
# See deploy/linux/README.md for full setup
cd deploy/linux
cp .env.example .env
# Edit .env with your credentials
./scripts/start.sh
```

Features:
- IBC auto-login with 2FA retry
- Daily auto-restart without re-authentication
- VNC access for debugging
- Two isolated IB Gateway sessions in Docker (live + paper)
- No Linux HTTP service required for the deployment path
- Optional SSH-first MCP access for Claude Code and other stdio MCP clients

See [deploy/linux/README.md](deploy/linux/README.md) for details.

### Troubleshooting

| Issue | Solution |
|-------|----------|
| "IBKR Gateway not detected" | Ensure TWS/Gateway is running on port 4002 |
| "Demo requires paper trading mode" | Ensure `control.json` has `trading_mode=paper` (default) |
| "Market data permission error" | Paper accounts have free delayed data; check IBKR subscriptions |
| Connection timeout | Verify IBKR Gateway API settings are enabled (Configure → API → Settings) |

### Expected Output

The demo displays:
- ✓ Connection status with server time
- ✓ AAPL quote with bid/ask/last prices
- ✓ SPY historical bars (last 5 entries)
- ✓ Account summary with buying power
- ✓ Current positions (if any)
- ✓ Next steps and documentation links

---

## Command-Line Interface

The `ibkr-gateway` command provides a user-friendly CLI for common operations.

### Available Commands

```bash
ibkr-gateway --help              # Show all commands
ibkr-gateway healthcheck         # Check IBKR Gateway connection
ibkr-gateway demo                # Run interactive demo
ibkr-gateway start-api           # Start REST API server
ibkr-gateway version             # Show version info
```

### Health Check

Check the connection to IBKR Gateway:

```bash
# Basic health check
ibkr-gateway healthcheck

# Check with paper mode (force)
ibkr-gateway --paper healthcheck

# Check with custom host/port
ibkr-gateway --host localhost --port 4002 healthcheck
```

**Output**: Connection status, server time, managed accounts

### Demo Mode

Run the interactive 5-minute demo:

```bash
# Run demo (paper mode required)
ibkr-gateway demo

# Demo with explicit paper mode
ibkr-gateway --paper demo
```

The demo automatically runs in paper mode for safety, even if you specify `--live`.

### Start API Server

Launch the FastAPI REST server:

```bash
# Start with defaults (port 8000)
ibkr-gateway start-api

# Custom port
ibkr-gateway start-api --port 8080

# Development mode with auto-reload
ibkr-gateway start-api --reload

# Custom host binding
ibkr-gateway start-api --host 0.0.0.0 --port 8000
```

**Endpoints available at**:
- API Documentation: http://localhost:8000/docs
- Health Check: http://localhost:8000/health
- OpenAPI Schema: http://localhost:8000/openapi.json

### Operator UI (VS Code)

The operator dashboard UI is served by the API at `http://localhost:8000/ui`.

Recommended VS Code extensions:
- ms-python.python
- ms-python.vscode-pylance
- ms-vscode.live-preview (optional, opens the UI in an editor tab)

Open the UI:
1. Start the API server.
2. In VS Code, run "Live Preview: Show" and navigate to `http://localhost:8000/ui`
   (or open the URL in your browser).

Set `ADMIN_TOKEN` before starting the API so admin actions work:

```powershell
$env:ADMIN_TOKEN="your-admin-token"
python -m api.server
```

You can also set `ADMIN_TOKEN` in `.env` at the repo root.

### Global Options

Apply to any command:

- `--host <host>`: Override IBKR Gateway host
- `--port <port>`: Override IBKR Gateway port
- `--paper`: Force paper trading mode
- `--live`: Force live trading mode (⚠️ **DANGEROUS**)
- `--help`: Show help for any command

### Examples

```bash
# Check paper trading connection
ibkr-gateway --paper healthcheck

# Run demo in paper mode
ibkr-gateway demo

# Start API server on custom port with auto-reload
ibkr-gateway start-api --port 8080 --reload

# Force live mode connection check (⚠️ use with caution)
ibkr-gateway --live healthcheck
```

---

## Project Status

| Phase | Description | Status |
| ------- | ------------- | -------- |
| Phase 0 | Repo structure, environment, safety rails | ✅ Complete |
| Phase 1 | IBKR client wrapper, contract resolution | ✅ Complete |
| Phase 2 | Market data (quotes, historical bars) | ✅ Complete |
| Phase 3 | Account status, positions, P&L | ✅ Complete |
| Phase 4 | Order placement and management | ✅ Complete |
| Phase 5 | FastAPI REST layer | ✅ Complete |
| Phase 6 | MCP server for Claude integration | ✅ Complete |
| Phase 7 | Natural language agent layer | ⏭️ Skipped |
| Phase 8 | Monitoring, simulation, persistence | ✅ Complete |

## Three Ways to Use

### 1. Direct Python (Manual Use)

Use the `ibkr_core` module directly in Python scripts or notebooks:

```python
from ibkr_core.client import IBKRClient
from ibkr_core.models import SymbolSpec
from ibkr_core.market_data import get_quote, get_historical_bars
from ibkr_core.account import get_account_summary, get_positions
from ibkr_core.orders import place_order, preview_order

# Connect to IBKR Gateway
client = IBKRClient(mode="paper")
client.connect()

# Get a quote
spec = SymbolSpec(symbol="AAPL", securityType="STK", exchange="SMART", currency="USD")
quote = get_quote(spec, client)
print(f"AAPL: ${quote.last} (bid: {quote.bid}, ask: {quote.ask})")

# Get account info
summary = get_account_summary(client)
print(f"Net Liquidation: ${summary.netLiquidation:,.2f}")

client.disconnect()
```

### 2. REST API

Run the FastAPI server for HTTP access:

```bash
# Start the API server
python -m api.server

# Or with uvicorn directly
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

**API Endpoints:**

| Method | Endpoint | Description |
| -------- | --------- | ------------- |
| GET | `/health` | Health check and connection status |
| GET | `/metrics` | Application metrics (counters, histograms, gauges) |
| POST | `/market-data/quote` | Get market quote |
| POST | `/market-data/historical` | Get historical bars |
| GET | `/account/summary` | Get account summary |
| GET | `/account/positions` | Get positions |
| GET | `/account/pnl` | Get P&L |
| POST | `/orders/preview` | Preview order |
| POST | `/orders` | Place order |
| GET | `/orders/{id}/status` | Get order status |
| POST | `/orders/{id}/cancel` | Cancel order |

**Example API calls:**

```bash
# Get a quote
curl -X POST http://localhost:8000/market-data/quote \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL", "securityType": "STK"}'

# Get account summary
curl http://localhost:8000/account/summary

# Preview an order
curl -X POST http://localhost:8000/orders/preview \
  -H "Content-Type: application/json" \
  -d '{
    "instrument": {"symbol": "AAPL", "securityType": "STK"},
    "side": "BUY",
    "quantity": 10,
    "orderType": "LMT",
    "limitPrice": 150.00
  }'
```

### 3. Claude MCP Integration

Use with Claude Desktop or any MCP-compatible client for natural language interaction with your brokerage.

**Setup:**

1. Start the REST API server (must be running):

   ```bash
   python -m api.server
   ```

2. Add to Claude Desktop config (`claude_desktop_config.json`):

   ```json
   {
     "mcpServers": {
       "ibkr-gateway": {
         "command": "python",
         "args": ["-m", "mcp_server.main"],
         "cwd": "C:\\path\\to\\mm-ibkr-gateway",
         "env": {
           "IBKR_API_URL": "http://localhost:8000"
         }
       }
     }
   }
   ```

3. Restart Claude Desktop

**Available MCP Tools:**

| Tool | Description |
| ------ | ------------- |
| `get_quote` | Get real-time bid/ask/last price, sizes, volume |
| `get_historical_data` | Get OHLCV bars (1m to 1mo intervals) |
| `get_account_status` | Get summary + all positions |
| `get_pnl` | Get P&L breakdown by timeframe and symbol |
| `preview_order` | Preview execution details before placing |
| `place_order` | Execute orders (market, limit, stop, trailing, bracket) |
| `get_order_status` | Check order status (pending, filled, cancelled) |
| `cancel_order` | Cancel open orders |

**Supported Order Types:**

- MKT, LMT, STP, STP_LMT
- TRAIL, TRAIL_LIMIT (with dollar amount or percentage)
- BRACKET (entry + take profit + stop loss)
- MOC (market-on-close), OPG (opening)

**Securities Covered:**

- STK (stocks), ETF, FUT (futures), OPT (options), IND (indices)

---

## Quick Start

### Prerequisites

- Python 3.10+
- Running IBKR Gateway or TWS (Paper account recommended)
- Poetry or pip for dependency management

### Installation

```bash
# Clone and enter directory
cd mm-ibkr-gateway

# Install dependencies
poetry install
# or
pip install -e .
```

**Note on IBKR Gateway**: The system uses [IBAutomater.jar](https://github.com/QuantConnect/IBAutomater) from QuantConnect to handle automated login and UI automation for IBKR Gateway. This is included in Windows deployments for production use.

### Configuration

1. Create runtime config (`config.json`). For Windows deployments, run `deploy/windows/configure.ps1` or create
   `C:\ProgramData\mm-ibkr-gateway\config.json` (override with `MM_IBKR_CONFIG_PATH`).

   Example (trimmed):

   ```json
   {
     "api_bind_host": "127.0.0.1",
     "api_port": 8000,
     "allowed_ips": "127.0.0.1",
     "ibkr_gateway_host": "127.0.0.1",
     "paper_gateway_port": 4002,
     "live_gateway_port": 4001,
     "data_storage_dir": "C:\\\\ProgramData\\\\mm-ibkr-gateway\\\\storage",
     "ibkr_gateway_path": "C:\\\\Jts\\\\ibgateway\\\\1042"
   }
   ```

2. Trading controls live in `C:\ProgramData\mm-ibkr-gateway\control.json` (use the operator UI or `PUT /admin/control`).

3. (Optional) create `.env` for secrets:

   ```ini
   API_KEY=
   ADMIN_TOKEN=
   ```

4. Verify connection:

   ```bash
   python -m ibkr_core.healthcheck
   ```

---

## Windows Deployment (Production)

**Status**: Work in progress - full production deployment guide available

For deploying the system as a Windows-based trading execution node with automated startup, health checks, and scheduled operation:

🔗 **See [deploy/windows/README.md](deploy/windows/README.md)** for complete setup instructions.

### Quick Overview

The Windows deployment provides:

- **Automated IBKR Gateway startup** with [IBAutomater.jar](https://github.com/QuantConnect/IBAutomater) for auto-login
- **FastAPI REST server** bound to LAN for remote clients (e.g., Raspberry Pi)
- **Task Scheduler integration** for time-windowed operation (market hours only)
- **Watchdog automation** for health checks and automatic restart
- **Google Drive sync** for audit logs and configuration persistence
- **Firewall restrictions** to IP-whitelist remote clients
- **Safety enforced** at system level (paper mode, orders disabled by default)

### Key Features

| Feature | Details |
|---------|----------|
| **IBAutomater** | Handles IBKR Gateway login automation and UI control via Java agent |
| **Time Window** | Services run only during weekday market hours (configurable) |
| **Health Checks** | Automatic service restart with exponential backoff |
| **Audit Trail** | SQLite database synced to Google Drive for compliance |
| **API Protection** | Firewall + API key authentication |
| **Boot Recovery** | Automatic reconciliation on system startup |

---

## Architecture

```text
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Claude Desktop │     │   Your Code     │     │   curl / HTTP   │
│   (MCP Client)  │     │   (Python)      │     │   Client        │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │ stdio                 │ import                │ HTTP
         ▼                       ▼                       ▼
┌─────────────────┐     ┌─────────────────────────────────────────┐
│   MCP Server    │───> │              REST API (FastAPI)         │
│   (Phase 6)     │HTTP │              (Phase 5)                  │
└─────────────────┘     └────────────────────┬────────────────────┘
                                             │
                                             │ function calls
                                             ▼
                        ┌─────────────────────────────────────────┐
                        │            ibkr_core                    │
                        │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
                        │  │ market   │ │ account  │ │ orders   │ │
                        │  │ _data.py │ │ .py      │ │ .py      │ │
                        │  └──────────┘ └──────────┘ └──────────┘ │
                        │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
                        │  │ client   │ │contracts │ │ models   │ │
                        │  │ .py      │ │ .py      │ │ .py      │ │
                        │  └──────────┘ └──────────┘ └──────────┘ │
                        └────────────────────┬────────────────────┘
                                             │
                                             │ ib_insync
                                             ▼
                        ┌─────────────────────────────────────────┐
                        │         IBKR Gateway / TWS              │
                        │         (Paper or Live)                 │
                        │                                         │
                        │  On Windows with IBAutomater:           │
                        │  - Automated login                      │
                        │  - UI automation & restart handling     │
                        │  - Task Scheduler integration           │
                        └─────────────────────────────────────────┘
```

---

## Monitoring & Observability

### Structured Logging

All logs include correlation IDs for request tracing:

```json
{"correlation_id": "abc-123", "message": "Order placed", "symbol": "AAPL", "status": "success"}
```

Configure via environment:
```bash
LOG_LEVEL=DEBUG    # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT=text    # json (default) or text for development
```

### Metrics Endpoint

Access application metrics at `GET /metrics`:

```bash
curl http://localhost:8000/metrics | jq
```

Returns counters, histograms (with p50/p90/p95/p99), and gauges for:
- API request counts and latencies by endpoint
- IBKR operation metrics (connect, disconnect, errors)
- Connection status gauges

### Audit Database

All order events are persisted to SQLite for compliance and debugging:

```bash
# Query recent audit events
python scripts/query_audit_log.py --limit 20 -v

# Export order history to CSV
python scripts/export_order_history.py --output orders.csv

# Filter by account
python scripts/query_audit_log.py --account DU12345
```

### Simulation Mode

Test without IBKR Gateway using the simulated client:

```bash
# Enable simulation mode
export IBKR_MODE=simulation

# All operations use synthetic data
python -m ibkr_core.cli quote AAPL  # Returns simulated quote
```

Useful for: unit tests, CI/CD pipelines, development without market data subscriptions.

---

## Project Structure

```text
mm-ibkr-gateway/
├── ibkr_core/              # Core IBKR integration
│   ├── client.py           # Connection wrapper
│   ├── config.py           # Environment & safety config
│   ├── contracts.py        # Contract resolution
│   ├── market_data.py      # Quotes & historical data
│   ├── account.py          # Account status & positions
│   ├── orders.py           # Order placement
│   ├── models.py           # Pydantic models
│   ├── persistence.py      # SQLite audit database (Phase 8)
│   ├── metrics.py          # In-memory metrics (Phase 8)
│   ├── simulation.py       # Simulated client (Phase 8)
│   └── logging_config.py   # Structured logging (Phase 8)
│
├── api/                    # FastAPI REST layer
│   ├── server.py           # Endpoint definitions
│   ├── models.py           # HTTP request/response models
│   ├── middleware.py       # Correlation ID middleware
│   ├── auth.py             # API key authentication
│   ├── dependencies.py     # FastAPI dependencies
│   └── errors.py           # Error handling
│
├── mcp_server/             # MCP server for Claude
│   ├── main.py             # MCP tools
│   ├── config.py           # MCP configuration
│   ├── http_client.py      # Async HTTP client
│   └── errors.py           # Error translation
│
├── scripts/                # Utility scripts
│   ├── query_audit_log.py  # Query audit database
│   └── export_order_history.py  # Export to CSV/JSON
│
├── tests/                  # 500+ tests
│   ├── test_simulation_*.py    # Simulation tests
│   ├── test_metrics_*.py       # Metrics tests
│   ├── test_persistence_*.py   # Audit DB tests
│   └── test_*.py               # Core module tests
│
├── .context/               # Design documentation
├── .agent_context/         # Agent onboarding docs
└── .env.example            # Configuration template
```

---

## Development & Testing

### Running Tests Locally

```bash
# Unit tests only (fast, no IBKR connection needed)
poetry run pytest -m "not integration"

# All tests (requires IBKR Gateway running)
poetry run pytest

# With coverage
poetry run pytest -m "not integration" --cov

# Specific test file
poetry run pytest tests/test_cli.py -v
```

### Code Quality

```bash
# Format code
poetry run black .
poetry run isort .

# Lint
poetry run flake8

# Type check
poetry run mypy ibkr_core api mcp_server

# Security scan
poetry run safety check
poetry run bandit -r ibkr_core api mcp_server
```

### Pre-commit Hooks (Optional)

Set up pre-commit hooks to automatically format and lint code:

```bash
poetry run pre-commit install
```

### Continuous Integration

All pull requests are automatically tested with:
- ✅ Code formatting (black, isort)
- ✅ Linting (flake8)
- ✅ Type checking (mypy)
- ✅ Unit tests on Python 3.10, 3.11, 3.12
- ✅ Coverage reporting (codecov)
- ✅ Security scanning (safety, bandit)

Integration tests run manually due to IBKR connection requirements.

---

## Runtime Configuration

Operational settings load from `config.json` (ProgramData on Windows, override with `MM_IBKR_CONFIG_PATH`).
Trading controls load from `control.json` (mm-ibkr-gateway).

### Environment Variables (Secrets and Tooling)

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `API_KEY` | (none) | API key for REST authentication |
| `ADMIN_TOKEN` | (none) | Admin token for `/admin/*` endpoints |
| `MM_IBKR_CONFIG_PATH` | (none) | Override path to `config.json` |
| `IBKR_API_URL` | `http://localhost:8000` | REST API URL (for MCP server) |
| `MCP_REQUEST_TIMEOUT` | `60` | MCP request timeout in seconds |
| `IBKR_MODE` | (none) | Override to `simulation` for testing without gateway |

---

## Key Design Decisions

1. **Schema-Driven**: All data models match `.context/SCHEMAS.md` exactly
2. **Pydantic Models**: Strong typing with IDE support and runtime validation
3. **Thin MCP Layer**: MCP tools are thin HTTP wrappers around the REST API
4. **Dedicated IBKR Thread**: Single-threaded executor for ib_insync event loop safety
5. **Error Preservation**: Error codes flow from IBKR → Core → API → MCP consistently

---

## Documentation

- [Phase Plan](.context/PHASE_PLAN.md) - Implementation roadmap
- [Schemas](.context/SCHEMAS.md) - JSON Schema definitions (source of truth)
- [Architecture Notes](.context/ARCH_NOTES.md) - Design decisions
- [TODO Backlog](.context/TODO_BACKLOG.md) - Task tracking and technical debt
- [API Documentation](api/API.md) - REST API reference
