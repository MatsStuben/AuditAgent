"""M6 verification: audit area analysis — relevance + risks in one call (SPEC 6.1, 10, 11).

Merges the coverage of the former per-assertion and per-risk suites.
"""

import pytest

from src.config.loader import RiskMatrix
from src.engine.materiality import calculate_materiality
from src.engine.scoping import scope_line_items
from src.llm.audit_area_analyser import (
    MAX_RISKS_PER_ASSERTION,
    MISSING_RATIONALE,
    NO_VERDICT_RATIONALE,
    AuditAreaAnalysisError,
    analyse_audit_area,
    build_user_message,
)
from src.llm.client import LLMTask
from src.llm.prompts import ANALYSE_AUDIT_AREA
from src.llm.schemas import (
    AssertionAnalysisOutput,
    AuditAreaAnalysisOutput,
    IdentifiedRiskOutput,
)
from src.models.audit_objects import Assertion, RiskLevel
from src.models.engagement import CompanyFact
from tests.conftest import make_engagement
from tests.fakes import ScriptedLLMClient

INVENTORY_CANDIDATES = [
    Assertion.EXISTENCE,
    Assertion.COMPLETENESS,
    Assertion.ACCURACY,
    Assertion.VALUATION,
    Assertion.RIGHTS_AND_OBLIGATIONS,
]


@pytest.fixture
def engagement(raiatea_engagement, static_config):
    raiatea_engagement.company_context = (
        "Raiatea is a fast-growing fashion retailer with highly seasonal inventory."
    )
    raiatea_engagement.company_facts = [
        CompanyFact(
            id="fact_1", fact_type="inventory_seasonality", value="high", rationale="Seasonal."
        ),
        CompanyFact(
            id="fact_2", fact_type="inventory_ageing", value="over 12 months", rationale="Aged."
        ),
    ]
    raiatea_engagement.materiality = calculate_materiality(raiatea_engagement)
    scope_line_items(raiatea_engagement, static_config)
    return raiatea_engagement


@pytest.fixture
def inventory(engagement):
    return engagement.line_item("inventory")


def _risk(likelihood="high", magnitude="high", description="Carried above recoverable value.",
          rationale="Aged seasonal stock.", fact_ids=None) -> IdentifiedRiskOutput:
    return IdentifiedRiskOutput(
        description=description,
        likelihood=likelihood,
        magnitude=magnitude,
        rationale=rationale,
        supporting_fact_ids=fact_ids or [],
    )


def _analysis(assertion, relevant=True, rationale="Because.", fact_ids=None, risks=None):
    return AssertionAnalysisOutput(
        assertion=assertion,
        relevant=relevant,
        rationale=rationale,
        supporting_fact_ids=fact_ids or [],
        risks=risks if risks is not None else ([_risk()] if relevant else []),
    )


def _all_five(**relevance) -> AuditAreaAnalysisOutput:
    return AuditAreaAnalysisOutput(
        assertions=[
            _analysis(a, relevant=relevance.get(a.value, True)) for a in INVENTORY_CANDIDATES
        ]
    )


def _client(output) -> ScriptedLLMClient:
    return ScriptedLLMClient(analyse_audit_area=output)


def _valuation(assessments):
    """Pick the assertion under test.

    Every candidate gets an assessment in profile order, so indexing by position would
    silently select whichever assertion happens to come first.
    """
    return next(a for a in assessments if a.assertion is Assertion.VALUATION)


# --- one call per area ---------------------------------------------------------------


def test_whole_area_costs_exactly_one_call(engagement, inventory):
    """SPEC 6.1: five assertions and their risks arrive in a single response."""
    client = _client(_all_five())

    assessments = analyse_audit_area(inventory, engagement, client=client)

    assert client.call_count() == 1
    assert len(assessments) == 5
    assert sum(len(a.risks) for a in assessments) == 5
    client.assert_all_consumed()


