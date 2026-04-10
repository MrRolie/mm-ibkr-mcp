import { tool } from "@opencode-ai/plugin";
import { z } from "zod";

/**
 * trade_calc — position-sizing calculator for single-leg orders.
 *
 * Computes:
 * - max quantity given a notional target and current price
 * - min quantity to sell that clears a target notional at current price
 * - shares needed to hit a target notional at current price (exact)
 * - whether a proposed quantity violates a minimum-remaining-position constraint
 * - concentration pct of a position relative to a portfolio base (e.g. netLiquidation)
 */
export default tool({
  description:
    "Position-sizing calculator for single-leg equity orders.\n\n**EXACT JSON PARAMETER NAMES — copy these into your tool call:**\n  currentPrice   (number, required) — current price per share\n  currentQty     (number, required) — current share count (integer)\n  proposedQty    (number, optional) — proposed shares to sell\n  minPositionFloor (number, optional) — floor: position value must stay ≥ this after trade\n  buyingPower    (number, optional) — available buying power\n  targetNotional (number, optional) — target sale proceeds in currency units\n  sellPctOfPosition (number, 0.0–1.0, optional) — fraction of position to sell\n  positionValue  (number, optional) — current market value of position\n\n**Actions:**\n  check_min_position  — Proposed sell qty vs floor. Returns safe qty if proposed would breach floor. Required: currentQty, currentPrice, minPositionFloor.\n  sell_qty_for_notional — Shares to sell to hit target notional. Required: currentQty, currentPrice, and (targetNotional OR sellPctOfPosition).\n  max_buy_qty — Max buyable whole shares. Required: currentPrice, buyingPower.\n\n**Example — check if selling 3 shares from 16-share position at $347 keeps value above $4500:**\n  action: check_min_position\n  currentQty: 16\n  currentPrice: 347.0\n  minPositionFloor: 4500\n  proposedQty: 3",
  args: {
    action: z
      .enum(["max_buy_qty", "sell_qty_for_notional", "check_min_position"])
      .describe("KEY: 'action' — Calculation to perform. Allowed values: 'max_buy_qty' | 'sell_qty_for_notional' | 'check_min_position'"),

    // ── shared ────────────────────────────────────────────────────────────────
    currentPrice: z
      .number()
      .positive()
      .describe("KEY: 'currentPrice' (number, required) — Current price per share"),

    // ── for max_buy_qty ───────────────────────────────────────────────────────
    buyingPower: z
      .number()
      .positive()
      .optional()
      .describe("KEY: 'buyingPower' (number, optional) — Available buying power. Required when action='max_buy_qty'"),

    // ── for sell_qty_for_notional & check_min_position ─────────────────────────
    currentQty: z
      .number()
      .int()
      .nonnegative()
      .optional()
      .describe("KEY: 'currentQty' (number, required) — Current share count as integer. Required when action='sell_qty_for_notional' or 'check_min_position'"),

    targetNotional: z
      .number()
      .nonnegative()
      .optional()
      .describe("KEY: 'targetNotional' (number, optional) — Target sale proceeds in currency units. Use when action='sell_qty_for_notional'"),

    // ── for check_min_position ─────────────────────────────────────────────────
    minPositionFloor: z
      .number()
      .nonnegative()
      .optional()
      .describe("KEY: 'minPositionFloor' (number, optional) — Hard floor: position value must stay ≥ this after the trade. Required when action='check_min_position'"),

    proposedQty: z
      .number()
      .int()
      .nonnegative()
      .optional()
      .describe("KEY: 'proposedQty' (number, optional) — Proposed number of shares to sell. Defaults to 20% of currentQty when omitted"),

    positionValue: z
      .number()
      .nonnegative()
      .optional()
      .describe("KEY: 'positionValue' (number, optional) — Current market value of position. Defaults to currentQty × currentPrice if not provided"),

    // ── for sell_qty_for_notional ─────────────────────────────────────────────
    sellPctOfPosition: z
      .number()
      .min(0)
      .max(1)
      .optional()
      .describe("KEY: 'sellPctOfPosition' (number 0.0–1.0, optional) — Fraction of currentQty to sell. Overrides targetNotional when provided"),
  },
  async execute(args) {
    const {
      action,
      currentPrice,
      buyingPower,
      currentQty,
      targetNotional,
      minPositionFloor,
      positionValue,
      sellPctOfPosition,
      proposedQty,
    } = args;

    switch (action) {
      case "max_buy_qty": {
        if (buyingPower === undefined) {
          return "Error: buyingPower is required for action=max_buy_qty";
        }
        if (currentPrice <= 0) {
          return "Error: currentPrice must be positive";
        }
        const maxQty = Math.floor(buyingPower / currentPrice);
        return JSON.stringify({
          action: "max_buy_qty",
          buyingPower,
          currentPrice,
          maxQty,
          estimatedNotional: maxQty * currentPrice,
        });
      }

      case "sell_qty_for_notional": {
        if (currentQty === undefined) {
          return "Error: currentQty is required for action=sell_qty_for_notional";
        }
        if (currentPrice <= 0) {
          return "Error: currentPrice must be positive";
        }

        let qtyToSell: number;

        if (sellPctOfPosition !== undefined) {
          // Fraction of current position — round down to whole shares
          qtyToSell = Math.floor(currentQty * sellPctOfPosition);
          const notional = qtyToSell * currentPrice;
          const remainingPositionQty = currentQty - qtyToSell;
          const remainingValue = remainingPositionQty * currentPrice;
          return JSON.stringify({
            action: "sell_qty_for_notional",
            method: "fraction_of_position",
            currentQty,
            sellPctOfPosition,
            qtyToSell,
            estimatedNotional: notional,
            remainingPositionQty,
            remainingValue,
            remainingValuePctOfOriginal: currentQty > 0 ? ((currentQty - qtyToSell) / currentQty) * 100 : 0,
          });
        }

        if (targetNotional !== undefined) {
          // Target notional amount
          const exactQty = targetNotional / currentPrice;
          qtyToSell = Math.floor(exactQty); // whole shares only
          const actualNotional = qtyToSell * currentPrice;
          const remainingPositionQty = currentQty - qtyToSell;
          const remainingValue = remainingPositionQty * currentPrice;
          return JSON.stringify({
            action: "sell_qty_for_notional",
            method: "target_notional",
            targetNotional,
            currentPrice,
            qtyToSell,
            estimatedNotional: actualNotional,
            remainingPositionQty,
            remainingValue,
            remainingValuePctOfOriginal: currentQty > 0 ? ((currentQty - qtyToSell) / currentQty) * 100 : 0,
          });
        }

        return "Error: provide either targetNotional or sellPctOfPosition";
      }

      case "check_min_position": {
        if (currentQty === undefined || currentPrice === undefined) {
          return "Error: currentQty and currentPrice are required for action=check_min_position";
        }
        if (minPositionFloor === undefined) {
          return "Error: minPositionFloor is required for action=check_min_position";
        }

        const currentValue = (positionValue ?? currentQty * currentPrice);
        const floorViolated = currentValue < minPositionFloor;

        // What qty would remain if we sold `proposedQty`?
        // If currentQty is positive (long), selling reduces it.
        const effectiveProposedQty = proposedQty ?? Math.floor(currentQty * 0.2); // default: 20% reduction
        const remainingQty = currentQty - effectiveProposedQty;
        const remainingValue = remainingQty * currentPrice;
        const wouldViolateFloor = remainingValue < minPositionFloor;

        if (wouldViolateFloor) {
          // Compute max sell qty that keeps us at or above the floor
          const maxSellQty = Math.max(0, Math.floor((currentValue - minPositionFloor) / currentPrice));
          const safeQtyToSell = Math.min(effectiveProposedQty, maxSellQty);
          const adjustedRemainingValue = currentValue - safeQtyToSell * currentPrice;
          return JSON.stringify({
            action: "check_min_position",
            currentQty,
            currentPrice,
            currentValue,
            minPositionFloor,
            proposedQty: effectiveProposedQty,
            floorViolated: currentValue < minPositionFloor,
            wouldViolateFloor,
            maxSellQtySafe: maxSellQty,
            adjustedQtyToSell: safeQtyToSell,
            adjustedRemainingValue: Math.max(0, adjustedRemainingValue),
            reason:
              safeQtyToSell < effectiveProposedQty
                ? `Selling ${effectiveProposedQty} shares would leave position value below floor ($${remainingValue.toFixed(2)} < $${minPositionFloor}). Adjusted to ${safeQtyToSell} shares.`
                : null,
          });
        }

        return JSON.stringify({
          action: "check_min_position",
          currentQty,
          currentPrice,
          currentValue,
          minPositionFloor,
          proposedQty: effectiveProposedQty,
          floorViolated: false,
          wouldViolateFloor: false,
          adjustedQtyToSell: effectiveProposedQty,
          adjustedRemainingValue: remainingValue,
        });
      }

      default:
        return `Error: unknown action "${action}"`;
    }
  },
});

