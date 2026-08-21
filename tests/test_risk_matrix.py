"""M6 verification: deterministic rating derivation (SPEC 11). Pure, no LLM."""

import pytest

from src.config.loader import RiskMatrix
from src.engine.risk_matrix import RiskMatrixError, derive_rating
from src.models.audit_objects import RiskLevel

L, M, H = RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH


def test_all_nine_combinations_are_derivable():
    for likelihood in RiskLevel:
        for magnitude in RiskLevel:
            assert isinstance(derive_rating(likelihood, magnitude), RiskLevel)


@pytest.mark.parametrize(
    ("likelihood", "magnitude", "expected"),
    [
        (L, L, L),
        (L, M, L),
        (L, H, M),
        (M, L, L),
        (M, M, M),
        (M, H, H),
        (H, L, M),
        (H, M, H),
        (H, H, H),
    ],
)
def test_shipped_matrix_values(likelihood, magnitude, expected):
    assert derive_rating(likelihood, magnitude) is expected


def test_string_levels_are_accepted():
    """Convenient at the boundary, since model output arrives as enum-valued strings."""
    assert derive_rating("high", "high") is RiskLevel.HIGH
    assert derive_rating("low", "low") is RiskLevel.LOW


def test_unknown_level_raises_rather_than_defaulting():
    """A silent default would put an unexplained rating in the audit file."""
    with pytest.raises(RiskMatrixError, match="no rating configured"):
        derive_rating("severe", "high")

    with pytest.raises(RiskMatrixError):
        derive_rating("high", None)


def test_matrix_is_configuration_not_constants():
    """Swapping the matrix changes every rating with no code change.

    This is the property that lets a methodology owner set risk appetite in
    `risk_matrix.json` without touching the engine.
    """
    inverted = RiskMatrix(
        label="test",
        matrix={
            "low": {"low": "high", "medium": "high", "high": "high"},
            "medium": {"low": "high", "medium": "high", "high": "high"},
            "high": {"low": "low", "medium": "low", "high": "low"},
        },
    )

    assert derive_rating(L, L, matrix=inverted) is RiskLevel.HIGH
    assert derive_rating(H, H, matrix=inverted) is RiskLevel.LOW
    # The shipped matrix is unaffected by the injected one.
    assert derive_rating(L, L) is RiskLevel.LOW


def test_asymmetric_matrices_are_supported():
    """The shipped matrix is symmetric, but the structure does not require it — e.g.
    weighting magnitude more heavily than likelihood."""
    magnitude_weighted = RiskMatrix(
        label="magnitude-weighted",
        matrix={
            "low": {"low": "low", "medium": "medium", "high": "high"},
            "medium": {"low": "low", "medium": "medium", "high": "high"},
            "high": {"low": "medium", "medium": "high", "high": "high"},
        },
    )

    assert derive_rating(L, H, matrix=magnitude_weighted) is RiskLevel.HIGH
    assert derive_rating(H, L, matrix=magnitude_weighted) is RiskLevel.MEDIUM


def test_derivation_is_reproducible():
    """Identical inputs must always give the identical rating — the point of deriving it."""
    assert {derive_rating(H, M) for _ in range(10)} == {RiskLevel.HIGH}


def test_no_combination_is_incoherent():
    """low/low can never rate high, nor high/high rate low, whatever the matrix says.

    Guards the shipped configuration, which is the failure the derivation exists to prevent.
    """
    assert derive_rating(L, L) is not RiskLevel.HIGH
    assert derive_rating(H, H) is not RiskLevel.LOW
