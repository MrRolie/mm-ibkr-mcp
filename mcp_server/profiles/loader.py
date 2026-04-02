"""Agent trading profile loader.

Profiles are JSON files stored under MCP_AGENT_PROFILE_DIR (default: data/profiles/).
Each file describes what an agent is allowed to trade and its risk limits.

Profile JSON schema (all fields optional except profile_id):

{
  "profile_id": "conservative",
  "description": "Conservative equities-only profile",
  "allowed_security_types": ["STK", "ETF"],
  "allowed_order_types": ["MKT", "LMT", "MOC"],
  "allowed_symbols": null,           // null = all symbols allowed
  "blocked_symbols": ["GME", "AMC"],
  "max_position_size_pct": 10.0,     // max % of net liquidation per position
  "max_position_notional": 50000.0,  // max USD per single position
  "max_order_quantity": 500,
  "max_daily_orders": 20,
  "max_daily_loss": -5000.0,         // stop if unrealised+realised loss > this
  "require_trade_approval": true,
  "require_live_trading_approval": true,
  "allow_options": false,
  "allow_short_selling": false,
  "notes": "Human-readable description of intent"
}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_PROFILE_DIR = "./data/profiles"

# Fallback minimal profile used when no profile file is found.
_BUILTIN_DEFAULT: Dict[str, Any] = {
    "profile_id": "default",
    "description": "Default conservative profile (no customisation found)",
    "allowed_security_types": ["STK", "ETF", "FUT", "OPT", "CASH", "CRYPTO"],
    "allowed_order_types": ["MKT", "LMT", "STP", "STP_LMT", "MOC", "OPG", "TRAIL", "TRAIL_LIMIT", "BRACKET"],
    "allowed_symbols": None,
    "blocked_symbols": [],
    "max_position_size_pct": 25.0,
    "max_position_notional": None,
    "max_order_quantity": None,
    "max_daily_orders": 50,
    "max_daily_loss": None,
    "require_trade_approval": True,
    "require_live_trading_approval": True,
    "allow_options": True,
    "allow_short_selling": True,
    "notes": "Built-in default profile with broad permissions and approval gates enabled.",
}


def get_profile_dir() -> Path:
    env_dir = os.environ.get("MCP_AGENT_PROFILE_DIR", "").strip()
    return Path(env_dir) if env_dir else Path(_DEFAULT_PROFILE_DIR)


def load_profile(profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Load a profile by ID from the profile directory.

    Falls back to the built-in default if the file is not found.
    """
    if profile_id is None:
        profile_id = os.environ.get("MCP_AGENT_PROFILE_ID", "default").strip()

    profile_dir = get_profile_dir()
    profile_path = profile_dir / f"{profile_id}.json"

    if not profile_path.exists():
        return dict(_BUILTIN_DEFAULT) | {"profile_id": profile_id, "_source": "builtin_default"}

    try:
        with open(profile_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("profile_id", profile_id)
        data["_source"] = str(profile_path)
        return data
    except Exception as exc:
        raise ValueError(f"Failed to load profile '{profile_id}': {exc}") from exc


def list_profiles() -> List[str]:
    """Return IDs of all profiles available in the profile directory."""
    profile_dir = get_profile_dir()
    if not profile_dir.exists():
        return []
    return sorted(p.stem for p in profile_dir.glob("*.json"))


def save_profile(profile: Dict[str, Any]) -> Path:
    """Persist a profile dict to disk. Returns the file path."""
    profile_id = profile.get("profile_id")
    if not profile_id:
        raise ValueError("Profile must have a 'profile_id' field")

    profile_dir = get_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    path = profile_dir / f"{profile_id}.json"

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2)

    return path
