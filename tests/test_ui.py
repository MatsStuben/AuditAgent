"""M14 verification: the app renders and wires to the engine (SPEC 16).

Streamlit's `AppTest` runs `app.py` headlessly, so the checks here are about wiring rather
than appearance: does the screen come up pre-populated, does it survive a completed
engagement, and — the one that matters — do the auditor's controls call `engine.recompute`
rather than assigning to model fields.

The walkthrough itself is manual (PLAN M14). What is worth automating is the failure this file
cannot see coming: an engine signature changes, and the UI keeps rendering while doing nothing.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.engine.recompute import override_risk_rating
from src.llm.client import LLMError
from src.llm.schemas import (
    EngagementSpecificFeedback,
    FeedbackClassificationOutput,
)
from src.models.audit_objects import Assertion, RiskLevel
from tests.conftest import (
    INVENTORY_RISK,
    scripted_analysis,
    scripted_facts,
    scripted_selection,
)
from tests.fakes import FailingLLMClient, ScriptedLLMClient

APP = str(Path(__file__).resolve().parent.parent / "src" / "ui" / "app.py")


def app(engagement=None, config=None, client=None) -> AppTest:
    """The app, optionally seeded with an already-run engagement and a scripted client."""
    at = AppTest.from_file(APP, default_timeout=30)
    if engagement is not None:
        at.session_state["engagement"] = engagement
        at.session_state["config"] = config
    if client is not None:
        at.session_state["client"] = client
    return at.run()


# --- it comes up, populated, without credentials -----------------------------------------------


def test_the_app_starts_before_the_pipeline_has_run():
    """SPEC 16: everything pre-populated, no blank-form workflow. The case data, materiality
    rule and scoping are deterministic, so the screen is worth showing before any LLM call."""
    at = app()

    assert not at.exception
    assert "Raiatea Ltd" in at.title[0].value
    assert at.session_state["engagement"].line_items
    # No client is built at startup — opening the page must not require an API key.
    assert "client" not in at.session_state


def test_a_completed_engagement_renders_every_section(engagement, static_config):
    at = app(engagement, static_config)

    assert not at.exception
    headings = [h.value for h in at.subheader]
    assert headings == [
        "Line items",
        "Company context",
        "Audit areas",
        "Traceability",
        "ISA coverage",
        "Auditor feedback",
    ]


def test_the_line_item_table_shows_all_eight_with_their_status(engagement, static_config):
    at = app(engagement, static_config)

    table = at.dataframe[0].value
    assert len(table) == 8
    statuses = dict(zip(table["line item"], table["status"], strict=True))
    assert statuses["inventory"] == "audit area"
    assert statuses["turnover"] == "material — audit logic not implemented in MVP"


def test_a_gap_is_surfaced_rather_than_left_to_be_noticed(engagement, static_config):
    """Removing the only response to a risk must show as an ISA 330.6/7 gap."""
    engagement.line_item("inventory").procedures = []

    at = app(engagement, static_config)

    assert any("ISA330.6_7" in error.value for error in at.error), [e.value for e in at.error]


# --- the controls reach the engine ---------------------------------------------------------------


def test_overriding_a_rating_goes_through_recompute(engagement, static_config):
    """The check the module docstring exists for.

    Assigning `risk.final_rating` here would look identical on screen while skipping the
    feedback record and the procedure re-selection. Both are asserted, so the UI cannot
    quietly stop using the engine.
    """
    client = ScriptedLLMClient(
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", INVENTORY_RISK)
    )
    at = app(engagement, static_config, client)

    at.selectbox(key=f"ratingpick_{INVENTORY_RISK}").set_value("low")
    at.text_input(key=f"riskreason_{INVENTORY_RISK}").set_value("Pre-sold stock.")
    next(b for b in at.button if b.label == "Apply rating").click().run()

    risk = at.session_state["engagement"].line_item("inventory").risk(INVENTORY_RISK)
    assert risk.final_rating is RiskLevel.LOW
    assert risk.system_rating is RiskLevel.HIGH  # the original conclusion survives
    assert at.session_state["engagement"].feedback, "no feedback recorded — recompute skipped"
    assert client.call_count() == 1, "procedures were not re-selected"


def test_a_failed_call_is_reported_and_changes_nothing(engagement, static_config):
    """The recompute rolls back, so the UI's job is to say so rather than half-redraw."""
    at = app(engagement, static_config, FailingLLMClient(error=LLMError("API unavailable")))

    at.selectbox(key=f"ratingpick_{INVENTORY_RISK}").set_value("low")
    at.text_input(key=f"riskreason_{INVENTORY_RISK}").set_value("Pre-sold stock.")
    at = next(b for b in at.button if b.label == "Apply rating").click().run()

    assert any("API unavailable" in error.value for error in at.error)
    risk = at.session_state["engagement"].line_item("inventory").risk(INVENTORY_RISK)
    assert risk.final_rating is RiskLevel.HIGH
    assert at.session_state["engagement"].feedback == []


