"""M9 verification: forward traceability (SPEC 14)."""

import pytest

from src.engine.pipeline import load_engagement
from src.engine.traceability import TraceabilityError, trace_procedure
from src.models.audit_objects import Assertion, Procedure
from src.models.engagement import CompanyFact
from tests.conftest import INVENTORY_RISK


def inventory_procedure(engagement) -> Procedure:
    (procedure,) = engagement.line_item("inventory").procedures
    return procedure


# --- the SPEC 14 chain, end to end ------------------------------------------------------


def test_chain_reaches_every_rung(engagement):
    """The SPEC 14 example, on the scripted run: test post-year-end sales → valuation →
    inventory → the aged-stock fact and the area's metrics → the ISA chain."""
    (chain,) = trace_procedure(inventory_procedure(engagement), engagement)

    assert chain.procedure.procedure_id == "INV_SUBSEQUENT_SALES"
    assert chain.risk.id == INVENTORY_RISK
    assert chain.assertion.assertion is Assertion.VALUATION
    assert chain.line_item.line_item_type == "inventory"
    assert [f.fact_type for f in chain.facts] == ["inventory_ageing"]
    assert chain.metrics is not None
    assert chain.metrics.amount_to_materiality_ratio == pytest.approx(33.97, abs=0.01)


def test_isa_chain_runs_relevance_then_risk_then_response(engagement):
    """SPEC 14's ISA rung, in order and deduplicated."""
    (chain,) = trace_procedure(inventory_procedure(engagement), engagement)

    assert chain.isa_chain == ["ISA315.29", "ISA315.28b_31", "ISA330.6_7"]


def test_the_chain_holds_the_live_objects_not_copies(engagement):
    """A UI reading `chain.risk.final_rating` after an M11 override must see the override.

    Identity, not equality: a copy would compare equal today and go stale the moment the
    auditor changed anything.
    """
    line_item = engagement.line_item("inventory")
    (chain,) = trace_procedure(inventory_procedure(engagement), engagement)

    assert chain.line_item is line_item
    assert chain.risk is line_item.risk(INVENTORY_RISK)
    assert chain.procedure is line_item.procedures[0]


def test_a_stale_copy_cannot_alter_the_trace(two_risk_engagement):
    """The argument identifies the procedure; the engagement supplies its links.

    A caller can easily hold a deserialised or edited procedure with the same ID — the UI
    round-trips session state, and M11 lets an auditor change procedures. Tracing the
    caller's version would report a chain the audit file does not contain.
    """
    stored = inventory_procedure(two_risk_engagement)
    stale = stored.model_copy(update={"risk_ids": ["risk_1"], "isa_refs": ["ISA999"]})

    chains = trace_procedure(stale, two_risk_engagement)

    assert [c.risk.id for c in chains] == ["risk_1", "risk_2"]
    assert all(c.procedure is stored for c in chains)
    assert all("ISA999" not in c.isa_chain for c in chains)


def test_links_are_resolved_by_id_not_by_position(engagement):
    """Every rung is reachable from the one below via its stored ID (SPEC 14)."""
    (chain,) = trace_procedure(inventory_procedure(engagement), engagement)

    assert chain.risk.id in chain.procedure.risk_ids
    assert chain.risk.assertion_id == chain.assertion.id
    assert chain.assertion.line_item_id == chain.line_item.id


def test_tracing_is_read_only(engagement):
    before = engagement.model_dump()
    trace_procedure(inventory_procedure(engagement), engagement)

    assert engagement.model_dump() == before


# --- fan-out: one procedure, several risks -----------------------------------------------


def test_one_procedure_two_risks_yields_two_chains(two_risk_engagement):
    procedure = inventory_procedure(two_risk_engagement)
    assert procedure.risk_ids == ["risk_1", "risk_2"]

    chains = trace_procedure(procedure, two_risk_engagement)

    assert [c.risk.id for c in chains] == ["risk_1", "risk_2"]


