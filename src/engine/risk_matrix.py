"""Deterministic derivation of a risk rating from likelihood and magnitude (SPEC 11).

This is the boundary the spec draws between model judgement and firm methodology: the LLM
assesses likelihood and magnitude, and the *rating* follows from a matrix a methodology owner
controls. Keeping it here rather than in the prompt makes ratings reproducible for identical
inputs and makes an incoherent combination such as low/low -> high structurally impossible.

The matrix itself lives in `risk_matrix.json`, not in this module, so changing risk appetite
never requires a code change.
"""

from src.config.loader import RiskMatrix, get_config
from src.models.audit_objects import RiskLevel


class RiskMatrixError(ValueError):
    """Raised when a rating cannot be derived from the supplied levels."""


def derive_rating(
    likelihood: RiskLevel | str,
    magnitude: RiskLevel | str,
    *,
    matrix: RiskMatrix | None = None,
) -> RiskLevel:
    """Return the configured rating for `likelihood` x `magnitude`.

    Raises rather than falling back to a default: a silent default would mean an unexplained
    rating in the audit file, which is worse than a loud failure.
    """
    matrix = matrix if matrix is not None else get_config().risk_matrix
    try:
        return matrix.rating(RiskLevel(likelihood), RiskLevel(magnitude))
    except (ValueError, KeyError) as exc:
        raise RiskMatrixError(
            f"no rating configured for likelihood={likelihood!r}, magnitude={magnitude!r}"
        ) from exc
