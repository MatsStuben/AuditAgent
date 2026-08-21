"""Run the pipeline against Raiatea and print the resulting audit plan.

    python -m src.demo

Presentation only — every number and judgement here comes from the engine. This is how the
audit plan is inspected end to end before the Streamlit UI exists.

Needs an API key (`.env`). A full run is five LLM calls.
"""

import sys

from src.config.loader import get_config
from src.engine.pipeline import load_engagement, run_pipeline
from src.llm.client import AnthropicLLMClient, LLMError
from src.models.audit_objects import ProcedureSource
from src.models.engagement import AuditEngagement, FinancialLineItemAssessment

RULE = "─" * 78


def _heading(text: str) -> str:
    return f"\n{RULE}\n{text}\n{RULE}"


def _materiality(engagement: AuditEngagement) -> str:
    materiality = engagement.materiality
    return (
        f"{_heading('MATERIALITY')}\n"
        f"{materiality.amount:,.0f}  "
        f"({materiality.rate:.1%} of {materiality.benchmark})\n"
        f"{materiality.basis}\n"
        f"[{materiality.label}]"
    )


def _line_items(engagement: AuditEngagement) -> str:
    rows = [
        f"{'line item':<26}{'CY':>13}{'YoY':>13}{'x mat':>8}  {'material':<9}status"
    ]
    for item in engagement.line_items:
        metrics = item.metrics
        if item.is_audit_area:
            status = "audit area" if item.material else "immaterial — out of scope"
        else:
            status = "audit logic not implemented in MVP" if item.material else "immaterial"
        rows.append(
            f"{item.line_item_type:<26}{item.cy:>13,.0f}{metrics.yoy_change:>+13,.0f}"
            f"{metrics.amount_to_materiality_ratio:>8.1f}  {str(item.material):<9}{status}"
        )
    return f"{_heading('LINE ITEMS')}\n" + "\n".join(rows)


def _facts(engagement: AuditEngagement) -> str:
    if not engagement.company_facts:
        return f"{_heading('COMPANY FACTS')}\nNone extracted."
    rows = [
        f"  {fact.id}  {fact.fact_type} = {fact.value}\n      {fact.rationale}"
        for fact in engagement.company_facts
    ]
    return (
        f"{_heading('COMPANY FACTS')}\n"
        f'Context: "{engagement.company_context}"\n\n' + "\n".join(rows)
    )


def _area(item: FinancialLineItemAssessment) -> str:
    lines = [_heading(f"AUDIT AREA — {item.line_item_type.upper()}")]

    for assertion in item.assertions:
        verdict = "RELEVANT" if assertion.relevant else "not relevant"
        cites = f"  [{', '.join(assertion.supporting_fact_ids)}]" if (
            assertion.supporting_fact_ids
        ) else ""
        lines.append(f"\n  {verdict:<13}{assertion.assertion.value}{cites}")
        lines.append(f"      {assertion.rationale}")
        lines.append(f"      {assertion.id} · {' '.join(assertion.isa_refs)}")

        for risk in assertion.risks:
            lines.append(
                f"\n      RISK {risk.id}   likelihood={risk.likelihood.value} "
                f"× magnitude={risk.magnitude.value} "
                f"→ {risk.final_rating.value.upper()}"
            )
            if risk.is_overridden:
                lines.append(
                    f"        (system rating was {risk.system_rating.value}; overridden)"
                )
            lines.append(f"        {risk.risk_description}")
            covering = item.procedures_for(risk.id)
            if covering:
                for procedure in covering:
                    lines.append(f"        → {procedure.id} {procedure.name}")
            else:
                lines.append("        → NO PROCEDURE (potential ISA 330.6/7 gap)")

    lines.append(f"\n  PROCEDURES ({len(item.procedures)})")
    for procedure in item.procedures:
        strength = (
            procedure.evidence_strength.value if procedure.evidence_strength else "unassessed"
        )
        marker = ""
        if procedure.source is ProcedureSource.AI_SUGGESTION:
            marker = "   *** AI SUGGESTION "
        elif procedure.source is ProcedureSource.AUDITOR_ADDED:
            marker = "   *** AUDITOR-ADDED (NOT CATALOGUE) "
        if procedure.requires_approval:
            marker += "— AUDITOR APPROVAL REQUIRED ***"
        lines.append(
            # 30, not 26: CASH_RECONCILIATION_REVIEW is itself 26 characters, and a field
            # exactly as wide as its longest value leaves no space before the next column.
            f"    {procedure.id}  {procedure.procedure_id or '(new)':<30}"
            f"evidence={strength:<11}addresses {', '.join(procedure.risk_ids)}{marker}"
        )
        lines.append(f"        {procedure.name}")
        lines.append(f"        {procedure.rationale}")
    return "\n".join(lines)


def _summary(engagement: AuditEngagement, calls: list[str]) -> str:
    risks = [risk for area in engagement.in_scope_audit_areas for risk in area.all_risks]
    procedures = [p for area in engagement.in_scope_audit_areas for p in area.procedures]
    uncovered = [
        risk.id
        for area in engagement.in_scope_audit_areas
        for risk in area.all_risks
        if not area.procedures_for(risk.id)
    ]
    dangling = {
        rid for area in engagement.in_scope_audit_areas for rid in area.dangling_risk_ids()
    }
    return (
        f"{_heading('SUMMARY')}\n"
        f"audit areas          {len(engagement.in_scope_audit_areas)}\n"
        f"risks identified     {len(risks)}\n"
        f"procedures proposed  {len(procedures)} "
        f"({sum(1 for p in procedures if p.requires_approval)} awaiting approval)\n"
        f"risks uncovered      {uncovered or 'none'}\n"
        f"dangling references  {dangling or 'none'}\n"
        f"LLM calls            {len(calls)}  {calls}"
    )


class _CountingClient:
    """Wraps the real client to report the SPEC 6.1 call budget."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[str] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs["task"].value)
        return self._inner.parse(**kwargs)


def main() -> int:
    config = get_config()
    engagement = load_engagement(config)
    client = _CountingClient(AnthropicLLMClient())

    print(f"{engagement.company} — year ended {engagement.year_end}")
    print("Running pipeline (5 LLM calls)…", flush=True)

    try:
        run_pipeline(engagement, client=client, config=config)
    except LLMError as exc:
        print(f"\nLLM call failed: {exc}", file=sys.stderr)
        return 1

    print(_materiality(engagement))
    print(_line_items(engagement))
    print(_facts(engagement))
    for item in engagement.in_scope_audit_areas:
        print(_area(item))
    print(_summary(engagement, client.calls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
