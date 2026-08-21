"""Scenario fixtures and the text scanners the evals assert with.

The scanners live here rather than in a test file because they are ordinary functions with
ordinary bugs, and `tests/test_eval_helpers.py` tests them offline. An eval that fails is only
informative if the thing doing the checking is known to work.

The central idea is *supported vs unsupported vocabulary*. A term is unsupported when it
appears in model output but appears nowhere in what was supplied to the call that produced it.
That is deliberately relative, not a fixed blocklist: "consignment" is an invention under the
short context and a supplied fact under the rich one, and the same list must give the right
answer for both. It also means the checks do not have to be rewritten whenever the context is
edited.
"""

import re

from src.config.loader import StaticConfig, get_config
from src.engine.pipeline import load_engagement, run_pipeline
from src.llm.client import AnthropicLLMClient
from src.models.audit_objects import (
    Assertion,
    AssertionAssessment,
    EvidenceStrength,
    Procedure,
    RiskLevel,
)
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment

#: The context the engine shipped with before the richer one replaced it. Kept because it is
#: the highest-pressure case for unsupported inference: two sentences about inventory, nothing
#: at all about cash, and eleven times materiality of cash to explain. Every company-specific
#: detail the model produces for cash under this context is necessarily invented.
MINIMAL_CONTEXT = (
    "Raiatea is a fast-growing fashion retailer. Inventory is highly seasonal and a "
    "meaningful share of inventory is more than 12 months old."
)


def rich_context(config: StaticConfig | None = None) -> str:
    """The demo default, read from `raiatea.json` so the eval cannot drift from it."""
    return (config or get_config()).engagement_input.company_context


#: Cash, described identically in Scenarios A and B (SPEC 22).
#:
#: A and B exist to isolate one variable, and the scenarios as SPEC 22 states them describe only
#: inventory. Leaving cash unsaid in both would hold it constant but would also put both runs
#: under the Scenario F pressure — every cash detail invented — and any A/B difference in the
#: cash area would then be noise rather than signal. So cash is described, and described the
#: same way, in both.
_SHARED_CASH_CONTEXT = (
    "The company holds cash across three bank accounts, all in GBP. Bank reconciliations "
    "are prepared monthly. There are no restricted cash balances or foreign-currency "
    "accounts. The company does not hold material physical cash at year end."
)

#: Scenario A — lower-risk inventory (SPEC 22). Stable industrial company, non-perishable
#: stock, low obsolescence, stable demand.
#:
#: The stock build is explained, and that is not decoration. Inventory rose 43.5% and sits at
#: 34x materiality; a build of that size against flat demand is itself an obsolescence signal,
#: and the first live run rated Scenario A `high` on exactly that reading — correctly. A
#: low-risk scenario has to be a low-risk story *for these numbers*, or the eval is asking the
#: model to ignore the figures it was given.
CONTEXT_A = (
    "Raiatea manufactures industrial fixings and fasteners for the construction trade. "
    "Demand has been stable for several years and the product range changes little between "
    "years. Inventory is non-perishable steel and brass componentry with no shelf life, and "
    "the company has not written down material amounts of stock in recent years. The "
    "increase in inventory this year reflects a bulk purchase of standard componentry ahead "
    "of an announced supplier price rise; the material is used across the whole range and is "
    "not specific to any customer, contract or season. Inventory "
    "is held across two warehouses and the company performs a full physical count at year "
    "end. Some inventory is held on consignment from suppliers, and management applies an "
    "ageing-based write-down policy to slow-moving stock.\n\n"
    f"{_SHARED_CASH_CONTEXT}"
)

#: Scenario B — higher-risk inventory (SPEC 22). Seasonal fashion retailer, rapidly changing
#: range, meaningful aged inventory. The same closing sentences as A, so the two differ in the
#: nature of the stock and nothing else.
CONTEXT_B = (
    "Raiatea is a fast-growing fashion retailer. Inventory is highly seasonal and the "
    "product range is replaced several times a year. A meaningful share of inventory is more "
    "than 12 months old. Inventory "
    "is held across two warehouses and the company performs a full physical count at year "
    "end. Some inventory is held on consignment from suppliers, and management applies an "
    "ageing-based write-down policy to slow-moving stock.\n\n"
    f"{_SHARED_CASH_CONTEXT}"
)


