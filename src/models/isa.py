"""ISA requirement records (SPEC 4, 5).

Modelled so further requirements are added as data, not code.
"""

from enum import StrEnum

from pydantic import BaseModel


class LinkedObjectType(StrEnum):
    """Runtime object types an ISA requirement can be satisfied by.

    Bounded deliberately: reverse coverage (SPEC 15) dispatches on this field instead of
    branching per requirement, so an unrecognised value would silently produce no coverage
    rather than an error. Values match the runtime class names in `src.models`.
    """

    ASSERTION_ASSESSMENT = "AssertionAssessment"
    RISK_ASSESSMENT = "RiskAssessment"
    PROCEDURE = "Procedure"


class ISARequirement(BaseModel):
    id: str
    standard: str
    paragraphs: list[str]
    purpose: str
    linked_object_type: LinkedObjectType
    """Runtime object type this requirement is satisfied by, e.g. "RiskAssessment"."""
