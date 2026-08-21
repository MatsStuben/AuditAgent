"""M7 verification: per-area procedure selection (SPEC 6.1, 13, ISA 330.6/330.7)."""

import pytest

from src.engine.materiality import calculate_materiality
from src.engine.scoping import scope_line_items
from src.llm.client import LLMTask
from src.llm.procedure_selector import (
    MISSING_RATIONALE,
    ProcedureSelectionError,
    build_user_message,
    select_procedures,
)
from src.llm.prompts import SELECT_PROCEDURES
from src.llm.schemas import ProcedureSelectionOutput
from src.models.audit_objects import (
    Assertion,
    AssertionAssessment,
    ProcedureSource,
    RiskAssessment,
    RiskLevel,
)
from src.models.engagement import CompanyFact
from tests.conftest import make_engagement
from tests.fakes import ScriptedLLMClient


@pytest.fixture
def engagement(raiatea_engagement, static_config):
    raiatea_engagement.company_context = (
        "Raiatea is a fast-growing fashion retailer with highly seasonal inventory."
    )
    raiatea_engagement.company_facts = [
        CompanyFact(
            id="fact_1", fact_type="inventory_ageing", value="over 12 months", rationale="Aged."
        )
    ]
    raiatea_engagement.materiality = calculate_materiality(raiatea_engagement)
    scope_line_items(raiatea_engagement, static_config)
    return raiatea_engagement


@pytest.fixture
def inventory(engagement):
    """Inventory with two assessed assertions carrying three risks between them."""
    item = engagement.line_item("inventory")
    existence = AssertionAssessment(
        id="assertion_1", line_item_id=item.id, assertion=Assertion.EXISTENCE,
        relevant=True, rationale="Dispersed stock.", isa_refs=["ISA315.29"],
        risks=[
            _risk("risk_1", "assertion_1", "Shrinkage.", RiskLevel.HIGH),
            _risk("risk_2", "assertion_1", "Over-receipting.", RiskLevel.MEDIUM),
        ],
    )
    valuation = AssertionAssessment(
        id="assertion_2", line_item_id=item.id, assertion=Assertion.VALUATION,
        relevant=True, rationale="Aged stock.", isa_refs=["ISA315.29"],
        risks=[_risk("risk_3", "assertion_2", "Carried above NRV.", RiskLevel.HIGH)],
    )
    rights = AssertionAssessment(
        id="assertion_3", line_item_id=item.id, assertion=Assertion.RIGHTS_AND_OBLIGATIONS,
        relevant=False, rationale="No consignment.", isa_refs=["ISA315.29"],
    )
    item.assertions = [existence, valuation, rights]
    return item


def _risk(risk_id, assertion_id, description, rating) -> RiskAssessment:
    return RiskAssessment(
        id=risk_id, assertion_id=assertion_id, risk_description=description,
        likelihood=rating, magnitude=rating, system_rating=rating, final_rating=rating,
        rationale="Because.",
    )


def _selection(procedure_id, risk_ids, rationale="Addresses the risk.") -> dict:
    return {"procedure_id": procedure_id, "risk_ids": risk_ids, "rationale": rationale}


def _output(*selections, suggestions=()) -> ProcedureSelectionOutput:
    return ProcedureSelectionOutput(
        selected_procedures=list(selections), suggested_new_procedures=list(suggestions)
    )


def _client(output) -> ScriptedLLMClient:
    return ScriptedLLMClient(select_procedures=output)


# --- one call per area ---------------------------------------------------------------


def test_whole_area_costs_exactly_one_call(engagement, inventory):
    """SPEC 6.1: three risks across two assertions, one response."""
    client = _client(
        _output(
            _selection("INV_PHYSICAL_COUNT", ["risk_1", "risk_2"]),
            _selection("INV_SUBSEQUENT_SALES", ["risk_3"]),
        )
    )

    procedures = select_procedures(inventory, engagement, client=client)

    assert client.call_count() == 1
    assert len(procedures) == 2
    client.assert_all_consumed()


