"""
Account status retrieval for IBKR accounts.

Provides structured access to:
- Account summary (balances, margin, buying power)
- Portfolio positions
- P&L (realized and unrealized)

Supports multi-account environments with explicit account_id filtering.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ibkr_core.broker import get_broker_adapter
from ibkr_core.client import IBKRClient
from ibkr_core.models import AccountPnl, AccountSummary, PnlDetail, Position

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class AccountError(Exception):
    """Base exception for account-related errors."""

    pass


class AccountSummaryError(AccountError):
    """Raised when account summary retrieval fails."""

    pass


class AccountPositionsError(AccountError):
    """Raised when positions retrieval fails."""

    pass


class AccountPnlError(AccountError):
    """Raised when P&L retrieval fails."""

    pass


# =============================================================================
# Account Summary
# =============================================================================

# IBKR account summary tags we need
ACCOUNT_SUMMARY_TAGS = [
    "NetLiquidation",
    "TotalCashValue",
    "BuyingPower",
    "ExcessLiquidity",
    "MaintMarginReq",
    "InitMarginReq",
    "AvailableFunds",
]


def _get_default_account_id(client: IBKRClient) -> str:
    """
    Get the default account ID from the client.

    Args:
        client: Connected IBKRClient instance.

    Returns:
        First managed account ID.

    Raises:
        AccountError: If no accounts are available.
    """
    accounts = client.managed_accounts
    if not accounts:
        raise AccountError("No managed accounts available")
    return accounts[0]


def get_account_summary(
    client: IBKRClient,
    account_id: Optional[str] = None,
    timeout_s: float = 10.0,
) -> AccountSummary:
    """
    Get account summary including balances, margin, and buying power.

    Args:
        client: Connected IBKRClient instance.
        account_id: Optional account ID. If None, uses the default account.
        timeout_s: Maximum time to wait for data (default 10.0 seconds).

    Returns:
        AccountSummary with current account status.

    Raises:
        AccountSummaryError: If summary retrieval fails.
    """
    client.ensure_connected()
    broker = get_broker_adapter(client)

    # Determine account ID
    if account_id is None:
        account_id = _get_default_account_id(client)

    logger.info(f"Requesting account summary for {account_id}")

    previous_timeout = broker.get_request_timeout()
    if previous_timeout is not None:
        try:
            broker.set_request_timeout(max(1.0, min(float(previous_timeout), float(timeout_s))))
        except Exception:
            previous_timeout = None

    try:
        # accountSummary() loads and caches subscription data on first call.
        # Repeated reqAccountSummary() calls can leak subscriptions and trigger
        # IBKR error 322 (max account summary requests exceeded).
        summary_values = broker.account_summary(account_id)

        if not summary_values:
            raise AccountSummaryError(f"No account summary data received for account {account_id}")

        # Filter by account_id and build a dict of tag -> value
        values_dict: Dict[str, float] = {}
        currency = "USD"  # Default

        for av in summary_values:
            if av.account != account_id:
                continue

            # Store the value
            if av.tag in ACCOUNT_SUMMARY_TAGS:
                try:
                    values_dict[av.tag] = float(av.value)
                except (ValueError, TypeError):
                    logger.warning(f"Could not parse {av.tag}={av.value}")

            # Capture currency from any field that has it
            if av.currency and av.currency != "":
                currency = av.currency

        if not values_dict:
            raise AccountSummaryError(f"No account summary values found for account {account_id}")

        # Build AccountSummary model
        now = datetime.now(timezone.utc)

        summary = AccountSummary(
            accountId=account_id,
            currency=currency,
            netLiquidation=values_dict.get("NetLiquidation", 0.0),
            cash=values_dict.get("TotalCashValue", 0.0),
            buyingPower=values_dict.get("BuyingPower", 0.0),
            marginExcess=values_dict.get("ExcessLiquidity", 0.0),
            maintenanceMargin=values_dict.get("MaintMarginReq", 0.0),
            initialMargin=values_dict.get("InitMarginReq", 0.0),
            timestamp=now,
        )

        logger.info(
            f"Account summary retrieved for {account_id}: "
            f"NLV={summary.netLiquidation:.2f} {currency}"
        )

        return summary

    except AccountSummaryError:
        raise
    except TimeoutError as e:
        raise AccountSummaryError(
            f"Account summary request timed out for {account_id} after {timeout_s:.1f}s"
        ) from e
    except Exception as e:
        raise AccountSummaryError(f"Failed to get account summary for {account_id}: {e}") from e
    finally:
        if previous_timeout is not None:
            try:
                broker.set_request_timeout(previous_timeout)
            except Exception:
                pass


# =============================================================================
# Positions
# =============================================================================

# Map IBKR secType to our asset class
SEC_TYPE_TO_ASSET_CLASS = {
    "STK": "STK",
    "ETF": "ETF",
    "FUT": "FUT",
    "OPT": "OPT",
    "CASH": "FX",
    "CFD": "CFD",
    "IND": "IND",
    "BOND": "BOND",
    "FUND": "FUND",
    "CRYPTO": "CRYPTO",
}


def get_positions(
    client: IBKRClient,
    account_id: Optional[str] = None,
    timeout_s: float = 10.0,
) -> List[Position]:
    """
    Get all open positions for an account.

    Args:
        client: Connected IBKRClient instance.
        account_id: Optional account ID. If None, uses the default account.
        timeout_s: Maximum time to wait for data (default 10.0 seconds).

    Returns:
        List of Position objects for the account.
        Empty list if no positions exist.

    Raises:
        AccountPositionsError: If positions retrieval fails.
    """
    client.ensure_connected()
    broker = get_broker_adapter(client)

    # Determine account ID for filtering
    target_account = account_id
    if target_account is None:
        target_account = _get_default_account_id(client)

    logger.info(f"Requesting positions for {target_account}")

    try:
        # Request positions from IBKR
        broker.request_positions()

        # Allow time for data to arrive
        broker.sleep(min(timeout_s, 2.0))

        # Get all positions
        raw_positions = broker.positions()

        # Filter by account
        positions: List[Position] = []
        for pos in raw_positions:
            if pos.account != target_account:
                continue

            contract = pos.contract

            # Determine asset class
            asset_class = SEC_TYPE_TO_ASSET_CLASS.get(contract.secType, contract.secType)

            # Build symbol string
            symbol = contract.symbol
            if contract.secType == "FUT" and contract.lastTradeDateOrContractMonth:
                # Include expiry for futures
                expiry = contract.lastTradeDateOrContractMonth
                symbol = f"{contract.symbol}_{expiry}"

            # Get market values from portfolio items if available
            # The positions() call gives us basic info; for market values we need portfolio()
            portfolio_items = broker.portfolio(pos.account)

            # Find matching portfolio item for market values
            market_price = 0.0
            market_value = 0.0
            unrealized_pnl = 0.0
            realized_pnl = 0.0

            for item in portfolio_items:
                if item.contract.conId == contract.conId:
                    market_price = item.marketPrice if item.marketPrice else 0.0
                    market_value = item.marketValue if item.marketValue else 0.0
                    unrealized_pnl = item.unrealizedPNL if item.unrealizedPNL else 0.0
                    realized_pnl = item.realizedPNL if item.realizedPNL else 0.0
                    break

            position = Position(
                accountId=pos.account,
                symbol=symbol,
                conId=contract.conId,
                assetClass=asset_class,
                currency=contract.currency or "USD",
                quantity=pos.position,
                avgPrice=pos.avgCost / abs(pos.position) if pos.position != 0 else 0.0,
                marketPrice=market_price,
                marketValue=market_value,
                unrealizedPnl=unrealized_pnl,
                realizedPnl=realized_pnl,
            )
            positions.append(position)

        logger.info(f"Retrieved {len(positions)} positions for {target_account}")

        return positions

    except AccountPositionsError:
        raise
    except Exception as e:
        raise AccountPositionsError(f"Failed to get positions for {target_account}: {e}") from e
    finally:
        # Cancel positions subscription
        try:
            broker.cancel_positions()
        except Exception:
            pass


# =============================================================================
# P&L
# =============================================================================


def get_pnl(
    client: IBKRClient,
    account_id: Optional[str] = None,
    timeframe: Optional[str] = None,
    timeout_s: float = 10.0,
) -> AccountPnl:
    """
    Get account P&L summary with per-symbol breakdown.

    Note: The timeframe parameter is currently ignored. Only current
    realized/unrealized P&L is returned. Timeframe-based P&L will be
    implemented in Phase 8.

    Args:
        client: Connected IBKRClient instance.
        account_id: Optional account ID. If None, uses the default account.
        timeframe: Ignored for now. Reserved for future use.
        timeout_s: Maximum time to wait for data (default 10.0 seconds).

    Returns:
        AccountPnl with realized/unrealized P&L and per-symbol breakdown.

    Raises:
        AccountPnlError: If P&L retrieval fails.
    """
    client.ensure_connected()
    broker = get_broker_adapter(client)

    # Determine account ID
    target_account = account_id
    if target_account is None:
        target_account = _get_default_account_id(client)

    logger.info(f"Requesting P&L for {target_account}")

    if timeframe:
        logger.warning(
            f"Timeframe '{timeframe}' is not yet supported. " "Returning current P&L only."
        )

    try:
        # Request PnL from IBKR
        # reqPnL subscribes to P&L updates
        broker.request_pnl(target_account)

        # Allow time for data to arrive
        broker.sleep(min(timeout_s, 2.0))

        # Get account PnL
        pnl_data = broker.pnl(target_account)

        # Get per-symbol PnL from positions/portfolio
        positions = get_positions(client, target_account, timeout_s)

        # Build per-symbol breakdown
        by_symbol: Dict[str, PnlDetail] = {}
        total_realized = 0.0
        total_unrealized = 0.0

        for pos in positions:
            # Use base symbol (strip expiry for futures)
            base_symbol = pos.symbol.split("_")[0] if "_" in pos.symbol else pos.symbol

            if base_symbol not in by_symbol:
                by_symbol[base_symbol] = PnlDetail(
                    symbol=base_symbol,
                    conId=pos.conId,
                    currency=pos.currency,
                    realized=0.0,
                    unrealized=0.0,
                )

            # Accumulate P&L
            by_symbol[base_symbol].realized += pos.realizedPnl
            by_symbol[base_symbol].unrealized += pos.unrealizedPnl
            total_realized += pos.realizedPnl
            total_unrealized += pos.unrealizedPnl

        # Use IBKR's account-level PnL if available (more accurate)
        if pnl_data:
            if hasattr(pnl_data, "dailyPnL") and pnl_data.dailyPnL is not None:
                # dailyPnL includes both realized and unrealized
                pass
            if hasattr(pnl_data, "unrealizedPnL") and pnl_data.unrealizedPnL is not None:
                total_unrealized = pnl_data.unrealizedPnL
            if hasattr(pnl_data, "realizedPnL") and pnl_data.realizedPnL is not None:
                total_realized = pnl_data.realizedPnL

        # Get currency from account summary
        try:
            summary = get_account_summary(client, target_account, timeout_s)
            currency = summary.currency
        except AccountSummaryError:
            currency = "USD"

        now = datetime.now(timezone.utc)

        account_pnl = AccountPnl(
            accountId=target_account,
            currency=currency,
            timeframe="CURRENT",  # Only current supported for now
            realized=total_realized,
            unrealized=total_unrealized,
            bySymbol=by_symbol,
            timestamp=now,
        )

        logger.info(
            f"P&L retrieved for {target_account}: "
            f"realized={total_realized:.2f}, unrealized={total_unrealized:.2f}"
        )

        return account_pnl

    except AccountPnlError:
        raise
    except AccountPositionsError as e:
        raise AccountPnlError(f"Failed to get P&L (positions error): {e}") from e
    except Exception as e:
        raise AccountPnlError(f"Failed to get P&L for {target_account}: {e}") from e
    finally:
        # Cancel PnL subscription
        try:
            broker.cancel_pnl(target_account)
        except Exception:
            pass


# =============================================================================
# Convenience Functions
# =============================================================================


def get_account_status(
    client: IBKRClient,
    account_id: Optional[str] = None,
    timeout_s: float = 10.0,
) -> Dict:
    """
    Get complete account status including summary and positions.

    This is a convenience function that combines account summary and positions
    into a single call.

    Args:
        client: Connected IBKRClient instance.
        account_id: Optional account ID. If None, uses the default account.
        timeout_s: Maximum time to wait for data (default 10.0 seconds).

    Returns:
        Dict with 'summary' and 'positions' keys.

    Raises:
        AccountError: If retrieval fails.
    """
    summary = get_account_summary(client, account_id, timeout_s)
    positions = get_positions(client, account_id, timeout_s)

    return {
        "summary": summary,
        "positions": positions,
    }


def list_managed_accounts(client: IBKRClient) -> List[str]:
    """
    List all managed account IDs.

    Args:
        client: Connected IBKRClient instance.

    Returns:
        List of account ID strings.
    """
    client.ensure_connected()
    return client.managed_accounts
