"""M1 verification: runtime domain models and their relationships (SPEC 4)."""

import pytest
from pydantic import ValidationError

from src.models.audit_objects import (
    AI_SUGGESTION_LABEL,
    Assertion,
    AssertionAssessment,
    EvidenceStrength,
    Procedure,
    ProcedureSource,
    RiskAssessment,
    RiskLevel,
)
from src.models.engagement import (
    AuditEngagement,
    CompanyFact,
    DerivedMetrics,
    FinancialLineItemAssessment,
    Materiality,
)
from src.models.feedback import AuditorFeedback, RuleProposal, RuleProposalStatus


def _procedure(**overrides) -> Procedure:
    defaults = dict(
        id="proc_1",
        risk_ids=["risk_1"],
        procedure_id="INV_SUBSEQUENT_SALES",
        name="Test post-year-end sales",
        description="...",
        procedure_type="test_of_details",
        evidence_strength=EvidenceStrength.HIGH,
        rationale="Addresses aged inventory overstatement.",
        isa_refs=["ISA330.6_7"],
    )
    return Procedure(**{**defaults, **overrides})


def _risk(**overrides) -> RiskAssessment:
    defaults = dict(
        id="risk_1",
        assertion_id="assertion_1",
        risk_description="Inventory may be carried above recoverable value.",
        likelihood=RiskLevel.HIGH,
        magnitude=RiskLevel.HIGH,
        system_rating=RiskLevel.HIGH,
        final_rating=RiskLevel.HIGH,
        rationale="Aged seasonal stock.",
        isa_refs=["ISA315.28b_31"],
    )
    return RiskAssessment(**{**defaults, **overrides})


# --- bounded values ------------------------------------------------------------------


def test_risk_levels_are_bounded():
    with pytest.raises(ValidationError):
        _risk(likelihood="severe")


def test_assertion_is_bounded():
    with pytest.raises(ValidationError):
        AssertionAssessment(
            id="a1", line_item_id="li_1", assertion="cutoff", relevant=True, rationale="x"
        )


def test_procedure_type_is_free_form():
    """Kept as str so new catalogue entries never require a code change (SPEC 12)."""
    assert _procedure(procedure_type="a_brand_new_technique").procedure_type


# --- override semantics --------------------------------------------------------------


def test_risk_defaults_to_not_overridden():
    risk = _risk()
    assert risk.system_rating is risk.final_rating
    assert risk.is_overridden is False
    assert risk.override_reason is None


def test_override_preserves_the_system_rating():
    """SPEC 11/18: the original system conclusion must survive the override."""
    risk = _risk()
    risk.final_rating = RiskLevel.LOW
    risk.is_overridden = True
    risk.override_reason = "Inventory is contractually pre-sold."

    assert risk.system_rating is RiskLevel.HIGH
    assert risk.final_rating is RiskLevel.LOW
    assert risk.likelihood is RiskLevel.HIGH and risk.magnitude is RiskLevel.HIGH


# --- AI suggestions ------------------------------------------------------------------


def test_catalogue_procedure_needs_no_approval():
    proc = _procedure()
    assert proc.source is ProcedureSource.CATALOGUE
    assert proc.requires_approval is False


def test_ai_suggestion_requires_approval():
    proc = _procedure(
        procedure_id=None, source=ProcedureSource.AI_SUGGESTION, approved=False
    )
    assert proc.requires_approval is True
    proc.approved = True
    assert proc.requires_approval is False
    assert "AUDITOR APPROVAL REQUIRED" in AI_SUGGESTION_LABEL


# --- relationships and traceability --------------------------------------------------


def _inventory_area(*, risks=(), procedures=()) -> FinancialLineItemAssessment:
    assertion = AssertionAssessment(
        id="assertion_1",
        line_item_id="li_inventory",
        assertion=Assertion.VALUATION,
        relevant=True,
        rationale="Seasonal inventory creates obsolescence risk.",
        supporting_fact_ids=["fact_1"],
        isa_refs=["ISA315.29"],
        risks=list(risks),
    )
    return FinancialLineItemAssessment(
        id="li_inventory", line_item_type="inventory", cy=8_900_000, py=6_200_000,
        material=True, is_audit_area=True, assertions=[assertion], procedures=list(procedures),
    )


