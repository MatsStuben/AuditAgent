"""Auditor feedback → candidate methodology rule (SPEC 19).

A separate pipeline from audit generation. Nothing here feeds back into the engagement's audit
work: the output is a `RuleProposal` addressed to a human methodology owner, who approves,
rejects or edits it. **No static config file is ever written.** That is the whole point of the
separation — a firm's methodology does not change because one auditor disagreed once, and an
engine that quietly rewrote its own rules would be impossible to audit.

The model is given what SPEC 19 lists and nothing more: the original system proposal, what the
auditor changed it to, the reason they gave, and the engagement context around it. Feedback
outlives the object it describes (SPEC 18) — re-analysing an area discards its risks while the
record survives — so a description that no longer resolves says so rather than raising. The
`before`/`after` snapshot still carries the substance of the override in that case.

Two boundaries are deterministic, decided here rather than by the model:

**Only judgement overrides are analysable.** An assertion, a risk or a procedure is a conclusion
the engine reached and the auditor disagreed with — the thing SPEC 19 exists to learn from.
Revising the company context or the figures is *new input*, not a disagreement; what follows from
it is dependency logic the engine already owns (SPEC 17). Generalising one would invite rules
about how the engine should respond to its own inputs.

**The context comes from the record, not from live state.** `AuditorFeedback.engagement_context`
holds the circumstances as they stood when the override was made. Facts are re-extracted and
figures revised as an engagement progresses, and letting a later state reach a proposal
attributed to an older override would misdescribe what the auditor decided.
"""

import logging

from src.llm.client import LLMClient, LLMTask
from src.llm.prompts import GENERALIZE_FEEDBACK
from src.llm.schemas import FeedbackClassificationOutput, MethodologyRuleProposalOutput
from src.models.audit_objects import AssertionAssessment, Procedure, RiskAssessment
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment
from src.models.feedback import AuditorFeedback, RuleProposal, RuleProposalStatus

logger = logging.getLogger(__name__)

RULE_PROPOSAL_ID_PREFIX = "rule"

#: Overrides of a system *judgement*. Engagement-input edits are excluded — see the module
#: docstring — and the UI should offer no analysis action for them.
ANALYSABLE_OBJECT_TYPES = frozenset(
    {"assertion_assessment", "risk_assessment", "procedure"}
)

UNRESOLVED = (
    "This object is no longer in the audit file; the override snapshot is all that remains."
)


class FeedbackGeneralizationError(ValueError):
    """The feedback does not belong to this engagement, or is not a judgement override."""


def is_analysable(feedback: AuditorFeedback) -> bool:
    """Whether this record can become a methodology proposal (SPEC 19). Deterministic."""
    return feedback.object_type in ANALYSABLE_OBJECT_TYPES


# --- describing what was overridden --------------------------------------------------------


def _locate(
    engagement: AuditEngagement, object_id: str
) -> tuple[FinancialLineItemAssessment, object] | None:
    """Find the overridden object and the area it belongs to, if it still exists."""
    for area in engagement.line_items:
        for assertion in area.assertions:
            if assertion.id == object_id:
                return area, assertion
            for risk in assertion.risks:
                if risk.id == object_id:
                    return area, risk
        for procedure in area.procedures:
            if procedure.id == object_id:
                return area, procedure
    return None


def _describe_risk(area: FinancialLineItemAssessment, risk: RiskAssessment) -> str:
    assertion = next((a for a in area.assertions if a.id == risk.assertion_id), None)
    return (
        f"Audit area: {area.line_item_type}\n"
        f"Assertion: {assertion.assertion.value if assertion else 'unknown'}\n"
        f"Risk: {risk.risk_description}\n"
        f"System assessment: likelihood {risk.likelihood.value}, magnitude "
        f"{risk.magnitude.value}, rating {risk.system_rating.value}\n"
        f"System rationale: {risk.rationale}"
    )


def _describe_assertion(
    area: FinancialLineItemAssessment, assertion: AssertionAssessment
) -> str:
    return (
        f"Audit area: {area.line_item_type}\n"
        f"Assertion: {assertion.assertion.value}\n"
        f"System conclusion: {'relevant' if assertion.relevant else 'not relevant'}\n"
        f"System rationale: {assertion.rationale}"
    )


def _describe_procedure(area: FinancialLineItemAssessment, procedure: Procedure) -> str:
    return (
        f"Audit area: {area.line_item_type}\n"
        f"Procedure: {procedure.name} ({procedure.procedure_id or 'AI suggestion'})\n"
        f"Responds to: {', '.join(procedure.risk_ids)}\n"
        f"Why it was selected: {procedure.rationale}"
    )


def describe_overridden_object(engagement: AuditEngagement, feedback: AuditorFeedback) -> str:
    """The system's original proposal, as far as it still exists."""
    located = _locate(engagement, feedback.object_id)
    if located is None:
        return UNRESOLVED

    area, obj = located
    if isinstance(obj, RiskAssessment):
        return _describe_risk(area, obj)
    if isinstance(obj, AssertionAssessment):
        return _describe_assertion(area, obj)
    return _describe_procedure(area, obj)


