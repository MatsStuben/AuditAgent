"""Reverse ISA coverage: which audit objects address each requirement (SPEC 15).

The mirror of `traceability`. That module walks forward from one procedure; this one walks
backward from each ISA requirement and reports what addresses it — and where expected work is
missing.

Two design points carry the feature:

Coverage is evaluated **only over audit areas** (SPEC 15, Coverage scope). A `GAP` means missing
work inside the scope the MVP claims to support. Raiatea has six material line items with no
implemented methodology; reporting those as ISA 315.29 gaps would bury the one real gap the
panel exists to surface. They are listed separately as an acknowledged scope boundary.

Each requirement is handled by dispatching on `ISARequirement.linked_object_type`, not by
branching per requirement ID. Adding a fourth requirement satisfied by an existing object type
is then a JSON edit (SPEC 4). The dispatch table must cover the whole enum — a missing entry
raises rather than silently reporting full coverage of a requirement nothing was checked
against.

Deterministic and read-only. No LLM.
"""

from collections.abc import Callable

from pydantic import BaseModel

from src.config.loader import StaticConfig, get_config
from src.models.engagement import AuditEngagement
from src.models.isa import ISARequirement, LinkedObjectType

#: Shown against material line items the MVP has no methodology for (SPEC 15).
NOT_IMPLEMENTED_LABEL = "material — audit logic not implemented in MVP"


class CoverageError(ValueError):
    """A requirement names an object type coverage cannot evaluate."""


class CoverageGap(BaseModel):
    """Expected work missing under a requirement, inside the scope the MVP supports."""

    requirement_id: str
    line_item_type: str
    object_id: str
    """The object that should have produced the linked work — the audit area with no
    assertions, the relevant assertion with no risks, the risk with no procedure."""
    description: str


class RequirementCoverage(BaseModel):
    requirement: ISARequirement
    addressed_by: list[str] = []
    """IDs of the runtime objects addressing this requirement, deduplicated. One procedure
    answering two risks addresses ISA 330.6/7 once."""
    gaps: list[CoverageGap] = []

    @property
    def satisfied(self) -> bool:
        return not self.gaps


class ScopeExclusion(BaseModel):
    """A material line item outside the MVP's implemented methodology (SPEC 15).

    Deliberately not a gap: it is a stated scope boundary, and conflating the two would make
    the gap signal useless on this engagement.
    """

    line_item_id: str
    line_item_type: str
    label: str = NOT_IMPLEMENTED_LABEL


class CoverageReport(BaseModel):
    requirements: list[RequirementCoverage] = []
    not_implemented: list[ScopeExclusion] = []

    @property
    def gaps(self) -> list[CoverageGap]:
        """Every gap across every requirement, in requirement order."""
        return [gap for coverage in self.requirements for gap in coverage.gaps]

    @property
    def satisfied(self) -> bool:
        return not self.gaps

    def for_requirement(self, requirement_id: str) -> RequirementCoverage | None:
        return next(
            (c for c in self.requirements if c.requirement.id == requirement_id), None
        )


def check_isa_coverage(
    engagement: AuditEngagement, config: StaticConfig | None = None
) -> CoverageReport:
    """Report what addresses each ISA requirement, and where work is missing (SPEC 15)."""
    config = config or get_config()

    requirements = []
    for requirement in config.isa_requirements:
        evaluate = _DISPATCH.get(requirement.linked_object_type)
        if evaluate is None:
            raise CoverageError(
                f"{requirement.id} is satisfied by {requirement.linked_object_type}, which "
                f"coverage cannot evaluate"
            )
        addressed_by, gaps = evaluate(engagement, requirement)
        requirements.append(
            RequirementCoverage(
                requirement=requirement, addressed_by=addressed_by, gaps=gaps
            )
        )

    return CoverageReport(
        requirements=requirements,
        not_implemented=[
            ScopeExclusion(line_item_id=item.id, line_item_type=item.line_item_type)
            for item in engagement.line_items
            if item.material is True and not item.is_audit_area
        ],
    )


Evaluation = tuple[list[str], list[CoverageGap]]


def _assertion_coverage(
    engagement: AuditEngagement, requirement: ISARequirement
) -> Evaluation:
    """ISA 315.29 — a material audit area should have assertion assessments."""
    addressed: list[str] = []
    gaps: list[CoverageGap] = []
    for area in engagement.in_scope_audit_areas:
        if not area.assertions:
            gaps.append(
                CoverageGap(
                    requirement_id=requirement.id,
                    line_item_type=area.line_item_type,
                    object_id=area.id,
                    description=(
                        f"{area.line_item_type} is a material audit area with no assertion "
                        f"assessments"
                    ),
                )
            )
        addressed.extend(assertion.id for assertion in area.assertions)
    return addressed, gaps


def _risk_coverage(engagement: AuditEngagement, requirement: ISARequirement) -> Evaluation:
    """ISA 315.28(b)/31 — a *relevant* assertion should have risk assessments.

    An assertion judged not relevant is expected to carry no risks (SPEC 10), so it is not a
    gap. Reporting one would penalise the engine for reaching a conclusion.
    """
    addressed: list[str] = []
    gaps: list[CoverageGap] = []
    for area in engagement.in_scope_audit_areas:
        for assertion in area.assertions:
            if not assertion.relevant:
                continue
            if not assertion.risks:
                gaps.append(
                    CoverageGap(
                        requirement_id=requirement.id,
                        line_item_type=area.line_item_type,
                        object_id=assertion.id,
                        description=(
                            f"{area.line_item_type}/{assertion.assertion.value} is relevant "
                            f"but has no risk assessment"
                        ),
                    )
                )
            addressed.extend(risk.id for risk in assertion.risks)
    return addressed, gaps


def _procedure_coverage(
    engagement: AuditEngagement, requirement: ISARequirement
) -> Evaluation:
    """ISA 330.6/7 — an assessed risk should have a responsive procedure.

    An unapproved AI suggestion does not close the gap. SPEC 13 says a suggestion "will not be
    used without" auditor approval, so treating one as a response would report the risk as
    answered by work that is not yet part of the plan. The suggestion still appears in
    `addressed_by`, so the UI can show that a proposed response exists and is waiting on a
    decision.
    """
    addressed: list[str] = []
    gaps: list[CoverageGap] = []
    for area in engagement.in_scope_audit_areas:
        for risk in area.all_risks:
            responses = area.procedures_for(risk.id)
            effective = [p for p in responses if not p.requires_approval]
            if not effective:
                gaps.append(
                    CoverageGap(
                        requirement_id=requirement.id,
                        line_item_type=area.line_item_type,
                        object_id=risk.id,
                        description=(
                            f"{risk.id} has a proposed response awaiting auditor approval"
                            if responses
                            else f"{risk.id} has no responsive procedure"
                        ),
                    )
                )
            addressed.extend(p.id for p in responses)
    return _deduplicated(addressed), gaps


def _deduplicated(ids: list[str]) -> list[str]:
    """Order-preserving. A procedure answering two risks addresses the requirement once."""
    seen: dict[str, None] = {}
    for object_id in ids:
        seen.setdefault(object_id)
    return list(seen)


_DISPATCH: dict[LinkedObjectType, Callable[[AuditEngagement, ISARequirement], Evaluation]] = {
    LinkedObjectType.ASSERTION_ASSESSMENT: _assertion_coverage,
    LinkedObjectType.RISK_ASSESSMENT: _risk_coverage,
    LinkedObjectType.PROCEDURE: _procedure_coverage,
}
