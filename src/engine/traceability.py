"""Forward traceability: procedure → risk → assertion → line item → evidence → ISA (SPEC 14).

Deterministic and read-only. Every link is resolved through the stored IDs, never inferred from
rationale text — free text is what the chain exists to replace.

One procedure produces one chain *per risk it addresses*. A procedure answering two risks is not
two procedures (SPEC 4), but it does sit on two distinct audit trails: the assertion, the
evidence and the reasoning differ above the risk even though everything below it is shared. The
fan-out is a property of the audit, not a gap in the model (SPEC 14).

A broken link raises. Returning a chain with a missing rung would present an untraceable
procedure as traceable, which is the one thing this module exists to prevent.
"""

from pydantic import BaseModel

from src.models.audit_objects import AssertionAssessment, Procedure, RiskAssessment
from src.models.engagement import (
    AuditEngagement,
    CompanyFact,
    DerivedMetrics,
    FinancialLineItemAssessment,
)


class TraceabilityError(ValueError):
    """A link in the chain does not resolve."""


class TraceChain(BaseModel):
    """One complete audit trail, from a procedure back to the ISA requirements it serves.

    Holds the runtime objects themselves rather than copies of their fields, so a caller
    reading `chain.assertion.rationale` sees current state and the UI needs no second lookup.
    `line_item` therefore also carries the area's other assertions and procedures; they are
    reachable, not part of this chain.
    """

    procedure: Procedure
    risk: RiskAssessment
    assertion: AssertionAssessment
    line_item: FinancialLineItemAssessment
    metrics: DerivedMetrics | None = None
    """The area's derived metrics — the quantitative half of SPEC 14's evidence rung. None
    only on an unscoped engagement, which the pipeline never produces procedures for."""
    facts: list[CompanyFact] = []
    """Facts cited by the assertion and by the risk, in that order, deduplicated. Empty is
    legitimate: not every judgement rests on an extracted fact."""
    isa_chain: list[str] = []
    """Requirement IDs in chain order — assertion, then risk, then procedure — as recorded on
    the objects at the time they were created. Read from the objects rather than looked up
    afresh, so the chain shows what the audit file actually claims."""


def trace_procedure(procedure: Procedure, engagement: AuditEngagement) -> list[TraceChain]:
    """Return one chain per risk the engagement records against this procedure, in that order.

    `procedure` identifies the procedure to trace; the links come from the engagement's own
    copy of it, so a stale or edited argument cannot change what the chain reports.

    Raises `TraceabilityError` if the procedure does not belong to the engagement, or if any
    risk, assertion or cited fact fails to resolve.
    """
    line_item, stored = _owning_procedure(procedure, engagement)
    facts_by_id = {fact.id: fact for fact in engagement.company_facts}

    chains = []
    for risk_id in stored.risk_ids:
        risk = line_item.risk(risk_id)
        if risk is None:
            raise TraceabilityError(
                f"{stored.id} addresses {risk_id}, which is not a risk in "
                f"{line_item.line_item_type}"
            )
        assertion = _assertion_for(risk, line_item)
        chains.append(
            TraceChain(
                procedure=stored,
                risk=risk,
                assertion=assertion,
                line_item=line_item,
                metrics=line_item.metrics,
                facts=_cited_facts(assertion, risk, facts_by_id),
                isa_chain=_isa_chain(assertion, risk, stored),
            )
        )
    return chains


def _owning_procedure(
    procedure: Procedure, engagement: AuditEngagement
) -> tuple[FinancialLineItemAssessment, Procedure]:
    """The area holding this procedure, and the area's own copy of it.

    Matched by ID rather than identity so a procedure round-tripped through serialisation
    still traces. IDs are engagement-unique (`AuditEngagement.next_id`), so at most one area
    can match.

    The *stored* procedure is what the chain is built from, and the argument serves only to
    identify it. A caller can hold a deserialised or edited object with the same ID and
    different `risk_ids` or `isa_refs`; tracing that would report links the audit file does
    not contain. The chain shows the engagement's state, not the caller's.
    """
    for line_item in engagement.line_items:
        for stored in line_item.procedures:
            if stored.id == procedure.id:
                return line_item, stored
    raise TraceabilityError(f"{procedure.id} does not belong to any line item in this engagement")


def _assertion_for(
    risk: RiskAssessment, line_item: FinancialLineItemAssessment
) -> AssertionAssessment:
    for assertion in line_item.assertions:
        if assertion.id == risk.assertion_id:
            return assertion
    raise TraceabilityError(
        f"{risk.id} names assertion {risk.assertion_id}, which is not in "
        f"{line_item.line_item_type}"
    )


def _cited_facts(
    assertion: AssertionAssessment,
    risk: RiskAssessment,
    facts_by_id: dict[str, CompanyFact],
) -> list[CompanyFact]:
    """Assertion facts then risk facts, deduplicated, order preserved.

    A dangling reference raises. The services validate fact IDs before storing them
    (SPEC 14), so one appearing here means state has been corrupted since — reporting the
    chain without it would quietly drop the evidence the conclusion rests on.
    """
    facts = []
    seen = set()
    for fact_id in [*assertion.supporting_fact_ids, *risk.supporting_fact_ids]:
        if fact_id in seen:
            continue
        seen.add(fact_id)
        fact = facts_by_id.get(fact_id)
        if fact is None:
            raise TraceabilityError(
                f"{fact_id} is cited in the chain for {risk.id} but is not a fact on this "
                f"engagement"
            )
        facts.append(fact)
    return facts


def _isa_chain(
    assertion: AssertionAssessment, risk: RiskAssessment, procedure: Procedure
) -> list[str]:
    chain = []
    for ref in [*assertion.isa_refs, *risk.isa_refs, *procedure.isa_refs]:
        if ref not in chain:
            chain.append(ref)
    return chain
