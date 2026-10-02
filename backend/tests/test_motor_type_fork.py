"""Framework B.16: the induction / permanent-magnet motor fork.

Twelve of the fourteen cataloged motors are permanent magnet machines. Before
this fork the engine sized all of them as induction motors, which is wrong in
two ways that matter:

  1. A PMM cannot start across the line. If the case says no VSD is available,
     a PMM design is not runnable at all -- yet the engine happily ranked such
     designs as feasible.
  2. A PMM's failure mode is irreversible magnet demagnetization, not the
     gradual insulation degradation that ``max_winding_temp_f`` models. The
     limit is not published for any cataloged PMM, so the honest outcome is a
     disclosed gap, not a silent pass.

These tests pin the behavior that made those two things visible.
"""

from __future__ import annotations

import pytest

from esp_engine.catalog import MotorModel, load_catalog
from esp_engine.config import EngineConfig
from esp_engine.models import Constraints, ElectricalConstraints
from esp_engine.motor import _full_load_amps, _magnet_gate, size_motor
from esp_engine.pipeline import DesignEngine
from esp_engine.provenance import Tracked
from esp_engine.results import FeasibilityVerdict
from tests.test_pipeline import _base_case


def _catalog():
    return load_catalog()


def _pmm_only_catalog():
    """Catalog with induction motors filtered out.

    Used by the tests below that assert the picker lands on a PMM: those
    invariants (VSD hard gate, magnet gate disclosure, mandatory-VSD
    warning) apply only when a PMM is what actually gets selected. Once
    the catalog carries a competitive induction motor in the same OD
    class -- see the demo PF+Eff seed case -- the picker preempts the
    PMM and the invariants can only be tested by explicitly filtering
    the alternative out. This preserves the tests as PMM-fork
    invariants rather than accidental catalog-composition assertions.
    """
    cat = load_catalog()
    return cat.model_copy(
        update={"motors": [m for m in cat.motors if m.is_permanent_magnet]}
    )


def _pmm() -> MotorModel:
    return next(m for m in _catalog().motors if m.is_permanent_magnet)


def _induction() -> MotorModel:
    return next(m for m in _catalog().motors if not m.is_permanent_magnet)


# =============================================================================
# The catalog declares the fork
# =============================================================================


def test_every_cataloged_motor_declares_its_type() -> None:
    """``motor_type`` is required with no default, so a new record cannot be
    added without someone deciding which machine it is."""
    for motor in _catalog().motors:
        assert motor.motor_type in ("induction", "permanent_magnet"), motor.id


def test_no_cataloged_motor_invents_a_power_factor_or_efficiency() -> None:
    """B.16 gives typical ranges (induction PF 0.80-0.88, PMM near unity). Using
    a range midpoint would silently change I_FL on every design and kVA on every
    transformer. None must mean "not published", never "not applicable"."""
    for motor in _catalog().motors:
        assert motor.power_factor is None or 0.0 < motor.power_factor <= 1.0
        assert motor.efficiency is None or 0.0 < motor.efficiency < 1.0


def test_induction_motor_cannot_carry_a_demagnetization_limit() -> None:
    """A demag temperature on an induction motor is a data-entry error, not a
    conservative extra field: it would make the magnet gate evaluate a machine
    that has no magnets."""
    ind = _induction()
    with pytest.raises(ValueError, match="demag"):
        ind.model_copy(update={"demag_temp_f": 300.0}).__class__.model_validate(
            {**ind.model_dump(), "demag_temp_f": 300.0}
        )


# =============================================================================
# The magnet gate can be falsified but never cleared
# =============================================================================


def test_magnet_gate_never_reports_a_pass() -> None:
    """The only magnet-temperature figure available is intake temperature, which
    is a LOWER bound on true magnet temperature (the magnet sits inside a rotor
    that is itself generating loss). A lower bound can prove the limit is
    exceeded; it can never prove the limit is respected. So there is deliberately
    no "pass" outcome -- a green magnet gate would be a fabricated release."""
    pmm_with_limit = _pmm().model_copy(update={"demag_temp_f": 300.0})
    for intake in (60.0, 200.0, 299.0, 299.99):
        gate = _magnet_gate(pmm_with_limit, estimated_magnet_temp_floor_f=intake)
        assert gate == "indeterminate_needs_vendor_thermal_model", intake
    for intake in (300.01, 400.0, 700.0):
        gate = _magnet_gate(pmm_with_limit, estimated_magnet_temp_floor_f=intake)
        assert gate == "exceeded_at_intake_temperature", intake


