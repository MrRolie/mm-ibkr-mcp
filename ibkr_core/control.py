"""Canonical control.json management for mm-ibkr-mcp."""

from __future__ import annotations

import getpass
import json
import logging
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from ibkr_core.paths import get_default_data_dir

logger = logging.getLogger(__name__)

DEFAULT_CONTROL_DIR = get_default_data_dir()


@dataclass
class ControlState:
    """Centralized trading control state."""

    orders_enabled: bool = False
    dry_run: bool = True
    block_reason: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
    # Legacy compatibility fields. New control.json does not need to persist them.
    trading_mode: Literal["paper", "live"] = "paper"
    live_trading_override_file: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        payload = {
            "orders_enabled": self.orders_enabled,
            "dry_run": self.dry_run,
            "block_reason": self.block_reason,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> ControlState:
        """Deserialize from dict, coercing types and applying defaults."""
        return cls(
            orders_enabled=_coerce_bool(data.get("orders_enabled", False)),
            dry_run=_coerce_bool(data.get("dry_run", True)),
            block_reason=_coerce_optional_str(data.get("block_reason")),
            updated_at=_coerce_optional_str(data.get("updated_at")),
            updated_by=_coerce_optional_str(data.get("updated_by")),
            trading_mode=_coerce_trading_mode(data.get("trading_mode", "paper")),
            live_trading_override_file=data.get("live_trading_override_file"),
        )

    @classmethod
    def defaults(cls) -> ControlState:
        """Return safe defaults (paper, disabled, dry-run)."""
        return cls()

    def is_live_trading_enabled(self) -> bool:
        """Check whether real order placement is effectively enabled."""
        return self.orders_enabled and not self.dry_run

    def effective_dry_run(self) -> bool:
        """Get effective dry_run state."""
        return self.dry_run or not self.orders_enabled

    def validate_override_file(self) -> tuple[bool, str]:
        """Legacy no-op kept for compatibility with older surfaces."""
        return True, ""


def get_base_dir(override: Optional[Path] = None) -> Path:
    """Return base directory for control artifacts."""
    if override is not None:
        return Path(override)
    env_dir = os.getenv("MM_IBKR_CONTROL_DIR")
    return Path(env_dir) if env_dir else DEFAULT_CONTROL_DIR


def get_control_path(base_dir: Optional[Path] = None) -> Path:
    """Return path to control.json."""
    return get_base_dir(base_dir) / "control.json"


def get_audit_log_path(base_dir: Optional[Path] = None) -> Path:
    """Return path to control.log."""
    return get_base_dir(base_dir) / "control.log"


def load_control(base_dir: Optional[Path] = None) -> ControlState:
    """Load control state from control.json."""
    control_path = get_control_path(base_dir)

    if not control_path.exists():
        return ensure_control_file(base_dir)

    try:
        with open(control_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ControlState.from_dict(data)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON in control.json: %s. Using defaults.", exc)
        return ControlState.defaults()
    except Exception as exc:
        logger.warning("Error reading control.json: %s. Using defaults.", exc)
        return ControlState.defaults()


def validate_control(state: ControlState) -> list[str]:
    """Validate control state for consistency."""
    errors = []
    if not isinstance(state.orders_enabled, bool):
        errors.append("orders_enabled must be boolean")
    if not isinstance(state.dry_run, bool):
        errors.append("dry_run must be boolean")
    return errors


def write_control(state: ControlState, base_dir: Optional[Path] = None) -> Path:
    """Write control state to control.json atomically."""
    control_path = get_control_path(base_dir)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    state = replace(
        state,
        updated_at=datetime.now(timezone.utc).isoformat(),
        updated_by=state.updated_by or getpass.getuser(),
    )

    temp_fd, temp_path = tempfile.mkstemp(
        suffix=".json",
        prefix="control_",
        dir=control_path.parent,
    )

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)

        if control_path.exists():
            control_path.unlink()
        Path(temp_path).rename(control_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

    return control_path


def ensure_control_file(base_dir: Optional[Path] = None) -> ControlState:
    """Ensure control.json exists with valid content."""
    control_path = get_control_path(base_dir)

    if control_path.exists():
        return load_control(base_dir)

    state = ControlState.defaults()
    write_control(state, base_dir)
    logger.info("Created control.json at %s", control_path)
    return state


def get_control_status(base_dir: Optional[Path] = None) -> dict:
    """Get comprehensive control status for display/API."""
    state = load_control(base_dir)
    errors = validate_control(state)
    override_valid, override_msg = state.validate_override_file()

    # Determine actual trading mode from active config port
    from ibkr_core.runtime_config import load_runtime_config
    runtime = load_runtime_config()
    if runtime.ibkr_port == runtime.ibkr_live_port:
        effective_mode = "live"
    elif runtime.ibkr_port == runtime.ibkr_paper_port:
        effective_mode = "paper"
    else:
        effective_mode = state.trading_mode

    return {
        "trading_mode": effective_mode,
        "orders_enabled": state.orders_enabled,
        "dry_run": state.dry_run,
        "effective_dry_run": state.effective_dry_run(),
        "block_reason": state.block_reason,
        "updated_at": state.updated_at,
        "updated_by": state.updated_by,
        "live_trading_override_file": state.live_trading_override_file,
        "override_file_exists": override_valid if state.live_trading_override_file else None,
        "override_file_message": override_msg if override_msg else None,
        "is_live_trading_enabled": state.is_live_trading_enabled(),
        "validation_errors": errors,
        "control_path": str(get_control_path(base_dir)),
    }


def write_audit_entry(
    action: str,
    base_dir: Optional[Path] = None,
    **kwargs: Any,
) -> Path:
    """Write a structured audit log entry to control.log."""
    log_path = get_audit_log_path(base_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"

    parts = [username, action]
    for key, value in kwargs.items():
        if value is not None:
            parts.append(f"{key}:{value}")

    message = " | ".join(parts)
    timestamp = datetime.now(timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{timestamp} | {message}\n")
    return log_path


def _coerce_trading_mode(value: str) -> Literal["paper", "live"]:
    """Coerce trading_mode to valid value, defaulting to paper."""
    if str(value).lower() == "live":
        return "live"
    return "paper"


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    return False
