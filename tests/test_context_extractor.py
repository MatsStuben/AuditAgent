"""M4 verification: company fact extraction (SPEC 3.2)."""

import pytest

from src.llm.client import LLMTask
from src.llm.context_extractor import build_user_message, extract_company_facts
from src.llm.prompts import EXTRACT_COMPANY_FACTS
from src.llm.schemas import CompanyFactOutput, CompanyFactsOutput
from tests.fakes import ScriptedLLMClient

RAIATEA_CONTEXT = (
    "Raiatea is a fast-growing fashion retailer. Inventory is highly seasonal and a "
    "meaningful share of inventory is more than 12 months old."
)


def _output(*facts: tuple[str, str, str]) -> CompanyFactsOutput:
    return CompanyFactsOutput(
        facts=[
            CompanyFactOutput(fact_type=ft, value=v, rationale=r) for ft, v, r in facts
        ]
    )


TWO_FACTS = (
    ("inventory_seasonality", "high", "The company describes its inventory as seasonal."),
    ("inventory_ageing", "over 12 months", "A meaningful share is more than 12 months old."),
)


@pytest.fixture
def engagement_with_context(raiatea_engagement):
    raiatea_engagement.company_context = RAIATEA_CONTEXT
    return raiatea_engagement


# --- happy path ----------------------------------------------------------------------


def test_facts_receive_sequential_ids(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=_output(*TWO_FACTS))

    facts = extract_company_facts(engagement_with_context, client=client)

    assert [f.id for f in facts] == ["fact_1", "fact_2"]
    assert [f.fact_type for f in facts] == ["inventory_seasonality", "inventory_ageing"]
    assert [f.value for f in facts] == ["high", "over 12 months"]
    client.assert_all_consumed()


def test_facts_are_attributed_to_the_company_context(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=_output(*TWO_FACTS))

    facts = extract_company_facts(engagement_with_context, client=client)

    assert all(f.source == "company_context" for f in facts)
    assert all(f.rationale for f in facts)


def test_facts_land_on_the_engagement(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=_output(*TWO_FACTS))

    engagement_with_context.company_facts = extract_company_facts(
        engagement_with_context, client=client
    )

    assert [f.id for f in engagement_with_context.company_facts] == ["fact_1", "fact_2"]


def test_extraction_does_not_assign_facts_to_the_engagement(engagement_with_context):
    """Assignment is the caller's, which is what makes replacement explicit."""
    client = ScriptedLLMClient(extract_company_facts=_output(*TWO_FACTS))

    extract_company_facts(engagement_with_context, client=client)

    assert engagement_with_context.company_facts == []
    assert engagement_with_context.company_context == RAIATEA_CONTEXT


def test_extraction_advances_the_id_counter(engagement_with_context):
    """The one piece of engagement state it does touch: allocating unreusable IDs."""
    assert engagement_with_context.id_sequences == {}

    extract_company_facts(
        engagement_with_context, client=ScriptedLLMClient(extract_company_facts=_output(*TWO_FACTS))
    )

    assert engagement_with_context.id_sequences == {"fact": 2}


# --- the model is called correctly ---------------------------------------------------


def test_uses_the_extraction_task_and_prompt(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=_output(*TWO_FACTS))

    extract_company_facts(engagement_with_context, client=client)

    call = client.calls_for(LLMTask.EXTRACT_COMPANY_FACTS)[0]
    assert call.system == EXTRACT_COMPANY_FACTS
    assert call.output_format is CompanyFactsOutput


def test_one_call_per_extraction(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=_output(*TWO_FACTS))

    extract_company_facts(engagement_with_context, client=client)

    assert client.call_count() == 1


def test_user_message_carries_the_context(engagement_with_context):
    message = build_user_message(engagement_with_context)

    assert RAIATEA_CONTEXT in message
    assert "Raiatea Ltd" in message


def test_user_message_excludes_unrelated_engagement_data(engagement_with_context):
    """SPEC 21: only the context relevant to this bounded judgement.

    Financial figures cannot inform what the context text says, and offering them invites
    facts the text does not support.
    """
    message = build_user_message(engagement_with_context)

    assert "8900000" not in message and "8,900,000" not in message
    assert "materiality" not in message.lower()
    assert "inventory" in message.lower()  # only because the context itself mentions it


# --- empty context -------------------------------------------------------------------


def test_empty_context_returns_nothing_without_calling_the_model(raiatea_engagement):
    client = ScriptedLLMClient()

    facts = extract_company_facts(raiatea_engagement, client=client)

    assert facts == []
    assert client.call_count() == 0


def test_whitespace_only_context_is_treated_as_empty(raiatea_engagement):
    raiatea_engagement.company_context = "   \n\t  "
    client = ScriptedLLMClient()

    assert extract_company_facts(raiatea_engagement, client=client) == []
    assert client.call_count() == 0


def test_model_returning_no_facts_is_not_an_error(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=_output())

    assert extract_company_facts(engagement_with_context, client=client) == []


# --- re-extraction -------------------------------------------------------------------