def test_uses_the_selection_task_and_prompt(engagement, inventory):
    client = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_1"])))

    select_procedures(inventory, engagement, client=client)

    call = client.calls_for(LLMTask.SELECT_PROCEDURES)[0]
    assert call.system == SELECT_PROCEDURES
    assert call.output_format is ProcedureSelectionOutput


# --- one procedure, several risks ----------------------------------------------------


def test_a_procedure_covering_two_risks_is_one_object(engagement, inventory):
    """The point of plural `risk_ids`: no duplication, one thing to keep in sync."""
    client = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_1", "risk_2"])))

    procedures = select_procedures(inventory, engagement, client=client)
    inventory.procedures = procedures

    assert len(procedures) == 1
    assert procedures[0].risk_ids == ["risk_1", "risk_2"]
    # Reachable from both risks, and it is the same object.
    assert inventory.procedures_for("risk_1")[0] is inventory.procedures_for("risk_2")[0]


def test_the_same_procedure_listed_twice_is_merged(engagement, inventory):
    """A split response must not produce two objects for one catalogue entry."""
    client = _client(
        _output(
            _selection("INV_PHYSICAL_COUNT", ["risk_1"]),
            _selection("INV_PHYSICAL_COUNT", ["risk_2"]),
        )
    )

    procedures = select_procedures(inventory, engagement, client=client)

    assert len(procedures) == 1
    assert procedures[0].risk_ids == ["risk_1", "risk_2"]


def test_each_procedure_is_fully_linked(engagement, inventory, static_config):
    client = _client(_output(_selection("INV_SUBSEQUENT_SALES", ["risk_3"])))

    procedure = select_procedures(inventory, engagement, client=client)[0]

    assert procedure.id.startswith("proc_")
    assert procedure.procedure_id == "INV_SUBSEQUENT_SALES"
    assert procedure.isa_refs == ["ISA330.6_7"] == static_config.isa_refs_for("Procedure")
    assert procedure.source is ProcedureSource.CATALOGUE
    assert procedure.approved is True
    assert procedure.requires_approval is False


def test_catalogue_details_come_from_config_not_the_model(engagement, inventory):
    """Name, description, type and evidence strength are approved methodology."""
    client = _client(_output(_selection("INV_SUBSEQUENT_SALES", ["risk_3"])))

    procedure = select_procedures(inventory, engagement, client=client)[0]

    assert procedure.name == "Test post-year-end sales"
    assert procedure.evidence_strength.value == "high"
    assert procedure.procedure_type == "test_of_details"
    assert "post-year-end sales evidence" in procedure.description


def test_procedure_ids_are_monotonic(engagement, inventory):
    client = _client(
        _output(
            _selection("INV_PHYSICAL_COUNT", ["risk_1"]),
            _selection("INV_SUBSEQUENT_SALES", ["risk_3"]),
        )
    )

    procedures = select_procedures(inventory, engagement, client=client)

    assert [p.id for p in procedures] == ["proc_1", "proc_2"]


def test_results_are_not_assigned_to_the_line_item(engagement, inventory):
    select_procedures(
        inventory, engagement, client=_client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_1"])))
    )

    assert inventory.procedures == []


# --- re-selection replaces ------------------------------------------------------------


def test_reselection_replaces_and_leaves_no_dangling_references(engagement, inventory):
    """SPEC 17: a rating override re-runs this call; stale procedures must not survive."""
    first = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_1", "risk_2"])))
    inventory.procedures = select_procedures(inventory, engagement, client=first)
    assert len(inventory.procedures) == 1

    second = _client(_output(_selection("INV_SUBSEQUENT_SALES", ["risk_3"])))
    inventory.procedures = select_procedures(inventory, engagement, client=second)

    assert [p.procedure_id for p in inventory.procedures] == ["INV_SUBSEQUENT_SALES"]
    assert inventory.dangling_risk_ids() == set()
    assert inventory.procedures_for("risk_1") == []  # the old coverage is gone


