"""Default path helpers for mm-ibkr-mcp."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_ENV = "MM_IBKR_DATA_DIR"
WINDOWS_DEFAULT_DATA_DIR = Path("C:/ProgramData/mm-ibkr-mcp")


def get_repo_root() -> Path:
    """Return the mm-ibkr-mcp repository root."""
    return Path(__file__).resolve().parents[1]


def get_default_data_dir() -> Path:
    """Return the default data directory for the current host."""
    env_dir = os.getenv(DATA_DIR_ENV)
    if env_dir:
        return Path(env_dir).expanduser()

    if os.name == "nt":
        return WINDOWS_DEFAULT_DATA_DIR

    repo_root = get_repo_root()
    if repo_root.parent.name == "projects":
        return repo_root.parent.parent / "data" / "ibkr-mcp"

    return repo_root / "data" / "ibkr-mcp"
