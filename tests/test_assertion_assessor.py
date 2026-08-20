"""M5 verification: assertion relevance (SPEC 10, ISA 315.29)."""

import pytest

from src.engine.materiality import calculate_materiality
from src.engine.scoping import scope_line_items
from src.llm.assertion_assessor import (
    MISSING_RATIONALE,
    NO_VERDICT_RATIONALE,
    AssertionAssessmentError,
    assess_assertions,
    build_user_message,
)
from src.llm.client import LLMTask
from src.llm.prompts import ASSESS_ASSERTIONS
from src.llm.schemas import AssertionRelevanceOutput, AssertionVerdict
from src.models.audit_objects import Assertion
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


def _verdict(assertion, relevant=True, rationale="Because.", fact_ids=None):
    return AssertionVerdict(
        assertion=assertion,
        relevant=relevant,
        rationale=rationale,
        supporting_fact_ids=fact_ids or [],
    )


def _all_five(**overrides) -> AssertionRelevanceOutput:
    return AssertionRelevanceOutput(
        assertions=[
            _verdict(a, relevant=overrides.get(a.value, True)) for a in INVENTORY_CANDIDATES
        ]
    )


def _client(output) -> ScriptedLLMClient:
    return ScriptedLLMClient(assess_assertions=output)


# --- happy path ----------------------------------------------------------------------


def test_five_candidates_produce_five_assessments(engagement, inventory):
    client = _client(_all_five())

    assessments = assess_assertions(inventory, engagement, client=client)

    assert len(assessments) == 5
    assert [a.assertion for a in assessments] == INVENTORY_CANDIDATES
    client.assert_all_consumed()


def test_each_assessment_is_fully_linked(engagement, inventory):
    assessments = assess_assertions(inventory, engagement, client=_client(_all_five()))

    for assessment in assessments:
        assert assessment.id.startswith("assertion_")
        assert assessment.line_item_id == inventory.id
        assert assessment.isa_refs == ["ISA315.29"]
        assert assessment.rationale
        assert assessment.risks == []


def test_assessment_ids_are_unique_and_monotonic(engagement, inventory):
    assessments = assess_assertions(inventory, engagement, client=_client(_all_five()))

    ids = [a.id for a in assessments]
    assert ids == [f"assertion_{i}" for i in range(1, 6)]
    assert len(set(ids)) == 5


def test_relevance_verdicts_are_carried_through(engagement, inventory):
    client = _client(_all_five(rights_and_obligations=False))

    assessments = assess_assertions(inventory, engagement, client=client)

    by_assertion = {a.assertion: a for a in assessments}
    assert by_assertion[Assertion.VALUATION].relevant is True
    assert by_assertion[Assertion.RIGHTS_AND_OBLIGATIONS].relevant is False


def test_isa_refs_come_from_config_not_a_hardcoded_string(engagement, inventory, static_config):
    """Adding an ISA requirement should be a JSON edit (SPEC 4)."""
    expected = static_config.isa_refs_for("AssertionAssessment")

    assessments = assess_assertions(inventory, engagement, client=_client(_all_five()))

    assert expected == ["ISA315.29"]
    assert all(a.isa_refs == expected for a in assessments)


def test_results_are_not_assigned_to_the_line_item(engagement, inventory):
    assess_assertions(inventory, engagement, client=_client(_all_five()))

    assert inventory.assertions == []


# --- candidate assertions come from config -------------------------------------------


def test_cash_uses_its_own_shorter_candidate_list(engagement, static_config):
    cash = engagement.line_item("cash")
    candidates = static_config.candidate_assertions("cash")
    client = _client(AssertionRelevanceOutput(assertions=[_verdict(a) for a in candidates]))

    assessments = assess_assertions(cash, engagement, client=client)

    assert [a.assertion for a in assessments] == candidates
    assert Assertion.VALUATION not in {a.assertion for a in assessments}


def test_non_audit_area_returns_nothing_without_calling_the_model(engagement):
    """Turnover is material but has no profile, so there is no methodology to apply."""
    client = ScriptedLLMClient()

    assert assess_assertions(engagement.line_item("turnover"), engagement, client=client) == []
    assert client.call_count() == 0


def test_immaterial_audit_area_returns_nothing_without_calling_the_model(static_config):
    """SPEC 6/8/10 restrict assessment to *material* audit areas.

    Cash here has an implemented profile but falls below materiality, so it is out of scope
    despite being an audit area — the inverse of the turnover case above.
    """
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

    assert assess_assertions(cash, engagement, client=client, config=static_config) == []
    assert client.call_count() == 0
    assert engagement.id_sequences == {}  # no IDs burned on out-of-scope work


def test_material_audit_area_is_assessed(engagement, inventory):
    """The positive half of the guard, so it cannot silently reject everything."""
    assert inventory.material is True

    assert len(assess_assertions(inventory, engagement, client=_client(_all_five()))) == 5


# --- the model is called correctly ---------------------------------------------------


def test_uses_the_assertion_task_and_prompt(engagement, inventory):
    client = _client(_all_five())

    assess_assertions(inventory, engagement, client=client)

    call = client.calls_for(LLMTask.ASSESS_ASSERTIONS)[0]
    assert call.system == ASSESS_ASSERTIONS
    assert call.output_format is AssertionRelevanceOutput


def test_one_call_per_audit_area(engagement, inventory):
    client = _client(_all_five())

    assess_assertions(inventory, engagement, client=client)

    assert client.call_count() == 1  # not one per assertion


