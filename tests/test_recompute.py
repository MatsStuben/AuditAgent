"""M11 verification: overrides, downstream recomputation and feedback (SPEC 17, 18).

Scenario D of SPEC 22 is the centre of this file: overriding one inventory risk must leave the
system's own conclusion intact, re-select only the work answering that risk, and leave every
unrelated object *the same instance*. Identity rather than equality throughout — a copy would
compare equal today and diverge the moment anything else changed.
"""

import pytest

from src.engine.coverage import check_isa_coverage
from src.engine.recompute import (
    RecomputeError,
    add_catalogue_procedure,
    approve_procedure,
    override_assertion_relevance,
    override_risk_rating,
    remove_procedure,
    update_company_context,
    update_financials,
)
from src.llm.client import LLMError, LLMTask
from src.models.audit_objects import (
    Assertion,
    Procedure,
    ProcedureSource,
    RiskLevel,
)
from tests.conftest import (
    CASH_RISK,
    INVENTORY_RISK,
    scripted_analysis,
    scripted_facts,
    scripted_selection,
)
from tests.fakes import FailingLLMClient, ScriptedLLMClient


def valuation(engagement):
    return next(
        a
        for a in engagement.line_item("inventory").assertions
        if a.assertion is Assertion.VALUATION
    )


def reselect(procedure_id: str, *risk_ids: str) -> ScriptedLLMClient:
    """A client scripted for one scoped procedure-selection call and nothing else."""
    return ScriptedLLMClient(select_procedures=scripted_selection(procedure_id, *risk_ids))


# --- Scenario D: risk rating override ------------------------------------------------------


def test_the_system_conclusion_survives_the_override(engagement):
    """SPEC 11/17: `system_rating` is the engine's answer and is never rewritten."""
    risk = engagement.line_item("inventory").risk(INVENTORY_RISK)
    assert risk.system_rating is RiskLevel.HIGH

    override_risk_rating(
        engagement,
        INVENTORY_RISK,
        RiskLevel.LOW,
        "Pre-sold stock with negligible obsolescence exposure.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK),
    )

    assert risk.system_rating is RiskLevel.HIGH
    assert risk.final_rating is RiskLevel.LOW
    assert risk.is_overridden is True
    assert risk.override_reason.startswith("Pre-sold stock")
    # The inputs to the rating are the original conclusion too, not just the rating itself.
    assert risk.likelihood is RiskLevel.HIGH
    assert risk.magnitude is RiskLevel.HIGH


def test_the_override_is_recoverable_from_the_feedback_record(engagement):
    feedback = override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "Because.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK),
    )

    assert engagement.feedback == [feedback]
    assert feedback.object_type == "risk_assessment"
    assert feedback.object_id == INVENTORY_RISK
    assert feedback.before == {"final_rating": "high"}
    assert feedback.after == {"final_rating": "low"}
    assert feedback.reason == "Because."


def test_exactly_one_scoped_selection_call_is_made(engagement):
    """SPEC 17: one call, and *not* audit area analysis — that would replace the risk."""
    client = reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK)

    override_risk_rating(engagement, INVENTORY_RISK, RiskLevel.LOW, "Because.", client=client)

    assert client.call_count(LLMTask.SELECT_PROCEDURES) == 1
    assert client.call_count(LLMTask.ANALYSE_AUDIT_AREA) == 0
    assert client.call_count(LLMTask.EXTRACT_COMPANY_FACTS) == 0


def test_the_scoped_call_shows_the_model_only_the_changed_risk(engagement, two_risk_engagement):
    """A scoped call must not invite the model to re-answer risks nobody touched."""
    client = reselect("INV_SUBSEQUENT_SALES", "risk_1")

    override_risk_rating(two_risk_engagement, "risk_1", RiskLevel.LOW, "Because.", client=client)

    (call,) = client.calls
    assert "risk_1" in call.user
    assert "risk_2" not in call.user
    # And it sees the override, not the superseded system rating (SPEC 13).
    assert "rating: low" in call.user


