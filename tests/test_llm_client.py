"""M3 verification: the LLM seam, output schemas and the scripted fake (SPEC 21)."""

import os
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, ValidationError

from src.llm.client import (
    DEFAULT_MODEL,
    TASK_CONFIG,
    AnthropicLLMClient,
    LLMError,
    LLMTask,
    TaskConfig,
)
from src.llm.prompts import (
    ASSESS_ASSERTIONS,
    ASSESS_RISKS,
    EXTRACT_COMPANY_FACTS,
    GENERALIZE_FEEDBACK,
    SELECT_PROCEDURES,
)
from src.llm.schemas import (
    AssertionRelevanceOutput,
    CompanyFactsOutput,
    FeedbackClassificationOutput,
    ProcedureSelectionOutput,
    RiskAssessmentOutput,
    RiskIdentificationOutput,
)
from src.models.audit_objects import Assertion, RiskLevel
from tests.fakes import FailingLLMClient, ScriptedLLMClient

# --- task configuration --------------------------------------------------------------


def test_task_config_covers_every_task():
    assert set(TASK_CONFIG) == set(LLMTask)
    assert len(LLMTask) == 5  # the five SPEC 21 services


@pytest.mark.parametrize("task", list(LLMTask))
def test_every_task_config_is_usable(task):
    config = TASK_CONFIG[task]

    assert config.model == DEFAULT_MODEL
    assert config.max_tokens > 0
    assert config.effort in {"low", "medium", "high", "xhigh", "max"}


def test_effort_is_tuned_per_task_not_uniform():
    """SPEC 21: not every task requires maximum reasoning effort."""
    efforts = {task: TASK_CONFIG[task].effort for task in LLMTask}

    assert efforts[LLMTask.EXTRACT_COMPANY_FACTS] == "low"  # near-mechanical
    assert efforts[LLMTask.ASSESS_RISKS] == "high"  # the core judgement
    assert len(set(efforts.values())) > 1


# --- prompts -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [EXTRACT_COMPANY_FACTS, ASSESS_ASSERTIONS, ASSESS_RISKS, SELECT_PROCEDURES,
     GENERALIZE_FEEDBACK],
)
def test_prompts_do_not_ask_for_json(prompt):
    """Output shape is enforced by schema; describing it in prose would duplicate that."""
    assert "JSON" not in prompt.upper()
    assert prompt.strip()


def test_risk_prompt_does_not_ask_for_a_rating():
    """The rating is derived from the matrix, so the model must not be asked for one."""
    assert "risk_rating" not in ASSESS_RISKS
    assert "likelihood" in ASSESS_RISKS and "magnitude" in ASSESS_RISKS


# --- schemas: bounded values ---------------------------------------------------------


def test_valid_risk_payload_parses():
    output = RiskAssessmentOutput(
        risk_description="Inventory may be carried above recoverable value.",
        likelihood="high",
        magnitude="medium",
        rationale="Aged seasonal stock.",
        supporting_fact_ids=["fact_1"],
    )

    assert output.likelihood is RiskLevel.HIGH
    assert output.magnitude is RiskLevel.MEDIUM


def test_out_of_range_likelihood_is_rejected():
    with pytest.raises(ValidationError):
        RiskAssessmentOutput(
            risk_description="x", likelihood="severe", magnitude="high", rationale="y"
        )


def test_unknown_assertion_is_rejected():
    with pytest.raises(ValidationError):
        AssertionRelevanceOutput(
            assertions=[{"assertion": "cutoff", "relevant": True, "rationale": "x"}]
        )


def test_risk_output_has_no_rating_field():
    """Decision 3: the model returns likelihood and magnitude only (SPEC 11)."""
    assert "risk_rating" not in RiskAssessmentOutput.model_fields
    assert set(RiskAssessmentOutput.model_fields) == {
        "risk_description", "likelihood", "magnitude", "rationale", "supporting_fact_ids",
    }


def test_supporting_fact_ids_default_to_empty():
    output = RiskAssessmentOutput(
        risk_description="x", likelihood="low", magnitude="low", rationale="y"
    )
    assert output.supporting_fact_ids == []


def test_two_risks_parse_and_more_are_not_schema_blocked():
    """The cap is a prompt instruction; M6 truncates rather than failing a good response."""
    assert len(RiskIdentificationOutput(risks=[_risk(), _risk()]).risks) == 2
    assert len(RiskIdentificationOutput(risks=[_risk(), _risk(), _risk()]).risks) == 3


def _risk() -> RiskAssessmentOutput:
    return RiskAssessmentOutput(
        risk_description="x", likelihood="low", magnitude="low", rationale="y"
    )


