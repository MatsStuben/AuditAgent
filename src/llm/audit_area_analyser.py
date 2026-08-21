"""Audit area analysis: assertion relevance and risks in one call (SPEC 6.1, 10, 11).

The audit area is the bounded unit of LLM reasoning. One call returns a verdict for every
candidate assertion plus the risks nested under each relevant one, so calls scale with audit
areas rather than with assertions and risks.

The deterministic/LLM boundary is unchanged by the batching:

    relevance, risk description, likelihood, magnitude   <- LLM judgement
    system_rating                                        <- risk_matrix.json
    final_rating                                         <- starts equal; only an auditor moves it

`IdentifiedRiskOutput` has no rating field, so the model cannot supply one. `system_rating` is
never mutated afterwards, so the original system conclusion survives any override (SPEC 18).

Analysis runs only for a line item that is **both** material and an audit area (SPEC 6, 8, 10).
"""

import logging

from src.config.loader import StaticConfig, get_config
from src.engine.risk_matrix import derive_rating
from src.llm.client import LLMClient, LLMTask
from src.llm.formatting import (
    format_company_context,
    format_company_facts,
    format_financial_context,
)
from src.llm.prompts import ANALYSE_AUDIT_AREA
from src.llm.schemas import AssertionAnalysisOutput, AuditAreaAnalysisOutput
from src.models.audit_objects import Assertion, AssertionAssessment, RiskAssessment
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment
from src.models.isa import LinkedObjectType

logger = logging.getLogger(__name__)

ASSERTION_ID_PREFIX = "assertion"
RISK_ID_PREFIX = "risk"

#: SPEC 11 allows a second risk only where genuinely distinct. `maxItems` is not enforced by
#: the API, so the cap is applied here rather than assumed from the schema.
MAX_RISKS_PER_ASSERTION = 2

NO_VERDICT_RATIONALE = (
    "No verdict was returned for this assertion. Defaulted to not relevant — "
    "auditor review required."
)
MISSING_RATIONALE = "No rationale was provided."


class AuditAreaAnalysisError(ValueError):
    """Raised when analysis is attempted before its inputs exist."""


def build_user_message(
    line_item: FinancialLineItemAssessment,
    engagement: AuditEngagement,
    candidates: list[Assertion],
) -> str:
    """The bounded context SPEC 10 and 11 specify — this audit area only.

    Other line items are excluded deliberately: they cannot bear on whether an assertion is
    relevant *here* or what could go wrong *here*, and including them would both dilute the
    judgement and break the one-area-per-prompt rule (SPEC 6.1).
    """
    return f"""\
Audit area: {line_item.line_item_type}

{format_financial_context(line_item, engagement.materiality)}

Company context:
{format_company_context(engagement)}

Company facts:
{format_company_facts(engagement)}

Candidate assertions:
{chr(10).join(f"- {a.value}" for a in candidates)}"""


def analyse_audit_area(
    line_item: FinancialLineItemAssessment,
    engagement: AuditEngagement,
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> list[AssertionAssessment]:
    """Return one `AssertionAssessment` per candidate assertion, risks nested inside.

    Does not assign to `line_item.assertions` — the caller does. Consumes assertion and risk
    IDs from the engagement's monotonic counter.
    """
    config = config or get_config()
    candidates = config.candidate_assertions(line_item.line_item_type)
    if not candidates:
        # Not an audit area: no methodology to apply, so no call to make.
        return []

    if engagement.materiality is None or line_item.metrics is None or line_item.material is None:
        raise AuditAreaAnalysisError(
            f"{line_item.line_item_type} must be scoped before it is analysed"
        )

    if not line_item.material:
        # SPEC 6/8/10 confine analysis to material audit areas.
        return []

    output = client.parse(
        task=LLMTask.ANALYSE_AUDIT_AREA,
        system=ANALYSE_AUDIT_AREA,
        user=build_user_message(line_item, engagement, candidates),
        output_format=AuditAreaAnalysisOutput,
    )

    analyses = _collect_analyses(output, candidates, line_item.line_item_type)
    known_fact_ids = {f.id for f in engagement.company_facts}
    assertion_refs = config.isa_refs_for(LinkedObjectType.ASSERTION_ASSESSMENT)
    risk_refs = config.isa_refs_for(LinkedObjectType.RISK_ASSESSMENT)

    assessments: list[AssertionAssessment] = []
    for candidate in candidates:
        analysis = analyses.get(candidate)

        if analysis is None:
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
                    isa_refs=list(assertion_refs),
                )
            )
            continue

        assessment = AssertionAssessment(
            id=engagement.next_id(ASSERTION_ID_PREFIX),
            line_item_id=line_item.id,
            assertion=candidate,
            relevant=analysis.relevant,
            rationale=analysis.rationale.strip() or MISSING_RATIONALE,
            supporting_fact_ids=_validated_fact_ids(
                analysis.supporting_fact_ids, known_fact_ids, candidate.value
            ),
            isa_refs=list(assertion_refs),
        )
        assessment.risks = _build_risks(
            analysis, assessment, engagement, config, known_fact_ids, risk_refs,
            line_item.line_item_type,
        )
        assessments.append(assessment)

    return assessments


