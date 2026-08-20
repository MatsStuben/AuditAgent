"""Deterministic derived metrics and line item scoping (SPEC 3.1, 8).

Scoping sets two *independent* flags on every supplied line item:

    material       CY amount exceeds materiality           (all line items)
    is_audit_area  an implemented profile exists for it     (cash, inventory)

The pipeline requires both. A line item can be material without being an audit area; that is
a displayed state, not an ISA gap (SPEC 15).
"""

from src.config.loader import StaticConfig, get_config
from src.models.engagement import AuditEngagement, DerivedMetrics


class ScopingError(ValueError):
    """Raised when scoping is attempted before its inputs exist."""


def derive_metrics(cy: float, py: float, materiality: float) -> DerivedMetrics:
    """Derived metrics for one line item (SPEC 3.1). Pure."""
    if materiality <= 0:
        raise ScopingError(f"materiality must be positive, got {materiality}")

    change = cy - py
    return DerivedMetrics(
        yoy_change=change,
        # None rather than a fabricated number: with no prior-year base there is no
        # meaningful percentage. abs() keeps the sign describing the direction of the
        # movement even when the prior year is negative.
        yoy_change_pct=(change / abs(py) * 100) if py != 0 else None,
        amount_to_materiality_ratio=cy / materiality,
    )


def scope_line_items(
    engagement: AuditEngagement, config: StaticConfig | None = None
) -> None:
    """Populate metrics, `material` and `is_audit_area` on every line item.

    Mutates `engagement` in place, matching the SPEC 6 pipeline shape.
    """
    if engagement.materiality is None:
        raise ScopingError("materiality must be calculated before scoping")

    config = config or get_config()
    threshold = engagement.materiality.amount

    for item in engagement.line_items:
        item.metrics = derive_metrics(item.cy, item.py, threshold)
        item.material = item.cy > threshold
        item.is_audit_area = config.is_audit_area(item.line_item_type)
