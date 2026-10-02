"""Tests for the §6D.2 motor thermal self-heating model.

The v0.6 framework closes the winding-temperature gap with a two-term display:
fluid temperature and calculated self-heating rise. These tests pin the
anchor calibration to the exact framework-cited numbers, exercise each
physical factor separately, and mutation-guard against silent regressions
back to the previous placeholder (winding = intake).
"""

from __future__ import annotations

import math

import pytest

from esp_engine.motor_thermal import (
    MAX_MODELED_FGVF,
    REFERENCE_EFFICIENCY,
    REFERENCE_OIL_RISE_F,
    REFERENCE_VELOCITY_FT_S,
    REFERENCE_WATER_RISE_F,
    VELOCITY_EXPONENT,
    estimate_motor_self_heating,
)


def _call(**overrides):
    base = dict(
        intake_temp_f=180.0,
        cooling_velocity_ft_s=REFERENCE_VELOCITY_FT_S,
        loading_frac=1.0,
        water_cut_frac=1.0,
        free_gas_fraction_at_intake=0.0,
        motor_efficiency=REFERENCE_EFFICIENCY,
        motor_type="induction",
    )
    base.update(overrides)
    return estimate_motor_self_heating(**base)


class TestFrameworkAnchor:
    """The framework cites specific rises for the two reference fluids at
    1 ft/sec, nameplate load, typical induction. Both must reproduce exactly
    or the model is anchored to something other than what the framework
    says."""

    def test_water_at_reference_conditions_is_50_F(self):
        result = _call(water_cut_frac=1.0)
        assert result.rise_f == pytest.approx(REFERENCE_WATER_RISE_F, abs=1e-9)
        assert result.reference_rise_f == pytest.approx(50.0, abs=1e-9)

    def test_oil_at_reference_conditions_is_90_F(self):
        result = _call(water_cut_frac=0.0)
        assert result.rise_f == pytest.approx(REFERENCE_OIL_RISE_F, abs=1e-9)
        assert result.reference_rise_f == pytest.approx(90.0, abs=1e-9)

    def test_water_cut_blend_is_linear(self):
        r50 = _call(water_cut_frac=0.5)
        # 50% water/50% oil → mean of anchors
        assert r50.reference_rise_f == pytest.approx(70.0, abs=1e-9)


class TestFactorsMoveTheAnswer:
    """Every input the framework requires must move the rise or the model
    isn't actually using it. This catches the placeholder regression that
    ignored inputs and returned intake temperature."""

    def test_higher_loading_increases_rise(self):
        low = _call(loading_frac=0.5)
        high = _call(loading_frac=0.9)
        assert high.rise_f > low.rise_f
        # Linear-in-load structure: doubling load doubles rise (other factors held)
        assert high.rise_f / low.rise_f == pytest.approx(0.9 / 0.5, abs=1e-9)

    def test_higher_velocity_reduces_rise(self):
        slow = _call(cooling_velocity_ft_s=1.0)
        fast = _call(cooling_velocity_ft_s=4.0)
        assert fast.rise_f < slow.rise_f
        # Dittus-Boelter form: (v_ref/v)^0.8
        ratio_expected = (1.0 / 4.0) ** VELOCITY_EXPONENT
        assert fast.rise_f / slow.rise_f == pytest.approx(ratio_expected, abs=1e-9)

    def test_higher_efficiency_reduces_rise(self):
        # A PMM at 0.94 efficiency generates ~40% of the heat of a typical
        # induction motor at the same load. That is a real, physical benefit
        # the previous placeholder did not capture at all.
        induction = _call(motor_efficiency=0.85)
        pmm = _call(motor_efficiency=0.94)
        assert pmm.rise_f < induction.rise_f
        assert pmm.rise_f / induction.rise_f == pytest.approx(
            (1 - 0.94) / (1 - 0.85), abs=1e-9
        )

    def test_gas_at_intake_increases_rise(self):
        no_gas = _call(free_gas_fraction_at_intake=0.0)
        with_gas = _call(free_gas_fraction_at_intake=0.2)
        assert with_gas.rise_f > no_gas.rise_f
        assert with_gas.gas_factor == pytest.approx(1.0 / 0.8, abs=1e-9)

    def test_gas_augmentation_is_capped_at_framework_limit(self):
        capped = _call(free_gas_fraction_at_intake=0.9)
        # Framework refuses to design above 50% FGVF on separate gates; the
        # thermal model must not extrapolate wildly for a configuration that
        # will be rejected upstream.
        assert capped.gas_factor == pytest.approx(1.0 / (1.0 - MAX_MODELED_FGVF), abs=1e-9)


