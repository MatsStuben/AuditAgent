"""M10 verification: reverse ISA coverage (SPEC 15)."""

import pytest

from src.engine import coverage as coverage_module
from src.engine.coverage import (
    NOT_IMPLEMENTED_LABEL,
    CoverageError,
    check_isa_coverage,
)
from src.engine.pipeline import load_engagement, run_pipeline
from src.models.audit_objects import Assertion, Procedure, ProcedureSource
from src.models.isa import ISARequirement, LinkedObjectType
from tests.conftest import CASH_RISK, INVENTORY_RISK

ASSERTION_REQ = "ISA315.29"
RISK_REQ = "ISA315.28b_31"
PROCEDURE_REQ = "ISA330.6_7"


def coverage(engagement, static_config):
    return check_isa_coverage(engagement, static_config)


# --- the completed run is clean -----------------------------------------------------------


def test_a_complete_engagement_reports_no_gaps(engagement, static_config):
    report = coverage(engagement, static_config)

    assert report.gaps == []
    assert report.satisfied


def test_every_requirement_is_evaluated(engagement, static_config):
    report = coverage(engagement, static_config)

    assert [c.requirement.id for c in report.requirements] == [
        ASSERTION_REQ,
        RISK_REQ,
        PROCEDURE_REQ,
    ]
    assert all(c.satisfied for c in report.requirements)


def test_requirements_list_the_objects_addressing_them(engagement, static_config):
    """The reverse of SPEC 14: requirement → which audit objects address it."""
    report = coverage(engagement, static_config)
    inventory = engagement.line_item("inventory")

    assertions = report.for_requirement(ASSERTION_REQ).addressed_by
    assert {a.id for a in inventory.assertions} <= set(assertions)

    assert set(report.for_requirement(RISK_REQ).addressed_by) == {INVENTORY_RISK, CASH_RISK}
    assert set(report.for_requirement(PROCEDURE_REQ).addressed_by) == {
        p.id for area in engagement.in_scope_audit_areas for p in area.procedures
    }


# --- scope: audit areas only ---------------------------------------------------------------


def test_material_line_items_without_methodology_are_not_gaps(engagement, static_config):
    """The decision this feature turns on (SPEC 15, Coverage scope).

    All eight Raiatea line items are material; six have no implemented methodology. Reporting
    those as ISA 315.29 gaps would produce six false gaps and bury the one real one.
    """
    report = coverage(engagement, static_config)

    excluded = {e.line_item_type for e in report.not_implemented}
    assert excluded == {
        "turnover",
        "profit_before_tax",
        "trade_debtors",
        "trade_creditors",
        "property_plant_equipment",
        "net_assets",
    }
    assert all(e.label == NOT_IMPLEMENTED_LABEL for e in report.not_implemented)
    assert report.for_requirement(ASSERTION_REQ).gaps == []


def test_an_immaterial_line_item_is_neither_a_gap_nor_an_exclusion(engagement, static_config):
    """Not material means no work was expected, so there is nothing to report either way."""
    turnover = engagement.line_item("turnover")
    turnover.material = False

    report = coverage(engagement, static_config)

    assert "turnover" not in {e.line_item_type for e in report.not_implemented}
    assert "turnover" not in {g.line_item_type for g in report.gaps}


def test_an_immaterial_audit_area_is_out_of_coverage_scope(engagement, static_config):
    """Coverage follows pipeline scope: material *and* implemented (SPEC 6, 8).

    A descoped area is cleared by the pipeline, so evaluating it would report gaps for work
    the engagement deliberately dropped.
    """
    inventory = engagement.line_item("inventory")
    inventory.material = False
    inventory.assertions = []
    inventory.procedures = []

    report = coverage(engagement, static_config)

    assert report.gaps == []
    assert "inventory" not in {e.line_item_type for e in report.not_implemented}


# --- ISA 315.29 -----------------------------------------------------------------------------


def test_a_material_audit_area_with_no_assertions_is_a_gap(engagement, static_config):
    inventory = engagement.line_item("inventory")
    inventory.assertions = []
    inventory.procedures = []

    report = coverage(engagement, static_config)

    (gap,) = report.for_requirement(ASSERTION_REQ).gaps
    assert gap.object_id == inventory.id
    assert gap.line_item_type == "inventory"
    assert report.for_requirement(RISK_REQ).satisfied
    assert report.for_requirement(PROCEDURE_REQ).satisfied