def test_object_graph_links_by_id():
    """SPEC 14: relationships are explicit IDs, not inferred from free text."""
    proc = _procedure()
    risk = _risk()
    item = _inventory_area(risks=[risk], procedures=[proc])
    assertion = item.assertions[0]

    assert proc.risk_ids == [risk.id]
    assert risk.assertion_id == assertion.id
    assert assertion.line_item_id == item.id
    # Full ISA chain reachable from the procedure upwards.
    assert proc.isa_refs == ["ISA330.6_7"]
    assert risk.isa_refs == ["ISA315.28b_31"]
    assert assertion.isa_refs == ["ISA315.29"]


# --- procedure/risk relationships ----------------------------------------------------


def test_one_procedure_can_address_several_risks_without_duplication():
    """SPEC 4/13: the relationship is explicit, not modelled by copying the procedure."""
    shrinkage = _risk(id="risk_1")
    receipting = _risk(id="risk_2")
    count = _procedure(id="proc_1", risk_ids=["risk_1", "risk_2"])
    item = _inventory_area(risks=[shrinkage, receipting], procedures=[count])

    # One object, reachable from both risks.
    assert len(item.procedures) == 1
    assert item.procedures_for("risk_1") == [count]
    assert item.procedures_for("risk_2") == [count]
    assert item.procedures_for("risk_1")[0] is item.procedures_for("risk_2")[0]


def test_procedures_for_returns_nothing_for_an_uncovered_risk():
    item = _inventory_area(
        risks=[_risk(id="risk_1"), _risk(id="risk_2")],
        procedures=[_procedure(risk_ids=["risk_1"])],
    )

    assert item.procedures_for("risk_2") == []


def test_procedure_must_address_at_least_one_risk():
    """A procedure answering nothing would break the SPEC 14 chain."""
    with pytest.raises(ValidationError):
        _procedure(risk_ids=[])


def test_all_risks_flattens_across_assertions():
    item = _inventory_area(risks=[_risk(id="risk_1"), _risk(id="risk_2")])

    assert [r.id for r in item.all_risks] == ["risk_1", "risk_2"]
    assert item.risk("risk_2").id == "risk_2"
    assert item.risk("risk_99") is None


def test_dangling_risk_ids_are_detectable():
    """Re-analysing an area replaces its risks, so stale procedures must be visible."""
    item = _inventory_area(
        risks=[_risk(id="risk_1")],
        procedures=[_procedure(risk_ids=["risk_1", "risk_stale"])],
    )

    assert item.dangling_risk_ids() == {"risk_stale"}


def test_no_dangling_ids_when_procedures_match_risks():
    item = _inventory_area(
        risks=[_risk(id="risk_1"), _risk(id="risk_2")],
        procedures=[_procedure(risk_ids=["risk_1", "risk_2"])],
    )

    assert item.dangling_risk_ids() == set()


def _engagement(*items: FinancialLineItemAssessment) -> AuditEngagement:
    return AuditEngagement(
        company="Raiatea Ltd", year_end="2025-12-31", line_items=list(items)
    )


def test_engagement_lookup():
    engagement = _engagement(
        FinancialLineItemAssessment(
            id="li_inventory", line_item_type="inventory", cy=8_900_000, py=6_200_000,
            material=True, is_audit_area=True,
        ),
        FinancialLineItemAssessment(
            id="li_turnover", line_item_type="turnover", cy=52_400_000, py=47_100_000,
            material=True, is_audit_area=False,
        ),
    )

    assert engagement.line_item("inventory").cy == 8_900_000
    assert engagement.line_item("nonexistent") is None
    # Material but not an audit area — a displayed state, not a gap (SPEC 15).
    assert engagement.line_item("turnover").material is True


