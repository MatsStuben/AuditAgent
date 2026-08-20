"""M2 verification: deterministic materiality (SPEC 7)."""

import pytest

from src.engine.materiality import (
    PBT_MARGIN_THRESHOLD,
    MaterialityError,
    calculate_materiality,
)
from tests.conftest import make_engagement


def test_raiatea_materiality_is_262k(raiatea_engagement):
    """5.24m / 52.4m = 10% > 5%, so 5% x PBT = 262,000 (SPEC 7)."""
    materiality = calculate_materiality(raiatea_engagement)

    assert materiality.amount == 262_000
    assert materiality.benchmark == "profit_before_tax"
    assert materiality.rate == 0.05


def test_turnover_branch_fires_below_the_threshold():
    """3% margin -> 0.5% of turnover."""
    engagement = make_engagement(
        ("turnover", 10_000_000, 9_000_000), ("profit_before_tax", 300_000, 250_000)
    )

    materiality = calculate_materiality(engagement)

    assert materiality.amount == 50_000
    assert materiality.benchmark == "turnover"
    assert materiality.rate == 0.005


def test_threshold_is_strict_so_exactly_five_percent_uses_turnover():
    """`>` not `>=`: a 5.0% margin takes the turnover branch (SPEC 7)."""
    engagement = make_engagement(
        ("turnover", 10_000_000, 9_000_000), ("profit_before_tax", 500_000, 400_000)
    )

    materiality = calculate_materiality(engagement)

    assert 500_000 / 10_000_000 == PBT_MARGIN_THRESHOLD
    assert materiality.benchmark == "turnover"
    assert materiality.amount == 50_000  # not 25_000, which the PBT branch would give


def test_loss_making_company_uses_turnover():
    """A negative margin is not > 5%, so the turnover benchmark applies."""
    engagement = make_engagement(
        ("turnover", 10_000_000, 9_000_000), ("profit_before_tax", -2_000_000, 100_000)
    )

    materiality = calculate_materiality(engagement)

    assert materiality.benchmark == "turnover"
    assert materiality.amount == 50_000


def test_basis_explains_which_branch_fired(raiatea_engagement):
    materiality = calculate_materiality(raiatea_engagement)

    assert "10.0% of turnover" in materiality.basis
    assert "above" in materiality.basis
    assert "5% of profit before tax" in materiality.basis


def test_label_marks_this_as_prototype_methodology(raiatea_engagement):
    """SPEC 7 requires this be labelled, not presented as an ISA formula."""
    assert "not an ISA-prescribed formula" in calculate_materiality(raiatea_engagement).label


def test_calculate_materiality_does_not_mutate_the_engagement(raiatea_engagement):
    calculate_materiality(raiatea_engagement)

    assert raiatea_engagement.materiality is None


def test_zero_turnover_is_rejected_rather_than_dividing_by_zero():
    engagement = make_engagement(("turnover", 0, 0), ("profit_before_tax", 100, 100))

    with pytest.raises(MaterialityError, match="turnover is zero"):
        calculate_materiality(engagement)


@pytest.mark.parametrize("missing", ["turnover", "profit_before_tax"])
def test_missing_benchmark_line_item_is_rejected(missing):
    supplied = [
        ("turnover", 10_000_000, 9_000_000),
        ("profit_before_tax", 300_000, 250_000),
    ]
    engagement = make_engagement(*[li for li in supplied if li[0] != missing])

    with pytest.raises(MaterialityError, match=missing):
        calculate_materiality(engagement)
