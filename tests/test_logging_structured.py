"""
Tests for structured logging with JSON format.

Verifies that:
- Logs are formatted as valid JSON
- All required fields are present in log records
- Log levels are configured correctly
- Custom formatters work as expected
"""

import json
import logging
import os
from io import StringIO
from pathlib import Path

import pytest

from ibkr_core.config import reset_config
from ibkr_core.logging_config import (
    CorrelationIdJsonFormatter,
    CorrelationIdTextFormatter,
    clear_correlation_id,
    configure_logging,
    get_log_format,
    get_log_level,
    log_with_context,
    set_correlation_id,
)
from ibkr_core.runtime_config import load_config_data, write_config_data


@pytest.fixture(autouse=True)
def runtime_config_fixture(tmp_path):
    """Ensure config.json is isolated per test."""
    reset_config()
    old_path = os.environ.get("MM_IBKR_CONFIG_PATH")
    config_path = tmp_path / "config.json"
    os.environ["MM_IBKR_CONFIG_PATH"] = str(config_path)
    load_config_data(create_if_missing=True)
    yield
    if old_path is not None:
        os.environ["MM_IBKR_CONFIG_PATH"] = old_path
    else:
        os.environ.pop("MM_IBKR_CONFIG_PATH", None)
    reset_config()

# =============================================================================
# Unit Tests - Log Configuration
# =============================================================================


class TestLogConfiguration:
    """Test log configuration functions."""

    def test_get_log_level_default(self):
        """Test default log level is INFO."""
        level = get_log_level()
        assert level == logging.INFO

    def test_get_log_level_from_config(self):
        """Test log level from config.json."""
        config_data = load_config_data()
        config_data["log_level"] = "DEBUG"
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))
        reset_config()
        assert get_log_level() == logging.DEBUG

        config_data = load_config_data()
        config_data["log_level"] = "WARNING"
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))
        reset_config()
        assert get_log_level() == logging.WARNING

        config_data = load_config_data()
        config_data["log_level"] = "ERROR"
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))
        reset_config()
        assert get_log_level() == logging.ERROR

    def test_get_log_format_default(self):
        """Test default log format is JSON."""
        format_type = get_log_format()
        assert format_type == "json"

    def test_get_log_format_from_config(self):
        """Test log format from config.json."""
        config_data = load_config_data()
        config_data["log_format"] = "text"
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))
        reset_config()
        assert get_log_format() == "text"

        config_data = load_config_data()
        config_data["log_format"] = "JSON"  # Case insensitive
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))
        reset_config()
        assert get_log_format() == "json"

    def test_configure_logging_json(self):
        """Test logging configuration with JSON format."""
        configure_logging(level=logging.INFO, format_type="json", force_reconfigure=True)

        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0
        assert root_logger.level == logging.INFO

        # Check formatter type
        handler = root_logger.handlers[0]
        assert isinstance(handler.formatter, CorrelationIdJsonFormatter)

    def test_configure_logging_text(self):
        """Test logging configuration with text format."""
        configure_logging(level=logging.DEBUG, format_type="text", force_reconfigure=True)

        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0
        assert root_logger.level == logging.DEBUG

        # Check formatter type
        handler = root_logger.handlers[0]
        assert isinstance(handler.formatter, CorrelationIdTextFormatter)


# =============================================================================
# Unit Tests - JSON Formatter
# =============================================================================