def test_unrelated_audit_areas_keep_their_objects(engagement):
    """The cash subtree must be untouched — same instances, not merely equal ones."""
    cash = engagement.line_item("cash")
    before = (cash.assertions, cash.risk(CASH_RISK), cash.procedures[0])

    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "Because.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK),
    )

    assert cash.assertions is before[0]
    assert cash.risk(CASH_RISK) is before[1]
    assert cash.procedures[0] is before[2]


def test_unrelated_procedures_in_the_same_area_keep_their_identity(
    static_config, two_risk_engagement
):
    """The narrower check: an override on one risk must not replace work in its own area.

    `risk_2` here is answered by a procedure of its own, so it is disjoint from the changed
    risk and must come through the override as the same object.
    """
    inventory = two_risk_engagement.line_item("inventory")
    inventory.procedures[0].risk_ids = ["risk_1"]
    untouched = Procedure(
        id="proc_99",
        risk_ids=["risk_2"],
        procedure_id="INV_AGED_STOCK_REVIEW",
        name="Aged stock review",
        description="Review the ageing profile.",
        procedure_type="analytical",
        rationale="Existing work.",
        isa_refs=["ISA330.6_7"],
    )
    inventory.procedures.append(untouched)

    override_risk_rating(
        two_risk_engagement, "risk_1", RiskLevel.LOW, "Because.",
        client=reselect("INV_SUBSEQUENT_SALES", "risk_1"), config=static_config,
    )

    # The superseded INV_SUBSEQUENT_SALES object is replaced; `untouched` is retained as is.
    assert inventory.procedures[0] is untouched
    assert untouched.risk_ids == ["risk_2"]
    assert [p.procedure_id for p in inventory.procedures] == [
        "INV_AGED_STOCK_REVIEW",
        "INV_SUBSEQUENT_SALES",
    ]


def test_a_shared_procedure_keeps_its_still_valid_links(static_config, two_risk_engagement):
    """A procedure in the affected closure may be updated, but not stripped of other work."""
    inventory = two_risk_engagement.line_item("inventory")
    shared = inventory.procedures[0]
    assert shared.risk_ids == ["risk_1", "risk_2"]

    override_risk_rating(
        two_risk_engagement, "risk_1", RiskLevel.LOW, "Because.",
        client=reselect("INV_SUBSEQUENT_SALES", "risk_1"), config=static_config,
    )

    # Same object, both links intact: re-selected for risk_1, retained for risk_2.
    assert inventory.procedures == [shared]
    assert set(shared.risk_ids) == {"risk_1", "risk_2"}


def test_a_procedure_answering_only_the_changed_risk_can_be_replaced(
    static_config, engagement
):
    """A low rating may call for different work; the superseded procedure goes."""
    inventory = engagement.line_item("inventory")
    assert [p.procedure_id for p in inventory.procedures] == ["INV_SUBSEQUENT_SALES"]

    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "Because.",
        client=reselect("INV_AGED_STOCK_REVIEW", INVENTORY_RISK), config=static_config,
    )

    assert [p.procedure_id for p in inventory.procedures] == ["INV_AGED_STOCK_REVIEW"]
    assert inventory.procedures[0].risk_ids == [INVENTORY_RISK]


def test_coverage_is_still_clean_after_the_override(static_config, engagement):
    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "Because.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK), config=static_config,
    )

    assert check_isa_coverage(engagement, static_config).satisfied


def test_an_override_that_changes_nothing_is_not_feedback(engagement):
    client = reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK)

    assert (
        override_risk_rating(engagement, INVENTORY_RISK, RiskLevel.HIGH, "No change.",
                             client=client)
        is None
    )
    assert engagement.feedback == []
    assert client.calls == []


