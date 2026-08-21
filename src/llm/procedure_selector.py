"""Procedure selection for a whole audit area (SPEC 6.1, 13, ISA 330.6/330.7).

One call covers every risk in the area. Each selected procedure names the risks it responds
to, and becomes **one** runtime `Procedure` carrying that list — never one copy per risk.

The model is shown `final_rating`, never `system_rating`. That is what lets an auditor's
risk-rating override be answered by re-running this call alone, without re-analysing the area
and destroying the original system conclusion (SPEC 17).

Two things the catalogue, not the model, decides: which procedures are available for an area,
and which assertions each of them addresses. A selection is kept only where both hold, so the
stored procedure→risk links can never contradict approved methodology.
"""

import logging

from src.config.loader import CatalogueProcedure, StaticConfig, get_config
from src.engine.catalogue import catalogue_for_assertions
from src.llm.client import LLMClient, LLMTask
from src.llm.formatting import format_company_context, format_company_facts
from src.llm.prompts import SELECT_PROCEDURES
from src.llm.schemas import ProcedureSelectionOutput
from src.models.audit_objects import (
    Assertion,
    AssertionAssessment,
    Procedure,
    ProcedureSource,
    RiskAssessment,
)
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment
from src.models.isa import LinkedObjectType

logger = logging.getLogger(__name__)

PROCEDURE_ID_PREFIX = "proc"

#: An AI suggestion has no catalogue name; one is derived from its description for display.
SUGGESTION_NAME_LENGTH = 60
AI_SUGGESTED_TYPE = "ai_suggested"

MISSING_RATIONALE = "No rationale was provided for this selection."


class ProcedureSelectionError(ValueError):
    """Raised when selection is attempted before its inputs exist."""


def _assertion_for(risk: RiskAssessment, assertions: list[AssertionAssessment]) -> str:
    match = next((a for a in assertions if a.id == risk.assertion_id), None)
    return match.assertion.value if match else "unknown"


def _format_risks(line_item: FinancialLineItemAssessment) -> str:
    lines = []
    for risk in line_item.all_risks:
        lines.append(
            f"- {risk.id} ({_assertion_for(risk, line_item.assertions)}, "
            f"rating: {risk.final_rating.value})\n"
            f"  {risk.risk_description}\n"
            f"  Why: {risk.rationale}"
        )
    return "\n\n".join(lines)


def _format_catalogue(subset: list[CatalogueProcedure]) -> str:
    if not subset:
        # SPEC 13 permits a suggestion precisely when the catalogue cannot respond, so the
        # call is still made — saying so plainly rather than sending an empty heading.
        return (
            "None. No approved catalogue procedure covers the assertions these risks affect, "
            "so only suggested new procedures are possible here."
        )
    lines = []
    for procedure in subset:
        assertions = ", ".join(a.value for a in procedure.assertions)
        lines.append(
            f"- {procedure.id} — {procedure.name}\n"
            f"  Addresses: {assertions} | evidence strength: "
            f"{procedure.evidence_strength.value} | type: {procedure.procedure_type}\n"
            f"  {procedure.description}"
        )
    return "\n\n".join(lines)


def build_user_message(
    line_item: FinancialLineItemAssessment,
    engagement: AuditEngagement,
    subset: list[CatalogueProcedure],
) -> str:
    """The bounded context SPEC 13 specifies, for one audit area.

    Ratings shown are `final_rating`, so an override is reflected here without re-analysis.
    """
    return f"""\
Audit area: {line_item.line_item_type}

Risks assessed for this area:

{_format_risks(line_item)}

Company context:
{format_company_context(engagement)}

Company facts:
{format_company_facts(engagement)}

Available procedures for this area:

{_format_catalogue(subset)}"""


def select_procedures(
    line_item: FinancialLineItemAssessment,
    engagement: AuditEngagement,
    *,
    client: LLMClient,
    config: StaticConfig | None = None,
) -> list[Procedure]:
    """Return the procedures responding to every risk in this audit area.

    Does not assign to `line_item.procedures` — the caller does, and that assignment is what
    makes re-selection replace rather than append (SPEC 17). Consumes procedure IDs from the
    engagement's monotonic counter.
    """
    config = config or get_config()

    if not config.is_audit_area(line_item.line_item_type):
        return []
    if line_item.material is None:
        raise ProcedureSelectionError(
            f"{line_item.line_item_type} must be scoped before procedures are selected"
        )
    if not line_item.material:
        # SPEC 6/8/13 confine procedure selection to material audit areas.
        return []

    risks = line_item.all_risks
    if not risks:
        # Nothing to respond to. Any ISA 330.6/7 gap is surfaced by coverage, not invented here.
        return []

    assertions_with_risks = [
        assertion.assertion for assertion in line_item.assertions if assertion.risks
    ]
    subset = catalogue_for_assertions(
        line_item.line_item_type, assertions_with_risks, catalogue=config.procedure_catalogue
    )
    if not subset:
        # Still call: SPEC 13 permits an AI suggestion precisely when the catalogue has no
        # adequate response, and returning early would make that unreachable. `by_id` is
        # empty below, so only suggestions can survive.
        logger.warning(
            "no catalogue procedures cover the assertions with risks on %s; "
            "only AI suggestions are possible",
            line_item.line_item_type,
        )

    output = client.parse(
        task=LLMTask.SELECT_PROCEDURES,
        system=SELECT_PROCEDURES,
        user=build_user_message(line_item, engagement, subset),
        output_format=ProcedureSelectionOutput,
    )

    known_risk_ids = {risk.id for risk in risks}
    # Which assertion each risk belongs to, so a procedure cannot be linked to a risk its
    # catalogue entry does not claim to address.
    risk_assertions = {
        risk.id: assertion.assertion
        for assertion in line_item.assertions
        for risk in assertion.risks
    }
    by_id = {procedure.id: procedure for procedure in subset}
    isa_refs = config.isa_refs_for(LinkedObjectType.PROCEDURE)

    procedures = _build_catalogue_procedures(
        output, by_id, known_risk_ids, risk_assertions, engagement, isa_refs,
        line_item.line_item_type,
    )
    procedures.extend(
        _build_suggestions(
            output, known_risk_ids, engagement, isa_refs, line_item.line_item_type
        )
    )
    return procedures


