"""M8 verification: pipeline orchestration (SPEC 6, 6.1).

The scripted run this exercises now lives in `conftest.py`, since M9 onwards needs a completed
engagement too.
"""

import pytest

from src.engine.pipeline import load_engagement, run_area, run_pipeline
from src.llm.client import LLMError, LLMTask
from src.models.audit_objects import Assertion, ProcedureSource
from tests.conftest import (
    CASH_RISK,
    INVENTORY_RISK,
    scripted_analysis,
    scripted_facts,
    scripted_selection,
)
from tests.fakes import ScriptedLLMClient

# --- load_engagement -----------------------------------------------------------------


def test_load_engagement_builds_an_unassessed_engagement(static_config):
    engagement = load_engagement(static_config)

    assert engagement.company == "Raiatea Ltd"
    assert engagement.year_end == "2025-12-31"
    assert len(engagement.line_items) == 8
    assert all(item.material is None and item.metrics is None for item in engagement.line_items)
    assert engagement.materiality is None
    assert engagement.company_facts == []


def test_load_engagement_seeds_the_company_context(static_config):
    """SPEC 16: pre-populated, not a blank form."""
    engagement = load_engagement(static_config)

    assert "fashion retailer" in engagement.company_context
    assert "12 months" in engagement.company_context


def test_line_item_ids_are_stable_and_unique(static_config):
    engagement = load_engagement(static_config)

    ids = [item.id for item in engagement.line_items]
    assert len(set(ids)) == 8
    assert engagement.line_item("inventory").id == "li_inventory"


# --- the call budget -----------------------------------------------------------------


def test_a_full_run_costs_exactly_five_calls(engagement, client):
    """SPEC 6.1: 1 extraction + 2 area analyses + 2 procedure selections.

    This is the test that stops the budget regressing toward per-assertion or per-risk calls
    as prompts are tuned.
    """
    assert client.call_count() == 5
    assert client.call_count(LLMTask.EXTRACT_COMPANY_FACTS) == 1
    assert client.call_count(LLMTask.ANALYSE_AUDIT_AREA) == 2
    assert client.call_count(LLMTask.SELECT_PROCEDURES) == 2
    client.assert_all_consumed()


def test_call_count_scales_with_areas_not_assertions_or_risks(engagement, client):
    n_assertions = sum(len(a.assertions) for a in engagement.in_scope_audit_areas)
    n_risks = sum(len(a.all_risks) for a in engagement.in_scope_audit_areas)

    assert n_assertions == 9  # 5 inventory + 4 cash
    assert n_risks == 2
    # Nine assertions still cost two analysis calls, not nine.
    assert client.call_count(LLMTask.ANALYSE_AUDIT_AREA) == len(engagement.in_scope_audit_areas)


def test_no_prompt_spans_more_than_one_audit_area(client, engagement):
    """SPEC 6.1: batching is within an area, never across areas."""
    for call in client.calls:
        if call.task is LLMTask.EXTRACT_COMPANY_FACTS:
            continue
        headers = [
            line for line in call.user.splitlines() if line.startswith("Audit area:")
        ]
        assert len(headers) == 1, f"{call.task} prompt covered {len(headers)} areas"


# --- scoping -------------------------------------------------------------------------


def test_all_eight_line_items_are_scoped(engagement):
    assert len(engagement.line_items) == 8
    for item in engagement.line_items:
        assert item.material is True
        assert item.metrics is not None


def test_only_cash_and_inventory_are_analysed(engagement):
    analysed = [item.line_item_type for item in engagement.line_items if item.assertions]

    assert analysed == ["inventory", "cash"]
    for item in engagement.line_items:
        if item.line_item_type not in {"inventory", "cash"}:
            assert item.assertions == []
            assert item.procedures == []


def test_materiality_is_calculated_before_scoping(engagement):
    assert engagement.materiality.amount == 262_000
    assert engagement.line_item("inventory").metrics.amount_to_materiality_ratio == pytest.approx(
        33.97, abs=0.01
    )


def test_facts_are_extracted_and_referenced(engagement):
    assert [fact.id for fact in engagement.company_facts] == ["fact_1"]

    valuation = next(
        a
        for a in engagement.line_item("inventory").assertions
        if a.assertion is Assertion.VALUATION
    )
    assert valuation.supporting_fact_ids == ["fact_1"]


