"""Focused tests for the MCP-first runtime config contract."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from ibkr_core.config import (
    InvalidConfigError,
    TradingDisabledError,
    get_config,
    load_config,
    reset_config,
)
from ibkr_core.control import ControlState, write_control
from ibkr_core.runtime_config import load_config_data, write_config_data


@pytest.fixture(autouse=True)
def isolated_runtime_env():
    """Point config.json and control.json at a temporary directory."""
    reset_config()
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

        config_data = load_config_data(create_if_missing=True)
        config_data["control_dir"] = str(control_dir)
        config_data["data_storage_dir"] = str(temp_path / "storage")
        config_data["log_dir"] = str(temp_path / "storage" / "logs")
        config_data["audit_db_path"] = str(temp_path / "storage" / "audit.db")
        write_config_data(config_data, path=config_path)
        write_control(ControlState(), base_dir=control_dir)

        yield config_path, control_dir

    reset_config()
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_defaults_match_mcp_runtime_contract():
    """Default config should expose only the retained MCP/core settings."""
    config = load_config()

    assert config.ibkr_host == "127.0.0.1"
    assert config.ibkr_port == 4002
    assert config.ibkr_client_id == 1
    assert config.default_account_id is None
    assert config.trading_mode == "paper"
    assert config.orders_enabled is False
    assert config.dry_run is True
    assert config.log_level == "INFO"
    assert config.log_format == "json"
    assert config.ibkr_gateway_port == 4002
    assert config.client_id == 1


def test_explicit_ibkr_connection_settings_override_defaults(isolated_runtime_env):
    """Explicit host, port, client id, and account id should drive the connection."""
    config_path, _ = isolated_runtime_env
    config_data = load_config_data()
    config_data["ibkr_host"] = "10.0.0.5"
    config_data["ibkr_port"] = 5001
    config_data["ibkr_client_id"] = 77
    config_data["default_account_id"] = "DU123456"
    write_config_data(config_data, path=config_path)

    config = load_config()

    assert config.ibkr_host == "10.0.0.5"
    assert config.ibkr_port == 5001
    assert config.ibkr_client_id == 77
    assert config.default_account_id == "DU123456"


def test_legacy_gateway_fields_migrate_to_canonical_connection(isolated_runtime_env):
    """Old gateway keys should still be read, but normalized to the canonical fields."""
    config_path, control_dir = isolated_runtime_env
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw.pop("ibkr_host", None)
    raw.pop("ibkr_port", None)
    raw.pop("ibkr_client_id", None)
    raw["ibkr_gateway_host"] = "192.168.1.50"
    raw["paper_gateway_port"] = 4444
    raw["paper_client_id"] = 17
    raw["control_dir"] = str(control_dir)
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_config()

    assert config.ibkr_host == "192.168.1.50"
    assert config.ibkr_port == 4444
    assert config.ibkr_client_id == 17


def test_write_config_data_drops_legacy_and_unknown_fields(isolated_runtime_env):
    """Persisted config.json should only contain the retained runtime schema."""
    config_path, control_dir = isolated_runtime_env
    write_config_data(
        {
            "ibkr_gateway_host": "192.168.1.50",
            "paper_gateway_port": 4444,
            "paper_client_id": 17,
            "control_dir": str(control_dir),
            "unknown_key": "discard-me",
        },
        path=config_path,
    )

    written = json.loads(config_path.read_text(encoding="utf-8"))

    assert written["ibkr_host"] == "192.168.1.50"
    assert written["ibkr_port"] == 4444
    assert written["ibkr_client_id"] == 17
    assert "paper_gateway_port" not in written
    assert "paper_client_id" not in written
    assert "unknown_key" not in written


def test_invalid_numeric_connection_fields_fall_back_to_defaults(isolated_runtime_env):
    """Invalid numeric values should coerce back to the canonical defaults."""
    config_path, control_dir = isolated_runtime_env
    raw = load_config_data()
    raw["ibkr_port"] = "not-a-number"
    raw["ibkr_client_id"] = "also-bad"
    raw["control_dir"] = str(control_dir)
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_config()

    assert config.ibkr_port == 4002
    assert config.ibkr_client_id == 1


def test_check_trading_enabled_raises_until_orders_are_enabled():
    """Default control.json should block real execution."""
    with pytest.raises(TradingDisabledError):
        load_config().check_trading_enabled()


def test_control_state_drives_trading_flags(isolated_runtime_env):
    """control.json should govern orders_enabled and dry_run, while trading mode stays paper."""
    _, control_dir = isolated_runtime_env
    write_control(
        ControlState(trading_mode="live", orders_enabled=True, dry_run=False),
        base_dir=control_dir,
    )

    config = load_config()

    assert config.trading_mode == "paper"
    assert config.orders_enabled is True
    assert config.dry_run is False
    config.check_trading_enabled()


def test_get_config_reloads_after_control_file_change(isolated_runtime_env):
    """The cached Config should refresh when control.json changes."""
    _, control_dir = isolated_runtime_env

    first = get_config()
    assert first.orders_enabled is False
    assert first.dry_run is True

    write_control(
        ControlState(orders_enabled=True, dry_run=False),
        base_dir=control_dir,
    )

    refreshed = get_config()
    assert refreshed.orders_enabled is True
    assert refreshed.dry_run is False


def test_invalid_host_or_port_raise_validation_errors(isolated_runtime_env):
    """Empty hosts should fall back; non-positive ports should fail validation."""
    config_path, _ = isolated_runtime_env
    config_data = load_config_data()
    config_data["ibkr_host"] = ""
    write_config_data(config_data, path=config_path)
    assert load_config().ibkr_host == "127.0.0.1"

    config_data = load_config_data()
    config_data["ibkr_host"] = "127.0.0.1"
    config_data["ibkr_port"] = 0
    write_config_data(config_data, path=config_path)
    with pytest.raises(InvalidConfigError, match="IBKR port must be positive"):
        load_config()
