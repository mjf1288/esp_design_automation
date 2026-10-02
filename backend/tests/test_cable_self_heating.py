"""Framework §6D.2 cable calculated-temperature model.

Tests target the pure function ``estimate_cable_self_heating`` so any
regression in the two-term shape, the reference anchor, or the physical
scaling laws (current-squared, Dittus-Boelter velocity, resistance
amplification, water-cut blend, fallback disclosures) fails a specific
assertion rather than a downstream integration test.
"""

from __future__ import annotations

import pytest

from esp_engine.cable_thermal import (
    COPPER_TEMP_COEFF_PER_F,
    MIN_MODELED_VELOCITY_FT_S,
    REFERENCE_FLUID_TEMP_F,
    REFERENCE_OIL_RISE_F,
    REFERENCE_VELOCITY_FT_S,
    REFERENCE_WATER_RISE_F,
    VELOCITY_EXPONENT,
    CableSelfHeating,
    estimate_cable_self_heating,
)


# ---------------------------------------------------------------------------
# Anchor: framework-derived reference values must be reproduced at reference
# conditions (ampacity, 1 ft/sec, 100% water). Any drift in these constants
# is either a deliberate re-anchor (in which case update this test with a
# citation) or a bug.
# ---------------------------------------------------------------------------


def test_anchor_pure_water_at_reference_conditions() -> None:
    sh = estimate_cable_self_heating(
        fluid_temp_f=77.0,
        cooling_velocity_ft_s=REFERENCE_VELOCITY_FT_S,
        current_a=100.0,
        ampacity_a=100.0,
        water_cut_frac=1.0,
    )
    # At reference conditions with T_fluid = 77 F the resistance factor is
    # 1.0 (the anchor defines it) and the rise reproduces the anchor to
    # within fixed-point tolerance.
    assert sh.rise_f == pytest.approx(REFERENCE_WATER_RISE_F, abs=1e-3)
    assert sh.conductor_temp_f == pytest.approx(
        77.0 + REFERENCE_WATER_RISE_F, abs=1e-3
    )
    assert sh.current_factor == pytest.approx(1.0)
    assert sh.velocity_factor == pytest.approx(1.0)
    assert sh.resistance_factor == pytest.approx(1.0, abs=1e-4)
    assert sh.reference_rise_f == pytest.approx(REFERENCE_WATER_RISE_F)


def test_anchor_pure_oil_at_reference_conditions() -> None:
    sh = estimate_cable_self_heating(
        fluid_temp_f=77.0,
        cooling_velocity_ft_s=REFERENCE_VELOCITY_FT_S,
        current_a=100.0,
        ampacity_a=100.0,
        water_cut_frac=0.0,
    )
    assert sh.rise_f == pytest.approx(REFERENCE_OIL_RISE_F, abs=1e-3)
    assert sh.reference_rise_f == pytest.approx(REFERENCE_OIL_RISE_F)


# ---------------------------------------------------------------------------
# Physical scaling laws. Each factor is perturbed in isolation and the
# resulting rise must move in the correct direction with the correct
# magnitude. This is the mutation guard: a future edit that swaps a plus
# for a minus, drops a term, or replaces one exponent with another will
# fail here rather than in a downstream regression.
# ---------------------------------------------------------------------------


def _baseline() -> CableSelfHeating:
    return estimate_cable_self_heating(
        fluid_temp_f=200.0,
        cooling_velocity_ft_s=1.5,
        current_a=80.0,
        ampacity_a=100.0,
        water_cut_frac=0.5,
    )


def test_current_squared_scaling() -> None:
    a = _baseline()
    b = estimate_cable_self_heating(
        fluid_temp_f=200.0,
        cooling_velocity_ft_s=1.5,
        current_a=40.0,  # half current
        ampacity_a=100.0,
        water_cut_frac=0.5,
    )
    # I²R means half the current → one quarter of the rise (holding R fixed
    # approximately; R changes weakly with rise which itself dropped, so
    # accept a small tolerance).
    assert b.rise_f == pytest.approx(a.rise_f * 0.25, rel=0.10)
    assert b.current_factor == pytest.approx(0.16, rel=1e-9)


def test_velocity_dittus_boelter_scaling() -> None:
    a = _baseline()
    b = estimate_cable_self_heating(
        fluid_temp_f=200.0,
        cooling_velocity_ft_s=3.0,  # 2x velocity
        current_a=80.0,
        ampacity_a=100.0,
        water_cut_frac=0.5,
    )
    # h ∝ v^0.8 so ΔT ∝ v^-0.8. Doubling velocity should scale rise by
    # 2^-0.8 ≈ 0.574, again modulo the weak resistance feedback.
    ratio = b.rise_f / a.rise_f
    expected = (1.5 / 3.0) ** VELOCITY_EXPONENT
    assert ratio == pytest.approx(expected, rel=0.05)


