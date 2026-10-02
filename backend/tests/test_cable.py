"""Cable temperature-correction tests; reference: physics-reference.md §7.1."""
from __future__ import annotations

from esp_engine.cable import _voltage_drop_v, size_cable
from esp_engine.config import ElectricalThresholds, MotorThresholds
from esp_engine.motor import size_motor


def _motor(catalog):
    result = size_motor(catalog=catalog, bhp_pump=98, gas_handling_hp=0, protector_loss_hp=2, series=400, max_od_in=4.0, frequency_hz=60, casing_id_in=5.0, total_fluid_rate_bpd=5000, intake_temp_f=200, thresholds=MotorThresholds())
    assert result is not None
    return result


def test_voltage_drop_increases_with_length_current_and_temperature(catalog):
    cable = next(c for c in catalog.cables if c.awg == 4)
    cool = _voltage_drop_v(cable, length_ft=1000, current_a=80, conductor_temp_f=77)
    long = _voltage_drop_v(cable, length_ft=2000, current_a=80, conductor_temp_f=77)
    high_current = _voltage_drop_v(cable, length_ft=1000, current_a=100, conductor_temp_f=77)
    hot = _voltage_drop_v(cable, length_ft=1000, current_a=80, conductor_temp_f=257)
    assert long > cool and high_current > cool and hot > cool
    assert hot / cool > 1.3  # 0.214%/F coefficient creates material hot-well increase


def test_cable_selects_smallest_passing_awg_and_is_deterministic(catalog):
    motor = _motor(catalog)
    # §6D.2: size_cable now takes fluid_temp + cooling_velocity + water_cut
    # and computes the calculated conductor temperature internally. The old
    # max_temp_f (which was fluid temperature masquerading as conductor
    # temperature) is gone by design.
    kwargs = dict(
        catalog=catalog, motor=motor, setting_depth_md_ft=100, casing_id_in=5.0,
        pump_od_in=4.0, fluid_temp_f=200,
        cooling_velocity_ft_s=motor.cooling_velocity_ft_s,
        water_cut_frac=0.5,
        thresholds=ElectricalThresholds(), available_surface_voltage_v=700,
    )
    result = size_cable(**kwargs)
    assert result is not None
    assert result.passed
    assert result.model_dump_json() == size_cable(**kwargs).model_dump_json()

    # At 100 A no conductor in this fixture meets the 30 V/1000 ft vendor
    # recommended practice, so selection falls back to the lowest-drop conductor
    # and must say so. (The earlier expectation of #4 came from a 5% fractional
    # voltage-drop criterion, which is a power-distribution convention rather than
    # ESP practice.)
    assert result.cable.awg == 2
    assert any("recommended practice" in w for w in result.warnings)

    # When the guideline is satisfiable, the preference branch must pick the
    # physically smallest conductor that passes rather than the largest.
    relaxed = size_cable(
        **{
            **kwargs,
            "thresholds": ElectricalThresholds(max_voltage_drop_v_per_1000ft=60.0),
        }
    )
    assert relaxed is not None
    assert relaxed.cable.awg == 4  # #6 fails 100-A ampacity; #4 is smallest pass
    assert not any("recommended practice" in w for w in relaxed.warnings)


def test_calculated_cable_limits_warn_but_nonfit_rejects(catalog):
    motor = _motor(catalog)
    # Hot well: 350 F fluid + any self-heating rise pushes all catalog
    # insulation classes over their B.14.5 rating.
    hot = size_cable(
        catalog=catalog, motor=motor, setting_depth_md_ft=10000,
        casing_id_in=5.0, pump_od_in=4.0, fluid_temp_f=350,
        cooling_velocity_ft_s=motor.cooling_velocity_ft_s,
        water_cut_frac=0.5,
        thresholds=ElectricalThresholds(),
    )
    assert hot is not None and not hot.passed
    assert any(w["actual"]>w["limit"] for row in hot.decision_surface for w in row["warnings"])
    # Clearance screen: pump OD + cable OD >= casing ID.
    assert size_cable(
        catalog=catalog, motor=motor, setting_depth_md_ft=100,
        casing_id_in=4.4, pump_od_in=4.0, fluid_temp_f=200,
        cooling_velocity_ft_s=motor.cooling_velocity_ft_s,
        water_cut_frac=0.5,
        thresholds=ElectricalThresholds(),
    ) is None
