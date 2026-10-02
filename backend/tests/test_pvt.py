"""PVT validation against correlations documented in physics-reference.md §2."""
from __future__ import annotations

import math

import pytest

from esp_engine.config import CorrelationConfig, ZFactorCorrelation
from esp_engine.pvt import (
    FluidSpec,
    dak_z_factor,
    solve_fluid_state,
    standing_bubble_point_psi,
    standing_rs_scf_stb,
    water_viscosity_cp,
)
from esp_engine.units import f_to_r


def _spec(**updates: float) -> FluidSpec:
    values = dict(oil_api=30.0, gas_sg=0.80, water_sg=1.02, gor_scf_stb=500.0, water_cut_frac=0.60)
    values.update(updates)
    return FluidSpec(**values)


def test_standing_published_equation_example() -> None:
    """Standing Eqns., reproduced in KSU ESP notes cited in reference §2.2: Rsb=500,
    gamma_g=.8, API=30, T=180 F gives Pb=2315.79 psia by direct hand evaluation."""
    pb = standing_bubble_point_psi(500.0, 0.80, 30.0, f_to_r(180.0))
    assert pb == pytest.approx(2315.7916, abs=0.01)
    assert standing_rs_scf_stb(pb, .8, 30.0, f_to_r(180), 500, pb) == pytest.approx(500.0)


def test_rs_monotonic_and_flat_above_bubble_point() -> None:
    pb = standing_bubble_point_psi(500, .8, 30, f_to_r(180))
    low = standing_rs_scf_stb(600, .8, 30, f_to_r(180), 500, pb)
    mid = standing_rs_scf_stb(1200, .8, 30, f_to_r(180), 500, pb)
    at_pb = standing_rs_scf_stb(pb, .8, 30, f_to_r(180), 500, pb)
    above = standing_rs_scf_stb(pb + 500, .8, 30, f_to_r(180), 500, pb)
    assert 0 < low < mid < at_pb
    assert at_pb == above == 500


def test_bo_and_z_physical_sanity() -> None:
    low_rs = solve_fluid_state(_spec(), 600, 180, CorrelationConfig())
    high_rs = solve_fluid_state(_spec(), 1800, 180, CorrelationConfig())
    assert high_rs.bo_rb_stb > low_rs.bo_rb_stb
    low_pressure_z = dak_z_factor(20, f_to_r(180), .8)
    assert low_pressure_z == pytest.approx(1.0, abs=.03)


def test_rankine_and_fraction_unit_traps_raise() -> None:
    with pytest.raises(ValueError, match="Rankine"):
        standing_bubble_point_psi(500, .8, 30, 180)  # Fahrenheit accidentally supplied
    with pytest.raises(ValueError, match="fraction"):
        solve_fluid_state(_spec(water_cut_frac=85.0), 1000, 180, CorrelationConfig())


def test_zero_gor_and_water_cut_edges_are_deterministic() -> None:
    zero = solve_fluid_state(_spec(gor_scf_stb=0.0, water_cut_frac=0.0), 1000, 180, CorrelationConfig())
    water = solve_fluid_state(_spec(gor_scf_stb=0.0, water_cut_frac=1.0), 1000, 180, CorrelationConfig())
    assert zero.bubble_point_psi == zero.rs_scf_stb == zero.free_gas_scf_stb == 0.0
    assert water.free_gas_scf_stb == 0.0
    assert water == solve_fluid_state(_spec(gor_scf_stb=0.0, water_cut_frac=1.0), 1000, 180, CorrelationConfig())


def test_selectable_hall_yarborough_and_water_viscosity_are_physical() -> None:
    """Hall--Yarborough and Mathews--Russell forms are the selectable/screening
    correlations identified by physics-reference.md §§2.5--2.6."""
    state = solve_fluid_state(_spec(), 1000, 180, CorrelationConfig(z_factor=ZFactorCorrelation.HALL_YARBOROUGH))
    assert .6 < state.z_factor < 1.2
    assert .1 < water_viscosity_cp(1000, f_to_r(180), 30000) < 1.5
    assert state.correlations_used["z_factor"] == "hall_yarborough"
