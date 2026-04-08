"""Transport and hosted MCP tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest
from httpx import ASGITransport
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ibkr_core.control import load_control
from mcp_server.config import MCPConfig
from mcp_server.main import create_mcp_server

HTTP_CONFIG = MCPConfig(
    transport="streamable-http",
    auth_token="test-token",
    public_base_url="http://testserver",
    auth_issuer_url="http://testserver",
    allowed_hosts=["testserver"],
    allowed_origins=["http://allowed-origin"],
)

AUTH_HEADERS = {
    "Authorization": "Bearer test-token",
    "Origin": "http://allowed-origin",
}


@asynccontextmanager
async def open_http_client(server, headers: dict[str, str] | None = None):
    """Open an ASGI-backed HTTP client with lifespan support."""
    app = server.streamable_http_app()
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=headers or {},
        ) as client:
            yield client


@asynccontextmanager
async def open_mcp_session(server):
    """Open an authenticated MCP session against the ASGI app."""
    async with open_http_client(server, headers=AUTH_HEADERS) as http_client:
        async with streamable_http_client("http://testserver/mcp", http_client=http_client) as streams:
            async with ClientSession(*streams[:2]) as session:
                await session.initialize()
                yield session


@pytest.mark.asyncio
async def test_streamable_http_requires_bearer_auth():
    """Hosted MCP endpoint should reject unauthenticated requests."""
    server = create_mcp_server(HTTP_CONFIG)

    async with open_http_client(server, headers={"Origin": "http://allowed-origin"}) as client:
        response = await client.post("/mcp", json={})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_streamable_http_validates_origin():
    """Hosted MCP endpoint should reject unexpected Origin headers."""
    server = create_mcp_server(HTTP_CONFIG)

    async with open_http_client(
        server,
        headers={"Authorization": "Bearer test-token", "Origin": "http://bad-origin"},
    ) as client:
        response = await client.post("/mcp", json={})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_streamable_http_initialize_list_and_call():
    """Remote MCP clients should be able to initialize, list tools, and call tools."""
    server = create_mcp_server(HTTP_CONFIG)

    async with open_mcp_session(server) as session:
        tools = await session.list_tools()
        result = await session.call_tool("get_trading_status")

    tool_names = [tool.name for tool in tools.tools]
    assert "get_trading_status" in tool_names
    assert result.isError is False
    assert result.structuredContent["tradingMode"] == "paper"
    assert result.structuredContent["ordersEnabled"] is False


@pytest.mark.asyncio
async def test_place_order_requires_client_order_id():
    """The MCP layer should enforce clientOrderId before calling the core order path."""
    server = create_mcp_server(HTTP_CONFIG)
    payload = {
        "order": {
            "instrument": {
                "symbol": "AAPL",
                "securityType": "STK",
                "exchange": "SMART",
                "currency": "USD",
            },
            "side": "BUY",
            "quantity": 1,
            "orderType": "MKT",
        }
    }

    async with open_mcp_session(server) as session:
        result = await session.call_tool("place_order", payload)

    assert result.isError is True
    assert "clientOrderId is required" in result.content[0].text


@pytest.mark.asyncio
async def test_admin_update_rejects_legacy_live_fields():
    """Legacy live-trading control fields should be rejected explicitly."""
    server = create_mcp_server(
        replace(HTTP_CONFIG, enable_admin_tools=True)
    )
    payload = {
        "request": {
            "reason": "reject legacy fields",
            "expectedCurrentState": {
                "tradingMode": "paper",
                "ordersEnabled": False,
                "dryRun": True,
                "liveTradingOverrideFile": None,
            },
            "tradingMode": "live",
        }
    }

    async with open_mcp_session(server) as session:
        result = await session.call_tool("admin_update_trading_control", payload)

    assert result.isError is True
    assert "Legacy live-trading fields are not supported" in result.content[0].text


@pytest.mark.asyncio
async def test_admin_update_can_toggle_paper_orders():
    """Admin control updates should support safe paper-mode changes with compare-and-swap semantics."""
    server = create_mcp_server(
        replace(HTTP_CONFIG, enable_admin_tools=True)
    )
    payload = {
        "request": {
            "reason": "enable paper order testing",
            "expectedCurrentState": {
                "tradingMode": "paper",
                "ordersEnabled": False,
                "dryRun": True,
                "liveTradingOverrideFile": None,
            },
            "ordersEnabled": True,
        }
    }

    async with open_mcp_session(server) as session:
        result = await session.call_tool("admin_update_trading_control", payload)

    assert result.isError is False
    assert result.structuredContent["currentState"]["ordersEnabled"] is True
    assert load_control().orders_enabled is True
