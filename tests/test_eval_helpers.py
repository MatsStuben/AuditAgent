"""Offline tests for the eval scanners.

The evals in `evals/` cost API calls and are opt-in, so their scanners are the one part of
the eval suite that can — and must — be checked deterministically. A false negative here
would make the unsupported-inference eval pass while saying nothing; a false positive would
fail a run over a word used innocently.
"""

import pytest

from evals.scenarios import (
    AMBIGUOUS_TERMS,
    MINIMAL_CONTEXT,
    SILENCE_PATTERNS,
    SUSPECT_TERMS,
    ambiguous_terms,
    rich_context,
    silence_language,
    supplied_to_analysis,
    unsupported_terms,
)
from src.models.engagement import AuditEngagement, CompanyFact


def test_flags_a_circumstance_the_input_never_supplied():
    produced = "Takings held as store floats at year end may never have been banked."
    assert unsupported_terms(produced, MINIMAL_CONTEXT)


def test_ignores_a_term_the_input_does_supply(static_config):
    """The scan is relative to what was supplied, which is what lets the same list serve both
    the short context and the rich one. Consignment stock is fiction under one and a stated
    fact under the other."""
    produced = "Consignment stock held across the two warehouses may be recorded as owned."
    assert unsupported_terms(produced, MINIMAL_CONTEXT)
    assert not unsupported_terms(produced, rich_context(static_config))


def test_matching_ignores_case():
    assert unsupported_terms("Cash-in-Transit at year end", MINIMAL_CONTEXT)


@pytest.mark.parametrize(
    "produced",
    [
        "Inventory is stored in bulk.",  # 'stored', not 'store'
        "The company restores written-down items to full value.",  # 'restores'
        "Storage costs are capitalised into inventory.",  # 'storage'
        "Workshops are not part of this area.",  # 'workshops', not 'shops'
    ],
)
def test_word_boundaries_prevent_false_positives(produced):
    """Every pattern is anchored, so a suspect word appearing inside a longer, innocent one
    is not a finding."""
    assert not unsupported_terms(produced, MINIMAL_CONTEXT)


def test_the_shipped_context_suppresses_only_what_it_states(static_config):
    """The richer demo context deliberately supplies several suspect terms, which is the point
    of subtracting supplied vocabulary rather than using a fixed blocklist. It must not
    suppress the rest."""
    supplied = rich_context(static_config)
    assert not unsupported_terms("Warehouse consignment stock and bank accounts.", supplied)
    assert unsupported_terms("Takings from tills held in escrow.", supplied)


SUSPECT_PROBES = {
    r"\bstores?\b": "store",
    r"\boutlets?\b": "outlet",
    r"\bbranch(es)?\b": "branch",
    r"\bshops?\b": "shop",
    r"\bshop floors?\b": "shop floor",
    r"\btills?\b": "till",
    r"\btakings\b": "takings",
    r"\bpoint[- ]of[- ]sale\b": "point-of-sale",
    r"\bepos\b": "epos",
    r"\bcard settlements?\b": "card settlements",
    r"\bcash[- ]in[- ]transit\b": "cash in transit",
    r"\bfloats?\b": "float",
    r"\bpetty cash\b": "petty cash",
    r"\bwarehouses?\b": "warehouse",
    r"\blogistics\b": "logistics",
    r"\bdistribution cent(re|er)s?\b": "distribution centre",
    r"\bmultiple locations\b": "multiple locations",
    r"\bblack friday\b": "black friday",
    r"\bchristmas\b": "christmas",
    r"\b(peak trading|trading peak)\b": "peak trading",
    r"\be-?commerce\b": "ecommerce",
    r"\bonline sales\b": "online sales",
    r"\bfranchis(e|es|ee|ees)\b": "franchisee",
    r"\bcontrol (deficienc(y|ies)|weakness(es)?|failures?)\b": "control weakness",
    r"\b(weak|inadequate|poor|insufficient) controls?\b": "weak controls",
    r"\b(controls?|systems?)[^.]{0,40}(kept pace|under strain|strained|outgrown)\b": (
        "controls may not have kept pace"
    ),
}

