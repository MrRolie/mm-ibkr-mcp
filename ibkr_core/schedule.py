"""
Schedule management for time-windowed trading operations.

Provides utilities to check if the current time is within the configured
run window, and to calculate next window start/end times.
"""

from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


class ScheduleConfig:
    """Configuration for the trading schedule window."""

    def __init__(
        self,
        start_time: str = "04:00",
        end_time: str = "20:00",
        days: str = "Mon,Tue,Wed,Thu,Fri",
        timezone: str = "America/Toronto",
    ):
        self.start_time = self._parse_time(start_time)
        self.end_time = self._parse_time(end_time)
        self.days = self._parse_days(days)
        self.timezone = ZoneInfo(timezone)

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse HH:MM format to time object."""
        parts = time_str.strip().split(":")
        return time(int(parts[0]), int(parts[1]))

    @staticmethod
    def _parse_days(days_str: str) -> set:
        """Parse comma-separated day abbreviations to weekday numbers."""
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        days = set()
        for day in days_str.split(","):
            day_lower = day.strip().lower()
            if day_lower in day_map:
                days.add(day_map[day_lower])
        return days

    @classmethod
    def from_env(cls) -> "ScheduleConfig":
        """Load schedule configuration from runtime config."""
        from ibkr_core.config import get_config

        config = get_config()
        return cls(
            start_time=config.run_window_start,
            end_time=config.run_window_end,
            days=config.run_window_days,
            timezone=config.run_window_timezone,
        )


def is_within_run_window(config: Optional[ScheduleConfig] = None) -> bool:
    """
    Check if the current time is within the configured run window.

    Args:
        config: Schedule configuration. If None, loads from environment.

    Returns:
        True if within run window, False otherwise.
    """
    if config is None:
        config = ScheduleConfig.from_env()

    now = datetime.now(config.timezone)

    # Check day of week
    if now.weekday() not in config.days:
        return False

    # Check time
    current_time = now.time()
    return config.start_time <= current_time < config.end_time


def get_next_window_start(config: Optional[ScheduleConfig] = None) -> Optional[datetime]:
    """
    Get the next scheduled run window start time.

    Args:
        config: Schedule configuration. If None, loads from environment.

    Returns:
        Datetime of next window start, or None if no valid schedule.
    """
    if config is None:
        config = ScheduleConfig.from_env()

    if not config.days:
        return None

    now = datetime.now(config.timezone)

    # Check if today's window hasn't started yet
    if now.weekday() in config.days:
        today_start = now.replace(
            hour=config.start_time.hour, minute=config.start_time.minute, second=0, microsecond=0
        )
        if now < today_start:
            return today_start

    # Find next valid day
    for days_ahead in range(1, 8):
        check_date = now + timedelta(days=days_ahead)
        if check_date.weekday() in config.days:
            return check_date.replace(
                hour=config.start_time.hour,
                minute=config.start_time.minute,
                second=0,
                microsecond=0,
            )

    return None


def get_next_window_end(config: Optional[ScheduleConfig] = None) -> Optional[datetime]:
    """
    Get the next scheduled run window end time.

    Args:
        config: Schedule configuration. If None, loads from environment.

    Returns:
        Datetime of next window end, or None if no valid schedule.
    """
    if config is None:
        config = ScheduleConfig.from_env()

    if not config.days:
        return None

    now = datetime.now(config.timezone)

    # Check if today's window hasn't ended yet
    if now.weekday() in config.days:
        today_end = now.replace(
            hour=config.end_time.hour, minute=config.end_time.minute, second=0, microsecond=0
        )
        if now < today_end:
            return today_end

    # Find next valid day
    for days_ahead in range(1, 8):
        check_date = now + timedelta(days=days_ahead)
        if check_date.weekday() in config.days:
            return check_date.replace(
                hour=config.end_time.hour, minute=config.end_time.minute, second=0, microsecond=0
            )

    return None


def get_window_status(config: Optional[ScheduleConfig] = None) -> dict:
    """
    Get comprehensive status of the run window.

    Args:
        config: Schedule configuration. If None, loads from environment.

    Returns:
        Dictionary with window status information.
    """
    if config is None:
        config = ScheduleConfig.from_env()

    now = datetime.now(config.timezone)
    in_window = is_within_run_window(config)

    return {
        "current_time": now.isoformat(),
        "timezone": str(config.timezone),
        "in_window": in_window,
        "window_start": config.start_time.strftime("%H:%M"),
        "window_end": config.end_time.strftime("%H:%M"),
        "active_days": [
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in sorted(config.days)
        ],
        "next_window_start": (get_next_window_start(config).isoformat() if not in_window else None),
        "next_window_end": (get_next_window_end(config).isoformat() if in_window else None),
    }
