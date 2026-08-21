"""Auditor overrides and the recomputation each one triggers (SPEC 17, 18).

One function per override type. Each records an `AuditorFeedback` before it mutates anything,
then recomputes only its own dependency subtree — never the whole engagement.

Three things this module holds to:

**The original system output survives.** `system_rating`, `likelihood` and `magnitude` are the
engine's conclusion and are never written here. A risk-rating override is deliberately routed
around area re-analysis for exactly that reason (SPEC 17): re-analysing would replace the risk
and destroy what the auditor is disagreeing with.

**Mutation is as narrow as the change allows.** Audit-area analysis is an area-level call, but
a risk-rating override re-selects procedures for that risk alone and merges the result into the
area's existing list. Procedures whose `risk_ids` do not include the changed risk keep their
identity and their links; a procedure that also answers other risks keeps those too.

**Coverage is not stored, so there is nothing to invalidate.** SPEC 17 ends every recompute
with "rerun ISA coverage"; `check_isa_coverage` is a pure read over live state, so callers get
a current report by asking for one. Caching it here would be the one piece of state these
functions could leave stale.

**An override either happens or it does not.** Every function that makes an LLM call restores
what it changed if the call fails, and appends its `AuditorFeedback` only once the recompute
has succeeded. A half-applied override is worse than a failed one: it puts an engagement in
front of the auditor holding a new rating with the old work beneath it, or a new context with
no work at all, while the log claims the change went through. What is *not* rolled back is the
ID counter — reusing an ID a failed run consumed is precisely what SPEC 14 forbids.

Deterministic except where noted: only relevance→relevant, a context change and a scope change
make LLM calls.
"""

import logging

from src.config.loader import StaticConfig, get_config
from src.engine.catalogue import filter_catalogue
from src.engine.materiality import calculate_materiality
from src.engine.pipeline import clear_area, run_area
from src.engine.scoping import scope_line_items
from src.llm.client import LLMClient
from src.llm.context_extractor import extract_company_facts
from src.llm.procedure_selector import PROCEDURE_ID_PREFIX, select_procedures
from src.models.audit_objects import (
    AssertionAssessment,
    Procedure,
    ProcedureSource,
    RiskAssessment,
    RiskLevel,
)
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment
from src.models.feedback import AuditorFeedback
from src.models.isa import LinkedObjectType

logger = logging.getLogger(__name__)

FEEDBACK_ID_PREFIX = "feedback"


class RecomputeError(ValueError):
    """An override names something the engagement does not contain, or is not permitted."""


# --- feedback ----------------------------------------------------------------------------


def engagement_context(
    engagement: AuditEngagement, line_item: FinancialLineItemAssessment | None
) -> dict:
    """The circumstances the auditor was judging against, small enough to keep (SPEC 18, 19).

    Stored on the record so a later reader — `llm.feedback_generalizer` above all — reasons
    about the engagement as it stood when the override was made, not as it stands now. Facts
    are re-extracted and figures revised as an engagement progresses, and attributing a
    proposal to an old override while feeding the model newer circumstances would misdescribe
    what the auditor actually decided.

    Bounded to the overridden object's own audit area. The whole audit file would put work the
    auditor never commented on into a judgement about their reasoning.
    """
    snapshot: dict = {
        "company": engagement.company,
        "materiality": engagement.materiality.amount if engagement.materiality else None,
        "company_facts": [
            {"id": f.id, "fact_type": f.fact_type, "value": f.value}
            for f in engagement.company_facts
        ],
    }
    if line_item is not None:
        snapshot["audit_area"] = {
            "line_item_type": line_item.line_item_type,
            "cy": line_item.cy,
            "amount_to_materiality_ratio": (
                line_item.metrics.amount_to_materiality_ratio if line_item.metrics else None
            ),
        }
    return snapshot