def test_procedure_selection_suggestion_is_optional():
    without = ProcedureSelectionOutput(
        selected_procedures=[{"procedure_id": "INV_SUBSEQUENT_SALES", "rationale": "x"}]
    )
    assert without.suggested_new_procedure is None

    with_suggestion = ProcedureSelectionOutput(
        selected_procedures=[],
        suggested_new_procedure={"description": "d", "rationale": "r"},
    )
    assert with_suggestion.suggested_new_procedure.description == "d"


def test_company_facts_output_allows_empty():
    assert CompanyFactsOutput(facts=[]).facts == []


# --- schemas: feedback classification union ------------------------------------------


def test_engagement_specific_branch_parses():
    output = FeedbackClassificationOutput(
        classification={"type": "engagement_specific", "reason": "One-off."}
    )
    assert output.classification.type == "engagement_specific"


def test_methodology_rule_branch_parses():
    output = FeedbackClassificationOutput(
        classification={
            "type": "methodology_rule_proposal",
            "condition": "revenue < 10m",
            "action": "do not require procedure X",
            "reason": "...",
        }
    )
    assert output.classification.action == "do not require procedure X"


def test_unknown_classification_type_is_rejected():
    with pytest.raises(ValidationError):
        FeedbackClassificationOutput(classification={"type": "maybe", "reason": "x"})


def test_methodology_branch_requires_a_condition():
    """Without a condition a proposal could not be evaluated on another engagement."""
    with pytest.raises(ValidationError):
        FeedbackClassificationOutput(
            classification={
                "type": "methodology_rule_proposal", "action": "a", "reason": "r"
            }
        )


# --- AnthropicLLMClient (stubbed transport, no network) ------------------------------


class _StubMessages:
    def __init__(self, response):
        self._response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _StubAnthropic:
    def __init__(self, response):
        self.messages = _StubMessages(response)