# --- the resulting object graph ------------------------------------------------------


def test_every_link_resolves(engagement):
    """SPEC 14: the whole chain is explicit IDs, end to end."""
    fact_ids = {fact.id for fact in engagement.company_facts}

    for item in engagement.in_scope_audit_areas:
        risk_ids = {risk.id for risk in item.all_risks}
        assertion_ids = {assertion.id for assertion in item.assertions}

        for assertion in item.assertions:
            assert assertion.line_item_id == item.id
            assert set(assertion.supporting_fact_ids) <= fact_ids
            for risk in assertion.risks:
                assert risk.assertion_id in assertion_ids
                assert set(risk.supporting_fact_ids) <= fact_ids

        for procedure in item.procedures:
            assert procedure.risk_ids
            assert set(procedure.risk_ids) <= risk_ids
        assert item.dangling_risk_ids() == set()


def test_irrelevant_assertions_carry_no_risks(engagement):
    for item in engagement.in_scope_audit_areas:
        for assertion in item.assertions:
            if not assertion.relevant:
                assert assertion.risks == []


def test_every_relevant_assertion_has_at_least_one_risk(engagement):
    relevant = [
        assertion
        for item in engagement.in_scope_audit_areas
        for assertion in item.assertions
        if assertion.relevant
    ]

    assert relevant
    assert all(assertion.risks for assertion in relevant)


def test_every_risk_has_a_responding_procedure(engagement):
    for item in engagement.in_scope_audit_areas:
        for risk in item.all_risks:
            assert item.procedures_for(risk.id), f"{risk.id} has no procedure"


def test_procedures_come_from_the_catalogue(engagement):
    procedures = [p for item in engagement.in_scope_audit_areas for p in item.procedures]

    assert [p.procedure_id for p in procedures] == [
        "INV_SUBSEQUENT_SALES",
        "CASH_BANK_CONFIRMATION",
    ]
    assert all(p.source is ProcedureSource.CATALOGUE for p in procedures)
    assert all(p.isa_refs == ["ISA330.6_7"] for p in procedures)


def test_isa_references_are_present_at_every_level(engagement):
    inventory = engagement.line_item("inventory")
    assertion = next(a for a in inventory.assertions if a.relevant)

    assert assertion.isa_refs == ["ISA315.29"]
    assert assertion.risks[0].isa_refs == ["ISA315.28b_31"]
    assert inventory.procedures[0].isa_refs == ["ISA330.6_7"]


def test_ids_are_unique_across_the_whole_engagement(engagement):
    ids = [fact.id for fact in engagement.company_facts]
    for item in engagement.line_items:
        ids.append(item.id)
        ids.extend(a.id for a in item.assertions)
        ids.extend(r.id for r in item.all_risks)
        ids.extend(p.id for p in item.procedures)

    assert len(ids) == len(set(ids))


# --- run_area ------------------------------------------------------------------------


class _FailsOnSelection:
    """Analysis succeeds, then the selection call fails — the risky window in `run_area`."""

    def __init__(self, analysis):
        self._analysis = analysis

    def parse(self, *, task, system, user, output_format):
        if task is LLMTask.SELECT_PROCEDURES:
            raise LLMError("API unavailable")
        return self._analysis


def test_run_area_clears_procedures_before_re_analysing(static_config, client):
    """A failure between the two calls must not leave procedures naming replaced risks.

    Re-analysis assigns new risk IDs, so procedures from the previous run become dangling
    the moment it succeeds. Clearing first means a failed selection leaves the area with no
    procedures rather than wrong ones.
    """
    engagement = run_pipeline(load_engagement(static_config), client=client, config=static_config)
    inventory = engagement.line_item("inventory")
    assert inventory.procedures
    assert inventory.dangling_risk_ids() == set()

    broken = _FailsOnSelection(
        scripted_analysis(Assertion.EXISTENCE, static_config.candidate_assertions("inventory"))
    )
    with pytest.raises(LLMError):
        run_area(inventory, engagement, client=broken, config=static_config)

    assert inventory.procedures == []
    assert inventory.dangling_risk_ids() == set()
    assert inventory.assertions  # the analysis that succeeded is kept


