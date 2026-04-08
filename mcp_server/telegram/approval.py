"""SQLite-backed approval state machine for human-in-the-loop confirmation.

Each approval record represents a single request sent to a human via Telegram.
Status transitions:

    pending  ->  approved  ->  used      (happy path: approved then consumed by ibkr_place_order)
             ->  denied               (human tapped Deny)
             ->  expired              (timeout elapsed without response)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional

ApprovalStatus = Literal["pending", "approved", "denied", "expired", "used"]
ApprovalType = Literal["trade", "trade_intent", "live_trading", "environment_change"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id        TEXT PRIMARY KEY,
    approval_type      TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    request_data       TEXT NOT NULL,
    requested_at       TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    resolved_at        TEXT,
    telegram_message_id INTEGER,
    resolve_note       TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status    ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_requested ON approvals(requested_at);
"""


def _db_path() -> str:
    try:
        from ibkr_core.persistence import get_db_path

        return str(Path(get_db_path()))
    except Exception:
        return "./data/audit.db"


@contextmanager
def _connect(path: Optional[str] = None):
    p = path or _db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def create_approval(
    approval_type: ApprovalType,
    request_data: Dict[str, Any],
    timeout_seconds: int = 300,
) -> Dict[str, Any]:
    """Insert a new pending approval and return the full record dict."""
    approval_id = str(uuid.uuid4())
    now = _now()
    expires_at = _iso(now + timedelta(seconds=timeout_seconds))
    requested_at = _iso(now)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO approvals
                (approval_id, approval_type, status, request_data, requested_at, expires_at)
            VALUES (?, ?, 'pending', ?, ?, ?)
            """,
            (approval_id, approval_type, json.dumps(request_data), requested_at, expires_at),
        )

    return {
        "approval_id": approval_id,
        "approval_type": approval_type,
        "status": "pending",
        "request_data": request_data,
        "requested_at": requested_at,
        "expires_at": expires_at,
        "resolved_at": None,
        "telegram_message_id": None,
        "resolve_note": None,
    }


def create_resolved_approval(
    approval_type: ApprovalType,
    request_data: Dict[str, Any],
    *,
    status: ApprovalStatus,
    resolve_note: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an approval record that is already in a resolved state."""
    record = create_approval(approval_type, request_data, timeout_seconds=3600)
    update_approval_status(record["approval_id"], status, resolve_note=resolve_note)
    refreshed = get_approval(record["approval_id"])
    assert refreshed is not None
    return refreshed


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    """Fetch an approval by ID, auto-expiring stale pending records first."""
    _expire_stale()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    try:
        rec["request_data"] = json.loads(rec["request_data"])
    except Exception:
        pass
    return rec


def update_approval_status(
    approval_id: str,
    status: ApprovalStatus,
    *,
    telegram_message_id: Optional[int] = None,
    resolve_note: Optional[str] = None,
) -> None:
    """Called by the Telegram bot callback handler when a button is pressed."""
    resolved_at = _iso(_now()) if status != "pending" else None
    with _connect() as conn:
        conn.execute(
            """
            UPDATE approvals
               SET status = ?,
                   resolved_at = ?,
                   telegram_message_id = COALESCE(?, telegram_message_id),
                   resolve_note = COALESCE(?, resolve_note)
             WHERE approval_id = ?
            """,
            (status, resolved_at, telegram_message_id, resolve_note, approval_id),
        )


def set_telegram_message_id(approval_id: str, message_id: int) -> None:
    """Store the Telegram message_id after the request message is sent."""
    with _connect() as conn:
        conn.execute(
            "UPDATE approvals SET telegram_message_id = ? WHERE approval_id = ?",
            (message_id, approval_id),
        )


def mark_used(approval_id: str) -> None:
    """Consume an approved record; prevents replay on subsequent ibkr_place_order calls."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE approvals
               SET status = 'used', resolved_at = ?
             WHERE approval_id = ? AND status = 'approved'
            """,
            (_iso(_now()), approval_id),
        )


def find_approved_trade_by_client_order_id(
    client_order_id: str,
) -> Optional[Dict[str, Any]]:
    """Find the most recent approved-but-unused trade approval matching a clientOrderId.

    This enables auto-resolution when the caller omits approval_id but has a
    recent matching approval on file.
    """
    _expire_stale()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM approvals
             WHERE approval_type = 'trade'
               AND status = 'approved'
               AND json_extract(request_data, '$.order.clientOrderId') = ?
             ORDER BY resolved_at DESC
             LIMIT 1
            """,
            (client_order_id,),
        ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    try:
        rec["request_data"] = json.loads(rec["request_data"])
    except Exception:
        pass
    return rec


def _expire_stale() -> None:
    """Flip pending approvals that have passed their expiry time to 'expired'."""
    now_iso = _iso(_now())
    with _connect() as conn:
        conn.execute(
            """
            UPDATE approvals
               SET status = 'expired', resolved_at = ?
             WHERE status = 'pending' AND expires_at < ?
             """,
            (now_iso, now_iso),
        )
