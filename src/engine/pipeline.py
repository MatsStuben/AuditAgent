"""The SPEC 6 pipeline, headless and importable.

    load engagement
    extract company facts        [1 LLM call]
    calculate materiality        [deterministic]
    scope line items             [deterministic]
    per material audit area:
        analyse audit area       [1 LLM call]  relevance + risks + derived ratings
        select procedures        [1 LLM call]  every risk in the area

Five calls for a cash + inventory run (SPEC 6.1). No Streamlit dependency, so the evals and
every test can drive it directly.
"""

import logging

from src.config.loader import StaticConfig, get_config
from src.engine.materiality import calculate_materiality
from src.engine.scoping import scope_line_items
from src.llm.audit_area_analyser import analyse_audit_area
from src.llm.client import LLMClient
from src.llm.context_extractor import extract_company_facts
from src.llm.procedure_selector import select_procedures
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment

logger = logging.getLogger(__name__)

LINE_ITEM_ID_PREFIX = "li"


def load_engagement(config: StaticConfig | None = None) -> AuditEngagement:
    """Build an engagement from static config, with nothing assessed yet.

    The company context is seeded from the input file so the engagement arrives
    pre-populated (SPEC 16); the auditor may replace it before running the pipeline.
    """
    config = config or get_config()
    source = config.engagement_input
    return AuditEngagement(
        company=source.company,
        year_end=source.year_end,
        company_context=source.company_context,
        line_items=[
            FinancialLineItemAssessment(
                id=f"{LINE_ITEM_ID_PREFIX}_{item.type}",
                line_item_type=item.type,
                cy=item.cy,
                py=item.py,
            )
            for item in source.line_items
        ],
    )


def clear_area(line_item: FinancialLineItemAssessment) -> None:
    """Drop the audit work from a line item that is no longer in scope (SPEC 17).

    A materiality change can push a previously material audit area below the threshold.
    Skipping it would leave its assertions, risks and procedures in place, and a traceability
    view or coverage report would then present out-of-scope work as live. Deterministic —
    no LLM call, because there is nothing to reassess.
    """
    if line_item.assertions or line_item.procedures:
        logger.info(
            "clearing audit work from %s: no longer a material audit area",
            line_item.line_item_type,
        )
    line_item.assertions = []
    line_item.procedures = []


def run_area(
    line_item: FinancialLineItemAssessment,
    engagement: AuditEngagement,
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> None:
    """Both LLM calls for one audit area. The unit M11 re-runs (SPEC 17).

    Procedures are cleared *before* re-analysis, not after: analysis replaces the area's
    risks, so procedures naming the old ones would be dangling in the window between the two
    calls — and would stay dangling if selection then failed.
    """
    line_item.procedures = []
    line_item.assertions = analyse_audit_area(
        line_item, engagement, client=client, config=config
    )
    line_item.procedures = select_procedures(
        line_item, engagement, client=client, config=config
    )


def run_pipeline(
    engagement: AuditEngagement,
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> AuditEngagement:
    """Run the full SPEC 6 sequence over `engagement`, in place.

    Returns the same engagement for convenience. Mutation is the point of this function, so
    it is not pure — unlike the services it orchestrates, which return their results.
    """
    config = config or get_config()

    engagement.company_facts = extract_company_facts(engagement, client=client)

    engagement.materiality = calculate_materiality(engagement)
    scope_line_items(engagement, config)

    for line_item in engagement.line_items:
        # Material alone is not enough: an implemented profile is what makes a line item an
        # audit area (SPEC 2.1). Both services guard this too; the loop keeps it explicit.
        if not line_item.material or not line_item.is_audit_area:
            clear_area(line_item)
            continue
        run_area(line_item, engagement, client=client, config=config)

    logger.info(
        "pipeline complete: %d line items scoped, %d audit areas analysed",
        len(engagement.line_items),
        len(engagement.in_scope_audit_areas),
    )
    return engagement