def test_returning_to_the_system_rating_clears_the_override_marker(static_config, engagement):
    """`is_overridden` describes the current state, not the history.

    An auditor who reverts is no longer departing from the engine's conclusion, and the risk
    card must stop saying so. Both moves stay in the append-only log.
    """
    risk = engagement.line_item("inventory").risk(INVENTORY_RISK)

    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "Pre-sold.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK), config=static_config,
    )
    assert risk.is_overridden is True

    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.HIGH, "On reflection, the system was right.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK), config=static_config,
    )

    assert risk.final_rating is risk.system_rating
    assert risk.is_overridden is False
    assert risk.override_reason is None
    assert [(f.before, f.after) for f in engagement.feedback] == [
        ({"final_rating": "high"}, {"final_rating": "low"}),
        ({"final_rating": "low"}, {"final_rating": "high"}),
    ]


def test_a_rating_that_lands_somewhere_new_is_still_an_override(static_config, engagement):
    risk = engagement.line_item("inventory").risk(INVENTORY_RISK)

    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.MEDIUM, "Somewhere between.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK), config=static_config,
    )

    assert risk.is_overridden is True
    assert risk.override_reason == "Somewhere between."


def test_an_unknown_risk_raises(engagement):
    with pytest.raises(RecomputeError, match="risk_99"):
        override_risk_rating(
            engagement, "risk_99", RiskLevel.LOW, "Because.", client=reselect("X", "y")
        )


# --- assertion relevance -------------------------------------------------------------------


def test_ruling_an_assertion_out_drops_its_risks_and_procedures(engagement):
    """SPEC 17: deterministic, no LLM call."""
    inventory = engagement.line_item("inventory")
    client = ScriptedLLMClient()

    override_assertion_relevance(
        engagement, valuation(engagement).id, False, "Not relevant here.", client=client
    )

    assert valuation(engagement).relevant is False
    assert valuation(engagement).risks == []
    assert inventory.procedures == []
    assert client.calls == []
    assert inventory.dangling_risk_ids() == set()


def test_ruling_out_leaves_a_shared_procedure_answering_its_other_risks(
    static_config, two_risk_engagement
):
    """Only the dropped risks are detached; the procedure survives on what remains.

    Built by moving `risk_2` onto a second assertion, so ruling out valuation drops one of the
    shared procedure's two risks and not the other.
    """
    inventory = two_risk_engagement.line_item("inventory")
    completeness = next(
        a for a in inventory.assertions if a.assertion is Assertion.COMPLETENESS
    )
    moved = valuation(two_risk_engagement).risks.pop()
    moved.assertion_id = completeness.id
    completeness.relevant = True
    completeness.risks = [moved]
    shared = inventory.procedures[0]

    override_assertion_relevance(
        two_risk_engagement, valuation(two_risk_engagement).id, False, "Because.",
        client=ScriptedLLMClient(), config=static_config,
    )

    assert inventory.procedures == [shared]
    assert shared.risk_ids == ["risk_2"]


