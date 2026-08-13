"""SQLite persistence helpers for MCP-native trade intents."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ibkr_core.models import CancelResult, OrderPreview, OrderResult, OrderSpec, OrderStatus
from ibkr_core.persistence import get_db_connection
from trade_core.idempotency import _canonical_json, _hash_short
from trade_core.models import (
    TradeIntentOrderRecord,
    TradeIntentOrderStatus,
    TradeIntentRecord,
    TradeIntentStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_intent (
    intent_id TEXT PRIMARY KEY,
    intent_key TEXT NOT NULL UNIQUE,
    account_id TEXT,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    approval_id TEXT,
    approval_status TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0,
    order_count INTEGER NOT NULL,
    orders_submitted INTEGER NOT NULL DEFAULT 0,
    orders_filled INTEGER NOT NULL DEFAULT 0,
    orders_cancelled INTEGER NOT NULL DEFAULT 0,
    orders_failed INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_intent_status ON trade_intent(status);
CREATE INDEX IF NOT EXISTS idx_trade_intent_account ON trade_intent(account_id);
CREATE INDEX IF NOT EXISTS idx_trade_intent_updated ON trade_intent(updated_at);

CREATE TABLE IF NOT EXISTS intent_order (
    intent_order_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    tif TEXT NOT NULL,
    limit_price REAL,
    status TEXT NOT NULL,
    order_id TEXT,
    ibkr_order_id TEXT,
    submitted_at TEXT,
    updated_at TEXT NOT NULL,
    order_payload TEXT NOT NULL,
    preview_payload TEXT,
    result_payload TEXT,
    last_error TEXT,
    FOREIGN KEY (intent_id) REFERENCES trade_intent(intent_id)
);

CREATE INDEX IF NOT EXISTS idx_intent_order_intent ON intent_order(intent_id);
CREATE INDEX IF NOT EXISTS idx_intent_order_status ON intent_order(status);
CREATE INDEX IF NOT EXISTS idx_intent_order_client ON intent_order(client_order_id);

CREATE TABLE IF NOT EXISTS execution_state (
    execution_id TEXT PRIMARY KEY,
    intent_order_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    order_id TEXT,
    ibkr_order_id TEXT,
    filled_quantity REAL NOT NULL DEFAULT 0,
    remaining_quantity REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL NOT NULL DEFAULT 0,
    submitted_at TEXT,
    completed_at TEXT,
    last_updated TEXT NOT NULL,
    error_message TEXT,
    FOREIGN KEY (intent_order_id) REFERENCES intent_order(intent_order_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_state_status ON execution_state(status);

CREATE TABLE IF NOT EXISTS position_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_position_snapshot_account ON position_snapshot(account_id);
CREATE INDEX IF NOT EXISTS idx_position_snapshot_created ON position_snapshot(created_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema() -> None:
    with get_db_connection() as conn:
        conn.executescript(SCHEMA)


def _json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _build_intent_key(
    account_id: Optional[str],
    reason: str,
    dry_run: bool,
    orders: Iterable[OrderSpec],
) -> str:
    material = _canonical_json(
        {
            "account_id": account_id,
            "reason": reason,
            "dry_run": bool(dry_run),
            "orders": [
                order.model_dump(mode="json", exclude_none=True)
                for order in orders
            ],
        }
    )
    return f"ti_{_hash_short(material, 24)}"


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_order_status(
    order_result: Optional[OrderResult] = None,
    order_status: Optional[OrderStatus] = None,
    cancel_result: Optional[CancelResult] = None,
    error: Optional[str] = None,
) -> TradeIntentOrderStatus:
    if error:
        return TradeIntentOrderStatus.FAILED
    if cancel_result is not None:
        if cancel_result.status == "CANCELLED":
            return TradeIntentOrderStatus.CANCELLED
        return TradeIntentOrderStatus.FAILED
    if order_status is not None:
        mapping = {
            "SUBMITTED": TradeIntentOrderStatus.SUBMITTED,
            "PENDING_SUBMIT": TradeIntentOrderStatus.SUBMITTED,
            "PARTIALLY_FILLED": TradeIntentOrderStatus.PARTIALLY_FILLED,
            "FILLED": TradeIntentOrderStatus.FILLED,
            "CANCELLED": TradeIntentOrderStatus.CANCELLED,
            "REJECTED": TradeIntentOrderStatus.REJECTED,
            "EXPIRED": TradeIntentOrderStatus.CANCELLED,
        }
        return mapping.get(order_status.status, TradeIntentOrderStatus.FAILED)
    if order_result is not None:
        if order_result.status == "SIMULATED":
            return TradeIntentOrderStatus.SIMULATED
        if order_result.status == "ACCEPTED":
            if order_result.orderStatus is not None:
                return _normalize_order_status(order_status=order_result.orderStatus)
            return TradeIntentOrderStatus.SUBMITTED
        return TradeIntentOrderStatus.REJECTED
    return TradeIntentOrderStatus.PLANNED


def _aggregate_intent_status(
    intent_row: sqlite3.Row,
    order_rows: list[sqlite3.Row],
) -> TradeIntentStatus:
    if not order_rows:
        return TradeIntentStatus.APPROVED

    statuses = {row["status"] for row in order_rows}
    approval_status = intent_row["approval_status"]

    if approval_status == "pending":
        return TradeIntentStatus.PENDING_APPROVAL
    if statuses == {TradeIntentOrderStatus.SIMULATED.value}:
        return TradeIntentStatus.SIMULATED
    if statuses == {TradeIntentOrderStatus.FILLED.value}:
        return TradeIntentStatus.FILLED
    if statuses <= {TradeIntentOrderStatus.CANCELLED.value}:
        return TradeIntentStatus.CANCELLED
    if TradeIntentOrderStatus.PARTIALLY_FILLED.value in statuses:
        return TradeIntentStatus.PARTIALLY_FILLED
    if statuses & {
        TradeIntentOrderStatus.SUBMITTED.value,
        TradeIntentOrderStatus.PARTIALLY_FILLED.value,
    }:
        return TradeIntentStatus.SUBMITTED
    if statuses & {
        TradeIntentOrderStatus.REJECTED.value,
        TradeIntentOrderStatus.FAILED.value,
    }:
        if statuses & {
            TradeIntentOrderStatus.SUBMITTED.value,
            TradeIntentOrderStatus.PARTIALLY_FILLED.value,
            TradeIntentOrderStatus.FILLED.value,
        }:
            return TradeIntentStatus.PARTIALLY_FILLED
        return TradeIntentStatus.FAILED
    return TradeIntentStatus.APPROVED


def _order_record_from_row(row: sqlite3.Row) -> TradeIntentOrderRecord:
    preview = json.loads(row["preview_payload"]) if row["preview_payload"] else None
    order = json.loads(row["order_payload"])
    return TradeIntentOrderRecord(
        intent_order_id=row["intent_order_id"],
        sequence_no=int(row["sequence_no"]),
        client_order_id=row["client_order_id"],
        order=OrderSpec.model_validate(order),
        preview=OrderPreview.model_validate(preview) if preview else None,
        status=TradeIntentOrderStatus(row["status"]),
        order_id=row["order_id"],
        ibkr_order_id=row["ibkr_order_id"],
        submitted_at=_parse_ts(row["submitted_at"]),
        updated_at=_parse_ts(row["updated_at"]) or datetime.now(timezone.utc),
        last_error=row["last_error"],
    )


def _intent_record_from_rows(intent_row: sqlite3.Row, order_rows: list[sqlite3.Row]) -> TradeIntentRecord:
    record = TradeIntentRecord(
        intent_id=intent_row["intent_id"],
        intent_key=intent_row["intent_key"],
        account_id=intent_row["account_id"],
        reason=intent_row["reason"],
        status=TradeIntentStatus(intent_row["status"]),
        approval_id=intent_row["approval_id"],
        approval_status=intent_row["approval_status"],
        dry_run=bool(intent_row["dry_run"]),
        order_count=int(intent_row["order_count"]),
        orders_submitted=int(intent_row["orders_submitted"]),
        orders_filled=int(intent_row["orders_filled"]),
        orders_cancelled=int(intent_row["orders_cancelled"]),
        orders_failed=int(intent_row["orders_failed"]),
        last_error=intent_row["last_error"],
        created_at=_parse_ts(intent_row["created_at"]) or datetime.now(timezone.utc),
        updated_at=_parse_ts(intent_row["updated_at"]) or datetime.now(timezone.utc),
        orders=[_order_record_from_row(row) for row in order_rows],
    )
    return record


def _refresh_intent_stats(conn: sqlite3.Connection, intent_id: str) -> TradeIntentRecord:
    intent_row = conn.execute(
        "SELECT * FROM trade_intent WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()
    if intent_row is None:
        raise KeyError(f"Unknown trade intent {intent_id}")

    order_rows = conn.execute(
        "SELECT * FROM intent_order WHERE intent_id = ? ORDER BY sequence_no ASC",
        (intent_id,),
    ).fetchall()

    orders_submitted = sum(
        1 for row in order_rows if row["status"] in {"SUBMITTED", "PARTIALLY_FILLED", "FILLED"}
    )
    orders_filled = sum(1 for row in order_rows if row["status"] == "FILLED")
    orders_cancelled = sum(1 for row in order_rows if row["status"] == "CANCELLED")
    orders_failed = sum(1 for row in order_rows if row["status"] in {"FAILED", "REJECTED"})
    status = _aggregate_intent_status(intent_row, order_rows)
    updated_at = _now_iso()

    conn.execute(
        """
        UPDATE trade_intent
           SET status = ?,
               orders_submitted = ?,
               orders_filled = ?,
               orders_cancelled = ?,
               orders_failed = ?,
               updated_at = ?
         WHERE intent_id = ?
        """,
        (
            status.value,
            orders_submitted,
            orders_filled,
            orders_cancelled,
            orders_failed,
            updated_at,
            intent_id,
        ),
    )

    refreshed = conn.execute(
        "SELECT * FROM trade_intent WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()
    assert refreshed is not None
    return _intent_record_from_rows(refreshed, order_rows)


def create_trade_intent(
    *,
    orders: list[OrderSpec],
    reason: str,
    account_id: Optional[str],
    dry_run: bool,
    require_approval: bool,
    previews: Optional[list[Optional[OrderPreview]]] = None,
) -> TradeIntentRecord:
    """Create or return an idempotent trade intent for a basket of explicit orders."""
    _ensure_schema()
    if not orders:
        raise ValueError("orders must contain at least one order")
    previews = previews or [None] * len(orders)
    if len(previews) != len(orders):
        raise ValueError("previews length must match orders length")

    client_order_ids = [order.clientOrderId for order in orders]
    if not all(client_order_ids):
        raise ValueError("Every basket order must include clientOrderId")
    if len(set(client_order_ids)) != len(client_order_ids):
        raise ValueError("clientOrderId values must be unique within a basket")

    intent_key = _build_intent_key(account_id, reason, dry_run, orders)

    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT intent_id FROM trade_intent WHERE intent_key = ?",
            (intent_key,),
        ).fetchone()
        if existing is not None:
            record = get_trade_intent(existing["intent_id"])
            assert record is not None
            return record

        now = _now_iso()
        intent_id = f"ti_{uuid.uuid4().hex[:12]}"
        status = (
            TradeIntentStatus.PENDING_APPROVAL.value
            if require_approval
            else TradeIntentStatus.APPROVED.value
        )
        approval_status = "pending" if require_approval else "approved"
        conn.execute(
            """
            INSERT INTO trade_intent (
                intent_id, intent_key, account_id, reason, status, approval_status,
                dry_run, order_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent_id,
                intent_key,
                account_id,
                reason,
                status,
                approval_status,
                int(bool(dry_run)),
                len(orders),
                now,
                now,
            ),
        )

        for index, (order, preview) in enumerate(zip(orders, previews), start=1):
            instrument = order.instrument
            conn.execute(
                """
                INSERT INTO intent_order (
                    intent_order_id, intent_id, sequence_no, client_order_id, symbol, side,
                    quantity, order_type, tif, limit_price, status, updated_at,
                    order_payload, preview_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"io_{uuid.uuid4().hex[:12]}",
                    intent_id,
                    index,
                    order.clientOrderId,
                    instrument.symbol,
                    order.side,
                    float(order.quantity),
                    order.orderType,
                    order.tif,
                    order.limitPrice,
                    TradeIntentOrderStatus.PLANNED.value,
                    now,
                    _json_dump(order.model_dump(mode="json", exclude_none=True)),
                    _json_dump(preview.model_dump(mode="json", exclude_none=True))
                    if preview is not None
                    else None,
                ),
            )

        return _refresh_intent_stats(conn, intent_id)


def get_trade_intent(intent_id: str) -> Optional[TradeIntentRecord]:
    """Return a persisted trade intent, or None when it does not exist."""
    _ensure_schema()
    with get_db_connection() as conn:
        intent_row = conn.execute(
            "SELECT * FROM trade_intent WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        if intent_row is None:
            return None
        order_rows = conn.execute(
            "SELECT * FROM intent_order WHERE intent_id = ? ORDER BY sequence_no ASC",
            (intent_id,),
        ).fetchall()
        return _intent_record_from_rows(intent_row, order_rows)


def list_trade_intents(
    *,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[TradeIntentRecord]:
    """List recent trade intents with optional status filtering."""
    _ensure_schema()
    limit = max(1, min(limit, 200))
    query = "SELECT * FROM trade_intent"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with get_db_connection() as conn:
        intent_rows = conn.execute(query, params).fetchall()
        records: list[TradeIntentRecord] = []
        for row in intent_rows:
            order_rows = conn.execute(
                "SELECT * FROM intent_order WHERE intent_id = ? ORDER BY sequence_no ASC",
                (row["intent_id"],),
            ).fetchall()
            records.append(_intent_record_from_rows(row, order_rows))
        return records


def set_trade_intent_approval(
    intent_id: str,
    *,
    approval_id: str,
    approval_status: str,
) -> TradeIntentRecord:
    """Attach or update approval state for a trade intent."""
    _ensure_schema()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE trade_intent
               SET approval_id = ?, approval_status = ?, updated_at = ?
             WHERE intent_id = ?
            """,
            (approval_id, approval_status, _now_iso(), intent_id),
        )
        return _refresh_intent_stats(conn, intent_id)


