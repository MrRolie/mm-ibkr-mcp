"""
Unit tests for config module.

Tests:
  - Configuration loading from config.json and env fallback
  - Validation of TRADING_MODE
  - Validation of ORDERS_ENABLED
  - TradingDisabledError when orders are disabled
  - Invalid configuration detection
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from ibkr_core.control import ControlState, get_control_path, write_control
from ibkr_core.config import (
    Config,
    InvalidConfigError,
    TradingDisabledError,
    get_config,
    load_config,
    reset_config,
)
from ibkr_core.runtime_config import load_config_data, write_config_data


@pytest.fixture(autouse=True)
def reset_config_fixture():
    """Reset config before each test."""
    reset_config()
    # Save and clear relevant env vars
    old_env = {}
    env_keys = [
        "IBKR_GATEWAY_HOST",
        "PAPER_GATEWAY_PORT",
        "PAPER_CLIENT_ID",
        "LIVE_GATEWAY_PORT",
        "LIVE_CLIENT_ID",
        "TRADING_MODE",
        "ORDERS_ENABLED",
        "API_PORT",
        "LOG_LEVEL",
        "LIVE_TRADING_OVERRIDE_FILE",
        "MM_IBKR_CONFIG_PATH",
        "MM_IBKR_CONTROL_DIR",
    ]
    for key in env_keys:
        old_env[key] = os.environ.get(key)
        if key in os.environ:
            del os.environ[key]

    temp_dir = tempfile.TemporaryDirectory()
    config_path = Path(temp_dir.name) / "config.json"
    control_dir = Path(temp_dir.name) / "control"
    os.environ["MM_IBKR_CONFIG_PATH"] = str(config_path)
    os.environ["MM_IBKR_CONTROL_DIR"] = str(control_dir)
    config_data = load_config_data(create_if_missing=True)
    config_data["control_dir"] = str(control_dir)
    config_data["data_storage_dir"] = str(Path(temp_dir.name) / "storage")
    config_data["log_dir"] = str(Path(temp_dir.name) / "storage" / "logs")
    config_data["audit_db_path"] = str(Path(temp_dir.name) / "storage" / "audit.db")
    config_data["watchdog_log_dir"] = str(Path(temp_dir.name) / "logs")
    write_config_data(config_data, path=config_path)
    write_control(ControlState(), base_dir=control_dir)

    yield

    # Restore
    for key, value in old_env.items():
        if value is not None:
            os.environ[key] = value
        elif key in os.environ:
            del os.environ[key]
    temp_dir.cleanup()
    reset_config()


class TestConfigDefaults:
    """Test default configuration values."""

    def test_defaults(self):
        """Test that defaults are applied when env vars are not set."""
        config = load_config()

        assert config.ibkr_gateway_host == "127.0.0.1"
        assert config.paper_gateway_port == 4002
        assert config.paper_client_id == 1
        assert config.live_gateway_port == 4001
        assert config.live_client_id == 777
        # Since default mode is paper, port should be paper port
        assert config.ibkr_gateway_port == 4002
        assert config.client_id == 1
        assert config.trading_mode == "paper"
        assert config.orders_enabled is False
        assert config.api_port == 8000
        assert config.log_level == "INFO"


class TestTradingMode:
    """Test TRADING_MODE validation."""

    def test_valid_paper_mode(self):
        """Test that paper mode is valid."""
        write_control(
            ControlState(trading_mode="paper", orders_enabled=False),
            base_dir=get_control_path().parent,
        )
        config = load_config()
        assert config.trading_mode == "paper"

    def test_valid_live_mode(self):
        """Test that live mode is valid (with orders disabled)."""
        write_control(
            ControlState(trading_mode="live", orders_enabled=False),
            base_dir=get_control_path().parent,
        )
        config = load_config()
        assert config.trading_mode == "live"

    def test_invalid_trading_mode_coerces_to_paper(self):
        """Test that invalid trading_mode coerces to paper."""
        control_path = get_control_path()
        control_path.parent.mkdir(parents=True, exist_ok=True)
        control_path.write_text(
            json.dumps(
                {
                    "trading_mode": "invalid",
                    "orders_enabled": False,
                    "dry_run": True,
                    "live_trading_override_file": None,
                }
            ),
            encoding="utf-8",
        )
        config = load_config()
        assert config.trading_mode == "paper"

    def test_trading_mode_case_insensitive(self):
        """Test that TRADING_MODE is case-insensitive."""
        control_path = get_control_path()
        control_path.parent.mkdir(parents=True, exist_ok=True)
        control_path.write_text(
            json.dumps(
                {
                    "trading_mode": "PAPER",
                    "orders_enabled": False,
                    "dry_run": True,
                    "live_trading_override_file": None,
                }
            ),
            encoding="utf-8",
        )
        config = load_config()
        assert config.trading_mode == "paper"


class TestOrdersEnabled:
    """Test ORDERS_ENABLED validation."""

    def test_orders_enabled_false(self):
        """Test that ORDERS_ENABLED=false is parsed correctly."""
        write_control(
            ControlState(trading_mode="paper", orders_enabled=False),
            base_dir=get_control_path().parent,
        )
        config = load_config()
        assert config.orders_enabled is False

    def test_orders_enabled_true(self):
        """Test that ORDERS_ENABLED=true is parsed correctly."""
        control_path = get_control_path()
        control_path.parent.mkdir(parents=True, exist_ok=True)
        control_path.write_text(
            json.dumps(
                {
                    "trading_mode": "paper",
                    "orders_enabled": "true",
                    "dry_run": True,
                    "live_trading_override_file": None,
                }
            ),
            encoding="utf-8",
        )
        config = load_config()
        assert config.orders_enabled is True

    def test_orders_enabled_yes(self):
        """Test that ORDERS_ENABLED=yes is parsed as true."""
        control_path = get_control_path()
        control_path.parent.mkdir(parents=True, exist_ok=True)
        control_path.write_text(
            json.dumps(
                {
                    "trading_mode": "paper",
                    "orders_enabled": "yes",
                    "dry_run": True,
                    "live_trading_override_file": None,
                }
            ),
            encoding="utf-8",
        )
        config = load_config()
        assert config.orders_enabled is True

    def test_orders_enabled_1(self):
        """Test that ORDERS_ENABLED=1 is parsed as true."""
        control_path = get_control_path()
        control_path.parent.mkdir(parents=True, exist_ok=True)
        control_path.write_text(
            json.dumps(
                {
                    "trading_mode": "paper",
                    "orders_enabled": "1",
                    "dry_run": True,
                    "live_trading_override_file": None,
                }
            ),
            encoding="utf-8",
        )
        config = load_config()
        assert config.orders_enabled is True


class TestLiveTradingOverride:
    """Test live trading override requirement."""

    def test_live_mode_with_orders_enabled_requires_override(self):
        """Test that live mode + orders enabled requires override file."""
        write_control(
            ControlState(trading_mode="live", orders_enabled=True),
            base_dir=get_control_path().parent,
        )

        with pytest.raises(InvalidConfigError, match="override"):
            load_config()

    def test_live_mode_with_orders_enabled_and_override_file(self):
        """Test that live mode + orders enabled works with override file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("override")
            override_path = f.name

        try:
            write_control(
                ControlState(
                    trading_mode="live",
                    orders_enabled=True,
                    live_trading_override_file=override_path,
                ),
                base_dir=get_control_path().parent,
            )

            config = load_config()
            assert config.trading_mode == "live"
            assert config.orders_enabled is True
        finally:
            Path(override_path).unlink()


