"""The auditor-facing surface (SPEC 16).

    uv run streamlit run src/ui/app.py

**No domain logic lives here.** This file reads session state, renders it, and calls
`engine`/`llm` functions. Anything that looks like a calculation or an audit decision — which
catalogue procedures may answer a risk, whether an ISA requirement is satisfied, what a rating
override recomputes — belongs to a module that is unit-tested without Streamlit. Everything
below either formats a value or calls one of those functions.

Two consequences of that rule are visible in the code:

* Overrides go through `engine.recompute`, never through direct assignment to a model field.
  The recompute functions record the feedback and rerun what depends on the change; assigning
  `risk.final_rating` here would look identical on screen and silently skip both.
* Recomputation is triggered by an explicit button, never by a widget's `on_change`. Streamlit
  reruns this script on every interaction, and several of these actions cost live LLM calls.

The engagement is held in `st.session_state` and nothing is persisted (SPEC 2): reloading the
browser starts a new engagement.
"""

import streamlit as st

from src.config.loader import get_config
from src.engine.catalogue import filter_catalogue
from src.engine.coverage import check_isa_coverage
from src.engine.pipeline import load_engagement, run_pipeline
from src.engine.recompute import (
    RecomputeError,
    add_auditor_procedure,
    add_catalogue_procedure,
    approve_procedure,
    override_assertion_relevance,
    override_risk_rating,
    remove_procedure,
    update_company_context,
    update_company_facts,
)
from src.engine.traceability import TraceabilityError, trace_procedure
from src.llm.client import AnthropicLLMClient, LLMError
from src.llm.feedback_generalizer import generalize_feedback, is_analysable
from src.models.audit_objects import (
    AI_SUGGESTION_LABEL,
    AUDITOR_ADDED_LABEL,
    ProcedureSource,
    RiskLevel,
)
from src.models.engagement import AuditEngagement, CompanyFact
from src.models.feedback import FeedbackAnalysisOutcome

RATINGS = [level.value for level in RiskLevel]

RATING_ICON = {RiskLevel.LOW: "🟢", RiskLevel.MEDIUM: "🟠", RiskLevel.HIGH: "🔴"}


# --- session ---------------------------------------------------------------------------------


def engagement() -> AuditEngagement:
    return st.session_state.engagement


def client() -> AnthropicLLMClient:
    """Built on first use, not at startup.

    Constructing it reads credentials, and the screen is worth showing without them — the case
    data, the materiality rule and the scoping are all deterministic. A missing key should
    surface when an LLM action is taken, not when the page is opened.
    """
    if "client" not in st.session_state:
        st.session_state.client = AnthropicLLMClient()
    return st.session_state.client


def config():
    return st.session_state.config


def initialise() -> None:
    """Pre-populate everything (SPEC 16: no blank-form workflows).

    The engagement is loaded from the case file at startup, so the auditor arrives at a
    populated screen and chooses when to spend the five calls a pipeline run costs.
    """
    if "engagement" in st.session_state:
        return
    st.session_state.config = get_config()
    st.session_state.engagement = load_engagement(st.session_state.config)


def act(action, *args, success: str = "Done.", **kwargs) -> None:
    """Run one engine action, reporting failure rather than leaving a half-drawn screen.

    `LLMError` is expected here — a live call can fail, and the recompute functions restore
    what they changed when it does, so the message is the whole of the recovery.
    """
    try:
        action(*args, **kwargs)
    except (LLMError, RecomputeError, TraceabilityError) as exc:
        st.error(str(exc))
        return
    st.session_state.last_action = success
    st.rerun()


# --- company data and materiality --------------------------------------------------------------


def render_header() -> None:
    audit = engagement()
    st.title(f"{audit.company} — audit plan")
    st.caption(f"Year ended {audit.year_end} · prototype, session state only")

    if audit.materiality is None:
        st.info("Run the pipeline from the sidebar to generate the plan.")
        return

    left, right = st.columns([1, 2])
    left.metric("Materiality", f"£{audit.materiality.amount:,.0f}")
    right.markdown(
        f"**{audit.materiality.rate:.1%} of {audit.materiality.benchmark}** — "
        f"{audit.materiality.basis}"
    )
    right.caption(audit.materiality.label)


