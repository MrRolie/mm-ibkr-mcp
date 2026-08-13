"""
Tests for audit log persistence.

Verifies that:
- Audit events are recorded correctly
- Audit events can be queried with various filters
- Correlation IDs are tracked properly
- Account IDs are recorded for multi-account support
"""

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from ibkr_core.logging_config import set_correlation_id
from ibkr_core.persistence import (
    get_database_stats,
    init_database,
    query_audit_log,
    query_orders,
    record_audit_event,
    save_order,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Initialize schema
    init_database(db_path)

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


class TestAuditEventRecording:
    """Test recording audit events."""

    def test_record_simple_event(self, temp_db):
        """Test recording a simple audit event."""
        event_id = record_audit_event(
            event_type="TEST_EVENT",
            event_data={"action": "test", "value": 123},
            correlation_id="test-correlation-123",
            db_path=temp_db,
        )

        assert event_id is not None
        assert isinstance(event_id, int)
        assert event_id > 0

    def test_record_event_with_correlation_context(self, temp_db):
        """Test that correlation ID is pulled from context if not provided."""
        set_correlation_id("context-correlation-456")

        event_id = record_audit_event(
            event_type="CONTEXT_TEST",
            event_data={"test": "value"},
            db_path=temp_db,
        )

        # Query back and verify correlation ID
        events = query_audit_log(correlation_id="context-correlation-456", db_path=temp_db)
        assert len(events) == 1
        assert events[0]["correlation_id"] == "context-correlation-456"

    def test_record_event_with_account_id(self, temp_db):
        """Test recording event with account ID."""
        record_audit_event(
            event_type="ORDER_PREVIEW",
            event_data={"symbol": "AAPL", "quantity": 100},
            account_id="DU12345",
            db_path=temp_db,
        )

        events = query_audit_log(account_id="DU12345", db_path=temp_db)
        assert len(events) == 1
        assert events[0]["account_id"] == "DU12345"

    def test_record_event_with_strategy_metadata(self, temp_db):
        """Strategy metadata should be stored and queryable."""
        record_audit_event(
            event_type="ORDER_PREVIEW",
            event_data={"symbol": "AAPL", "quantity": 100},
            strategy_id="pe_rebalance",
            virtual_subaccount_id="pe_rebalance",
            db_path=temp_db,
        )

        events = query_audit_log(strategy_id="pe_rebalance", db_path=temp_db)
        assert len(events) == 1
        assert events[0]["strategy_id"] == "pe_rebalance"
        assert events[0]["virtual_subaccount_id"] == "pe_rebalance"

    def test_record_event_with_user_context(self, temp_db):
        """Test recording event with user context."""
        user_ctx = {"api_key": "test-key", "client_ip": "192.168.1.1"}

        record_audit_event(
            event_type="API_CALL",
            event_data={"endpoint": "/orders"},
            user_context=user_ctx,
            db_path=temp_db,
        )

        events = query_audit_log(event_type="API_CALL", db_path=temp_db)
        assert len(events) == 1
        assert events[0]["user_context"] == user_ctx

    def test_record_multiple_events(self, temp_db):
        """Test recording multiple events."""
        for i in range(5):
            record_audit_event(
                event_type="BATCH_TEST",
                event_data={"index": i},
                correlation_id=f"batch-{i}",
                db_path=temp_db,
            )

        events = query_audit_log(event_type="BATCH_TEST", limit=10, db_path=temp_db)
        assert len(events) == 5


class TestAuditLogQuerying:
    """Test querying audit log."""

    @pytest.fixture(autouse=True)
    def setup_test_data(self, temp_db):
        """Set up test audit data."""
        self.db = temp_db

        # Order preview events
        record_audit_event(
            event_type="ORDER_PREVIEW",
            event_data={"symbol": "AAPL", "quantity": 100},
            correlation_id="corr-001",
            account_id="DU12345",
            db_path=temp_db,
        )

        record_audit_event(
            event_type="ORDER_PREVIEW",
            event_data={"symbol": "GOOGL", "quantity": 50},
            correlation_id="corr-002",
            account_id="DU12345",
            db_path=temp_db,
        )

        # Order submit events
        record_audit_event(
            event_type="ORDER_SUBMIT",
            event_data={"symbol": "AAPL", "order_id": "ord-001"},
            correlation_id="corr-001",
            account_id="DU12345",
            db_path=temp_db,
        )

        # Different account
        record_audit_event(
            event_type="ORDER_PREVIEW",
            event_data={"symbol": "MSFT", "quantity": 75},
            correlation_id="corr-003",
            account_id="DU67890",
            db_path=temp_db,
        )

    def test_query_all_events(self, temp_db):
        """Test querying all events."""
        events = query_audit_log(limit=100, db_path=temp_db)
        assert len(events) >= 4

    def test_query_by_event_type(self, temp_db):
        """Test filtering by event type."""
        events = query_audit_log(event_type="ORDER_PREVIEW", db_path=temp_db)
        assert len(events) == 3
        for event in events:
            assert event["event_type"] == "ORDER_PREVIEW"

    def test_query_by_correlation_id(self, temp_db):
        """Test filtering by correlation ID."""
        events = query_audit_log(correlation_id="corr-001", db_path=temp_db)
        assert len(events) == 2  # Preview and submit
        for event in events:
            assert event["correlation_id"] == "corr-001"

    def test_query_by_account_id(self, temp_db):
        """Test filtering by account ID (important for multi-account)."""
        events = query_audit_log(account_id="DU12345", db_path=temp_db)
        assert len(events) == 3

        events_other = query_audit_log(account_id="DU67890", db_path=temp_db)
        assert len(events_other) == 1

    def test_query_with_limit_and_offset(self, temp_db):
        """Test pagination with limit and offset."""
        # Get first 2 results
        page1 = query_audit_log(limit=2, offset=0, db_path=temp_db)
        assert len(page1) == 2

        # Get next 2 results
        page2 = query_audit_log(limit=2, offset=2, db_path=temp_db)
        assert len(page2) == 2

        # Pages should not overlap
        page1_ids = {e["id"] for e in page1}
        page2_ids = {e["id"] for e in page2}
        assert page1_ids.isdisjoint(page2_ids)

    def test_query_returns_deserialized_json(self, temp_db):
        """Test that JSON fields are deserialized."""
        events = query_audit_log(correlation_id="corr-001", db_path=temp_db)
        assert len(events) > 0

        event = events[0]
        assert isinstance(event["event_data"], dict)
        assert "symbol" in event["event_data"]

    def test_recorded_timestamps_are_timezone_aware_utc(self, temp_db):
        """Audit timestamps should be persisted as timezone-aware UTC ISO strings."""
        record_audit_event(
            event_type="UTC_TIMESTAMP_TEST",
            event_data={"symbol": "AAPL"},
            db_path=temp_db,
        )

        events = query_audit_log(event_type="UTC_TIMESTAMP_TEST", db_path=temp_db)
        parsed = datetime.fromisoformat(events[0]["timestamp"])

        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


class TestOrderStrategyPersistence:
    """Test persistence of strategy/virtual subaccount metadata."""

    def test_save_order_with_strategy_metadata(self, temp_db):
        """Order history should store strategy metadata."""
        save_order(
            order_id="ord-100",
            account_id="DU12345",
            symbol="AAPL",
            side="BUY",
            quantity=10,
            order_type="MKT",
            status="SUBMITTED",
            strategy_id="pe_rebalance",
            virtual_subaccount_id="pe_rebalance",
            db_path=temp_db,
        )

        orders = query_orders(strategy_id="pe_rebalance", db_path=temp_db)
        assert len(orders) == 1
        assert orders[0]["strategy_id"] == "pe_rebalance"
        assert orders[0]["virtual_subaccount_id"] == "pe_rebalance"

    def test_audit_event_inherits_strategy_from_order(self, temp_db):
        """Audit events should resolve strategy metadata from order history."""
        save_order(
            order_id="ord-101",
            account_id="DU12345",
            symbol="MSFT",
            side="SELL",
            quantity=5,
            order_type="MKT",
            status="SUBMITTED",
            strategy_id="intraday_reversion",
            virtual_subaccount_id="intraday_reversion",
            db_path=temp_db,
        )

        record_audit_event(
            event_type="ORDER_CANCELLED",
            event_data={"order_id": "ord-101"},
            db_path=temp_db,
        )

        events = query_audit_log(strategy_id="intraday_reversion", db_path=temp_db)
        assert len(events) == 1
        assert events[0]["strategy_id"] == "intraday_reversion"


class TestDatabaseStats:
    """Test database statistics."""

    def test_stats_on_empty_database(self, temp_db):
        """Test stats on newly initialized database."""
        # Create a fresh database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            fresh_db = f.name

        try:
            init_database(fresh_db)
            stats = get_database_stats(fresh_db)

            assert stats["audit_log_count"] == 0
            assert stats["order_history_count"] == 0
            assert stats["schema_version"] == 2
            assert stats["db_size_bytes"] > 0  # File exists
        finally:
            if os.path.exists(fresh_db):
                os.unlink(fresh_db)

    def test_stats_with_data(self, temp_db):
        """Test stats after adding data."""
        # Add some events
        for i in range(10):
            record_audit_event(
                event_type="STATS_TEST",
                event_data={"index": i},
                db_path=temp_db,
            )

        stats = get_database_stats(temp_db)
        assert stats["audit_log_count"] == 10
        assert stats["db_size_mb"] > 0


class TestDatabaseInitialization:
    """Test database initialization and schema."""

    def test_init_creates_tables(self, temp_db):
        """Test that init creates required tables."""
        import sqlite3

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'")
        assert cursor.fetchone() is not None

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='order_history'")
        assert cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        assert cursor.fetchone() is not None

        conn.close()

    def test_init_is_idempotent(self, temp_db):
        """Test that init can be called multiple times safely."""
        # Initialize again
        init_database(temp_db)
        init_database(temp_db)

        # Should still work
        stats = get_database_stats(temp_db)
        assert stats["schema_version"] == 2

    def test_schema_version_recorded(self, temp_db):
        """Test that schema version is recorded."""
        import sqlite3

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        cursor.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()

        assert row is not None
        assert row[0] == 2

        conn.close()