def test_magnet_gate_is_not_applicable_to_induction_motors() -> None:
    assert (
        _magnet_gate(_induction(), estimated_magnet_temp_floor_f=900.0)
        == "not_applicable"
    )


def test_magnet_gate_discloses_the_missing_limit_rather_than_assuming_one() -> None:
    """No vendor publishes a demagnetization temperature for any ESP PMM in this
    catalog. A generic N33SH magnet-grade limit exists in the literature but is
    not a property of these motors, so it must not be substituted."""
    pmm = _pmm()
    assert pmm.demag_temp_f is None
    assert _magnet_gate(pmm, estimated_magnet_temp_floor_f=180.0) == "limit_not_published"


# =============================================================================
# B.14.1 full-load current degrades to a labelled fallback
# =============================================================================


def test_full_load_amps_returns_none_without_power_factor_or_efficiency() -> None:
    """B.14.1 needs both PF and efficiency. Missing either must return None so
    the caller falls back explicitly and labels the basis, rather than
    defaulting a factor and reporting a computed-looking current."""
    m = _pmm()
    for update in (
        {"power_factor": 0.9, "efficiency": None},
        {"power_factor": None, "efficiency": 0.94},
        {"power_factor": None, "efficiency": None},
    ):
        amps, basis = _full_load_amps(m.model_copy(update=update), volts=m.volts)
        assert amps is None, update
        assert basis == "nameplate_linear_scaling", update

    both = m.model_copy(update={"power_factor": 0.98, "efficiency": 0.94})
    amps, basis = _full_load_amps(both, volts=both.volts)
    assert amps is not None and amps > 0.0
    assert basis == "b14_1_from_power_factor_and_efficiency"

    # B.14.1 is an identity, not a fit: check it against a hand calculation.
    expected = (both.hp * 746.0) / (3.0**0.5 * both.volts * 0.98 * 0.94)
    assert amps == pytest.approx(expected, rel=1e-12)

    # A zero or nonsensical voltage must not produce an infinite current.
    assert _full_load_amps(both, volts=0.0)[0] is None


def test_amps_basis_is_always_labelled() -> None:
    """Whichever path produced the current, the result says which one, so a
    reviewer never has to guess whether an ampere figure is derived or scaled."""
    cfg = EngineConfig()
    sized = size_motor(
        catalog=_catalog(),
        bhp_pump=40.0,
        gas_handling_hp=0.0,
        protector_loss_hp=2.0,
        series=None,
        max_od_in=6.0,
        frequency_hz=60.0,
        casing_id_in=6.276,
        total_fluid_rate_bpd=3000.0,
        intake_temp_f=180.0,
        thresholds=cfg.thresholds.motor,
    )
    assert sized is not None
    assert sized.amps_basis in (
        "b14_1_from_power_factor_and_efficiency",
        "nameplate_linear_scaling",
    )
    if sized.amps_basis == "nameplate_linear_scaling":
        assert sized.full_load_amps_b14_1 is None
        assert any("linearly scaled from nameplate" in w for w in sized.warnings)


# =============================================================================
# VSD necessity is a property of the machine
# =============================================================================


def test_every_permanent_magnet_motor_requires_a_vsd() -> None:
    pmms = [m for m in _catalog().motors if m.is_permanent_magnet]
    assert pmms, "fixture regression: catalog should contain permanent magnet motors"
    assert all(m.vsd_required for m in pmms)
    assert not any(m.vsd_required for m in _catalog().motors if not m.is_permanent_magnet)


