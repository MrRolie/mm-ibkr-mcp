"""
Structured logging configuration with correlation IDs.

Provides:
- JSON log formatting using python-json-logger
- Correlation ID context management
- Log level configuration
- Integration with standard Python logging
"""

import logging
import os
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from pythonjsonlogger import jsonlogger

_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID for the current context."""
    _correlation_id.set(correlation_id)


def get_correlation_id() -> Optional[str]:
    """Get the correlation ID for the current context."""
    return _correlation_id.get()


def clear_correlation_id() -> None:
    """Clear the correlation ID for the current context."""
    _correlation_id.set(None)


class CorrelationIdJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that automatically includes correlation_id from context."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)

        correlation_id = get_correlation_id()
        if correlation_id:
            log_record["correlation_id"] = correlation_id
        if "timestamp" not in log_record:
            log_record["timestamp"] = self.formatTime(record, self.datefmt)
        if "level" not in log_record:
            log_record["level"] = record.levelname
        if "logger" not in log_record:
            log_record["logger"] = record.name

        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno


class CorrelationIdTextFormatter(logging.Formatter):
    """Text formatter that includes correlation_id from context."""

    def format(self, record):
        correlation_id = get_correlation_id()
        if correlation_id:
            record.correlation_id = correlation_id
            record.correlation_prefix = f"[{correlation_id[:8]}]"
        else:
            record.correlation_id = None
            record.correlation_prefix = ""
        return super().format(record)


def get_log_level() -> int:
    """Get log level from runtime config."""
    from ibkr_core.config import get_config

    level_name = get_config().log_level.upper()
    return getattr(logging, level_name, logging.INFO)


def get_log_format() -> str:
    """Get log format from runtime config."""
    from ibkr_core.config import get_config

    return get_config().log_format.lower()


def get_log_file_path() -> Optional[Path]:
    """Return the configured log file path."""
    from ibkr_core.config import get_config

    log_dir = get_config().log_dir
    if not log_dir:
        return None

    path = Path(log_dir)
    return path / "ibkr-mcp.log"


def configure_logging(
    level: Optional[int] = None,
    format_type: Optional[str] = None,
    force_reconfigure: bool = False,
) -> None:
    """Configure structured logging for the application."""
    root_logger = logging.getLogger()
    if root_logger.handlers and not force_reconfigure:
        return

    if level is None:
        level = get_log_level()
    if format_type is None:
        format_type = get_log_format()

    root_logger.handlers.clear()

    console_stream = sys.stderr if os.getenv("MCP_TRANSPORT") == "stdio" else sys.stdout
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(level)

    if format_type == "json":
        formatter = CorrelationIdJsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    else:
        formatter = CorrelationIdTextFormatter(
            fmt="%(asctime)s [%(levelname)s] %(correlation_prefix)s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(level)

    log_file_path = get_log_file_path()
    if log_file_path:
        try:
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to configure file logging at %s: %s",
                log_file_path,
                exc,
            )

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging configured: level=%s, format=%s", logging.getLevelName(level), format_type)


class correlation_context:
    """Context manager for setting correlation ID."""

    def __init__(self, correlation_id: str):
        self.correlation_id = correlation_id
        self.token = None

    def __enter__(self):
        self.token = _correlation_id.set(self.correlation_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _correlation_id.set(None)
        return False


def log_with_context(logger: logging.Logger, level: int, message: str, **kwargs):
    """Log a message with additional context fields."""
    logger.log(level, message, extra=kwargs)


if not logging.getLogger().handlers:
    configure_logging()