def test_call_count_is_independent_of_risk_count(engagement, inventory):
    """Two risks on every assertion still costs one call — the point of batching."""
    output = AuditAreaAnalysisOutput(
        assertions=[
            _analysis(a, risks=[_risk(description="First."), _risk(description="Second.")])
            for a in INVENTORY_CANDIDATES
        ]
    )
    client = _client(output)

    assessments = analyse_audit_area(inventory, engagement, client=client)

    assert client.call_count() == 1
    assert sum(len(a.risks) for a in assessments) == 10


def test_uses_the_analysis_task_and_prompt(engagement, inventory):
    client = _client(_all_five())

    analyse_audit_area(inventory, engagement, client=client)

    call = client.calls_for(LLMTask.ANALYSE_AUDIT_AREA)[0]
    assert call.system == ANALYSE_AUDIT_AREA
    assert call.output_format is AuditAreaAnalysisOutput


# --- assertions ----------------------------------------------------------------------


def test_five_candidates_produce_five_assessments(engagement, inventory):
    assessments = analyse_audit_area(inventory, engagement, client=_client(_all_five()))

    assert [a.assertion for a in assessments] == INVENTORY_CANDIDATES


def test_each_assessment_is_fully_linked(engagement, inventory):
    assessments = analyse_audit_area(inventory, engagement, client=_client(_all_five()))

    for assessment in assessments:
        assert assessment.id.startswith("assertion_")
        assert assessment.line_item_id == inventory.id
        assert assessment.isa_refs == ["ISA315.29"]
        assert assessment.rationale


def test_relevance_verdicts_are_carried_through(engagement, inventory):
    client = _client(_all_five(rights_and_obligations=False))

    by_assertion = {
        a.assertion: a for a in analyse_audit_area(inventory, engagement, client=client)
    }

    assert by_assertion[Assertion.VALUATION].relevant is True
    assert by_assertion[Assertion.RIGHTS_AND_OBLIGATIONS].relevant is False


def test_cash_uses_its_own_shorter_candidate_list(engagement, static_config):
    cash = engagement.line_item("cash")
    candidates = static_config.candidate_assertions("cash")
    client = _client(AuditAreaAnalysisOutput(assertions=[_analysis(a) for a in candidates]))

    assessments = analyse_audit_area(cash, engagement, client=client)

    assert [a.assertion for a in assessments] == candidates
    assert Assertion.VALUATION not in {a.assertion for a in assessments}


def test_results_are_not_assigned_to_the_line_item(engagement, inventory):
    analyse_audit_area(inventory, engagement, client=_client(_all_five()))

    assert inventory.assertions == []


# --- risks ---------------------------------------------------------------------------


def test_risks_are_nested_under_their_assertion(engagement, inventory):
    assessments = analyse_audit_area(inventory, engagement, client=_client(_all_five()))

    for assessment in assessments:
        for risk in assessment.risks:
            assert risk.assertion_id == assessment.id
            assert risk.isa_refs == ["ISA315.28b_31"]
    # Procedures are area-level, so analysis leaves the area's procedure list untouched.
    assert inventory.procedures == []


def test_ids_are_unique_and_monotonic_across_both_types(engagement, inventory):
    assessments = analyse_audit_area(inventory, engagement, client=_client(_all_five()))

    assertion_ids = [a.id for a in assessments]
    risk_ids = [r.id for a in assessments for r in a.risks]
    assert assertion_ids == [f"assertion_{i}" for i in range(1, 6)]
    assert risk_ids == [f"risk_{i}" for i in range(1, 6)]
    assert len(set(assertion_ids + risk_ids)) == 10


def test_two_risks_per_assertion_are_kept(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[
            _analysis(
                Assertion.VALUATION,
                risks=[_risk(description="Obsolescence."), _risk(description="NRV.")],
            )
        ]
    )

    valuation = next(
        a for a in analyse_audit_area(inventory, engagement, client=_client(output))
        if a.assertion is Assertion.VALUATION
    )

    assert [r.risk_description for r in valuation.risks] == ["Obsolescence.", "NRV."]


