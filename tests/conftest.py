"""Shared fixtures.

The scripted pipeline run at the bottom is the fixture M9 onwards build on: traceability,
coverage and recomputation all need a complete, linked engagement, and none of them should be
scripting LLM output to get one.
"""

import pytest

from src.config.loader import StaticConfig, load_config
from src.engine.pipeline import load_engagement, run_pipeline
from src.llm.schemas import (
    AssertionAnalysisOutput,
    AuditAreaAnalysisOutput,
    CompanyFactOutput,
    CompanyFactsOutput,
    IdentifiedRiskOutput,
    ProcedureSelectionOutput,
)
from src.models.audit_objects import Assertion
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment
from tests.fakes import ScriptedLLMClient


@pytest.fixture
def static_config() -> StaticConfig:
    return load_config()


def build_engagement(config: StaticConfig) -> AuditEngagement:
    """An engagement with line items loaded but nothing assessed yet.

    Delegates to the real `load_engagement` so fixtures cannot drift from production, then
    blanks the seeded context — most tests set their own, and those that do not should see
    the no-context path rather than a silently inherited one.
    """
    engagement = load_engagement(config)
    engagement.company_context = ""
    return engagement


@pytest.fixture
def raiatea_engagement(static_config) -> AuditEngagement:
    return build_engagement(static_config)


def make_engagement(*line_items: tuple[str, float, float]) -> AuditEngagement:
    """A synthetic engagement from (type, cy, py) triples, for edge cases the case data
    does not exercise."""
    return AuditEngagement(
        company="Synthetic Ltd",
        year_end="2025-12-31",
        line_items=[
            FinancialLineItemAssessment(id=f"li_{t}", line_item_type=t, cy=cy, py=py)
            for t, cy, py in line_items
        ],
    )


# --- a completed pipeline run, scripted end to end (M8 onwards) -------------------------

# The script below makes exactly one assertion relevant per area, so the IDs the engine
# allocates are predictable: inventory takes assertion_1..5 and risk_1, cash takes
# assertion_6..9 and risk_2.
INVENTORY_RISK = "risk_1"
CASH_RISK = "risk_2"


def scripted_facts() -> CompanyFactsOutput:
    return CompanyFactsOutput(
        facts=[
            CompanyFactOutput(
                fact_type="inventory_ageing", value="over 12 months", rationale="Aged stock."
            )
        ]
    )


def scripted_analysis(
    relevant: Assertion, candidates: list[Assertion]
) -> AuditAreaAnalysisOutput:
    """A verdict for every candidate, with exactly one relevant and carrying one risk."""
    return AuditAreaAnalysisOutput(
        assertions=[
            AssertionAnalysisOutput(
                assertion=candidate,
                relevant=candidate is relevant,
                rationale="Because.",
                supporting_fact_ids=["fact_1"] if candidate is relevant else [],
                risks=[
                    IdentifiedRiskOutput(
                        description=f"{candidate.value} risk.",
                        likelihood="high",
                        magnitude="high",
                        rationale="Because.",
                        supporting_fact_ids=["fact_1"],
                    )
                ]
                if candidate is relevant
                else [],
            )
            for candidate in candidates
        ]
    )


def scripted_selection(procedure_id: str, *risk_ids: str) -> ProcedureSelectionOutput:
    return ProcedureSelectionOutput(
        selected_procedures=[
            {
                "procedure_id": procedure_id,
                "risk_ids": list(risk_ids),
                "rationale": "Responds.",
            }
        ]
    )


@pytest.fixture
def client(static_config) -> ScriptedLLMClient:
    return ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=[
            scripted_analysis(
                Assertion.VALUATION, static_config.candidate_assertions("inventory")
            ),
            scripted_analysis(Assertion.EXISTENCE, static_config.candidate_assertions("cash")),
        ],
        select_procedures=[
            scripted_selection("INV_SUBSEQUENT_SALES", INVENTORY_RISK),
            scripted_selection("CASH_BANK_CONFIRMATION", CASH_RISK),
        ],
    )


@pytest.fixture
def engagement(static_config, client) -> AuditEngagement:
    """A fully linked engagement: facts, materiality, scoping, assertions, risks, procedures."""
    return run_pipeline(load_engagement(static_config), client=client, config=static_config)


@pytest.fixture
def two_risk_engagement(static_config) -> AuditEngagement:
    """As `engagement`, but inventory valuation carries two risks answered by one procedure.

    The shared-procedure case SPEC 14 calls out. `INV_SUBSEQUENT_SALES` addresses valuation,
    so both risks on that assertion are legitimately its responsibility — which is what makes
    this the fixture for anything that must not treat a procedure as answering exactly one
    risk. Cash takes `risk_3` here, since inventory consumes two IDs.
    """
    analysis = scripted_analysis(
        Assertion.VALUATION, static_config.candidate_assertions("inventory")
    )
    valuation = next(a for a in analysis.assertions if a.assertion is Assertion.VALUATION)
    valuation.risks.append(
        valuation.risks[0].model_copy(update={"description": "A second, distinct risk."})
    )
    client = ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=[
            analysis,
            scripted_analysis(Assertion.EXISTENCE, static_config.candidate_assertions("cash")),
        ],
        select_procedures=[
            scripted_selection("INV_SUBSEQUENT_SALES", "risk_1", "risk_2"),
            scripted_selection("CASH_BANK_CONFIRMATION", "risk_3"),
        ],
    )
    return run_pipeline(load_engagement(static_config), client=client, config=static_config)