def test_pmm_selection_is_a_hard_violation_when_no_vsd_is_available() -> None:
    """The regression this fork exists to catch.

    With no VSD on site the engine used to return FEASIBLE with three ranked
    candidates -- the highest-scoring candidates in the whole demo set -- every
    one of them specifying a permanent magnet motor that cannot be started
    without a drive. The design was unbuildable and nothing said so.
    """
    engine = DesignEngine(catalog=_pmm_only_catalog())
    case = _base_case(
        constraints=Constraints(
            electrical=ElectricalConstraints(
                vsd_available=Tracked.stated(False, None, note="no VSD on site"),
                fixed_frequency_hz=Tracked.stated(60.0, "Hz", note="fixed drive"),
            )
        )
    )
    result = engine.run(case)

    assert result.verdict is FeasibilityVerdict.NO_VIABLE_CONFIGURATION
    assert not result.candidates

    cited = [
        v
        for r in result.rejected
        for v in r.violations
        if "permanent magnet" in v.message and "no VSD is available" in v.message
    ]
    assert cited, "the rejection must name the reason, not just drop the config"
    assert all(v.rigidity == "hard" for v in cited)
    assert all(v.constraint_type == "electrical" for v in cited)
    assert all(v.remedy_hint for v in cited)


def test_vsd_case_reports_the_requirement_without_blocking() -> None:
    """When a VSD IS available a PMM is fine, but the report must still record
    that the drive is mandatory hardware rather than an optimization the
    operator could later remove."""
    result = DesignEngine(catalog=_pmm_only_catalog()).run(_base_case())
    assert result.candidates
    best = result.candidates[0]
    assert best.motor_type == "permanent_magnet"
    assert best.requires_vsd is True
    assert any("rotor-position control is mandatory" in w for w in best.warnings)


# =============================================================================
# The data gap is disclosed, not scored
# =============================================================================


def test_missing_demag_limit_is_disclosed_but_does_not_move_the_score() -> None:
    """Penalizing the missing limit would bias selection toward the two
    induction motors purely because more is published about them, which is
    selection on data availability rather than on engineering merit. The gap
    belongs in the disclosure channel, alongside
    ``depends_on_estimated_catalog_data``."""
    result = DesignEngine(catalog=_pmm_only_catalog()).run(_base_case())
    best = result.candidates[0]

    assert best.magnet_thermal_limit_unverified is True
    assert result.requires_magnet_thermal_verification is True

    # The flag must not appear as a scored soft violation.
    assert not any(
        "demagnetization" in v.message.lower() for v in best.soft_violations
    )
    assert any("demagnetization limit is not published" in w for w in best.warnings)


def test_unresolved_magnet_gate_produces_a_data_request() -> None:
    """A disclosed gap that generates no ask is a dead end. It must turn into a
    specific request the application engineer can send to the vendor."""
    result = DesignEngine(catalog=_pmm_only_catalog()).run(_base_case())
    magnet_requests = [
        r for r in result.recommended_data_requests if "demagnetization" in r.lower()
    ]
    assert magnet_requests
    assert "irreversible" in magnet_requests[0].lower()


def test_loading_band_discloses_its_induction_provenance() -> None:
    """The 75-85% band is calibrated on induction-motor practice. B.16 notes a
    PMM holds efficiency and near-unity PF at partial load, so applying the
    induction band to a PMM overstates an underloading concern. No cataloged
    PMM publishes a partial-load curve to recalibrate from, so the band is
    applied and its provenance is stated."""
    cfg = EngineConfig()
    assert cfg.thresholds.motor.pmm_target_loading_min is None, (
        "once vendor partial-load data exists, set this and update this test"
    )
    sized = size_motor(
        catalog=_catalog(),
        bhp_pump=40.0,
        gas_handling_hp=0.0,
        protector_loss_hp=2.0,
        series=None,
        max_od_in=6.0,
        frequency_hz=60.0,
        casing_id_in=6.276,
        total_fluid_rate_bpd=3000.0,
        intake_temp_f=180.0,
        thresholds=cfg.thresholds.motor,
    )
    assert sized is not None
    if sized.motor_type == "permanent_magnet":
        assert sized.loading_band_is_induction_derived is True