def test_run_area_replaces_rather_than_appends(static_config, client):
    engagement = run_pipeline(load_engagement(static_config), client=client, config=static_config)
    inventory = engagement.line_item("inventory")
    before = len(inventory.assertions)

    rerun = ScriptedLLMClient(
        analyse_audit_area=scripted_analysis(
            Assertion.EXISTENCE, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_PHYSICAL_COUNT", "risk_3"),
    )
    run_area(inventory, engagement, client=rerun, config=static_config)

    assert len(inventory.assertions) == before  # replaced, not doubled
    assert [p.procedure_id for p in inventory.procedures] == ["INV_PHYSICAL_COUNT"]
    assert inventory.dangling_risk_ids() == set()


# --- areas leaving scope -------------------------------------------------------------


def test_area_that_becomes_immaterial_is_cleared_on_rerun(static_config, client):
    """SPEC 17: a financial-data change rescopes, and work on a descoped area must go.

    Cash (3.12m) is material at 262k. Raising turnover to 700m with a 2.9% margin puts
    materiality at 3.5m, which leaves cash below the threshold while inventory (8.9m) stays
    above it — so exactly one area leaves scope.
    """
    engagement = run_pipeline(load_engagement(static_config), client=client, config=static_config)
    cash = engagement.line_item("cash")
    assert cash.assertions and cash.procedures

    engagement.line_item("turnover").cy = 700_000_000
    engagement.line_item("profit_before_tax").cy = 20_000_000

    rerun = ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=scripted_analysis(
            Assertion.VALUATION, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
    )
    run_pipeline(engagement, client=rerun, config=static_config)

    assert engagement.materiality.amount == 3_500_000
    assert cash.material is False
    # The old subtree is gone, not merely skipped.
    assert cash.assertions == []
    assert cash.procedures == []
    # And no call was spent reassessing an area that is out of scope.
    assert rerun.call_count() == 3  # extraction + inventory's two
    rerun.assert_all_consumed()


def test_the_remaining_area_is_still_analysed_after_a_rescope(static_config, client):
    """The positive control: clearing must not descope areas that are still in scope."""
    engagement = run_pipeline(load_engagement(static_config), client=client, config=static_config)
    engagement.line_item("turnover").cy = 700_000_000
    engagement.line_item("profit_before_tax").cy = 20_000_000

    rerun = ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=scripted_analysis(
            Assertion.VALUATION, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
    )
    run_pipeline(engagement, client=rerun, config=static_config)

    inventory = engagement.line_item("inventory")
    assert inventory.material is True
    assert inventory.assertions
    assert inventory.procedures
    assert inventory.dangling_risk_ids() == set()


def test_clearing_leaves_scoping_metrics_intact(static_config, client):
    """A descoped area is still shown in the UI, so its metrics must survive (SPEC 3.3)."""
    engagement = run_pipeline(load_engagement(static_config), client=client, config=static_config)
    engagement.line_item("turnover").cy = 700_000_000
    engagement.line_item("profit_before_tax").cy = 20_000_000

    rerun = ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=scripted_analysis(
            Assertion.VALUATION, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
    )
    run_pipeline(engagement, client=rerun, config=static_config)

    cash = engagement.line_item("cash")
    assert cash.metrics is not None
    assert cash.is_audit_area is True  # still implemented, just out of scope
    assert cash.material is False


def test_non_audit_areas_never_accumulate_work(engagement):
    for item in engagement.line_items:
        if not item.is_audit_area:
            assert item.assertions == []
            assert item.procedures == []


# --- pipeline shape ------------------------------------------------------------------


def test_run_pipeline_returns_the_same_engagement(static_config, client):
    engagement = load_engagement(static_config)

    assert run_pipeline(engagement, client=client, config=static_config) is engagement


def test_pipeline_without_context_makes_no_extraction_call(static_config):
    engagement = load_engagement(static_config)
    engagement.company_context = ""
    client = ScriptedLLMClient(
        analyse_audit_area=[
            scripted_analysis(Assertion.VALUATION, static_config.candidate_assertions("inventory")),
            scripted_analysis(Assertion.EXISTENCE, static_config.candidate_assertions("cash")),
        ],
        select_procedures=[
            scripted_selection("INV_SUBSEQUENT_SALES", INVENTORY_RISK),
            scripted_selection("CASH_BANK_CONFIRMATION", CASH_RISK),
        ],
    )

    run_pipeline(engagement, client=client, config=static_config)

    assert client.call_count() == 4  # no extraction
    assert engagement.company_facts == []
