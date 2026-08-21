"""Deterministic filtering of the approved procedure catalogue (SPEC 12).

Filtering is data-driven: it reads `audit_areas` and `assertions` off the catalogue records
themselves, so adding a procedure or an audit area is a JSON-only change. Nothing here
branches on a specific area or assertion.

The LLM only ever sees a filtered subset, so it cannot select a procedure that is wrong for
the area it is reasoning about.
"""

from src.config.loader import CatalogueProcedure, get_config
from src.models.audit_objects import Assertion


def filter_catalogue(
    audit_area: str,
    assertion: Assertion,
    *,
    catalogue: list[CatalogueProcedure] | None = None,
) -> list[CatalogueProcedure]:
    """Catalogue procedures applicable to one assertion on one audit area. Pure."""
    catalogue = catalogue if catalogue is not None else get_config().procedure_catalogue
    return [
        procedure
        for procedure in catalogue
        if audit_area in procedure.audit_areas and assertion in procedure.assertions
    ]


def catalogue_for_assertions(
    audit_area: str,
    assertions: list[Assertion],
    *,
    catalogue: list[CatalogueProcedure] | None = None,
) -> list[CatalogueProcedure]:
    """The subset offered to the model for a whole audit area (SPEC 13).

    The union over the assertions that actually carry risks — not everything catalogued for
    the area. An assertion ruled out has no risks, so offering its procedures would invite a
    response to work the engagement has already excluded.

    Order follows the catalogue file, and each procedure appears once however many of the
    assertions it serves.
    """
    catalogue = catalogue if catalogue is not None else get_config().procedure_catalogue
    wanted = set(assertions)
    return [
        procedure
        for procedure in catalogue
        if audit_area in procedure.audit_areas and wanted & set(procedure.assertions)
    ]