def test_user_message_carries_the_bounded_context(engagement, inventory, static_config):
    message = build_user_message(inventory, engagement, INVENTORY_CANDIDATES)

    assert "inventory" in message
    assert "8,900,000" in message and "6,200,000" in message  # CY and PY
    assert "+2,700,000" in message and "+43.5%" in message  # derived metrics
    assert "262,000" in message  # materiality
    assert "34.0x" in message  # ratio
    assert "fashion retailer" in message  # raw context
    assert "fact_1" in message and "fact_2" in message  # facts, by referenceable ID
    for candidate in INVENTORY_CANDIDATES:
        assert candidate.value in message


def test_user_message_excludes_other_line_items(engagement, inventory):
    """SPEC 10/21: this judgement is about one audit area; cash cannot inform it."""
    message = build_user_message(inventory, engagement, INVENTORY_CANDIDATES)

    assert "3,120,000" not in message  # cash CY
    assert "52,400,000" not in message  # turnover CY
    assert "trade_debtors" not in message


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
    """Cash has no valuation candidate; accepting one would contradict its profile."""
    cash = engagement.line_item("cash")
    candidates = static_config.candidate_assertions("cash")
    client = _client(
        AssertionRelevanceOutput(
            assertions=[_verdict(a) for a in candidates] + [_verdict(Assertion.VALUATION)]
        )
    )

    assessments = assess_assertions(cash, engagement, client=client)

    assert Assertion.VALUATION not in {a.assertion for a in assessments}
    assert len(assessments) == len(candidates)


def test_missing_verdict_defaults_to_not_relevant(engagement, inventory):
    """Every candidate must get a verdict, or M10 cannot tell 'considered' from 'skipped'."""
    partial = AssertionRelevanceOutput(
        assertions=[_verdict(a) for a in INVENTORY_CANDIDATES if a is not Assertion.VALUATION]
    )

    assessments = assess_assertions(inventory, engagement, client=_client(partial))

    valuation = next(a for a in assessments if a.assertion is Assertion.VALUATION)
    assert len(assessments) == 5
    assert valuation.relevant is False
    assert valuation.rationale == NO_VERDICT_RATIONALE
    assert valuation.id  # still a real, referenceable object


def test_duplicate_verdicts_keep_the_first(engagement, inventory):
    output = AssertionRelevanceOutput(
        assertions=[
            _verdict(a, rationale="first") for a in INVENTORY_CANDIDATES
        ]
        + [_verdict(Assertion.VALUATION, relevant=False, rationale="second")]
    )

    assessments = assess_assertions(inventory, engagement, client=_client(output))

    valuation = next(a for a in assessments if a.assertion is Assertion.VALUATION)
    assert len(assessments) == 5
    assert valuation.rationale == "first"
    assert valuation.relevant is True


def test_unknown_fact_ids_are_dropped(engagement, inventory):
    """A dangling reference would break traceability silently (SPEC 14)."""
    output = AssertionRelevanceOutput(
        assertions=[
            _verdict(a, fact_ids=["fact_1", "fact_99", "made_up"]) for a in INVENTORY_CANDIDATES
        ]
    )

    assessments = assess_assertions(inventory, engagement, client=_client(output))

    assert all(a.supporting_fact_ids == ["fact_1"] for a in assessments)


def test_known_fact_ids_are_preserved_in_order(engagement, inventory):
    output = AssertionRelevanceOutput(
        assertions=[_verdict(a, fact_ids=["fact_2", "fact_1"]) for a in INVENTORY_CANDIDATES]
    )

    assessments = assess_assertions(inventory, engagement, client=_client(output))

    assert assessments[0].supporting_fact_ids == ["fact_2", "fact_1"]


def test_blank_rationale_is_marked_rather_than_stored_empty(engagement, inventory):
    """The verdict must survive, but the gap in the audit trail has to be visible."""
    output = AssertionRelevanceOutput(
        assertions=[_verdict(a, rationale="   ") for a in INVENTORY_CANDIDATES]
    )

    assessments = assess_assertions(inventory, engagement, client=_client(output))

    assert all(a.rationale == MISSING_RATIONALE for a in assessments)


def test_rationale_whitespace_is_stripped(engagement, inventory):
    output = AssertionRelevanceOutput(
        assertions=[_verdict(a, rationale="  Seasonal stock.  ") for a in INVENTORY_CANDIDATES]
    )

    assessments = assess_assertions(inventory, engagement, client=_client(output))

    assert assessments[0].rationale == "Seasonal stock."


# --- preconditions -------------------------------------------------------------------


def test_unscoped_line_item_is_rejected(raiatea_engagement):
    """SPEC 10 requires materiality and derived metrics in the prompt."""
    client = ScriptedLLMClient()

    with pytest.raises(AssertionAssessmentError, match="must be scoped"):
        assess_assertions(raiatea_engagement.line_item("inventory"), raiatea_engagement,
                          client=client)
    assert client.call_count() == 0


# --- live (opt-in) -------------------------------------------------------------------


@pytest.mark.llm
def test_live_inventory_valuation_is_relevant(engagement, inventory):
    """Seasonal, aged inventory must make valuation relevant — the demo depends on it."""
    from src.llm.client import AnthropicLLMClient

    assessments = assess_assertions(inventory, engagement, client=AnthropicLLMClient())

    assert [a.assertion for a in assessments] == INVENTORY_CANDIDATES
    valuation = next(a for a in assessments if a.assertion is Assertion.VALUATION)
    assert valuation.relevant is True
    assert valuation.rationale != NO_VERDICT_RATIONALE
    known = {f.id for f in engagement.company_facts}
    assert all(set(a.supporting_fact_ids) <= known for a in assessments)
