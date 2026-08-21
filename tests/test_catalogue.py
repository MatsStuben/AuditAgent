"""M7 verification: deterministic catalogue filtering (SPEC 12). Pure, no LLM."""

from src.config.loader import CatalogueProcedure
from src.engine.catalogue import catalogue_for_assertions, filter_catalogue
from src.models.audit_objects import Assertion


def _ids(procedures) -> list[str]:
    return [p.id for p in procedures]


# --- shipped catalogue ---------------------------------------------------------------


def test_inventory_valuation_returns_every_valuation_procedure():
    """Three, not two: INV_COST_TEST covers accuracy *and* valuation, so it qualifies.

    Order follows the catalogue file, which keeps the prompt stable between runs.
    """
    assert _ids(filter_catalogue("inventory", Assertion.VALUATION)) == [
        "INV_COST_TEST",
        "INV_AGED_STOCK_REVIEW",
        "INV_SUBSEQUENT_SALES",
    ]


def test_cash_has_no_valuation_procedures():
    """Valuation is not a cash candidate, so nothing can be selected for it."""
    assert filter_catalogue("cash", Assertion.VALUATION) == []


def test_filtering_respects_the_audit_area():
    """An inventory procedure must not surface for cash, or vice versa."""
    cash_existence = _ids(filter_catalogue("cash", Assertion.EXISTENCE))
    inventory_existence = _ids(filter_catalogue("inventory", Assertion.EXISTENCE))

    assert cash_existence == ["CASH_BANK_CONFIRMATION", "CASH_RECONCILIATION_REVIEW"]
    assert inventory_existence == ["INV_PHYSICAL_COUNT"]
    assert not set(cash_existence) & set(inventory_existence)


def test_unknown_area_or_assertion_returns_nothing():
    assert filter_catalogue("trade_debtors", Assertion.EXISTENCE) == []


def test_a_procedure_serving_two_assertions_appears_for_both():
    """INV_COST_TEST covers accuracy and valuation."""
    assert "INV_COST_TEST" in _ids(filter_catalogue("inventory", Assertion.ACCURACY))
    assert "INV_COST_TEST" in _ids(filter_catalogue("inventory", Assertion.VALUATION))


# --- the per-area subset -------------------------------------------------------------


def test_area_subset_is_the_union_over_assertions():
    subset = catalogue_for_assertions(
        "inventory", [Assertion.EXISTENCE, Assertion.VALUATION]
    )

    assert _ids(subset) == [
        "INV_PHYSICAL_COUNT",
        "INV_COST_TEST",
        "INV_AGED_STOCK_REVIEW",
        "INV_SUBSEQUENT_SALES",
    ]


def test_area_subset_lists_each_procedure_once():
    """INV_COST_TEST serves both assertions but must not appear twice."""
    subset = catalogue_for_assertions(
        "inventory", [Assertion.ACCURACY, Assertion.VALUATION]
    )

    assert _ids(subset).count("INV_COST_TEST") == 1


def test_area_subset_excludes_assertions_without_risks():
    """An assertion ruled out has no risks, so offering its procedures would invite a
    response to work the engagement has already excluded."""
    subset = catalogue_for_assertions("inventory", [Assertion.VALUATION])

    assert "INV_RIGHTS_REVIEW" not in _ids(subset)  # rights_and_obligations only
    assert "INV_PHYSICAL_COUNT" not in _ids(subset)  # existence/completeness only


def test_area_subset_is_empty_without_assertions():
    assert catalogue_for_assertions("inventory", []) == []


# --- data-driven, not hardcoded ------------------------------------------------------


def _synthetic() -> list[CatalogueProcedure]:
    return [
        CatalogueProcedure(
            id="NEW_AREA_EXISTENCE",
            name="Something new",
            audit_areas=["trade_debtors"],
            assertions=[Assertion.EXISTENCE],
            procedure_type="external_confirmation",
            evidence_strength="high",
            description="...",
        ),
        CatalogueProcedure(
            id="NEW_AREA_VALUATION",
            name="Something else",
            audit_areas=["trade_debtors"],
            assertions=[Assertion.VALUATION],
            procedure_type="test_of_details",
            evidence_strength="medium",
            description="...",
        ),
    ]


def test_a_synthetic_catalogue_filters_correctly_with_no_code_change():
    """Adding an audit area is a JSON edit; nothing in the filter names an area (SPEC 12)."""
    catalogue = _synthetic()

    assert _ids(filter_catalogue("trade_debtors", Assertion.EXISTENCE, catalogue=catalogue)) == [
        "NEW_AREA_EXISTENCE"
    ]
    assert filter_catalogue("inventory", Assertion.EXISTENCE, catalogue=catalogue) == []


def test_synthetic_area_subset_works_too():
    subset = catalogue_for_assertions(
        "trade_debtors",
        [Assertion.EXISTENCE, Assertion.VALUATION],
        catalogue=_synthetic(),
    )

    assert _ids(subset) == ["NEW_AREA_EXISTENCE", "NEW_AREA_VALUATION"]