def test_implemented_vs_in_scope_audit_areas():
    """Pipeline scope is material AND implemented; the two properties must not conflate."""
    engagement = _engagement(
        FinancialLineItemAssessment(
            id="li_inventory", line_item_type="inventory", cy=8_900_000, py=6_200_000,
            material=True, is_audit_area=True,
        ),
        FinancialLineItemAssessment(
            id="li_cash", line_item_type="cash", cy=1_000, py=900,
            material=False, is_audit_area=True,
        ),
        FinancialLineItemAssessment(
            id="li_turnover", line_item_type="turnover", cy=52_400_000, py=47_100_000,
            material=True, is_audit_area=False,
        ),
    )

    assert [li.line_item_type for li in engagement.implemented_audit_areas] == [
        "inventory", "cash",
    ]
    # Immaterial cash is implemented but out of scope; material turnover is not implemented.
    assert [li.line_item_type for li in engagement.in_scope_audit_areas] == ["inventory"]


def test_in_scope_audit_areas_is_empty_before_scoping():
    """`material` is None until M2 runs; that must not read as in-scope."""
    engagement = _engagement(
        FinancialLineItemAssessment(
            id="li_inventory", line_item_type="inventory", cy=8_900_000, py=6_200_000,
            is_audit_area=True,
        ),
    )

    assert engagement.implemented_audit_areas != []
    assert engagement.in_scope_audit_areas == []


def test_next_id_is_monotonic_per_prefix():
    """IDs are the traceability contract (SPEC 14) and must never be reused."""
    engagement = _engagement()

    assert [engagement.next_id("fact") for _ in range(3)] == ["fact_1", "fact_2", "fact_3"]
    # Prefixes are independent counters.
    assert engagement.next_id("risk") == "risk_1"
    assert engagement.next_id("fact") == "fact_4"


def test_next_id_does_not_reset_when_a_collection_is_replaced():
    engagement = _engagement()
    engagement.company_facts = [
        CompanyFact(id=engagement.next_id("fact"), fact_type="a", value="b", rationale="c")
    ]

    engagement.company_facts = []  # replaced, e.g. after a context edit

    assert engagement.next_id("fact") == "fact_2"


def test_id_counters_are_per_engagement():
    first, second = _engagement(), _engagement()
    first.next_id("fact")
    first.next_id("fact")

    assert second.next_id("fact") == "fact_1"


def test_new_line_item_starts_unassessed():
    item = FinancialLineItemAssessment(
        id="li_cash", line_item_type="cash", cy=3_120_000, py=2_890_000
    )
    assert item.material is None  # not yet scoped, distinct from "not material"
    assert item.metrics is None
    assert item.is_audit_area is False
    assert item.assertions == []


def test_metrics_allow_absent_percentage_when_py_is_zero():
    metrics = DerivedMetrics(yoy_change=100.0, yoy_change_pct=None, amount_to_materiality_ratio=2.0)
    assert metrics.yoy_change_pct is None


def test_materiality_carries_the_prototype_label():
    materiality = Materiality(
        amount=262_000, benchmark="profit_before_tax", rate=0.05,
        basis="PBT/turnover 10% > 5%, so 5% of profit before tax.",
    )
    assert "not an ISA-prescribed formula" in materiality.label


# --- feedback ------------------------------------------------------------------------


def test_feedback_preserves_before_and_after():
    feedback = AuditorFeedback(
        id="feedback_1",
        object_type="risk_assessment",
        object_id="risk_1",
        before={"final_rating": "high"},
        after={"final_rating": "low"},
        reason="Inventory is contractually pre-sold.",
    )
    assert feedback.before["final_rating"] == "high"
    assert feedback.after["final_rating"] == "low"


def test_rule_proposal_starts_pending():
    proposal = RuleProposal(
        id="rule_1",
        condition="revenue < 10m AND inventory valuation risk != high",
        action="do not require procedure X",
        reason="...",
        source_feedback_id="feedback_1",
    )
    assert proposal.status is RuleProposalStatus.PENDING_REVIEW


def test_company_fact_defaults_to_company_context_source():
    fact = CompanyFact(
        id="fact_1", fact_type="inventory_seasonality", value="high",
        rationale="The company describes its inventory as highly seasonal.",
    )
    assert fact.source == "company_context"
