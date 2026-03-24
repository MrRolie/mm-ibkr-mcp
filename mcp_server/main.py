"""Modern MCP server for direct IBKR trading and market data access."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Callable, Optional, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ibkr_core.account import (
    AccountError,
    get_account_summary,
    get_pnl,
    get_positions,
)
from ibkr_core.client import ConnectionError as IBKRConnectionError
from ibkr_core.config import InvalidConfigError, get_config, reset_config
from ibkr_core.control import (
    ControlState,
    get_control_path,
    get_control_status,
    load_control,
    validate_control,
    write_audit_entry,
    write_control,
)
from ibkr_core.contracts import (
    AmbiguousContractError,
    ContractNotFoundError,
    ContractResolutionError,
    contract_to_resolved_contract,
    resolve_contract,
)
from ibkr_core.market_data import (
    MarketDataError,
    MarketDataPermissionError,
    MarketDataTimeoutError,
    NoMarketDataError,
    PacingViolationError,
    get_historical_bars,
    get_option_chain,
    get_option_snapshot,
    get_quote,
)
from ibkr_core.models import (
    AccountPnl,
    AccountSummary,
    CancelResult,
    OptionChainResponse,
    OptionSnapshotResponse,
    OrderPreview,
    OrderResult,
    OrderSpec,
    OrderStatus,
    Quote,
    ResolvedContract,
    SymbolSpec,
)
from ibkr_core.orders import (
    OrderCancelError,
    OrderError,
    OrderNotFoundError,
    OrderPlacementError,
    OrderPreviewError,
    OrderValidationError,
    cancel_order,
    cancel_order_set,
    get_open_orders,
    get_order_set_status,
    get_order_status,
    place_order,
    preview_order,
)
from ibkr_core.schedule import get_window_status
from mcp_server.config import MCPConfig, get_mcp_config
from mcp_server.errors import MCPToolError
from mcp_server.models import (
    AccountStatusResponse,
    GatewayVerificationResponse,
    HealthResponse,
    HistoricalBarsResponse,
    OpenOrderInfo,
    OpenOrdersResponse,
    OrderSetStatusResponse,
    PositionsResponse,
    ScheduleStatusResponse,
    TradingControlExpectation,
    TradingControlUpdateRequest,
    TradingControlUpdateResponse,
    TradingStatusResponse,
)
from mcp_server.security import StaticBearerTokenVerifier
from mcp_server.services import IBKRMCPService

logger = logging.getLogger(__name__)

LIVE_TRADING_ENABLE_CONFIRMATION = "ENABLE LIVE TRADING AND REAL ORDER PLACEMENT"

T = TypeVar("T")

READ_TOOL = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
PREVIEW_TOOL = ToolAnnotations(readOnlyHint=True)
WRITE_TOOL = ToolAnnotations(destructiveHint=True)
IDEMPOTENT_WRITE_TOOL = ToolAnnotations(destructiveHint=True, idempotentHint=True)


def _tool_error(exc: Exception) -> MCPToolError:
    """Map core exceptions to MCP-friendly errors."""
    if isinstance(exc, MCPToolError):
        return exc
    if isinstance(exc, (ValueError, OrderValidationError)):
        return MCPToolError("VALIDATION_ERROR", str(exc))
    if isinstance(exc, ContractNotFoundError):
        return MCPToolError("NOT_FOUND", str(exc))
    if isinstance(exc, AmbiguousContractError):
        return MCPToolError("AMBIGUOUS_CONTRACT", str(exc))
    if isinstance(exc, OrderNotFoundError):
        return MCPToolError("ORDER_NOT_FOUND", str(exc))
    if isinstance(exc, MarketDataPermissionError):
        return MCPToolError("MARKET_DATA_PERMISSION_DENIED", str(exc))
    if isinstance(exc, NoMarketDataError):
        return MCPToolError("NO_MARKET_DATA", str(exc))
    if isinstance(exc, (MarketDataTimeoutError, asyncio.TimeoutError)):  # type: ignore[name-defined]
        return MCPToolError("TIMEOUT", str(exc))
    if isinstance(exc, PacingViolationError):
        return MCPToolError("RATE_LIMITED", str(exc))
    if isinstance(exc, IBKRConnectionError):
        return MCPToolError("IBKR_CONNECTION_ERROR", str(exc))
    if isinstance(exc, InvalidConfigError):
        return MCPToolError("INVALID_CONFIG", str(exc))
    if isinstance(exc, (OrderPlacementError, OrderCancelError, OrderPreviewError, OrderError)):
        return MCPToolError("ORDER_ERROR", str(exc))
    if isinstance(exc, (AccountError, ContractResolutionError, MarketDataError)):
        return MCPToolError(type(exc).__name__.upper(), str(exc))
    return MCPToolError("INTERNAL_ERROR", str(exc))


def _status_from_control_dict(payload: dict[str, Any]) -> TradingStatusResponse:
    """Convert control status dicts to the typed response model."""
    return TradingStatusResponse(
        tradingMode=payload["trading_mode"],
        ordersEnabled=payload["orders_enabled"],
        dryRun=payload["dry_run"],
        effectiveDryRun=payload["effective_dry_run"],
        liveTradingOverrideFile=payload.get("live_trading_override_file"),
        overrideFileExists=payload.get("override_file_exists"),
        overrideFileMessage=payload.get("override_file_message"),
        isLiveTradingEnabled=payload["is_live_trading_enabled"],
        validationErrors=payload.get("validation_errors", []),
        controlPath=payload["control_path"],
    )


def _schedule_from_dict(payload: dict[str, Any]) -> ScheduleStatusResponse:
    """Convert schedule dicts to the typed response model."""
    return ScheduleStatusResponse(
        currentTime=payload["current_time"],
        timezone=payload["timezone"],
        inWindow=payload["in_window"],
        windowStart=payload["window_start"],
        windowEnd=payload["window_end"],
        activeDays=payload.get("active_days", []),
        nextWindowStart=payload.get("next_window_start"),
        nextWindowEnd=payload.get("next_window_end"),
    )


def _open_orders_from_list(payload: list[dict[str, Any]]) -> OpenOrdersResponse:
    """Convert raw open-order payloads to typed models."""
    orders = [
        OpenOrderInfo(
            orderId=order["order_id"],
            clientOrderId=order.get("client_order_id"),
            symbol=order["symbol"],
            side=order["side"],
            quantity=float(order["quantity"]),
            orderType=order["order_type"],
            status=order["status"],
            filledQuantity=float(order.get("filled", 0.0)),
            remainingQuantity=float(order.get("remaining", 0.0)),
        )
        for order in payload
    ]
    return OpenOrdersResponse(count=len(orders), orders=orders)


def _order_set_response(
    requested_order_ids: list[str],
    found_orders: list[OrderStatus],
) -> OrderSetStatusResponse:
    """Build aggregated order-set status output."""
    found_ids = {order.orderId for order in found_orders}
    missing_ids = [order_id for order_id in requested_order_ids if order_id not in found_ids]
    return OrderSetStatusResponse(
        requestedOrderIds=requested_order_ids,
        foundCount=len(found_orders),
        missingOrderIds=missing_ids,
        foundOrders=found_orders,
    )


def _current_trading_status() -> TradingStatusResponse:
    """Read and normalize the current trading-control state."""
    return _status_from_control_dict(get_control_status())


def _status_from_control_state(state: ControlState) -> TradingStatusResponse:
    """Build TradingStatusResponse from a ControlState instance."""
    validation_errors = validate_control(state)
    override_exists, override_message = state.validate_override_file()
    return TradingStatusResponse(
        tradingMode=state.trading_mode,
        ordersEnabled=state.orders_enabled,
        dryRun=state.dry_run,
        effectiveDryRun=state.effective_dry_run(),
        liveTradingOverrideFile=state.live_trading_override_file,
        overrideFileExists=override_exists if state.is_live_trading_enabled() else None,
        overrideFileMessage=override_message or None,
        isLiveTradingEnabled=state.is_live_trading_enabled(),
        validationErrors=validation_errors,
        controlPath=str(get_control_path()),
    )


def _json_payload(value: Any) -> str:
    """Serialize Pydantic models or plain values as formatted JSON."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    return json.dumps(value, indent=2, default=str)