# --- ISA 315.28(b)/31 ------------------------------------------------------------------------


def test_a_relevant_assertion_with_no_risks_is_a_gap(engagement, static_config):
    inventory = engagement.line_item("inventory")
    valuation = next(a for a in inventory.assertions if a.assertion is Assertion.VALUATION)
    valuation.risks = []
    inventory.procedures = []

    report = coverage(engagement, static_config)

    (gap,) = report.for_requirement(RISK_REQ).gaps
    assert gap.object_id == valuation.id
    assert "valuation" in gap.description
    assert report.for_requirement(ASSERTION_REQ).satisfied


def test_an_irrelevant_assertion_with_no_risks_is_not_a_gap(engagement, static_config):
    """SPEC 10 requires a non-relevant assertion to carry no risks. Reporting that as a gap
    would penalise the engine for reaching a conclusion."""
    inventory = engagement.line_item("inventory")
    assert any(not a.relevant and not a.risks for a in inventory.assertions)

    report = coverage(engagement, static_config)

    assert report.for_requirement(RISK_REQ).satisfied


# --- ISA 330.6/7 ------------------------------------------------------------------------------


def test_a_risk_with_no_procedure_is_a_gap(engagement, static_config):
    inventory = engagement.line_item("inventory")
    inventory.procedures = []

    report = coverage(engagement, static_config)

    (gap,) = report.for_requirement(PROCEDURE_REQ).gaps
    assert gap.object_id == INVENTORY_RISK
    assert gap.line_item_type == "inventory"
    assert report.for_requirement(ASSERTION_REQ).satisfied
    assert report.for_requirement(RISK_REQ).satisfied


def test_dropping_one_risk_from_a_shared_procedure_gaps_only_that_risk(
    two_risk_engagement, static_config
):
    """The check that catches coverage reading procedures as if each answered one risk.

    `procedures_for` resolves the link both ways, so removing one ID from a procedure covering
    two risks must gap exactly that risk and leave the other answered by the same procedure.
    """
    inventory = two_risk_engagement.line_item("inventory")
    (procedure,) = inventory.procedures
    assert procedure.risk_ids == ["risk_1", "risk_2"]
    procedure.risk_ids = ["risk_1"]

    report = check_isa_coverage(two_risk_engagement, static_config)

    (gap,) = report.for_requirement(PROCEDURE_REQ).gaps
    assert gap.object_id == "risk_2"
    assert procedure.id in report.for_requirement(PROCEDURE_REQ).addressed_by
    assert report.for_requirement(ASSERTION_REQ).satisfied
    assert report.for_requirement(RISK_REQ).satisfied


def test_a_shared_procedure_is_listed_once(two_risk_engagement, static_config):
    """One procedure answering two risks addresses ISA 330.6/7 once, not twice."""
    report = check_isa_coverage(two_risk_engagement, static_config)
    (procedure,) = two_risk_engagement.line_item("inventory").procedures

    addressed = report.for_requirement(PROCEDURE_REQ).addressed_by
    assert addressed.count(procedure.id) == 1


def test_an_unapproved_suggestion_does_not_close_the_gap(engagement, static_config):
    """SPEC 13: a suggestion "will not be used without" approval, so it is not yet a response.

    It still appears in `addressed_by`, so the panel can show a proposed response is waiting on
    a decision rather than showing nothing at all.
    """
    inventory = engagement.line_item("inventory")
    suggestion = Procedure(
        id="proc_99",
        risk_ids=[INVENTORY_RISK],
        name="Suggested procedure",
        description="Something the catalogue does not cover.",
        procedure_type="ai_suggested",
        rationale="Because.",
        source=ProcedureSource.AI_SUGGESTION,
        approved=False,
        isa_refs=[PROCEDURE_REQ],  # as the selector assigns them; only approval is missing
    )
    inventory.procedures = [suggestion]

    report = coverage(engagement, static_config)

    (gap,) = report.for_requirement(PROCEDURE_REQ).gaps
    assert gap.object_id == INVENTORY_RISK
    assert "approval" in gap.description
    assert "proc_99" in report.for_requirement(PROCEDURE_REQ).addressed_by


