# AGENTS.md – mm-ibkr-mcp

## Purpose
Canonical Interactive Brokers MCP repo for agent-driven account monitoring,
order preview, trade execution, Telegram approvals, and durable trade-intent
tracking.

The repo assumes IB Gateway or TWS is already running on the user's machine.
It does not manage the broker process.

## Stack
- Python 3.11+
- `uv`
- MCP SDK
- `ib-insync`
- Pydantic v2
- SQLite

## Commands
| Task | Command |
|------|---------|
| Install | `uv sync --group dev` |
| Test (non-integration) | `uv run pytest -m "not integration" -q` |
| Test (all) | `uv run pytest` |
| Compile check | `uv run python -m compileall -q ibkr_core mcp_server trade_core tests` |
| MCP server | `uv run ibkr-mcp` |

## Safety Rules

**Real capital is at risk in this repo. It is imperative that the agent follows all skill directives exactly, especially the ibkr-place-order and ibkr-evaluate-order skills. Do not skip steps, skip questions, or short-circuit skill workflows for any reason.**

> **Critical confirmation rule:** When any skill — particularly ibkr-place-order Phase 1 — requires the agent to present state to the user and confirm before proceeding, the agent MUST use the `question` tool. Presenting a status summary in plain text and ending the turn is a policy violation. See the Confirmation Policy section below.

1. Safe defaults stay on: `orders_enabled=false`, `dry_run=true`.
2. Never bypass `control.json` safety controls. Use admin tools (requires `MCP_ENABLE_ADMIN_TOOLS=true` in `.env`) to programmatically adjust `orders_enabled` or `dry_run` if requested by user.
3. Treat `MCP_ORDER_APPROVAL_MODE=telegram` as the default launch mode.
4. Telegram approval requests (`ibkr_request_trade_approval`, `ibkr_request_trade_intent_approval`, `ibkr_request_environment_change`) block synchronously. Do not manually poll for their completion; simply wait for the tool to return the final approved, denied, or expired status.
5. Ensure `approval_id` is always passed from the approval tools to their corresponding execution tools.
6. Preserve UTC ISO 8601 timestamps for persisted audit and order history.
7. Keep order flows preview-first when adding or changing execution behavior.

## Code Conventions
- Prefer MCP-first changes; do not reintroduce gateway-management, REST, or UI surfaces.
- Keep the SQLite audit trail durable and append-oriented.
- Preserve correlation IDs through order and audit flows.
- Prefer small, explicit runtime config over mode-specific fallback layers.

## Key Areas
- `ibkr_core/orders.py` – execution and safety checks
- `ibkr_core/persistence.py` – audit and order-history persistence
- `mcp_server/main.py` – public MCP tool surface
- `trade_core/persistence.py` – trade-intent lifecycle

## Confirmation Policy

The `question` tool is the **only permitted mechanism** for soliciting user decisions. Use it for:

- Confirming or adjusting trading state, mode, or controls (as required by ibkr-place-order Phase 1)
- Presenting memory candidates for user selection
- Presenting skill drafts for approval
- Any yes/no, multiple-choice, or freeform confirmation

Do NOT use freeform text to request confirmations, choices, or approvals. Batch all questions into a single `question` call. Ending a turn with a confirmation request in plain text is a policy violation.

> **Why this matters here:** Real capital is at risk in this repo. Every trading-state confirmation must go through the structured `question` tool so the skill workflow gates are not bypassed. See: ibkr-place-order Phase 1, Step 2.

## Tool Usage Policy

Custom tools in `.opencode/tools/` are available in this repo and should be used for any calculation task where they apply. Before performing arithmetic or constraint reasoning manually, check whether a relevant custom tool exists:

- `trade_calc` — position sizing with floor compliance (`action: check_min_position`, `action: sell_qty_for_notional`, `action: max_buy_qty`)
- `trade_calc_concentration_pct` — concentration % before/after a trade

When a custom tool applies, call it. Do not substitute manual arithmetic or hallucinate constraint results. If the tool does not exist for a needed calculation, build it first before proceeding.