# --- AI suggestions -------------------------------------------------------------------


def test_ai_suggestion_is_flagged_unapproved(engagement, inventory):
    client = _client(
        _output(
            _selection("INV_SUBSEQUENT_SALES", ["risk_3"]),
            suggestions=[
                {
                    "description": "Obtain third-party valuations for aged seasonal lines.",
                    "risk_ids": ["risk_3"],
                    "rationale": "The catalogue has no independent valuation procedure.",
                }
            ],
        )
    )

    procedures = select_procedures(inventory, engagement, client=client)

    suggestion = next(p for p in procedures if p.source is ProcedureSource.AI_SUGGESTION)
    assert suggestion.approved is False
    assert suggestion.requires_approval is True
    assert suggestion.procedure_id is None  # no catalogue entry exists
    assert suggestion.risk_ids == ["risk_3"]


def test_ai_suggestion_has_no_assessed_evidence_strength(engagement, inventory):
    """Strength is approved methodology; claiming one would imply it had been vetted."""
    client = _client(
        _output(
            suggestions=[
                {"description": "Something novel.", "risk_ids": ["risk_3"], "rationale": "r"}
            ]
        )
    )

    suggestion = select_procedures(inventory, engagement, client=client)[0]

    assert suggestion.evidence_strength is None


def test_long_suggestion_description_is_truncated_for_its_name(engagement, inventory):
    description = "A very long procedure description that runs well past any sensible name " * 2
    client = _client(
        _output(
            suggestions=[
                {"description": description, "risk_ids": ["risk_3"], "rationale": "r"}
            ]
        )
    )

    suggestion = select_procedures(inventory, engagement, client=client)[0]

    assert len(suggestion.name) <= 61  # 60 plus the ellipsis
    assert suggestion.description == description.strip()  # full text preserved


# --- defensive handling of model output ----------------------------------------------


def test_procedure_outside_the_offered_subset_is_rejected(engagement, inventory):
    """A cash procedure cannot enter the inventory programme."""
    client = _client(
        _output(
            _selection("CASH_BANK_CONFIRMATION", ["risk_1"]),
            _selection("INV_PHYSICAL_COUNT", ["risk_1"]),
        )
    )

    procedures = select_procedures(inventory, engagement, client=client)

    assert [p.procedure_id for p in procedures] == ["INV_PHYSICAL_COUNT"]


def test_invented_procedure_id_is_rejected(engagement, inventory):
    client = _client(_output(_selection("INV_MADE_UP", ["risk_1"])))

    assert select_procedures(inventory, engagement, client=client) == []


def test_unknown_risk_ids_are_dropped_but_the_procedure_survives(engagement, inventory):
    client = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_1", "risk_99"])))

    procedures = select_procedures(inventory, engagement, client=client)

    assert len(procedures) == 1
    assert procedures[0].risk_ids == ["risk_1"]


def test_procedure_naming_only_unknown_risks_is_discarded(engagement, inventory):
    """Dropping every reference leaves it addressing nothing, which breaks SPEC 14."""
    client = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_98", "risk_99"])))

    assert select_procedures(inventory, engagement, client=client) == []


def test_suggestion_naming_only_unknown_risks_is_discarded(engagement, inventory):
    client = _client(
        _output(suggestions=[{"description": "d", "risk_ids": ["risk_99"], "rationale": "r"}])
    )

    assert select_procedures(inventory, engagement, client=client) == []


def test_procedure_linked_to_a_risk_for_an_unsupported_assertion_is_rejected(
    engagement, inventory
):
    """INV_PHYSICAL_COUNT covers existence/completeness, so it cannot answer a valuation risk.

    Storing the link would make reverse coverage report risk_3 as answered while the approved
    catalogue says that procedure does not address valuation — hiding a real gap.
    """
    client = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_3"])))

    assert select_procedures(inventory, engagement, client=client) == []