#: Company-specific circumstances a fashion retailer plausibly has, and that the model
#: reached for unprompted in the first end-to-end runs: retail outlets and their cash
#: handling, other locations holding stock, treasury arrangements, dated trading events, and
#: assertions about control effectiveness (which SPEC 26 puts outside the MVP entirely).
#:
#: Patterns, not words, because the useful unit is "any mention of tills" rather than one
#: spelling of it. Anything matching here is only reported when the same pattern fails to
#: match the supplied input. Each is anchored at both ends: without the leading `\b`,
#: "stores?" matches inside "restores" and the scanner reports findings that are not there.
SUSPECT_TERMS: tuple[str, ...] = (
    r"\bstores?\b",
    r"\boutlets?\b",
    r"\bbranch(es)?\b",
    r"\bshops?\b",
    r"\bshop floors?\b",
    r"\btills?\b",
    r"\btakings\b",
    r"\bpoint[- ]of[- ]sale\b",
    r"\bepos\b",
    r"\bcard settlements?\b",
    r"\bcash[- ]in[- ]transit\b",
    r"\bfloats?\b",
    r"\bpetty cash\b",
    r"\bwarehouses?\b",
    r"\blogistics\b",
    r"\bdistribution cent(re|er)s?\b",
    r"\bmultiple locations\b",
    r"\bblack friday\b",
    r"\bchristmas\b",
    r"\b(peak trading|trading peak)\b",
    r"\be-?commerce\b",
    r"\bonline sales\b",
    r"\bfranchis(e|es|ee|ees)\b",
    r"\bcontrol (deficienc(y|ies)|weakness(es)?|failures?)\b",
    r"\b(weak|inadequate|poor|insufficient) controls?\b",
    r"\b(controls?|systems?)[^.]{0,40}(kept pace|under strain|strained|outgrown)\b",
)

#: Terms that are *also* how the assertions themselves are defined, so their presence cannot
#: be gated on. Rights and obligations for cash is, definitionally, about amounts subject to
#: restriction, pledge or third-party entitlement; rights for inventory is about consignment
#: and goods held for others. A risk written as "cash may include amounts subject to
#: third-party entitlement" states the generic mechanism, which SPEC 21.1 permits — the same
#: words in "cash held at the group's escrow agent" would be an invention, and no regex can
#: tell the two apart.
#:
#: Reported for human review rather than asserted on. Anything here that turns out to be a
#: reliable signal of invention belongs in `SUSPECT_TERMS` instead.
AMBIGUOUS_TERMS: tuple[str, ...] = (
    r"\bforeign[- ]currenc(y|ies)\b",
    r"\bexchange rates?\b",
    r"\bretranslation\b",
    r"\boverdrafts?\b",
    r"\bborrowings?\b",
    r"\bescrow\b",
    r"\brestricted cash\b",
    r"\bconsignment\b",
    r"\bthird[- ]part(y|ies)\b",
)

#: Phrasings that rule something out because nothing was said about it. Distinct from a
#: negative *fact* ("there are no restricted cash balances", which the context does state):
#: every verb here is about the information, not about the company. A not-relevant verdict
#: resting on one of these is reasoning from silence, which SPEC 10 does not permit.
SILENCE_PATTERNS: tuple[str, ...] = (
    r"\bnot (mentioned|stated|disclosed|indicated|specified|provided|supplied)\b",
    r"\bno (mention|indication|information|evidence|suggestion|reference|detail)s?"
    r" (of|that|to|about)\b",
    r"\bnothing (in the context|supplied|suggests|indicates)\b",
    r"\b(absence|lack) of (any )?(information|evidence|mention|indication|detail)\b",
    r"\bdoes not (mention|state|disclose|indicate|specify|describe|suggest|refer)\b",
    r"\bis silent\b",
    r"\b(context|information)[^.]{0,30}does not\b",
)


#: Characters of surrounding text to quote with a finding.
SNIPPET_WINDOW = 90


def _matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _snippet(pattern: str, text: str) -> str:
    """The match plus its surroundings, so a finding can be judged without a second run.

    Half of what this scanner reports is a pattern being too broad rather than the model
    inventing anything, and the two are indistinguishable from the pattern alone.
    """
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return ""
    start = max(0, match.start() - SNIPPET_WINDOW)
    end = min(len(text), match.end() + SNIPPET_WINDOW)
    return " ".join(text[start:end].split())


def _scan(terms: tuple[str, ...], produced: str, supplied: str) -> list[str]:
    return [
        f"{pattern} → …{_snippet(pattern, produced)}…"
        for pattern in terms
        if _matches(pattern, produced) and not _matches(pattern, supplied)
    ]


def unsupported_terms(produced: str, supplied: str) -> list[str]:
    """Suspect terms present in `produced` and absent from `supplied`. Asserted on.

    Each finding is the pattern and the text around its first match: the pattern says what
    tripped, the snippet says whether that was fair.
    """
    return _scan(SUSPECT_TERMS, produced, supplied)


def ambiguous_terms(produced: str, supplied: str) -> list[str]:
    """The same scan over `AMBIGUOUS_TERMS`. Printed for review, never asserted on."""
    return _scan(AMBIGUOUS_TERMS, produced, supplied)


def silence_language(text: str) -> list[str]:
    """Silence patterns present in `text`."""
    return [pattern for pattern in SILENCE_PATTERNS if _matches(pattern, text)]


# --- what was supplied, per stage ------------------------------------------------------


def supplied_to_analysis(engagement: AuditEngagement) -> str:
    """The company-specific input the analysis call sees: context plus extracted facts.

    Facts are included because they are themselves checked against the context, so a term
    that survives extraction is one the context supports.
    """
    facts = " ".join(
        f"{f.fact_type} {f.value} {f.rationale}" for f in engagement.company_facts
    )
    return f"{engagement.company_context}\n{facts}"


