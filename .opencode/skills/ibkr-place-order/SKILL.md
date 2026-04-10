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

### Phase 1 — Trading Mode Confirmation (always do this first)

1. Call `ibkr_get_trading_status` — record `tradingMode`, `dryRun`, `ordersEnabled`, `blockReason`
2. **Call `question` to confirm current state is the target:**
   - Show the full current state: `tradingMode`, `dryRun`, `ordersEnabled`, `blockReason`
   - Header: "Confirm trading status"
   - Options: "Yes, proceed as-is", "No, adjust settings"
3. **If user confirms "Yes, proceed as-is":** proceed to Phase 2 with current state
4. **If user chooses "No, adjust settings":** call `question` for each toggle that needs changing, one at a time:
   - For `tradingMode`: "Paper (simulation)" or "Live trading"
   - For `dryRun`: "Simulation only (dryRun=true)" or "Live execution (dryRun=false)"
   - For `ordersEnabled`: "Orders blocked" or "Orders enabled"
   - After each toggle is set, call the appropriate tool to apply the change (environment change or admin update), re-check status, then continue to the next toggle
   - Once all intended toggles are set, re-check `ibkr_get_trading_status` and confirm the final state before proceeding to Phase 2
5. **Never assume intent — always confirm current state or proposed changes via `question`.**

### Phase 2 — Approval

6. Call `ibkr_request_trade_approval` with:
   - `order`: the full OrderSpec (must include `clientOrderId` matching the evaluated order)
   - `preview`: the OrderPreview from `ibkr_preview_order`
   - `reason`: human-readable reason for the trade
7. If the response is `denied`, report to the user and stop
8. If the response is `expired`, inform the user and stop
9. If the response is `approved`, proceed immediately to placement

### Phase 3 — Placement

9. Call `ibkr_place_order` with the `order` (the `approval_id` auto-resolves via `clientOrderId` match — no need to pass it explicitly)
10. Call `ibkr_get_order_status` to verify the broker accepted the order
11. Report the final order status

## Guardrails

- **Prerequisite:** `ibkr-evaluate-order` must have been run first. Do not use this skill without pre-validation.
- Use the **same `clientOrderId`** that was used in the evaluation phase — this is what enables auto-resolution of the `approval_id`.
- `tradingMode=paper` with `dryRun=true` means the order is **always simulated** — confirm this is the user's intended mode in Phase 1 before proceeding.
- If the user denies the Telegram approval request, do not place the order.
- After placement, always verify with `ibkr_get_order_status` — do not assume the order succeeded without confirmation.
- **Mandatory `question` tool:** Do NOT use freeform text to request confirmations. Use the `question` tool exclusively for all Phase 1 confirmation flows. Ending a turn with a plain-text confirmation request is a policy violation.

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
