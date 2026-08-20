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


#: Displayed against any procedure the LLM invented rather than chose (SPEC 13).
AI_SUGGESTION_LABEL = "AI SUGGESTION — AUDITOR APPROVAL REQUIRED"


class Procedure(BaseModel):
    """A procedure selected for a specific risk (SPEC 4)."""

    id: str
    risk_id: str
    procedure_id: str | None = None
    """Catalogue entry id. None for an AI suggestion, which has no catalogue entry."""
    name: str
    description: str
    procedure_type: str
    """Free-form by design: the catalogue must stay data-driven (SPEC 12)."""
    evidence_strength: EvidenceStrength
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
    procedures: list[Procedure] = Field(default_factory=list)
    is_overridden: bool = False
    override_reason: str | None = None


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
