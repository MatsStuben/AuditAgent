"""Evals for the epistemic discipline of the LLM layer (SPEC 10, 11, 13, 21).

The first end-to-end Raiatea runs were audit-plausible and partly fictional: risks about store
floats, card settlements in transit and stock controls that had "not kept pace with expansion",
none of which the engagement had supplied. These evals check the model reasons from what it was
given, in both directions — no invented circumstances, and no ruling an assertion out merely
because the input was silent.

Live model, opt-in: `pytest -m eval`. Two scenarios, five calls each (SPEC 6.1).

Assertions are behavioural, never exact-string: they check what kind of thing the model said,
not which words it chose. The one exception is the near-duplicate risk check, which is
advisory — semantic restatement cannot be detected lexically, so it prints for review rather
than failing.
"""

import pytest

from evals.scenarios import (
    MINIMAL_CONTEXT,
    ambiguous_terms,
    analysis_text,
    fact_text,
    selection_text,
    silence_language,
    supplied_to_analysis,
    supplied_to_selection,
    unsupported_terms,
)
from src.models.audit_objects import Assertion, RiskLevel

pytestmark = pytest.mark.eval

# `config`, `minimal_run` and `rich_run` are session fixtures in `conftest.py`, shared with the
# other eval modules so one five-call run answers everything asked of it.


# --- no unsupported company specifics ---------------------------------------------------


def test_extracted_facts_stay_inside_the_context(minimal_run):
    """Extraction is the first place invention becomes durable: a fact is cited by ID
    downstream, so anything invented here is laundered into evidence."""
    found = unsupported_terms(fact_text(minimal_run), MINIMAL_CONTEXT)
    assert not found, f"facts introduce circumstances the context does not state: {found}"


@pytest.mark.parametrize("scenario", ["minimal_run", "rich_run"])
def test_analysis_introduces_no_unsupplied_circumstances(scenario, request):
    engagement = request.getfixturevalue(scenario)
    found = unsupported_terms(analysis_text(engagement), supplied_to_analysis(engagement))
    assert not found, f"analysis invents company-specific circumstances: {found}"


@pytest.mark.parametrize("scenario", ["minimal_run", "rich_run"])
def test_report_terms_that_could_be_invention_or_assertion_vocabulary(
    scenario, request, capsys
):
    """Advisory, never a gate.

    Rights and obligations cannot be described without saying "third-party entitlement" or
    "consignment", so these terms appear legitimately even when nothing was supplied about
    them. Whether a given use states a generic mechanism or asserts a circumstance is a
    reading, not a match. Run with `-s`.
    """
    engagement = request.getfixturevalue(scenario)
    found = ambiguous_terms(analysis_text(engagement), supplied_to_analysis(engagement))
    with capsys.disabled():
        print(f"\n[{scenario}] terms to read in context: {len(found)}")
        for finding in found:
            print(f"  {finding}")


@pytest.mark.parametrize("scenario", ["minimal_run", "rich_run"])
def test_selection_introduces_no_unsupplied_circumstances(scenario, request, config):
    """Selection responds to an assessed risk; it does not get to add to what is known.

    Its supplied vocabulary includes the catalogue, so quoting an approved procedure's own
    description back is not an invention.
    """
    engagement = request.getfixturevalue(scenario)
    found = unsupported_terms(
        selection_text(engagement), supplied_to_selection(engagement, config)
    )
    assert not found, f"procedure rationales invent company-specific circumstances: {found}"


# --- absence of evidence is not evidence of absence -------------------------------------


@pytest.mark.parametrize("scenario", ["minimal_run", "rich_run"])
def test_assertions_are_ruled_out_on_facts_not_on_silence(scenario, request):
    """Which assertions get ruled out is the model's judgement and is not asserted here.

    What is asserted is the grounds. A not-relevant verdict must rest on a supplied fact —
    cited by ID — or on the inherent nature of the item. It must not rest on the input having
    said nothing about the matter, which is the reasoning the richer context exists to
    replace.
    """
    engagement = request.getfixturevalue(scenario)
    offenders = []
    for item in engagement.in_scope_audit_areas:
        for assertion in item.assertions:
            if assertion.relevant or assertion.supporting_fact_ids:
                continue
            silence = silence_language(assertion.rationale)
            if silence:
                offenders.append(
                    f"{item.line_item_type}/{assertion.assertion.value}: "
                    f"{silence} — {assertion.rationale!r}"
                )
    assert not offenders, "assertions ruled out from silence rather than evidence: " + "; ".join(
        offenders
    )