def test_more_than_two_risks_are_truncated_not_rejected(engagement, inventory):
    """`maxItems` is not API-enforced, so the cap is applied in code."""
    output = AuditAreaAnalysisOutput(
        assertions=[
            _analysis(
                Assertion.VALUATION,
                risks=[_risk(description=f"Risk {i}.") for i in range(5)],
            )
        ]
    )

    valuation = next(
        a for a in analyse_audit_area(inventory, engagement, client=_client(output))
        if a.assertion is Assertion.VALUATION
    )

    assert len(valuation.risks) == MAX_RISKS_PER_ASSERTION == 2
    assert [r.risk_description for r in valuation.risks] == ["Risk 0.", "Risk 1."]


def test_risks_on_a_non_relevant_assertion_are_discarded(engagement, inventory):
    """SPEC 10: an assertion ruled out cannot carry work."""
    output = AuditAreaAnalysisOutput(
        assertions=[
            _analysis(Assertion.RIGHTS_AND_OBLIGATIONS, relevant=False, risks=[_risk()])
        ]
    )

    assessment = next(
        a for a in analyse_audit_area(inventory, engagement, client=_client(output))
        if a.assertion is Assertion.RIGHTS_AND_OBLIGATIONS
    )

    assert assessment.relevant is False
    assert assessment.risks == []


def test_relevant_assertion_with_no_risk_is_left_empty(engagement, inventory):
    """A contradiction, but inventing a risk would be worse. M10 surfaces it as a gap."""
    output = AuditAreaAnalysisOutput(
        assertions=[_analysis(Assertion.VALUATION, risks=[])]
    )

    assessment = _valuation(analyse_audit_area(inventory, engagement, client=_client(output)))

    assert assessment.relevant is True
    assert assessment.risks == []


# --- rating derivation ---------------------------------------------------------------


def test_system_rating_comes_from_the_matrix_not_the_model(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[_analysis(Assertion.VALUATION, risks=[_risk("low", "low")])]
    )

    risk = _valuation(analyse_audit_area(inventory, engagement, client=_client(output))).risks[0]

    assert (risk.likelihood, risk.magnitude) == (RiskLevel.LOW, RiskLevel.LOW)
    assert risk.system_rating is RiskLevel.LOW


def test_changing_the_matrix_changes_the_rating_for_identical_model_output(
    engagement, inventory, static_config
):
    """The rating is methodology, not model output."""
    inverted = static_config.model_copy(
        update={
            "risk_matrix": RiskMatrix(
                label="inverted",
                matrix={
                    "low": {"low": "high", "medium": "high", "high": "high"},
                    "medium": {"low": "low", "medium": "medium", "high": "high"},
                    "high": {"low": "low", "medium": "low", "high": "low"},
                },
            )
        }
    )
    output = AuditAreaAnalysisOutput(
        assertions=[_analysis(Assertion.VALUATION, risks=[_risk("low", "low")])]
    )

    risk = _valuation(
        analyse_audit_area(inventory, engagement, client=_client(output), config=inverted)
    ).risks[0]

    assert risk.likelihood is RiskLevel.LOW
    assert risk.system_rating is RiskLevel.HIGH


def test_mixed_levels_derive_the_configured_middle(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[_analysis(Assertion.VALUATION, risks=[_risk("low", "high")])]
    )

    risk = _valuation(analyse_audit_area(inventory, engagement, client=_client(output))).risks[0]

    assert risk.system_rating is RiskLevel.MEDIUM


def test_final_rating_starts_equal_and_unoverridden(engagement, inventory):
    risk = analyse_audit_area(inventory, engagement, client=_client(_all_five()))[0].risks[0]

    assert risk.final_rating is risk.system_rating
    assert risk.is_overridden is False
    assert risk.override_reason is None


# --- the user message ----------------------------------------------------------------