def _build_catalogue_procedures(
    output: ProcedureSelectionOutput,
    by_id: dict[str, CatalogueProcedure],
    known_risk_ids: set[str],
    risk_assertions: dict[str, Assertion],
    engagement: AuditEngagement,
    isa_refs: list[str],
    area: str,
) -> list[Procedure]:
    # Merged by catalogue id: if the model lists the same procedure twice for different
    # risks, that is one procedure covering both, not two objects.
    merged: dict[str, tuple[list[str], str]] = {}
    for selection in output.selected_procedures:
        if selection.procedure_id not in by_id:
            logger.warning(
                "discarding %s: not in the catalogue subset offered for %s",
                selection.procedure_id,
                area,
            )
            continue

        risk_ids = _validated_risk_ids(
            selection.risk_ids,
            known_risk_ids,
            selection.procedure_id,
            area,
            risk_assertions=risk_assertions,
            allowed_assertions=set(by_id[selection.procedure_id].assertions),
        )
        if not risk_ids:
            logger.warning(
                "discarding %s: names no risk it is approved to address in %s",
                selection.procedure_id,
                area,
            )
            continue

        rationale = selection.rationale.strip() or MISSING_RATIONALE
        if selection.procedure_id in merged:
            existing_ids, existing_rationale = merged[selection.procedure_id]
            merged[selection.procedure_id] = (
                existing_ids + [r for r in risk_ids if r not in existing_ids],
                existing_rationale,
            )
        else:
            merged[selection.procedure_id] = (risk_ids, rationale)

    procedures = []
    for procedure_id, (risk_ids, rationale) in merged.items():
        entry = by_id[procedure_id]
        procedures.append(
            Procedure(
                id=engagement.next_id(PROCEDURE_ID_PREFIX),
                risk_ids=risk_ids,
                procedure_id=entry.id,
                name=entry.name,
                description=entry.description,
                procedure_type=entry.procedure_type,
                evidence_strength=entry.evidence_strength,
                rationale=rationale,
                source=ProcedureSource.CATALOGUE,
                approved=True,
                isa_refs=list(isa_refs),
            )
        )
    return procedures


def _build_suggestions(
    output: ProcedureSelectionOutput,
    known_risk_ids: set[str],
    engagement: AuditEngagement,
    isa_refs: list[str],
    area: str,
) -> list[Procedure]:
    suggestions = []
    for suggested in output.suggested_new_procedures:
        description = suggested.description.strip()
        if not description:
            logger.warning("discarding an AI suggestion with no description for %s", area)
            continue

        risk_ids = _validated_risk_ids(suggested.risk_ids, known_risk_ids, "suggestion", area)
        if not risk_ids:
            logger.warning("discarding an AI suggestion naming no risk in %s", area)
            continue

        name = description[:SUGGESTION_NAME_LENGTH].rstrip()
        if len(description) > SUGGESTION_NAME_LENGTH:
            name += "…"

        suggestions.append(
            Procedure(
                id=engagement.next_id(PROCEDURE_ID_PREFIX),
                risk_ids=risk_ids,
                procedure_id=None,  # no catalogue entry exists
                name=name,
                description=description,
                procedure_type=AI_SUGGESTED_TYPE,
                # Left unset: strength is approved methodology the catalogue carries, and
                # this has not been assessed. Inventing one would imply it had been.
                evidence_strength=None,
                rationale=suggested.rationale.strip() or MISSING_RATIONALE,
                source=ProcedureSource.AI_SUGGESTION,
                approved=False,
                isa_refs=list(isa_refs),
            )
        )
    return suggestions


def _validated_risk_ids(
    returned: list[str],
    known: set[str],
    procedure_id: str,
    area: str,
    *,
    risk_assertions: dict[str, Assertion] | None = None,
    allowed_assertions: set[Assertion] | None = None,
) -> list[str]:
    """Keep only risk references this procedure may legitimately answer (SPEC 14).

    Two checks. The risk must exist in this area, and — for a catalogue procedure — it must
    belong to an assertion that procedure is approved to address. Without the second, the
    model could link an existence-only procedure to a valuation risk, and reverse coverage
    would then report that risk as answered while the approved methodology says it is not.

    `allowed_assertions` is None for an AI suggestion, which has no catalogue mapping to
    contradict.

    An unusable id is dropped from an otherwise-good procedure; the procedure is discarded
    only if that leaves it addressing nothing.
    """
    validated: list[str] = []
    unknown: list[str] = []
    mismatched: list[str] = []

    for risk_id in returned:
        if risk_id not in known:
            unknown.append(risk_id)
        elif (
            allowed_assertions is not None
            and (risk_assertions or {}).get(risk_id) not in allowed_assertions
        ):
            mismatched.append(risk_id)
        else:
            validated.append(risk_id)

    if unknown:
        logger.warning(
            "dropping unknown risk ids %s cited by %s for %s",
            sorted(unknown),
            procedure_id,
            area,
        )
    if mismatched:
        logger.warning(
            "dropping risk ids %s from %s for %s: their assertions are not ones the "
            "catalogue entry addresses",
            sorted(mismatched),
            procedure_id,
            area,
        )
    return validated
