"""Extraction of structured company facts from free-text context (SPEC 3.2).

Fact IDs are assigned here, never by the model. Everything downstream references facts by ID
(`supporting_fact_ids`), so the IDs must be unique and must never come to mean something
different.

IDs come from the engagement's monotonic counter and are never reused, so re-extraction
after a context edit continues at `fact_3` rather than restarting at `fact_1`. Restarting
would be unsafe: a retained `AuditorFeedback` snapshot, or an assertion still awaiting an
explicit recompute, could hold `fact_1` and silently resolve it to unrelated new evidence.
Monotonic IDs make such a reference visibly unresolved instead.
"""

from src.llm.client import LLMClient, LLMTask
from src.llm.prompts import EXTRACT_COMPANY_FACTS
from src.llm.schemas import CompanyFactsOutput
from src.models.engagement import AuditEngagement, CompanyFact

FACT_ID_PREFIX = "fact"
COMPANY_CONTEXT_SOURCE = "company_context"


def build_user_message(engagement: AuditEngagement) -> str:
    """The bounded context for this judgement: the company context and nothing else.

    Deliberately excludes financials, materiality and scoping — none of them bear on what
    the context text says, and including them would invite the model to infer facts the
    text does not support (SPEC 21).
    """
    return (
        f"Company: {engagement.company}\n\n"
        f"Company context:\n{engagement.company_context.strip()}"
    )


def extract_company_facts(
    engagement: AuditEngagement, *, client: LLMClient
) -> list[CompanyFact]:
    """Return facts extracted from the engagement's company context.

    Does not assign `engagement.company_facts` — the caller does, which is what makes
    replacement explicit. It does consume IDs from the engagement's monotonic counter,
    since allocating a never-reused ID necessarily advances shared state.
    """
    if not engagement.company_context.strip():
        return []  # nothing to extract; do not spend a call

    output = client.parse(
        task=LLMTask.EXTRACT_COMPANY_FACTS,
        system=EXTRACT_COMPANY_FACTS,
        user=build_user_message(engagement),
        output_format=CompanyFactsOutput,
    )

    facts: list[CompanyFact] = []
    for extracted in output.facts:
        fact_type = extracted.fact_type.strip()
        value = extracted.value.strip()
        rationale = extracted.rationale.strip()
        if not fact_type or not value or not rationale:
            # Unusable. A fact with no type or value has nothing to display; one with no
            # rationale could still be cited by ID as support for an assertion or risk
            # while offering no evidence explanation, which is worse than absent. Dropped
            # before an ID is allocated, so no ID is spent on it.
            continue
        facts.append(
            CompanyFact(
                id=engagement.next_id(FACT_ID_PREFIX),
                fact_type=fact_type,
                value=value,
                source=COMPANY_CONTEXT_SOURCE,
                rationale=rationale,
            )
        )
    return facts
