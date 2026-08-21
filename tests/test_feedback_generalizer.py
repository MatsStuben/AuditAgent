"""M12 verification: feedback → candidate methodology rule (SPEC 19).

The claim under test is a negative one as much as a positive one: an override can *propose* a
methodology change, and can never make one. Hence the byte-identical config check below.
"""

import hashlib
from pathlib import Path

import pytest

from src.config.loader import DATA_DIR
from src.engine.recompute import (
    add_auditor_procedure,
    override_risk_rating,
    update_company_context,
)
from src.llm.client import LLMTask
from src.llm.feedback_generalizer import (
    UNRESOLVED,
    FeedbackGeneralizationError,
    build_user_message,
    format_engagement_context,
    generalize_feedback,
    is_analysable,
)
from src.llm.prompts import GENERALIZE_FEEDBACK
from src.llm.schemas import (
    EngagementSpecificFeedback,
    FeedbackClassificationOutput,
    MethodologyRuleProposalOutput,
)
from src.models.audit_objects import RiskLevel
from src.models.feedback import AuditorFeedback, FeedbackAnalysisOutcome, RuleProposalStatus
from tests.conftest import CASH_RISK, INVENTORY_RISK, scripted_selection
from tests.fakes import ScriptedLLMClient

METHODOLOGY_FILES = (
    "procedure_catalogue.json",
    "audit_area_profiles.json",
    "risk_matrix.json",
    "isa_requirements.json",
)


def _rule(
    condition="turnover is below 10m and inventory valuation risk is not high",
    action="do not require subsequent-sales testing",
    reason="Pre-sold stock removes the exposure the procedure responds to.",
) -> FeedbackClassificationOutput:
    return FeedbackClassificationOutput(
        classification=MethodologyRuleProposalOutput(
            type="methodology_rule_proposal", condition=condition, action=action, reason=reason
        )
    )


def _specific(reason="Turns on this client's contractual pre-sale terms.") -> (
    FeedbackClassificationOutput
):
    return FeedbackClassificationOutput(
        classification=EngagementSpecificFeedback(type="engagement_specific", reason=reason)
    )


def _client(output) -> ScriptedLLMClient:
    return ScriptedLLMClient(generalize_feedback=output)


@pytest.fixture
def overridden(static_config, engagement):
    """An engagement carrying one real override: inventory valuation risk, high → low."""
    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW,
        "The inventory is contractually pre-sold and has very low obsolescence exposure.",
        client=ScriptedLLMClient(
            select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", INVENTORY_RISK)
        ),
        config=static_config,
    )
    return engagement


# --- the two branches ------------------------------------------------------------------------


def test_a_generalisable_reason_becomes_a_pending_proposal(overridden):
    feedback = overridden.feedback[0]
    client = _client(_rule())

    proposal = generalize_feedback(feedback, overridden, client=client)

    assert proposal is not None
    assert proposal.condition.startswith("turnover is below 10m")
    assert proposal.action == "do not require subsequent-sales testing"
    assert proposal.status is RuleProposalStatus.PENDING_REVIEW
    assert proposal.source_feedback_id == feedback.id
    assert client.call_count(LLMTask.GENERALIZE_FEEDBACK) == 1


def test_an_engagement_specific_reason_proposes_nothing(overridden):
    feedback = overridden.feedback[0]

    assert generalize_feedback(feedback, overridden, client=_client(_specific())) is None
    assert overridden.rule_proposals == []
    assert len(overridden.feedback_analyses) == 1
    analysis = overridden.feedback_analyses[0]
    assert analysis.source_feedback_id == feedback.id
    assert analysis.outcome is FeedbackAnalysisOutcome.ENGAGEMENT_SPECIFIC
    assert "contractual pre-sale" in analysis.reason


def test_reanalysing_an_engagement_specific_result_does_not_make_another_call(overridden):
    feedback = overridden.feedback[0]
    generalize_feedback(feedback, overridden, client=_client(_specific()))
    client = _client(_specific("A different answer."))

    assert generalize_feedback(feedback, overridden, client=client) is None
    assert client.calls == []
    assert len(overridden.feedback_analyses) == 1


def test_a_proposal_is_filed_on_the_engagement_and_traces_to_its_override(overridden):
    proposal = generalize_feedback(overridden.feedback[0], overridden, client=_client(_rule()))

    assert overridden.rule_proposals == [proposal]
    source = next(f for f in overridden.feedback if f.id == proposal.source_feedback_id)
    assert source.before == {"final_rating": "high"}
    assert source.after == {"final_rating": "low"}
    assert overridden.feedback_analyses[0].proposal_id == proposal.id


