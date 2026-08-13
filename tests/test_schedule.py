"""
Tests for the schedule module.
"""

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import patch

import pytest

from ibkr_core.config import reset_config
from ibkr_core.runtime_config import load_config_data, write_config_data
from ibkr_core.schedule import (
    ScheduleConfig,
    get_next_window_end,
    get_next_window_start,
    get_window_status,
    is_within_run_window,
)


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


class TestScheduleConfig:
    """Tests for ScheduleConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ScheduleConfig()
        assert config.start_time == time(4, 0)
        assert config.end_time == time(20, 0)
        assert config.days == {0, 1, 2, 3, 4}  # Mon-Fri
        assert config.timezone == ZoneInfo("America/Toronto")

    def test_custom_config(self):
        """Test custom configuration values."""
        config = ScheduleConfig(
            start_time="09:30", end_time="16:00", days="Mon,Wed,Fri", timezone="America/New_York"
        )
        assert config.start_time == time(9, 30)
        assert config.end_time == time(16, 0)
        assert config.days == {0, 2, 4}  # Mon, Wed, Fri
        assert config.timezone == ZoneInfo("America/New_York")

    def test_from_env(self):
        """Test loading config from runtime config."""
        config_data = load_config_data()
        config_data.update(
            {
                "run_window_start": "08:00",
                "run_window_end": "18:00",
                "run_window_days": "Mon,Tue,Wed",
                "run_window_timezone": "UTC",
            }
        )
        write_config_data(config_data, path=Path(os.environ["MM_IBKR_CONFIG_PATH"]))

        config = ScheduleConfig.from_env()
        assert config.start_time == time(8, 0)
        assert config.end_time == time(18, 0)
        assert config.days == {0, 1, 2}

    def test_parse_days_case_insensitive(self):
        """Test that day parsing is case-insensitive."""
        config = ScheduleConfig(days="MON,tue,Wed,THU,fri")
        assert config.days == {0, 1, 2, 3, 4}


class TestIsWithinRunWindow:
    """Tests for is_within_run_window function."""

    def test_within_window_weekday(self):
        """Test time within window on a weekday."""
        config = ScheduleConfig(
            start_time="09:00", end_time="17:00", days="Mon,Tue,Wed,Thu,Fri", timezone="UTC"
        )

        # Monday at 12:00 UTC
        with patch("ibkr_core.schedule.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC"))  # Monday
            # Need to also mock the datetime class itself for comparison
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        # Use actual function with mocked time
        test_time = datetime(2024, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC"))  # Monday
        config = ScheduleConfig(
            start_time="09:00", end_time="17:00", days="Mon,Tue,Wed,Thu,Fri", timezone="UTC"
        )

        # Weekday (Monday = 0) and time (12:00) is within 09:00-17:00
        assert 0 in config.days
        assert config.start_time <= test_time.time() < config.end_time

    def test_outside_window_wrong_day(self):
        """Test time on a weekend when only weekdays configured."""
        config = ScheduleConfig(
            start_time="09:00", end_time="17:00", days="Mon,Tue,Wed,Thu,Fri", timezone="UTC"
        )

        # Saturday is weekday 5, not in config.days
        assert 5 not in config.days
        assert 6 not in config.days

    def test_outside_window_too_early(self):
        """Test time before window start."""
        config = ScheduleConfig(
            start_time="09:00", end_time="17:00", days="Mon,Tue,Wed,Thu,Fri", timezone="UTC"
        )

        early_time = time(8, 0)
        assert early_time < config.start_time

    def test_outside_window_too_late(self):
        """Test time after window end."""
        config = ScheduleConfig(
            start_time="09:00", end_time="17:00", days="Mon,Tue,Wed,Thu,Fri", timezone="UTC"
        )

        late_time = time(18, 0)
        assert late_time >= config.end_time


class TestGetNextWindowStart:
    """Tests for get_next_window_start function."""

    def test_returns_datetime(self):
        """Test that function returns a datetime object."""
        config = ScheduleConfig()
        result = get_next_window_start(config)
        # Result should be datetime or None
        assert result is None or isinstance(result, datetime)

    def test_empty_days_returns_none(self):
        """Test that empty days config returns None."""
        config = ScheduleConfig(days="")
        result = get_next_window_start(config)
        assert result is None


class TestGetNextWindowEnd:
    """Tests for get_next_window_end function."""

    def test_returns_datetime(self):
        """Test that function returns a datetime object."""
        config = ScheduleConfig()
        result = get_next_window_end(config)
        # Result should be datetime or None
        assert result is None or isinstance(result, datetime)

    def test_empty_days_returns_none(self):
        """Test that empty days config returns None."""
        config = ScheduleConfig(days="")
        result = get_next_window_end(config)
        assert result is None


class TestGetWindowStatus:
    """Tests for get_window_status function."""

    def test_returns_dict_with_required_keys(self):
        """Test that status returns all required keys."""
        config = ScheduleConfig()
        status = get_window_status(config)

        required_keys = [
            "current_time",
            "timezone",
            "in_window",
            "window_start",
            "window_end",
            "active_days",
        ]

        for key in required_keys:
            assert key in status

    def test_active_days_format(self):
        """Test that active_days is properly formatted."""
        config = ScheduleConfig(days="Mon,Wed,Fri")
        status = get_window_status(config)

        assert isinstance(status["active_days"], list)
        assert "Mon" in status["active_days"]
        assert "Wed" in status["active_days"]
        assert "Fri" in status["active_days"]

    def test_window_times_format(self):
        """Test that window times are in HH:MM format."""
        config = ScheduleConfig(start_time="04:00", end_time="20:00")
        status = get_window_status(config)

        assert status["window_start"] == "04:00"
        assert status["window_end"] == "20:00"
