"""Modern MCP server for direct IBKR trading and market data access."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ibkr_core.account import (
    AccountError,
    get_account_summary,
    get_pnl,
    get_positions,
)
from ibkr_core.client import ConnectionError as IBKRConnectionError
from ibkr_core.config import InvalidConfigError, ensure_runtime_files, get_config, reset_config
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
    AgentProfileResponse,
    ApprovalStatusResponse,
    AuditLogEntry,
    AuditLogResponse,
    BasketPreviewItem,
    CancelTradeIntentResponse,
    EmergencyStopResponse,
    GatewayVerificationResponse,
    HealthResponse,
    HistoricalBarsResponse,
    NotifyResponse,
    OpenOrderInfo,
    OpenOrdersResponse,
    OrderBasketPreviewResponse,
    OrderImpactResponse,
    OrderSetStatusResponse,
    PortfolioRiskResponse,
    PositionLimitsCheckResponse,
    PositionsResponse,
    ProfileValidationResponse,
    ScheduleStatusResponse,
    SessionActivityResponse,
    SessionOrderSummary,
    TradeIntentListResponse,
    TradeIntentOrderInfo,
    TradeIntentResponse,
    TradingControlExpectation,
    TradingControlUpdateRequest,
    TradingControlUpdateResponse,
    TradingStatusResponse,
)
from mcp_server.profiles.loader import load_profile, list_profiles
from mcp_server.profiles.validator import validate_order_against_profile
from mcp_server.risk.impact import assess_order_impact
from mcp_server.risk.portfolio import compute_portfolio_risk
from mcp_server.security import StaticBearerTokenVerifier
from mcp_server.services import IBKRMCPService
from mcp_server.telegram.approval import (
    create_approval,
    create_resolved_approval,
    get_approval,
    mark_used,
    set_telegram_message_id,
)
from mcp_server.telegram.config import TelegramConfig
from mcp_server.telegram.notifications import (
    format_emergency_stop,
    format_environment_change,
    format_notification,
    format_trade_approval,
    format_trade_intent_approval,
)
from trade_core import (
    create_trade_intent,
    get_trade_intent,
    list_trade_intent_order_ids,
    list_trade_intents,
    record_position_snapshot,
    record_trade_intent_cancellation,
    record_trade_intent_reconcile,
    record_trade_intent_submission,
    set_trade_intent_approval,
    update_trade_intent_status,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

READ_TOOL = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
PREVIEW_TOOL = ToolAnnotations(readOnlyHint=True)
WRITE_TOOL = ToolAnnotations(destructiveHint=True)
IDEMPOTENT_WRITE_TOOL = ToolAnnotations(destructiveHint=True, idempotentHint=True)
_TELEGRAM_APP_UNSET = object()


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
        blockReason=payload.get("block_reason"),
        updatedAt=payload.get("updated_at"),
        updatedBy=payload.get("updated_by"),
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
        blockReason=state.block_reason,
        updatedAt=state.updated_at,
        updatedBy=state.updated_by,
        liveTradingOverrideFile=state.live_trading_override_file,
        overrideFileExists=override_exists if state.live_trading_override_file else None,
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
        blockReason=state.block_reason,
        liveTradingOverrideFile=state.live_trading_override_file,
    )


def _approval_response_from_record(rec: dict[str, Any]) -> ApprovalStatusResponse:
    """Convert a stored approval record to the typed response model."""
    return ApprovalStatusResponse(
        approvalId=rec["approval_id"],
        approvalType=rec["approval_type"],
        status=rec["status"],
        requestedAt=rec["requested_at"],
        expiresAt=rec["expires_at"],
        resolvedAt=rec.get("resolved_at"),
        resolveNote=rec.get("resolve_note"),
        telegramMessageId=rec.get("telegram_message_id"),
    )


def _ensure_telegram_ready(
    config: MCPConfig,
    telegram_cfg: Optional[TelegramConfig],
    telegram_app: Any = _TELEGRAM_APP_UNSET,
) -> None:
    """Raise a configuration error when Telegram mode is required but unavailable."""
    if config.approval_requires_telegram and telegram_cfg is None:
        raise MCPToolError(
            "CONFIG_ERROR",
            "MCP_ORDER_APPROVAL_MODE=telegram requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
        )
    if (
        config.approval_requires_telegram
        and telegram_app is not _TELEGRAM_APP_UNSET
        and telegram_app is None
    ):
        raise MCPToolError(
            "CONFIG_ERROR",
            "Telegram approval mode is enabled but the Telegram bot is not running",
        )


def _validate_approval(
    approval_id: Optional[str],
    *,
    required_type: str,
) -> Optional[dict[str, Any]]:
    """Validate an approval record and return it when it is ready for use."""
    if approval_id is None:
        raise MCPToolError("APPROVAL_REQUIRED", "approval_id is required for this operation")
    rec = get_approval(approval_id)
    if rec is None:
        raise MCPToolError("INVALID_APPROVAL", f"Approval '{approval_id}' not found.")
    if rec.get("approval_type") != required_type:
        raise MCPToolError(
            "INVALID_APPROVAL",
            f"Approval '{approval_id}' is type '{rec.get('approval_type')}', expected '{required_type}'.",
        )
    status = rec.get("status")
    if status == "expired":
        raise MCPToolError("APPROVAL_EXPIRED", "Approval has expired. Request a new one.")
    if status == "denied":
        raise MCPToolError(
            "APPROVAL_DENIED",
            f"Approval was denied. Note: {rec.get('resolve_note', '')}",
        )
    if status == "used":
        raise MCPToolError(
            "APPROVAL_ALREADY_USED",
            "This approval has already been consumed by a previous operation.",
        )
    if status != "approved":
        raise MCPToolError(
            "APPROVAL_PENDING",
            f"Approval is still '{status}'. Wait for the human to respond.",
        )
    return rec


def _trade_intent_response(record: Any) -> TradeIntentResponse:
    """Convert an internal TradeIntentRecord into the MCP response model."""
    return TradeIntentResponse(
        intentId=record.intent_id,
        intentKey=record.intent_key,
        accountId=record.account_id,
        reason=record.reason,
        status=record.status.value,
        approvalId=record.approval_id,
        approvalStatus=record.approval_status,
        dryRun=record.dry_run,
        orderCount=record.order_count,
        ordersSubmitted=record.orders_submitted,
        ordersFilled=record.orders_filled,
        ordersCancelled=record.orders_cancelled,
        ordersFailed=record.orders_failed,
        lastError=record.last_error,
        createdAt=record.created_at.isoformat(),
        updatedAt=record.updated_at.isoformat(),
        orders=[
            TradeIntentOrderInfo(
                intentOrderId=order.intent_order_id,
                sequenceNo=order.sequence_no,
                clientOrderId=order.client_order_id,
                order=order.order,
                preview=order.preview,
                status=order.status.value,
                orderId=order.order_id,
                ibkrOrderId=order.ibkr_order_id,
                submittedAt=order.submitted_at.isoformat() if order.submitted_at else None,
                updatedAt=order.updated_at.isoformat(),
                lastError=order.last_error,
            )
            for order in record.orders
        ],
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
    ensure_runtime_files()
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

    # Telegram bot Application (None when not configured)
    telegram_app: Optional[Any] = None
    telegram_cfg: Optional[TelegramConfig] = None
    if config.telegram_enabled:
        telegram_cfg = TelegramConfig(
            bot_token=config.telegram_bot_token,  # type: ignore[arg-type]
            chat_id=config.telegram_chat_id,  # type: ignore[arg-type]
            approval_timeout_seconds=config.telegram_approval_timeout_seconds,
            live_unlock_timeout_seconds=config.telegram_live_unlock_timeout_seconds,
        )

    @asynccontextmanager
    async def app_lifespan(server: FastMCP):
        nonlocal telegram_app
        logger.info(
            "Starting IBKR MCP server transport=%s host=%s port=%s path=%s",
            config.transport,
            config.host,
            config.port,
            config.streamable_http_path,
        )
        if telegram_cfg is not None:
            try:
                from mcp_server.telegram.bot import start_bot

                telegram_app = await start_bot(telegram_cfg)
                logger.info("Telegram bot started")
            except Exception as exc:
                logger.warning("Failed to start Telegram bot: %s", exc)
        try:
            yield {
                "transport": config.transport,
                "public_base_url": config.public_base_url,
            }
        finally:
            if telegram_app is not None:
                from mcp_server.telegram.bot import stop_bot

                await stop_bot(telegram_app)
            await service.shutdown()
            logger.info("IBKR MCP server shutdown complete")

    mcp = FastMCP(
        name="mm-ibkr-mcp",
        instructions=(
            "Interactive Brokers MCP tools for account monitoring, single-order execution, "
            "and durable basket execution. IB Gateway or TWS is assumed to already be "
            "running and reachable via the IB connection settings in config.json.\n"
            "Canonical workflow:\n"
            "1. ibkr_health + ibkr_get_trading_status + ibkr_get_schedule_status — verify "
            "connectivity and runtime safety.\n"
            "2. If needed, ibkr_request_environment_change to switch between live and paper. "
            "Once approved, call ibkr_execute_environment_change.\n"
            "3. ibkr_get_account_summary + ibkr_get_positions + ibkr_get_portfolio_risk — "
            "understand the account before trading.\n"
            "3. ibkr_resolve_contract, ibkr_get_quote, ibkr_get_historical_bars, and the "
            "options tools — fully qualify and inspect the instrument.\n"
            "4. ibkr_preview_order or ibkr_preview_order_basket — estimate execution, margin, "
            "and commission before any submission.\n"
            "5. ibkr_assess_order_impact + ibkr_validate_against_profile — check portfolio and "
            "agent-profile constraints.\n"
            "6. Single-order flow: ibkr_request_trade_approval only when "
            "MCP_ORDER_APPROVAL_MODE=telegram, then ibkr_place_order.\n"
            "7. Basket flow: ibkr_create_trade_intent, optionally "
            "ibkr_request_trade_intent_approval, then ibkr_submit_trade_intent and "
            "ibkr_reconcile_trade_intent.\n"
            "8. Use ibkr_cancel_order, ibkr_cancel_trade_intent, or ibkr_emergency_stop only "
            "on explicit user instruction.\n"
            "Approval mode is controlled only by MCP_ORDER_APPROVAL_MODE=telegram|yolo.\n"
            "ALWAYS use a unique clientOrderId for every order."
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
            gateway_host = gateway_config.ibkr_host
            gateway_port = gateway_config.ibkr_port
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
            "Place a single-leg or bracket order. Requires a clientOrderId. "
            "When MCP_ORDER_APPROVAL_MODE=telegram, an approval_id from "
            "ibkr_request_trade_approval is also required."
        ),
        annotations=IDEMPOTENT_WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_place_order(
        order: OrderSpec, approval_id: Optional[str] = None
    ) -> OrderResult:
        _ensure_fully_qualified_option(order.instrument)
        if not order.clientOrderId:
            raise MCPToolError(
                "VALIDATION_ERROR",
                "clientOrderId is required for ibkr_place_order and is used as the idempotency key",
            )

        if config.approval_requires_telegram:
            _ensure_telegram_ready(config, telegram_cfg)
            _validate_approval(approval_id, required_type="trade")
            assert approval_id is not None
            mark_used(approval_id)

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
        name="ibkr_preview_order_basket",
        title="Preview Order Basket",
        description="Preview a basket of explicit orders without placing them.",
        annotations=PREVIEW_TOOL,
        structured_output=True,
    )
    async def ibkr_preview_order_basket(orders: list[OrderSpec]) -> OrderBasketPreviewResponse:
        if not orders:
            raise MCPToolError("VALIDATION_ERROR", "orders must contain at least one order")

        items: list[BasketPreviewItem] = []
        warnings: list[str] = []
        previewed_count = 0
        failed_count = 0
        total_notional = 0.0
        total_commission = 0.0
        saw_notional = False
        saw_commission = False

        for order in orders:
            _ensure_fully_qualified_option(order.instrument)
            if not order.clientOrderId:
                raise MCPToolError(
                    "VALIDATION_ERROR",
                    "Every basket order must include clientOrderId",
                )
            try:
                preview = await call_core(lambda client, current=order: preview_order(client, current))
                previewed_count += 1
                if preview.estimatedNotional is not None:
                    total_notional += float(preview.estimatedNotional)
                    saw_notional = True
                if preview.estimatedCommission is not None:
                    total_commission += float(preview.estimatedCommission)
                    saw_commission = True
                for warning in preview.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
                items.append(
                    BasketPreviewItem(
                        clientOrderId=order.clientOrderId,
                        symbol=order.instrument.symbol,
                        preview=preview,
                        error=None,
                    )
                )
            except Exception as exc:
                failed_count += 1
                tool_error = _tool_error(exc)
                items.append(
                    BasketPreviewItem(
                        clientOrderId=order.clientOrderId,
                        symbol=order.instrument.symbol,
                        preview=None,
                        error=tool_error.message,
                    )
                )

        return OrderBasketPreviewResponse(
            orderCount=len(orders),
            previewedCount=previewed_count,
            failedCount=failed_count,
            estimatedTotalNotional=round(total_notional, 2) if saw_notional else None,
            estimatedTotalCommission=round(total_commission, 2) if saw_commission else None,
            warnings=warnings,
            items=items,
        )

    @mcp.tool(
        name="ibkr_create_trade_intent",
        title="Create Trade Intent",
        description=(
            "Create or return an idempotent basket-style trade intent from explicit orders. "
            "This persists the basket and optional previews before submission."
        ),
        annotations=IDEMPOTENT_WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_create_trade_intent(
        orders: list[OrderSpec],
        reason: str,
        account_id: Optional[str] = None,
        preview_orders: bool = True,
    ) -> TradeIntentResponse:
        if not orders:
            raise MCPToolError("VALIDATION_ERROR", "orders must contain at least one order")

        resolved_account_id = (
            account_id
            or next((order.accountId for order in orders if order.accountId), None)
            or get_config().default_account_id
        )
        previews: list[Optional[OrderPreview]] = []
        if preview_orders:
            for order in orders:
                _ensure_fully_qualified_option(order.instrument)
                try:
                    preview = await call_core(
                        lambda client, current=order: preview_order(client, current)
                    )
                except Exception as exc:
                    logger.warning(
                        "Preview failed while creating trade intent for %s: %s",
                        order.clientOrderId or order.instrument.symbol,
                        exc,
                    )
                    preview = None
                previews.append(preview)
        else:
            previews = [None] * len(orders)

        record = create_trade_intent(
            orders=orders,
            reason=reason,
            account_id=resolved_account_id,
            dry_run=_current_trading_status().effectiveDryRun,
            require_approval=config.approval_requires_telegram,
            previews=previews,
        )
        return _trade_intent_response(record)

    @mcp.tool(
        name="ibkr_request_trade_intent_approval",
        title="Request Trade Intent Approval",
        description=(
            "Request a single Telegram approval covering a persisted trade intent. "
            "In YOLO mode, the approval is auto-approved immediately."
        ),
        annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def ibkr_request_trade_intent_approval(intent_id: str) -> ApprovalStatusResponse:
        record = get_trade_intent(intent_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Trade intent '{intent_id}' not found.")

        request_payload = {
            "intent_id": record.intent_id,
            "reason": record.reason,
            "account_id": record.account_id,
            "orders": [
                order.order.model_dump(mode="json", exclude_none=True)
                for order in record.orders
            ],
        }

        if not config.approval_requires_telegram:
            rec = create_resolved_approval(
                "trade_intent",
                request_payload,
                status="approved",
                resolve_note="Auto-approved because MCP_ORDER_APPROVAL_MODE=yolo",
            )
            set_trade_intent_approval(
                record.intent_id,
                approval_id=rec["approval_id"],
                approval_status=rec["status"],
            )
            return _approval_response_from_record(rec)

        _ensure_telegram_ready(config, telegram_cfg, telegram_app)
        timeout = telegram_cfg.approval_timeout_seconds if telegram_cfg else 300
        rec = create_approval(
            "trade_intent",
            request_payload,
            timeout_seconds=timeout,
        )
        approval_id = rec["approval_id"]
        set_trade_intent_approval(record.intent_id, approval_id=approval_id, approval_status="pending")

        if telegram_app is not None and telegram_cfg is not None:
            from mcp_server.telegram.bot import send_approval_request

            message = format_trade_intent_approval(
                approval_id,
                record.intent_id,
                record.reason,
                request_payload["orders"],
            )
            msg_id = await send_approval_request(telegram_app, telegram_cfg, approval_id, message)
            if msg_id:
                set_telegram_message_id(approval_id, msg_id)

        latest = get_approval(approval_id) or rec
        return _approval_response_from_record(latest)

    @mcp.tool(
        name="ibkr_submit_trade_intent",
        title="Submit Trade Intent",
        description=(
            "Submit the planned orders in a persisted trade intent. "
            "Requires a trade-intent approval when MCP_ORDER_APPROVAL_MODE=telegram."
        ),
        annotations=IDEMPOTENT_WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_submit_trade_intent(
        intent_id: str,
        approval_id: Optional[str] = None,
    ) -> TradeIntentResponse:
        record = get_trade_intent(intent_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Trade intent '{intent_id}' not found.")

        if config.approval_requires_telegram:
            _ensure_telegram_ready(config, telegram_cfg)
            resolved_approval_id = approval_id or record.approval_id
            rec = _validate_approval(resolved_approval_id, required_type="trade_intent")
            assert resolved_approval_id is not None
            set_trade_intent_approval(
                intent_id,
                approval_id=resolved_approval_id,
                approval_status=rec["status"],
            )
            mark_used(resolved_approval_id)
            set_trade_intent_approval(
                intent_id,
                approval_id=resolved_approval_id,
                approval_status="used",
            )
            update_trade_intent_status(intent_id, status="SUBMITTING")
        else:
            update_trade_intent_status(intent_id, status="SUBMITTING")

        latest = get_trade_intent(intent_id)
        assert latest is not None
        for order_info in latest.orders:
            if order_info.status.value != "PLANNED":
                continue
            try:
                result = await call_core(
                    lambda client, current=order_info.order: place_order(client, current)
                )
            except Exception as exc:
                tool_error = _tool_error(exc)
                result = OrderResult(
                    orderId=None,
                    clientOrderId=order_info.client_order_id,
                    status="REJECTED",
                    orderStatus=None,
                    errors=[tool_error.message],
                )
            record_trade_intent_submission(
                intent_id=intent_id,
                intent_order_id=order_info.intent_order_id,
                order_result=result,
            )

        refreshed = get_trade_intent(intent_id)
        assert refreshed is not None
        return _trade_intent_response(refreshed)

    @mcp.tool(
        name="ibkr_get_trade_intent",
        title="Get Trade Intent",
        description="Fetch the persisted state of a trade intent and its orders.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_trade_intent(intent_id: str) -> TradeIntentResponse:
        record = get_trade_intent(intent_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Trade intent '{intent_id}' not found.")
        return _trade_intent_response(record)

    @mcp.tool(
        name="ibkr_list_trade_intents",
        title="List Trade Intents",
        description="List recent persisted trade intents with optional status filtering.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_list_trade_intents(
        status: Optional[str] = None,
        limit: int = 50,
    ) -> TradeIntentListResponse:
        records = list_trade_intents(status=status.upper() if status else None, limit=limit)
        intents = [_trade_intent_response(record) for record in records]
        return TradeIntentListResponse(count=len(intents), intents=intents)

    @mcp.tool(
        name="ibkr_reconcile_trade_intent",
        title="Reconcile Trade Intent",
        description="Refresh a trade intent against current broker order status and positions.",
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_reconcile_trade_intent(intent_id: str) -> TradeIntentResponse:
        record = get_trade_intent(intent_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Trade intent '{intent_id}' not found.")

        active_statuses = {"SUBMITTED", "PARTIALLY_FILLED"}
        for order_info in record.orders:
            if order_info.order_id is None or order_info.status.value not in active_statuses:
                continue
            try:
                status = await call_core(
                    lambda client, current_id=order_info.order_id: get_order_status(client, current_id)
                )
            except Exception as exc:
                logger.warning(
                    "Failed to reconcile order %s in intent %s: %s",
                    order_info.order_id,
                    intent_id,
                    exc,
                )
                continue
            record_trade_intent_reconcile(
                intent_id=intent_id,
                intent_order_id=order_info.intent_order_id,
                order_status=status,
            )

        try:
            summary = await call_core(
                lambda client: get_account_summary(client, account_id=record.account_id)
            )
            positions = await call_core(
                lambda client: get_positions(client, account_id=record.account_id)
            )
            record_position_snapshot(
                account_id=summary.accountId,
                snapshot_type="trade_intent_reconcile",
                payload={
                    "intent_id": intent_id,
                    "summary": summary.model_dump(mode="json", exclude_none=True),
                    "positions": [
                        position.model_dump(mode="json", exclude_none=True)
                        for position in positions
                    ],
                },
            )
        except Exception as exc:
            logger.warning("Failed to record position snapshot for %s: %s", intent_id, exc)

        refreshed = get_trade_intent(intent_id)
        assert refreshed is not None
        return _trade_intent_response(refreshed)

    @mcp.tool(
        name="ibkr_cancel_trade_intent",
        title="Cancel Trade Intent",
        description="Cancel all active broker orders associated with a trade intent.",
        annotations=WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_cancel_trade_intent(intent_id: str) -> CancelTradeIntentResponse:
        record = get_trade_intent(intent_id)
        if record is None:
            raise MCPToolError("NOT_FOUND", f"Trade intent '{intent_id}' not found.")

        cancelled_ids: list[str] = []
        failed_count = 0
        active_statuses = {"SUBMITTED", "PARTIALLY_FILLED"}

        for order_info in record.orders:
            if order_info.order_id is None or order_info.status.value not in active_statuses:
                continue
            try:
                result = await call_core(
                    lambda client, current_id=order_info.order_id: cancel_order(client, current_id)
                )
            except Exception as exc:
                result = CancelResult(
                    orderId=order_info.order_id,
                    status="REJECTED",
                    message=_tool_error(exc).message,
                )
            updated = record_trade_intent_cancellation(
                intent_id=intent_id,
                intent_order_id=order_info.intent_order_id,
                cancel_result=result,
            )
            if result.status == "CANCELLED":
                cancelled_ids.append(result.orderId)
            else:
                failed_count += 1
            record = updated

        refreshed = get_trade_intent(intent_id)
        assert refreshed is not None
        return CancelTradeIntentResponse(
            intentId=intent_id,
            cancelledOrderIds=cancelled_ids,
            cancelledCount=len(cancelled_ids),
            failedCount=failed_count,
            intent=_trade_intent_response(refreshed),
        )

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

    # -----------------------------------------------------------------------
    # Telegram human-in-the-loop tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="ibkr_notify",
        title="Send Telegram Notification",
        description=(
            "Send an informational notification to the operator via Telegram. "
            "No approval required; purely informational."
        ),
        annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def ibkr_notify(
        title: str,
        body: str,
        level: str = "info",
    ) -> NotifyResponse:
        if telegram_app is None or telegram_cfg is None:
            return NotifyResponse(
                sent=False,
                message="Telegram is not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID).",
            )
        from mcp_server.telegram.bot import send_notification

        text = format_notification(title, body, level)
        msg_id = await send_notification(telegram_app, telegram_cfg, text)
        return NotifyResponse(
            sent=msg_id is not None,
            telegramMessageId=msg_id,
            message="Notification sent." if msg_id else "Failed to send notification.",
        )

    @mcp.tool(
        name="ibkr_request_trade_approval",
        title="Request Trade Approval",
        description=(
            "Send a trade approval request to the operator via Telegram. "
            "Returns an approval_id to poll with ibkr_check_approval_status. "
            "When MCP_ORDER_APPROVAL_MODE=telegram, this approval_id "
            "must be passed to ibkr_place_order."
        ),
        annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def ibkr_request_trade_approval(
        order: OrderSpec,
        reason: str,
        preview: Optional[OrderPreview] = None,
    ) -> ApprovalStatusResponse:
        _ensure_fully_qualified_option(order.instrument)
        order_data = order.model_dump(mode="json", exclude_none=True)
        preview_data = preview.model_dump(mode="json", exclude_none=True) if preview else None
        timeout = telegram_cfg.approval_timeout_seconds if telegram_cfg else 300

        if not config.approval_requires_telegram:
            rec = create_resolved_approval(
                "trade",
                {"order": order_data, "preview": preview_data, "reason": reason},
                status="approved",
                resolve_note="Auto-approved because MCP_ORDER_APPROVAL_MODE=yolo",
            )
            return _approval_response_from_record(rec)

        _ensure_telegram_ready(config, telegram_cfg, telegram_app)

        rec = create_approval(
            "trade",
            {"order": order_data, "preview": preview_data, "reason": reason},
            timeout_seconds=timeout,
        )
        approval_id = rec["approval_id"]

        if telegram_app is not None and telegram_cfg is not None:
            from mcp_server.telegram.bot import send_approval_request

            text = format_trade_approval(approval_id, order_data, preview_data, reason)
            msg_id = await send_approval_request(telegram_app, telegram_cfg, approval_id, text)
            if msg_id:
                set_telegram_message_id(approval_id, msg_id)
        return _approval_response_from_record(get_approval(approval_id) or rec)

    @mcp.tool(
        name="ibkr_request_environment_change",
        title="Request Environment Change",
        description=(
            "Send a request to the operator via Telegram to switch the IBKR connection "
            "between 'live' (real-money) and 'paper' (simulated) environments. "
            "Returns an approval_id — poll ibkr_check_approval_status until resolved. "
            "Once approved, you MUST call ibkr_execute_environment_change with the approval_id "
            "to actually apply the change. Switching environments will automatically engage "
            "safety locks (orders disabled, dry-run enabled)."
        ),
        annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def ibkr_request_environment_change(target_env: str, reason: str) -> ApprovalStatusResponse:
        if target_env not in ("live", "paper"):
            raise ValueError("target_env must be 'live' or 'paper'")

        timeout = telegram_cfg.live_unlock_timeout_seconds if telegram_cfg else 120

        if not config.approval_requires_telegram:
            rec = create_resolved_approval(
                "environment_change",
                {"reason": reason, "target_env": target_env},
                status="approved",
                resolve_note="Auto-approved because MCP_ORDER_APPROVAL_MODE=yolo",
            )
            return _approval_response_from_record(rec)

        _ensure_telegram_ready(config, telegram_cfg, telegram_app)

        rec = create_approval(
            "environment_change",
            {"reason": reason, "target_env": target_env},
            timeout_seconds=timeout,
        )
        approval_id = rec["approval_id"]

        if telegram_app is not None and telegram_cfg is not None:
            from mcp_server.telegram.bot import send_approval_request
            
            runtime = get_config()
            target_port = runtime.ibkr_live_port if target_env == "live" else runtime.ibkr_paper_port
            
            from mcp_server.telegram.notifications import format_environment_change
            text = format_environment_change(approval_id, target_env, reason, target_port)
            msg_id = await send_approval_request(telegram_app, telegram_cfg, approval_id, text)
            if msg_id:
                set_telegram_message_id(approval_id, msg_id)
        return _approval_response_from_record(get_approval(approval_id) or rec)

    @mcp.tool(
        name="ibkr_execute_environment_change",
        title="Execute Environment Change",
        description=(
            "Apply an approved environment change. Provide the approval_id from "
            "ibkr_request_environment_change. This applies safety locks to control.json "
            "and switches the active connection port in config.json. The connection "
            "will automatically reconnect on the next tool call."
        ),
        annotations=WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_execute_environment_change(approval_id: str, target_env: str) -> Dict[str, Any]:
        if target_env not in ("live", "paper"):
            raise ValueError("target_env must be 'live' or 'paper'")
            
        rec = get_approval(approval_id)
        if not rec:
            raise MCPToolError("NOT_FOUND", f"Approval '{approval_id}' not found.")
        
        status = rec["status"]
        if status != "approved":
            raise MCPToolError("INVALID_STATE", f"Approval is not in 'approved' state: {status}")
            
        approval_type = rec["approval_type"]
        if approval_type != "environment_change":
            raise MCPToolError("INVALID_STATE", f"Expected environment_change approval, got {approval_type}")
            
        request_data = rec["request_data"]
        approved_target = request_data.get("target_env")
        if approved_target != target_env:
            raise MCPToolError("INVALID_STATE", f"Approval was for {approved_target}, but tool called for {target_env}")

        # 1. Engage Safety Locks (control.json)
        current_control = load_control()
        updated_control = replace(
            current_control,
            orders_enabled=False,
            dry_run=True,
            block_reason=f"Safety lock engaged after environment switch to {target_env}",
            updated_by="mcp-server-env-switch"
        )
        write_control(updated_control)
        write_audit_entry(
            "env_switch_safety_lock",
            reason=f"Switched to {target_env}",
            approval_id=approval_id
        )

        # 2. Update Environment / Port (config.json)
        runtime = get_config()
        target_port = runtime.ibkr_live_port if target_env == "live" else runtime.ibkr_paper_port
        
        from ibkr_core.runtime_config import update_config_data
        update_config_data({"ibkr_port": target_port})
        
        # 3. Mark approval as used
        mark_used(approval_id)
        
        # 4. Invalidate current client to force reconnect
        await service.invalidate_client()

        return {
            "success": True,
            "targetEnv": target_env,
            "newPort": target_port,
            "safetyLocked": True,
            "message": "Environment changed successfully. Safety locks engaged (orders disabled, dry-run enabled). The next IBKR operation will use the new connection."
        }

    @mcp.tool(
        name="ibkr_check_approval_status",
        title="Check Approval Status",
        description=(
            "Poll the status of a pending trade, trade-intent, or execution-unlock approval. "
            "Status values: pending | approved | denied | expired | used."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_check_approval_status(approval_id: str) -> ApprovalStatusResponse:
        rec = get_approval(approval_id)
        if rec is None:
            raise MCPToolError("NOT_FOUND", f"Approval '{approval_id}' not found.")
        return _approval_response_from_record(rec)

    # -----------------------------------------------------------------------
    # Risk and impact assessment tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="ibkr_assess_order_impact",
        title="Assess Order Impact",
        description=(
            "Compute portfolio-level impact of a proposed order: concentration change, "
            "buying-power usage, margin impact, and max-loss estimate. "
            "Provide an OrderPreview from ibkr_preview_order for the best accuracy."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_assess_order_impact(
        order: OrderSpec,
        preview: Optional[OrderPreview] = None,
        account_id: Optional[str] = None,
    ) -> OrderImpactResponse:
        _ensure_fully_qualified_option(order.instrument)

        def operation(client):
            from ibkr_core.account import get_account_summary, get_positions
            from ibkr_core.market_data import get_quote

            summary = get_account_summary(client, account_id=account_id)
            positions = get_positions(client, account_id=account_id)
            quote = None
            try:
                quote = get_quote(order.instrument, client)
            except Exception:
                pass
            return summary, positions, quote

        summary, positions, quote = await call_core(operation)
        order_data = order.model_dump(mode="json", exclude_none=True)
        preview_data = preview.model_dump(mode="json", exclude_none=True) if preview else None
        account_data = summary.model_dump(mode="json", exclude_none=True)
        positions_data = [p.model_dump(mode="json", exclude_none=True) for p in positions]
        quote_data = quote.model_dump(mode="json", exclude_none=True) if quote else None

        result = assess_order_impact(order_data, preview_data, account_data, positions_data, quote_data)
        return OrderImpactResponse(**result)

    @mcp.tool(
        name="ibkr_get_portfolio_risk",
        title="Get Portfolio Risk",
        description=(
            "Compute portfolio-wide risk metrics: margin utilisation, "
            "concentration by symbol, unrealised P&L, and an overall risk level."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_portfolio_risk(account_id: Optional[str] = None) -> PortfolioRiskResponse:
        def operation(client):
            from ibkr_core.account import get_account_summary, get_positions

            return (
                get_account_summary(client, account_id=account_id),
                get_positions(client, account_id=account_id),
            )

        summary, positions = await call_core(operation)
        result = compute_portfolio_risk(
            summary.model_dump(mode="json", exclude_none=True),
            [p.model_dump(mode="json", exclude_none=True) for p in positions],
        )
        return PortfolioRiskResponse(**result)

    @mcp.tool(
        name="ibkr_check_position_limits",
        title="Check Position Limits",
        description=(
            "Validate a proposed order against the active agent profile's position limits. "
            "Returns passed=true when no violations are found."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_check_position_limits(
        order: OrderSpec,
        profile_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> PositionLimitsCheckResponse:
        _ensure_fully_qualified_option(order.instrument)
        profile = load_profile(profile_id or config.agent_profile_id)

        def operation(client):
            from ibkr_core.account import get_account_summary, get_positions

            return (
                get_account_summary(client, account_id=account_id),
                get_positions(client, account_id=account_id),
            )

        summary, positions = await call_core(operation)
        order_data = order.model_dump(mode="json", exclude_none=True)
        violations = validate_order_against_profile(
            order_data,
            profile,
            account_data=summary.model_dump(mode="json", exclude_none=True),
            positions_data=[p.model_dump(mode="json", exclude_none=True) for p in positions],
        )
        return PositionLimitsCheckResponse(
            passed=len(violations) == 0,
            violations=violations,
            profileId=profile.get("profile_id", "unknown"),
        )

    # -----------------------------------------------------------------------
    # Agent profile tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="ibkr_get_agent_profile",
        title="Get Agent Profile",
        description=(
            "Load and return the active agent trading profile with its constraints. "
            "Use profile_id to fetch a specific profile, or omit to load the default."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_agent_profile(
        profile_id: Optional[str] = None,
    ) -> AgentProfileResponse:
        profile = load_profile(profile_id or config.agent_profile_id)
        return AgentProfileResponse(
            profileId=profile.get("profile_id", "unknown"),
            description=profile.get("description"),
            allowedSecurityTypes=profile.get("allowed_security_types"),
            allowedOrderTypes=profile.get("allowed_order_types"),
            allowedSymbols=profile.get("allowed_symbols"),
            blockedSymbols=profile.get("blocked_symbols", []),
            maxPositionSizePct=profile.get("max_position_size_pct"),
            maxPositionNotional=profile.get("max_position_notional"),
            maxOrderQuantity=profile.get("max_order_quantity"),
            maxDailyOrders=profile.get("max_daily_orders"),
            maxDailyLoss=profile.get("max_daily_loss"),
            requireTradeApproval=profile.get("require_trade_approval", True),
            requireLiveTradingApproval=profile.get("require_live_trading_approval", True),
            allowOptions=profile.get("allow_options", True),
            allowShortSelling=profile.get("allow_short_selling", True),
            notes=profile.get("notes"),
            source=profile.get("_source"),
        )

    @mcp.tool(
        name="ibkr_validate_against_profile",
        title="Validate Against Profile",
        description=(
            "Check a proposed order against the agent's trading profile constraints. "
            "Returns passed=true and an empty violations list when the order is within limits."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_validate_against_profile(
        order: OrderSpec,
        profile_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> ProfileValidationResponse:
        _ensure_fully_qualified_option(order.instrument)
        profile = load_profile(profile_id or config.agent_profile_id)

        def operation(client):
            from ibkr_core.account import get_account_summary, get_positions

            return (
                get_account_summary(client, account_id=account_id),
                get_positions(client, account_id=account_id),
            )

        summary, positions = await call_core(operation)
        order_data = order.model_dump(mode="json", exclude_none=True)
        violations = validate_order_against_profile(
            order_data,
            profile,
            account_data=summary.model_dump(mode="json", exclude_none=True),
            positions_data=[p.model_dump(mode="json", exclude_none=True) for p in positions],
        )
        instrument = order.instrument
        return ProfileValidationResponse(
            passed=len(violations) == 0,
            violations=violations,
            profileId=profile.get("profile_id", "unknown"),
            symbol=instrument.symbol,
            side=order.side,
            quantity=order.quantity,
        )

    # -----------------------------------------------------------------------
    # Session activity and audit log tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="ibkr_get_session_activity",
        title="Get Session Activity",
        description=(
            "Summarise trading activity for today's session: orders placed, filled, "
            "cancelled, and pending — with a list of the most recent orders."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_session_activity() -> SessionActivityResponse:
        from ibkr_core.persistence import get_db_path

        today = datetime.now(timezone.utc).date().isoformat()
        db_path = get_db_path()

        def _query() -> List[Dict[str, Any]]:
            if not Path(db_path).exists():
                return []
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT order_id, symbol, side, quantity, order_type, status,
                           placed_at, ibkr_order_id
                      FROM order_history
                     WHERE DATE(placed_at) = ?
                     ORDER BY placed_at DESC
                     LIMIT 50
                    """,
                    (today,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

        rows = await asyncio.get_event_loop().run_in_executor(None, _query)

        placed = len(rows)
        filled = sum(1 for r in rows if r["status"] == "FILLED")
        cancelled = sum(1 for r in rows if r["status"] in {"CANCELLED", "EXPIRED"})
        pending = sum(1 for r in rows if r["status"] in {"SUBMITTED", "PARTIALLY_FILLED", "PENDING_SUBMIT"})

        recent = [
            SessionOrderSummary(
                orderId=r["order_id"],
                symbol=r["symbol"],
                side=r["side"],
                quantity=float(r["quantity"]),
                orderType=r["order_type"],
                status=r["status"],
                placedAt=r["placed_at"],
                ibkrOrderId=r.get("ibkr_order_id"),
            )
            for r in rows[:20]
        ]

        return SessionActivityResponse(
            sessionDate=today,
            ordersPlaced=placed,
            ordersFilled=filled,
            ordersCancelled=cancelled,
            ordersPending=pending,
            recentOrders=recent,
        )

    @mcp.tool(
        name="ibkr_get_audit_log",
        title="Get Audit Log",
        description=(
            "Query the SQLite audit log. Filter by event_type, symbol, account_id, "
            "or date range. Returns up to limit entries (default 50, max 200)."
        ),
        annotations=READ_TOOL,
        structured_output=True,
    )
    async def ibkr_get_audit_log(
        event_type: Optional[str] = None,
        symbol: Optional[str] = None,
        account_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 50,
    ) -> AuditLogResponse:
        from ibkr_core.persistence import get_db_path

        limit = max(1, min(limit, 200))
        db_path = get_db_path()

        def _query() -> List[Dict[str, Any]]:
            if not Path(db_path).exists():
                return []
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                clauses: List[str] = []
                params: List[Any] = []
                if event_type:
                    clauses.append("event_type = ?")
                    params.append(event_type)
                if account_id:
                    clauses.append("account_id = ?")
                    params.append(account_id)
                if since:
                    clauses.append("timestamp >= ?")
                    params.append(since)
                if until:
                    clauses.append("timestamp <= ?")
                    params.append(until)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                rows = conn.execute(
                    f"""
                    SELECT id, correlation_id, timestamp, event_type, event_data, account_id
                      FROM audit_log
                    {where}
                     ORDER BY timestamp DESC
                     LIMIT ?
                    """,
                    params + [limit],
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

        rows = await asyncio.get_event_loop().run_in_executor(None, _query)

        entries: List[AuditLogEntry] = []
        for r in rows:
            try:
                event_data = json.loads(r.get("event_data") or "{}")
            except Exception:
                event_data = {}
            # Filter by symbol post-fetch if requested
            if symbol and event_data.get("symbol") != symbol:
                continue
            entries.append(
                AuditLogEntry(
                    id=r["id"],
                    correlationId=r.get("correlation_id"),
                    timestamp=r["timestamp"],
                    eventType=r["event_type"],
                    eventData=event_data,
                    accountId=r.get("account_id"),
                )
            )

        filters: Dict[str, Any] = {}
        if event_type:
            filters["event_type"] = event_type
        if symbol:
            filters["symbol"] = symbol
        if account_id:
            filters["account_id"] = account_id
        if since:
            filters["since"] = since
        if until:
            filters["until"] = until

        return AuditLogResponse(
            totalReturned=len(entries),
            entries=entries,
            queryFilters=filters,
        )

    # -----------------------------------------------------------------------
    # Emergency stop
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="ibkr_emergency_stop",
        title="Emergency Stop",
        description=(
            "PANIC BUTTON: cancel ALL open orders, disable order placement in control.json, "
            "and send a Telegram alert. Use only in emergency situations."
        ),
        annotations=WRITE_TOOL,
        structured_output=True,
    )
    async def ibkr_emergency_stop(reason: str = "emergency stop") -> EmergencyStopResponse:
        errors: List[str] = []
        cancelled_ids: List[str] = []

        # 1. Get all open orders
        try:
            raw_orders = await call_core(lambda client: get_open_orders(client))
            order_ids = [o["order_id"] for o in raw_orders if o.get("order_id")]
        except Exception as exc:
            errors.append(f"Failed to list open orders: {exc}")
            order_ids = []

        # 2. Cancel them all
        if order_ids:
            try:
                await call_core(lambda client: cancel_order_set(client, order_ids))
                cancelled_ids = order_ids
            except Exception as exc:
                errors.append(f"Failed to cancel orders: {exc}")

        # 3. Disable orders in control.json
        trading_disabled = False
        try:
            current = load_control()
            if current.orders_enabled or current.dry_run is False or current.block_reason != reason:
                updated = replace(
                    current,
                    orders_enabled=False,
                    dry_run=True,
                    block_reason=reason,
                )
                write_control(updated)
                write_audit_entry(
                    action="EMERGENCY_STOP",
                    reason=reason,
                    updated_fields="ordersEnabled,dryRun,blockReason",
                    source="mcp",
                )
                reset_config()
                await service.invalidate_client()
            trading_disabled = True
        except Exception as exc:
            errors.append(f"Failed to disable trading: {exc}")

        # 4. Send Telegram notification
        tg_notified = False
        account_id = "unknown"
        try:
            summary = await call_core(lambda client: get_account_summary(client))
            account_id = summary.accountId
        except Exception:
            pass

        if telegram_app is not None and telegram_cfg is not None:
            from mcp_server.telegram.bot import send_notification

            text = format_emergency_stop(len(cancelled_ids), account_id)
            msg_id = await send_notification(telegram_app, telegram_cfg, text)
            tg_notified = msg_id is not None

        msg = (
            f"Emergency stop executed: {len(cancelled_ids)} order(s) cancelled, "
            f"trading {'disabled' if trading_disabled else 'disable-FAILED'}."
        )
        if errors:
            msg += f" Errors: {'; '.join(errors)}"

        return EmergencyStopResponse(
            success=len(errors) == 0,
            ordersCancelled=len(cancelled_ids),
            cancelledOrderIds=cancelled_ids,
            tradingDisabled=trading_disabled,
            telegramNotified=tg_notified,
            message=msg,
        )

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
                "Only ordersEnabled, dryRun, and blockReason are supported."
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

            unsupported_fields: list[str] = []
            if request.tradingMode is not None:
                unsupported_fields.append("tradingMode")
            if request.liveTradingOverrideFile is not None:
                unsupported_fields.append("liveTradingOverrideFile")
            if request.liveEnableConfirmation is not None:
                unsupported_fields.append("liveEnableConfirmation")
            if unsupported_fields:
                raise MCPToolError(
                    "VALIDATION_ERROR",
                    "Legacy live-trading fields are not supported in mm-ibkr-mcp. "
                    "Update ordersEnabled, dryRun, and blockReason instead.",
                    {"unsupportedFields": unsupported_fields},
                )

            updated = replace(current)
            updated_fields: list[str] = []

            if (
                request.ordersEnabled is not None
                and request.ordersEnabled != current.orders_enabled
            ):
                updated.orders_enabled = request.ordersEnabled
                updated_fields.append("ordersEnabled")
            if request.dryRun is not None and request.dryRun != current.dry_run:
                updated.dry_run = request.dryRun
                updated_fields.append("dryRun")
            if request.blockReason is not None:
                normalized_reason = request.blockReason.strip() or None
                if normalized_reason != current.block_reason:
                    updated.block_reason = normalized_reason
                    updated_fields.append("blockReason")

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
        description="Checklist for safe order preparation with human-in-the-loop approval.",
    )
    def pre_trade_checklist() -> str:
        return (
            "Before placing any order, complete ALL of the following steps:\n"
            "1. ibkr_health + ibkr_get_trading_status + ibkr_get_schedule_status — verify "
            "connectivity and runtime safety.\n"
            "2. If needed, ibkr_request_environment_change to switch between live and paper. "
            "Once approved, call ibkr_execute_environment_change.\n"
            "3. ibkr_get_agent_profile — review trading constraints for this session.\n"
            "4. ibkr_get_portfolio_risk — understand current portfolio exposure.\n"
            "4. ibkr_resolve_contract — fully qualify the instrument.\n"
            "5. For options: ibkr_get_option_chain → ibkr_get_option_snapshot.\n"
            "6. ibkr_preview_order or ibkr_preview_order_basket — estimate execution, "
            "margin, and commission.\n"
            "7. ibkr_assess_order_impact — compute concentration and buying-power impact.\n"
            "8. ibkr_validate_against_profile — confirm the order is within profile limits.\n"
            "9. For a single order: request approval only when "
            "MCP_ORDER_APPROVAL_MODE=telegram, then call ibkr_place_order.\n"
            "10. For a basket: ibkr_create_trade_intent, request approval only when "
            "MCP_ORDER_APPROVAL_MODE=telegram, then ibkr_submit_trade_intent.\n"
            "11. After submission, use ibkr_get_order_status or "
            "ibkr_reconcile_trade_intent to confirm execution state.\n"
            "STOP if any step returns errors or warnings that cannot be resolved.\n"
            "Do not move out of dry-run or enable orders unless the user explicitly instructs it."
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
            "5. If ordersEnabled=true and dryRun=false, confirm the user explicitly requested real execution."
        )

    return mcp


mcp = create_mcp_server()


def main() -> None:
    """Run the MCP server using the configured transport."""
    server = create_mcp_server()
    server.run(transport=get_mcp_config().transport)


if __name__ == "__main__":
    main()
