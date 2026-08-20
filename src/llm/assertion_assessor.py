"""Assertion relevance for a material audit area (SPEC 10, ISA 315.29).

The candidate assertions come from configuration; the *relevance* decision is the bounded
LLM judgement. The model can only rule on candidates it was given, and every candidate gets
a verdict — otherwise the ISA 315.29 coverage check in M10 could not tell a considered
"not relevant" from an assertion nobody looked at.

Assessment applies only to line items that are **both** material and audit areas (SPEC 6, 8,
10). Anything else returns no assessments and makes no call.
"""

import logging

from src.config.loader import StaticConfig, get_config
from src.llm.client import LLMClient, LLMTask
from src.llm.prompts import ASSESS_ASSERTIONS
from src.llm.schemas import AssertionRelevanceOutput
from src.models.audit_objects import Assertion, AssertionAssessment
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment
from src.models.isa import LinkedObjectType

logger = logging.getLogger(__name__)

ASSERTION_ID_PREFIX = "assertion"

NO_VERDICT_RATIONALE = (
    "No verdict was returned for this assertion. Defaulted to not relevant — "
    "auditor review required."
)
MISSING_RATIONALE = "No rationale was provided for this verdict."


class AssertionAssessmentError(ValueError):
    """Raised when assertion assessment is attempted before its inputs exist."""


def _format_facts(engagement: AuditEngagement) -> str:
    if not engagement.company_facts:
        return "None extracted."
    return "\n".join(
        f"- {f.id} ({f.fact_type}): {f.value} — {f.rationale}" for f in engagement.company_facts
    )


def build_user_message(
    line_item: FinancialLineItemAssessment,
    engagement: AuditEngagement,
    candidates: list[Assertion],
) -> str:
    """The bounded context SPEC 10 specifies — this audit area only.

    Other line items are excluded deliberately: they cannot bear on whether an assertion is
    relevant *here*, and including them would dilute the judgement.
    """
    metrics = line_item.metrics
    materiality = engagement.materiality
    pct = "not available" if metrics.yoy_change_pct is None else f"{metrics.yoy_change_pct:+.1f}%"

    return f"""\
Audit area: {line_item.line_item_type}

Current year amount: {line_item.cy:,.0f}
Prior year amount: {line_item.py:,.0f}
Year-on-year movement: {metrics.yoy_change:+,.0f} ({pct})
Materiality: {materiality.amount:,.0f}
Amount as a multiple of materiality: {metrics.amount_to_materiality_ratio:.1f}x

Company context:
{engagement.company_context.strip() or "None provided."}

Company facts:
{_format_facts(engagement)}

Candidate assertions:
{chr(10).join(f"- {a.value}" for a in candidates)}"""


def assess_assertions(
    line_item: FinancialLineItemAssessment,
    engagement: AuditEngagement,
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> list[AssertionAssessment]:
    """Return one `AssertionAssessment` per candidate assertion, in profile order.

    Does not assign to `line_item.assertions` — the caller does. Consumes assertion IDs from
    the engagement's monotonic counter.
    """
    config = config or get_config()
    candidates = config.candidate_assertions(line_item.line_item_type)
    if not candidates:
        # Not an audit area: no methodology to apply, so no call to make.
        return []

    if engagement.materiality is None or line_item.metrics is None or line_item.material is None:
        raise AssertionAssessmentError(
            f"{line_item.line_item_type} must be scoped before assertions are assessed"
        )

    if not line_item.material:
        # SPEC 6/8/10 restrict assertion assessment to *material* audit areas. Guarded here
        # as well as in the pipeline so no caller can spend a call on an out-of-scope area
        # or leave assertions behind on one.
        return []

    output = client.parse(
        task=LLMTask.ASSESS_ASSERTIONS,
        system=ASSESS_ASSERTIONS,
        user=build_user_message(line_item, engagement, candidates),
        output_format=AssertionRelevanceOutput,
    )

    verdicts = _collect_verdicts(output, candidates, line_item.line_item_type)
    known_fact_ids = {f.id for f in engagement.company_facts}
    isa_refs = config.isa_refs_for(LinkedObjectType.ASSERTION_ASSESSMENT)

    assessments: list[AssertionAssessment] = []
    for candidate in candidates:
        verdict = verdicts.get(candidate)
        if verdict is None:
            logger.warning(
                "no verdict returned for %s/%s; defaulting to not relevant",
                line_item.line_item_type,
                candidate.value,
            )
            assessments.append(
                AssertionAssessment(
                    id=engagement.next_id(ASSERTION_ID_PREFIX),
                    line_item_id=line_item.id,
                    assertion=candidate,
                    relevant=False,
                    rationale=NO_VERDICT_RATIONALE,
                    isa_refs=list(isa_refs),
                )
            )
            continue

        assessments.append(
            AssertionAssessment(
                id=engagement.next_id(ASSERTION_ID_PREFIX),
                line_item_id=line_item.id,
                assertion=candidate,
                relevant=verdict.relevant,
                rationale=verdict.rationale.strip() or MISSING_RATIONALE,
                supporting_fact_ids=_validated_fact_ids(
                    verdict.supporting_fact_ids, known_fact_ids, candidate
                ),
                isa_refs=list(isa_refs),
            )
        )
    return assessments


def _collect_verdicts(
    output: AssertionRelevanceOutput, candidates: list[Assertion], area: str
) -> dict[Assertion, object]:
    """Index verdicts by assertion, discarding anything outside the candidate list."""
    verdicts: dict[Assertion, object] = {}
    allowed = set(candidates)
    for verdict in output.assertions:
        if verdict.assertion not in allowed:
            # The model ruled on something it was not asked about — e.g. valuation for
            # cash. Accepting it would put an assertion in the file that the area's
            # methodology says does not apply.
            logger.warning(
                "discarding verdict for %s: not a candidate assertion for %s",
                verdict.assertion.value,
                area,
            )
            continue
        if verdict.assertion in verdicts:
            logger.warning(
                "duplicate verdict for %s/%s; keeping the first", area, verdict.assertion.value
            )
            continue
        verdicts[verdict.assertion] = verdict
    return verdicts


def _validated_fact_ids(
    returned: list[str], known: set[str], assertion: Assertion
) -> list[str]:
    """Keep only references to facts that actually exist (SPEC 14).

    A dangling ID would break traceability silently, so it is dropped rather than stored.
    """
    validated = [fact_id for fact_id in returned if fact_id in known]
    unknown = [fact_id for fact_id in returned if fact_id not in known]
    if unknown:
        logger.warning(
            "dropping unknown fact ids %s cited for %s", sorted(unknown), assertion.value
        )
    return validated