def test_mismatched_risk_ids_are_dropped_but_valid_ones_survive(engagement, inventory):
    """risk_1 is existence (supported); risk_3 is valuation (not)."""
    client = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_1", "risk_3"])))

    procedures = select_procedures(inventory, engagement, client=client)

    assert len(procedures) == 1
    assert procedures[0].risk_ids == ["risk_1"]


def test_a_procedure_serving_two_assertions_may_answer_either(engagement, inventory):
    """INV_COST_TEST covers accuracy and valuation, so a valuation risk is legitimate."""
    client = _client(_output(_selection("INV_COST_TEST", ["risk_3"])))

    procedures = select_procedures(inventory, engagement, client=client)

    assert [p.procedure_id for p in procedures] == ["INV_COST_TEST"]
    assert procedures[0].risk_ids == ["risk_3"]


def test_ai_suggestions_are_not_assertion_constrained(engagement, inventory):
    """A suggestion has no catalogue mapping, so there is nothing for it to contradict."""
    client = _client(
        _output(
            suggestions=[
                {"description": "Novel cut-off test.", "risk_ids": ["risk_1", "risk_3"],
                 "rationale": "r"}
            ]
        )
    )

    suggestion = select_procedures(inventory, engagement, client=client)[0]

    assert suggestion.risk_ids == ["risk_1", "risk_3"]


def test_blank_rationale_is_marked_rather_than_stored_empty(engagement, inventory):
    client = _client(_output(_selection("INV_PHYSICAL_COUNT", ["risk_1"], rationale="  ")))

    procedure = select_procedures(inventory, engagement, client=client)[0]

    assert procedure.rationale == MISSING_RATIONALE


def test_empty_selection_returns_nothing(engagement, inventory):
    """Leaves the ISA 330.6/7 gap for coverage to report rather than inventing work."""
    assert select_procedures(inventory, engagement, client=_client(_output())) == []


# --- partial catalogue coverage -------------------------------------------------------


def _partial_catalogue_config(static_config):
    """Config where nothing in the catalogue covers inventory valuation.

    The shipped data happens to cover every candidate assertion, but config validation only
    requires one procedure per *area*, so this is reachable once the engine is extended.
    """
    return static_config.model_copy(
        update={
            "procedure_catalogue": [
                entry
                for entry in static_config.procedure_catalogue
                if entry.id in {"INV_RIGHTS_REVIEW", "CASH_BANK_CONFIRMATION"}
            ]
        }
    )


@pytest.fixture
def valuation_only(engagement):
    """Inventory whose single risk sits on an assertion the partial catalogue cannot serve."""
    item = engagement.line_item("inventory")
    item.assertions = [
        AssertionAssessment(
            id="assertion_1", line_item_id=item.id, assertion=Assertion.VALUATION,
            relevant=True, rationale="Aged stock.",
            risks=[_risk("risk_1", "assertion_1", "Carried above NRV.", RiskLevel.HIGH)],
        )
    ]
    return item


def test_empty_catalogue_subset_still_permits_an_ai_suggestion(
    engagement, valuation_only, static_config
):
    """SPEC 13 allows a suggestion precisely when the catalogue cannot respond.

    Returning early here would make that unreachable — the one case the feature exists for.
    """
    client = _client(
        _output(
            suggestions=[
                {
                    "description": "Obtain independent net realisable value evidence.",
                    "risk_ids": ["risk_1"],
                    "rationale": "No approved procedure covers inventory valuation.",
                }
            ]
        )
    )

    procedures = select_procedures(
        valuation_only, engagement, client=client, config=_partial_catalogue_config(static_config)
    )

    assert client.call_count() == 1  # the call was made, not skipped
    assert len(procedures) == 1
    assert procedures[0].source is ProcedureSource.AI_SUGGESTION
    assert procedures[0].requires_approval is True


def test_empty_catalogue_subset_still_rejects_catalogue_selections(
    engagement, valuation_only, static_config
):
    """Nothing was offered, so nothing catalogued can be selected."""
    client = _client(_output(_selection("INV_RIGHTS_REVIEW", ["risk_1"])))

    procedures = select_procedures(
        valuation_only, engagement, client=client, config=_partial_catalogue_config(static_config)
    )

    assert procedures == []


