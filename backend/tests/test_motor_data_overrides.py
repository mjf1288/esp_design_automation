"""Framework B.14.1: operator-supplied motor electrical data.

No public ESP motor datasheet in this catalog publishes power factor or
efficiency, so B.14.1's I_FL identity never engaged and every operating current
came from the declared nameplate-scaling fallback. Operators frequently DO hold
that data -- it arrives on the purchase datasheet for the specific unit -- so
this slice lets a case supply it per motor through the ``Tracked`` provenance
mechanism, rather than inventing midpoints in the catalog.

The tests here pin three separate things:

  1. An override actually reaches B.14.1 and is labelled as operator-sourced,
     because the provenance of a current propagates into cable gauge and
     transformer kVA and a reviewer must be able to see it.
  2. Every way of getting an override *wrong* is a refusal, not a warning. An
     override that silently matches nothing is the worst failure available here:
     the report would say vendor data was supplied while the number underneath
     it came from the fallback.
  3. Supplying PF and efficiency does not get to make the design look better.
     Back-solving PF x Eff from the cataloged HP/volts/amps triple yields
     0.46-0.59 across these motors, far below any plausible ESP figure, so
     B.14.1 with real PF data returns a current ~35% BELOW nameplate. Trusting
     it would shrink the cable on the strength of a contradiction.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from esp_engine.catalog import MotorModel, load_catalog
from esp_engine.config import MotorThresholds
from esp_engine.models import (
    Case,
    ElectricalConstraints,
    MotorElectricalOverride,
)
from esp_engine.motor import (
    _apply_override,
    _full_load_amps,
    _reconcile_with_nameplate,
    size_motor,
)
from esp_engine.pipeline import DesignEngine
from esp_engine.provenance import Tracked
from tests.test_pipeline import _base_case


def _catalog():
    return load_catalog()


def _unpublished_motor() -> MotorModel:
    """A cataloged motor with neither power factor nor efficiency published."""
    return next(
        m
        for m in _catalog().motors
        if m.power_factor is None and m.efficiency is None
    )


def _with_overrides(case: Case, *overrides: MotorElectricalOverride) -> Case:
    """Add overrides while preserving every other constraint on the case.

    Replacing the whole ``Constraints`` block instead would silently drop the
    base case's VSD availability and voltage limits, and the resulting design
    difference would look like it came from the override.
    """
    electrical = case.constraints.electrical.model_copy(
        update={"motor_data_overrides": overrides}
    )
    return case.model_copy(
        update={
            "constraints": case.constraints.model_copy(
                update={"electrical": electrical}
            )
        }
    )


def _stated(value: float, unit: str | None = None) -> Tracked:
    return Tracked.stated(value, unit, note="purchase datasheet, operator held")


# =============================================================================
# Malformed overrides are refusals
# =============================================================================


def test_power_factor_above_unity_is_refused() -> None:
    """A power factor above 1 is not a conservative input, it is a wrong one."""
    with pytest.raises(ValidationError, match="outside"):
        MotorElectricalOverride(motor_id="m", power_factor=_stated(1.4))


def test_efficiency_as_percentage_is_refused() -> None:
    """94 instead of 0.94 must not be read as a 9400% efficient motor.

    This is the single most likely operator data-entry error, and it moves
    B.14.1's current by two orders of magnitude in the unsafe direction.
    """
    with pytest.raises(ValidationError, match="fraction, not a percentage"):
        MotorElectricalOverride(motor_id="m", efficiency=_stated(94.0))


def test_empty_override_is_refused() -> None:
    """An override carrying no values is a mistake that would read as success."""
    with pytest.raises(ValidationError, match="supplies no values"):
        ElectricalConstraints(
            motor_data_overrides=(MotorElectricalOverride(motor_id="m"),)
        )


def test_duplicate_motor_ids_are_refused() -> None:
    """Two overrides for one motor have no defined precedence, so neither wins."""
    with pytest.raises(ValidationError):
        ElectricalConstraints(
            motor_data_overrides=(
                MotorElectricalOverride(motor_id="m", power_factor=_stated(0.9)),
                MotorElectricalOverride(motor_id="m", efficiency=_stated(0.9)),
            )
        )


def test_unknown_motor_id_is_refused_by_the_engine() -> None:
    """A mistyped id must stop the run, not quietly do nothing.

    The report would otherwise disclose operator-supplied data while the number
    beneath it came from nameplate scaling -- a wrong answer wearing the label
    of a right one.
    """
    case = _with_overrides(
        _base_case(),
        MotorElectricalOverride(
            motor_id="novomet-pmm-400-family-estimatee", power_factor=_stated(0.96)
        ),
    )
    with pytest.raises(ValueError, match="not in catalog"):
        DesignEngine().run(case)


def test_demag_temp_on_an_induction_motor_is_refused() -> None:
    """An induction motor has no magnets, so it has no demagnetization limit.

    Accepting this would make the B.16 magnet gate evaluate a machine with no
    magnets. Enforced by re-validating through ``MotorModel`` rather than
    ``model_copy``, so the catalog's own invariant does the work.
    """
    induction = next(m for m in _catalog().motors if not m.is_permanent_magnet)
    override = MotorElectricalOverride(
        motor_id=induction.id, demag_temp_f=_stated(300.0, "F")
    )
    with pytest.raises(ValidationError):
        _apply_override(induction, override)


# =============================================================================
# A complete override unlocks B.14.1; a partial one does not
# =============================================================================


def test_both_factors_unlock_b14_1() -> None:
    motor = _unpublished_motor()
    assert _full_load_amps(motor, volts=motor.volts)[1] == "nameplate_linear_scaling"
    effective, notes = _apply_override(
        motor,
        MotorElectricalOverride(
            motor_id=motor.id, power_factor=_stated(0.96), efficiency=_stated(0.908)
        ),
    )
    amps, basis = _full_load_amps(effective, volts=effective.volts)
    assert basis == "b14_1_from_power_factor_and_efficiency"
    assert amps is not None
    assert len(notes) == 2


def test_one_factor_alone_does_not_unlock_b14_1() -> None:
    """B.14.1 needs both factors. One is not half an answer, it is no answer."""
    motor = _unpublished_motor()
    override = MotorElectricalOverride(
        motor_id=motor.id, power_factor=_stated(0.96)
    )
    assert override.unlocks_b14_1() is False
    effective, _ = _apply_override(motor, override)
    assert _full_load_amps(effective, volts=effective.volts) == (
        None,
        "nameplate_linear_scaling",
    )


def test_override_of_a_published_value_is_disclosed_as_a_replacement() -> None:
    """Two claims about one machine; the report must not hide which was used.

    No motor in the shipped catalog publishes a power factor, so the published
    record is synthesized here. The path still has to work, because the moment a
    vendor publishes one this becomes the common case, and silently preferring
    either source over the other is a decision the engine must not make for the
    operator.
    """
    base = _unpublished_motor()
    published = base.model_copy(update={"power_factor": 0.98, "efficiency": 0.94})
    _, notes = _apply_override(
        published,
        MotorElectricalOverride(motor_id=published.id, power_factor=_stated(0.91)),
    )
    assert any("REPLACES" in n for n in notes)
    assert any("0.98" in n for n in notes), "the displaced value must be shown"


# =============================================================================
# B.14.1 is frequency-invariant under constant V/Hz
# =============================================================================


@pytest.mark.parametrize("frequency_hz", [45.0, 50.0, 60.0, 65.0, 70.0])
def test_full_load_amps_does_not_drift_with_frequency(frequency_hz: float) -> None:
    """Regression: mixing nameplate HP with scaled volts made I_FL vary as 1/r.

    Under constant V/Hz both horsepower and voltage scale with frequency, so
    full-load current cancels to a constant. The earlier form understated it by
    7.7% at 65 Hz -- and 65 Hz is where the winning candidate on the base case
    actually runs, so the error landed on the delivered design, in the direction
    that undersizes cable.
    """
    motor = _unpublished_motor()
    effective, _ = _apply_override(
        motor,
        MotorElectricalOverride(
            motor_id=motor.id, power_factor=_stated(0.96), efficiency=_stated(0.908)
        ),
    )
    at_nameplate, _ = _full_load_amps(effective, volts=effective.volts)
    scaled, _ = _full_load_amps(
        effective, volts=effective.volts * frequency_hz / 60.0
    )
    assert at_nameplate is not None and scaled is not None
    assert scaled == pytest.approx(at_nameplate, rel=1e-12)


def test_full_load_amps_matches_the_b14_1_identity() -> None:
    motor = _unpublished_motor()
    effective, _ = _apply_override(
        motor,
        MotorElectricalOverride(
            motor_id=motor.id, power_factor=_stated(0.96), efficiency=_stated(0.908)
        ),
    )
    amps, _ = _full_load_amps(effective, volts=effective.volts)
    expected = (effective.hp * 746.0) / (
        math.sqrt(3) * effective.volts * 0.96 * 0.908
    )
    assert amps == pytest.approx(expected, rel=1e-12)


# =============================================================================
# The nameplate reconciliation
# =============================================================================


def test_agreement_within_tolerance_keeps_the_b14_1_value() -> None:
    amps, gap, disclosure = _reconcile_with_nameplate(
        101.0, 100.0, tolerance_frac=0.10
    )
    assert amps == 101.0
    assert gap == pytest.approx(0.01)
    assert disclosure is None


def test_b14_1_far_below_nameplate_carries_the_nameplate_value() -> None:
    """The unsafe direction. Choosing the lower of two contradictory currents to
    size a conductor is not a defensible engineering decision."""
    amps, gap, disclosure = _reconcile_with_nameplate(
        37.1, 57.0, tolerance_frac=0.10
    )
    assert amps == 57.0
    assert gap < -0.10
    assert disclosure is not None
    assert "disagree" in disclosure


def test_b14_1_far_above_nameplate_carries_the_b14_1_value() -> None:
    amps, _, disclosure = _reconcile_with_nameplate(
        90.0, 57.0, tolerance_frac=0.10
    )
    assert amps == 90.0
    assert disclosure is not None


def test_every_cataloged_motor_contradicts_its_own_nameplate() -> None:
    """Documents the catalog defect that makes the reconciliation necessary.

    Back-solving PF x Eff from the published HP / volts / amps triple should land
    near 0.80 x 0.87 = 0.70 for an induction motor and 0.98 x 0.94 = 0.92 for a
    PMM (B.16). Measured, it lands between 0.393 and 0.784 -- below the plausible
    floor for its own type on all fourteen motors. So at least one of the three
    published quantities is not a full-load value; the most likely explanation is
    that volts and amps come from different winding taps, which ESP motors are
    routinely specified with.

    The spread matters as much as the offset. A single systematic factor could be
    corrected. A 2x spread cannot, which is why the reconciliation carries the
    conservative current rather than trying to repair the catalog.

    Asserted rather than merely noted, so that correcting the catalog fails this
    test and forces the reconciliation to be revisited instead of silently
    becoming dead code.
    """
    # Lower plausible bound on PF x Eff by motor type, from B.16.
    floors = {"induction": 0.80 * 0.85, "permanent_magnet": 0.98 * 0.92}
    implied_values = []
    for motor in _catalog().motors:
        implied = (motor.hp * 746.0) / (math.sqrt(3) * motor.volts * motor.amps)
        implied_values.append(implied)
        floor = floors[motor.motor_type]
        assert implied < floor, (
            f"{motor.id} ({motor.motor_type}) implies PF x Eff = {implied:.3f}, "
            f"at or above the B.16 floor {floor:.3f}. The nameplate triple is no "
            "longer self-contradictory for this motor -- revisit whether the "
            "reconciliation should still override B.14.1 here."
        )
    assert max(implied_values) / min(implied_values) > 1.5, (
        "the implied values are no longer widely spread, so a single systematic "
        "correction may now be defensible"
    )


# =============================================================================
# End to end through the engine
# =============================================================================


def test_override_changes_the_basis_but_not_the_current() -> None:
    """The honest outcome on today's catalog.

    Supplying real PF and efficiency relabels the current as B.14.1-derived and
    discloses the contradiction, but does not move the design point -- because
    the conservative current is still the nameplate one. An earlier build of
    this slice reported the voltage drop falling from 20.5% to 12.3% on exactly
    this case. That improvement was entirely an artifact of trusting a current
    that contradicted the nameplate.
    """
    # The default catalog now includes a series-400 induction motor
    # (seeded to exercise the \u00a76 phasor form on the demo route). That
    # motor beats the PMM at the top rank, so the test no longer
    # exercises the Novomet PMM unless the induction alternative is
    # explicitly filtered out. This test's premise is specifically
    # about the PMM contradiction case, so restrict to PMM-only.
    catalog = _catalog()
    pmm_only = catalog.model_copy(
        update={"motors": [m for m in catalog.motors if m.is_permanent_magnet]}
    )
    engine = DesignEngine(catalog=pmm_only)
    base = engine.run(_base_case())
    motor_id = "novomet-pmm-400-family-estimate"
    overridden = engine.run(
        _with_overrides(
            _base_case(),
            MotorElectricalOverride(
                motor_id=motor_id,
                power_factor=_stated(0.96),
                efficiency=_stated(0.908),
            ),
        )
    )
    b, c = base.candidates[0], overridden.candidates[0]
    assert b.configuration.config_id == c.configuration.config_id
    assert b.amps_basis == "nameplate_linear_scaling"
    assert c.amps_basis == "b14_1_from_operator_supplied_data"
    assert c.uses_operator_supplied_motor_data is True
    assert overridden.uses_operator_supplied_motor_data is True
    assert base.uses_operator_supplied_motor_data is False
    assert c.design_point.cable_voltage_drop_frac == pytest.approx(
        b.design_point.cable_voltage_drop_frac, rel=1e-12
    )
    assert any("disagree" in w for w in c.warnings)


def test_demag_override_makes_the_magnet_gate_falsifiable_but_never_passing() -> None:
    """B.16: intake temperature is a lower bound on magnet temperature.

    Supplying the vendor limit moves the gate off ``limit_not_published``, which
    is progress -- the gate can now be falsified. It must still never read
    ``pass``, because a lower bound cannot clear an upper limit.
    """
    catalog = _catalog()
    pmm = next(m for m in catalog.motors if m.is_permanent_magnet)
    assert pmm.demag_temp_f is None

    def gate_at(limit_f: float, intake_f: float) -> str:
        effective, _ = _apply_override(
            pmm,
            MotorElectricalOverride(
                motor_id=pmm.id, demag_temp_f=_stated(limit_f, "F")
            ),
        )
        sizing = size_motor(
            catalog=catalog.model_copy(
                update={
                    "motors": tuple(
                        effective if m.id == pmm.id else m for m in catalog.motors
                    )
                }
            ),
            bhp_pump=20.0,
            gas_handling_hp=0.0,
            protector_loss_hp=1.0,
            series=None,
            max_od_in=effective.od_in + 1.0,
            frequency_hz=60.0,
            casing_id_in=effective.od_in + 2.0,
            total_fluid_rate_bpd=3000.0,
            intake_temp_f=intake_f,
            thresholds=MotorThresholds(),
        )
        assert sizing is not None
        return sizing.magnet_gate

    assert gate_at(320.0, 185.0) == "indeterminate_needs_vendor_thermal_model"
    assert gate_at(150.0, 185.0) == "exceeded_at_intake_temperature"
    assert "pass" not in {gate_at(320.0, 185.0), gate_at(150.0, 185.0)}
