"""M1 verification: the five static config files load, validate and cross-reference."""

import json
import shutil

import pytest
from pydantic import ValidationError

from src.config.loader import (
    DATA_DIR,
    ConfigError,
    RiskMatrix,
    StaticConfig,
    get_config,
    load_config,
)
from src.models.audit_objects import (
    Assertion,
    AssertionAssessment,
    EvidenceStrength,
    Procedure,
    RiskAssessment,
    RiskLevel,
)
from src.models.isa import LinkedObjectType


@pytest.fixture(scope="module")
def config() -> StaticConfig:
    return load_config()


# --- shipped MVP content -------------------------------------------------------------


def test_all_five_config_files_load(config):
    assert config.engagement_input.company == "Raiatea Ltd"
    assert config.engagement_input.year_end == "2025-12-31"
    assert config.risk_matrix.label.startswith("Prototype")


def test_mvp_counts(config):
    assert len(config.engagement_input.line_items) == 8
    assert len(config.audit_area_profiles) == 2
    assert len(config.procedure_catalogue) == 7
    assert len(config.isa_requirements) == 3


def test_raiatea_figures_match_the_case(config):
    amounts = {li.type: (li.cy, li.py) for li in config.engagement_input.line_items}
    assert amounts["turnover"] == (52_400_000, 47_100_000)
    assert amounts["profit_before_tax"] == (5_240_000, 4_850_000)
    assert amounts["inventory"] == (8_900_000, 6_200_000)
    assert amounts["cash"] == (3_120_000, 2_890_000)
    assert amounts["property_plant_equipment"] == (4_600_000, 4_800_000)


def test_audit_areas_are_exactly_cash_and_inventory(config):
    assert set(config.audit_area_profiles) == {"cash", "inventory"}
    assert config.is_audit_area("cash")
    assert config.is_audit_area("inventory")
    # Material, but no implemented methodology — the SPEC 2.1 distinction.
    assert not config.is_audit_area("turnover")
    assert not config.is_audit_area("trade_debtors")


def test_inventory_has_valuation_but_cash_does_not(config):
    """The asymmetry that makes the inventory demo meaningful."""
    assert Assertion.VALUATION in config.candidate_assertions("inventory")
    assert Assertion.VALUATION not in config.candidate_assertions("cash")
    assert config.candidate_assertions("unknown_area") == []


def test_isa_requirements_link_to_runtime_object_types(config):
    linked = {r.id: r.linked_object_type for r in config.isa_requirements}
    assert linked == {
        "ISA315.29": LinkedObjectType.ASSERTION_ASSESSMENT,
        "ISA315.28b_31": LinkedObjectType.RISK_ASSESSMENT,
        "ISA330.6_7": LinkedObjectType.PROCEDURE,
    }


def test_linked_object_types_match_real_runtime_classes():
    """Reverse coverage dispatches on this value, so a drifted name would break it."""
    runtime_names = {AssertionAssessment.__name__, RiskAssessment.__name__, Procedure.__name__}
    assert {t.value for t in LinkedObjectType} == runtime_names


def test_every_config_assertion_is_a_known_enum_member(config):
    """Pydantic coerces to the enum, so reaching here at all proves membership."""
    for profile in config.audit_area_profiles.values():
        for assertion in profile.candidate_assertions:
            assert isinstance(assertion, Assertion)
    for proc in config.procedure_catalogue:
        for assertion in proc.assertions:
            assert isinstance(assertion, Assertion)
        assert isinstance(proc.evidence_strength, EvidenceStrength)


def test_get_config_is_cached():
    assert get_config() is get_config()


# --- risk matrix ---------------------------------------------------------------------


def test_risk_matrix_covers_all_nine_combinations(config):
    for likelihood in RiskLevel:
        for magnitude in RiskLevel:
            assert isinstance(config.risk_matrix.rating(likelihood, magnitude), RiskLevel)


def test_risk_matrix_shipped_values(config):
    rating = config.risk_matrix.rating
    assert rating(RiskLevel.LOW, RiskLevel.LOW) is RiskLevel.LOW
    assert rating(RiskLevel.HIGH, RiskLevel.HIGH) is RiskLevel.HIGH
    assert rating(RiskLevel.LOW, RiskLevel.HIGH) is RiskLevel.MEDIUM
    assert rating(RiskLevel.HIGH, RiskLevel.LOW) is RiskLevel.MEDIUM
    assert rating(RiskLevel.MEDIUM, RiskLevel.MEDIUM) is RiskLevel.MEDIUM


def test_incomplete_risk_matrix_is_rejected():
    with pytest.raises((ConfigError, ValidationError)):
        RiskMatrix(label="broken", matrix={"low": {"low": "low"}})


def test_risk_matrix_rejects_a_rating_outside_the_enum():
    with pytest.raises((ConfigError, ValidationError)):
        RiskMatrix(
            label="broken",
            matrix={
                lk: {mg: ("severe" if (lk, mg) == ("high", "high") else "low") for mg in
                     ("low", "medium", "high")}
                for lk in ("low", "medium", "high")
            },
        )


# --- config drift guards -------------------------------------------------------------
#
# These are the checks that fire when someone adds an audit area or procedure and gets the
# wiring wrong. Each mutates a copy of the real data directory, never the real one.


