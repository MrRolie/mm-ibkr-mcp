"""Tests for the SSH stdio MCP entrypoint wrapper."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ibkr_core.control import ControlState, write_control
from ibkr_core.runtime_config import load_config_data, write_config_data

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "deploy/linux/scripts/run_mcp_stdio.sh"


@pytest.mark.asyncio
async def test_stdio_entrypoint_initializes_and_lists_tools():
    """The SSH wrapper should boot the MCP server and expose the canonical tools."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / "config.json"
        control_dir = temp_path / "control"
        config_data = load_config_data(create_if_missing=True)
        config_data["control_dir"] = str(control_dir)
        config_data["data_storage_dir"] = str(temp_path / "storage")
        config_data["log_dir"] = str(temp_path / "storage" / "logs")
        config_data["audit_db_path"] = str(temp_path / "storage" / "audit.db")
        config_data["watchdog_log_dir"] = str(temp_path / "logs")
        write_config_data(config_data, path=config_path)
        write_control(ControlState(), base_dir=control_dir)

        server = StdioServerParameters(
            command=str(ENTRYPOINT),
            cwd=str(REPO_ROOT),
            env={
                "HOME": os.environ.get("HOME", ""),
                "PATH": os.environ.get("PATH", ""),
                "PYTHONUNBUFFERED": "1",
                "IBKR_MCP_PROJECT_DIR": str(REPO_ROOT),
                "IBKR_MCP_VENV_PATH": str(REPO_ROOT / ".venv"),
                "MM_IBKR_CONFIG_PATH": str(config_path),
                "MM_IBKR_CONTROL_DIR": str(control_dir),
            },
        )

        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("ibkr_get_trading_status")

    tool_names = {tool.name for tool in tools.tools}
    assert "ibkr_get_trading_status" in tool_names
    assert "ibkr_get_option_snapshot" in tool_names
    assert result.isError is False
    assert result.structuredContent["tradingMode"] == "paper"
    assert result.structuredContent["ordersEnabled"] is False