def test_empty_catalogue_subset_says_so_in_the_prompt(engagement, valuation_only):
    message = build_user_message(valuation_only, engagement, [])

    assert "No approved catalogue procedure covers" in message
    assert "only suggested new procedures are possible" in message


# --- the user message ----------------------------------------------------------------


def test_user_message_carries_every_risk_with_its_final_rating(engagement, inventory):
    from src.engine.catalogue import catalogue_for_assertions

    subset = catalogue_for_assertions(
        "inventory", [Assertion.EXISTENCE, Assertion.VALUATION]
    )
    message = build_user_message(inventory, engagement, subset)

    for risk_id in ("risk_1", "risk_2", "risk_3"):
        assert risk_id in message
    assert "Shrinkage." in message and "Carried above NRV." in message
    assert "existence" in message and "valuation" in message
    assert "rating: high" in message and "rating: medium" in message


def test_user_message_shows_final_rating_not_system_rating(engagement, inventory):
    """SPEC 13/17: an override must reach selection without re-analysing the area."""
    from src.engine.catalogue import catalogue_for_assertions

    overridden = inventory.all_risks[0]
    overridden.final_rating = RiskLevel.LOW
    overridden.is_overridden = True
    assert overridden.system_rating is RiskLevel.HIGH

    subset = catalogue_for_assertions("inventory", [Assertion.EXISTENCE, Assertion.VALUATION])
    message = build_user_message(inventory, engagement, subset)

    risk_line = next(line for line in message.splitlines() if line.startswith("- risk_1 "))
    assert "rating: low" in risk_line
    assert "system_rating" not in message


def test_user_message_labels_an_override_reason_as_auditor_judgement(engagement, inventory):
    from src.engine.catalogue import catalogue_for_assertions

    overridden = inventory.all_risks[0]
    overridden.final_rating = RiskLevel.LOW
    overridden.is_overridden = True
    overridden.override_reason = "The stock is contractually pre-sold."

    subset = catalogue_for_assertions("inventory", [Assertion.EXISTENCE, Assertion.VALUATION])
    message = build_user_message(inventory, engagement, subset)

    assert "Auditor judgement" in message
    assert "contractually pre-sold" in message


def test_user_message_offers_only_the_areas_catalogue(engagement, inventory):
    from src.engine.catalogue import catalogue_for_assertions

    subset = catalogue_for_assertions("inventory", [Assertion.EXISTENCE, Assertion.VALUATION])
    message = build_user_message(inventory, engagement, subset)

    assert "INV_PHYSICAL_COUNT" in message
    assert "CASH_BANK_CONFIRMATION" not in message
    assert "INV_RIGHTS_REVIEW" not in message  # its assertion was ruled out


def test_user_message_carries_context_and_facts(engagement, inventory):
    from src.engine.catalogue import catalogue_for_assertions

    subset = catalogue_for_assertions("inventory", [Assertion.VALUATION])
    message = build_user_message(inventory, engagement, subset)

    assert "fashion retailer" in message
    assert "fact_1" in message


# --- scope guards --------------------------------------------------------------------


def test_area_with_no_risks_makes_no_call(engagement):
    """Nothing to respond to; any gap is coverage's to report."""
    cash = engagement.line_item("cash")
    client = ScriptedLLMClient()

    assert select_procedures(cash, engagement, client=client) == []
    assert client.call_count() == 0


def test_non_audit_area_makes_no_call(engagement):
    client = ScriptedLLMClient()

    assert select_procedures(engagement.line_item("turnover"), engagement, client=client) == []
    assert client.call_count() == 0


