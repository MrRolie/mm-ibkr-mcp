"""
Deterministic idempotency key helpers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(payload: dict[str, Any]) -> str:
    """Serialize payload in a stable form for hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_short(text: str, size: int) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:size]


def build_job_key(
    intent_id: str,
    job_type: str,
    scheduled_at: str,
    order_spec: dict[str, Any],
) -> str:
    """
    Build a deterministic scheduled-job key.

    Key format:
      jk_<24 hex chars>
    """
    material = _canonical_json(
        {
            "intent_id": intent_id,
            "job_type": job_type,
            "scheduled_at": scheduled_at,
            "order_spec": order_spec,
        }
    )
    return f"jk_{_hash_short(material, 24)}"


def build_client_order_id(
    *,
    seed: str,
    strategy_id: str,
    symbol: str,
    side: str,
    order_type: str,
    tif: str,
    quantity: float,
    reason: str,
) -> str:
    """
    Build deterministic clientOrderId suitable for IBKR submissions.

    The returned value is compact and stable:
      mm-<12 hex chars>
    """
    material = _canonical_json(
        {
            "seed": seed,
            "strategy_id": strategy_id,
            "symbol": symbol.upper(),
            "side": side.upper(),
            "order_type": order_type.upper(),
            "tif": tif.upper(),
            "quantity": float(quantity),
            "reason": reason,
        }
    )
    return f"mm-{_hash_short(material, 12)}"