def test_fanned_out_chains_are_complete_and_differ_only_above_the_risk(two_risk_engagement):
    """The fan-out is a property of the audit, not a gap (SPEC 14): each chain stands alone.

    Here both risks sit on the same assertion, so only the risk rung differs — but each chain
    must still carry every rung rather than sharing one partial record.
    """
    first, second = trace_procedure(
        inventory_procedure(two_risk_engagement), two_risk_engagement
    )

    assert first.risk is not second.risk
    assert first.risk.risk_description != second.risk.risk_description
    assert first.assertion is second.assertion
    assert first.line_item is second.line_item
    assert first.procedure is second.procedure
    for chain in (first, second):
        assert chain.facts and chain.isa_chain and chain.metrics is not None


# --- broken links raise rather than returning a partial chain ----------------------------


def test_a_dangling_risk_id_raises(engagement):
    """A partial chain would present an untraceable procedure as traceable."""
    procedure = inventory_procedure(engagement)
    procedure.risk_ids = ["risk_99"]

    with pytest.raises(TraceabilityError, match="risk_99"):
        trace_procedure(procedure, engagement)


def test_a_dangling_risk_id_raises_even_when_other_risks_resolve(two_risk_engagement):
    """Returning the chains that happen to work would hide the broken one."""
    procedure = inventory_procedure(two_risk_engagement)
    procedure.risk_ids = ["risk_1", "risk_99"]

    with pytest.raises(TraceabilityError, match="risk_99"):
        trace_procedure(procedure, two_risk_engagement)


def test_a_procedure_from_another_engagement_raises(engagement, static_config):
    other = load_engagement(static_config)

    with pytest.raises(TraceabilityError, match="does not belong"):
        trace_procedure(inventory_procedure(engagement), other)


def test_a_dangling_assertion_id_raises(engagement):
    line_item = engagement.line_item("inventory")
    line_item.risk(INVENTORY_RISK).assertion_id = "assertion_99"

    with pytest.raises(TraceabilityError, match="assertion_99"):
        trace_procedure(inventory_procedure(engagement), engagement)


def test_a_dangling_fact_id_raises(engagement):
    """The services validate fact IDs before storing them, so one dangling here means state
    was corrupted afterwards — dropping it would quietly lose the cited evidence."""
    engagement.line_item("inventory").risk(INVENTORY_RISK).supporting_fact_ids = ["fact_99"]

    with pytest.raises(TraceabilityError, match="fact_99"):
        trace_procedure(inventory_procedure(engagement), engagement)


# --- evidence rung ------------------------------------------------------------------------


def test_facts_run_assertion_first_then_risk_deduplicated(engagement):
    line_item = engagement.line_item("inventory")
    engagement.company_facts.append(
        CompanyFact(id="fact_2", fact_type="growth", value="fast", rationale="Stated.")
    )
    assertion = next(a for a in line_item.assertions if a.assertion is Assertion.VALUATION)
    assertion.supporting_fact_ids = ["fact_2", "fact_1"]
    line_item.risk(INVENTORY_RISK).supporting_fact_ids = ["fact_1"]

    (chain,) = trace_procedure(inventory_procedure(engagement), engagement)

    assert [f.id for f in chain.facts] == ["fact_2", "fact_1"]


def test_a_chain_with_no_cited_facts_is_legitimate(engagement):
    """Not every judgement rests on an extracted fact; an empty evidence rung is not a break."""
    line_item = engagement.line_item("inventory")
    for assertion in line_item.assertions:
        assertion.supporting_fact_ids = []
    line_item.risk(INVENTORY_RISK).supporting_fact_ids = []

    (chain,) = trace_procedure(inventory_procedure(engagement), engagement)

    assert chain.facts == []
    assert chain.isa_chain == ["ISA315.29", "ISA315.28b_31", "ISA330.6_7"]