class TestJsonFormatter:
    """Test JSON log formatter."""

    def setup_method(self):
        """Set up logger with JSON formatter for each test."""
        clear_correlation_id()

        # Create test logger
        self.logger = logging.getLogger("test_json")
        self.logger.handlers.clear()
        self.logger.setLevel(logging.DEBUG)

        # Create string stream to capture output
        self.log_stream = StringIO()
        handler = logging.StreamHandler(self.log_stream)
        handler.setLevel(logging.DEBUG)

        # Add JSON formatter
        formatter = CorrelationIdJsonFormatter()
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def teardown_method(self):
        """Clean up after test."""
        self.logger.handlers.clear()
        clear_correlation_id()

    def test_valid_json_output(self):
        """Test that log output is valid JSON."""
        self.logger.info("Test message")

        output = self.log_stream.getvalue().strip()
        # Should be valid JSON
        log_data = json.loads(output)

        assert isinstance(log_data, dict)
        assert "message" in log_data

    def test_required_fields_present(self):
        """Test that all required fields are present in JSON log."""
        self.logger.info("Test message")

        output = self.log_stream.getvalue().strip()
        log_data = json.loads(output)

        # Required fields
        assert "timestamp" in log_data
        assert "level" in log_data
        assert "logger" in log_data
        assert "message" in log_data
        assert "module" in log_data
        assert "function" in log_data
        assert "line" in log_data

    def test_log_levels(self):
        """Test different log levels in JSON output."""
        test_cases = [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ]

        for level, level_name in test_cases:
            # Clear stream
            self.log_stream.seek(0)
            self.log_stream.truncate()

            # Log message
            self.logger.log(level, f"Test {level_name} message")

            # Verify level in JSON
            output = self.log_stream.getvalue().strip()
            log_data = json.loads(output)
            assert log_data["level"] == level_name

    def test_correlation_id_included_when_set(self):
        """Test that correlation_id is included when set in context."""
        test_id = "test-correlation-123"
        set_correlation_id(test_id)

        self.logger.info("Message with correlation ID")

        output = self.log_stream.getvalue().strip()
        log_data = json.loads(output)

        assert "correlation_id" in log_data
        assert log_data["correlation_id"] == test_id

    def test_correlation_id_not_included_when_not_set(self):
        """Test that correlation_id is not included when not set."""
        clear_correlation_id()

        self.logger.info("Message without correlation ID")

        output = self.log_stream.getvalue().strip()
        log_data = json.loads(output)

        assert "correlation_id" not in log_data

    def test_extra_fields_included(self):
        """Test that extra fields are included in JSON log."""
        self.logger.info(
            "User action", extra={"user_id": 123, "action": "login", "ip": "192.168.1.1"}
        )

        output = self.log_stream.getvalue().strip()
        log_data = json.loads(output)

        assert log_data["user_id"] == 123
        assert log_data["action"] == "login"
        assert log_data["ip"] == "192.168.1.1"

    def test_log_with_context_helper(self):
        """Test log_with_context helper function."""
        set_correlation_id("helper-test-123")

        log_with_context(
            self.logger,
            logging.INFO,
            "Order placed",
            order_id="ABC123",
            symbol="AAPL",
            quantity=100,
        )

        output = self.log_stream.getvalue().strip()
        log_data = json.loads(output)

        assert log_data["message"] == "Order placed"
        assert log_data["order_id"] == "ABC123"
        assert log_data["symbol"] == "AAPL"
        assert log_data["quantity"] == 100
        assert log_data["correlation_id"] == "helper-test-123"


# =============================================================================
# Unit Tests - Text Formatter
# =============================================================================


class TestTextFormatter:
    """Test text log formatter with correlation ID."""

    def setup_method(self):
        """Set up logger with text formatter."""
        clear_correlation_id()

        self.logger = logging.getLogger("test_text")
        self.logger.handlers.clear()
        self.logger.setLevel(logging.INFO)

        self.log_stream = StringIO()
        handler = logging.StreamHandler(self.log_stream)
        handler.setLevel(logging.INFO)

        # Use text formatter
        formatter = CorrelationIdTextFormatter(
            fmt="%(asctime)s [%(levelname)s] %(correlation_prefix)s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def teardown_method(self):
        """Clean up after test."""
        self.logger.handlers.clear()
        clear_correlation_id()

    def test_text_format_without_correlation_id(self):
        """Test text format without correlation ID."""
        self.logger.info("Test message")

        output = self.log_stream.getvalue()
        assert "Test message" in output
        assert "[INFO]" in output
        # Should not have correlation prefix when ID not set

    def test_text_format_with_correlation_id(self):
        """Test text format with correlation ID prefix."""
        test_id = "abc123-def456-ghi789"
        set_correlation_id(test_id)

        self.logger.info("Test message")

        output = self.log_stream.getvalue()
        assert "Test message" in output
        # Should have first 8 chars of correlation ID
        assert "[abc123-d]" in output or "abc123-d" in output


# =============================================================================
# Integration Tests
# =============================================================================


class TestStructuredLoggingIntegration:
    """Integration tests for structured logging."""

    def test_logging_reconfiguration(self):
        """Test that logging can be reconfigured."""
        # Configure as JSON
        configure_logging(level=logging.DEBUG, format_type="json", force_reconfigure=True)

        root_logger = logging.getLogger()
        first_handler = root_logger.handlers[0]
        assert isinstance(first_handler.formatter, CorrelationIdJsonFormatter)

        # Reconfigure as text
        configure_logging(level=logging.INFO, format_type="text", force_reconfigure=True)

        root_logger = logging.getLogger()
        second_handler = root_logger.handlers[0]
        assert isinstance(second_handler.formatter, CorrelationIdTextFormatter)

    def test_multiple_loggers_share_configuration(self):
        """Test that multiple loggers share the same root configuration."""
        configure_logging(level=logging.INFO, format_type="json", force_reconfigure=True)

        logger1 = logging.getLogger("app.module1")
        logger2 = logging.getLogger("app.module2")

        # Both should inherit from root logger
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0

        # Both loggers should use the same handler
        # (they don't have handlers themselves, they use root's handlers)
        assert len(logger1.handlers) == 0
        assert len(logger2.handlers) == 0

    def test_third_party_loggers_suppressed(self):
        """Test that noisy third-party loggers are suppressed."""
        configure_logging(level=logging.INFO, format_type="json", force_reconfigure=True)

        # These loggers should be set to WARNING level
        assert logging.getLogger("urllib3").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("asyncio").level == logging.WARNING