AMBIGUOUS_PROBES = {
    r"\bforeign[- ]currenc(y|ies)\b": "foreign currency",
    r"\bexchange rates?\b": "exchange rate",
    r"\bretranslation\b": "retranslation",
    r"\boverdrafts?\b": "overdraft",
    r"\bborrowings?\b": "borrowings",
    r"\bescrow\b": "escrow",
    r"\brestricted cash\b": "restricted cash",
    r"\bconsignment\b": "consignment",
    r"\bthird[- ]part(y|ies)\b": "third-party",
}


@pytest.mark.parametrize(
    ("terms", "probes", "scan"),
    [
        (SUSPECT_TERMS, SUSPECT_PROBES, unsupported_terms),
        (AMBIGUOUS_TERMS, AMBIGUOUS_PROBES, ambiguous_terms),
    ],
    ids=["suspect", "ambiguous"],
)
def test_every_term_compiles_and_matches_itself(terms, probes, scan):
    """A pattern with a typo would sit in the list matching nothing, quietly narrowing the
    check. Each is exercised against a string built to satisfy it."""
    assert set(probes) == set(terms), "probe list has drifted from the term list"
    unmatched = [pattern for pattern, probe in probes.items() if not scan(probe, "")]
    assert not unmatched, f"terms that never match: {unmatched}"


def test_the_two_term_lists_are_disjoint():
    """A term in both would be asserted on and excused at once."""
    assert not set(SUSPECT_TERMS) & set(AMBIGUOUS_TERMS)


def test_assertion_vocabulary_is_reported_but_not_asserted_on():
    """The distinction the split exists for: rights and obligations cannot be described
    without this vocabulary, so it must not fail a run."""
    mechanism = (
        "Cash may include amounts subject to restriction, pledge or third-party entitlement "
        "and yet be presented as unrestricted."
    )
    assert not unsupported_terms(mechanism, MINIMAL_CONTEXT)
    assert ambiguous_terms(mechanism, MINIMAL_CONTEXT)


def test_findings_quote_the_text_that_tripped_them():
    """A pattern alone cannot be judged; roughly half of what this scanner reports is the
    pattern being too broad rather than the model inventing anything."""
    produced = "Cash held as store floats at the year end may never have been banked."
    findings = unsupported_terms(produced, MINIMAL_CONTEXT)
    (finding,) = [f for f in findings if f.startswith(r"\bstores?\b")]
    assert "may never have been banked" in finding


def test_silence_language_flags_reasoning_from_absence():
    assert silence_language("The context does not mention any foreign currency accounts.")
    assert silence_language("No indication of restricted balances was provided.")
    assert silence_language("Nothing in the context suggests third-party holdings.")


def test_silence_language_allows_a_stated_negative_fact():
    """A negative *fact* is evidence; a negative *about the information* is not. Every verb
    in the pattern list is about the information, so a stated fact passes."""
    assert not silence_language(
        "The company holds no restricted cash balances and no foreign-currency accounts "
        "(fact_7), so translation misstatement cannot arise."
    )
    assert not silence_language("Cash is not subject to estimation, so valuation cannot vary.")


def test_silence_patterns_are_all_exercised_by_the_positive_cases():
    """Guards against a pattern that can never match anything — a typo in one would otherwise
    sit in the list unnoticed and quietly narrow the check."""
    corpus = " ".join(
        [
            "the amount is not mentioned",
            "no indication of restricted balances",
            "nothing in the context suggests otherwise",
            "the absence of information about this",
            "the company does not disclose its arrangements",
            "the context is silent on this point",
            "the information provided does not extend to it",
        ]
    )
    unmatched = [p for p in SILENCE_PATTERNS if p not in silence_language(corpus)]
    assert not unmatched, f"silence patterns that never match: {unmatched}"


def test_supplied_vocabulary_includes_the_extracted_facts():
    """Facts are part of what the analysis call is given, so a term the extractor legitimately
    produced must not then be reported as an invention downstream."""
    engagement = AuditEngagement(
        company="Raiatea Ltd", year_end="2025-12-31", company_context="Seasonal retailer."
    )
    engagement.company_facts = [
        CompanyFact(
            id="fact_1",
            fact_type="inventory_locations",
            value="two warehouses",
            rationale="Inventory is held across two warehouses.",
        )
    ]
    supplied = supplied_to_analysis(engagement)
    assert not unsupported_terms("Stock at the warehouses may be miscounted.", supplied)