def test_immaterial_audit_area_makes_no_call(static_config):
    small = make_engagement(
        ("turnover", 10_000_000, 9_000_000),
        ("profit_before_tax", 300_000, 250_000),
        ("cash", 10_000, 9_000),
    )
    small.materiality = calculate_materiality(small)  # 50,000
    scope_line_items(small, static_config)
    cash = small.line_item("cash")
    cash.assertions = [
        AssertionAssessment(
            id="assertion_1", line_item_id=cash.id, assertion=Assertion.EXISTENCE,
            relevant=True, rationale="Carried over.",
            risks=[_risk("risk_1", "assertion_1", "x", RiskLevel.HIGH)],
        )
    ]
    client = ScriptedLLMClient()

    assert select_procedures(cash, small, client=client, config=static_config) == []
    assert client.call_count() == 0


def test_unscoped_line_item_is_rejected(raiatea_engagement):
    client = ScriptedLLMClient()

    with pytest.raises(ProcedureSelectionError, match="must be scoped"):
        select_procedures(
            raiatea_engagement.line_item("inventory"), raiatea_engagement, client=client
        )
    assert client.call_count() == 0


# --- scoped re-selection (SPEC 17) ----------------------------------------------------


def test_a_scoped_call_offers_only_the_named_risks(engagement, inventory):
    """An override re-selects for the risk it changed, not for the whole area."""
    client = _client(_output(_selection("INV_SUBSEQUENT_SALES", ["risk_3"])))

    procedures = select_procedures(
        inventory, engagement, client=client, risk_ids={"risk_3"}
    )

    (call,) = client.calls
    assert "risk_3" in call.user
    assert "risk_1" not in call.user and "risk_2" not in call.user
    assert [p.risk_ids for p in procedures] == [["risk_3"]]


def test_a_scoped_call_narrows_the_catalogue_to_those_risks_assertions(
    engagement, inventory
):
    """Offering the whole area's catalogue would invite work for risks nobody asked about."""
    client = _client(_output(_selection("INV_SUBSEQUENT_SALES", ["risk_3"])))

    select_procedures(inventory, engagement, client=client, risk_ids={"risk_3"})

    (call,) = client.calls
    # risk_3 is a valuation risk; existence-only procedures are not on offer.
    assert "INV_SUBSEQUENT_SALES" in call.user
    assert "INV_PHYSICAL_COUNT" not in call.user


def test_a_scoped_call_still_rejects_risks_outside_the_scope(engagement, inventory):
    """The model may only answer what it was shown — otherwise a scoped call could
    silently rewrite links for risks the override never touched."""
    client = _client(_output(_selection("INV_SUBSEQUENT_SALES", ["risk_1", "risk_3"])))

    procedures = select_procedures(
        inventory, engagement, client=client, risk_ids={"risk_3"}
    )

    assert [p.risk_ids for p in procedures] == [["risk_3"]]


def test_scoping_to_an_unknown_risk_raises(engagement, inventory):
    with pytest.raises(ProcedureSelectionError, match="risk_99"):
        select_procedures(
            inventory, engagement, client=_client(_output()), risk_ids={"risk_99"}
        )


# --- live (opt-in) -------------------------------------------------------------------


@pytest.mark.llm
def test_live_selection_covers_every_risk_from_the_catalogue(engagement, inventory):
    """Run with `pytest -m llm`."""
    from src.llm.client import AnthropicLLMClient

    procedures = select_procedures(inventory, engagement, client=AnthropicLLMClient())
    inventory.procedures = procedures

    assert procedures
    assert inventory.dangling_risk_ids() == set()
    catalogue_ids = {"INV_PHYSICAL_COUNT", "INV_COST_TEST", "INV_AGED_STOCK_REVIEW",
                     "INV_SUBSEQUENT_SALES"}
    for procedure in procedures:
        if procedure.source is ProcedureSource.CATALOGUE:
            assert procedure.procedure_id in catalogue_ids
            assert procedure.approved is True
        else:
            assert procedure.requires_approval is True
        assert procedure.risk_ids
    # Every risk gets a response, which is what ISA 330.6 asks for.
    for risk in inventory.all_risks:
        assert inventory.procedures_for(risk.id), f"{risk.id} left without a procedure"
