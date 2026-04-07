"""
Configuration management and safety rails for IBKR integration.

The canonical runtime uses explicit IB connection settings from config.json and
a minimal control.json for runtime safety toggles.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from ibkr_core.runtime_config import get_config_path, load_runtime_config

logger = logging.getLogger(__name__)

env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    load_dotenv(env_file)


class TradingDisabledError(Exception):
    """Raised when order placement is attempted but real execution is disabled."""


class InvalidConfigError(Exception):
    """Raised when configuration is invalid."""


def _effective_control_dir(runtime_control_dir: str) -> str:
    """Resolve the active control directory with env override support."""
    env_override = os.environ.get("MM_IBKR_CONTROL_DIR", "").strip()
    return env_override or runtime_control_dir


@dataclass
class Config:
    """Central configuration holder for the MCP runtime."""

    ibkr_host: str
    ibkr_port: int
    ibkr_live_port: int
    ibkr_paper_port: int
    ibkr_client_id: int
    default_account_id: Optional[str]
    trading_mode: str
    orders_enabled: bool
    dry_run: bool
    log_level: str
    log_format: str
    data_storage_dir: str
    log_dir: str
    audit_db_path: str
    run_window_start: str
    run_window_end: str
    run_window_days: str
    run_window_timezone: str
    control_dir: str
    live_trading_override_file: Optional[str]

    @property
    def ibkr_gateway_port(self) -> int:
        """Compatibility alias for callers that still refer to the gateway port."""
        return self.ibkr_port

    @property
    def client_id(self) -> int:
        """Compatibility alias for callers that still refer to the client id."""
        return self.ibkr_client_id

    def validate(self) -> None:
        """Validate configuration."""
        if not self.ibkr_host:
            raise InvalidConfigError("IBKR host must not be empty")
        if self.ibkr_port <= 0:
            raise InvalidConfigError(f"IBKR port must be positive, got {self.ibkr_port}")
        if self.ibkr_client_id < 0:
            raise InvalidConfigError(
                f"IBKR client id must be non-negative, got {self.ibkr_client_id}"
            )

    def check_trading_enabled(self) -> None:
        """Raise TradingDisabledError if orders are not enabled for real execution."""
        if not self.orders_enabled or self.dry_run:
            raise TradingDisabledError(
                "Order placement is disabled by control.json. "
                "Update control.json to enable."
            )


def load_config() -> Config:
    """Load and validate configuration from config.json and control.json."""
    runtime = load_runtime_config(create_if_missing=True)
    control_dir = _effective_control_dir(runtime.control_dir)

    if control_dir:
        os.environ["MM_IBKR_CONTROL_DIR"] = control_dir

    from ibkr_core.control import (
        ensure_control_file,
        get_control_path,
        load_control as _load_control_state,
    )

    ensure_control_file(Path(control_dir))
    control_state = _load_control_state()
    logger.debug(
        "Loaded trading controls from control.json at %s: mode=%s, orders=%s dry_run=%s",
        get_control_path(),
        control_state.trading_mode,
        control_state.orders_enabled,
        control_state.effective_dry_run(),
    )

    config = Config(
        ibkr_host=runtime.ibkr_host,
        ibkr_port=runtime.ibkr_port,
        ibkr_live_port=runtime.ibkr_live_port,
        ibkr_paper_port=runtime.ibkr_paper_port,
        ibkr_client_id=runtime.ibkr_client_id,
        default_account_id=runtime.default_account_id,
        trading_mode=control_state.trading_mode,
        orders_enabled=control_state.orders_enabled,
        dry_run=control_state.effective_dry_run(),
        log_level=runtime.log_level,
        log_format=runtime.log_format,
        data_storage_dir=runtime.data_storage_dir,
        log_dir=runtime.log_dir,
        audit_db_path=runtime.audit_db_path,
        run_window_start=runtime.run_window_start,
        run_window_end=runtime.run_window_end,
        run_window_days=runtime.run_window_days,
        run_window_timezone=runtime.run_window_timezone,
        control_dir=control_dir,
        live_trading_override_file=control_state.live_trading_override_file,
    )
    config.validate()
    return config


def ensure_runtime_files() -> tuple[Path, Path]:
    """Persist default runtime artifacts when they do not already exist."""
    runtime = load_runtime_config(create_if_missing=True)
    control_dir = _effective_control_dir(runtime.control_dir)

    if control_dir:
        os.environ["MM_IBKR_CONTROL_DIR"] = control_dir

    from ibkr_core.control import ensure_control_file, get_control_path

    ensure_control_file(Path(control_dir))
    return get_config_path(), get_control_path(Path(control_dir))


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