def test_proposals_accumulate_rather_than_replace(static_config, overridden):
    """Each refers to a different override; none supersedes the one before it."""
    generalize_feedback(overridden.feedback[0], overridden, client=_client(_rule()))
    override_risk_rating(
        overridden, CASH_RISK, RiskLevel.LOW, "Balances are confirmed directly.",
        client=ScriptedLLMClient(
            select_procedures=scripted_selection("CASH_BANK_CONFIRMATION", CASH_RISK)
        ),
        config=static_config,
    )

    second = generalize_feedback(
        overridden.feedback[1], overridden, client=_client(_rule(condition="a second rule"))
    )

    assert [p.id for p in overridden.rule_proposals] == ["rule_1", "rule_2"]
    assert second.source_feedback_id == overridden.feedback[1].id


def test_re_analysing_the_same_override_does_not_file_a_second_proposal(overridden):
    """Two pending proposals for one override is the same question asked twice."""
    first = generalize_feedback(overridden.feedback[0], overridden, client=_client(_rule()))
    client = _client(_rule(condition="something else"))

    again = generalize_feedback(overridden.feedback[0], overridden, client=client)

    assert again is first
    assert overridden.rule_proposals == [first]
    assert client.calls == []


# --- what the model is shown --------------------------------------------------------------------


def test_the_prompt_carries_the_four_spec_19_inputs(overridden):
    client = _client(_rule())

    generalize_feedback(overridden.feedback[0], overridden, client=client)

    (call,) = client.calls
    assert call.system is GENERALIZE_FEEDBACK
    assert call.task is LLMTask.GENERALIZE_FEEDBACK
    # the system proposal, the change, the reason, the engagement context
    assert "valuation" in call.user and "rating high" in call.user
    assert "final_rating: high" in call.user and "final_rating: low" in call.user
    assert "contractually pre-sold" in call.user
    assert "Raiatea Ltd" in call.user and "262,000" in call.user


def test_the_prompt_does_not_carry_unrelated_audit_work(overridden):
    """A rule drawn from work the auditor never commented on would not be their feedback."""
    client = _client(_rule())

    generalize_feedback(overridden.feedback[0], overridden, client=client)

    (call,) = client.calls
    assert "cash" not in call.user.lower()


def test_an_auditor_added_procedure_is_described_as_such_for_methodology_analysis(
    static_config, engagement
):
    """A non-catalogue addition must not be misrepresented to the LLM as an AI suggestion."""
    feedback = add_auditor_procedure(
        engagement,
        INVENTORY_RISK,
        "Inspect signed customer orders",
        "Inspect orders supporting the stock held at year end.",
        "The stock is contractually pre-sold to named customers.",
        config=static_config,
    )

    message = build_user_message(engagement, feedback)

    assert "auditor-added procedure" in message
    assert "AI suggestion" not in message
    assert "Inspect signed customer orders" in message


def test_a_record_whose_object_is_gone_still_describes_the_override(static_config, overridden):
    """SPEC 18: feedback outlives its object, and generalisation must survive that."""
    feedback = overridden.feedback[0]
    overridden.line_item("inventory").assertions = []  # as a re-analysis would leave it

    message = build_user_message(overridden, feedback)

    assert UNRESOLVED in message
    assert "final_rating: high" in message  # the substance is still there
    assert "contractually pre-sold" in message


def test_the_context_shown_is_the_one_recorded_with_the_override(static_config, overridden):
    """A later re-extraction must not reach a proposal attributed to an earlier judgement.

    Otherwise the model reasons about circumstances the auditor did not have, and the proposal
    misdescribes what they decided.
    """
    feedback = overridden.feedback[0]
    before = format_engagement_context(feedback.engagement_context)
    assert "inventory_ageing" in before and "8,900,000" in before

    update_company_context(
        overridden, "A revised description, with different facts in it.", "Client update.",
        client=_context_client(static_config), config=static_config,
    )

    assert overridden.company_facts[0].id == "fact_2"  # the file has since moved on
    assert format_engagement_context(feedback.engagement_context) == before
    assert "fact_2" not in build_user_message(overridden, feedback)


# --- eligibility is deterministic ----------------------------------------------------------------