def test_user_message_carries_the_bounded_context(engagement, inventory):
    message = build_user_message(inventory, engagement, INVENTORY_CANDIDATES)

    assert "inventory" in message
    assert "8,900,000" in message and "6,200,000" in message
    assert "+2,700,000" in message and "+43.5%" in message
    assert "262,000" in message and "34.0x" in message
    assert "fashion retailer" in message
    assert "fact_1" in message and "fact_2" in message
    for candidate in INVENTORY_CANDIDATES:
        assert candidate.value in message


def test_user_message_covers_exactly_one_audit_area(engagement, inventory):
    """SPEC 6.1: batching is within an area, never across areas."""
    message = build_user_message(inventory, engagement, INVENTORY_CANDIDATES)

    assert "3,120,000" not in message  # cash
    assert "52,400,000" not in message  # turnover
    assert "trade_debtors" not in message


def test_user_message_does_not_suggest_a_rating(engagement, inventory):
    message = build_user_message(inventory, engagement, INVENTORY_CANDIDATES)

    assert "risk_rating" not in message
    assert "system_rating" not in message


def test_user_message_handles_absent_facts_and_context(raiatea_engagement, static_config):
    raiatea_engagement.materiality = calculate_materiality(raiatea_engagement)
    scope_line_items(raiatea_engagement, static_config)

    message = build_user_message(
        raiatea_engagement.line_item("inventory"), raiatea_engagement, INVENTORY_CANDIDATES
    )

    assert "None provided." in message
    assert "None extracted." in message


# --- defensive handling of model output ----------------------------------------------


def test_verdict_outside_the_candidate_list_is_dropped(engagement, static_config):
    cash = engagement.line_item("cash")
    candidates = static_config.candidate_assertions("cash")
    output = AuditAreaAnalysisOutput(
        assertions=[_analysis(a) for a in candidates] + [_analysis(Assertion.VALUATION)]
    )

    assessments = analyse_audit_area(cash, engagement, client=_client(output))

    assert Assertion.VALUATION not in {a.assertion for a in assessments}
    assert len(assessments) == len(candidates)


def test_missing_verdict_defaults_to_not_relevant(engagement, inventory):
    """Every candidate must get a verdict, or M10 cannot tell considered from skipped."""
    partial = AuditAreaAnalysisOutput(
        assertions=[_analysis(a) for a in INVENTORY_CANDIDATES if a is not Assertion.VALUATION]
    )

    assessments = analyse_audit_area(inventory, engagement, client=_client(partial))

    valuation = next(a for a in assessments if a.assertion is Assertion.VALUATION)
    assert len(assessments) == 5
    assert valuation.relevant is False
    assert valuation.rationale == NO_VERDICT_RATIONALE
    assert valuation.risks == []
    assert valuation.id


def test_duplicate_verdicts_keep_the_first(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[_analysis(a, rationale="first") for a in INVENTORY_CANDIDATES]
        + [_analysis(Assertion.VALUATION, relevant=False, rationale="second")]
    )

    assessments = analyse_audit_area(inventory, engagement, client=_client(output))

    valuation = next(a for a in assessments if a.assertion is Assertion.VALUATION)
    assert len(assessments) == 5
    assert valuation.rationale == "first"
    assert valuation.relevant is True


def test_unknown_fact_ids_are_dropped_from_assertions_and_risks(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[
            _analysis(
                Assertion.VALUATION,
                fact_ids=["fact_1", "fact_99"],
                risks=[_risk(fact_ids=["fact_2", "invented"])],
            )
        ]
    )

    assessment = _valuation(analyse_audit_area(inventory, engagement, client=_client(output)))

    assert assessment.supporting_fact_ids == ["fact_1"]
    assert assessment.risks[0].supporting_fact_ids == ["fact_2"]