def test_re_extraction_replaces_the_previous_set(engagement_with_context):
    """SPEC 17: editing the context must not accumulate stale facts."""
    client = ScriptedLLMClient(
        extract_company_facts=[
            _output(*TWO_FACTS),
            _output(("industry", "industrial manufacturing", "Stable industrial company.")),
        ]
    )
    engagement_with_context.company_facts = extract_company_facts(
        engagement_with_context, client=client
    )
    assert len(engagement_with_context.company_facts) == 2

    engagement_with_context.company_context = "A stable industrial company."
    engagement_with_context.company_facts = extract_company_facts(
        engagement_with_context, client=client
    )

    facts = engagement_with_context.company_facts
    assert len(facts) == 1
    assert facts[0].fact_type == "industry"


def test_re_extraction_never_reuses_an_id(engagement_with_context):
    """IDs are the traceability contract: a reused ID could let a retained reference —
    an AuditorFeedback snapshot, or an object awaiting an explicit recompute — silently
    resolve to different evidence."""
    client = ScriptedLLMClient(
        extract_company_facts=[
            _output(*TWO_FACTS),
            _output(("industry", "industrial manufacturing", "Stable industrial company.")),
        ]
    )
    first = extract_company_facts(engagement_with_context, client=client)
    first_ids = {f.id for f in first}
    assert first_ids == {"fact_1", "fact_2"}

    engagement_with_context.company_context = "A stable industrial company."
    second = extract_company_facts(engagement_with_context, client=client)

    assert {f.id for f in second}.isdisjoint(first_ids)
    assert second[0].id == "fact_3"  # continues, does not restart


def test_ids_stay_unique_across_many_re_extractions(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=[_output(*TWO_FACTS) for _ in range(4)])

    seen: list[str] = []
    for _ in range(4):
        seen.extend(f.id for f in extract_company_facts(engagement_with_context, client=client))

    assert len(seen) == len(set(seen)) == 8
    assert seen[-1] == "fact_8"


def test_second_extraction_sends_the_edited_context(engagement_with_context):
    client = ScriptedLLMClient(extract_company_facts=[_output(*TWO_FACTS), _output()])
    extract_company_facts(engagement_with_context, client=client)

    engagement_with_context.company_context = "A stable industrial company."
    extract_company_facts(engagement_with_context, client=client)

    assert "stable industrial" in client.last_user_message(LLMTask.EXTRACT_COMPANY_FACTS)
    assert "fashion retailer" not in client.last_user_message(LLMTask.EXTRACT_COMPANY_FACTS)


# --- defensive handling of model output ----------------------------------------------


def test_blank_facts_are_dropped_and_ids_stay_contiguous(engagement_with_context):
    """An unusable fact must not consume an ID, or IDs would gap for no visible reason."""
    client = ScriptedLLMClient(
        extract_company_facts=_output(
            ("inventory_seasonality", "high", "Seasonal."),
            ("", "high", "Blank type."),
            ("industry", "  ", "Blank value."),
            ("inventory_ageing", "over 12 months", "Aged stock."),
        )
    )

    facts = extract_company_facts(engagement_with_context, client=client)

    assert [f.id for f in facts] == ["fact_1", "fact_2"]
    assert [f.fact_type for f in facts] == ["inventory_seasonality", "inventory_ageing"]
    assert engagement_with_context.id_sequences == {"fact": 2}


def test_fact_without_a_rationale_is_dropped(engagement_with_context):
    """A fact cited by ID with no evidence explanation weakens the audit trail more than
    its absence would (SPEC 21 requires a rationale)."""
    client = ScriptedLLMClient(
        extract_company_facts=_output(
            ("inventory_seasonality", "high", "   "),
            ("inventory_ageing", "over 12 months", "Aged stock."),
        )
    )

    facts = extract_company_facts(engagement_with_context, client=client)

    assert [f.fact_type for f in facts] == ["inventory_ageing"]
    assert all(f.rationale for f in facts)


def test_surrounding_whitespace_is_stripped(engagement_with_context):
    client = ScriptedLLMClient(
        extract_company_facts=_output(("  industry  ", "  retail  ", "  Because.  "))
    )

    fact = extract_company_facts(engagement_with_context, client=client)[0]

    assert fact.fact_type == "industry"
    assert fact.value == "retail"
    assert fact.rationale == "Because."


# --- live (opt-in) -------------------------------------------------------------------


@pytest.mark.llm
def test_live_extraction_produces_referenceable_facts(engagement_with_context):
    """The whole M4 path against the real model. Run with `pytest -m llm`."""
    from src.llm.client import AnthropicLLMClient

    facts = extract_company_facts(engagement_with_context, client=AnthropicLLMClient())

    assert facts, "expected facts from a context this specific"
    # The property everything downstream depends on: unique, contiguous, referenceable IDs.
    assert [f.id for f in facts] == [f"fact_{i}" for i in range(1, len(facts) + 1)]
    assert len({f.id for f in facts}) == len(facts)
    assert all(f.fact_type and f.value and f.source == "company_context" for f in facts)
    # The seasonality/ageing signal is what drives the inventory valuation demo.
    assert any("season" in f.fact_type.lower() or "age" in f.fact_type.lower() for f in facts)