def _format_change(values: dict) -> str:
    if not values:
        return "nothing recorded"
    return ", ".join(f"{key}: {value}" for key, value in values.items())


def _format_amount(value: object) -> str:
    return f"{value:,.0f}" if isinstance(value, int | float) else "not available"


def format_engagement_context(snapshot: dict) -> str:
    """The circumstances as they stood when the override was recorded (SPEC 18).

    Read from `AuditorFeedback.engagement_context`, whose shape is built by
    `engine.recompute.engagement_context`. Tolerant of missing keys: a record written before a
    field existed should still be analysable, just with less to go on.
    """
    facts = snapshot.get("company_facts") or []
    fact_lines = (
        "\n".join(f"- {f['fact_type']}: {f['value']}" for f in facts) or "None extracted."
    )
    area = snapshot.get("audit_area") or {}
    area_lines = (
        f"\nAudit area: {area['line_item_type']}, amount {_format_amount(area.get('cy'))} "
        f"({area.get('amount_to_materiality_ratio', 0):.1f}x materiality)"
        if area
        else ""
    )
    return (
        f"Company: {snapshot.get('company', 'not recorded')}\n"
        f"Materiality: {_format_amount(snapshot.get('materiality'))}"
        f"{area_lines}\n\n"
        f"Company facts:\n{fact_lines}"
    )


def build_user_message(engagement: AuditEngagement, feedback: AuditorFeedback) -> str:
    """The four inputs SPEC 19 names, and no more.

    Deliberately excludes the rest of the audit file. The question is whether *this* reasoning
    generalises, and showing unrelated areas would invite a rule drawn from work the auditor
    never commented on.

    The engagement rung comes from the record's own snapshot, so the model sees what the
    auditor saw rather than whatever the file has since become.
    """
    return f"""\
Original system proposal:

{describe_overridden_object(engagement, feedback)}

Auditor change:
- from — {_format_change(feedback.before)}
- to — {_format_change(feedback.after)}

Reason the auditor gave:
{feedback.reason.strip() or "None given."}

Engagement context, as it stood when the override was made:
{format_engagement_context(feedback.engagement_context)}"""


# --- the call -------------------------------------------------------------------------------


def generalize_feedback(
    feedback: AuditorFeedback, engagement: AuditEngagement, *, client: LLMClient
) -> RuleProposal | None:
    """Classify one override, returning a candidate rule or None (SPEC 19).

    None means the model judged the reasoning engagement-specific — the expected answer for
    most overrides, and the one the prompt prefers when uncertain. A proposal is appended to
    `engagement.rule_proposals` and returned; unlike the pipeline's outputs these accumulate
    rather than replace, because each one refers to a different override and none of them is
    superseded by the next.

    Re-analysing an existing proposal's feedback returns that proposal without spending a
    call: two pending proposals for one override would be the same question asked twice of a
    reviewer.

    The argument identifies the record; the engagement supplies it. Everything below is read
    from the engagement's own copy, so a caller holding a deserialised or edited record with
    the same ID cannot have a proposal filed against the real record's ID while the model was
    shown something else.
    """
    record = next((f for f in engagement.feedback if f.id == feedback.id), None)
    if record is None:
        raise FeedbackGeneralizationError(
            f"{feedback.id} is not a feedback record of this engagement"
        )
    if not is_analysable(record):
        raise FeedbackGeneralizationError(
            f"{record.id} records a change to the engagement's {record.object_id}, which is new "
            f"input rather than a judgement the auditor overrode; nothing to generalise"
        )

    existing = next(
        (p for p in engagement.rule_proposals if p.source_feedback_id == record.id), None
    )
    if existing is not None:
        return existing

    output = client.parse(
        task=LLMTask.GENERALIZE_FEEDBACK,
        system=GENERALIZE_FEEDBACK,
        user=build_user_message(engagement, record),
        output_format=FeedbackClassificationOutput,
    )
    classification = output.classification

    if not isinstance(classification, MethodologyRuleProposalOutput):
        logger.info(
            "%s judged engagement-specific: %s", record.id, classification.reason.strip()
        )
        return None

    condition = classification.condition.strip()
    action = classification.action.strip()
    if not condition or not action:
        # A rule with no condition applies always, and one with no action asks for nothing.
        # Either way a reviewer has nothing to approve, so it is dropped rather than filed.
        logger.warning(
            "discarding a rule proposal for %s: condition or action is empty", record.id
        )
        return None

    proposal = RuleProposal(
        id=engagement.next_id(RULE_PROPOSAL_ID_PREFIX),
        condition=condition,
        action=action,
        reason=classification.reason.strip(),
        source_feedback_id=record.id,
        status=RuleProposalStatus.PENDING_REVIEW,
    )
    engagement.rule_proposals.append(proposal)
    return proposal