def record_feedback(
    engagement: AuditEngagement,
    *,
    object_type: str,
    object_id: str,
    before: dict,
    after: dict,
    reason: str,
    line_item: FinancialLineItemAssessment | None = None,
) -> AuditorFeedback:
    """Append one override record and return it (SPEC 18).

    `before` is captured from live state before the mutation; the record is appended after the
    recompute succeeds, so the log never claims a change that did not land.

    The context snapshot is taken here, at commit time, and that is the same state the auditor
    judged against: no judgement override touches the facts, the figures or materiality. The
    two engagement-input edits do move them, and their snapshot is therefore of the revised
    state — which is correct for a record of new input, and moot besides, since those records
    are not analysable for methodology (SPEC 19).
    """
    feedback = AuditorFeedback(
        id=engagement.next_id(FEEDBACK_ID_PREFIX),
        object_type=object_type,
        object_id=object_id,
        before=before,
        after=after,
        reason=reason,
        engagement_context=engagement_context(engagement, line_item),
    )
    engagement.feedback.append(feedback)
    return feedback


# --- rollback ------------------------------------------------------------------------------


def _capture(engagement: AuditEngagement) -> tuple:
    """Everything an engagement-wide recompute may replace, so a failure can put it back.

    Shallow by design: it records *which objects* the engagement points at, not their
    contents. That is enough because these paths replace lists and rebind fields rather than
    editing existing objects in place — and it means a rollback restores the original objects
    themselves, so a UI still holding one is not left pointing at an orphaned copy.
    """
    return (
        engagement.company_context,
        engagement.company_facts,
        engagement.materiality,
        [
            (item, item.cy, item.metrics, item.material, item.is_audit_area,
             item.assertions, item.procedures)
            for item in engagement.line_items
        ],
    )


def _restore(engagement: AuditEngagement, state: tuple) -> None:
    context, facts, materiality, items = state
    engagement.company_context = context
    engagement.company_facts = facts
    engagement.materiality = materiality
    for item, cy, metrics, material, is_audit_area, assertions, procedures in items:
        item.cy = cy
        item.metrics = metrics
        item.material = material
        item.is_audit_area = is_audit_area
        item.assertions = assertions
        item.procedures = procedures


# --- lookups -----------------------------------------------------------------------------


def _find_risk(
    engagement: AuditEngagement, risk_id: str
) -> tuple[FinancialLineItemAssessment, RiskAssessment]:
    for line_item in engagement.line_items:
        risk = line_item.risk(risk_id)
        if risk is not None:
            return line_item, risk
    raise RecomputeError(f"{risk_id} is not a risk in this engagement")


def _find_assertion(
    engagement: AuditEngagement, assertion_id: str
) -> tuple[FinancialLineItemAssessment, AssertionAssessment]:
    for line_item in engagement.line_items:
        for assertion in line_item.assertions:
            if assertion.id == assertion_id:
                return line_item, assertion
    raise RecomputeError(f"{assertion_id} is not an assertion in this engagement")


def _find_procedure(
    engagement: AuditEngagement, procedure_id: str
) -> tuple[FinancialLineItemAssessment, Procedure]:
    for line_item in engagement.line_items:
        for procedure in line_item.procedures:
            if procedure.id == procedure_id:
                return line_item, procedure
    raise RecomputeError(f"{procedure_id} is not a procedure in this engagement")


# --- scoped procedure re-selection --------------------------------------------------------


def _merge_procedures(
    line_item: FinancialLineItemAssessment,
    changed_risk_ids: set[str],
    fresh: list[Procedure],
) -> None:
    """Fold a scoped selection back into the area's procedure list, in place (SPEC 17).

    The affected closure is the procedures naming a changed risk. Each loses that link and
    survives on its remaining ones; it is dropped only when the changed risks were the whole
    of its reason to exist. Everything else keeps its identity untouched — an override on one
    assertion must not silently replace the work answering another.

    A fresh selection naming a catalogue entry the area already holds re-links that same
    object rather than creating a second one: a procedure exists once, however many risks it
    answers.
    """
    retained: list[Procedure] = []
    for procedure in line_item.procedures:
        remaining = [rid for rid in procedure.risk_ids if rid not in changed_risk_ids]
        if not remaining:
            logger.info(
                "dropping %s from %s: it answered only the changed risks",
                procedure.id,
                line_item.line_item_type,
            )
            continue
        procedure.risk_ids = remaining
        retained.append(procedure)

    by_catalogue = {p.procedure_id: p for p in retained if p.procedure_id is not None}
    merged = list(retained)
    for procedure in fresh:
        existing = by_catalogue.get(procedure.procedure_id) if procedure.procedure_id else None
        if existing is None:
            merged.append(procedure)
            continue
        existing.risk_ids += [r for r in procedure.risk_ids if r not in existing.risk_ids]
        # The fresh call reasoned about the changed rating; its rationale is the current one.
        existing.rationale = procedure.rationale

    line_item.procedures = merged