def _response(parsed, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(
        parsed_output=parsed, stop_reason=stop_reason, stop_details=stop_details
    )


class _Out(BaseModel):
    value: str


def test_client_applies_the_task_config():
    stub = _StubAnthropic(_response(_Out(value="ok")))
    client = AnthropicLLMClient(client=stub)

    result = client.parse(
        task=LLMTask.EXTRACT_COMPANY_FACTS, system="sys", user="usr", output_format=_Out
    )

    assert result.value == "ok"
    kwargs = stub.messages.kwargs
    config = TASK_CONFIG[LLMTask.EXTRACT_COMPANY_FACTS]
    assert kwargs["model"] == config.model
    assert kwargs["max_tokens"] == config.max_tokens
    assert kwargs["output_config"] == {"effort": config.effort}
    assert kwargs["output_format"] is _Out
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": "usr"}]


def test_client_config_is_injectable():
    stub = _StubAnthropic(_response(_Out(value="ok")))
    override = {LLMTask.ASSESS_RISKS: TaskConfig(model="other", max_tokens=11, effort="max")}
    client = AnthropicLLMClient(client=stub, config=override)

    client.parse(task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=_Out)

    assert stub.messages.kwargs["model"] == "other"
    assert stub.messages.kwargs["max_tokens"] == 11


def test_refusal_raises_rather_than_returning_none():
    stub = _StubAnthropic(
        _response(None, stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    )
    client = AnthropicLLMClient(client=stub)

    with pytest.raises(LLMError, match="declined"):
        client.parse(task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=_Out)


def test_truncated_response_raises():
    stub = _StubAnthropic(_response(None, stop_reason="max_tokens"))
    client = AnthropicLLMClient(client=stub)

    with pytest.raises(LLMError, match="token cap"):
        client.parse(task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=_Out)


def test_missing_parsed_output_raises():
    stub = _StubAnthropic(_response(None))
    client = AnthropicLLMClient(client=stub)

    with pytest.raises(LLMError, match="no parsed output"):
        client.parse(task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=_Out)


def test_connection_error_is_wrapped():
    stub = _StubAnthropic(anthropic.APIConnectionError(request=None))
    client = AnthropicLLMClient(client=stub)

    with pytest.raises(LLMError, match="could not reach"):
        client.parse(task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=_Out)


def test_api_status_error_is_wrapped_with_its_code():
    """A real SDK exception, so this keeps working if the SDK changes its hierarchy."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.RateLimitError(
        "slow down", response=httpx2.Response(429, request=request), body=None
    )
    client = AnthropicLLMClient(client=_StubAnthropic(error))

    with pytest.raises(LLMError, match="429"):
        client.parse(task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=_Out)


def test_rate_limit_and_not_found_both_surface_their_status():
    """Retryable and non-retryable failures stay distinguishable via the status code."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    not_found = anthropic.NotFoundError(
        "no such model", response=httpx2.Response(404, request=request), body=None
    )
    client = AnthropicLLMClient(client=_StubAnthropic(not_found))

    with pytest.raises(LLMError, match="404"):
        client.parse(task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=_Out)


# --- the scripted fake ---------------------------------------------------------------


def test_fake_returns_queued_responses_in_order():
    first, second = _risk(), _risk()
    client = ScriptedLLMClient(assess_risks=[first, second])

    a = client.parse(
        task=LLMTask.ASSESS_RISKS, system="s", user="u1", output_format=RiskAssessmentOutput
    )
    b = client.parse(
        task=LLMTask.ASSESS_RISKS, system="s", user="u2", output_format=RiskAssessmentOutput
    )

    assert a is first and b is second
    assert client.call_count(LLMTask.ASSESS_RISKS) == 2
    assert client.call_count() == 2
    assert client.last_user_message(LLMTask.ASSESS_RISKS) == "u2"


def test_fake_accepts_a_single_response_without_a_list():
    client = ScriptedLLMClient(assess_risks=_risk())

    client.parse(
        task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=RiskAssessmentOutput
    )

    client.assert_all_consumed()


def test_fake_rejects_an_unscripted_task():
    client = ScriptedLLMClient(assess_risks=_risk())

    with pytest.raises(AssertionError, match="unscripted LLM call"):
        client.parse(
            task=LLMTask.SELECT_PROCEDURES, system="s", user="u",
            output_format=ProcedureSelectionOutput,
        )


def test_fake_rejects_more_calls_than_scripted():
    client = ScriptedLLMClient(assess_risks=[_risk()])
    client.parse(
        task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=RiskAssessmentOutput
    )

    with pytest.raises(AssertionError, match="more times than scripted"):
        client.parse(
            task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=RiskAssessmentOutput
        )


def test_fake_rejects_a_wrongly_typed_response():
    """Catches a test that queues the wrong shape for the service under test."""
    client = ScriptedLLMClient(assess_risks=CompanyFactsOutput(facts=[]))

    with pytest.raises(AssertionError, match="asked for RiskAssessmentOutput"):
        client.parse(
            task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=RiskAssessmentOutput
        )


def test_fake_rejects_an_unknown_task_name():
    with pytest.raises(ValueError):
        ScriptedLLMClient(not_a_real_task=_risk())


def test_assert_all_consumed_flags_leftovers():
    client = ScriptedLLMClient(assess_risks=[_risk(), _risk()])
    client.parse(
        task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=RiskAssessmentOutput
    )

    with pytest.raises(AssertionError, match="never consumed"):
        client.assert_all_consumed()


def test_failing_fake_propagates_its_error():
    client = FailingLLMClient(error=LLMError("upstream down"))

    with pytest.raises(LLMError, match="upstream down"):
        client.parse(
            task=LLMTask.ASSESS_RISKS, system="s", user="u", output_format=RiskAssessmentOutput
        )


# --- live smoke test (opt-in) --------------------------------------------------------


@pytest.mark.llm
def test_live_structured_output_round_trip():
    """Confirms credentials and native structured output. Run with `pytest -m llm`."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        from dotenv import load_dotenv

        load_dotenv()

    client = AnthropicLLMClient()
    result = client.parse(
        task=LLMTask.EXTRACT_COMPANY_FACTS,
        system=EXTRACT_COMPANY_FACTS,
        user=(
            "Company context: Raiatea is a fast-growing fashion retailer. Inventory is "
            "highly seasonal and a meaningful share of inventory is more than 12 months old."
        ),
        output_format=CompanyFactsOutput,
    )

    assert isinstance(result, CompanyFactsOutput)
    assert result.facts, "expected at least one fact from a context this specific"
    assert all(f.fact_type and f.value and f.rationale for f in result.facts)


@pytest.mark.llm
def test_live_enum_constraint_is_enforced_by_the_api():
    """The bounded values in SPEC 21 must come back inside the enum, not as free text."""
    client = AnthropicLLMClient()
    result = client.parse(
        task=LLMTask.ASSESS_RISKS,
        system=ASSESS_RISKS,
        user=(
            "Audit area: inventory, GBP 8.9m, 34x materiality, up 43% year on year.\n"
            "Assertion: valuation.\n"
            "Company context: seasonal fashion retailer with meaningful aged stock."
        ),
        output_format=RiskIdentificationOutput,
    )

    assert 1 <= len(result.risks) <= 2
    for risk in result.risks:
        assert risk.likelihood in set(RiskLevel)
        assert risk.magnitude in set(RiskLevel)
        assert risk.risk_description and risk.rationale


@pytest.mark.llm
def test_live_assertion_verdicts_stay_inside_the_candidate_enum():
    client = AnthropicLLMClient()
    result = client.parse(
        task=LLMTask.ASSESS_ASSERTIONS,
        system=ASSESS_ASSERTIONS,
        user=(
            "Audit area: cash, GBP 3.12m, 12x materiality.\n"
            "Candidate assertions: existence, completeness, accuracy, rights_and_obligations.\n"
            "Company context: fast-growing fashion retailer."
        ),
        output_format=AssertionRelevanceOutput,
    )

    assert result.assertions
    assert all(v.assertion in set(Assertion) for v in result.assertions)