def _collect_analyses(
    output: AuditAreaAnalysisOutput, candidates: list[Assertion], area: str
) -> dict[Assertion, AssertionAnalysisOutput]:
    """Index verdicts by assertion, discarding anything outside the candidate list."""
    analyses: dict[Assertion, AssertionAnalysisOutput] = {}
    allowed = set(candidates)
    for analysis in output.assertions:
        if analysis.assertion not in allowed:
            # The model ruled on something it was not asked about — e.g. valuation for cash.
            # Accepting it would put an assertion in the file that the area's methodology
            # says does not apply.
            logger.warning(
                "discarding verdict for %s: not a candidate assertion for %s",
                analysis.assertion.value,
                area,
            )
            continue
        if analysis.assertion in analyses:
            logger.warning(
                "duplicate verdict for %s/%s; keeping the first", area, analysis.assertion.value
            )
            continue
        analyses[analysis.assertion] = analysis
    return analyses


def _build_risks(
    analysis: AssertionAnalysisOutput,
    assessment: AssertionAssessment,
    engagement: AuditEngagement,
    config: StaticConfig,
    known_fact_ids: set[str],
    isa_refs: list[str],
    area: str,
) -> list[RiskAssessment]:
    if not analysis.relevant:
        if analysis.risks:
            # SPEC 10: an assertion ruled out cannot carry risks. Keeping them would put
            # work in the file for an assertion the engagement says is not relevant.
            logger.warning(
                "discarding %d risk(s) on non-relevant assertion %s/%s",
                len(analysis.risks),
                area,
                assessment.assertion.value,
            )
        return []

    identified = analysis.risks
    if len(identified) > MAX_RISKS_PER_ASSERTION:
        logger.warning(
            "model returned %d risks for %s/%s; keeping the first %d",
            len(identified),
            area,
            assessment.assertion.value,
            MAX_RISKS_PER_ASSERTION,
        )
        identified = identified[:MAX_RISKS_PER_ASSERTION]

    risks: list[RiskAssessment] = []
    for risk_output in identified:
        description = risk_output.description.strip()
        if not description:
            # Nothing an auditor could design a procedure against, and nothing to display.
            logger.warning(
                "discarding a risk with no description for %s/%s",
                area,
                assessment.assertion.value,
            )
            continue

        system_rating = derive_rating(
            risk_output.likelihood, risk_output.magnitude, matrix=config.risk_matrix
        )
        risks.append(
            RiskAssessment(
                id=engagement.next_id(RISK_ID_PREFIX),
                assertion_id=assessment.id,
                risk_description=description,
                likelihood=risk_output.likelihood,
                magnitude=risk_output.magnitude,
                system_rating=system_rating,
                final_rating=system_rating,
                rationale=risk_output.rationale.strip() or MISSING_RATIONALE,
                supporting_fact_ids=_validated_fact_ids(
                    risk_output.supporting_fact_ids, known_fact_ids, assessment.assertion.value
                ),
                isa_refs=list(isa_refs),
            )
        )

    if not risks:
        # A relevant assertion with no risk is a contradiction, but inventing one would be
        # worse. Left empty so the ISA 315.28/31 coverage check surfaces it (SPEC 15).
        logger.warning(
            "no usable risk returned for relevant assertion %s/%s",
            area,
            assessment.assertion.value,
        )
    return risks


def _validated_fact_ids(returned: list[str], known: set[str], context: str) -> list[str]:
    """Keep only references to facts that actually exist (SPEC 14).

    A dangling ID would break traceability silently, so it is dropped rather than stored.
    """
    validated = [fact_id for fact_id in returned if fact_id in known]
    unknown = [fact_id for fact_id in returned if fact_id not in known]
    if unknown:
        logger.warning("dropping unknown fact ids %s cited for %s", sorted(unknown), context)
    return validated