def render_line_items() -> None:
    audit = engagement()
    if audit.materiality is None:
        return

    st.subheader("Line items")
    rows = []
    for item in audit.line_items:
        metrics = item.metrics
        if item.is_audit_area:
            status = "audit area" if item.material else "immaterial — out of scope"
        else:
            status = (
                "material — audit logic not implemented in MVP"
                if item.material
                else "immaterial"
            )
        rows.append(
            {
                "line item": item.line_item_type,
                "CY": f"{item.cy:,.0f}",
                "PY": f"{item.py:,.0f}",
                "YoY": f"{metrics.yoy_change:+,.0f}",
                "YoY %": "n/a" if metrics.yoy_change_pct is None
                else f"{metrics.yoy_change_pct:+.1f}%",
                "× materiality": f"{metrics.amount_to_materiality_ratio:.1f}",
                "material": "yes" if item.material else "no",
                "status": status,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")


# --- context and facts ---------------------------------------------------------------------------


def render_context() -> None:
    audit = engagement()
    st.subheader("Company context")

    with st.form("context"):
        text = st.text_area("Context", value=audit.company_context, height=200)
        reason = st.text_input("Why is it changing?", placeholder="e.g. client sent an update")
        submitted = st.form_submit_button("Save and re-run both audit areas")

    if submitted:
        if text == audit.company_context:
            st.info("Unchanged — nothing to re-run.")
        else:
            st.warning("Re-extracting facts and re-running every audit area…")
            act(
                update_company_context,
                audit,
                text,
                reason,
                client=client(),
                config=config(),
                success="Context updated and both areas re-run.",
            )

    render_facts()


def render_facts() -> None:
    """Editable, because extraction is a model output the auditor may need to correct.

    Correcting a fact here costs `2n` calls and no re-extraction: the context has not changed,
    so asking the model again would risk repeating the mistake being corrected. IDs are held
    in the table and passed straight back, which is what lets `update_company_facts` keep a
    corrected fact's traceability intact and tell an edit from a new fact.
    """
    audit = engagement()
    st.markdown("**Extracted facts**")
    if not audit.company_facts and audit.materiality is None:
        st.caption("None extracted yet — run the pipeline.")
        return

    with st.form("facts"):
        edited = st.data_editor(
            [
                {
                    "id": fact.id,
                    "type": fact.fact_type,
                    "value": fact.value,
                    "from the context": fact.rationale,
                }
                for fact in audit.company_facts
            ],
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            disabled=["id"],
            key="factrows",
        )
        reason = st.text_input("Why are they changing?", key="factreason")
        if st.form_submit_button("Save facts and re-run both audit areas"):
            act(
                update_company_facts,
                audit,
                [
                    CompanyFact(
                        id=str(row.get("id") or ""),
                        fact_type=str(row.get("type") or ""),
                        value=str(row.get("value") or ""),
                        rationale=str(row.get("from the context") or ""),
                    )
                    for row in edited
                ],
                reason,
                client=client(),
                config=config(),
                success="Facts updated and both areas re-run.",
            )


# --- assertions, risks, procedures ---------------------------------------------------------------


def render_assertion(area, assertion) -> None:
    verdict = "relevant" if assertion.relevant else "not relevant"
    with st.expander(
        f"{assertion.assertion.value} — {verdict} · {len(assertion.risks)} risk(s)",
        expanded=assertion.relevant,
    ):
        st.write(assertion.rationale)
        st.caption(
            f"{assertion.id} · ISA {', '.join(assertion.isa_refs)}"
            + (
                f" · cites {', '.join(assertion.supporting_fact_ids)}"
                if assertion.supporting_fact_ids
                else ""
            )
        )

        with st.form(f"relevance_{assertion.id}"):
            relevant = st.checkbox("Relevant", value=assertion.relevant)
            reason = st.text_input("Reason", key=f"relreason_{assertion.id}")
            if not assertion.relevant:
                st.caption(
                    "Ruling this in re-analyses the whole area and **replaces every assertion "
                    "and risk in it**, discarding overrides held on them (SPEC 17)."
                )
            if st.form_submit_button("Apply relevance") and relevant is not assertion.relevant:
                act(
                    override_assertion_relevance,
                    engagement(),
                    assertion.id,
                    relevant,
                    reason,
                    client=client(),
                    config=config(),
                    success=f"{assertion.assertion.value} set to {relevant}.",
                )

        for risk in assertion.risks:
            render_risk(area, risk)


def render_risk(area, risk) -> None:
    st.markdown(
        f"### {RATING_ICON[risk.final_rating]} {risk.id} — {risk.final_rating.value.upper()}"
    )
    st.write(risk.risk_description)
    st.caption(
        f"likelihood {risk.likelihood.value} × magnitude {risk.magnitude.value} → "
        f"system rating **{risk.system_rating.value}**"
        + (
            f" · overridden to {risk.final_rating.value}: {risk.override_reason}"
            if risk.is_overridden
            else ""
        )
    )
    st.caption(risk.rationale)

    with st.form(f"rating_{risk.id}"):
        columns = st.columns([1, 2, 1])
        rating = columns[0].selectbox(
            "Final rating",
            RATINGS,
            index=RATINGS.index(risk.final_rating.value),
            key=f"ratingpick_{risk.id}",
        )
        reason = columns[1].text_input("Reason", key=f"riskreason_{risk.id}")
        columns[2].markdown("&nbsp;", unsafe_allow_html=True)
        if columns[2].form_submit_button("Apply rating"):
            act(
                override_risk_rating,
                engagement(),
                risk.id,
                RiskLevel(rating),
                reason,
                client=client(),
                config=config(),
                success=f"{risk.id} set to {rating}; procedures re-selected.",
            )

    render_procedures(area, risk, assertion_of=risk.assertion_id)


def render_procedures(area, risk, assertion_of: str) -> None:
    responses = area.procedures_for(risk.id)
    if not responses:
        st.warning(f"No procedure responds to {risk.id} — an ISA 330.6/7 gap.")

    for procedure in responses:
        strength = (
            procedure.evidence_strength.value if procedure.evidence_strength else "unassessed"
        )
        label = f"**{procedure.procedure_id or 'new procedure'}** — {procedure.name}"
        answers = ", ".join(procedure.risk_ids)
        st.markdown(f"- {label}  \n  evidence: {strength} · answers {answers}")
        st.caption(procedure.rationale)

        if procedure.source is ProcedureSource.AI_SUGGESTION:
            st.caption(f"⚠️ {AI_SUGGESTION_LABEL}")
        elif procedure.source is ProcedureSource.AUDITOR_ADDED:
            st.caption(f"⚠️ {AUDITOR_ADDED_LABEL}")

        # Both actions write a feedback record, and procedure feedback is the clearest input
        # SPEC 19 has to learn from — "removed by the auditor" gives the generalizer no
        # judgement to assess. So the reason is asked for here as it is everywhere else.
        with st.form(f"proc_{procedure.id}_{risk.id}"):
            reason = st.text_input("Reason", key=f"procreason_{procedure.id}_{risk.id}")
            columns = st.columns(2)
            if columns[0].form_submit_button("Remove from this risk"):
                act(
                    remove_procedure,
                    engagement(),
                    procedure.id,
                    risk.id,
                    reason,
                    success=f"{procedure.id} detached from {risk.id}.",
                )
            if procedure.requires_approval and columns[1].form_submit_button(
                "Approve suggestion"
            ):
                act(
                    approve_procedure,
                    engagement(),
                    procedure.id,
                    reason,
                    success=f"{procedure.id} approved.",
                )

    render_add_procedure(area, risk, assertion_of)


def render_add_procedure(area, risk, assertion_of: str) -> None:
    """Offer both approved catalogue work and explicit engagement-specific work.

    `filter_catalogue` decides what is eligible — the same constraint the model is held to —
    so the catalogue picker cannot record a link approved methodology does not support. A
    free-text addition is clearly marked as non-catalogue and becomes feedback rather than a
    silent methodology change.
    """
    assertion = next(a for a in area.assertions if a.id == assertion_of)
    eligible = filter_catalogue(
        area.line_item_type, assertion.assertion, catalogue=config().procedure_catalogue
    )
    already = {p.procedure_id for p in area.procedures_for(risk.id)}
    options = [entry for entry in eligible if entry.id not in already]
    if options:
        with st.form(f"add_{risk.id}"):
            choice = st.selectbox(
                "Add a catalogue procedure",
                options,
                format_func=lambda e: f"{e.id} — {e.name} ({e.evidence_strength.value})",
                key=f"pick_{risk.id}",
            )
            reason = st.text_input("Reason", key=f"addreason_{risk.id}")
            if st.form_submit_button("Add"):
                act(
                    add_catalogue_procedure,
                    engagement(),
                    risk.id,
                    choice.id,
                    reason or "Added by the auditor.",
                    config(),
                    success=f"{choice.id} added to {risk.id}.",
                )

    with st.form(f"addcustom_{risk.id}"):
        st.caption(
            "Add engagement-specific work. It is active in this plan but remains unassessed "
            "methodology and can be analysed from the feedback log."
        )
        name = st.text_input("Custom procedure name", key=f"customname_{risk.id}")
        description = st.text_area(
            "Custom procedure description", key=f"customdesc_{risk.id}"
        )
        reason = st.text_input("Why add it?", key=f"customreason_{risk.id}")
        if st.form_submit_button("Add custom procedure"):
            act(
                add_auditor_procedure,
                engagement(),
                risk.id,
                name,
                description,
                reason,
                config=config(),
                success=f"Custom procedure added to {risk.id}.",
            )


def render_audit_areas() -> None:
    areas = engagement().in_scope_audit_areas
    if not areas:
        return

    st.subheader("Audit areas")
    for tab, area in zip(st.tabs([a.line_item_type for a in areas]), areas, strict=True):
        with tab:
            st.caption(
                f"{area.cy:,.0f} · {area.metrics.amount_to_materiality_ratio:.1f}× materiality "
                f"· {len(area.all_risks)} risk(s) · {len(area.procedures)} procedure(s)"
            )
            for assertion in area.assertions:
                render_assertion(area, assertion)


# --- traceability and coverage ----------------------------------------------------------------


def render_traceability() -> None:
    audit = engagement()
    procedures = [p for area in audit.in_scope_audit_areas for p in area.procedures]
    if not procedures:
        return

    st.subheader("Traceability")
    st.caption("Every procedure resolves back to a risk, an assertion, a line item and an ISA "
               "requirement (SPEC 14).")
    procedure = st.selectbox(
        "Procedure",
        procedures,
        format_func=lambda p: f"{p.id} — {p.procedure_id or p.name}",
    )

    try:
        chains = trace_procedure(procedure, audit)
    except TraceabilityError as exc:
        st.error(str(exc))
        return

    for chain in chains:
        st.markdown(
            f"**{chain.procedure.name}**  \n"
            f"↳ risk `{chain.risk.id}` ({chain.risk.final_rating.value}) — "
            f"{chain.risk.risk_description}  \n"
            f"↳ assertion `{chain.assertion.id}` — {chain.assertion.assertion.value}  \n"
            f"↳ line item `{chain.line_item.id}` — {chain.line_item.line_item_type}"
        )
        if chain.facts:
            st.caption(
                "supporting facts: "
                + "; ".join(f"{f.id} {f.fact_type}={f.value}" for f in chain.facts)
            )
        st.caption("ISA chain: " + " → ".join(chain.isa_chain))


def render_coverage() -> None:
    audit = engagement()
    if not audit.in_scope_audit_areas:
        return

    st.subheader("ISA coverage")
    report = check_isa_coverage(audit, config())

    for coverage in report.requirements:
        icon = "✅" if coverage.satisfied else "❌"
        with st.expander(
            f"{icon} {coverage.requirement.id} — {coverage.requirement.standard} "
            f"{', '.join(coverage.requirement.paragraphs)}",
            expanded=not coverage.satisfied,
        ):
            st.caption(coverage.requirement.purpose)
            st.write(
                "Addressed by: " + (", ".join(coverage.addressed_by) or "nothing")
            )
            for gap in coverage.gaps:
                st.error(f"GAP — {coverage.requirement.id}: {gap.description}")

    if report.not_implemented:
        st.caption(
            "Outside the MVP's implemented methodology, not reported as gaps: "
            + ", ".join(x.line_item_type for x in report.not_implemented)
        )


# --- feedback and methodology learning --------------------------------------------------------


def render_feedback() -> None:
    audit = engagement()
    st.subheader("Auditor feedback")
    if not audit.feedback:
        st.caption("No overrides yet. Every override is recorded here with what it replaced.")
        return

    for record in audit.feedback:
        st.markdown(
            f"**{record.id}** · {record.object_type} `{record.object_id}`  \n"
            f"{record.before} → {record.after}"
        )
        st.caption(record.reason or "No reason given.")

        analysis = next(
            (a for a in audit.feedback_analyses if a.source_feedback_id == record.id), None
        )
        if analysis is not None:
            if analysis.outcome is FeedbackAnalysisOutcome.ENGAGEMENT_SPECIFIC:
                st.info(
                    "**Engagement-specific — no methodology rule proposed.**  \n"
                    f"Reason: {analysis.reason or 'No reasoning was provided.'}"
                )
            elif analysis.outcome is FeedbackAnalysisOutcome.INCOMPLETE_RULE_PROPOSAL:
                st.warning(
                    "**No methodology rule was filed.** The proposed rule was incomplete.  \n"
                    f"Reason: {analysis.reason or 'No reasoning was provided.'}"
                )
            else:
                st.success(
                    "**Candidate methodology rule proposed.**  \n"
                    f"Reason: {analysis.reason or 'No reasoning was provided.'}"
                )
            continue
        if not is_analysable(record):
            st.caption("Revised input rather than an overridden judgement — not analysable.")
            continue
        if st.button("Analyse for a methodology rule", key=f"gen_{record.id}"):
            act(
                generalize_feedback,
                record,
                audit,
                client=client(),
                success=f"{record.id} analysed.",
            )

    if audit.rule_proposals:
        st.markdown("**Candidate methodology rules**")
        st.caption("Advisory only — nothing here changes the approved methodology (SPEC 19).")
        for proposal in audit.rule_proposals:
            st.info(
                f"**IF** {proposal.condition}  \n**THEN** {proposal.action}  \n"
                f"_{proposal.reason}_  \n"
                f"`{proposal.id}` · from `{proposal.source_feedback_id}` · "
                f"status: {proposal.status.value}"
            )


# --- sidebar and entry point ------------------------------------------------------------------


def render_sidebar() -> None:
    audit = engagement()
    with st.sidebar:
        st.header("Pipeline")
        st.caption(
            "One run is five LLM calls: facts, then analysis and procedure selection for each "
            "of the two audit areas (SPEC 6.1)."
        )
        if st.button("Run the pipeline", type="primary"):
            with st.spinner("Running…"):
                act(
                    run_pipeline,
                    audit,
                    client=client(),
                    config=config(),
                    success="Pipeline complete.",
                )

        if st.button("Reset to the case file"):
            for key in ("engagement", "config", "client"):
                st.session_state.pop(key, None)
            st.rerun()

        st.divider()
        st.metric("Risks", sum(len(a.all_risks) for a in audit.in_scope_audit_areas))
        st.metric("Procedures", sum(len(a.procedures) for a in audit.in_scope_audit_areas))
        st.metric("Overrides", len(audit.feedback))

        if last := st.session_state.pop("last_action", None):
            st.success(last)


def main() -> None:
    st.set_page_config(page_title="Audit planning engine", layout="wide")
    initialise()

    render_sidebar()
    render_header()
    render_line_items()
    render_context()
    render_audit_areas()
    render_traceability()
    render_coverage()
    render_feedback()


main()
