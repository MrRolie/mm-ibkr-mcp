"""Tests for approval auto-resolution by clientOrderId."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from ibkr_core.config import reset_config
from ibkr_core.control import ControlState, write_control
from ibkr_core.runtime_config import load_config_data, write_config_data
from mcp_server.telegram.approval import (
    create_resolved_approval,
    find_approved_trade_by_client_order_id,
    mark_used,
)


@pytest.fixture(autouse=True)
def temp_runtime_env():
    """Point SQLite state at a temporary directory."""
    old_env = {
        key: os.environ.get(key)
        for key in ("MM_IBKR_CONFIG_PATH", "MM_IBKR_CONTROL_DIR")
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / "config.json"
        control_dir = temp_path / "control"

        os.environ["MM_IBKR_CONFIG_PATH"] = str(config_path)
        os.environ["MM_IBKR_CONTROL_DIR"] = str(control_dir)
        reset_config()

        config_data = load_config_data(create_if_missing=True)
        config_data["control_dir"] = str(control_dir)
        config_data["data_storage_dir"] = str(temp_path / "storage")
        config_data["log_dir"] = str(temp_path / "storage" / "logs")
        config_data["audit_db_path"] = str(temp_path / "storage" / "audit.db")
        write_config_data(config_data, path=config_path)
        write_control(ControlState(), base_dir=control_dir)

        yield

    reset_config()
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _trade_request_data(client_order_id: str) -> Dict[str, Any]:
    return {
        "order": {
            "instrument": {"symbol": "NVDA", "securityType": "STK", "currency": "USD"},
            "side": "SELL",
            "quantity": 5,
            "orderType": "LMT",
            "limitPrice": 181.84,
            "tif": "DAY",
            "clientOrderId": client_order_id,
        },
        "reason": "test",
    }


class TestFindApprovedTradeByClientOrderId:
    def test_finds_approved_matching_trade(self):
        """Returns the approval when clientOrderId matches and status=approved."""
        rec = create_resolved_approval(
            "trade",
            _trade_request_data("nvda-sell-001"),
            status="approved",
            resolve_note="test approval",
        )
        result = find_approved_trade_by_client_order_id("nvda-sell-001")
        assert result is not None
        assert result["approval_id"] == rec["approval_id"]
        assert result["status"] == "approved"

    def test_returns_none_when_no_match(self):
        """Returns None when no approval exists for the given clientOrderId."""
        result = find_approved_trade_by_client_order_id("nonexistent-id")
        assert result is None

    def test_returns_none_for_wrong_client_order_id(self):
        """Returns None when approvals exist but for a different clientOrderId."""
        create_resolved_approval(
            "trade",
            _trade_request_data("other-order-999"),
            status="approved",
        )
        result = find_approved_trade_by_client_order_id("nvda-sell-001")
        assert result is None

    def test_returns_none_for_used_approval(self):
        """Does not return approvals that have already been consumed."""
        rec = create_resolved_approval(
            "trade",
            _trade_request_data("nvda-sell-used"),
            status="approved",
        )
        mark_used(rec["approval_id"])
        result = find_approved_trade_by_client_order_id("nvda-sell-used")
        assert result is None

    def test_returns_none_for_denied_approval(self):
        """Does not return denied approvals."""
        create_resolved_approval(
            "trade",
            _trade_request_data("nvda-sell-denied"),
            status="denied",
        )
        result = find_approved_trade_by_client_order_id("nvda-sell-denied")
        assert result is None

    def test_returns_none_for_non_trade_approval_type(self):
        """Does not return non-trade approvals even if clientOrderId matches."""
        create_resolved_approval(
            "environment_change",
            {"order": {"clientOrderId": "env-change-001"}, "target_env": "live"},
            status="approved",
        )
        result = find_approved_trade_by_client_order_id("env-change-001")
        assert result is None

    def test_returns_most_recent_when_multiple_match(self):
        """When multiple approved records match, returns the most recent."""
        create_resolved_approval(
            "trade",
            _trade_request_data("multi-match"),
            status="approved",
            resolve_note="first",
        )
        second = create_resolved_approval(
            "trade",
            _trade_request_data("multi-match"),
            status="approved",
            resolve_note="second",
        )
        result = find_approved_trade_by_client_order_id("multi-match")
        assert result is not None
        assert result["approval_id"] == second["approval_id"]

    def test_returns_none_when_client_order_id_missing_in_request(self):
        """Returns None when the approval has no clientOrderId in request_data."""
        create_resolved_approval(
            "trade",
            {"order": {"symbol": "AAPL"}, "reason": "no clientOrderId"},
            status="approved",
        )
        result = find_approved_trade_by_client_order_id("any-id")
        assert result is None
