"""M2 verification: derived metrics and line item scoping (SPEC 3.1, 8)."""

import pytest

from src.engine.materiality import calculate_materiality
from src.engine.scoping import ScopingError, derive_metrics, scope_line_items
from tests.conftest import make_engagement

MATERIALITY = 262_000


@pytest.fixture
def scoped(raiatea_engagement):
    raiatea_engagement.materiality = calculate_materiality(raiatea_engagement)
    scope_line_items(raiatea_engagement)
    return raiatea_engagement


# --- derived metrics -----------------------------------------------------------------


def test_inventory_metrics(scoped):
    metrics = scoped.line_item("inventory").metrics

    assert metrics.yoy_change == 2_700_000
    assert metrics.yoy_change_pct == pytest.approx(43.55, abs=0.01)
    assert metrics.amount_to_materiality_ratio == pytest.approx(33.97, abs=0.01)


def test_cash_metrics(scoped):
    metrics = scoped.line_item("cash").metrics

    assert metrics.yoy_change == 230_000
    assert metrics.yoy_change_pct == pytest.approx(7.96, abs=0.01)
    assert metrics.amount_to_materiality_ratio == pytest.approx(11.91, abs=0.01)


def test_negative_movement_keeps_its_sign(scoped):
    """PPE fell year on year; the metrics must not report that as growth."""
    metrics = scoped.line_item("property_plant_equipment").metrics

    assert metrics.yoy_change == -200_000
    assert metrics.yoy_change_pct == pytest.approx(-4.17, abs=0.01)


def test_ratios_that_divide_exactly(scoped):
    """Turnover is exactly 200x materiality and PBT exactly 20x — no rounding slack."""
    assert scoped.line_item("turnover").metrics.amount_to_materiality_ratio == 200.0
    assert scoped.line_item("profit_before_tax").metrics.amount_to_materiality_ratio == 20.0


def test_zero_prior_year_yields_no_percentage():
    """No prior-year base means no meaningful percentage, so None rather than a fake."""
    metrics = derive_metrics(cy=500_000, py=0, materiality=MATERIALITY)

    assert metrics.yoy_change == 500_000
    assert metrics.yoy_change_pct is None
    assert metrics.amount_to_materiality_ratio == pytest.approx(1.908, abs=0.001)


def test_negative_prior_year_reports_direction_of_movement():
    """abs() base: moving from -100k to -50k is an improvement, not a -50% fall."""
    metrics = derive_metrics(cy=-50_000, py=-100_000, materiality=MATERIALITY)

    assert metrics.yoy_change == 50_000
    assert metrics.yoy_change_pct == pytest.approx(50.0)


def test_non_positive_materiality_is_rejected():
    with pytest.raises(ScopingError, match="must be positive"):
        derive_metrics(cy=1, py=1, materiality=0)


# --- scoping -------------------------------------------------------------------------


def test_all_eight_raiatea_line_items_are_material(scoped):
    """Every supplied item exceeds 262k, so nothing is descoped by the case data."""
    assert len(scoped.line_items) == 8
    assert all(item.material for item in scoped.line_items)


def test_only_cash_and_inventory_are_audit_areas(scoped):
    assert {li.line_item_type for li in scoped.implemented_audit_areas} == {"cash", "inventory"}
    assert {li.line_item_type for li in scoped.in_scope_audit_areas} == {"cash", "inventory"}


def test_material_but_not_an_audit_area(scoped):
    """The SPEC 2.1 state the terminology exists to express — not an ISA gap."""
    turnover = scoped.line_item("turnover")

    assert turnover.material is True
    assert turnover.is_audit_area is False


def test_below_threshold_line_item_is_not_material(static_config):
    """Fixture-only, since no Raiatea item falls below materiality (SPEC 3.3)."""
    engagement = make_engagement(
        ("turnover", 10_000_000, 9_000_000),
        ("profit_before_tax", 300_000, 250_000),
        ("cash", 10_000, 9_000),
    )
    engagement.materiality = calculate_materiality(engagement)  # 50,000 via turnover
    scope_line_items(engagement, static_config)

    cash = engagement.line_item("cash")
    assert cash.material is False
    # Implemented, but out of scope because it is immaterial.
    assert cash.is_audit_area is True
    assert engagement.in_scope_audit_areas == []
    assert [li.line_item_type for li in engagement.implemented_audit_areas] == ["cash"]


def test_amount_exactly_equal_to_materiality_is_not_material(static_config):
    """`>` not `>=`, matching SPEC 8."""
    engagement = make_engagement(
        ("turnover", 10_000_000, 9_000_000),
        ("profit_before_tax", 300_000, 250_000),
        ("cash", 50_000, 40_000),
    )
    engagement.materiality = calculate_materiality(engagement)
    scope_line_items(engagement, static_config)

    assert engagement.materiality.amount == 50_000
    assert engagement.line_item("cash").material is False


def test_negative_amount_is_not_material(static_config):
    """Documents current literal behaviour: SPEC 8 compares the amount, not its magnitude.

    A line item of -1m is therefore *not* material even though its magnitude far exceeds
    materiality. No Raiatea item is negative, so this only matters for future data.
    """
    engagement = make_engagement(
        ("turnover", 10_000_000, 9_000_000),
        ("profit_before_tax", 300_000, 250_000),
        ("cash", -1_000_000, 500_000),
    )
    engagement.materiality = calculate_materiality(engagement)
    scope_line_items(engagement, static_config)

    assert engagement.line_item("cash").material is False


def test_scoping_before_materiality_is_rejected(raiatea_engagement):
    with pytest.raises(ScopingError, match="materiality must be calculated"):
        scope_line_items(raiatea_engagement)


def test_unscoped_line_items_start_with_nothing_assessed(raiatea_engagement):
    assert all(item.material is None for item in raiatea_engagement.line_items)
    assert all(item.metrics is None for item in raiatea_engagement.line_items)


def test_rescoping_with_unchanged_materiality_changes_nothing(scoped):
    """Idempotent on the same inputs, and does not accumulate line items."""
    before = scoped.line_item("inventory").metrics.model_copy()
    scope_line_items(scoped)

    assert scoped.line_item("inventory").metrics == before
    assert len(scoped.line_items) == 8


def test_rescoping_after_a_materiality_change_overwrites_stale_values(scoped):
    """SPEC 17: a materiality change must flow through to metrics and scope.

    Raising materiality to 4m puts cash (3.12m) below the threshold while inventory (8.9m)
    stays above it, so a stale `material` flag or ratio would be caught here.
    """
    assert scoped.line_item("cash").material is True
    assert scoped.line_item("inventory").metrics.amount_to_materiality_ratio == pytest.approx(
        33.97, abs=0.01
    )

    scoped.materiality.amount = 4_000_000
    scope_line_items(scoped)

    # Ratios recomputed against the new threshold, not left at the old one.
    assert scoped.line_item("inventory").metrics.amount_to_materiality_ratio == pytest.approx(2.225)
    assert scoped.line_item("cash").metrics.amount_to_materiality_ratio == pytest.approx(0.78)
    # And the scope decision flipped with it.
    assert scoped.line_item("cash").material is False
    assert scoped.line_item("inventory").material is True
    # Cash is still implemented, just no longer in scope.
    assert [li.line_item_type for li in scoped.in_scope_audit_areas] == ["inventory"]
    assert {li.line_item_type for li in scoped.implemented_audit_areas} == {"cash", "inventory"}
    # YoY figures do not depend on materiality and must be unaffected.
    assert scoped.line_item("cash").metrics.yoy_change == 230_000
