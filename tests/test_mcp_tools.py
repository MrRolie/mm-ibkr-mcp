"""Tests for the modern MCP tool/resource/prompt surface."""

from __future__ import annotations

import pytest

from mcp_server.config import MCPConfig
from mcp_server.main import create_mcp_server

CANONICAL_TOOL_NAMES = [
    # Health / status
    "health",
    "get_trading_status",
    "get_schedule_status",
    # Market data
    "resolve_contract",
    "get_quote",
    "get_historical_bars",
    # Account
    "get_account_summary",
    "get_positions",
    "get_pnl",
    # Orders
    "list_open_orders",
    "get_order_status",
    "get_order_set_status",
    "preview_order",
    "place_order",
    "cancel_order",
    "cancel_order_set",
    "preview_order_basket",
    "create_trade_intent",
    "request_trade_intent_approval",
    "submit_trade_intent",
    "get_trade_intent",
    "list_trade_intents",
    "reconcile_trade_intent",
    "cancel_trade_intent",
    # Options
    "get_option_chain",
    "get_option_snapshot",
    # Telegram human-in-the-loop
    "notify",
    "request_trade_approval",
    "request_environment_change",
    "execute_environment_change",
    "check_approval_status",
    # Risk and impact
    "assess_order_impact",
    "get_portfolio_risk",
    "check_position_limits",
    # Agent profiles
    "get_agent_profile",
    "validate_against_profile",
    # Session / audit
    "get_session_activity",
    "get_audit_log",
    # Emergency
    "emergency_stop",
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

    assert tools["health"].annotations.readOnlyHint is True
    assert tools["health"].annotations.idempotentHint is True
    assert tools["place_order"].annotations.destructiveHint is True
    assert tools["place_order"].annotations.idempotentHint is True
    assert tools["cancel_order"].annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_admin_tools_are_opt_in():
    """Admin tools should appear only when explicitly enabled."""
    server = create_mcp_server(MCPConfig(enable_admin_tools=True))
    tools = {tool.name for tool in await server.list_tools()}

    assert "admin_verify_gateway" in tools
    assert "admin_update_trading_control" in tools


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
