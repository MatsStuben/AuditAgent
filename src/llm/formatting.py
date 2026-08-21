"""Shared prompt fragments.

Every task that shows the model an audit area or the company's facts presents them
identically. Keeping the wording in one place means a change to how facts are described
cannot drift between the assertion, risk and procedure prompts.
"""

from src.models.engagement import AuditEngagement, FinancialLineItemAssessment, Materiality


def format_company_facts(engagement: AuditEngagement) -> str:
    """Facts listed with their IDs, which is how the model cites them back."""
    if not engagement.company_facts:
        return "None extracted."
    return "\n".join(
        f"- {f.id} ({f.fact_type}): {f.value} — {f.rationale}" for f in engagement.company_facts
    )


def format_company_context(engagement: AuditEngagement) -> str:
    return engagement.company_context.strip() or "None provided."


def format_financial_context(
    line_item: FinancialLineItemAssessment, materiality: Materiality
) -> str:
    """The figures SPEC 10 and 11 both require: amounts, movement and materiality."""
    metrics = line_item.metrics
    pct = "not available" if metrics.yoy_change_pct is None else f"{metrics.yoy_change_pct:+.1f}%"
    return (
        f"Current year amount: {line_item.cy:,.0f}\n"
        f"Prior year amount: {line_item.py:,.0f}\n"
        f"Year-on-year movement: {metrics.yoy_change:+,.0f} ({pct})\n"
        f"Materiality: {materiality.amount:,.0f}\n"
        f"Amount as a multiple of materiality: {metrics.amount_to_materiality_ratio:.1f}x"
    )