def test_an_approved_suggestion_closes_the_gap(engagement, static_config):
    inventory = engagement.line_item("inventory")
    (catalogue_procedure,) = inventory.procedures
    inventory.procedures = [
        catalogue_procedure.model_copy(
            update={"source": ProcedureSource.AI_SUGGESTION, "approved": True}
        )
    ]

    report = coverage(engagement, static_config)

    assert report.for_requirement(PROCEDURE_REQ).satisfied


# --- dispatch ---------------------------------------------------------------------------------


def extended_config(static_config):
    """Config with a fourth requirement, satisfied by an object type that already exists."""
    extra = ISARequirement(
        id="ISA330.18",
        standard="ISA 330",
        paragraphs=["18"],
        purpose="Substantive procedures for material classes of transactions.",
        linked_object_type=LinkedObjectType.PROCEDURE,
    )
    return static_config.model_copy(
        update={"isa_requirements": [*static_config.isa_requirements, extra]}
    )


def test_adding_a_requirement_does_not_retroactively_cover_existing_work(
    engagement, static_config
):
    """Config changing cannot make an existing engagement compliant.

    The procedures in this engagement were created before ISA330.18 existed and record no
    reference to it. Counting them because their *type* matches would report the firm as
    covered on a requirement nothing in the file has ever addressed.
    """
    report = check_isa_coverage(engagement, extended_config(static_config))

    added = report.for_requirement("ISA330.18")
    assert not added.satisfied
    assert added.addressed_by == []
    assert all("none recording ISA330.18" in gap.description for gap in added.gaps)
    assert report.for_requirement(PROCEDURE_REQ).satisfied


def test_re_running_the_pipeline_attaches_the_new_requirement(static_config, client):
    """The other half: a fresh run picks the reference up through `isa_refs_for`, so a new
    requirement is covered by re-running the audit, not by editing config (SPEC 4)."""
    config = extended_config(static_config)

    rerun = run_pipeline(load_engagement(config), client=client, config=config)
    report = check_isa_coverage(rerun, config)

    added = report.for_requirement("ISA330.18")
    assert added.satisfied
    assert added.addressed_by == report.for_requirement(PROCEDURE_REQ).addressed_by


@pytest.mark.parametrize(
    ("requirement_id", "strip"),
    [
        (ASSERTION_REQ, "assertions"),
        (RISK_REQ, "risks"),
        (PROCEDURE_REQ, "procedures"),
    ],
)
def test_work_that_does_not_record_the_requirement_is_a_gap(
    engagement, static_config, requirement_id, strip
):
    """Coverage reports what the audit file claims, not what its shape implies.

    An object of the right type in the right place is not coverage if it never referenced the
    requirement — the same links `traceability` walks forward (SPEC 14).
    """
    inventory = engagement.line_item("inventory")
    targets = {
        "assertions": inventory.assertions,
        "risks": inventory.all_risks,
        "procedures": inventory.procedures,
    }[strip]
    for target in targets:
        target.isa_refs = []

    report = check_isa_coverage(engagement, static_config)
    stripped = report.for_requirement(requirement_id)

    assert not stripped.satisfied
    assert all(gap.line_item_type == "inventory" for gap in stripped.gaps)
    assert all("none recording" in gap.description for gap in stripped.gaps)
    assert not any(t.id in stripped.addressed_by for t in targets)
    assert all(
        c.satisfied for c in report.requirements if c.requirement.id != requirement_id
    ), "stripping one level's references must not gap the others"


def test_an_object_type_coverage_cannot_evaluate_raises(engagement, static_config, monkeypatch):
    """Silently reporting full coverage of an unchecked requirement is the failure the
    bounded `LinkedObjectType` enum exists to prevent."""
    monkeypatch.delitem(coverage_module._DISPATCH, LinkedObjectType.PROCEDURE)

    with pytest.raises(CoverageError, match=PROCEDURE_REQ):
        check_isa_coverage(engagement, static_config)


# --- read-only ---------------------------------------------------------------------------------


def test_coverage_is_read_only(engagement, static_config):
    before = engagement.model_dump()
    check_isa_coverage(engagement, static_config)

    assert engagement.model_dump() == before