class TestDegenerateInputRefusals:
    """The model must not accept nonsense inputs silently."""

    def test_rejects_negative_load(self):
        with pytest.raises(ValueError):
            _call(loading_frac=-0.1)

    def test_rejects_zero_load(self):
        # A zero-load motor is a no-op with no operating amps and no losses;
        # letting it through returns rise=0 and hides the caller bug.
        with pytest.raises(ValueError):
            _call(loading_frac=0.0)

    def test_rejects_out_of_range_water_cut(self):
        with pytest.raises(ValueError):
            _call(water_cut_frac=1.2)

    def test_rejects_fgvf_at_one(self):
        with pytest.raises(ValueError):
            _call(free_gas_fraction_at_intake=1.0)


class TestEfficiencyFallback:
    """When the catalog omits efficiency the model must fall back to the
    anchor's reference induction value, and disclose the fallback rather than
    silently apply it. This is where the previous placeholder pattern hid the
    biggest deficiencies."""

    def test_missing_efficiency_uses_reference_and_discloses(self):
        r = _call(motor_efficiency=None)
        # Efficiency factor is exactly 1.0 at the reference value
        assert r.efficiency_factor == pytest.approx(1.0, abs=1e-9)
        assert r.efficiency_basis == "reference_induction_fallback"

    def test_cataloged_efficiency_is_reported(self):
        r = _call(motor_efficiency=0.92)
        assert r.efficiency_basis == "cataloged"


class TestPlaceholderRegressionGuard:
    """Mutation guard: if the model regresses to the pre-fix behavior
    (winding = intake, rise = 0), these must fail loudly."""

    def test_rise_is_positive_at_realistic_operating_point(self):
        # Any real ESP running under load past a real cooling velocity must
        # produce nonzero self-heating. The pre-fix placeholder returned
        # zero rise regardless of these numbers.
        r = estimate_motor_self_heating(
            intake_temp_f=180.0,
            cooling_velocity_ft_s=3.0,
            loading_frac=0.7,
            water_cut_frac=0.9,
            free_gas_fraction_at_intake=0.02,
            motor_efficiency=0.85,
            motor_type="induction",
        )
        assert r.rise_f > 0.0
        assert r.winding_temp_f > r.fluid_temp_f

    def test_winding_equals_fluid_plus_rise(self):
        r = _call()
        assert r.winding_temp_f == pytest.approx(r.fluid_temp_f + r.rise_f, abs=1e-9)

    def test_hot_well_pushes_toward_rating(self):
        # An oil-dominant, hot well with modest cooling velocity must push
        # winding temperature meaningfully above intake — the whole reason
        # the framework requires this calculation. If future code silently
        # zeroes any factor, this catches it.
        r = estimate_motor_self_heating(
            intake_temp_f=250.0,
            cooling_velocity_ft_s=1.0,
            loading_frac=0.9,
            water_cut_frac=0.2,
            free_gas_fraction_at_intake=0.0,
            motor_efficiency=0.85,
            motor_type="induction",
        )
        # 82°F rise at these inputs; not the trivial 0.
        assert r.rise_f > 60.0
        assert r.winding_temp_f > 300.0
