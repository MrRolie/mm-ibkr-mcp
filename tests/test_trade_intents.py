"""Focused tests for MCP-native trade-intent persistence."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ibkr_core.config import reset_config
from ibkr_core.control import ControlState, write_control
from ibkr_core.models import OrderResult, OrderSpec, OrderStatus, SymbolSpec
from ibkr_core.runtime_config import load_config_data, write_config_data
from trade_core import (
    create_trade_intent,
    get_trade_intent,
    list_trade_intents,
    record_trade_intent_submission,
)


@pytest.fixture(autouse=True)
def temp_runtime_env():
    """Point config, control, and SQLite state at a temporary directory."""
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


def _sample_order(client_order_id: str = "basket-001") -> OrderSpec:
    return OrderSpec(
        accountId="DU123456",
        instrument=SymbolSpec(
            symbol="AAPL",
            securityType="STK",
            exchange="SMART",
            currency="USD",
        ),
        side="BUY",
        quantity=10,
        orderType="LMT",
        limitPrice=180.0,
        tif="DAY",
        clientOrderId=client_order_id,
    )


def test_create_trade_intent_is_idempotent():
    """The same basket intent should collapse to one persisted record."""
    order = _sample_order()

    first = create_trade_intent(
        orders=[order],
        reason="enter alpha basket",
        account_id="DU123456",
        dry_run=True,
        require_approval=True,
    )
    second = create_trade_intent(
        orders=[order],
        reason="enter alpha basket",
        account_id="DU123456",
        dry_run=True,
        require_approval=True,
    )

    assert first.intent_id == second.intent_id
    assert first.intent_key == second.intent_key
    assert first.status.value == "PENDING_APPROVAL"
    assert len(first.orders) == 1
    assert first.orders[0].status.value == "PLANNED"
    assert len(list_trade_intents()) == 1


def test_record_trade_intent_submission_updates_execution_state():
    """Submitting a persisted order should update aggregate trade-intent state."""
    record = create_trade_intent(
        orders=[_sample_order("basket-002")],
        reason="submit one order",
        account_id="DU123456",
        dry_run=False,
        require_approval=False,
    )

    result = OrderResult(
        orderId="ord_123",
        clientOrderId="basket-002",
        status="ACCEPTED",
        orderStatus=OrderStatus(
            orderId="ord_123",
            clientOrderId="basket-002",
            status="SUBMITTED",
            filledQuantity=0,
            remainingQuantity=10,
            avgFillPrice=0,
            lastUpdate=datetime.now(timezone.utc),
        ),
    )

    updated = record_trade_intent_submission(
        intent_id=record.intent_id,
        intent_order_id=record.orders[0].intent_order_id,
        order_result=result,
    )
    refreshed = get_trade_intent(record.intent_id)

    assert updated.status.value == "SUBMITTED"
    assert updated.orders_submitted == 1
    assert updated.orders_failed == 0
    assert refreshed is not None
    assert refreshed.orders[0].order_id == "ord_123"
    assert refreshed.orders[0].status.value == "SUBMITTED"
