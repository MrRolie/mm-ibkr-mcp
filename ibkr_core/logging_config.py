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

# =============================================================================
# Context Variables for Correlation ID
# =============================================================================

# Thread-safe context variable for correlation ID
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


# =============================================================================
# Custom JSON Formatter with Correlation ID
# =============================================================================


class CorrelationIdJsonFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter that automatically includes correlation_id from context.

    Adds the following fields to every log record:
    - timestamp: ISO-8601 timestamp
    - level: Log level (DEBUG, INFO, etc.)
    - logger: Logger name
    - message: Log message
    - correlation_id: Request correlation ID (if set in context)
    - module: Python module name
    - function: Function name
    - line: Line number
    """

    def add_fields(self, log_record, record, message_dict):
        """Add custom fields to log record."""
        super().add_fields(log_record, record, message_dict)

        # Add correlation ID from context if available
        correlation_id = get_correlation_id()
        if correlation_id:
            log_record["correlation_id"] = correlation_id

        # Ensure timestamp is always present
        if "timestamp" not in log_record:
            log_record["timestamp"] = self.formatTime(record, self.datefmt)

        # Ensure level is always present
        if "level" not in log_record:
            log_record["level"] = record.levelname

        # Ensure logger name is present
        if "logger" not in log_record:
            log_record["logger"] = record.name

        # Add source location
        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno


# =============================================================================
# Text Formatter with Correlation ID (for development)
# =============================================================================


class CorrelationIdTextFormatter(logging.Formatter):
    """
    Text formatter that includes correlation_id from context.

    Format: [timestamp] [level] [correlation_id] logger - message
    """

    def format(self, record):
        """Format log record with correlation ID."""
        correlation_id = get_correlation_id()
        if correlation_id:
            # Add correlation_id as a field on the record
            record.correlation_id = correlation_id
            record.correlation_prefix = f"[{correlation_id[:8]}]"
        else:
            record.correlation_id = None
            record.correlation_prefix = ""

        return super().format(record)


# =============================================================================
# Logging Configuration
# =============================================================================


def get_log_level() -> int:
    """
    Get log level from runtime config.

    Supported values: DEBUG, INFO, WARNING, ERROR, CRITICAL
    Default: INFO
    """
    from ibkr_core.config import get_config

    level_name = get_config().log_level.upper()
    return getattr(logging, level_name, logging.INFO)


def get_log_format() -> str:
    """
    Get log format from runtime config.

    Supported values: json, text
    Default: json
    """
    from ibkr_core.config import get_config

    return get_config().log_format.lower()


def get_log_file_path() -> Optional[Path]:
    """
    Get log file path from runtime config.

    LOG_DIR specifies the directory where log files are stored.
    Returns the full path to ibkr-gateway.log in that directory.
    """
    from ibkr_core.config import get_config

    log_dir = get_config().log_dir
    if not log_dir:
        return None

    path = Path(log_dir)
    return path / "ibkr-gateway.log"


def configure_logging(
    level: Optional[int] = None,
    format_type: Optional[str] = None,
    force_reconfigure: bool = False,
) -> None:
    """
    Configure structured logging for the application.

    This should be called once at application startup.

    Args:
        level: Log level (defaults to config.json or INFO)
        format_type: "json" or "text" (defaults to config.json or "json")
        force_reconfigure: If True, reconfigure even if already configured
    """
    # Check if already configured
    root_logger = logging.getLogger()
    if root_logger.handlers and not force_reconfigure:
        return

    # Get configuration
    if level is None:
        level = get_log_level()
    if format_type is None:
        format_type = get_log_format()

    # Remove existing handlers
    root_logger.handlers.clear()

    # stdio MCP must reserve stdout for protocol messages only.
    console_stream = sys.stderr if os.getenv("MCP_TRANSPORT") == "stdio" else sys.stdout
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(level)

    # Configure formatter based on format type
    if format_type == "json":
        # Note: python-json-logger v4.0 uses different API than v2.0
        # It doesn't take a format string in __init__
        formatter = CorrelationIdJsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    else:
        # Text format for development
        formatter = CorrelationIdTextFormatter(
            fmt="%(asctime)s [%(levelname)s] %(correlation_prefix)s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(level)

    # Optional file handler for persistent logs
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

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # Log configuration complete
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured: level={logging.getLevelName(level)}, format={format_type}")


# =============================================================================
# Context Manager for Correlation ID
# =============================================================================


class correlation_context:
    """
    Context manager for setting correlation ID.

    Usage:
        with correlation_context("abc123"):
            logger.info("This log will have correlation_id=abc123")
    """

    def __init__(self, correlation_id: str):
        self.correlation_id = correlation_id
        self.token = None

    def __enter__(self):
        self.token = _correlation_id.set(self.correlation_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _correlation_id.set(None)
        return False


# =============================================================================
# Structured Logging Helpers
# =============================================================================


def log_with_context(logger: logging.Logger, level: int, message: str, **kwargs):
    """
    Log a message with additional context fields.

    This is useful for adding structured data to logs.

    Args:
        logger: Logger to use
        level: Log level (e.g., logging.INFO)
        message: Log message
        **kwargs: Additional fields to include in the log record

    Example:
        log_with_context(
            logger, logging.INFO, "Order placed",
            order_id="123", symbol="AAPL", quantity=100
        )
    """
    # Create a log record with extra fields
    logger.log(level, message, extra=kwargs)


# =============================================================================
# Module Initialization
# =============================================================================

# Auto-configure logging on module import if not already configured
# This ensures logging works even if configure_logging() isn't explicitly called
if not logging.getLogger().handlers:
    configure_logging()
