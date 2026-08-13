"""
Tests for order validation logic.

Pure unit tests that verify OrderSpec validation without connecting to IBKR.

Run these tests:
    pytest tests/test_orders_validation.py -v
"""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from ibkr_core.models import OrderSpec, SymbolSpec
from ibkr_core.orders import OrderValidationError, check_safety_guards, validate_order_spec

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def order_samples():
    """Load order samples from JSON fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "order_samples.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def valid_symbol_spec():
    """Create a valid SymbolSpec for testing."""
    return SymbolSpec(
        symbol="AAPL",
        securityType="STK",
        exchange="SMART",
        currency="USD",
    )


@pytest.fixture
def valid_order_spec(valid_symbol_spec):
    """Create a valid OrderSpec for testing."""
    return OrderSpec(
        instrument=valid_symbol_spec,
        side="BUY",
        quantity=1,
        orderType="LMT",
        limitPrice=150.00,
        tif="DAY",
    )


# =============================================================================
# OrderSpec Pydantic Validation Tests
# =============================================================================


class TestOrderSpecPydanticValidation:
    """Test Pydantic model validation for OrderSpec."""

    def test_valid_market_order(self, valid_symbol_spec):
        """Test creating a valid market order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="MKT",
            tif="DAY",
        )
        assert order.side == "BUY"
        assert order.quantity == 1
        assert order.orderType == "MKT"
        assert order.tif == "DAY"
        assert order.limitPrice is None

    def test_valid_limit_order(self, valid_symbol_spec):
        """Test creating a valid limit order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=10,
            orderType="LMT",
            limitPrice=200.00,
            tif="GTC",
        )
        assert order.side == "SELL"
        assert order.quantity == 10
        assert order.orderType == "LMT"
        assert order.limitPrice == 200.00
        assert order.tif == "GTC"

    def test_valid_stop_order(self, valid_symbol_spec):
        """Test creating a valid stop order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=5,
            orderType="STP",
            stopPrice=140.00,
            tif="DAY",
        )
        assert order.orderType == "STP"
        assert order.stopPrice == 140.00

    def test_valid_stop_limit_order(self, valid_symbol_spec):
        """Test creating a valid stop-limit order."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=5,
            orderType="STP_LMT",
            stopPrice=140.00,
            limitPrice=139.00,
            tif="DAY",
        )
        assert order.orderType == "STP_LMT"
        assert order.stopPrice == 140.00
        assert order.limitPrice == 139.00

    def test_negative_quantity_rejected(self, valid_symbol_spec):
        """Test that negative quantity is rejected by Pydantic."""
        with pytest.raises(ValidationError) as exc_info:
            OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=-1,
                orderType="MKT",
                tif="DAY",
            )
        assert "quantity" in str(exc_info.value).lower()

    def test_zero_quantity_rejected(self, valid_symbol_spec):
        """Test that zero quantity is rejected by Pydantic."""
        with pytest.raises(ValidationError) as exc_info:
            OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=0,
                orderType="MKT",
                tif="DAY",
            )
        assert "quantity" in str(exc_info.value).lower()

    def test_invalid_side_rejected(self, valid_symbol_spec):
        """Test that invalid side is rejected by Pydantic."""
        with pytest.raises(ValidationError) as exc_info:
            OrderSpec(
                instrument=valid_symbol_spec,
                side="HOLD",
                quantity=1,
                orderType="MKT",
                tif="DAY",
            )
        assert "side" in str(exc_info.value).lower()

    def test_invalid_order_type_rejected(self, valid_symbol_spec):
        """Test that invalid order type is rejected by Pydantic."""
        with pytest.raises(ValidationError) as exc_info:
            OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=1,
                orderType="INVALID",
                tif="DAY",
            )
        assert "orderType" in str(exc_info.value).lower() or "order" in str(exc_info.value).lower()

    def test_invalid_tif_rejected(self, valid_symbol_spec):
        """Test that invalid TIF is rejected by Pydantic."""
        with pytest.raises(ValidationError) as exc_info:
            OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=1,
                orderType="MKT",
                tif="INVALID",
            )
        assert "tif" in str(exc_info.value).lower()

    def test_negative_limit_price_rejected(self, valid_symbol_spec):
        """Test that negative limit price is rejected by Pydantic."""
        with pytest.raises(ValidationError) as exc_info:
            OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=1,
                orderType="LMT",
                limitPrice=-10.00,
                tif="DAY",
            )
        assert "limitPrice" in str(exc_info.value) or "limit" in str(exc_info.value).lower()


# =============================================================================
# validate_order_spec() Function Tests
# =============================================================================


class TestValidateOrderSpec:
    """Test the validate_order_spec() function."""

    def test_valid_market_order_passes(self, valid_symbol_spec):
        """Test that valid market order passes validation."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="MKT",
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_valid_limit_order_passes(self, valid_symbol_spec):
        """Test that valid limit order passes validation."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=150.00,
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_market_order_with_limit_price_fails(self, valid_symbol_spec):
        """Test that market order with limit price fails validation."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="MKT",
            limitPrice=150.00,  # Should not be present for MKT
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "limit price" in errors[0].lower()

    def test_limit_order_without_limit_price_fails(self, valid_symbol_spec):
        """Test that limit order without limit price fails validation."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=None,  # Required for LMT
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "limit" in errors[0].lower()

    def test_limit_order_with_zero_price_fails(self, valid_symbol_spec):
        """Test that limit order with zero price fails validation."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=0.0,  # Must be positive
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "positive" in errors[0].lower() or "limit" in errors[0].lower()

    def test_stop_order_without_stop_price_fails(self, valid_symbol_spec):
        """Test that stop order without stop price fails validation."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=1,
            orderType="STP",
            stopPrice=None,  # Required for STP
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "stop" in errors[0].lower()

    def test_stop_limit_order_without_both_prices_fails(self, valid_symbol_spec):
        """Test that stop-limit order without both prices fails validation."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=1,
            orderType="STP_LMT",
            stopPrice=None,
            limitPrice=None,
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 2  # Missing both stop and limit
        error_text = " ".join(errors).lower()
        assert "stop" in error_text
        assert "limit" in error_text

    def test_stop_limit_order_without_stop_price_fails(self, valid_symbol_spec):
        """Test that stop-limit order without stop price fails."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=1,
            orderType="STP_LMT",
            stopPrice=None,
            limitPrice=139.00,
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "stop" in errors[0].lower()

    def test_stop_limit_order_without_limit_price_fails(self, valid_symbol_spec):
        """Test that stop-limit order without limit price fails."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=1,
            orderType="STP_LMT",
            stopPrice=140.00,
            limitPrice=None,
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert len(errors) == 1
        assert "limit" in errors[0].lower()

    def test_all_tif_values_accepted(self, valid_symbol_spec):
        """Test that all valid TIF values are accepted."""
        for tif in ["DAY", "GTC", "IOC", "FOK"]:
            order = OrderSpec(
                instrument=valid_symbol_spec,
                side="BUY",
                quantity=1,
                orderType="MKT",
                tif=tif,
            )
            errors = validate_order_spec(order)
            assert errors == [], f"TIF {tif} should be valid"

    def test_all_order_types_validated(self, valid_symbol_spec):
        """Test all order types with proper required fields."""
        # MKT - no price needed
        mkt_order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="MKT",
            tif="DAY",
        )
        assert validate_order_spec(mkt_order) == []

        # LMT - limit price required
        lmt_order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=100.00,
            tif="DAY",
        )
        assert validate_order_spec(lmt_order) == []

        # STP - stop price required
        stp_order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=1,
            orderType="STP",
            stopPrice=100.00,
            tif="DAY",
        )
        assert validate_order_spec(stp_order) == []

        # STP_LMT - both prices required
        stp_lmt_order = OrderSpec(
            instrument=valid_symbol_spec,
            side="SELL",
            quantity=1,
            orderType="STP_LMT",
            stopPrice=100.00,
            limitPrice=99.00,
            tif="DAY",
        )
        assert validate_order_spec(stp_lmt_order) == []


