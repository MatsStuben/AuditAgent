"""Evals for SPEC 22 D and E, end to end against the live model.

Both paths are already covered deterministically in `tests/test_recompute.py` and
`tests/test_feedback_generalizer.py` against the scripted fake. What those cannot answer is
whether the *model* behaves sensibly inside them: whether a scoped re-selection returns usable
work for a downgraded risk, and whether the classifier discriminates between a reason that
generalises and one that does not. That is what these add.

Live model, opt-in: `pytest -m eval`. Three calls on top of the shared rich run.
"""

import hashlib
from pathlib import Path

import pytest

from evals.scenarios import assertion_of, audit_area, fresh, highest_system_rating
from src.config.loader import DATA_DIR
from src.engine.coverage import check_isa_coverage
from src.engine.recompute import override_risk_rating, remove_procedure
from src.engine.traceability import trace_procedure
from src.llm.client import AnthropicLLMClient
from src.llm.feedback_generalizer import generalize_feedback
from src.models.audit_objects import Assertion, RiskLevel
from src.models.feedback import RuleProposalStatus

pytestmark = pytest.mark.eval

INVENTORY = "inventory"
METHODOLOGY_FILES = (
    "procedure_catalogue.json",
    "audit_area_profiles.json",
    "risk_matrix.json",
    "isa_requirements.json",
)

GENERALISABLE = (
    "Where inventory is non-perishable and the company has had no material write-downs for "
    "several years, subsequent-sales testing adds nothing over the ageing review and should "
    "not be required."
)
ENGAGEMENT_SPECIFIC = (
    "This particular customer contract was signed after year end and I have seen the "
    "documentation myself."
)


def _digests() -> dict[str, str]:
    return {
        name: hashlib.sha256(Path(DATA_DIR / name).read_bytes()).hexdigest()
        for name in METHODOLOGY_FILES
    }


# --- Scenario D: risk override, end to end ---------------------------------------------------


@pytest.fixture(scope="module")
def overridden(rich_run, config):
    """Inventory valuation risk overridden high → low, on a copy of the shared run."""
    engagement = fresh(rich_run)
    valuation = assertion_of(engagement, INVENTORY, Assertion.VALUATION)
    high = [r for r in valuation.risks if r.system_rating is RiskLevel.HIGH]
    if not high:
        pytest.skip(
            "Scenario D is specified as high → low; this run rated inventory valuation "
            f"{highest_system_rating(valuation)}, which is a different experiment"
        )

    feedback = override_risk_rating(
        engagement,
        high[0].id,
        RiskLevel.LOW,
        "The inventory is contractually pre-sold and has very low obsolescence exposure.",
        client=AnthropicLLMClient(),
        config=config,
    )
    return engagement, feedback


def test_the_original_system_conclusion_survives(overridden):
    engagement, feedback = overridden
    risk = audit_area(engagement, INVENTORY).risk(feedback.object_id)

    assert risk.system_rating is not None
    assert risk.final_rating is RiskLevel.LOW
    assert risk.is_overridden is True
    assert feedback.before["final_rating"] == risk.system_rating.value
    # The engine's own reasoning is intact, not just its rating.
    assert risk.likelihood and risk.magnitude and risk.rationale


def test_the_area_is_still_coherent_after_a_live_reselection(overridden, config):
    """The scoped call returned real procedures, correctly linked — the failure mode here is
    a model response that validates but leaves the area half-linked."""
    engagement, _ = overridden
    inventory = audit_area(engagement, INVENTORY)

    assert inventory.procedures, "re-selection left the area with no procedures"
    assert not inventory.dangling_risk_ids()
    for procedure in inventory.procedures:
        assert trace_procedure(procedure, engagement), f"{procedure.id} traces nowhere"
    assert check_isa_coverage(engagement, config).satisfied, (
        f"gaps after the override: {check_isa_coverage(engagement, config).gaps}"
    )


def test_the_cash_area_is_untouched_by_an_inventory_override(rich_run, overridden):
    """SPEC 22 D: unrelated areas remain unchanged. Compared by value against the shared run,
    since `overridden` works on a copy and identity cannot be used across the two."""
    engagement, _ = overridden

    assert audit_area(engagement, "cash").model_dump() == audit_area(
        rich_run, "cash"
    ).model_dump()


# --- Scenario E: feedback becomes a candidate rule ---------------------------------------------


def test_a_generalisable_reason_produces_a_pending_rule(run_a, capsys):
    """SPEC 22 E. A failure here means reading the printed classification and judging whether
    the reason really does generalise — not tightening the prompt until this passes.

    Run against Scenario A, whose stock *is* non-perishable and unwritten-down. Attached to
    the fashion run this was classified engagement-specific, and defensibly so: an auditor
    stating a condition their own engagement does not meet is stating a hypothesis, not
    generalising from what they just decided.
    """
    engagement = fresh(run_a)
    inventory = audit_area(engagement, INVENTORY)
    procedure = inventory.procedures[0]
    feedback = remove_procedure(
        engagement, procedure.id, procedure.risk_ids[0], GENERALISABLE
    )

    proposal = generalize_feedback(feedback, engagement, client=AnthropicLLMClient())

    with capsys.disabled():
        print(f"\n[E] removed {procedure.procedure_id}: {proposal}")

    assert proposal is not None, "a reason stated as a general principle was not generalised"
    assert proposal.condition and proposal.action
    assert proposal.status is RuleProposalStatus.PENDING_REVIEW
    assert proposal.source_feedback_id == feedback.id


def test_an_engagement_specific_reason_is_not_generalised(rich_run, capsys):
    """The discriminating half. Without it, a classifier that always proposes a rule passes
    the eval above — and SPEC 19's prompt is explicitly meant to prefer the narrow answer."""
    engagement = fresh(rich_run)
    inventory = audit_area(engagement, INVENTORY)
    procedure = inventory.procedures[0]
    feedback = remove_procedure(
        engagement, procedure.id, procedure.risk_ids[0], ENGAGEMENT_SPECIFIC
    )

    proposal = generalize_feedback(feedback, engagement, client=AnthropicLLMClient())

    with capsys.disabled():
        print(f"\n[E] one-off reason classified as: {proposal or 'engagement_specific'}")

    assert proposal is None, f"a one-off judgement was proposed as methodology: {proposal}"


def test_the_live_learning_path_writes_no_methodology(rich_run):
    """SPEC 19, against the real model rather than a fake: nothing in this pipeline touches
    the approved config, whichever way it classifies."""
    before = _digests()
    engagement = fresh(rich_run)
    procedure = audit_area(engagement, INVENTORY).procedures[0]
    feedback = remove_procedure(
        engagement, procedure.id, procedure.risk_ids[0], GENERALISABLE
    )

    generalize_feedback(feedback, engagement, client=AnthropicLLMClient())

    assert _digests() == before