def test_ruling_an_assertion_in_re_analyses_the_area(static_config, engagement):
    """SPEC 17: two calls, because the missing risks cannot be derived deterministically."""
    existence = next(
        a
        for a in engagement.line_item("inventory").assertions
        if a.assertion is Assertion.EXISTENCE
    )
    client = ScriptedLLMClient(
        analyse_audit_area=scripted_analysis(
            Assertion.EXISTENCE, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_PHYSICAL_COUNT_ATTENDANCE", "risk_3"),
    )

    override_assertion_relevance(
        engagement, existence.id, True, "Count coverage is thin.",
        client=client, config=static_config,
    )

    inventory = engagement.line_item("inventory")
    regenerated = next(a for a in inventory.assertions if a.assertion is Assertion.EXISTENCE)
    assert regenerated.relevant is True
    assert [r.id for r in regenerated.risks] == ["risk_3"]
    assert client.call_count(LLMTask.ANALYSE_AUDIT_AREA) == 1
    assert client.call_count(LLMTask.SELECT_PROCEDURES) == 1
    assert inventory.dangling_risk_ids() == set()


def test_flipping_back_regenerates_the_dropped_work(static_config, engagement):
    """Out then in: the risks and procedures return, from the fresh analysis."""
    client = ScriptedLLMClient(
        analyse_audit_area=scripted_analysis(
            Assertion.VALUATION, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
    )
    assertion_id = valuation(engagement).id

    override_assertion_relevance(
        engagement, assertion_id, False, "Out.", client=client, config=static_config
    )
    assert engagement.line_item("inventory").procedures == []

    override_assertion_relevance(
        engagement, valuation(engagement).id, True, "Back in.",
        client=client, config=static_config,
    )

    inventory = engagement.line_item("inventory")
    assert [r.id for r in valuation(engagement).risks] == ["risk_3"]
    assert [p.procedure_id for p in inventory.procedures] == ["INV_SUBSEQUENT_SALES"]
    assert [f.after for f in engagement.feedback] == [{"relevant": False}, {"relevant": True}]


def test_the_auditors_verdict_wins_over_a_re_analysis_that_disagrees(static_config, engagement):
    """Left with no risks, which coverage then reports — better than inventing one."""
    existence = next(
        a
        for a in engagement.line_item("inventory").assertions
        if a.assertion is Assertion.EXISTENCE
    )
    client = ScriptedLLMClient(
        analyse_audit_area=scripted_analysis(
            Assertion.VALUATION, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
    )

    override_assertion_relevance(
        engagement, existence.id, True, "Because.", client=client, config=static_config
    )

    inventory = engagement.line_item("inventory")
    regenerated = next(a for a in inventory.assertions if a.assertion is Assertion.EXISTENCE)
    assert regenerated.relevant is True
    assert regenerated.risks == []
    gaps = check_isa_coverage(engagement, static_config).gaps
    assert [g.object_id for g in gaps] == [regenerated.id]


def test_re_analysis_does_not_touch_the_other_area(static_config, engagement):
    cash = engagement.line_item("cash")
    before = cash.assertions
    client = ScriptedLLMClient(
        analyse_audit_area=scripted_analysis(
            Assertion.VALUATION, static_config.candidate_assertions("inventory")
        ),
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
    )
    existence = next(a for a in before if a.assertion is Assertion.EXISTENCE)
    assert existence.relevant is True  # cash's relevant assertion, left alone

    override_assertion_relevance(
        engagement, valuation(engagement).id, False, "Out.",
        client=client, config=static_config,
    )

    assert cash.assertions is before


# --- procedure overrides ---------------------------------------------------------------------


def test_adding_a_catalogue_procedure(static_config, engagement):
    inventory = engagement.line_item("inventory")

    feedback = add_catalogue_procedure(
        engagement, INVENTORY_RISK, "INV_AGED_STOCK_REVIEW", "Wanted the ageing work too.",
        config=static_config,
    )

    added = inventory.procedures[-1]
    assert added.procedure_id == "INV_AGED_STOCK_REVIEW"
    assert added.risk_ids == [INVENTORY_RISK]
    assert added.source is ProcedureSource.CATALOGUE
    assert added.approved is True
    assert added.isa_refs == ["ISA330.6_7"]
    assert feedback.object_id == added.id


def test_adding_a_procedure_the_catalogue_does_not_offer_raises(static_config, engagement):
    """The same constraint the model is held to: no link approved methodology denies."""
    with pytest.raises(RecomputeError, match="CASH_BANK_CONFIRMATION"):
        add_catalogue_procedure(
            engagement, INVENTORY_RISK, "CASH_BANK_CONFIRMATION", "Because.",
            config=static_config,
        )


def test_adding_a_procedure_the_area_already_holds_links_the_existing_object(
    static_config, two_risk_engagement
):
    inventory = two_risk_engagement.line_item("inventory")
    existing = inventory.procedures[0]
    existing.risk_ids = ["risk_1"]

    add_catalogue_procedure(
        two_risk_engagement, "risk_2", "INV_SUBSEQUENT_SALES", "Covers this one as well.",
        config=static_config,
    )

    assert inventory.procedures == [existing]
    assert existing.risk_ids == ["risk_1", "risk_2"]


def test_removing_detaches_one_risk_and_keeps_the_procedure(static_config, two_risk_engagement):
    inventory = two_risk_engagement.line_item("inventory")
    shared = inventory.procedures[0]

    feedback = remove_procedure(two_risk_engagement, shared.id, "risk_1", "Not needed here.")

    assert inventory.procedures == [shared]
    assert shared.risk_ids == ["risk_2"]
    assert feedback.before == {"risk_ids": ["risk_1", "risk_2"]}
    assert feedback.after == {"risk_ids": ["risk_2"]}


def test_removing_the_last_reference_drops_the_procedure(static_config, engagement):
    inventory = engagement.line_item("inventory")
    procedure = inventory.procedures[0]

    remove_procedure(engagement, procedure.id, INVENTORY_RISK, "Not needed.")

    assert inventory.procedures == []
    gaps = check_isa_coverage(engagement, static_config).gaps
    assert [(g.requirement_id, g.object_id) for g in gaps] == [("ISA330.6_7", INVENTORY_RISK)]


def test_approving_a_suggestion_closes_the_gap(static_config, engagement):
    inventory = engagement.line_item("inventory")
    suggestion = inventory.procedures[0].model_copy(
        update={
            "id": "proc_99",
            "source": ProcedureSource.AI_SUGGESTION,
            "approved": False,
            "procedure_id": None,
        }
    )
    inventory.procedures = [suggestion]
    assert not check_isa_coverage(engagement, static_config).satisfied

    feedback = approve_procedure(engagement, "proc_99", "Reviewed and accepted.")

    assert suggestion.approved is True
    assert feedback.before == {"approved": False}
    assert check_isa_coverage(engagement, static_config).satisfied


def test_approving_a_catalogue_procedure_raises(engagement):
    procedure = engagement.line_item("inventory").procedures[0]

    with pytest.raises(RecomputeError, match="catalogue procedure"):
        approve_procedure(engagement, procedure.id, "Because.")


# --- context and financials -------------------------------------------------------------------


def test_a_context_change_reruns_facts_and_every_area(static_config, engagement):
    """SPEC 17: `1 + 2n` calls — one extraction, then both calls for each of the two areas."""
    client = ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=[
            scripted_analysis(
                Assertion.VALUATION, static_config.candidate_assertions("inventory")
            ),
            scripted_analysis(
                Assertion.EXISTENCE, static_config.candidate_assertions("cash")
            ),
        ],
        select_procedures=[
            scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
            scripted_selection("CASH_BANK_CONFIRMATION", "risk_4"),
        ],
    )
    materiality = engagement.materiality

    feedback = update_company_context(
        engagement, "A new description of the business.", "Client sent an update.",
        client=client, config=static_config,
    )

    assert engagement.company_context == "A new description of the business."
    assert client.call_count(LLMTask.EXTRACT_COMPANY_FACTS) == 1
    assert client.call_count(LLMTask.ANALYSE_AUDIT_AREA) == 2
    assert client.call_count(LLMTask.SELECT_PROCEDURES) == 2
    assert engagement.materiality is materiality  # financials did not move
    assert feedback.before["company_context"].startswith("Raiatea")
    assert check_isa_coverage(engagement, static_config).satisfied


def test_an_unchanged_context_does_nothing(engagement):
    client = ScriptedLLMClient()

    assert update_company_context(engagement, engagement.company_context, client=client) is None
    assert engagement.feedback == []


def test_a_pbt_change_moves_materiality_and_reruns_only_scope_changes(
    static_config, engagement
):
    """Cash falls out of scope and is cleared; inventory stays and is left alone — no calls."""
    inventory = engagement.line_item("inventory")
    before = inventory.assertions
    cash = engagement.line_item("cash")
    assert cash.material is True

    feedback = update_financials(
        engagement, {"profit_before_tax": 80_000_000}, "Revised group figures.",
        client=ScriptedLLMClient(), config=static_config,
    )

    assert engagement.materiality.amount == pytest.approx(4_000_000)
    assert cash.material is False
    assert cash.assertions == [] and cash.procedures == []
    assert cash.metrics is not None and cash.is_audit_area is True  # still displayed
    assert inventory.assertions is before
    assert feedback.before == {"profit_before_tax": 5_240_000.0}


def test_an_area_entering_scope_runs_both_of_its_calls(static_config, raiatea_engagement):
    """Started below the threshold, so entering scope is the first work the area gets."""
    from src.engine.pipeline import run_pipeline

    engagement = run_pipeline(
        raiatea_engagement,
        client=ScriptedLLMClient(
            extract_company_facts=scripted_facts(),
            analyse_audit_area=[
                scripted_analysis(
                    Assertion.VALUATION, static_config.candidate_assertions("inventory")
                ),
                scripted_analysis(
                    Assertion.EXISTENCE, static_config.candidate_assertions("cash")
                ),
            ],
            select_procedures=[
                scripted_selection("INV_SUBSEQUENT_SALES", "risk_1"),
                scripted_selection("CASH_BANK_CONFIRMATION", "risk_2"),
            ],
        ),
        config=static_config,
    )
    update_financials(
        engagement, {"profit_before_tax": 80_000_000}, "Up.",
        client=ScriptedLLMClient(), config=static_config,
    )
    assert engagement.line_item("cash").assertions == []

    client = ScriptedLLMClient(
        analyse_audit_area=scripted_analysis(
            Assertion.EXISTENCE, static_config.candidate_assertions("cash")
        ),
        select_procedures=scripted_selection("CASH_BANK_CONFIRMATION", "risk_3"),
    )
    update_financials(
        engagement, {"profit_before_tax": 5_240_000}, "Back down.",
        client=client, config=static_config,
    )

    cash = engagement.line_item("cash")
    assert cash.material is True
    assert [r.id for r in cash.all_risks] == ["risk_3"]  # never a reused id
    assert client.call_count(LLMTask.ANALYSE_AUDIT_AREA) == 1
    assert client.call_count(LLMTask.SELECT_PROCEDURES) == 1


def test_an_unknown_line_item_raises(engagement):
    with pytest.raises(RecomputeError, match="goodwill"):
        update_financials(
            engagement, {"goodwill": 1.0}, client=ScriptedLLMClient(), config=None
        )


def test_unchanged_financials_do_nothing(static_config, engagement):
    cy = engagement.line_item("inventory").cy

    assert (
        update_financials(
            engagement, {"inventory": cy}, client=ScriptedLLMClient(), config=static_config
        )
        is None
    )
    assert engagement.feedback == []


# --- a failed LLM call leaves nothing half-applied ---------------------------------------------


def test_a_failed_selection_leaves_the_rating_and_the_log_untouched(engagement):
    """The auditor must not be shown a new rating with the old work beneath it."""
    inventory = engagement.line_item("inventory")
    risk = inventory.risk(INVENTORY_RISK)
    before = inventory.procedures

    with pytest.raises(LLMError):
        override_risk_rating(
            engagement, INVENTORY_RISK, RiskLevel.LOW, "Because.", client=FailingLLMClient(
                error=LLMError("API unavailable")
            ),
        )

    assert risk.final_rating is RiskLevel.HIGH
    assert risk.is_overridden is False
    assert risk.override_reason is None
    assert inventory.procedures is before
    assert engagement.feedback == []


def test_a_failed_extraction_leaves_the_old_context_in_place(engagement):
    """A new context above facts drawn from the old one is a file nobody can tell is stale."""
    original = engagement.company_context
    facts = engagement.company_facts

    with pytest.raises(LLMError):
        update_company_context(
            engagement, "Something new.", "Because.",
            client=FailingLLMClient(error=LLMError("API unavailable")),
        )

    assert engagement.company_context == original
    assert engagement.company_facts is facts
    assert engagement.feedback == []


def test_a_context_change_failing_on_the_second_area_rolls_back_the_first(
    static_config, engagement
):
    """The whole recompute is one unit: the area that succeeded is put back too."""
    inventory = engagement.line_item("inventory")
    before = (engagement.company_facts, inventory.assertions, inventory.procedures)
    client = ScriptedLLMClient(
        extract_company_facts=scripted_facts(),
        analyse_audit_area=[
            scripted_analysis(
                Assertion.VALUATION, static_config.candidate_assertions("inventory")
            ),
            LLMError("API unavailable"),  # cash, the second area
        ],
        select_procedures=scripted_selection("INV_SUBSEQUENT_SALES", "risk_3"),
    )

    with pytest.raises(LLMError):
        update_company_context(
            engagement, "Something new.", "Because.", client=client, config=static_config
        )

    assert engagement.company_context.startswith("Raiatea")
    assert engagement.company_facts is before[0]
    assert inventory.assertions is before[1]
    assert inventory.procedures is before[2]
    assert engagement.feedback == []


def test_a_failed_rescope_restores_the_figures_and_materiality(static_config, engagement):
    """A half-applied scope change is a threshold that no longer matches the file under it."""
    cash = engagement.line_item("cash")
    pbt = engagement.line_item("profit_before_tax")
    # Push cash out of scope first, so putting the figure back makes it re-enter — and it is
    # that area's re-analysis that fails.
    update_financials(
        engagement, {"profit_before_tax": 80_000_000}, "Up.",
        client=ScriptedLLMClient(), config=static_config,
    )
    before = (engagement.materiality, pbt.cy, cash.material, cash.assertions)

    with pytest.raises(LLMError):
        update_financials(
            engagement, {"profit_before_tax": 5_240_000}, "Back down.",
            client=FailingLLMClient(error=LLMError("API unavailable")),
            config=static_config,
        )

    assert engagement.materiality is before[0]
    assert pbt.cy == before[1] == 80_000_000
    assert cash.material is before[2] is False
    assert cash.assertions is before[3]
    assert len(engagement.feedback) == 1  # the first change only


def test_ids_consumed_by_a_failed_run_are_not_reused(static_config, engagement):
    """SPEC 14: a retained reference must stay visibly unresolved, never silently rebound."""
    with pytest.raises(LLMError):
        update_company_context(
            engagement, "Something new.", "Because.",
            client=ScriptedLLMClient(
                extract_company_facts=scripted_facts(),
                analyse_audit_area=LLMError("API unavailable"),
            ),
            config=static_config,
        )

    assert engagement.company_facts[0].id == "fact_1"  # the surviving, restored fact
    assert engagement.id_sequences["fact"] == 2  # fact_2 was consumed and is gone for good


# --- the feedback log ------------------------------------------------------------------------


def test_feedback_ids_are_monotonic_and_never_reused(static_config, engagement):
    override_risk_rating(
        engagement, INVENTORY_RISK, RiskLevel.LOW, "One.",
        client=reselect("INV_SUBSEQUENT_SALES", INVENTORY_RISK), config=static_config,
    )
    override_risk_rating(
        engagement, CASH_RISK, RiskLevel.LOW, "Two.",
        client=reselect("CASH_BANK_CONFIRMATION", CASH_RISK), config=static_config,
    )

    assert [f.id for f in engagement.feedback] == ["feedback_1", "feedback_2"]
