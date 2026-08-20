"""Deterministic materiality calculation (SPEC 7).

The rule below is **prototype methodology, not an ISA-prescribed formula**:

    if profit_before_tax / turnover > 5%:
        materiality = 5% of profit before tax
    else:
        materiality = 0.5% of turnover

The rates live here as named constants rather than in JSON: it is a single formula, not the
kind of per-area knowledge that would otherwise become scattered branching. If materiality
methodology ever varies by engagement, it moves to config at that point.
"""

from src.models.engagement import AuditEngagement, Materiality

#: Line items this rule depends on.
TURNOVER = "turnover"
PROFIT_BEFORE_TAX = "profit_before_tax"

PBT_MARGIN_THRESHOLD = 0.05
PBT_RATE = 0.05
TURNOVER_RATE = 0.005


class MaterialityError(ValueError):
    """Raised when materiality cannot be calculated from the supplied line items."""


def _cy_amount(engagement: AuditEngagement, line_item_type: str) -> float:
    item = engagement.line_item(line_item_type)
    if item is None:
        raise MaterialityError(
            f"cannot calculate materiality: '{line_item_type}' line item is missing"
        )
    return item.cy


def calculate_materiality(engagement: AuditEngagement) -> Materiality:
    """Return materiality for the engagement. Pure — does not assign to the engagement."""
    turnover = _cy_amount(engagement, TURNOVER)
    profit_before_tax = _cy_amount(engagement, PROFIT_BEFORE_TAX)

    if turnover == 0:
        raise MaterialityError("cannot calculate materiality: turnover is zero")

    margin = profit_before_tax / turnover

    if margin > PBT_MARGIN_THRESHOLD:
        return Materiality(
            amount=PBT_RATE * profit_before_tax,
            benchmark=PROFIT_BEFORE_TAX,
            rate=PBT_RATE,
            basis=(
                f"Profit before tax is {margin:.1%} of turnover, above the "
                f"{PBT_MARGIN_THRESHOLD:.0%} threshold, so materiality is "
                f"{PBT_RATE:.0%} of profit before tax."
            ),
        )

    return Materiality(
        amount=TURNOVER_RATE * turnover,
        benchmark=TURNOVER,
        rate=TURNOVER_RATE,
        basis=(
            f"Profit before tax is {margin:.1%} of turnover, at or below the "
            f"{PBT_MARGIN_THRESHOLD:.0%} threshold, so materiality is "
            f"{TURNOVER_RATE:.1%} of turnover."
        ),
    )
