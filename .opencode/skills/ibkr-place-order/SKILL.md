---
name: ibkr-place-order
description: Execute a pre-evaluated single-leg order via Telegram approval. Use after ibkr-evaluate-order confirms the order is within profile limits and the user has confirmed they want to submit. Handles trading mode changes, approval, placement, and verification.
---

# IBKR Place Order

Execute a pre-evaluated single-leg order through Telegram approval and broker placement.

## When to Use

- After `ibkr-evaluate-order` has been run and the user confirmed the order
- The order parameters are fully resolved and the user has explicitly approved submission
- Do NOT use this skill if the order has not been pre-validated — always run `ibkr-evaluate-order` first

## Workflow

### Phase 1 — Trading Mode Preparation

1. Call `ibkr_get_trading_status` — check `dryRun`, `ordersEnabled`, `tradingMode`, `blockReason`
2. If the user wants to change `tradingMode` (paper ↔ live):
   - Call `ibkr_request_environment_change` with `target_env` and `reason`
   - Wait for approval (blocking call)
   - Once approved, call `ibkr_execute_environment_change` with the `approval_id`
   - Re-check `ibkr_get_trading_status` to confirm new state
3. If `dryRun=true` and the user wants live execution:
   - Call `ibkr_admin_update_trading_control` with:
     - `expectedCurrentState` from the current trading status
     - `ordersEnabled: true` and `dryRun: false`
     - `reason` explaining why
4. If `ordersEnabled=false` or `blockReason` is set, stop here and report the block to the user

### Phase 2 — Approval

5. Call `ibkr_request_trade_approval` with:
   - `order`: the full OrderSpec (must include `clientOrderId` matching the evaluated order)
   - `preview`: the OrderPreview from `ibkr_preview_order`
   - `reason`: human-readable reason for the trade
6. If the response is `denied`, report to the user and stop
7. If the response is `expired`, inform the user and stop
8. If the response is `approved`, proceed immediately to placement

### Phase 3 — Placement

9. Call `ibkr_place_order` with the `order` (the `approval_id` auto-resolves via `clientOrderId` match — no need to pass it explicitly)
10. Call `ibkr_get_order_status` to verify the broker accepted the order
11. Report the final order status

## Guardrails

- **Prerequisite:** `ibkr-evaluate-order` must have been run first. Do not use this skill without pre-validation.
- Use the **same `clientOrderId`** that was used in the evaluation phase — this is what enables auto-resolution of the `approval_id`.
- If `dryRun=true` in `tradingMode=paper`, the order will not execute — confirm with the user that they want to override before proceeding.
- If the user denies the Telegram approval request, do not place the order.
- After placement, always verify with `ibkr_get_order_status` — do not assume the order succeeded without confirmation.

## Output Contract

After successful execution:
```
Order <clientOrderId>:
  Symbol: <symbol>
  Side: <side> | Qty: <qty> | Type: <orderType>
  Est. Price: <price> | Notional: <notional>
  Status: <status> | Filled: <filled>/<qty>
  Order ID: <orderId>
```

After blocked/rejected:
```
Order <clientOrderId>: <BLOCKED|REJECTED>
  Reason: <message>
  Control State: dryRun=<bool>, ordersEnabled=<bool>, blockReason=<reason>
```

## Resources

- `ibkr_get_trading_status`
- `ibkr_request_environment_change`
- `ibkr_execute_environment_change`
- `ibkr_admin_update_trading_control`
- `ibkr_request_trade_approval`
- `ibkr_place_order`
- `ibkr_get_order_status`