def _normalize_control_expectation(state: ControlState) -> TradingControlExpectation:
    """Convert ControlState into the compare-and-swap expectation model."""
    return TradingControlExpectation(
        tradingMode=state.trading_mode,
        ordersEnabled=state.orders_enabled,
        dryRun=state.dry_run,
        liveTradingOverrideFile=state.live_trading_override_file,
    )


def _ensure_fully_qualified_option(spec: SymbolSpec) -> None:
    """Require a fully specified option contract for actionable tools."""
    if spec.securityType != "OPT":
        return
    if not spec.expiry or spec.strike is None or not spec.right:
        raise ValueError(
            "Option contracts must include expiry, strike, and right for quote/order tools"
        )


def create_mcp_server(config: Optional[MCPConfig] = None) -> FastMCP:
    """Create a fully configured FastMCP server instance."""
    config = config or get_mcp_config()
    service = IBKRMCPService(
        request_timeout=config.request_timeout,
        connect_timeout=config.connect_timeout,
    )

    token_verifier = None
    auth_settings = None
    transport_security = None
    if config.is_http_transport:
        token_verifier = StaticBearerTokenVerifier(config.auth_token or "")
        auth_settings = config.build_auth_settings()
        transport_security = config.build_transport_security()

    @asynccontextmanager
    async def app_lifespan(server: FastMCP):
        logger.info(
            "Starting IBKR MCP server transport=%s host=%s port=%s path=%s",
            config.transport,
            config.host,
            config.port,
            config.streamable_http_path,
        )
        try:
            yield {
                "transport": config.transport,
                "public_base_url": config.public_base_url,
            }
        finally:
            await service.shutdown()
            logger.info("IBKR MCP server shutdown complete")

    mcp = FastMCP(
        name="mm-ibkr-gateway",
        instructions=(
            "IBKR trading tools with safety rails. Workflow: "
            "1) resolve the contract before trading, "
            "2) inspect ibkr_get_trading_status and ibkr_get_schedule_status, "
            "3) preview every order before placing it, "
            "4) always provide a unique clientOrderId to ibkr_place_order, "
            "5) never enable live trading implicitly, "
            "6) treat option trades as fully qualified single-leg contracts only, "
            "7) use ibkr_get_option_chain to discover options and ibkr_get_option_snapshot "
            "to inspect greeks before placing options orders."
        ),
        debug=False,
        log_level=config.log_level,
        host=config.host,
        port=config.port,
        sse_path=config.sse_path,
        message_path=config.message_path,
        streamable_http_path=config.streamable_http_path,
        json_response=config.json_response,
        stateless_http=config.stateless_http,
        lifespan=app_lifespan,
        token_verifier=token_verifier,
        auth=auth_settings,
        transport_security=transport_security,
    )

    async def call_core(
        operation: Callable[[Any], T],
        *,
        timeout_s: Optional[float] = None,
    ) -> T:
        try:
            return await service.run_with_client(operation, timeout_s=timeout_s)
        except Exception as exc:  # pragma: no cover - mapped by unit tests
            raise _tool_error(exc) from exc

    async def get_health_model() -> HealthResponse:
        trading_status = _current_trading_status()
        gateway_host = None
        gateway_port = None
        try:
            gateway_config = get_config()
            gateway_host = gateway_config.ibkr_gateway_host
            gateway_port = gateway_config.ibkr_gateway_port
        except Exception as exc:
            logger.info("Gateway runtime config unavailable during health check: %s", exc)

        ibkr_connected = False
        server_time = None
        managed_accounts: list[str] = []
        try:
            client = await service.get_client()
            ibkr_connected = client.is_connected
            managed_accounts = client.managed_accounts
            server_time_dt = await call_core(
                lambda current_client: current_client.get_server_time(timeout_s=2.0),
                timeout_s=4.0,
            )
            server_time = server_time_dt.isoformat()
        except Exception as exc:
            logger.info("Health check returning degraded state: %s", exc)

        return HealthResponse(
            status="ok" if ibkr_connected else "degraded",
            ibkrConnected=ibkr_connected,
            serverTime=server_time,
            tradingMode=trading_status.tradingMode,
            ordersEnabled=trading_status.ordersEnabled,
            gatewayHost=gateway_host,
            gatewayPort=gateway_port,
            managedAccounts=managed_accounts,
        )

    async def get_trading_status_model() -> TradingStatusResponse:
        return _current_trading_status()

    async def get_schedule_status_model() -> ScheduleStatusResponse:
        return _schedule_from_dict(get_window_status())

    async def get_account_status_model(account_id: Optional[str] = None) -> AccountStatusResponse:
        def operation(client):
            summary = get_account_summary(client, account_id=account_id)
            positions = get_positions(client, account_id=account_id)
            return summary, positions

        summary, positions = await call_core(operation)
        return AccountStatusResponse(
            summary=summary,
            positions=positions,
            positionCount=len(positions),
        )

    async def get_positions_model(account_id: Optional[str] = None) -> PositionsResponse:
        def operation(client):
            positions = get_positions(client, account_id=account_id)
            if account_id:
                resolved_account_id = account_id
            elif positions:
                resolved_account_id = positions[0].accountId
            else:
                resolved_account_id = get_account_summary(client, account_id=None).accountId
            return resolved_account_id, positions

        resolved_account_id, positions = await call_core(operation)
        return PositionsResponse(
            accountId=resolved_account_id,
            positionCount=len(positions),
            positions=positions,
        )

    @mcp.tool(
        name="ibkr_health",
        title="IBKR Health",
        description="Check gateway connectivity and basic runtime health.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_health() -> HealthResponse:
        return await get_health_model()

    @mcp.tool(
        name="ibkr_get_trading_status",
        title="Trading Status",
        description="Inspect trading-control state from control.json.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_trading_status() -> TradingStatusResponse:
        return await get_trading_status_model()

    @mcp.tool(
        name="ibkr_get_schedule_status",
        title="Schedule Status",
        description="Inspect the configured trading schedule window.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_schedule_status() -> ScheduleStatusResponse:
        return await get_schedule_status_model()

    @mcp.tool(
        name="ibkr_resolve_contract",
        title="Resolve Contract",
        description="Resolve a SymbolSpec into a fully qualified IBKR contract.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_resolve_contract(instrument: SymbolSpec) -> ResolvedContract:
        _ensure_fully_qualified_option(instrument)
        return await call_core(
            lambda client: contract_to_resolved_contract(resolve_contract(instrument, client))
        )

    @mcp.tool(
        name="ibkr_get_quote",
        title="Get Quote",
        description="Get a market-data snapshot for a fully specified instrument.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_quote(instrument: SymbolSpec) -> Quote:
        _ensure_fully_qualified_option(instrument)
        return await call_core(lambda client: get_quote(instrument, client))

    @mcp.tool(
        name="ibkr_get_historical_bars",
        title="Get Historical Bars",
        description="Get historical OHLCV bars for a fully specified instrument.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_historical_bars(
        instrument: SymbolSpec,
        bar_size: str,
        duration: str,
        what_to_show: str = "TRADES",
        rth_only: bool = True,
    ) -> HistoricalBarsResponse:
        _ensure_fully_qualified_option(instrument)
        bars = await call_core(
            lambda client: get_historical_bars(
                instrument,
                client,
                bar_size=bar_size,
                duration=duration,
                what_to_show=what_to_show,
                rth_only=rth_only,
            )
        )
        return HistoricalBarsResponse(symbol=instrument.symbol, barCount=len(bars), bars=bars)

    @mcp.tool(
        name="ibkr_get_account_summary",
        title="Get Account Summary",
        description="Get balances, buying power, and margin metrics for an account.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_account_summary(account_id: Optional[str] = None) -> AccountSummary:
        return await call_core(lambda client: get_account_summary(client, account_id=account_id))

    @mcp.tool(
        name="ibkr_get_positions",
        title="Get Positions",
        description="List open positions for an account.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_positions(account_id: Optional[str] = None) -> PositionsResponse:
        return await get_positions_model(account_id=account_id)

    @mcp.tool(
        name="ibkr_get_pnl",
        title="Get PnL",
        description="Get account P&L with per-symbol breakdown.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_pnl(
        account_id: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> AccountPnl:
        return await call_core(
            lambda client: get_pnl(client, account_id=account_id, timeframe=timeframe)
        )

    @mcp.tool(
        name="ibkr_list_open_orders",
        title="List Open Orders",
        description="List currently open orders on the active IBKR connection.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_list_open_orders() -> OpenOrdersResponse:
        payload = await call_core(lambda client: get_open_orders(client))
        return _open_orders_from_list(payload)

    @mcp.tool(
        name="ibkr_get_order_status",
        title="Get Order Status",
        description="Get the latest status for a single order id.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_order_status(order_id: str) -> OrderStatus:
        return await call_core(lambda client: get_order_status(client, order_id))

    @mcp.tool(
        name="ibkr_get_order_set_status",
        title="Get Order Set Status",
        description="Get aggregate status for a list of related order ids.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_order_set_status(order_ids: list[str]) -> OrderSetStatusResponse:
        if not order_ids:
            raise MCPToolError("VALIDATION_ERROR", "order_ids must contain at least one order id")
        found_orders = await call_core(lambda client: get_order_set_status(client, order_ids))
        return _order_set_response(order_ids, found_orders)

    @mcp.tool(
        name="ibkr_preview_order",
        title="Preview Order",
        description="Preview a single-leg or bracket order without placing it.",
        annotations=PREVIEW_TOOL,
        structured_output=True,
    )
    async def ibkr_preview_order(order: OrderSpec) -> OrderPreview:
        _ensure_fully_qualified_option(order.instrument)
        return await call_core(lambda client: preview_order(client, order))

    @mcp.tool(
        name="ibkr_place_order",
        title="Place Order",
        description=(
            "Place a single-leg or bracket order. Requires a clientOrderId and respects "
            "control.json safety rails."
        ),
        annotations=IDEMPOTENT_WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_place_order(order: OrderSpec) -> OrderResult:
        _ensure_fully_qualified_option(order.instrument)
        if not order.clientOrderId:
            raise MCPToolError(
                "VALIDATION_ERROR",
                "clientOrderId is required for ibkr_place_order and is used as the idempotency key",
            )
        return await call_core(lambda client: place_order(client, order))

    @mcp.tool(
        name="ibkr_cancel_order",
        title="Cancel Order",
        description="Cancel a single open order by order id.",
        annotations=WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_cancel_order(order_id: str) -> CancelResult:
        return await call_core(lambda client: cancel_order(client, order_id))

    @mcp.tool(
        name="ibkr_cancel_order_set",
        title="Cancel Order Set",
        description="Cancel a set of related orders, such as bracket legs.",
        annotations=WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_cancel_order_set(order_ids: list[str]) -> CancelResult:
        if not order_ids:
            raise MCPToolError("VALIDATION_ERROR", "order_ids must contain at least one order id")
        return await call_core(lambda client: cancel_order_set(client, order_ids))

    @mcp.tool(
        name="ibkr_get_option_chain",
        title="Get Option Chain",
        description=(
            "Discover single-leg option contracts for an underlying and return a bounded "
            "list of qualified candidates."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_option_chain(
        underlying: SymbolSpec,
        expiries: Optional[list[str]] = None,
        expiry_start: Optional[str] = None,
        expiry_end: Optional[str] = None,
        min_strike: Optional[float] = None,
        max_strike: Optional[float] = None,
        strike_count: int = 10,
        max_candidates: int = 24,
        rights: Optional[list[str]] = None,
        option_exchange: Optional[str] = None,
    ) -> OptionChainResponse:
        return await call_core(
            lambda client: get_option_chain(
                underlying,
                client,
                expiries=expiries,
                expiry_start=expiry_start,
                expiry_end=expiry_end,
                min_strike=min_strike,
                max_strike=max_strike,
                strike_count=strike_count,
                max_candidates=max_candidates,
                rights=rights,
                option_exchange=option_exchange,
            )
        )

    @mcp.tool(
        name="ibkr_get_option_snapshot",
        title="Get Option Snapshot",
        description="Get quote, volatility, and greeks for a fully specified single-leg option.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_option_snapshot(instrument: SymbolSpec) -> OptionSnapshotResponse:
        _ensure_fully_qualified_option(instrument)
        return await call_core(lambda client: get_option_snapshot(instrument, client))

    if config.enable_admin_tools:

        @mcp.tool(
            name="ibkr_admin_verify_gateway",
            title="Verify Gateway",
            description="Verify the active gateway connection by fetching an account summary.",
            annotations=READ_TOOL,
            structured_output=True,
        )
        async def ibkr_admin_verify_gateway(
            account_id: Optional[str] = None,
        ) -> GatewayVerificationResponse:
            summary = await call_core(
                lambda client: get_account_summary(client, account_id=account_id)
            )
            return GatewayVerificationResponse(
                success=True,
                message="Gateway verified via account summary.",
                verificationMode="pooled",
                accountId=summary.accountId,
                netLiquidation=summary.netLiquidation,
                currency=summary.currency,
                summaryTimestamp=summary.timestamp.isoformat() if summary.timestamp else None,
            )

        @mcp.tool(
            name="ibkr_admin_update_trading_control",
            title="Update Trading Control",
            description=(
                "Safely update control.json with compare-and-swap semantics. "
                "Required for explicit live trading changes."
            ),
            annotations=WRITE_TOOL,
            structured_output=True,
        )
        async def ibkr_admin_update_trading_control(
            request: TradingControlUpdateRequest,
        ) -> TradingControlUpdateResponse:
            current = load_control()
            previous_status = _status_from_control_state(current)
            expected = _normalize_control_expectation(current)
            if request.expectedCurrentState != expected:
                raise MCPToolError(
                    "STATE_MISMATCH",
                    "expectedCurrentState does not match the current trading-control state",
                    {
                        "expectedCurrentState": request.expectedCurrentState.model_dump(
                            mode="json", exclude_none=True
                        ),
                        "actualCurrentState": expected.model_dump(
                            mode="json", exclude_none=True
                        ),
                    },
                )

            updated = replace(current)
            updated_fields: list[str] = []

            if request.tradingMode is not None and request.tradingMode != current.trading_mode:
                updated.trading_mode = request.tradingMode
                updated_fields.append("tradingMode")
            if (
                request.ordersEnabled is not None
                and request.ordersEnabled != current.orders_enabled
            ):
                updated.orders_enabled = request.ordersEnabled
                updated_fields.append("ordersEnabled")
            if request.dryRun is not None and request.dryRun != current.dry_run:
                updated.dry_run = request.dryRun
                updated_fields.append("dryRun")
            if request.liveTradingOverrideFile is not None:
                normalized_path = request.liveTradingOverrideFile.strip() or None
                if normalized_path != current.live_trading_override_file:
                    updated.live_trading_override_file = normalized_path
                    updated_fields.append("liveTradingOverrideFile")

            next_state = TradingControlExpectation(
                tradingMode=updated.trading_mode,
                ordersEnabled=updated.orders_enabled,
                dryRun=updated.dry_run,
                liveTradingOverrideFile=updated.live_trading_override_file,
            )
            if (
                not current.is_live_trading_enabled()
                and next_state.tradingMode == "live"
                and next_state.ordersEnabled
                and request.liveEnableConfirmation != LIVE_TRADING_ENABLE_CONFIRMATION
            ):
                raise MCPToolError(
                    "CONFIRMATION_REQUIRED",
                    "Exact live trading confirmation string required before enabling live real-money trading",
                    {"required": LIVE_TRADING_ENABLE_CONFIRMATION},
            )

            validation_errors = validate_control(updated)
            if validation_errors:
                raise MCPToolError(
                    "VALIDATION_ERROR",
                    "Invalid trading control update",
                    {"validationErrors": validation_errors},
                )

            if not updated_fields:
                current_status = _current_trading_status()
                return TradingControlUpdateResponse(
                    success=True,
                    updatedFields=[],
                    previousState=current_status,
                    currentState=current_status,
                    message="No changes applied.",
                )

            write_control(updated)
            write_audit_entry(
                action="CONTROL_UPDATED",
                reason=request.reason,
                updated_fields=",".join(updated_fields),
                source="mcp",
            )
            reset_config()
            await service.invalidate_client()

            current_status = _current_trading_status()
            return TradingControlUpdateResponse(
                success=True,
                updatedFields=updated_fields,
                previousState=previous_status,
                currentState=current_status,
                message="Trading control updated.",
            )

    if config.enable_legacy_aliases:

        @mcp.tool(
            name="get_quote",
            title="Legacy Get Quote",
            description="Compatibility alias for ibkr_get_quote.",
            annotations=READ_TOOL,
            structured_output=True,
        )
        async def legacy_get_quote(instrument: SymbolSpec) -> Quote:
            return await ibkr_get_quote(instrument)

        @mcp.tool(
            name="get_historical_data",
            title="Legacy Get Historical Data",
            description="Compatibility alias for ibkr_get_historical_bars.",
            annotations=READ_TOOL,
            structured_output=True,
        )
        async def legacy_get_historical_data(
            instrument: SymbolSpec,
            bar_size: str,
            duration: str,
            what_to_show: str = "TRADES",
            rth_only: bool = True,
        ) -> HistoricalBarsResponse:
            return await ibkr_get_historical_bars(
                instrument=instrument,
                bar_size=bar_size,
                duration=duration,
                what_to_show=what_to_show,
                rth_only=rth_only,
            )

        @mcp.tool(
            name="get_account_status",
            title="Legacy Get Account Status",
            description="Compatibility alias for combined account summary and positions.",
            annotations=READ_TOOL,
            structured_output=True,
        )
        async def legacy_get_account_status(
            account_id: Optional[str] = None,
        ) -> AccountStatusResponse:
            return await get_account_status_model(account_id=account_id)

        @mcp.tool(
            name="get_pnl",
            title="Legacy Get PnL",
            description="Compatibility alias for ibkr_get_pnl.",
            annotations=READ_TOOL,
            structured_output=True,
        )
        async def legacy_get_pnl(
            account_id: Optional[str] = None,
            timeframe: Optional[str] = None,
        ) -> AccountPnl:
            return await ibkr_get_pnl(account_id=account_id, timeframe=timeframe)

        @mcp.tool(
            name="preview_order",
            title="Legacy Preview Order",
            description="Compatibility alias for ibkr_preview_order.",
            annotations=PREVIEW_TOOL,
            structured_output=True,
        )
        async def legacy_preview_order(order: OrderSpec) -> OrderPreview:
            return await ibkr_preview_order(order)

        @mcp.tool(
            name="place_order",
            title="Legacy Place Order",
            description="Compatibility alias for ibkr_place_order.",
            annotations=IDEMPOTENT_WRITE_TOOL,
            structured_output=True,
        )
        async def legacy_place_order(order: OrderSpec) -> OrderResult:
            return await ibkr_place_order(order)

        @mcp.tool(
            name="get_order_status",
            title="Legacy Get Order Status",
            description="Compatibility alias for ibkr_get_order_status.",
            annotations=READ_TOOL,
            structured_output=True,
        )
        async def legacy_get_order_status(order_id: str) -> OrderStatus:
            return await ibkr_get_order_status(order_id)

        @mcp.tool(
            name="cancel_order",
            title="Legacy Cancel Order",
            description="Compatibility alias for ibkr_cancel_order.",
            annotations=WRITE_TOOL,
            structured_output=True,
        )
        async def legacy_cancel_order(order_id: str) -> CancelResult:
            return await ibkr_cancel_order(order_id)

    @mcp.resource(
        "ibkr://status/overview",
        title="IBKR Status Overview",
        description="Combined health, trading status, and schedule status.",
        mime_type="application/json",
    )
    async def resource_status_overview() -> str:
        payload = {
            "health": (await get_health_model()).model_dump(mode="json", exclude_none=True),
            "tradingStatus": (
                await get_trading_status_model()
            ).model_dump(mode="json", exclude_none=True),
            "scheduleStatus": (
                await get_schedule_status_model()
            ).model_dump(mode="json", exclude_none=True),
        }
        return _json_payload(payload)

    @mcp.resource(
        "ibkr://account/default/summary",
        title="Default Account Summary",
        description="Summary for the default managed account.",
        mime_type="application/json",
    )
    async def resource_default_account_summary() -> str:
        return _json_payload(await ibkr_get_account_summary())

    @mcp.resource(
        "ibkr://account/default/positions",
        title="Default Account Positions",
        description="Positions for the default managed account.",
        mime_type="application/json",
    )
    async def resource_default_account_positions() -> str:
        return _json_payload(await ibkr_get_positions())

    @mcp.resource(
        "ibkr://orders/open",
        title="Open Orders",
        description="Current open orders on the active connection.",
        mime_type="application/json",
    )
    async def resource_open_orders() -> str:
        return _json_payload(await ibkr_list_open_orders())

    @mcp.resource(
        "ibkr://options/chain/{symbol}",
        title="Default Option Chain",
        description=(
            "Option chain for a stock-style underlying symbol using SMART/USD defaults."
        ),
        mime_type="application/json",
    )
    async def resource_option_chain(symbol: str) -> str:
        chain = await ibkr_get_option_chain(
            underlying=SymbolSpec(
                symbol=symbol.upper(),
                securityType="STK",
                exchange="SMART",
                currency="USD",
            ),
            strike_count=8,
            max_candidates=16,
        )
        return _json_payload(chain)

    @mcp.prompt(
        name="pre_trade_checklist",
        title="Pre-Trade Checklist",
        description="Checklist for safe order preparation.",
    )
    def pre_trade_checklist() -> str:
        return (
            "Before placing any order:\n"
            "1. Call ibkr_get_trading_status and ibkr_get_schedule_status.\n"
            "2. Resolve the exact contract with ibkr_resolve_contract.\n"
            "3. For options, discover candidates with ibkr_get_option_chain and inspect a fully "
            "qualified contract with ibkr_get_option_snapshot.\n"
            "4. Preview the order with ibkr_preview_order.\n"
            "5. Use a unique clientOrderId with ibkr_place_order.\n"
            "6. Never enable live trading unless the user explicitly requests it."
        )

    @mcp.prompt(
        name="option_contract_selection",
        title="Option Contract Selection",
        description="Workflow for choosing a single-leg option contract.",
    )
    def option_contract_selection(underlying_symbol: str, thesis: str = "") -> str:
        thesis_line = f"Trading thesis: {thesis}\n" if thesis else ""
        return (
            f"Select a single-leg option for {underlying_symbol}.\n"
            f"{thesis_line}"
            "Steps:\n"
            "1. Call ibkr_get_option_chain with the underlying SymbolSpec.\n"
            "2. Narrow by expiry, strike window, and right.\n"
            "3. Call ibkr_get_option_snapshot on the final fully specified contract.\n"
            "4. Confirm the exact expiry, strike, right, exchange, and multiplier before trading."
        )

    @mcp.prompt(
        name="order_review",
        title="Order Review",
        description="Checklist for reviewing an order preview before placement.",
    )
    def order_review(order_summary: str = "") -> str:
        summary_prefix = f"Order summary:\n{order_summary}\n\n" if order_summary else ""
        return (
            f"{summary_prefix}"
            "Review:\n"
            "1. Instrument details are fully qualified.\n"
            "2. Side, quantity, and order type match the user's intent.\n"
            "3. Preview warnings are understood.\n"
            "4. A unique clientOrderId is ready for placement.\n"
            "5. If live trading is enabled, confirm the user explicitly requested real execution."
        )

    return mcp


mcp = create_mcp_server()


def main() -> None:
    """Run the MCP server using the configured transport."""
    server = create_mcp_server()
    server.run(transport=get_mcp_config().transport)


if __name__ == "__main__":
    main()