def test_velocity_floor_prevents_divergence() -> None:
    # Velocity below the modeled minimum must clip, not extrapolate. The
    # separate cooling-velocity gate is responsible for rejecting the
    # configuration; this floor keeps the calculation finite during
    # enumeration.
    sh = estimate_cable_self_heating(
        fluid_temp_f=200.0,
        cooling_velocity_ft_s=0.01,
        current_a=80.0,
        ampacity_a=100.0,
        water_cut_frac=0.5,
    )
    expected_v_factor = (
        REFERENCE_VELOCITY_FT_S / MIN_MODELED_VELOCITY_FT_S
    ) ** VELOCITY_EXPONENT
    assert sh.velocity_factor == pytest.approx(expected_v_factor)


def test_water_cut_blend_is_linear() -> None:
    dry = estimate_cable_self_heating(
        fluid_temp_f=200.0, cooling_velocity_ft_s=1.5,
        current_a=80.0, ampacity_a=100.0, water_cut_frac=0.0,
    )
    wet = estimate_cable_self_heating(
        fluid_temp_f=200.0, cooling_velocity_ft_s=1.5,
        current_a=80.0, ampacity_a=100.0, water_cut_frac=1.0,
    )
    mid = estimate_cable_self_heating(
        fluid_temp_f=200.0, cooling_velocity_ft_s=1.5,
        current_a=80.0, ampacity_a=100.0, water_cut_frac=0.5,
    )
    assert mid.reference_rise_f == pytest.approx(
        0.5 * (dry.reference_rise_f + wet.reference_rise_f)
    )
    assert dry.rise_f > wet.rise_f  # oil is a worse coolant than water


def test_resistance_amplification_matches_fixed_point() -> None:
    sh = estimate_cable_self_heating(
        fluid_temp_f=250.0,
        cooling_velocity_ft_s=1.0,
        current_a=100.0,
        ampacity_a=100.0,
        water_cut_frac=1.0,
    )
    # resistance_factor = R(T_c) / R(anchor T_c). At the anchor operating
    # point (77 F fluid, water rise 20 F, so anchor T_c = 97 F) this ratio
    # is 1.0; here the fluid is much hotter, so it must exceed 1.
    anchor_T_c = REFERENCE_FLUID_TEMP_F + REFERENCE_WATER_RISE_F
    raw_at_Tc = 1.0 + COPPER_TEMP_COEFF_PER_F * (sh.conductor_temp_f - 77.0)
    raw_at_anchor = 1.0 + COPPER_TEMP_COEFF_PER_F * (anchor_T_c - 77.0)
    expected = raw_at_Tc / raw_at_anchor
    # Fixed-point convergence is ~4 decimal places at 4 iterations; the
    # deviation of a 1e-6 relative-tolerance failure came from that, not
    # from a bug.
    assert sh.resistance_factor == pytest.approx(expected, rel=1e-4)
    assert sh.resistance_factor > 1.0


def test_conductor_temp_equals_fluid_plus_rise_exactly() -> None:
    # A cheap invariant, but the two-term display requirement makes it
    # load-bearing: fluid and rise must always be arithmetic components
    # of the reported conductor temperature.
    sh = _baseline()
    assert sh.conductor_temp_f == pytest.approx(sh.fluid_temp_f + sh.rise_f)


# ---------------------------------------------------------------------------
# Refusals must be enforced at the input boundary, not passed through as
# NaN or negative rise.
# ---------------------------------------------------------------------------


def test_refusal_on_out_of_range_fluid_temp() -> None:
    with pytest.raises(ValueError, match="fluid_temp_f"):
        estimate_cable_self_heating(
            fluid_temp_f=-999.0, cooling_velocity_ft_s=1.0,
            current_a=1.0, ampacity_a=1.0, water_cut_frac=0.0,
        )


def test_refusal_on_bad_water_cut() -> None:
    with pytest.raises(ValueError, match="water_cut_frac"):
        estimate_cable_self_heating(
            fluid_temp_f=100.0, cooling_velocity_ft_s=1.0,
            current_a=1.0, ampacity_a=1.0, water_cut_frac=1.5,
        )


def test_refusal_on_zero_ampacity() -> None:
    with pytest.raises(ValueError, match="ampacity_a"):
        estimate_cable_self_heating(
            fluid_temp_f=100.0, cooling_velocity_ft_s=1.0,
            current_a=10.0, ampacity_a=0.0, water_cut_frac=0.0,
        )


def test_refusal_on_negative_current() -> None:
    with pytest.raises(ValueError, match="current_a"):
        estimate_cable_self_heating(
            fluid_temp_f=100.0, cooling_velocity_ft_s=1.0,
            current_a=-1.0, ampacity_a=10.0, water_cut_frac=0.0,
        )


def test_refusal_on_nonpositive_temp_coeff() -> None:
    with pytest.raises(ValueError, match="conductor_temp_coeff"):
        estimate_cable_self_heating(
            fluid_temp_f=100.0, cooling_velocity_ft_s=1.0,
            current_a=10.0, ampacity_a=100.0, water_cut_frac=0.0,
            conductor_temp_coeff_per_f=0.0,
        )
