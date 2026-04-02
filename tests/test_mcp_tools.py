"""Tests for the modern MCP tool/resource/prompt surface."""

from __future__ import annotations

import pytest

from mcp_server.config import MCPConfig
from mcp_server.main import create_mcp_server

CANONICAL_TOOL_NAMES = [
    # Health / status
    "ibkr_health",
    "ibkr_get_trading_status",
    "ibkr_get_schedule_status",
    # Market data
    "ibkr_resolve_contract",
    "ibkr_get_quote",
    "ibkr_get_historical_bars",
    # Account
    "ibkr_get_account_summary",
    "ibkr_get_positions",
    "ibkr_get_pnl",
    # Orders
    "ibkr_list_open_orders",
    "ibkr_get_order_status",
    "ibkr_get_order_set_status",
    "ibkr_preview_order",
    "ibkr_place_order",
    "ibkr_cancel_order",
    "ibkr_cancel_order_set",
    # Options
    "ibkr_get_option_chain",
    "ibkr_get_option_snapshot",
    # Telegram human-in-the-loop
    "ibkr_notify",
    "ibkr_request_trade_approval",
    "ibkr_request_live_trading_unlock",
    "ibkr_check_approval_status",
    # Risk and impact
    "ibkr_assess_order_impact",
    "ibkr_get_portfolio_risk",
    "ibkr_check_position_limits",
    # Agent profiles
    "ibkr_get_agent_profile",
    "ibkr_validate_against_profile",
    # Session / audit
    "ibkr_get_session_activity",
    "ibkr_get_audit_log",
    # Emergency
    "ibkr_emergency_stop",
]


@pytest.mark.asyncio
async def test_mcp_tool_surface_matches_contract():
    """The default server should advertise the exact canonical tool list."""
    server = create_mcp_server(MCPConfig())
    tools = await server.list_tools()

    assert [tool.name for tool in tools] == CANONICAL_TOOL_NAMES


@pytest.mark.asyncio
async def test_mcp_tools_have_annotations_and_output_schemas():
    """Every exposed tool should publish annotations and structured output schema."""
    server = create_mcp_server(MCPConfig())
    tools = {tool.name: tool for tool in await server.list_tools()}

    for name in CANONICAL_TOOL_NAMES:
        assert tools[name].outputSchema is not None
        assert tools[name].title
        assert tools[name].description
        assert tools[name].annotations is not None

    assert tools["ibkr_health"].annotations.readOnlyHint is True
    assert tools["ibkr_health"].annotations.idempotentHint is True
    assert tools["ibkr_place_order"].annotations.destructiveHint is True
    assert tools["ibkr_place_order"].annotations.idempotentHint is True
    assert tools["ibkr_cancel_order"].annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_admin_tools_are_opt_in():
    """Admin tools should appear only when explicitly enabled."""
    server = create_mcp_server(MCPConfig(enable_admin_tools=True))
    tools = {tool.name for tool in await server.list_tools()}

    assert "ibkr_admin_verify_gateway" in tools
    assert "ibkr_admin_update_trading_control" in tools


@pytest.mark.asyncio
async def test_legacy_aliases_are_opt_in():
    """Legacy non-namespaced aliases should stay hidden by default."""
    default_tools = {tool.name for tool in await create_mcp_server(MCPConfig()).list_tools()}
    legacy_tools = {
        tool.name
        for tool in await create_mcp_server(MCPConfig(enable_legacy_aliases=True)).list_tools()
    }

    assert "get_quote" not in default_tools
    assert {"get_quote", "get_historical_data", "get_account_status"}.issubset(legacy_tools)


@pytest.mark.asyncio
async def test_resources_and_prompts_are_registered():
    """Claude Code discoverability surfaces should be registered."""
    server = create_mcp_server(MCPConfig())

    resources = await server.list_resources()
    resource_templates = await server.list_resource_templates()
    prompts = await server.list_prompts()

    assert {str(resource.uri) for resource in resources} == {
        "ibkr://status/overview",
        "ibkr://account/default/summary",
        "ibkr://account/default/positions",
        "ibkr://orders/open",
    }
    assert {template.uriTemplate for template in resource_templates} == {
        "ibkr://options/chain/{symbol}"
    }
    assert {prompt.name for prompt in prompts} == {
        "pre_trade_checklist",
        "option_contract_selection",
        "order_review",
    }
