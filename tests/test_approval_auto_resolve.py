"""Tests for approval auto-resolution by clientOrderId and order params."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from ibkr_core.config import reset_config
from ibkr_core.control import ControlState, write_control
from ibkr_core.runtime_config import load_config_data, write_config_data
from mcp_server.telegram.approval import (
    _connect,
    _expire_stale,
    create_resolved_approval,
    find_approved_trade_by_client_order_id,
    find_approved_trade_by_order_params,
    get_approval,
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


def _order_params_request(
    symbol: str = "NVDA",
    security_type: str = "STK",
    side: str = "SELL",
    quantity: float = 5.0,
    order_type: str = "MKT",
) -> Dict[str, Any]:
    """Return a minimal trade request_data dict for order-params tests."""
    return {
        "order": {
            "instrument": {
                "symbol": symbol,
                "securityType": security_type,
                "currency": "USD",
            },
            "side": side,
            "quantity": quantity,
            "orderType": order_type,
        },
        "reason": "test order-params fallback",
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


class TestFindApprovedTradeByOrderParams:
    """Tests for the defence-in-depth order-params auto-resolution fallback."""

    def test_finds_approved_matching_params(self):
        """Returns the approval when all order params match exactly."""
        rec = create_resolved_approval(
            "trade",
            _order_params_request(symbol="NVDA", side="SELL", quantity=5.0, order_type="MKT"),
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA",
            security_type="STK",
            side="SELL",
            quantity=5.0,
            order_type="MKT",
        )
        assert result is not None
        assert result["approval_id"] == rec["approval_id"]

    def test_finds_approved_matching_params_case_insensitive(self):
        """Matches case insensitively on symbol, security_type, side, and order_type."""
        rec = create_resolved_approval(
            "trade",
            _order_params_request(symbol="nvda", side="sell", quantity=5.0, order_type="mkt"),
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA",
            security_type="STK",
            side="SELL",
            quantity=5.0,
            order_type="MKT",
        )
        assert result is not None
        assert result["approval_id"] == rec["approval_id"]

    def test_finds_approved_matching_params_float_int_equivalence(self):
        """Matches quantities robustly across float and int differences in JSON serialization."""
        rec = create_resolved_approval(
            "trade",
            _order_params_request(symbol="NVDA", side="SELL", quantity=5, order_type="MKT"),
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA",
            security_type="STK",
            side="SELL",
            quantity=5.0,
            order_type="MKT",
        )
        assert result is not None
        assert result["approval_id"] == rec["approval_id"]

    def test_returns_none_for_symbol_mismatch(self):
        """Returns None when symbol does not match."""
        create_resolved_approval(
            "trade",
            _order_params_request(symbol="AAPL"),
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is None

    def test_returns_none_for_quantity_mismatch(self):
        """Returns None when quantity does not match (float comparison)."""
        create_resolved_approval(
            "trade",
            _order_params_request(quantity=10.0),
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is None

    def test_returns_none_for_side_mismatch(self):
        """Returns None when side does not match."""
        create_resolved_approval(
            "trade",
            _order_params_request(side="BUY"),
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is None

    def test_returns_none_for_order_type_mismatch(self):
        """Returns None when orderType does not match."""
        create_resolved_approval(
            "trade",
            _order_params_request(order_type="LMT"),
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is None

    def test_returns_none_for_used_approval(self):
        """Does not return approvals that have already been consumed."""
        rec = create_resolved_approval(
            "trade",
            _order_params_request(),
            status="approved",
        )
        mark_used(rec["approval_id"])
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is None

    def test_returns_none_for_denied_approval(self):
        """Does not return denied approvals."""
        create_resolved_approval(
            "trade",
            _order_params_request(),
            status="denied",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is None

    def test_returns_none_for_non_trade_approval_type(self):
        """Does not return non-trade approvals even if params match."""
        create_resolved_approval(
            "trade_intent",
            {
                "order": {
                    "instrument": {"symbol": "NVDA", "securityType": "STK"},
                    "side": "SELL",
                    "quantity": 5.0,
                    "orderType": "MKT",
                }
            },
            status="approved",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is None

    def test_returns_most_recent_when_multiple_match(self):
        """When multiple approvals match, returns the most recently resolved."""
        first = create_resolved_approval(
            "trade",
            _order_params_request(),
            status="approved",
            resolve_note="first",
        )
        second = create_resolved_approval(
            "trade",
            _order_params_request(),
            status="approved",
            resolve_note="second",
        )
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT"
        )
        assert result is not None
        assert result["approval_id"] == second["approval_id"]
        # First should still exist but not be returned
        assert result["approval_id"] != first["approval_id"]

    def test_returns_none_when_approval_expired(self):
        """Does not return approvals that were approved but have since expired."""
        rec = create_resolved_approval(
            "trade",
            _order_params_request(),
            status="approved",
        )
        # Manually backdate resolved_at to 11 minutes ago so _expire_stale() expires it
        with _connect() as conn:
            old_time = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
            conn.execute(
                "UPDATE approvals SET resolved_at = ? WHERE approval_id = ?",
                (old_time, rec["approval_id"]),
            )
        _expire_stale(approved_unused_expiry_seconds=600)
        # Approval should now be expired, not returned
        refreshed = get_approval(rec["approval_id"])
        assert refreshed["status"] == "expired"
        result = find_approved_trade_by_order_params(
            symbol="NVDA", security_type="STK", side="SELL", quantity=5.0, order_type="MKT",
            approved_unused_expiry_seconds=600,
        )
        assert result is None


class TestApprovedExpiryAfter10Minutes:
    """Tests for the 10-minute approved-but-unused expiry policy."""

    def _backdate_approved(self, approval_id: str, minutes_ago: int) -> None:
        """Backdate an approved approval's resolved_at timestamp."""
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        with _connect() as conn:
            conn.execute(
                "UPDATE approvals SET resolved_at = ? WHERE approval_id = ?",
                (old_time, approval_id),
            )

    def test_approved_not_expired_within_10_minutes(self):
        """Approved-but-unused approvals under 10 minutes old are NOT expired."""
        rec = create_resolved_approval(
            "trade",
            {
                "order": {
                    "instrument": {"symbol": "AAPL", "securityType": "STK"},
                    "side": "BUY",
                    "quantity": 1,
                    "orderType": "MKT",
                }
            },
            status="approved",
        )
        self._backdate_approved(rec["approval_id"], minutes_ago=5)
        _expire_stale(approved_unused_expiry_seconds=600)
        refreshed = get_approval(rec["approval_id"])
        assert refreshed["status"] == "approved"

    def test_approved_expires_after_10_minutes(self):
        """Approved-but-unused approvals more than 10 minutes old are expired."""
        rec = create_resolved_approval(
            "trade",
            {
                "order": {
                    "instrument": {"symbol": "AAPL", "securityType": "STK"},
                    "side": "BUY",
                    "quantity": 1,
                    "orderType": "MKT",
                }
            },
            status="approved",
        )
        self._backdate_approved(rec["approval_id"], minutes_ago=11)
        _expire_stale(approved_unused_expiry_seconds=600)
        refreshed = get_approval(rec["approval_id"])
        assert refreshed["status"] == "expired"

    def test_used_approval_not_affected_by_expiry(self):
        """Used approvals are not touched by the 10-minute expiry."""
        rec = create_resolved_approval(
            "trade",
            {
                "order": {
                    "instrument": {"symbol": "TSLA", "securityType": "STK"},
                    "side": "BUY",
                    "quantity": 2,
                    "orderType": "MKT",
                }
            },
            status="approved",
        )
        mark_used(rec["approval_id"])
        self._backdate_approved(rec["approval_id"], minutes_ago=15)
        _expire_stale(approved_unused_expiry_seconds=600)
        refreshed = get_approval(rec["approval_id"])
        assert refreshed["status"] == "used"

    def test_pending_approval_still_expires_by_its_own_window(self):
        """Pending approvals still expire by their per-approval expires_at, not by 10-min rule."""
        from mcp_server.telegram.approval import create_approval, update_approval_status

        # Create a pending approval with a past expires_at
        rec = create_approval(
            "trade",
            {
                "order": {
                    "instrument": {"symbol": "SPY", "securityType": "STK"},
                    "side": "BUY",
                    "quantity": 1,
                    "orderType": "MKT",
                }
            },
            timeout_seconds=0,  # Already expired
        )
        _expire_stale()
        refreshed = get_approval(rec["approval_id"])
        assert refreshed["status"] == "expired"

