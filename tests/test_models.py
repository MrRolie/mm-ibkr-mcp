"""
Unit tests for Pydantic models.

Tests validation and serialization of models against schema definitions.
"""

from datetime import datetime
from typing import Dict

import pytest

from ibkr_core.models import (
    AccountPnl,
    AccountSummary,
    Bar,
    CancelResult,
    OrderPreview,
    OrderResult,
    OrderSpec,
    OrderStatus,
    PnlDetail,
    Position,
    Quote,
    SymbolSpec,
)


class TestSymbolSpec:
    """Test SymbolSpec model."""

    def test_valid_stock_symbol(self):
        """Test valid stock symbol spec."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")
        assert spec.symbol == "AAPL"
        assert spec.securityType == "STK"

    def test_security_type_is_normalized(self):
        """Test security type normalization for LLM-friendly lowercase input."""
        spec = SymbolSpec(symbol="AAPL", securityType="stk")
        assert spec.securityType == "STK"

    def test_valid_futures_symbol(self):
        """Test valid futures symbol spec."""
        spec = SymbolSpec(symbol="MES", securityType="FUT", exchange="GLOBEX", currency="USD")
        assert spec.symbol == "MES"
        assert spec.securityType == "FUT"

    def test_invalid_security_type(self):
        """Test that invalid security type raises validation error."""
        with pytest.raises(ValueError, match="securityType"):
            SymbolSpec(symbol="TEST", securityType="INVALID")

    def test_option_specs(self):
        """Test valid option symbol spec."""
        spec = SymbolSpec(
            symbol="SPX", securityType="OPT", strike=5000.0, right="C", expiry="2024-12-20"
        )
        assert spec.strike == 5000.0
        assert spec.right == "C"

    def test_option_right_is_normalized(self):
        """Test friendly option right inputs normalize to C/P."""
        spec = SymbolSpec(
            symbol="SPX",
            securityType="OPT",
            strike=5000.0,
            right="call",
            expiry="2024-12-20",
        )
        assert spec.right == "C"

    def test_invalid_option_right(self):
        """Test that invalid option right raises validation error."""
        with pytest.raises(ValueError, match="right"):
            SymbolSpec(symbol="SPX", securityType="OPT", right="X")


class TestQuote:
    """Test Quote model."""

    def test_valid_quote(self):
        """Test valid quote."""
        now = datetime.now()
        quote = Quote(
            symbol="AAPL",
            conId=265598,
            bid=150.0,
            ask=150.10,
            last=150.05,
            bidSize=100,
            askSize=100,
            lastSize=50,
            volume=1000000,
            timestamp=now,
            source="IBKR_REALTIME",
        )
        assert quote.symbol == "AAPL"
        assert quote.bid == 150.0

    def test_minimal_quote(self):
        """Test minimal quote (only required fields)."""
        now = datetime.now()
        quote = Quote(symbol="AAPL", conId=265598, timestamp=now, source="IBKR_REALTIME")
        assert quote.bid == 0.0  # defaults
        assert quote.ask == 0.0


class TestBar:
    """Test Bar model."""

    def test_valid_bar(self):
        """Test valid bar."""
        now = datetime.now()
        bar = Bar(
            symbol="AAPL",
            time=now,
            open=150.0,
            high=151.0,
            low=149.0,
            close=150.5,
            volume=1000000,
            barSize="1 day",
            source="IBKR_HISTORICAL",
        )
        assert bar.symbol == "AAPL"
        assert bar.close == 150.5


class TestAccountSummary:
    """Test AccountSummary model."""

    def test_valid_account_summary(self):
        """Test valid account summary."""
        now = datetime.now()
        summary = AccountSummary(
            accountId="DU123456",
            currency="USD",
            netLiquidation=100000.0,
            cash=50000.0,
            buyingPower=100000.0,
            marginExcess=0.0,
            maintenanceMargin=10000.0,
            initialMargin=15000.0,
            timestamp=now,
        )
        assert summary.accountId == "DU123456"
        assert summary.netLiquidation == 100000.0


class TestPosition:
    """Test Position model."""

    def test_valid_position(self):
        """Test valid position."""
        pos = Position(
            accountId="DU123456",
            symbol="AAPL",
            conId=265598,
            assetClass="STK",
            currency="USD",
            quantity=100.0,
            avgPrice=150.0,
            marketPrice=152.0,
            marketValue=15200.0,
            unrealizedPnl=200.0,
            realizedPnl=500.0,
        )
        assert pos.quantity == 100.0
        assert pos.unrealizedPnl == 200.0

    def test_short_position(self):
        """Test short position (negative quantity)."""
        pos = Position(
            accountId="DU123456",
            symbol="AAPL",
            conId=265598,
            assetClass="STK",
            currency="USD",
            quantity=-100.0,
            avgPrice=150.0,
            marketPrice=152.0,
            marketValue=-15200.0,
            unrealizedPnl=-200.0,
            realizedPnl=0.0,
        )
        assert pos.quantity == -100.0


class TestAccountPnl:
    """Test AccountPnl model."""

    def test_valid_account_pnl(self):
        """Test valid account P&L."""
        now = datetime.now()
        pnl = AccountPnl(
            accountId="DU123456",
            currency="USD",
            timeframe="INTRADAY",
            realized=500.0,
            unrealized=200.0,
            bySymbol={
                "AAPL": PnlDetail(symbol="AAPL", currency="USD", realized=200.0, unrealized=100.0)
            },
            timestamp=now,
        )
        assert pnl.realized == 500.0
        assert "AAPL" in pnl.bySymbol


class TestOrderSpec:
    """Test OrderSpec model."""

    def test_valid_market_order(self):
        """Test valid market order spec."""
        spec = OrderSpec(
            instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
            side="buy",
            quantity=100.0,
            orderType="mkt",
        )
        assert spec.side == "BUY"
        assert spec.quantity == 100.0
        assert spec.orderType == "MKT"
        assert spec.tif == "DAY"  # default

    def test_valid_limit_order(self):
        """Test valid limit order spec."""
        spec = OrderSpec(
            instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
            side="SELL",
            quantity=50.0,
            orderType="LMT",
            limitPrice=155.0,
            tif="GTC",
        )
        assert spec.orderType == "LMT"
        assert spec.limitPrice == 155.0

    def test_invalid_side(self):
        """Test that invalid side raises validation error."""
        with pytest.raises(ValueError, match="side"):
            OrderSpec(
                instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
                side="INVALID",
                quantity=100.0,
                orderType="MKT",
            )

    def test_invalid_order_type(self):
        """Test that invalid order type raises validation error."""
        with pytest.raises(ValueError, match="orderType"):
            OrderSpec(
                instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
                side="BUY",
                quantity=100.0,
                orderType="INVALID",
            )

    def test_zero_or_negative_quantity(self):
        """Test that zero or negative quantity raises validation error."""
        with pytest.raises(ValueError):
            OrderSpec(
                instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
                side="BUY",
                quantity=0.0,
                orderType="MKT",
            )

        with pytest.raises(ValueError):
            OrderSpec(
                instrument=SymbolSpec(symbol="AAPL", securityType="STK"),
                side="BUY",
                quantity=-100.0,
                orderType="MKT",
            )


class TestOrderStatus:
    """Test OrderStatus model."""

    def test_valid_submitted_order(self):
        """Test valid submitted order status."""
        now = datetime.now()
        status = OrderStatus(
            orderId="123456",
            status="SUBMITTED",
            filledQuantity=0.0,
            remainingQuantity=100.0,
            avgFillPrice=0.0,
            lastUpdate=now,
        )
        assert status.status == "SUBMITTED"

    def test_valid_filled_order(self):
        """Test valid filled order status."""
        now = datetime.now()
        status = OrderStatus(
            orderId="123456",
            status="FILLED",
            filledQuantity=100.0,
            remainingQuantity=0.0,
            avgFillPrice=150.05,
            lastUpdate=now,
        )
        assert status.filledQuantity == 100.0


class TestOrderResult:
    """Test OrderResult model."""

    def test_accepted_result(self):
        """Test accepted order result."""
        result = OrderResult(
            orderId="123456",
            status="ACCEPTED",
            orderStatus=OrderStatus(
                orderId="123456",
                status="SUBMITTED",
                filledQuantity=0.0,
                remainingQuantity=100.0,
                avgFillPrice=0.0,
                lastUpdate=datetime.now(),
            ),
        )
        assert result.status == "ACCEPTED"

    def test_simulated_result(self):
        """Test simulated order result (for disabled trading)."""
        result = OrderResult(status="SIMULATED", errors=["Trading is disabled"])
        assert result.status == "SIMULATED"

    def test_rejected_result(self):
        """Test rejected order result."""
        result = OrderResult(status="REJECTED", errors=["Insufficient buying power"])
        assert result.status == "REJECTED"


class TestCancelResult:
    """Test CancelResult model."""

    def test_cancelled_result(self):
        """Test cancelled order result."""
        result = CancelResult(orderId="123456", status="CANCELLED")
        assert result.status == "CANCELLED"

    def test_already_filled_result(self):
        """Test already-filled cancel result."""
        result = CancelResult(
            orderId="123456", status="ALREADY_FILLED", message="Order was already filled"
        )
        assert result.status == "ALREADY_FILLED"
