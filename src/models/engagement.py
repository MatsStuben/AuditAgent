"""Engagement-level runtime state (SPEC 4)."""

from pydantic import BaseModel, Field

from src.models.audit_objects import AssertionAssessment


class CompanyFact(BaseModel):
    """A structured fact extracted from free-text company context (SPEC 3.2).

    `id` is assigned by the engine rather than the model, so downstream `supporting_fact_ids`
    always reference something real.
    """

    id: str
    fact_type: str
    value: str
    source: str = "company_context"
    rationale: str


class Materiality(BaseModel):
    """Result of the prototype materiality rule (SPEC 7)."""

    amount: float
    benchmark: str
    """Which line item the rate was applied to, e.g. "profit_before_tax"."""
    rate: float
    basis: str
    """Human-readable explanation of which branch of the rule fired."""
    label: str = "Prototype methodology, not an ISA-prescribed formula."


class DerivedMetrics(BaseModel):
    """Deterministic metrics per line item (SPEC 3.1)."""

    yoy_change: float
    yoy_change_pct: float | None = None
    """None when the prior-year amount is zero, rather than a fabricated percentage."""
    amount_to_materiality_ratio: float


class FinancialLineItemAssessment(BaseModel):
    """One of the supplied financial line items and its assessment state (SPEC 2.1).

    Deliberately not called `BalanceAssessment`: turnover is a P&L item and profit_before_tax
    is both a line item and the materiality benchmark, so "balance" would be wrong for three
    of the eight.

    `material` and `is_audit_area` are independent. The pipeline requires both; a line item
    can be material without having implemented methodology, which is a displayed state and
    not an ISA gap (SPEC 15).
    """

    id: str
    line_item_type: str
    """Plain str, not an enum, so new line items are a JSON-only change."""
    cy: float
    py: float
    metrics: DerivedMetrics | None = None
    material: bool | None = None
    is_audit_area: bool = False
    assertions: list[AssertionAssessment] = Field(default_factory=list)


class AuditEngagement(BaseModel):
    """Root runtime object for one engagement (SPEC 4)."""

    company: str
    year_end: str
    company_context: str = ""
    company_facts: list[CompanyFact] = Field(default_factory=list)
    materiality: Materiality | None = None
    line_items: list[FinancialLineItemAssessment] = Field(default_factory=list)

    def line_item(self, line_item_type: str) -> FinancialLineItemAssessment | None:
        """Look up a line item by its type, e.g. "inventory"."""
        return next((li for li in self.line_items if li.line_item_type == line_item_type), None)

    @property
    def implemented_audit_areas(self) -> list[FinancialLineItemAssessment]:
        """Line items with implemented methodology, irrespective of materiality.

        Use for display. This is *not* pipeline scope — see `in_scope_audit_areas`.
        """
        return [li for li in self.line_items if li.is_audit_area]

    @property
    def in_scope_audit_areas(self) -> list[FinancialLineItemAssessment]:
        """What the pipeline actually processes: material AND implemented (SPEC 6, 8).

        `material` is None before scoping, so this is empty on an unscoped engagement
        rather than silently returning every implemented area.
        """
        return [li for li in self.line_items if li.material is True and li.is_audit_area]