def update_trade_intent_status(
    intent_id: str,
    *,
    status: str,
    last_error: Optional[str] = None,
) -> TradeIntentRecord:
    """Force an intent-level status update, preserving aggregate counters."""
    _ensure_schema()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE trade_intent
               SET status = ?, last_error = COALESCE(?, last_error), updated_at = ?
             WHERE intent_id = ?
            """,
            (status, last_error, _now_iso(), intent_id),
        )
        return _refresh_intent_stats(conn, intent_id)


def list_trade_intent_order_ids(intent_id: str) -> list[dict[str, str]]:
    """Return the mapped broker order ids for a trade intent."""
    _ensure_schema()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT intent_order_id, client_order_id, order_id
              FROM intent_order
             WHERE intent_id = ? AND order_id IS NOT NULL
             ORDER BY sequence_no ASC
            """,
            (intent_id,),
        ).fetchall()
        return [
            {
                "intent_order_id": row["intent_order_id"],
                "client_order_id": row["client_order_id"],
                "order_id": row["order_id"],
            }
            for row in rows
        ]


def record_trade_intent_submission(
    *,
    intent_id: str,
    intent_order_id: str,
    order_result: OrderResult,
) -> TradeIntentRecord:
    """Persist the result of submitting one order in a trade intent."""
    _ensure_schema()
    normalized_status = _normalize_order_status(order_result=order_result)
    now = _now_iso()
    submitted_at = now if normalized_status != TradeIntentOrderStatus.PLANNED else None
    order_status = order_result.orderStatus

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE intent_order
               SET status = ?,
                   order_id = COALESCE(?, order_id),
                   ibkr_order_id = COALESCE(?, ibkr_order_id),
                   submitted_at = COALESCE(?, submitted_at),
                   updated_at = ?,
                   result_payload = ?,
                   last_error = ?
             WHERE intent_order_id = ? AND intent_id = ?
            """,
            (
                normalized_status.value,
                order_result.orderId,
                order_result.orderId,
                submitted_at,
                now,
                _json_dump(order_result.model_dump(mode="json", exclude_none=True)),
                "; ".join(order_result.errors) if order_result.errors else None,
                intent_order_id,
                intent_id,
            ),
        )

        execution_row = conn.execute(
            "SELECT execution_id FROM execution_state WHERE intent_order_id = ?",
            (intent_order_id,),
        ).fetchone()
        exec_id = execution_row["execution_id"] if execution_row is not None else f"ex_{uuid.uuid4().hex[:12]}"
        completed_at = None
        filled_quantity = 0.0
        remaining_quantity = 0.0
        avg_fill_price = 0.0
        if order_status is not None:
            filled_quantity = float(order_status.filledQuantity)
            remaining_quantity = float(order_status.remainingQuantity)
            avg_fill_price = float(order_status.avgFillPrice)
            if order_status.status in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}:
                completed_at = now
        if execution_row is None:
            conn.execute(
                """
                INSERT INTO execution_state (
                    execution_id, intent_order_id, status, order_id, ibkr_order_id,
                    filled_quantity, remaining_quantity, avg_fill_price,
                    submitted_at, completed_at, last_updated, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exec_id,
                    intent_order_id,
                    normalized_status.value,
                    order_result.orderId,
                    order_result.orderId,
                    filled_quantity,
                    remaining_quantity,
                    avg_fill_price,
                    submitted_at,
                    completed_at,
                    now,
                    "; ".join(order_result.errors) if order_result.errors else None,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE execution_state
                   SET status = ?,
                       order_id = COALESCE(?, order_id),
                       ibkr_order_id = COALESCE(?, ibkr_order_id),
                       filled_quantity = ?,
                       remaining_quantity = ?,
                       avg_fill_price = ?,
                       submitted_at = COALESCE(?, submitted_at),
                       completed_at = COALESCE(?, completed_at),
                       last_updated = ?,
                       error_message = ?
                 WHERE intent_order_id = ?
                """,
                (
                    normalized_status.value,
                    order_result.orderId,
                    order_result.orderId,
                    filled_quantity,
                    remaining_quantity,
                    avg_fill_price,
                    submitted_at,
                    completed_at,
                    now,
                    "; ".join(order_result.errors) if order_result.errors else None,
                    intent_order_id,
                ),
            )

        return _refresh_intent_stats(conn, intent_id)


def record_trade_intent_reconcile(
    *,
    intent_id: str,
    intent_order_id: str,
    order_status: OrderStatus,
) -> TradeIntentRecord:
    """Persist the latest broker order status during reconciliation."""
    _ensure_schema()
    normalized_status = _normalize_order_status(order_status=order_status)
    now = _now_iso()
    completed_at = (
        now
        if order_status.status in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}
        else None
    )
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE intent_order
               SET status = ?,
                   order_id = COALESCE(?, order_id),
                   ibkr_order_id = COALESCE(?, ibkr_order_id),
                   updated_at = ?,
                   last_error = NULL
             WHERE intent_order_id = ? AND intent_id = ?
            """,
            (
                normalized_status.value,
                order_status.orderId,
                order_status.orderId,
                now,
                intent_order_id,
                intent_id,
            ),
        )
        execution_row = conn.execute(
            "SELECT execution_id FROM execution_state WHERE intent_order_id = ?",
            (intent_order_id,),
        ).fetchone()
        exec_id = execution_row["execution_id"] if execution_row is not None else f"ex_{uuid.uuid4().hex[:12]}"
        if execution_row is None:
            conn.execute(
                """
                INSERT INTO execution_state (
                    execution_id, intent_order_id, status, order_id, ibkr_order_id,
                    filled_quantity, remaining_quantity, avg_fill_price,
                    submitted_at, completed_at, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exec_id,
                    intent_order_id,
                    normalized_status.value,
                    order_status.orderId,
                    order_status.orderId,
                    float(order_status.filledQuantity),
                    float(order_status.remainingQuantity),
                    float(order_status.avgFillPrice),
                    order_status.lastUpdate.isoformat(),
                    completed_at,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE execution_state
                   SET status = ?,
                       order_id = COALESCE(?, order_id),
                       ibkr_order_id = COALESCE(?, ibkr_order_id),
                       filled_quantity = ?,
                       remaining_quantity = ?,
                       avg_fill_price = ?,
                       submitted_at = COALESCE(?, submitted_at),
                       completed_at = COALESCE(?, completed_at),
                       last_updated = ?,
                       error_message = NULL
                 WHERE intent_order_id = ?
                """,
                (
                    normalized_status.value,
                    order_status.orderId,
                    order_status.orderId,
                    float(order_status.filledQuantity),
                    float(order_status.remainingQuantity),
                    float(order_status.avgFillPrice),
                    order_status.lastUpdate.isoformat(),
                    completed_at,
                    now,
                    intent_order_id,
                ),
            )
        return _refresh_intent_stats(conn, intent_id)