# =============================================================================
# Safety Guards Tests
# =============================================================================


class TestSafetyGuards:
    """Test the check_safety_guards() function."""

    def test_no_guards_returns_empty(self, valid_order_spec):
        """Test that no guards returns no warnings."""
        warnings = check_safety_guards(valid_order_spec)
        assert warnings == []

    def test_notional_guard_triggered(self, valid_order_spec):
        """Test that notional guard triggers warning."""
        warnings = check_safety_guards(
            valid_order_spec,
            estimated_notional=50000.0,
            max_notional=10000.0,
        )
        assert len(warnings) == 1
        assert "notional" in warnings[0].lower()
        assert "50,000" in warnings[0]

    def test_notional_guard_not_triggered_when_under(self, valid_order_spec):
        """Test that notional guard does not trigger when under limit."""
        warnings = check_safety_guards(
            valid_order_spec,
            estimated_notional=5000.0,
            max_notional=10000.0,
        )
        assert warnings == []

    def test_quantity_guard_triggered(self, valid_order_spec):
        """Test that quantity guard triggers warning."""
        # Create order with larger quantity
        order = OrderSpec(
            instrument=valid_order_spec.instrument,
            side="BUY",
            quantity=100,
            orderType="LMT",
            limitPrice=150.00,
            tif="DAY",
        )
        warnings = check_safety_guards(order, max_quantity=50)
        assert len(warnings) == 1
        assert "quantity" in warnings[0].lower()

    def test_quantity_guard_not_triggered_when_under(self, valid_order_spec):
        """Test that quantity guard does not trigger when under limit."""
        warnings = check_safety_guards(valid_order_spec, max_quantity=100)
        assert warnings == []

    def test_both_guards_can_trigger(self):
        """Test that both guards can trigger simultaneously."""
        spec = SymbolSpec(symbol="AAPL", securityType="STK")
        order = OrderSpec(
            instrument=spec,
            side="BUY",
            quantity=100,
            orderType="LMT",
            limitPrice=200.00,
            tif="DAY",
        )
        warnings = check_safety_guards(
            order,
            estimated_notional=20000.0,
            max_notional=10000.0,
            max_quantity=50,
        )
        assert len(warnings) == 2