export const concentration_pct = tool({
  description:
    "Compute portfolio concentration % before and after a proposed trade.\n\n**EXACT JSON PARAMETER NAMES:**\n  portfolioBase   (number, required) — Portfolio base (e.g. net liquidation in currency units)\n  positionValue   (number, required) — Current market value of the position\n  tradeNotional   (number, required) — Notional of the proposed trade (positive = buy, negative = sell)\n  currentQty      (number, optional) — Current share count (for sell trades)\n  currentPrice    (number, optional) — Current price per share (for sell trades)",
  args: {
    portfolioBase: z
      .number()
      .positive()
      .describe("KEY: 'portfolioBase' (number, required) — Portfolio base, e.g. net liquidation value in currency units"),
    positionValue: z
      .number()
      .nonnegative()
      .describe("KEY: 'positionValue' (number, required) — Current market value of the position in currency units"),
    tradeNotional: z
      .number()
      .describe("KEY: 'tradeNotional' (number, required) — Proposed trade notional; positive for buys, negative for sells"),
    currentQty: z
      .number()
      .int()
      .nonnegative()
      .optional()
      .describe("KEY: 'currentQty' (number, optional) — Current share count (for sell trades)"),
    currentPrice: z
      .number()
      .positive()
      .optional()
      .describe("KEY: 'currentPrice' (number, optional) — Current price per share (for sell trades)"),
  },
  async execute(args) {
    const { portfolioBase, positionValue, tradeNotional } = args;

    const concentrationBefore = (positionValue / portfolioBase) * 100;
    const newPositionValue = positionValue + tradeNotional;
    const concentrationAfter = (newPositionValue / portfolioBase) * 100;

    return JSON.stringify({
      portfolioBase,
      positionValue,
      tradeNotional,
      concentrationBeforePct: parseFloat(concentrationBefore.toFixed(4)),
      concentrationAfterPct: parseFloat(concentrationAfter.toFixed(4)),
      concentrationDeltaPct: parseFloat((concentrationAfter - concentrationBefore).toFixed(4)),
    });
  },
});