def test_risk_without_a_description_is_dropped(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[
            _analysis(
                Assertion.VALUATION,
                risks=[_risk(description="  "), _risk(description="Real risk.")],
            )
        ]
    )

    assessment = _valuation(analyse_audit_area(inventory, engagement, client=_client(output)))

    assert [r.risk_description for r in assessment.risks] == ["Real risk."]
    assert assessment.risks[0].id == "risk_1"  # the discarded one burned no ID


def test_blank_rationales_are_marked_rather_than_stored_empty(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[_analysis(Assertion.VALUATION, rationale="  ", risks=[_risk(rationale=" ")])]
    )

    assessment = _valuation(analyse_audit_area(inventory, engagement, client=_client(output)))

    assert assessment.rationale == MISSING_RATIONALE
    assert assessment.risks[0].rationale == MISSING_RATIONALE


def test_whitespace_is_stripped(engagement, inventory):
    output = AuditAreaAnalysisOutput(
        assertions=[
            _analysis(
                Assertion.VALUATION,
                rationale="  Seasonal stock.  ",
                risks=[_risk(description="  Obsolescence.  ")],
            )
        ]
    )

    assessment = _valuation(analyse_audit_area(inventory, engagement, client=_client(output)))

    assert assessment.rationale == "Seasonal stock."
    assert assessment.risks[0].risk_description == "Obsolescence."


# --- scope guards --------------------------------------------------------------------


def test_non_audit_area_returns_nothing_without_calling_the_model(engagement):
    client = ScriptedLLMClient()

    assert analyse_audit_area(engagement.line_item("turnover"), engagement, client=client) == []
    assert client.call_count() == 0


def test_immaterial_audit_area_returns_nothing_without_calling_the_model(static_config):
    engagement = make_engagement(
        ("turnover", 10_000_000, 9_000_000),
        ("profit_before_tax", 300_000, 250_000),
        ("cash", 10_000, 9_000),
    )
    engagement.materiality = calculate_materiality(engagement)  # 50,000
    scope_line_items(engagement, static_config)
    cash = engagement.line_item("cash")
    assert cash.is_audit_area is True and cash.material is False

    client = ScriptedLLMClient()

    assert analyse_audit_area(cash, engagement, client=client, config=static_config) == []
    assert client.call_count() == 0
    assert engagement.id_sequences == {}


def test_unscoped_line_item_is_rejected(raiatea_engagement):
    client = ScriptedLLMClient()

    with pytest.raises(AuditAreaAnalysisError, match="must be scoped"):
        analyse_audit_area(
            raiatea_engagement.line_item("inventory"), raiatea_engagement, client=client
        )
    assert client.call_count() == 0


# --- live (opt-in) -------------------------------------------------------------------


@pytest.mark.llm
def test_live_analysis_returns_relevance_and_risks_in_one_call(engagement, inventory):
    """The whole batched call against the real model. Run with `pytest -m llm`."""
    from src.engine.risk_matrix import derive_rating
    from src.llm.client import AnthropicLLMClient

    assessments = analyse_audit_area(inventory, engagement, client=AnthropicLLMClient())

    assert [a.assertion for a in assessments] == INVENTORY_CANDIDATES

    valuation = next(a for a in assessments if a.assertion is Assertion.VALUATION)
    assert valuation.relevant is True
    assert valuation.risks, "aged seasonal inventory must yield a valuation risk"
    # Absolute sanity: aged seasonal stock is not a low valuation risk.
    assert any(r.final_rating in {RiskLevel.MEDIUM, RiskLevel.HIGH} for r in valuation.risks)

    known = {f.id for f in engagement.company_facts}
    for assessment in assessments:
        assert len(assessment.risks) <= MAX_RISKS_PER_ASSERTION
        if not assessment.relevant:
            assert assessment.risks == []
        assert set(assessment.supporting_fact_ids) <= known
        for risk in assessment.risks:
            assert risk.system_rating is derive_rating(risk.likelihood, risk.magnitude)
            assert risk.final_rating is risk.system_rating
            assert set(risk.supporting_fact_ids) <= known
