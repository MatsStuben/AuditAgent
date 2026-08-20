"""Loading and validation of the five static config files (SPEC 3.3).

These models describe *static methodology and input*, deliberately separate from the runtime
audit state in `src.models`.

Validation here is structural — schema conformance, enum membership and cross-file references.
It does not assert MVP-specific counts (8 line items, 2 audit areas, ...); those belong in
tests, so that adding an audit area stays a JSON-only change.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from src.models.audit_objects import Assertion, EvidenceStrength, RiskLevel
from src.models.isa import ISARequirement

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class ConfigError(ValueError):
    """Raised when static config is internally inconsistent."""


class LineItemInput(BaseModel):
    type: str
    cy: float
    py: float


class EngagementInput(BaseModel):
    """Contents of `raiatea.json`."""

    company: str
    year_end: str
    line_items: list[LineItemInput]


class AuditAreaProfile(BaseModel):
    """Candidate-assertion knowledge for one audit area (SPEC 9)."""

    candidate_assertions: list[Assertion]

    @model_validator(mode="after")
    def _non_empty(self) -> "AuditAreaProfile":
        if not self.candidate_assertions:
            raise ConfigError("profile has no candidate assertions")
        return self


class CatalogueProcedure(BaseModel):
    """One approved procedure in the catalogue (SPEC 12)."""

    id: str
    name: str
    audit_areas: list[str]
    assertions: list[Assertion]
    procedure_type: str
    evidence_strength: EvidenceStrength
    description: str


class RiskMatrix(BaseModel):
    """Deterministic likelihood x magnitude -> rating mapping (SPEC 11).

    Outer key is likelihood, inner key is magnitude.
    """

    label: str
    matrix: dict[RiskLevel, dict[RiskLevel, RiskLevel]]

    @model_validator(mode="after")
    def _complete(self) -> "RiskMatrix":
        missing = [
            f"{likelihood}/{magnitude}"
            for likelihood in RiskLevel
            for magnitude in RiskLevel
            if magnitude not in self.matrix.get(likelihood, {})
        ]
        if missing:
            raise ConfigError(f"risk matrix missing combinations: {missing}")
        return self

    def rating(self, likelihood: RiskLevel, magnitude: RiskLevel) -> RiskLevel:
        return self.matrix[likelihood][magnitude]


class StaticConfig(BaseModel):
    """All five static files, loaded and cross-validated together."""

    engagement_input: EngagementInput
    audit_area_profiles: dict[str, AuditAreaProfile]
    procedure_catalogue: list[CatalogueProcedure]
    risk_matrix: RiskMatrix
    isa_requirements: list[ISARequirement] = Field(default_factory=list)

    def is_audit_area(self, line_item_type: str) -> bool:
        """An implemented profile is what makes a line item an audit area (SPEC 2.1)."""
        return line_item_type in self.audit_area_profiles

    def candidate_assertions(self, audit_area: str) -> list[Assertion]:
        profile = self.audit_area_profiles.get(audit_area)
        return list(profile.candidate_assertions) if profile else []


def _duplicates(values: list[str]) -> list[str]:
    return sorted({v for v in values if values.count(v) > 1})


def validate_cross_references(config: StaticConfig) -> StaticConfig:
    """Check integrity *across* the config files.

    Deliberately not a Pydantic `model_validator`: `ConfigError` subclasses `ValueError`, so
    Pydantic would swallow it into a `ValidationError` and the readable message would be
    buried. Callers get one predictable error type for every integrity problem.
    """
    areas = set(config.audit_area_profiles)

    duplicate_types = _duplicates([li.type for li in config.engagement_input.line_items])
    if duplicate_types:
        raise ConfigError(f"duplicate line item types: {duplicate_types}")

    unknown_types = areas - {li.type for li in config.engagement_input.line_items}
    if unknown_types:
        raise ConfigError(
            f"audit area profiles with no matching line item: {sorted(unknown_types)}"
        )

    duplicate_procs = _duplicates([p.id for p in config.procedure_catalogue])
    if duplicate_procs:
        raise ConfigError(f"duplicate procedure ids: {duplicate_procs}")

    duplicate_isa = _duplicates([r.id for r in config.isa_requirements])
    if duplicate_isa:
        raise ConfigError(f"duplicate ISA requirement ids: {duplicate_isa}")

    for proc in config.procedure_catalogue:
        if not proc.audit_areas:
            raise ConfigError(f"procedure {proc.id} lists no audit areas")

        orphaned = set(proc.audit_areas) - areas
        if orphaned:
            raise ConfigError(
                f"procedure {proc.id} references audit areas without a profile: {sorted(orphaned)}"
            )

        # Checked per area rather than against the union of all of them: a procedure
        # claiming to apply to both cash and inventory must be valid for both, or its area
        # metadata is misleading and catalogue filtering will surprise later.
        for area in proc.audit_areas:
            candidates = set(config.audit_area_profiles[area].candidate_assertions)
            unreachable = set(proc.assertions) - candidates
            if unreachable:
                raise ConfigError(
                    f"procedure {proc.id} targets assertions that are not candidates for "
                    f"audit area '{area}': {sorted(unreachable)}"
                )

    # A profile is what declares an area implemented. Without at least one approved
    # procedure, selection for that area would fall back entirely to AI suggestions,
    # defeating the bounded-catalogue design (SPEC 12, 13).
    covered = {area for proc in config.procedure_catalogue for area in proc.audit_areas}
    uncovered = areas - covered
    if uncovered:
        raise ConfigError(
            f"audit areas with a profile but no catalogue procedures: {sorted(uncovered)}"
        )

    return config


def _read_json(directory: Path, filename: str):
    path = directory / filename
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"missing config file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc


def load_config(directory: Path | None = None) -> StaticConfig:
    """Load and validate all static config from `directory` (defaults to `src/data`).

    The directory argument keeps this testable against synthetic config without patching.
    """
    directory = directory or DATA_DIR
    config = StaticConfig(
        engagement_input=_read_json(directory, "raiatea.json"),
        audit_area_profiles=_read_json(directory, "audit_area_profiles.json"),
        procedure_catalogue=_read_json(directory, "procedure_catalogue.json"),
        risk_matrix=_read_json(directory, "risk_matrix.json"),
        isa_requirements=_read_json(directory, "isa_requirements.json"),
    )
    return validate_cross_references(config)


@lru_cache(maxsize=1)
def get_config() -> StaticConfig:
    """Cached accessor for the default config. Use `load_config` for custom directories."""
    return load_config()
