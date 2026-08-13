"""
Runtime configuration loader for mm-ibkr-mcp.

The canonical runtime keeps only MCP-relevant settings: explicit IBKR connection
details, logging, persistence paths, and the trading schedule window.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ibkr_core.paths import get_default_data_dir

logger = logging.getLogger(__name__)

CONFIG_PATH_ENV = "MM_IBKR_CONFIG_PATH"
DEFAULT_DATA_DIR = get_default_data_dir()
DEFAULT_CONFIG_PATH = DEFAULT_DATA_DIR / "config.json"
SCHEMA_VERSION = 1


def get_config_path() -> Path:
    """Return the config.json path (override with MM_IBKR_CONFIG_PATH)."""
    env_path = os.getenv(CONFIG_PATH_ENV)
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def _default_config() -> Dict[str, Any]:
    storage_dir = DEFAULT_DATA_DIR / "storage"
    log_dir = storage_dir / "logs"
    return {
        "schema_version": SCHEMA_VERSION,
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 4002,
        "ibkr_live_port": int(os.environ.get("MM_IBKR_LIVE_PORT", "4001")),
        "ibkr_paper_port": int(os.environ.get("MM_IBKR_PAPER_PORT", "4002")),
        "ibkr_client_id": 1,
        "default_account_id": None,
        "log_level": "INFO",
        "log_format": "json",
        "data_storage_dir": str(storage_dir),
        "log_dir": str(log_dir),
        "audit_db_path": str(storage_dir / "audit.db"),
        "control_dir": str(DEFAULT_DATA_DIR),
        "run_window_start": "04:00",
        "run_window_end": "20:00",
        "run_window_days": "Mon,Tue,Wed,Thu,Fri",
        "run_window_timezone": "America/Toronto",
    }


CONFIG_KEYS = set(_default_config().keys())


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _coerce_str(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    defaults = _default_config()
    filtered = {key: value for key, value in raw.items() if key in CONFIG_KEYS}
    merged: Dict[str, Any] = {**defaults, **filtered}

    merged["schema_version"] = _coerce_int(merged.get("schema_version"), SCHEMA_VERSION)
    merged["ibkr_host"] = _coerce_str(
        raw.get("ibkr_host") or raw.get("ibkr_gateway_host"),
        defaults["ibkr_host"],
    )
    merged["ibkr_port"] = _coerce_int(
        raw.get("ibkr_port"),
        _coerce_int(raw.get("paper_gateway_port"), defaults["ibkr_port"]),
    )
    merged["ibkr_live_port"] = _coerce_int(raw.get("ibkr_live_port"), defaults["ibkr_live_port"])
    merged["ibkr_paper_port"] = _coerce_int(raw.get("ibkr_paper_port"), defaults["ibkr_paper_port"])
    merged["ibkr_client_id"] = _coerce_int(
        raw.get("ibkr_client_id"),
        _coerce_int(raw.get("paper_client_id"), defaults["ibkr_client_id"]),
    )
    merged["default_account_id"] = _coerce_optional_str(raw.get("default_account_id"))
    merged["log_level"] = _coerce_str(raw.get("log_level"), defaults["log_level"]).upper()
    merged["log_format"] = _coerce_str(raw.get("log_format"), defaults["log_format"]).lower()
    merged["data_storage_dir"] = _coerce_str(
        raw.get("data_storage_dir"), defaults["data_storage_dir"]
    )
    merged["log_dir"] = _coerce_str(
        raw.get("log_dir"), str(Path(merged["data_storage_dir"]) / "logs")
    )
    merged["audit_db_path"] = _coerce_str(
        raw.get("audit_db_path"), str(Path(merged["data_storage_dir"]) / "audit.db")
    )
    merged["control_dir"] = _coerce_str(raw.get("control_dir"), defaults["control_dir"])
    merged["run_window_start"] = _coerce_str(
        raw.get("run_window_start"), defaults["run_window_start"]
    )
    merged["run_window_end"] = _coerce_str(raw.get("run_window_end"), defaults["run_window_end"])
    merged["run_window_days"] = _coerce_str(
        raw.get("run_window_days"), defaults["run_window_days"]
    )
    merged["run_window_timezone"] = _coerce_str(
        raw.get("run_window_timezone"), defaults["run_window_timezone"]
    )
    return merged


def load_config_data(create_if_missing: bool = False) -> Dict[str, Any]:
    """Load config.json data with defaults merged."""
    path = get_config_path()
    if not path.exists():
        if create_if_missing:
            data = _default_config()
            write_config_data(data, path=path)
            return data
        logger.warning("config.json not found at %s; using defaults", path)
        return _default_config()

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("config.json root must be an object")
        return _normalize_config(raw)
    except Exception as exc:
        logger.warning("Failed to read config.json (%s). Using defaults.", exc)
        return _default_config()


def write_config_data(data: Dict[str, Any], path: Path | None = None) -> Path:
    """Write config.json atomically."""
    target = path or get_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_config(data)
    temp_path = target.with_suffix(f".tmp.{os.getpid()}")
    temp_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    temp_path.replace(target)
    return target


def update_config_data(updates: Dict[str, Any], path: Path | None = None) -> Dict[str, Any]:
    """Update config.json with provided fields and return merged config."""
    current = load_config_data(create_if_missing=True)
    current.update(updates)
    write_config_data(current, path=path)
    return _normalize_config(current)


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int
    ibkr_host: str
    ibkr_port: int
    ibkr_live_port: int
    ibkr_paper_port: int
    ibkr_client_id: int
    default_account_id: Optional[str]
    log_level: str
    log_format: str
    data_storage_dir: str
    log_dir: str
    audit_db_path: str
    control_dir: str
    run_window_start: str
    run_window_end: str
    run_window_days: str
    run_window_timezone: str


def load_runtime_config(create_if_missing: bool = False) -> RuntimeConfig:
    """Load config.json as RuntimeConfig."""
    data = load_config_data(create_if_missing=create_if_missing)
    return RuntimeConfig(
        schema_version=data["schema_version"],
        ibkr_host=data["ibkr_host"],
        ibkr_port=data["ibkr_port"],
        ibkr_live_port=data["ibkr_live_port"],
        ibkr_paper_port=data["ibkr_paper_port"],
        ibkr_client_id=data["ibkr_client_id"],
        default_account_id=data["default_account_id"],
        log_level=data["log_level"],
        log_format=data["log_format"],
        data_storage_dir=data["data_storage_dir"],
        log_dir=data["log_dir"],
        audit_db_path=data["audit_db_path"],
        control_dir=data["control_dir"],
        run_window_start=data["run_window_start"],
        run_window_end=data["run_window_end"],
        run_window_days=data["run_window_days"],
        run_window_timezone=data["run_window_timezone"],
    )