def supplied_to_selection(
    engagement: AuditEngagement, config: StaticConfig | None = None
) -> str:
    """Everything analysis saw, plus the catalogue and the risks already assessed.

    The catalogue matters: `INV_RIGHTS_REVIEW` names consignment and third-party
    documentation, so a selection rationale echoing those is quoting approved methodology
    rather than inventing a circumstance.
    """
    config = config or get_config()
    catalogue = " ".join(
        f"{p.name} {p.description} {p.procedure_type}" for p in config.procedure_catalogue
    )
    risks = " ".join(
        f"{r.risk_description} {r.rationale}"
        for item in engagement.line_items
        for r in item.all_risks
    )
    return f"{supplied_to_analysis(engagement)}\n{catalogue}\n{risks}"


# --- what the model produced, per stage ------------------------------------------------


def fact_text(engagement: AuditEngagement) -> str:
    return "\n".join(
        f"{f.fact_type} {f.value} {f.rationale}" for f in engagement.company_facts
    )


def analysis_text(engagement: AuditEngagement) -> str:
    """Every string the analysis call authored, across all audit areas."""
    parts = []
    for item in engagement.in_scope_audit_areas:
        for assertion in item.assertions:
            parts.append(assertion.rationale)
            for risk in assertion.risks:
                parts.append(f"{risk.risk_description} {risk.rationale}")
    return "\n".join(parts)


def selection_text(engagement: AuditEngagement) -> str:
    """Every string the selection call authored. Catalogue names and descriptions are
    excluded — those are config, not model output."""
    parts = []
    for item in engagement.in_scope_audit_areas:
        for procedure in item.procedures:
            parts.append(procedure.rationale)
            if procedure.procedure_id is None:
                parts.append(procedure.description)  # an AI suggestion is model-authored
    return "\n".join(parts)


# --- running a scenario -----------------------------------------------------------------


def run_scenario(context: str, config: StaticConfig | None = None) -> AuditEngagement:
    """One full live pipeline run over `context`. Five API calls (SPEC 6.1)."""
    config = config or get_config()
    engagement = load_engagement(config)
    engagement.company_context = context
    return run_pipeline(engagement, client=AnthropicLLMClient(), config=config)


def fresh(engagement: AuditEngagement) -> AuditEngagement:
    """A private deep copy of a completed run.

    Scenario runs are session-scoped because each costs five live calls, and D and E both
    mutate the engagement they act on. Handing out copies is what stops one eval's override
    from becoming another eval's starting state.
    """
    return engagement.model_copy(deep=True)


# --- reading a completed run ---------------------------------------------------------------

#: Ordinal, for the comparative assertions SPEC 22 C asks for. Ratings are compared by rank
#: rather than by equality: the claim is that context *moves* risk, not that it lands on a
#: particular level.
RISK_RANK: dict[RiskLevel, int] = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def audit_area(engagement: AuditEngagement, line_item_type: str) -> FinancialLineItemAssessment:
    return next(
        i for i in engagement.in_scope_audit_areas if i.line_item_type == line_item_type
    )


def assertion_of(
    engagement: AuditEngagement, line_item_type: str, assertion: Assertion
) -> AssertionAssessment:
    area = audit_area(engagement, line_item_type)
    return next(a for a in area.assertions if a.assertion is assertion)


def highest_rating(assertion: AssertionAssessment) -> RiskLevel | None:
    """The assertion's most severe risk rating, or None if it carries no risks.

    The peak rather than the mean: an assertion carrying one high risk and one low is a high
    valuation exposure, and averaging would report it as medium.
    """
    if not assertion.risks:
        return None
    return max((r.final_rating for r in assertion.risks), key=lambda r: RISK_RANK[r])


def highest_system_rating(assertion: AssertionAssessment) -> RiskLevel | None:
    """The peak rating the *engine* reached, before any override.

    Scenario D is specified as high → low (SPEC 22), and an eval that overrides whatever it
    finds is testing a different experiment whenever the run came back medium — while still
    passing or failing plausibly.
    """
    if not assertion.risks:
        return None
    return max((r.system_rating for r in assertion.risks), key=lambda r: RISK_RANK[r])


def relevant_assertions(engagement: AuditEngagement, line_item_type: str) -> list[Assertion]:
    return [
        a.assertion for a in audit_area(engagement, line_item_type).assertions if a.relevant
    ]


def procedures_for_assertion(
    engagement: AuditEngagement, line_item_type: str, assertion: Assertion
) -> list[Procedure]:
    """The procedures responding to any risk on one assertion."""
    area = audit_area(engagement, line_item_type)
    risk_ids = {r.id for r in assertion_of(engagement, line_item_type, assertion).risks}
    return [p for p in area.procedures if risk_ids & set(p.risk_ids)]


def strongest_evidence(procedures: list[Procedure]) -> EvidenceStrength | None:
    """The most persuasive evidence strength in a procedure set (ISA 330.7).

    AI suggestions carry no assessed strength (SPEC 13) and are skipped: an unapproved
    suggestion is not evidence the plan has yet.
    """
    assessed = [p.evidence_strength for p in procedures if p.evidence_strength is not None]
    if not assessed:
        return None
    return max(assessed, key=lambda s: RISK_RANK[RiskLevel(s.value)])