class TestTradingDisabledError:
    """Test TradingDisabledError behavior."""

    def test_check_trading_enabled_raises_when_disabled(self):
        """Test that check_trading_enabled raises when orders are disabled."""
        write_control(
            ControlState(trading_mode="paper", orders_enabled=False),
            base_dir=get_control_path().parent,
        )
        config = load_config()

        with pytest.raises(TradingDisabledError):
            config.check_trading_enabled()

    def test_check_trading_enabled_ok_when_enabled(self):
        """Test that check_trading_enabled does not raise when orders are enabled."""
        write_control(
            ControlState(trading_mode="paper", orders_enabled=True),
            base_dir=get_control_path().parent,
        )
        config = load_config()

        # Should not raise
        config.check_trading_enabled()


class TestInvalidConfiguration:
    """Test invalid configuration handling."""

    def test_invalid_ibkr_port(self):
        """Test that invalid paper_gateway_port is coerced to default."""
        config_data = load_config_data()
        config_data["paper_gateway_port"] = "not_a_number"
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))

        config = load_config()
        assert config.paper_gateway_port == 4002

    def test_invalid_api_port(self):
        """Test that invalid api_port is coerced to default."""
        config_data = load_config_data()
        config_data["api_port"] = "not_a_number"
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))

        config = load_config()
        assert config.api_port == 8000


class TestGlobalConfig:
    """Test global config singleton."""

    def test_get_config_singleton(self):
        """Test that get_config returns the same instance when files are unchanged."""
        config1 = get_config()
        config2 = get_config()

        assert config1 is config2

    def test_get_config_reloads_when_control_changes(self):
        """Changing control.json should invalidate the cached config."""
        config1 = get_config()

        write_control(
            ControlState(trading_mode="live", orders_enabled=False, dry_run=False),
            base_dir=get_control_path().parent,
        )

        config2 = get_config()

        assert config1 is not config2
        assert config2.trading_mode == "live"
        assert config2.orders_enabled is False

    def test_reset_config(self):
        """Test that reset_config clears the singleton."""
        config1 = get_config()
        reset_config()
        config2 = get_config()

        # Different instances
        assert config1 is not config2
