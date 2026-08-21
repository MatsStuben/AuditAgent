"""Pydantic output models for every LLM call (SPEC 21).

One model per bounded judgement. These are the *only* shapes model output may take: nothing
unstructured becomes system state.

Bounded values reuse the domain enums (`Assertion`, `RiskLevel`) rather than redeclaring
strings, so the API receives a real, enforced JSON-schema `enum`.

Note on what the API enforces: the SDK's schema transform keeps `enum` as a hard constraint
but demotes `maxItems`/`const` to description hints. Anything expressed that way is guidance
to the model, not a guarantee, and must still be enforced in engine code.
"""

from typing import Literal

from pydantic import BaseModel, Field

from src.models.audit_objects import Assertion, RiskLevel

# --- extract_company_facts -----------------------------------------------------------


class CompanyFactOutput(BaseModel):
    """One structured fact drawn from the free-text company context (SPEC 3.2).

    No `id`: the engine assigns fact IDs so downstream `supporting_fact_ids` always
    reference something real.
    """

    fact_type: str = Field(description="Short snake_case category, e.g. inventory_seasonality.")
    value: str = Field(description="The value of the fact, e.g. high.")
    rationale: str = Field(description="What in the context supports this fact.")


class CompanyFactsOutput(BaseModel):
    facts: list[CompanyFactOutput]


# --- assess_assertions ---------------------------------------------------------------


class IdentifiedRiskOutput(BaseModel):
    """One risk of material misstatement, nested under the assertion it affects (SPEC 11).

    Deliberately has **no** rating field. The rating is derived from likelihood x magnitude
    via `risk_matrix.json`, so the model cannot supply one and an incoherent low/low -> high
    is structurally impossible.
    """

    description: str = Field(
        description="What could actually go wrong, specific enough to design a procedure against."
    )
    likelihood: RiskLevel
    magnitude: RiskLevel
    rationale: str
    supporting_fact_ids: list[str] = Field(default_factory=list)


class AssertionAnalysisOutput(BaseModel):
    """A relevance verdict plus the risks that follow from it (SPEC 10)."""

    assertion: Assertion
    relevant: bool
    rationale: str
    supporting_fact_ids: list[str] = Field(
        default_factory=list,
        description="IDs of company facts supporting this verdict. Omit if none apply.",
    )
    risks: list[IdentifiedRiskOutput] = Field(
        default_factory=list,
        description=(
            "The most significant risk for this assertion, and a second only if genuinely "
            "distinct — never more than two. Empty when the assertion is not relevant."
        ),
    )


class AuditAreaAnalysisOutput(BaseModel):
    """One response covering a whole audit area (SPEC 6.1).

    Relevance and risks arrive together so the number of LLM calls scales with audit areas
    rather than with assertions and risks.

    Neither the two-risk cap nor the "no risks when not relevant" rule is enforceable in the
    schema — `maxItems` is not API-enforced and cross-field rules cannot be expressed — so
    both are applied in `audit_area_analyser`.
    """

    assertions: list[AssertionAnalysisOutput] = Field(
        description="A verdict for every candidate assertion supplied, in any order."
    )


# --- select_procedures ---------------------------------------------------------------


class SelectedProcedureOutput(BaseModel):
    procedure_id: str = Field(description="Must be an id from the supplied catalogue subset.")
    risk_ids: list[str] = Field(
        description=(
            "The ids of the risks this procedure responds to. At least one, and every id "
            "must be one of the risks supplied for this audit area."
        )
    )
    rationale: str


class SuggestedProcedureOutput(BaseModel):
    """A procedure the model invented because the catalogue had no adequate response.

    Becomes a `Procedure` marked `ai_suggestion` and unapproved (SPEC 13).
    """

    description: str
    risk_ids: list[str] = Field(description="The ids of the risks this procedure responds to.")
    rationale: str


class ProcedureSelectionOutput(BaseModel):
    """One response covering every risk in an audit area (SPEC 6.1, 13).

    `risk_ids` on each entry is what preserves
    Procedure -> Risk -> Assertion -> Audit Area -> Company Facts -> ISA.
    """

    selected_procedures: list[SelectedProcedureOutput]
    suggested_new_procedures: list[SuggestedProcedureOutput] = Field(
        default_factory=list,
        description=(
            "Only where no catalogue procedure adequately responds to a risk. Each requires "
            "auditor approval before use."
        ),
    )


# --- generalize_feedback -------------------------------------------------------------


class EngagementSpecificFeedback(BaseModel):
    """The override reflects a one-off judgement; no methodology change implied."""

    type: Literal["engagement_specific"]
    reason: str


class MethodologyRuleProposalOutput(BaseModel):
    """The override generalises into a candidate rule for methodology review."""

    type: Literal["methodology_rule_proposal"]
    condition: str
    action: str
    reason: str


class FeedbackClassificationOutput(BaseModel):
    """Classification of one auditor override (SPEC 19)."""

    classification: EngagementSpecificFeedback | MethodologyRuleProposalOutput = Field(
        discriminator="type"
    )
