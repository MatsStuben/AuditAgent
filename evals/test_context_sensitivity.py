"""Evals for SPEC 22 A/B/C: identical numbers, different company context.

This is the product's central claim (SPEC 25). The financials are byte-identical between the
two runs and so is the cash paragraph; the inventory narrative is the only variable. Anything
that differs in the inventory output is therefore attributable to context, which is the whole
point of running them as a pair.

Assertions are comparative and ordinal wherever possible — exact-string matching on model prose
would fail on a synonym — but ordering alone is satisfiable by a degenerate model, so each is
paired with an absolute bound. `rank(B) > rank(A)` is equally true of a collapsed scale that
rates aged seasonal stock `low` and stable industrial componentry lower still.

Live model, opt-in: `pytest -m eval`. Ten calls (two scenarios, SPEC 6.1). Advisory, not a gate
— read `run_evals.py`'s table before concluding a prompt regressed.
"""

import pytest

from evals.scenarios import (
    RISK_RANK,
    assertion_of,
    highest_rating,
    procedures_for_assertion,
    relevant_assertions,
    strongest_evidence,
)
from src.models.audit_objects import Assertion, EvidenceStrength, RiskLevel

pytestmark = pytest.mark.eval

INVENTORY = "inventory"


def _valuation(engagement):
    return assertion_of(engagement, INVENTORY, Assertion.VALUATION)


# --- C: the same numbers must produce different audit output --------------------------------


def test_the_two_runs_share_their_financials_exactly(run_a, run_b):
    """Guards the experiment rather than the model: if the figures ever diverge, every
    comparison below becomes meaningless while still passing or failing plausibly."""
    def figures(run):
        return [(i.line_item_type, i.cy, i.py) for i in run.line_items]

    assert figures(run_a) == figures(run_b)
    assert run_a.materiality.amount == run_b.materiality.amount
    assert run_a.company_context != run_b.company_context


def test_context_moves_inventory_valuation_risk(run_a, run_b):
    """SPEC 22 C, the core claim: aged seasonal stock is a higher valuation risk than stable
    non-perishable componentry, on identical numbers."""
    rating_a, rating_b = highest_rating(_valuation(run_a)), highest_rating(_valuation(run_b))
    assert rating_a is not None and rating_b is not None, (
        f"valuation carried no risk in one run: A={rating_a}, B={rating_b}"
    )

    assert RISK_RANK[rating_b] > RISK_RANK[rating_a], (
        f"context did not move valuation risk: A={rating_a.value}, B={rating_b.value}"
    )


def test_the_rating_scale_has_not_collapsed(run_a, run_b):
    """The absolute bound behind the comparison above.

    Without it, `A=low, B=medium` and a scale that has quietly slid a level are the same
    result. Aged stock over 12 months at 34x materiality is not a low valuation risk, and
    stable non-perishable componentry with no recent write-downs is not a high one.
    """
    assert highest_rating(_valuation(run_b)) in (RiskLevel.MEDIUM, RiskLevel.HIGH)
    assert highest_rating(_valuation(run_a)) is not RiskLevel.HIGH


def test_valuation_is_relevant_in_both_runs(run_a, run_b):
    """SPEC 22: relevance is not what varies. Inventory can be misvalued in either business;
    what differs is how likely and how large."""
    assert _valuation(run_a).relevant, "valuation ruled out for the industrial scenario"
    assert _valuation(run_b).relevant, "valuation ruled out for the fashion scenario"


def test_the_lower_risk_scenario_does_not_flag_more_assertions(run_a, run_b):
    """Catches the opposite failure from a collapsed scale: a model marking everything
    relevant regardless of what it was told. Equality is allowed — the assertions in play are
    a property of inventory as a balance, and only their weight should move."""
    a, b = relevant_assertions(run_a, INVENTORY), relevant_assertions(run_b, INVENTORY)

    assert len(a) <= len(b), (
        f"the lower-risk scenario flagged more assertions: A={[x.value for x in a]}, "
        f"B={[x.value for x in b]}"
    )


# --- procedures respond to the assessment ----------------------------------------------------


def test_the_higher_risk_scenario_draws_at_least_as_much_work(run_a, run_b):
    """ISA 330.7: the response is proportionate to the assessment."""
    a = procedures_for_assertion(run_a, INVENTORY, Assertion.VALUATION)
    b = procedures_for_assertion(run_b, INVENTORY, Assertion.VALUATION)

    assert b, "the higher-risk scenario selected no valuation procedure at all"
    assert len(b) >= len(a), (
        f"the higher-risk scenario drew less work: A={[p.procedure_id for p in a]}, "
        f"B={[p.procedure_id for p in b]}"
    )


def test_the_higher_risk_scenario_obtains_persuasive_evidence(run_b):
    """ISA 330.7 again, absolutely rather than comparatively: a high valuation risk on aged
    stock should draw at least one procedure the catalogue rates as strong evidence."""
    procedures = procedures_for_assertion(run_b, INVENTORY, Assertion.VALUATION)

    assert strongest_evidence(procedures) is EvidenceStrength.HIGH, (
        "no high-strength valuation procedure selected: "
        f"{[(p.procedure_id, p.evidence_strength) for p in procedures]}"
    )


# --- the unchanged half ------------------------------------------------------------------------


def test_the_cash_area_is_broadly_unaffected(run_a, run_b, capsys):
    """Advisory. Cash is described identically in both, so a large swing in its assessment
    would mean the model is reading the inventory narrative as evidence about cash.

    Printed rather than asserted: cash relevance is a judgement, the two runs are independent
    samples, and one assertion moving is not by itself wrong. Run with `-s`.
    """
    a, b = relevant_assertions(run_a, "cash"), relevant_assertions(run_b, "cash")

    with capsys.disabled():
        print(f"\n[cash] A relevant: {[x.value for x in a]}")
        print(f"[cash] B relevant: {[x.value for x in b]}")
        if set(a) != set(b):
            print(f"[cash] differs despite identical cash context: {set(a) ^ set(b)}")