def test_an_engagement_input_change_is_not_analysable(static_config, overridden):
    """SPEC 19 learns from overridden judgements. Revised source data is new input, and what
    follows from it is dependency logic the engine already owns (SPEC 17)."""
    update_company_context(
        overridden, "A revised description.", "Client update.",
        client=_context_client(static_config), config=static_config,
    )
    record = overridden.feedback[1]
    client = _client(_rule())

    assert is_analysable(record) is False
    with pytest.raises(FeedbackGeneralizationError, match="new input"):
        generalize_feedback(record, overridden, client=client)

    assert client.calls == []
    assert overridden.rule_proposals == []


@pytest.mark.parametrize(
    "object_type", ["risk_assessment", "assertion_assessment", "procedure"]
)
def test_judgement_overrides_are_analysable(object_type):
    assert is_analysable(
        AuditorFeedback(id="feedback_1", object_type=object_type, object_id="x")
    )


def test_a_same_id_record_cannot_smuggle_different_evidence(overridden):
    """The argument identifies the record; the engagement supplies it.

    A proposal filed against a real feedback ID must have been drawn from that record, or
    `source_feedback_id` no longer names the evidence the model actually assessed.
    """
    real = overridden.feedback[0]
    forged = real.model_copy(
        update={"reason": "Fabricated reason.", "after": {"final_rating": "high"}}
    )
    client = _client(_rule())

    proposal = generalize_feedback(forged, overridden, client=client)

    (call,) = client.calls
    assert "Fabricated reason." not in call.user
    assert "contractually pre-sold" in call.user
    assert proposal.source_feedback_id == real.id


# --- rejection and errors ------------------------------------------------------------------------


@pytest.mark.parametrize("condition,action", [("", "do something"), ("if x", "   ")])
def test_a_rule_with_nothing_to_review_is_dropped(overridden, condition, action):
    """No condition means it always applies; no action means it asks for nothing."""
    proposal = generalize_feedback(
        overridden.feedback[0], overridden, client=_client(_rule(condition, action))
    )

    assert proposal is None
    assert overridden.rule_proposals == []


def test_feedback_from_another_engagement_raises(overridden):
    stranger = AuditorFeedback(
        id="feedback_99", object_type="risk_assessment", object_id=INVENTORY_RISK
    )

    with pytest.raises(FeedbackGeneralizationError, match="feedback_99"):
        generalize_feedback(stranger, overridden, client=_client(_rule()))


# --- the negative claim: methodology is never rewritten -------------------------------------------


def test_generalising_leaves_every_methodology_file_byte_identical(overridden):
    """SPEC 19: the LLM must not automatically alter production methodology."""
    before = {name: _digest(name) for name in METHODOLOGY_FILES}

    generalize_feedback(overridden.feedback[0], overridden, client=_client(_rule()))

    assert {name: _digest(name) for name in METHODOLOGY_FILES} == before


def test_a_proposal_changes_nothing_in_the_audit_file(overridden):
    """It is addressed to a methodology owner, not to this engagement."""
    inventory = overridden.line_item("inventory")
    risk = inventory.risk(INVENTORY_RISK)
    before = (inventory.assertions, inventory.procedures, risk.final_rating)

    generalize_feedback(overridden.feedback[0], overridden, client=_client(_rule()))

    assert inventory.assertions is before[0]
    assert inventory.procedures is before[1]
    assert inventory.risk(INVENTORY_RISK).final_rating is before[2]


def _digest(name: str) -> str:
    return hashlib.sha256(Path(DATA_DIR / name).read_bytes()).hexdigest()


def _context_client(static_config) -> ScriptedLLMClient:
    """Scripted for the `1 + 2n` calls a context change costs."""
    from src.models.audit_objects import Assertion
    from tests.conftest import scripted_analysis, scripted_facts

    return ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=[
            scripted_analysis(
                Assertion.VALUATION, static_config.candidate_assertions("inventory")
            ),
            scripted_analysis(Assertion.EXISTENCE, static_config.candidate_assertions("cash")),
        ],
        select_procedures=[
            scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
            scripted_selection("CASH_BANK_CONFIRMATION", "risk_4"),
        ],
    )


# --- live (opt-in) --------------------------------------------------------------------------------


@pytest.mark.llm
def test_live_classification_returns_one_of_the_two_branches(overridden):
    """Run with `pytest -m llm`."""
    from src.llm.client import AnthropicLLMClient

    proposal = generalize_feedback(
        overridden.feedback[0], overridden, client=AnthropicLLMClient()
    )

    if proposal is not None:
        assert proposal.condition and proposal.action
        assert proposal.status is RuleProposalStatus.PENDING_REVIEW
        assert proposal.source_feedback_id == overridden.feedback[0].id