def test_facts_are_cited_only_where_they_bear_on_the_area(rich_run):
    """A fact about inventory is not evidence about cash.

    The earlier runs cited the industry and growth facts as support for cash existence risks,
    which reads as grounding while supplying none. Facts naming one area and not the other are
    treated as area-specific; anything about the company as a whole is left alone.
    """
    def area_of(fact) -> str | None:
        text = f"{fact.fact_type} {fact.value} {fact.rationale}".lower()
        mentions_inventory = "inventor" in text or "stock" in text
        mentions_cash = "cash" in text or "bank" in text
        if mentions_inventory and not mentions_cash:
            return "inventory"
        if mentions_cash and not mentions_inventory:
            return "cash"
        return None

    facts = {f.id: f for f in rich_run.company_facts}
    offenders = []
    for item in rich_run.in_scope_audit_areas:
        for assertion in item.assertions:
            cited = list(assertion.supporting_fact_ids)
            for risk in assertion.risks:
                cited.extend(risk.supporting_fact_ids)
            for fact_id in set(cited):
                area = area_of(facts[fact_id])
                if area is not None and area != item.line_item_type:
                    offenders.append(
                        f"{item.line_item_type}/{assertion.assertion.value} cites "
                        f"{fact_id} ({facts[fact_id].fact_type}), an {area} fact"
                    )
    assert not offenders, "facts cited outside their area: " + "; ".join(offenders)


# --- the judgement is still doing its job ------------------------------------------------


def test_inventory_valuation_risk_stays_elevated(rich_run):
    """The check that stops the stricter prompts from buying discipline with blandness.

    Aged stock over 12 months, an ageing-based write-down policy and a 43% increase against
    inventory at 34x materiality is the clearest risk in the case. If tightening the epistemic
    rules flattened this to low, the model has stopped reasoning rather than stopped inventing.
    """
    inventory = next(
        i for i in rich_run.in_scope_audit_areas if i.line_item_type == "inventory"
    )
    valuation = next(
        a for a in inventory.assertions if a.assertion is Assertion.VALUATION
    )
    assert valuation.relevant, "inventory valuation should be relevant on this engagement"
    assert valuation.risks, "a relevant valuation assertion must carry a risk"
    assert any(
        r.final_rating in (RiskLevel.MEDIUM, RiskLevel.HIGH) for r in valuation.risks
    ), f"valuation risk ratings collapsed to low: {[r.final_rating for r in valuation.risks]}"
    assert any(p.risk_ids for p in inventory.procedures), "valuation risk drew no procedures"


def test_cash_analysis_is_grounded_and_responsive(rich_run):
    """Cash is explicitly described in the rich context, so its analysis should read as an
    assessment of this company rather than of retailers in general."""
    cash = next(i for i in rich_run.in_scope_audit_areas if i.line_item_type == "cash")

    assert any(a.relevant for a in cash.assertions), "no cash assertion judged relevant"
    assert cash.all_risks, "a relevant cash assertion produced no risk"
    assert cash.procedures, "cash risks drew no procedures"
    assert not cash.dangling_risk_ids(), "cash procedures reference risks that do not exist"

    grounded = [
        r for r in cash.all_risks if r.supporting_fact_ids
    ]
    assert grounded, (
        "no cash risk cites a supplied fact, although the context describes cash directly"
    )


def test_second_risks_are_distinct(rich_run, capsys):
    """Advisory. Two risks on one assertion must differ in mechanism *and* in the response
    they call for — which is a semantic judgement no lexical check can make. The overlap
    bound here catches only near-verbatim restatement; the printed pairs are for review.

    Run with `-s` to see the report.
    """
    def content_words(text: str) -> set[str]:
        return {w for w in text.lower().split() if len(w) > 4}

    pairs = []
    for item in rich_run.in_scope_audit_areas:
        for assertion in item.assertions:
            if len(assertion.risks) < 2:
                continue
            first, second = assertion.risks[0], assertion.risks[1]
            a, b = content_words(first.risk_description), content_words(second.risk_description)
            overlap = len(a & b) / len(a | b) if a | b else 0.0
            pairs.append((item.line_item_type, assertion.assertion.value, overlap, first, second))

    with capsys.disabled():
        for area, assertion, overlap, first, second in pairs:
            print(f"\n[{area}/{assertion}] overlap {overlap:.0%}")
            print(f"  1. {first.risk_description}")
            print(f"  2. {second.risk_description}")

    restated = [
        f"{area}/{assertion} ({overlap:.0%})"
        for area, assertion, overlap, _, _ in pairs
        if overlap > 0.7
    ]
    assert not restated, "second risk restates the first: " + "; ".join(restated)
