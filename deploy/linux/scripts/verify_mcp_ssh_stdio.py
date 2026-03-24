#!/usr/bin/env python3
"""Verify local or SSH stdio MCP access for mm-ibkr-gateway."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def get_repo_dir() -> Path:
    return Path(__file__).resolve().parents[3]


def get_default_wrapper() -> Path:
    return Path(__file__).resolve().with_name("run_mcp_stdio.sh")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ssh-target",
        help="Optional SSH target such as ibkr-mcp@ib-host. If omitted, verify the local wrapper directly.",
    )
    parser.add_argument(
        "--remote-command",
        help="Optional remote command path for SSH mode. Leave unset when using a forced command.",
    )
    parser.add_argument(
        "--local-command",
        default=str(get_default_wrapper()),
        help="Local wrapper command to execute when --ssh-target is not provided.",
    )
    parser.add_argument(
        "--identity-file",
        help="Optional SSH identity file for --ssh-target mode.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Optional SSH port for --ssh-target mode.",
    )
    parser.add_argument(
        "--gateway-checks",
        action="store_true",
        help="Also run gateway-dependent checks for health, paper preview, and option discovery.",
    )
    parser.add_argument(
        "--underlying-symbol",
        default="AAPL",
        help="Underlying symbol to use for option discovery checks.",
    )
    parser.add_argument(
        "--verbose-server-log",
        action="store_true",
        help="Mirror server stderr during verification instead of suppressing it.",
    )
    return parser.parse_args()


def build_server_params(args: argparse.Namespace) -> StdioServerParameters:
    repo_dir = get_repo_dir()
    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "IBKR_MCP_PROJECT_DIR": str(repo_dir),
    }

    project_venv = repo_dir / ".venv"
    if project_venv.exists():
        env["IBKR_MCP_VENV_PATH"] = str(project_venv)

    if args.ssh_target:
        ssh_args: list[str] = ["-T"]
        if args.identity_file:
            ssh_args.extend(["-i", args.identity_file])
        if args.port:
            ssh_args.extend(["-p", str(args.port)])
        ssh_args.append(args.ssh_target)
        if args.remote_command:
            ssh_args.append(args.remote_command)
        return StdioServerParameters(command="ssh", args=ssh_args, env=env, cwd=str(repo_dir))

    return StdioServerParameters(
        command=args.local_command,
        env=env,
        cwd=str(repo_dir),
    )


def require_success(result: Any, label: str) -> dict[str, Any]:
    if result.isError:
        message = result.content[0].text if result.content else f"{label} failed"
        raise RuntimeError(message)
    return result.structuredContent or {}


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    server = build_server_params(args)
    summary: dict[str, Any] = {
        "mode": "ssh" if args.ssh_target else "local",
        "toolCount": 0,
        "checks": {},
    }

    errlog: io.TextIOBase | None = None
    if args.verbose_server_log:
        errlog = sys.stderr
        errlog_context = contextlib.nullcontext(errlog)
    else:
        errlog_context = open(os.devnull, "w", encoding="utf-8")

    with errlog_context as active_errlog:
        async with stdio_client(server, errlog=active_errlog) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                summary["toolCount"] = len(tools.tools)

                trading_status = require_success(
                    await session.call_tool("ibkr_get_trading_status"),
                    "ibkr_get_trading_status",
                )
                summary["checks"]["trading_status"] = {
                    "tradingMode": trading_status["tradingMode"],
                    "ordersEnabled": trading_status["ordersEnabled"],
                    "effectiveDryRun": trading_status["effectiveDryRun"],
                }

                if args.gateway_checks:
                    health = require_success(await session.call_tool("ibkr_health"), "ibkr_health")
                    summary["checks"]["health"] = {
                        "status": health["status"],
                        "ibkrConnected": health["ibkrConnected"],
                        "gatewayHost": health.get("gatewayHost"),
                        "gatewayPort": health.get("gatewayPort"),
                    }

                    preview = require_success(
                        await session.call_tool(
                            "ibkr_preview_order",
                            {
                                "order": {
                                    "instrument": {
                                        "symbol": args.underlying_symbol,
                                        "securityType": "STK",
                                        "exchange": "SMART",
                                        "currency": "USD",
                                    },
                                    "side": "BUY",
                                    "quantity": 1,
                                    "orderType": "MKT",
                                }
                            },
                        ),
                        "ibkr_preview_order",
                    )
                    summary["checks"]["paper_preview"] = {
                        "status": preview.get("status"),
                        "estimatedCommission": preview.get("estimatedCommission"),
                    }

                    option_chain = require_success(
                        await session.call_tool(
                            "ibkr_get_option_chain",
                            {
                                "underlying": {
                                    "symbol": args.underlying_symbol,
                                    "securityType": "STK",
                                    "exchange": "SMART",
                                    "currency": "USD",
                                },
                                "strike_count": 1,
                                "max_candidates": 1,
                            },
                        ),
                        "ibkr_get_option_chain",
                    )
                    candidates = option_chain.get("candidates", [])
                    if not candidates:
                        raise RuntimeError("ibkr_get_option_chain returned no option candidates")

                    option_snapshot = require_success(
                        await session.call_tool(
                            "ibkr_get_option_snapshot",
                            {"instrument": candidates[0]["instrument"]},
                        ),
                        "ibkr_get_option_snapshot",
                    )
                    summary["checks"]["option_snapshot"] = {
                        "symbol": option_snapshot["contract"]["symbol"],
                        "expiry": option_snapshot["contract"]["expiry"],
                        "right": option_snapshot["contract"]["right"],
                        "strike": option_snapshot["contract"]["strike"],
                    }

    return summary


def main() -> None:
    args = parse_args()
    result = asyncio.run(verify(args))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
