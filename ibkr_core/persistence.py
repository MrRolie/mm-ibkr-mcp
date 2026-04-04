"""
Database persistence layer for audit logs and order history.

Provides:
- SQLite database connection management
- Audit log recording with correlation IDs
- Order history tracking (full lifecycle)
- Query functions for audit logs and orders
- Schema migrations
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ibkr_core.logging_config import get_correlation_id, log_with_context

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_DB_PATH = "./data/audit.db"
SCHEMA_VERSION = 2


def _utc_now_iso() -> str:
    """Return an ISO 8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> str:
    """
    Get database path from runtime config or default.

    Returns:
        Path to SQLite database file
    """
    from ibkr_core.config import get_config

    config = get_config()
    return config.audit_db_path or DEFAULT_DB_PATH


# =============================================================================
# Database Connection
# =============================================================================


@contextmanager
def get_db_connection(db_path: Optional[str] = None):
    """
    Get a database connection context manager.

    Args:
        db_path: Optional database path override

    Yields:
        sqlite3.Connection: Database connection

    Example:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audit_log")
    """
    path = db_path or get_db_path()

    # Ensure directory exists
    db_dir = Path(path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    # Connect with row factory for dict-like access
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =============================================================================
# Schema Management
# =============================================================================

AUDIT_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT NOT NULL,
    user_context TEXT,
    account_id TEXT,
    strategy_id TEXT,
    virtual_subaccount_id TEXT,
    UNIQUE(correlation_id, event_type, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_log(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_account ON audit_log(account_id);
CREATE INDEX IF NOT EXISTS idx_audit_strategy ON audit_log(strategy_id);
CREATE INDEX IF NOT EXISTS idx_audit_virtual_subaccount ON audit_log(virtual_subaccount_id);
"""

ORDER_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_history (
    order_id TEXT PRIMARY KEY,
    correlation_id TEXT,
    account_id TEXT NOT NULL,
    strategy_id TEXT,
    virtual_subaccount_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    placed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ibkr_order_id TEXT,
    preview_data TEXT,
    fill_data TEXT,
    config_snapshot TEXT,
    market_snapshot TEXT,
    UNIQUE(order_id)
);

CREATE INDEX IF NOT EXISTS idx_order_correlation ON order_history(correlation_id);
CREATE INDEX IF NOT EXISTS idx_order_account ON order_history(account_id);
CREATE INDEX IF NOT EXISTS idx_order_symbol ON order_history(symbol);
CREATE INDEX IF NOT EXISTS idx_order_status ON order_history(status);
CREATE INDEX IF NOT EXISTS idx_order_placed_at ON order_history(placed_at);
CREATE INDEX IF NOT EXISTS idx_order_ibkr_id ON order_history(ibkr_order_id);
CREATE INDEX IF NOT EXISTS idx_order_strategy ON order_history(strategy_id);
CREATE INDEX IF NOT EXISTS idx_order_virtual_subaccount ON order_history(virtual_subaccount_id);
"""

SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _get_table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row["name"] for row in cursor.fetchall()}


def _add_column_if_missing(
    cursor: sqlite3.Cursor,
    table_name: str,
    column_name: str,
    column_def: str,
) -> bool:
    columns = _get_table_columns(cursor, table_name)
    if column_name in columns:
        return False
    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
    return True


def _migrate_to_v2(cursor: sqlite3.Cursor) -> None:
    """
    Migrate database to schema version 2.

    Changes:
    - Add strategy_id and virtual_subaccount_id columns to audit_log
    - Add strategy_id and virtual_subaccount_id columns to order_history
    """
    log_with_context(logger, logging.INFO, "Migrating audit database to schema version 2")

    if _table_exists(cursor, "audit_log"):
        _add_column_if_missing(cursor, "audit_log", "strategy_id", "TEXT")
        _add_column_if_missing(cursor, "audit_log", "virtual_subaccount_id", "TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_strategy ON audit_log(strategy_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_virtual_subaccount ON audit_log(virtual_subaccount_id)"
        )

    if _table_exists(cursor, "order_history"):
        _add_column_if_missing(cursor, "order_history", "strategy_id", "TEXT")
        _add_column_if_missing(cursor, "order_history", "virtual_subaccount_id", "TEXT")
        cursor.execute(
            """
            UPDATE order_history
            SET virtual_subaccount_id = COALESCE(virtual_subaccount_id, strategy_id)
            WHERE virtual_subaccount_id IS NULL
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_strategy ON order_history(strategy_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_virtual_subaccount ON order_history(virtual_subaccount_id)"
        )


def init_database(db_path: Optional[str] = None) -> None:
    """
    Initialize database schema.

    Creates tables and indexes if they don't exist.

    Args:
        db_path: Optional database path override
    """
    path = db_path or get_db_path()

    log_with_context(
        logger,
        logging.INFO,
        "Initializing audit database",
        db_path=path,
        schema_version=SCHEMA_VERSION,
    )

    with get_db_connection(path) as conn:
        cursor = conn.cursor()

        # Create schema version table
        cursor.executescript(SCHEMA_VERSION_TABLE)

        # Check current schema version
        cursor.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        current_version = row["version"] if row else 0

        if current_version < SCHEMA_VERSION:
            if current_version < 2:
                _migrate_to_v2(cursor)
            # Apply schema
            cursor.executescript(AUDIT_LOG_SCHEMA)
            cursor.executescript(ORDER_HISTORY_SCHEMA)

            # Update schema version
            cursor.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _utc_now_iso()),
            )

            log_with_context(
                logger,
                logging.INFO,
                "Database schema initialized",
                db_path=path,
                schema_version=SCHEMA_VERSION,
                previous_version=current_version,
            )
        else:
            log_with_context(
                logger,
                logging.DEBUG,
                "Database schema already up to date",
                db_path=path,
                schema_version=SCHEMA_VERSION,
            )


# =============================================================================
# Audit Log Functions
# =============================================================================


def record_audit_event(
    event_type: str,
    event_data: Dict[str, Any],
    correlation_id: Optional[str] = None,
    user_context: Optional[Dict[str, Any]] = None,
    account_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    virtual_subaccount_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Record an audit event to the database.

    Args:
        event_type: Type of event (e.g., "ORDER_PREVIEW", "ORDER_SUBMIT", "ORDER_FILLED")
        event_data: Event data as dictionary (will be JSON serialized)
        correlation_id: Optional correlation ID (uses current context if not provided)
        user_context: Optional user/session context (API key, IP, etc.)
        account_id: Optional account ID for multi-account tracking
        strategy_id: Optional strategy identifier for virtual subaccount tracking
        virtual_subaccount_id: Optional virtual subaccount identifier
        db_path: Optional database path override

    Returns:
        int: ID of inserted audit log record

    Example:
        record_audit_event(
            "ORDER_PREVIEW",
            {"order_id": "123", "symbol": "AAPL", "quantity": 100},
            account_id="DU12345"
        )
    """
    # Get correlation ID from context if not provided
    if correlation_id is None:
        correlation_id = get_correlation_id()

    timestamp = _utc_now_iso()

    # Extract strategy metadata from the event payload when present
    if strategy_id is None:
        strategy_id = event_data.get("strategyId") or event_data.get("strategy_id")
    if virtual_subaccount_id is None:
        virtual_subaccount_id = event_data.get("virtualSubaccountId") or event_data.get(
            "virtual_subaccount_id"
        )
    if virtual_subaccount_id is None:
        virtual_subaccount_id = strategy_id

    # Try to resolve missing metadata from order_history when order_id is provided
    order_id = event_data.get("order_id") or event_data.get("orderId")

    # Serialize data
    event_data_json = json.dumps(event_data)
    user_context_json = json.dumps(user_context) if user_context else None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        if order_id and (strategy_id is None or virtual_subaccount_id is None):
            try:
                cursor.execute(
                    "SELECT strategy_id, virtual_subaccount_id FROM order_history WHERE order_id = ?",
                    (order_id,),
                )
                order_row = cursor.fetchone()
                if order_row:
                    if strategy_id is None:
                        strategy_id = order_row["strategy_id"]
                    if virtual_subaccount_id is None:
                        virtual_subaccount_id = order_row["virtual_subaccount_id"]
            except sqlite3.OperationalError:
                # order_history may not exist yet during first-run initialization
                pass

        cursor.execute(
            """
            INSERT INTO audit_log
            (
                correlation_id, timestamp, event_type, event_data,
                user_context, account_id, strategy_id, virtual_subaccount_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                correlation_id,
                timestamp,
                event_type,
                event_data_json,
                user_context_json,
                account_id,
                strategy_id,
                virtual_subaccount_id,
            ),
        )

        log_with_context(
            logger,
            logging.DEBUG,
            "Audit event recorded",
            event_type=event_type,
            audit_id=cursor.lastrowid,
            account_id=account_id,
            strategy_id=strategy_id,
            virtual_subaccount_id=virtual_subaccount_id,
        )

        return cursor.lastrowid


def query_audit_log(
    event_type: Optional[str] = None,
    correlation_id: Optional[str] = None,
    account_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[str] = None,
    strategy_id: Optional[str] = None,
    virtual_subaccount_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query audit log with filters.

    Args:
        event_type: Filter by event type
        correlation_id: Filter by correlation ID
        account_id: Filter by account ID
        start_time: Filter by start time (ISO format)
        end_time: Filter by end time (ISO format)
        limit: Maximum number of results
        offset: Offset for pagination
        db_path: Optional database path override
        strategy_id: Filter by strategy identifier
        virtual_subaccount_id: Filter by virtual subaccount identifier

    Returns:
        List of audit log records as dictionaries
    """
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    if correlation_id:
        query += " AND correlation_id = ?"
        params.append(correlation_id)

    if account_id:
        query += " AND account_id = ?"
        params.append(account_id)

    if strategy_id:
        query += " AND strategy_id = ?"
        params.append(strategy_id)

    if virtual_subaccount_id:
        query += " AND virtual_subaccount_id = ?"
        params.append(virtual_subaccount_id)

    if start_time:
        query += " AND timestamp >= ?"
        params.append(start_time)

    if end_time:
        query += " AND timestamp <= ?"
        params.append(end_time)

    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            record = dict(row)
            # Deserialize JSON fields
            record["event_data"] = json.loads(record["event_data"])
            if record["user_context"]:
                record["user_context"] = json.loads(record["user_context"])
            results.append(record)

        return results


# =============================================================================
# Order History Functions
# =============================================================================


def save_order(
    order_id: str,
    account_id: str,
    symbol: str,
    side: str,
    quantity: float,
    order_type: str,
    status: str,
    correlation_id: Optional[str] = None,
    ibkr_order_id: Optional[str] = None,
    preview_data: Optional[Dict[str, Any]] = None,
    fill_data: Optional[Dict[str, Any]] = None,
    config_snapshot: Optional[Dict[str, Any]] = None,
    market_snapshot: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    strategy_id: Optional[str] = None,
    virtual_subaccount_id: Optional[str] = None,
) -> None:
    """
    Save or update order in order history.

    Args:
        order_id: Our internal order ID
        account_id: IBKR account ID (required for multi-account support)
        symbol: Symbol (e.g., "AAPL", "MES")
        side: "BUY" or "SELL"
        quantity: Order quantity
        order_type: Order type (MKT, LMT, etc.)
        status: Order status
        correlation_id: Optional correlation ID
        ibkr_order_id: IBKR's order ID
        preview_data: Order preview data
        fill_data: Fill/execution data
        config_snapshot: Configuration and control state at order time
        market_snapshot: Market conditions at order time (quote data)
        db_path: Optional database path override
        strategy_id: Optional strategy identifier for virtual subaccount tracking
        virtual_subaccount_id: Optional virtual subaccount identifier
    """
    # Get correlation ID from context if not provided
    if correlation_id is None:
        correlation_id = get_correlation_id()

    if virtual_subaccount_id is None:
        virtual_subaccount_id = strategy_id

    timestamp = _utc_now_iso()

    # Serialize JSON fields
    preview_json = json.dumps(preview_data) if preview_data else None
    fill_json = json.dumps(fill_data) if fill_data else None
    config_json = json.dumps(config_snapshot) if config_snapshot else None
    market_json = json.dumps(market_snapshot) if market_snapshot else None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check if order exists
        cursor.execute("SELECT order_id FROM order_history WHERE order_id = ?", (order_id,))
        exists = cursor.fetchone()

        if exists:
            # Update existing order
            cursor.execute(
                """
                UPDATE order_history
                SET status = ?, updated_at = ?, ibkr_order_id = ?,
                    fill_data = COALESCE(?, fill_data),
                    correlation_id = COALESCE(?, correlation_id),
                    strategy_id = COALESCE(?, strategy_id),
                    virtual_subaccount_id = COALESCE(?, virtual_subaccount_id)
                WHERE order_id = ?
                """,
                (
                    status,
                    timestamp,
                    ibkr_order_id,
                    fill_json,
                    correlation_id,
                    strategy_id,
                    virtual_subaccount_id,
                    order_id,
                ),
            )

            log_with_context(
                logger,
                logging.DEBUG,
                "Order updated in history",
                order_id=order_id,
                account_id=account_id,
                status=status,
            )
        else:
            # Insert new order
            cursor.execute(
                """
                INSERT INTO order_history
                (
                    order_id, correlation_id, account_id, strategy_id, virtual_subaccount_id,
                    symbol, side, quantity, order_type, status, placed_at, updated_at,
                    ibkr_order_id, preview_data, fill_data, config_snapshot, market_snapshot
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    correlation_id,
                    account_id,
                    strategy_id,
                    virtual_subaccount_id,
                    symbol,
                    side,
                    quantity,
                    order_type,
                    status,
                    timestamp,
                    timestamp,
                    ibkr_order_id,
                    preview_json,
                    fill_json,
                    config_json,
                    market_json,
                ),
            )

            log_with_context(
                logger,
                logging.DEBUG,
                "Order saved to history",
                order_id=order_id,
                account_id=account_id,
                symbol=symbol,
                status=status,
            )


def update_order_status(
    order_id: str,
    status: str,
    fill_data: Optional[Dict[str, Any]] = None,
    ibkr_order_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """
    Update order status and optional fill data.

    Args:
        order_id: Our internal order ID
        status: New status
        fill_data: Optional fill/execution data
        ibkr_order_id: Optional IBKR order ID
        db_path: Optional database path override

    Returns:
        bool: True if order was found and updated, False otherwise
    """
    timestamp = _utc_now_iso()
    fill_json = json.dumps(fill_data) if fill_data else None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        if fill_json:
            cursor.execute(
                """
                UPDATE order_history
                SET status = ?, updated_at = ?, fill_data = ?,
                    ibkr_order_id = COALESCE(?, ibkr_order_id)
                WHERE order_id = ?
                """,
                (status, timestamp, fill_json, ibkr_order_id, order_id),
            )
        else:
            cursor.execute(
                """
                UPDATE order_history
                SET status = ?, updated_at = ?,
                    ibkr_order_id = COALESCE(?, ibkr_order_id)
                WHERE order_id = ?
                """,
                (status, timestamp, ibkr_order_id, order_id),
            )

        updated = cursor.rowcount > 0

        if updated:
            log_with_context(
                logger,
                logging.DEBUG,
                "Order status updated",
                order_id=order_id,
                status=status,
            )

        return updated


def get_order(order_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get order by ID.

    Args:
        order_id: Our internal order ID
        db_path: Optional database path override

    Returns:
        Order record as dictionary, or None if not found
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM order_history WHERE order_id = ?", (order_id,))

        row = cursor.fetchone()
        if not row:
            return None

        record = dict(row)

        # Deserialize JSON fields
        if record["preview_data"]:
            record["preview_data"] = json.loads(record["preview_data"])
        if record["fill_data"]:
            record["fill_data"] = json.loads(record["fill_data"])
        if record["config_snapshot"]:
            record["config_snapshot"] = json.loads(record["config_snapshot"])
        if record["market_snapshot"]:
            record["market_snapshot"] = json.loads(record["market_snapshot"])

        return record


def query_orders(
    account_id: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    correlation_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[str] = None,
    strategy_id: Optional[str] = None,
    virtual_subaccount_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query orders with filters.

    Args:
        account_id: Filter by account ID (important for multi-account)
        symbol: Filter by symbol
        status: Filter by status
        correlation_id: Filter by correlation ID
        start_time: Filter by start time (ISO format)
        end_time: Filter by end time (ISO format)
        limit: Maximum number of results
        offset: Offset for pagination
        db_path: Optional database path override
        strategy_id: Filter by strategy identifier
        virtual_subaccount_id: Filter by virtual subaccount identifier

    Returns:
        List of order records as dictionaries
    """
    query = "SELECT * FROM order_history WHERE 1=1"
    params = []

    if account_id:
        query += " AND account_id = ?"
        params.append(account_id)

    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)

    if status:
        query += " AND status = ?"
        params.append(status)

    if correlation_id:
        query += " AND correlation_id = ?"
        params.append(correlation_id)

    if strategy_id:
        query += " AND strategy_id = ?"
        params.append(strategy_id)

    if virtual_subaccount_id:
        query += " AND virtual_subaccount_id = ?"
        params.append(virtual_subaccount_id)

    if start_time:
        query += " AND placed_at >= ?"
        params.append(start_time)

    if end_time:
        query += " AND placed_at <= ?"
        params.append(end_time)

    query += " ORDER BY placed_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            record = dict(row)

            # Deserialize JSON fields
            if record["preview_data"]:
                record["preview_data"] = json.loads(record["preview_data"])
            if record["fill_data"]:
                record["fill_data"] = json.loads(record["fill_data"])
            if record["config_snapshot"]:
                record["config_snapshot"] = json.loads(record["config_snapshot"])
            if record["market_snapshot"]:
                record["market_snapshot"] = json.loads(record["market_snapshot"])

            results.append(record)

        return results


# =============================================================================
# Database Utilities
# =============================================================================


def get_database_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Get database statistics.

    Returns:
        Dictionary with database stats (table counts, size, etc.)
    """
    path = db_path or get_db_path()

    with get_db_connection(path) as conn:
        cursor = conn.cursor()

        # Get table counts
        cursor.execute("SELECT COUNT(*) as count FROM audit_log")
        audit_count = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM order_history")
        order_count = cursor.fetchone()["count"]

        # Get database file size
        db_size_bytes = Path(path).stat().st_size if Path(path).exists() else 0

        return {
            "db_path": path,
            "db_size_bytes": db_size_bytes,
            "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
            "audit_log_count": audit_count,
            "order_history_count": order_count,
            "schema_version": SCHEMA_VERSION,
        }


# =============================================================================
# Module Initialization
# =============================================================================

# Auto-initialize database on module import
try:
    init_database()
except Exception as e:
    logger.warning(f"Failed to auto-initialize audit database: {e}")
