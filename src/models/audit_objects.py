"""Runtime audit objects: assertions, risks and procedures.

These hold engagement state generated during the pipeline. Static methodology lives in
`src.config`, not here.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Assertion(StrEnum):
    """Assertions in scope for the MVP (SPEC 3.3).

    Bounded on purpose: this enum is the contract handed to the LLM for schema-constrained
    output, and the config loader validates every assertion named in JSON against it.
    """

    EXISTENCE = "existence"
    COMPLETENESS = "completeness"
    ACCURACY = "accuracy"
    VALUATION = "valuation"
    RIGHTS_AND_OBLIGATIONS = "rights_and_obligations"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceStrength(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProcedureSource(StrEnum):
    CATALOGUE = "catalogue"
    AI_SUGGESTION = "ai_suggestion"
    AUDITOR_ADDED = "auditor_added"


#: Displayed against any procedure the LLM invented rather than chose (SPEC 13).
AI_SUGGESTION_LABEL = "AI SUGGESTION — AUDITOR APPROVAL REQUIRED"

#: Displayed against an engagement-specific procedure entered by an auditor.  It is active
#: audit work, but it is not silently represented as approved catalogue methodology.
AUDITOR_ADDED_LABEL = "AUDITOR-ADDED — NOT CATALOGUE METHODOLOGY"


class Procedure(BaseModel):
    """A procedure selected in response to one or more risks (SPEC 4, 13).

    Procedures are held on the audit area (`FinancialLineItemAssessment.procedures`), not
    nested under a single risk, because one procedure genuinely answers several risks — test
    counts address both shrinkage and over-receipting. The relationship is carried explicitly
    in `risk_ids` rather than by duplicating the procedure under each risk, so there is one
    object per selected procedure and no copies to keep in sync.
    """

    id: str
    risk_ids: list[str] = Field(min_length=1)
    """The risks this procedure responds to. Never empty: a procedure answering nothing
    would break the SPEC 14 traceability chain."""
    procedure_id: str | None = None
    """Catalogue entry id. None for an AI suggestion or auditor-added procedure."""
    name: str
    description: str
    procedure_type: str
    """Free-form by design: the catalogue must stay data-driven (SPEC 12)."""
    evidence_strength: EvidenceStrength | None = None
    """None for an AI suggestion or auditor-added procedure, which have no assessed strength.

    Evidence strength is approved methodology carried by the catalogue, not something to ask
    the model for — an unapproved suggestion or engagement-specific auditor addition has
    simply not been assessed, and inventing a value would misrepresent it as vetted.
    """
    rationale: str
    source: ProcedureSource = ProcedureSource.CATALOGUE
    approved: bool = True
    """AI suggestions start unapproved and require an auditor decision."""
    isa_refs: list[str] = Field(default_factory=list)

    @property
    def requires_approval(self) -> bool:
        return self.source is ProcedureSource.AI_SUGGESTION and not self.approved


class RiskAssessment(BaseModel):
    """An assertion-level risk (SPEC 11).

    `likelihood` and `magnitude` come from the LLM. `system_rating` is derived deterministically
    from them via the configured risk matrix and is never mutated, so the original system
    conclusion survives any auditor override. Downstream logic reads `final_rating` only.
    """

    id: str
    assertion_id: str
    risk_description: str
    likelihood: RiskLevel
    magnitude: RiskLevel
    system_rating: RiskLevel
    final_rating: RiskLevel
    rationale: str
    supporting_fact_ids: list[str] = Field(default_factory=list)
    isa_refs: list[str] = Field(default_factory=list)
    is_overridden: bool = False
    override_reason: str | None = None
    # Procedures are not held here. They live on the audit area and name the risks they
    # address, so a procedure answering several risks exists once (see `Procedure`). Use
    # `FinancialLineItemAssessment.procedures_for(risk.id)` to fetch them.


class AssertionAssessment(BaseModel):
    """Whether an assertion is relevant for a given audit area (SPEC 10)."""

    id: str
    line_item_id: str
    assertion: Assertion
    relevant: bool
    rationale: str
    supporting_fact_ids: list[str] = Field(default_factory=list)
    isa_refs: list[str] = Field(default_factory=list)
    risks: list[RiskAssessment] = Field(default_factory=list)