# =============================================================================
# Fixture-Based Tests
# =============================================================================


class TestOrderSamplesFromFixture:
    """Test using samples from the JSON fixture file."""

    def test_valid_orders_from_fixture(self, order_samples):
        """Test that all valid order samples pass validation."""
        for sample in order_samples["valid_orders"]:
            spec_data = sample["order_spec"]

            # Create SymbolSpec
            instrument = SymbolSpec(**spec_data["instrument"])

            # Create OrderSpec
            order_data = {k: v for k, v in spec_data.items() if k != "instrument"}
            order = OrderSpec(instrument=instrument, **order_data)

            # Validate
            errors = validate_order_spec(order)
            assert (
                errors == []
            ), f"Sample '{sample['description']}' should be valid, got errors: {errors}"

    def test_invalid_orders_from_fixture_fail_pydantic(self, order_samples):
        """Test that invalid order samples fail Pydantic or our validation."""
        for sample in order_samples["invalid_orders"]:
            spec_data = sample["order_spec"]
            expected_error_keyword = sample["expected_error"].lower()

            try:
                # Create SymbolSpec
                instrument = SymbolSpec(**spec_data["instrument"])

                # Create OrderSpec (may fail here for Pydantic errors)
                order_data = {k: v for k, v in spec_data.items() if k != "instrument"}
                order = OrderSpec(instrument=instrument, **order_data)

                # If we get here, run our validation
                errors = validate_order_spec(order)

                # Should have errors
                assert errors, f"Sample '{sample['description']}' should have validation errors"
                error_text = " ".join(errors).lower()
                assert (
                    expected_error_keyword in error_text or sample["expected_error"] in error_text
                ), (
                    f"Sample '{sample['description']}' should mention '{expected_error_keyword}', "
                    f"got: {errors}"
                )

            except ValidationError as e:
                # Pydantic validation error is expected for some cases
                error_str = str(e).lower()
                # Some error fields are named differently in Pydantic
                assert (
                    expected_error_keyword in error_str
                    or sample["expected_error"] in error_str
                    or "input" in error_str  # Generic Pydantic message
                ), f"Sample '{sample['description']}' Pydantic error should mention '{expected_error_keyword}'"


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Test edge cases in order validation."""

    def test_fractional_quantity_allowed(self, valid_symbol_spec):
        """Test that fractional quantities are allowed (for stocks that support it)."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=0.5,
            orderType="MKT",
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_very_small_quantity_allowed(self, valid_symbol_spec):
        """Test that very small quantities are allowed."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=0.001,
            orderType="MKT",
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_very_large_quantity_allowed(self, valid_symbol_spec):
        """Test that large quantities pass validation (guards may warn)."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1000000,
            orderType="MKT",
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_very_small_limit_price_allowed(self, valid_symbol_spec):
        """Test that very small limit prices are allowed."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=0.01,
            tif="DAY",
        )
        errors = validate_order_spec(order)
        assert errors == []

    def test_outsideRth_flag(self, valid_symbol_spec):
        """Test outsideRth flag is accepted."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=150.00,
            tif="DAY",
            outsideRth=True,
        )
        assert order.outsideRth is True
        errors = validate_order_spec(order)
        assert errors == []

    def test_transmit_flag(self, valid_symbol_spec):
        """Test transmit flag is accepted."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="LMT",
            limitPrice=150.00,
            tif="DAY",
            transmit=False,
        )
        assert order.transmit is False
        errors = validate_order_spec(order)
        assert errors == []

    def test_client_order_id_preserved(self, valid_symbol_spec):
        """Test that clientOrderId is preserved."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="MKT",
            tif="DAY",
            clientOrderId="my-order-123",
        )
        assert order.clientOrderId == "my-order-123"
        errors = validate_order_spec(order)
        assert errors == []

    def test_account_id_preserved(self, valid_symbol_spec):
        """Test that accountId is preserved."""
        order = OrderSpec(
            instrument=valid_symbol_spec,
            side="BUY",
            quantity=1,
            orderType="MKT",
            tif="DAY",
            accountId="DU123456",
        )
        assert order.accountId == "DU123456"
        errors = validate_order_spec(order)
        assert errors == []


