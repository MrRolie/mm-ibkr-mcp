"""
Configuration management and safety rails for IBKR integration.

Trading controls (trading_mode, orders_enabled, live_trading_override_file)
are loaded from the canonical control.json managed by mm-ibkr-gateway.

Other settings (IBKR connection, API port, paths) are loaded from the
ProgramData config.json file.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from ibkr_core.runtime_config import get_config_path, load_runtime_config

logger = logging.getLogger(__name__)

# Load .env file
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    load_dotenv(env_file)


class TradingDisabledError(Exception):
    """Raised when order placement is attempted but ORDERS_ENABLED=false."""

    pass


class InvalidConfigError(Exception):
    """Raised when configuration is invalid."""

    pass


@dataclass
class Config:
    """Central configuration holder."""

    # IBKR Connection
    ibkr_gateway_host: str

    # Paper trading connection
    paper_gateway_port: int
    paper_client_id: int

    # Live trading connection
    live_gateway_port: int
    live_client_id: int

    # Trading Mode
    trading_mode: str  # "paper" or "live"
    orders_enabled: bool

    # API Server
    api_port: int
    api_bind_host: str
    api_request_timeout: float
    allowed_ips: str
    log_level: str
    log_format: str

    # Path settings
    data_storage_dir: Optional[str]
    log_dir: Optional[str]
    audit_db_path: str
    watchdog_log_dir: str
    ibkr_gateway_path: str

    # Run window settings
    run_window_start: str
    run_window_end: str
    run_window_days: str
    run_window_timezone: str

    # Control.json location
    control_dir: str

    # Admin settings
    admin_restart_enabled: bool

    # Override file for live trading (for extra safety)
    live_trading_override_file: Optional[str]

    @property
    def ibkr_gateway_port(self) -> int:
        """Return the appropriate gateway port based on trading mode."""
        return self.live_gateway_port if self.trading_mode == "live" else self.paper_gateway_port

    @property
    def client_id(self) -> int:
        """Return the appropriate client ID based on trading mode."""
        return self.live_client_id if self.trading_mode == "live" else self.paper_client_id

    def validate(self) -> None:
        """Validate configuration."""
        if self.trading_mode not in ("paper", "live"):
            raise InvalidConfigError(
                f"TRADING_MODE must be 'paper' or 'live', got '{self.trading_mode}'"
            )

        if self.trading_mode == "live" and self.orders_enabled:
            # Extra safety check
            if self.live_trading_override_file:
                override_path = Path(self.live_trading_override_file)
                if not override_path.exists():
                    raise InvalidConfigError(
                        "Live trading with orders enabled requires override file "
                        f"at {self.live_trading_override_file}"
                    )
            else:
                raise InvalidConfigError(
                    "Live trading with orders enabled is extremely dangerous. "
                    "Set live_trading_override_file in control.json to proceed."
                )

    def check_trading_enabled(self) -> None:
        """Raise TradingDisabledError if orders are not enabled."""
        if not self.orders_enabled:
            raise TradingDisabledError(
                "Order placement is disabled (orders_enabled=false). "
                "Update control.json to enable."
            )


def load_config() -> Config:
    """Load and validate configuration from config.json and control.json."""

    runtime = load_runtime_config()

    if runtime.control_dir:
        os.environ["MM_IBKR_CONTROL_DIR"] = runtime.control_dir

    # IBKR Connection
    ibkr_host = runtime.ibkr_gateway_host

    # Paper trading connection
    paper_port = runtime.paper_gateway_port
    paper_client_id = runtime.paper_client_id

    # Live trading connection
    live_port = runtime.live_gateway_port
    live_client_id = runtime.live_client_id

    from ibkr_core.control import get_control_path, load_control as _load_control_state

    control_state = _load_control_state()
    trading_mode = control_state.trading_mode
    orders_enabled = control_state.orders_enabled
    live_trading_override_file = control_state.live_trading_override_file
    logger.debug(
        "Loaded trading controls from control.json at %s: mode=%s, orders=%s",
        get_control_path(),
        trading_mode,
        orders_enabled,
    )

    # API Server
    api_port = runtime.api_port
    api_bind_host = runtime.api_bind_host
    api_request_timeout = runtime.api_request_timeout
    allowed_ips = runtime.allowed_ips

    log_level = runtime.log_level
    log_format = runtime.log_format

    # Path settings
    data_storage_dir = runtime.data_storage_dir
    log_dir = runtime.log_dir
    audit_db_path = runtime.audit_db_path
    watchdog_log_dir = runtime.watchdog_log_dir
    ibkr_gateway_path = runtime.ibkr_gateway_path

    run_window_start = runtime.run_window_start
    run_window_end = runtime.run_window_end
    run_window_days = runtime.run_window_days
    run_window_timezone = runtime.run_window_timezone

    control_dir = runtime.control_dir

    admin_restart_enabled = runtime.admin_restart_enabled

    # Note: live_trading_override_file is now loaded from control.json above

    config = Config(
        ibkr_gateway_host=ibkr_host,
        paper_gateway_port=paper_port,
        paper_client_id=paper_client_id,
        live_gateway_port=live_port,
        live_client_id=live_client_id,
        trading_mode=trading_mode,
        orders_enabled=orders_enabled,
        api_port=api_port,
        api_bind_host=api_bind_host,
        api_request_timeout=api_request_timeout,
        allowed_ips=allowed_ips,
        log_level=log_level,
        log_format=log_format,
        data_storage_dir=data_storage_dir,
        log_dir=log_dir,
        audit_db_path=audit_db_path,
        watchdog_log_dir=watchdog_log_dir,
        ibkr_gateway_path=ibkr_gateway_path,
        run_window_start=run_window_start,
        run_window_end=run_window_end,
        run_window_days=run_window_days,
        run_window_timezone=run_window_timezone,
        control_dir=control_dir,
        admin_restart_enabled=admin_restart_enabled,
        live_trading_override_file=live_trading_override_file,
    )

    config.validate()
    return config


# Global config instance
_config: Optional[Config] = None
_config_signature: Optional[tuple[tuple[str, bool, int, int], tuple[str, bool, int, int]]] = None


def _file_signature(path: Path) -> tuple[str, bool, int, int]:
    """Return a lightweight signature that changes when a file changes."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (str(path), False, -1, -1)
    return (str(path), True, stat.st_mtime_ns, stat.st_size)


def _current_config_signature() -> tuple[tuple[str, bool, int, int], tuple[str, bool, int, int]]:
    """Return signatures for runtime config.json and control.json."""
    config_path = get_config_path()
    runtime = load_runtime_config()
    control_path = Path(runtime.control_dir) / "control.json"
    return (_file_signature(config_path), _file_signature(control_path))


def get_config() -> Config:
    """Get the cached config instance, reloading when source files change."""
    global _config, _config_signature
    signature = _current_config_signature()
    if _config is None or _config_signature != signature:
        _config = load_config()
        _config_signature = _current_config_signature()
    return _config


def reset_config() -> None:
    """Reset config (useful for testing)."""
    global _config, _config_signature
    _config = None
    _config_signature = None