def record_trade_intent_cancellation(
    *,
    intent_id: str,
    intent_order_id: str,
    cancel_result: CancelResult,
) -> TradeIntentRecord:
    """Persist a cancel result for one order inside a trade intent."""
    _ensure_schema()
    normalized_status = _normalize_order_status(cancel_result=cancel_result)
    now = _now_iso()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE intent_order
               SET status = ?,
                   updated_at = ?,
                   last_error = CASE WHEN ? = 'CANCELLED' THEN NULL ELSE ? END
             WHERE intent_order_id = ? AND intent_id = ?
            """,
            (
                normalized_status.value,
                now,
                cancel_result.status,
                cancel_result.message,
                intent_order_id,
                intent_id,
            ),
        )
        conn.execute(
            """
            UPDATE execution_state
               SET status = ?,
                   completed_at = COALESCE(completed_at, ?),
                   last_updated = ?,
                   error_message = CASE WHEN ? = 'CANCELLED' THEN NULL ELSE ? END
             WHERE intent_order_id = ?
            """,
            (
                normalized_status.value,
                now,
                now,
                cancel_result.status,
                cancel_result.message,
                intent_order_id,
            ),
        )
        return _refresh_intent_stats(conn, intent_id)


def record_position_snapshot(
    *,
    account_id: str,
    snapshot_type: str,
    payload: dict[str, Any],
) -> str:
    """Persist a lightweight JSON snapshot tied to reconciliation."""
    _ensure_schema()
    snapshot_id = f"ps_{uuid.uuid4().hex[:12]}"
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO position_snapshot (snapshot_id, account_id, snapshot_type, created_at, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (snapshot_id, account_id, snapshot_type, _now_iso(), _json_dump(payload)),
        )
    return snapshot_id
