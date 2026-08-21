"""Eval: risk level drives procedure selection, with context held fixed (SPEC 25.8).

A/B varies context and risk together, so it cannot separate the two — a heavier procedure set
under Scenario B might be responding to the aged stock rather than to the rating. This holds the
context, the figures and the risk description constant and moves only `final_rating`, which is
the one thing the selection prompt is shown. If the work does not weaken, the rating is not
driving selection and ISA 330.7 is not being applied.

Live model, opt-in: `pytest -m eval`. One call on top of the shared rich run.
"""

import pytest

from evals.scenarios import (
    RISK_RANK,
    assertion_of,
    fresh,
    highest_system_rating,
    procedures_for_assertion,
    strongest_evidence,
)
from src.engine.catalogue import filter_catalogue
from src.engine.recompute import override_risk_rating
from src.llm.client import AnthropicLLMClient
from src.models.audit_objects import Assertion, EvidenceStrength, RiskLevel

pytestmark = pytest.mark.eval

INVENTORY = "inventory"
REASON = (
    "The stock is contractually pre-sold to a single customer at a fixed price, so the "
    "obsolescence exposure the system assessed does not arise."
)


def _weight(procedures) -> tuple[int, int]:
    """How much work a procedure set represents: count, then peak evidence strength.

    Ordinal on both axes, because the catalogue is small and a weakened response shows up as
    either fewer procedures or less persuasive ones.
    """
    strongest = strongest_evidence(procedures)
    return len(procedures), RISK_RANK[RiskLevel(strongest.value)] if strongest else -1


def _weakening_is_available(procedures, config) -> bool:
    """Whether the catalogue even permits a weaker response than the one selected.

    A single medium-strength procedure cannot weaken: dropping it entirely would leave the
    risk unanswered, which ISA 330.6 does not allow, and the catalogue holds nothing lower.
    In that position the scenario cannot evidence the claim either way, and the eval says so
    rather than passing on an exception that would also excuse a selector ignoring the rating.
    """
    if len(procedures) > 1:
        return True  # the count can fall
    peak = strongest_evidence(procedures)
    if peak is None:
        return False
    available = {
        p.evidence_strength
        for p in filter_catalogue(
            INVENTORY, Assertion.VALUATION, catalogue=config.procedure_catalogue
        )
    }
    return any(RISK_RANK[RiskLevel(s.value)] < RISK_RANK[RiskLevel(peak.value)] for s in available)


@pytest.fixture(scope="module")
def downgraded(rich_run, config):
    """The rich run with **every** valuation risk overridden down to low.

    All of them, not the first: one procedure often answers both risks on an assertion, and
    downgrading one leaves that procedure legitimately retained for the other. The comparison
    would then measure the risk that was not overridden. Costs one call per risk.

    A copy: the shared run must reach every other eval unmodified.
    """
    engagement = fresh(rich_run)
    valuation = assertion_of(engagement, INVENTORY, Assertion.VALUATION)
    if highest_system_rating(valuation) is not RiskLevel.HIGH:
        pytest.skip(
            "Scenario D is a high → low override; this run rated inventory valuation "
            f"{highest_system_rating(valuation)}, so there is no high-risk response to weaken"
        )

    for risk in list(valuation.risks):
        override_risk_rating(
            engagement, risk.id, RiskLevel.LOW, REASON,
            client=AnthropicLLMClient(), config=config,
        )
    return engagement


def test_lowering_the_rating_weakens_the_response(rich_run, downgraded, config, capsys):
    """SPEC 25.8. The check is ordinal: fewer procedures, or less persuasive ones, or both.

    Strict. An unchanged set is the exact behaviour this eval exists to catch — a selector
    reading the risk description and ignoring the rating produces one — so where weakening is
    possible at all, it is required.
    """
    before = procedures_for_assertion(rich_run, INVENTORY, Assertion.VALUATION)
    after = procedures_for_assertion(downgraded, INVENTORY, Assertion.VALUATION)

    with capsys.disabled():
        print(f"\n[before] {[(p.procedure_id, p.evidence_strength) for p in before]}")
        print(f"[after ] {[(p.procedure_id, p.evidence_strength) for p in after]}")

    if not _weakening_is_available(before, config):
        pytest.skip(
            f"the catalogue offers nothing weaker than {[p.procedure_id for p in before]}, "
            "so this run cannot evidence the claim either way"
        )

    assert _weight(after) < _weight(before), (
        f"the response did not weaken despite the rating dropping to low: "
        f"{[(p.procedure_id, p.evidence_strength) for p in before]} → "
        f"{[(p.procedure_id, p.evidence_strength) for p in after]}"
    )


def test_the_downgraded_risk_still_has_a_response(downgraded):
    """Weaker, not absent. A low risk is still an assessed risk, and ISA 330.6 asks for a
    response to each — a gap here would be reported by coverage, not by the model's silence."""
    after = procedures_for_assertion(downgraded, INVENTORY, Assertion.VALUATION)

    assert after, "the valuation risk was left with no procedure at all"


def test_a_high_rating_reaches_for_persuasive_evidence(rich_run):
    """The other half of the comparison, stated absolutely: the un-overridden run should be
    obtaining strong evidence over aged stock at 34x materiality (ISA 330.7)."""
    valuation = assertion_of(rich_run, INVENTORY, Assertion.VALUATION)
    if valuation.risks and max(
        RISK_RANK[r.final_rating] for r in valuation.risks
    ) < RISK_RANK[RiskLevel.HIGH]:
        pytest.skip("valuation was not rated high in this run; nothing to check")

    procedures = procedures_for_assertion(rich_run, INVENTORY, Assertion.VALUATION)
    assert strongest_evidence(procedures) is EvidenceStrength.HIGH, (
        f"a high valuation risk drew only "
        f"{[(p.procedure_id, p.evidence_strength) for p in procedures]}"
    )