@pytest.fixture
def data_copy(tmp_path):
    dest = tmp_path / "data"
    shutil.copytree(DATA_DIR, dest)
    return dest


def _rewrite(path, mutate):
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload))


def test_copied_data_still_loads(data_copy):
    """Guards the fixture itself, so the failures below mean what they claim."""
    assert load_config(data_copy).engagement_input.company == "Raiatea Ltd"


def test_procedure_referencing_an_area_without_a_profile_is_rejected(data_copy):
    _rewrite(
        data_copy / "procedure_catalogue.json",
        lambda procs: procs[0]["audit_areas"].append("trade_debtors"),
    )
    with pytest.raises(ConfigError, match="without a profile"):
        load_config(data_copy)


def test_procedure_targeting_a_non_candidate_assertion_is_rejected(data_copy):
    """Cash has no valuation candidate, so a cash valuation procedure is dead config."""
    _rewrite(
        data_copy / "procedure_catalogue.json",
        lambda procs: procs[0]["assertions"].append("valuation"),
    )
    with pytest.raises(ConfigError, match="not candidates"):
        load_config(data_copy)


def test_assertions_are_validated_against_each_area_not_the_union(data_copy):
    """A multi-area procedure must be valid for *every* area it claims, not just one.

    Valuation is an inventory candidate but not a cash one. Checking the union would let
    this through with misleading area metadata.
    """

    def mutate(procs):
        inventory_valuation = next(p for p in procs if p["id"] == "INV_AGED_STOCK_REVIEW")
        inventory_valuation["audit_areas"].append("cash")

    _rewrite(data_copy / "procedure_catalogue.json", mutate)
    with pytest.raises(ConfigError, match="audit area 'cash'"):
        load_config(data_copy)


def test_audit_area_without_any_catalogue_procedure_is_rejected(data_copy):
    """A profile declares an area implemented; bounded methodology must exist for it."""

    def add_profile(profiles):
        profiles["trade_debtors"] = {"candidate_assertions": ["existence", "valuation"]}

    _rewrite(data_copy / "audit_area_profiles.json", add_profile)
    with pytest.raises(ConfigError, match="no catalogue procedures"):
        load_config(data_copy)


def test_audit_area_needs_only_one_procedure_not_full_assertion_coverage(data_copy):
    """Deliberately permissive: one procedure is enough to make an area implemented.

    Full per-assertion catalogue coverage is not required at this stage.
    """

    def add_profile(profiles):
        profiles["trade_debtors"] = {"candidate_assertions": ["existence", "valuation"]}

    def add_one_procedure(procs):
        procs.append(
            {
                "id": "DEBT_CONFIRMATION",
                "name": "Confirm debtor balances",
                "audit_areas": ["trade_debtors"],
                "assertions": ["existence"],
                "procedure_type": "external_confirmation",
                "evidence_strength": "high",
                "description": "Circularise a sample of trade debtors.",
            }
        )

    _rewrite(data_copy / "audit_area_profiles.json", add_profile)
    _rewrite(data_copy / "procedure_catalogue.json", add_one_procedure)

    config = load_config(data_copy)
    # "valuation" has no procedure, and that is accepted for now.
    assert config.is_audit_area("trade_debtors")
    assert Assertion.VALUATION in config.candidate_assertions("trade_debtors")


def test_unknown_linked_object_type_is_rejected(data_copy):
    _rewrite(
        data_copy / "isa_requirements.json",
        lambda reqs: reqs[0].update({"linked_object_type": "AssertionAssesment"}),  # typo
    )
    with pytest.raises((ConfigError, ValidationError)):
        load_config(data_copy)


def test_profile_for_a_missing_line_item_is_rejected(data_copy):
    _rewrite(
        data_copy / "audit_area_profiles.json",
        lambda profiles: profiles.update({"goodwill": {"candidate_assertions": ["valuation"]}}),
    )
    with pytest.raises(ConfigError, match="no matching line item"):
        load_config(data_copy)


def test_duplicate_procedure_ids_are_rejected(data_copy):
    _rewrite(
        data_copy / "procedure_catalogue.json",
        lambda procs: procs.append(dict(procs[0])),
    )
    with pytest.raises(ConfigError, match="duplicate procedure ids"):
        load_config(data_copy)


def test_duplicate_line_item_types_are_rejected(data_copy):
    _rewrite(
        data_copy / "raiatea.json",
        lambda payload: payload["line_items"].append(dict(payload["line_items"][0])),
    )
    with pytest.raises(ConfigError, match="duplicate line item types"):
        load_config(data_copy)


def test_unknown_assertion_in_a_profile_is_rejected(data_copy):
    _rewrite(
        data_copy / "audit_area_profiles.json",
        lambda profiles: profiles["cash"]["candidate_assertions"].append("cutoff"),
    )
    with pytest.raises((ConfigError, ValidationError)):
        load_config(data_copy)


def test_missing_file_reports_the_path(data_copy):
    (data_copy / "risk_matrix.json").unlink()
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(data_copy)


def test_malformed_json_reports_the_path(data_copy):
    (data_copy / "risk_matrix.json").write_text("{not json")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(data_copy)
