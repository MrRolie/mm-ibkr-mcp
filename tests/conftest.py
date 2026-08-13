"""Pytest configuration for mm-ibkr-mcp tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


def pytest_configure() -> None:
    """Ensure test config opens the run window for local MCP/core tests."""
    if os.environ.get("MM_IBKR_CONFIG_PATH"):
        return

    base_dir = Path(tempfile.mkdtemp(prefix="mm-ibkr-test-config-"))
    config_path = base_dir / "config.json"
    os.environ["MM_IBKR_CONFIG_PATH"] = str(config_path)
    control_dir = base_dir / "control"
    os.environ["MM_IBKR_CONTROL_DIR"] = str(control_dir)

    from ibkr_core.runtime_config import write_config_data
    from ibkr_core.control import ControlState, write_control

    write_config_data(
        {
            "run_window_start": "00:00",
            "run_window_end": "23:59",
            "run_window_days": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
            "run_window_timezone": "America/Toronto",
        },
        path=config_path,
    )
    write_control(ControlState(), base_dir=control_dir)


@pytest.fixture(autouse=True)
def reset_control_state() -> None:
    """Reset control.json to defaults for each test."""
    from ibkr_core.control import ControlState, get_base_dir, write_control

    write_control(ControlState(), base_dir=get_base_dir())
    yield
