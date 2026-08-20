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


class AssertionVerdict(BaseModel):
    assertion: Assertion
    relevant: bool
    rationale: str
    supporting_fact_ids: list[str] = Field(
        default_factory=list,
        description="IDs of company facts supporting this verdict. Omit if none apply.",
    )


class AssertionRelevanceOutput(BaseModel):
    """A verdict for each candidate assertion supplied in the prompt (SPEC 10)."""

    assertions: list[AssertionVerdict]


# --- assess_risks --------------------------------------------------------------------


class RiskAssessmentOutput(BaseModel):
    """One assertion-level risk (SPEC 11).

    Deliberately has **no** `risk_rating` field. The rating is derived from
    likelihood x magnitude via `risk_matrix.json`, so the model cannot supply one and an
    incoherent low/low -> high is structurally impossible.
    """

    risk_description: str
    likelihood: RiskLevel
    magnitude: RiskLevel
    rationale: str
    supporting_fact_ids: list[str] = Field(default_factory=list)


class RiskIdentificationOutput(BaseModel):
    risks: list[RiskAssessmentOutput] = Field(
        description=(
            "The single most significant risk for this assertion. Include a second only if "
            "it is genuinely distinct. Never more than two."
        )
    )
    """The two-risk cap is a prompt-level instruction, not a schema constraint: `maxItems`
    is not enforced by the API, so M6 truncates rather than failing an otherwise-good
    response."""


# --- select_procedures ---------------------------------------------------------------


class SelectedProcedureOutput(BaseModel):
    procedure_id: str = Field(description="Must be an id from the supplied catalogue subset.")
    rationale: str


class SuggestedProcedureOutput(BaseModel):
    """A procedure the model invented because the catalogue had no adequate response.

    Becomes a `Procedure` marked `ai_suggestion` and unapproved (SPEC 13).
    """

    description: str
    rationale: str


class ProcedureSelectionOutput(BaseModel):
    selected_procedures: list[SelectedProcedureOutput]
    suggested_new_procedure: SuggestedProcedureOutput | None = Field(
        default=None,
        description="Only when no catalogue procedure adequately responds to the risk.",
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