def _detach_risks(
    line_item: FinancialLineItemAssessment, dropped_risk_ids: set[str]
) -> None:
    """Remove references to risks that no longer exist. No LLM call, nothing reselected."""
    _merge_procedures(line_item, dropped_risk_ids, [])


# --- overrides ----------------------------------------------------------------------------


def override_risk_rating(
    engagement: AuditEngagement,
    risk_id: str,
    final_rating: RiskLevel,
    reason: str,
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> AuditorFeedback | None:
    """Change a risk's final rating and re-select the procedures answering it (SPEC 17).

    One LLM call, scoped to this risk. `system_rating` is untouched, so the engine's original
    conclusion stays visible beside the auditor's. Returns None if the rating is unchanged —
    an override that changes nothing is not feedback.

    `is_overridden` states whether the *current* rating departs from the system's, so setting a
    risk back to its system rating clears the marker and its reason. Both moves stay in the
    feedback log; what would be wrong is the live risk card claiming a disagreement that has
    since been withdrawn.

    If selection fails the rating is put back and nothing is recorded.
    """
    line_item, risk = _find_risk(engagement, risk_id)
    if risk.final_rating is final_rating:
        return None

    before = {"final_rating": risk.final_rating.value}
    previous = (risk.final_rating, risk.is_overridden, risk.override_reason)

    risk.final_rating = final_rating
    risk.is_overridden = final_rating is not risk.system_rating
    risk.override_reason = reason if risk.is_overridden else None

    try:
        fresh = select_procedures(
            line_item, engagement, client=client, config=config, risk_ids={risk_id}
        )
    except Exception:
        risk.final_rating, risk.is_overridden, risk.override_reason = previous
        raise

    _merge_procedures(line_item, {risk_id}, fresh)
    return record_feedback(
        engagement,
        object_type="risk_assessment",
        object_id=risk_id,
        before=before,
        after={"final_rating": final_rating.value},
        reason=reason,
        line_item=line_item,
    )


def override_assertion_relevance(
    engagement: AuditEngagement,
    assertion_id: str,
    relevant: bool,
    reason: str,
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> AuditorFeedback | None:
    """Change whether an assertion is relevant, and recompute what depends on it (SPEC 17).

    The two directions are not symmetrical. Ruling an assertion out is deterministic: its
    risks are dropped and procedures lose those links. Ruling one in needs risks that never
    existed and cannot be derived, so the area is re-analysed — which **replaces every
    assertion and risk in the area**, discarding any override held on them (SPEC 17). The UI
    must warn before calling this direction.

    If re-analysis still judges the assertion irrelevant, the auditor's verdict is reapplied
    and the assertion is left with no risks. Coverage then reports the ISA 315.28(b)/31 gap,
    which is the honest outcome — better than fabricating a risk the engine does not hold.
    """
    line_item, assertion = _find_assertion(engagement, assertion_id)
    if assertion.relevant is relevant:
        return None

    def commit() -> AuditorFeedback:
        return record_feedback(
            engagement,
            object_type="assertion_assessment",
            object_id=assertion_id,
            before={"relevant": not relevant},
            after={"relevant": relevant},
            reason=reason,
            line_item=line_item,
        )

    if not relevant:
        dropped = {risk.id for risk in assertion.risks}
        assertion.relevant = False
        assertion.risks = []
        _detach_risks(line_item, dropped)
        return commit()

    target = assertion.assertion
    # All-or-nothing: `run_area` leaves the area untouched if either call fails, so a failure
    # here means the assertion is still irrelevant and no feedback is recorded.
    run_area(line_item, engagement, client=client, config=config)
    reanalysed = next((a for a in line_item.assertions if a.assertion is target), None)
    if reanalysed is None:
        raise RecomputeError(
            f"re-analysis of {line_item.line_item_type} returned no verdict on {target.value}"
        )
    if not reanalysed.relevant:
        logger.warning(
            "re-analysis still judges %s/%s irrelevant; keeping the auditor's verdict, which "
            "leaves it with no risks",
            line_item.line_item_type,
            target.value,
        )
        reanalysed.relevant = True
    return commit()


def add_catalogue_procedure(
    engagement: AuditEngagement,
    risk_id: str,
    catalogue_id: str,
    reason: str,
    config: StaticConfig | None = None,
) -> AuditorFeedback:
    """Attach an approved catalogue procedure to a risk (SPEC 17). Deterministic.

    The entry must be one the catalogue offers for this area *and* this risk's assertion — the
    same constraint the model is held to, so an auditor addition cannot record a link approved
    methodology does not support. Where the area already holds that procedure, the risk is
    added to it rather than a second copy being created.
    """
    config = config or get_config()
    line_item, risk = _find_risk(engagement, risk_id)
    assertion = next(a for a in line_item.assertions if a.id == risk.assertion_id)

    allowed = filter_catalogue(
        line_item.line_item_type,
        assertion.assertion,
        catalogue=config.procedure_catalogue,
    )
    entry = next((p for p in allowed if p.id == catalogue_id), None)
    if entry is None:
        raise RecomputeError(
            f"{catalogue_id} is not catalogued for {line_item.line_item_type}/"
            f"{assertion.assertion.value}"
        )

    existing = next(
        (p for p in line_item.procedures if p.procedure_id == catalogue_id), None
    )
    if existing is not None:
        if risk_id in existing.risk_ids:
            raise RecomputeError(f"{catalogue_id} already responds to {risk_id}")
        before = {"risk_ids": list(existing.risk_ids)}
        existing.risk_ids = [*existing.risk_ids, risk_id]
        return record_feedback(
            engagement,
            object_type="procedure",
            object_id=existing.id,
            before=before,
            after={"risk_ids": list(existing.risk_ids)},
            reason=reason,
            line_item=line_item,
        )

    procedure = Procedure(
        id=engagement.next_id(PROCEDURE_ID_PREFIX),
        risk_ids=[risk_id],
        procedure_id=entry.id,
        name=entry.name,
        description=entry.description,
        procedure_type=entry.procedure_type,
        evidence_strength=entry.evidence_strength,
        rationale=reason,
        source=ProcedureSource.CATALOGUE,
        approved=True,
        isa_refs=list(config.isa_refs_for(LinkedObjectType.PROCEDURE)),
    )
    line_item.procedures.append(procedure)
    return record_feedback(
        engagement,
        object_type="procedure",
        object_id=procedure.id,
        before={},
        after={"procedure_id": entry.id, "risk_ids": [risk_id]},
        reason=reason,
        line_item=line_item,
    )


def remove_procedure(
    engagement: AuditEngagement, procedure_id: str, risk_id: str, reason: str
) -> AuditorFeedback:
    """Detach a procedure from one risk (SPEC 17). Deterministic.

    Removal is per risk, not per procedure: a procedure answering several risks is still doing
    the other work. It is deleted only when its last reference goes.
    """
    line_item, procedure = _find_procedure(engagement, procedure_id)
    if risk_id not in procedure.risk_ids:
        raise RecomputeError(f"{procedure_id} does not respond to {risk_id}")

    before = {"risk_ids": list(procedure.risk_ids)}
    remaining = [rid for rid in procedure.risk_ids if rid != risk_id]
    if remaining:
        procedure.risk_ids = remaining
    else:
        line_item.procedures = [p for p in line_item.procedures if p.id != procedure_id]

    return record_feedback(
        engagement,
        object_type="procedure",
        object_id=procedure_id,
        before=before,
        after={"risk_ids": remaining},
        reason=reason,
        line_item=line_item,
    )


def approve_procedure(
    engagement: AuditEngagement, procedure_id: str, reason: str
) -> AuditorFeedback:
    """Approve an AI-suggested procedure (SPEC 13, 17). Deterministic.

    Until this happens the suggestion does not close an ISA 330.6/7 gap, so approval is the
    act that makes it part of the plan.
    """
    line_item, procedure = _find_procedure(engagement, procedure_id)
    if procedure.source is not ProcedureSource.AI_SUGGESTION:
        raise RecomputeError(f"{procedure_id} is a catalogue procedure; approval does not apply")
    if procedure.approved:
        raise RecomputeError(f"{procedure_id} is already approved")

    procedure.approved = True
    return record_feedback(
        engagement,
        object_type="procedure",
        object_id=procedure_id,
        before={"approved": False},
        after={"approved": True},
        reason=reason,
        line_item=line_item,
    )


def update_company_context(
    engagement: AuditEngagement,
    company_context: str,
    reason: str = "",
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> AuditorFeedback | None:
    """Replace the company context and rerun everything that reads it (SPEC 17).

    Facts are re-extracted, then every in-scope audit area is re-analysed and its procedures
    re-selected: `1 + 2n` calls. Materiality and scoping are untouched — they read the
    financials, which have not changed.

    All of it or none of it. A failure part-way would otherwise leave the new context sitting
    above facts and assessments drawn from the old one, which is exactly the kind of file an
    auditor cannot tell is stale.
    """
    if company_context == engagement.company_context:
        return None

    before = {"company_context": engagement.company_context}
    state = _capture(engagement)

    try:
        engagement.company_context = company_context
        engagement.company_facts = extract_company_facts(engagement, client=client)
        for line_item in engagement.in_scope_audit_areas:
            run_area(line_item, engagement, client=client, config=config)
    except Exception:
        _restore(engagement, state)
        raise

    return record_feedback(
        engagement,
        object_type="engagement",
        object_id="company_context",
        before=before,
        after={"company_context": company_context},
        reason=reason,
    )


def update_financials(
    engagement: AuditEngagement,
    amounts: dict[str, float],
    reason: str = "",
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> AuditorFeedback | None:
    """Revise current-year figures, then re-scope and rerun what scope change requires.

    `amounts` maps line item type to a new CY amount. Materiality is recalculated and every
    line item re-scoped, because a PBT change moves the threshold under all of them (SPEC 17).

    Only areas whose scope *changed* run LLM calls: one entering runs both of its calls, one
    leaving is cleared deterministically. An area that stays in scope keeps its existing work
    even where its own figures moved — SPEC 17 ties the rerun to scope, and re-analysing on
    every figure edit would discard auditor overrides across the whole engagement. Where the
    auditor wants that work redone, `run_area` is the explicit way to ask for it.
    """
    config = config or get_config()
    unknown = set(amounts) - {li.line_item_type for li in engagement.line_items}
    if unknown:
        raise RecomputeError(f"{sorted(unknown)} are not line items in this engagement")

    changed = {
        line_item_type: amount
        for line_item_type, amount in amounts.items()
        if engagement.line_item(line_item_type).cy != amount
    }
    if not changed:
        return None

    before = {t: engagement.line_item(t).cy for t in changed}
    state = _capture(engagement)

    try:
        for line_item_type, amount in changed.items():
            engagement.line_item(line_item_type).cy = amount

        was_in_scope = {li.id for li in engagement.in_scope_audit_areas}
        engagement.materiality = calculate_materiality(engagement)
        scope_line_items(engagement, config)

        for line_item in engagement.line_items:
            in_scope_now = line_item.material is True and line_item.is_audit_area
            if in_scope_now and line_item.id not in was_in_scope:
                run_area(line_item, engagement, client=client, config=config)
            elif not in_scope_now and line_item.id in was_in_scope:
                clear_area(line_item)
    except Exception:
        # Restores the figures and the materiality they moved, not just the audit work: a
        # half-applied scope change is a threshold that no longer matches the file under it.
        _restore(engagement, state)
        raise

    return record_feedback(
        engagement,
        object_type="engagement",
        object_id="financials",
        before=before,
        after=dict(changed),
        reason=reason,
    )