# =============================================================================
# OrderSpec Serialization Tests
# =============================================================================


class TestOrderSpecSerialization:
    """Test OrderSpec serialization."""

    def test_to_dict(self, valid_order_spec):
        """Test OrderSpec serializes to dict correctly."""
        data = valid_order_spec.model_dump()
        assert data["side"] == "BUY"
        assert data["quantity"] == 1
        assert data["orderType"] == "LMT"
        assert data["limitPrice"] == 150.00
        assert "instrument" in data

    def test_to_json(self, valid_order_spec):
        """Test OrderSpec serializes to JSON correctly."""
        json_str = valid_order_spec.model_dump_json()
        assert "BUY" in json_str
        assert "LMT" in json_str
        assert "150" in json_str

    def test_round_trip(self, valid_order_spec):
        """Test OrderSpec survives JSON round-trip."""
        json_str = valid_order_spec.model_dump_json()
        data = json.loads(json_str)

        # Reconstruct
        instrument = SymbolSpec(**data["instrument"])
        order_data = {k: v for k, v in data.items() if k != "instrument"}
        reconstructed = OrderSpec(instrument=instrument, **order_data)

        assert reconstructed.side == valid_order_spec.side
        assert reconstructed.quantity == valid_order_spec.quantity
        assert reconstructed.orderType == valid_order_spec.orderType
        assert reconstructed.limitPrice == valid_order_spec.limitPrice