def test_a_failed_first_run_leaves_the_screen_on_the_unrun_engagement(
    raiatea_engagement, static_config
):
    """The pipeline is transactional, so the UI must not be showing half a plan.

    Facts and inventory succeed, cash fails. Rendering what landed would present a finished-
    looking audit plan with one area silently missing.
    """
    client = ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=[
            scripted_analysis(
                Assertion.VALUATION, static_config.candidate_assertions("inventory")
            ),
            LLMError("API unavailable"),
        ],
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", INVENTORY_RISK),
    )
    at = app(raiatea_engagement, static_config, client)

    at = next(b for b in at.button if b.label == "Run the pipeline").click().run()

    assert any("API unavailable" in error.value for error in at.error)
    audit = at.session_state["engagement"]
    assert audit.materiality is None and audit.company_facts == []
    assert all(not item.assertions for item in audit.line_items)
    # And the screen says so rather than showing a partial plan.
    assert any("Run the pipeline" in info.value for info in at.info)


def test_editing_a_fact_goes_through_the_engine(engagement, static_config):
    """SPEC 16 asks for an editable fact list; SPEC 14 says the ID must survive the edit."""
    client = ScriptedLLMClient(
        analyse_audit_area=[
            scripted_analysis(
                Assertion.VALUATION, static_config.candidate_assertions("inventory")
            ),
            scripted_analysis(Assertion.EXISTENCE, static_config.candidate_assertions("cash")),
        ],
        select_procedures=[
            scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
            scripted_selection("CASH_BANK_CONFIRMATION", "risk_4"),
        ],
    )
    at = app(engagement, static_config, client)
    fact = engagement.company_facts[0]

    at.session_state["factrows"] = {
        "edited_rows": {0: {"value": "over 18 months"}},
        "added_rows": [],
        "deleted_rows": [],
    }
    at.text_input(key="factreason").set_value("The ageing report says 18.")
    at = next(
        b for b in at.button if b.label == "Save facts and re-run both audit areas"
    ).click().run()

    edited = at.session_state["engagement"].company_facts[0]
    assert edited.id == fact.id, "the fact lost its ID and with it its traceability"
    assert edited.value == "over 18 months"
    assert at.session_state["engagement"].feedback, "no feedback recorded — engine bypassed"


def test_removing_a_procedure_records_the_auditors_own_reason(engagement, static_config):
    """Procedure feedback is the clearest input SPEC 19 has; a canned reason gives the
    generalizer no judgement to assess."""
    at = app(engagement, static_config)
    procedure = engagement.line_item("inventory").procedures[0]

    at.text_input(key=f"procreason_{procedure.id}_{INVENTORY_RISK}").set_value(
        "The ageing review already covers this."
    )
    at = next(b for b in at.button if b.label == "Remove from this risk").click().run()

    record = at.session_state["engagement"].feedback[-1]
    assert record.object_id == procedure.id
    assert record.reason == "The ageing review already covers this."


def test_the_feedback_log_offers_analysis_only_where_it_applies(engagement, static_config):
    """SPEC 19: engagement-input records are not analysable, and the action is not offered."""
    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "Pre-sold.",
        client=ScriptedLLMClient(
            select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", INVENTORY_RISK)
        ),
        config=static_config,
    )

    at = app(engagement, static_config)

    assert any(b.label == "Analyse for a methodology rule" for b in at.button)


def test_feedback_analysis_shows_an_engagement_specific_outcome(engagement, static_config):
    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "Pre-sold.",
        client=ScriptedLLMClient(
            select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", INVENTORY_RISK)
        ),
        config=static_config,
    )
    client = ScriptedLLMClient(
        generalize_feedback=FeedbackClassificationOutput(
            classification=EngagementSpecificFeedback(
                type="engagement_specific",
                reason="This turns on this client's particular contract.",
            )
        )
    )
    at = app(engagement, static_config, client)

    at = next(
        button for button in at.button if button.label == "Analyse for a methodology rule"
    ).click().run()

    analysis = at.session_state["engagement"].feedback_analyses[0]
    assert analysis.reason == "This turns on this client's particular contract."
    assert any("Engagement-specific" in info.value for info in at.info)
    assert any("particular contract" in info.value for info in at.info)
