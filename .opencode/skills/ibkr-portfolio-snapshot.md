---
name: ibkr-portfolio-snapshot
description: Collect current account balances, positions, and portfolio risk in one call. Use when starting a trading session, before evaluating any order, or when the user asks about account exposure.
---

# IBKR Portfolio Snapshot

Use this skill at the start of any trading session or before evaluating a proposed order. It fetches the canonical read-only view of account health without touching execution.

## When to Use

- User asks about buying power, exposure, margin, or current positions
- Starting a session to trade or monitor the portfolio
- Before calling `ibkr-evaluate-order`
- As a precursor to `ibkr_get_portfolio_risk`

## Workflow

1. Call `ibkr_get_account_summary` — get buying power, net liquidation, margin
2. Call `ibkr_get_positions` — get all open positions with avg price, market value, unrealized P&L
3. Call `ibkr_get_portfolio_risk` — get concentration by symbol, margin utilisation, risk level, and warnings

Return a formatted table with:
- Net liquidation, buying power, maintenance margin
- Top 3 concentrations by symbol
- Overall risk level
- Any margin or concentration warnings

## Guardrails

- This skill is **read-only**. Do not call execution or approval tools.
- Do not assume the account is in a specific mode (paper/live) — always show the trading status if relevant.
- If any call fails, return what was successfully retrieved plus the error.

## Output Contract

Returns a structured summary:
```
Account: <id>
Currency: <currency>

Balances:
  Net Liquidation: <value>
  Buying Power: <value>
  Maintenance Margin: <value>

Positions: <count> open positions
  <Symbol> | <qty> shares | <marketValue> | <unrealizedP&L>

Risk Overview:
  Margin Utilisation: <pct>%
  Largest Position: <symbol> (<pct>% of net liq)
  Risk Level: <low|medium|high|critical>
  Warnings: <list or "None">
```

## Resources

- `ibkr_get_account_summary`
- `ibkr_get_positions`
- `ibkr_get_portfolio_risk`
