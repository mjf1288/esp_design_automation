"""TDH validation against physics-reference.md §4, including the full kernel path."""
from __future__ import annotations

import pytest

from esp_engine.config import CorrelationConfig, GasThresholds
from esp_engine.gas import assess_gas
from esp_engine.inflow import InflowSpec
from esp_engine.intake import solve_intake_conditions
from esp_engine.pvt import FluidSpec, solve_fluid_state
from esp_engine.tdh import compute_tdh


def test_darcy_weibach_published_equation_example() -> None:
    """Darcy--Weisbach / Swamee--Jain equations in physics-reference.md §4.2:
    the test independently reconstructs Hf=f(L/D)v²/(2g) from returned diagnostics."""
    result = compute_tdh(setting_depth_tvd_ft=8000, pip_psi=1000, wellhead_pressure_psi=100,
                         total_liquid_rate_bpd=1500, tubing_id_in=2.441, tubing_length_ft=8000,
                         mixture_sg=.95, mixture_viscosity_cp=1.0)
    diameter = 2.441 / 12
    expected = result.friction_factor * 8000 / diameter * result.velocity_ft_s**2 / (2 * 32.174)
    assert result.tubing_friction_ft == pytest.approx(expected)


def test_tdh_and_friction_increase_with_depth_and_rate() -> None:
    base = compute_tdh(setting_depth_tvd_ft=7000, pip_psi=1000, wellhead_pressure_psi=100, total_liquid_rate_bpd=1000,
                       tubing_id_in=2.441, tubing_length_ft=7000, mixture_sg=.95, mixture_viscosity_cp=1)
    deeper = compute_tdh(setting_depth_tvd_ft=8000, pip_psi=1000, wellhead_pressure_psi=100, total_liquid_rate_bpd=1000,
                         tubing_id_in=2.441, tubing_length_ft=8000, mixture_sg=.95, mixture_viscosity_cp=1)
    faster = compute_tdh(setting_depth_tvd_ft=7000, pip_psi=1000, wellhead_pressure_psi=100, total_liquid_rate_bpd=1500,
                         tubing_id_in=2.441, tubing_length_ft=7000, mixture_sg=.95, mixture_viscosity_cp=1)
    assert deeper.tdh_ft > base.tdh_ft
    assert faster.tubing_friction_ft > base.tubing_friction_ft
    assert faster.velocity_ft_s > base.velocity_ft_s


def test_realistic_complete_case_physical_consistency_and_determinism() -> None:
    """Complete real-ish well from the requested acceptance case: 8,000-ft pump,
    2,000-psi reservoir, 30 API oil, 500 scf/stb GOR, 60% water cut, and 1,500 bpd.
    The bounds are practical ESP screening bands, not a vendor release design."""
    cfg = CorrelationConfig()
    fluid_spec = FluidSpec(oil_api=30, gas_sg=.8, water_sg=1.02, gor_scf_stb=500, water_cut_frac=.6)
    state = solve_fluid_state(fluid_spec, 1500, 175, cfg)
    inflow = InflowSpec(reservoir_pressure_psi=2000, productivity_index_bpd_psi=1.5, bubble_point_psi=state.bubble_point_psi)
    intake = solve_intake_conditions(case_geometry_casing_id_in=6, setting_depth_md_ft=8000, setting_depth_tvd_ft=8000,
        target_rate_bpd=1500, inflow=inflow, fluid_spec=fluid_spec, perforation_tvd_ft=8500, bht_f=180,
        surface_temp_f=70, casing_pressure_psi=50, cfg=cfg)
    gas = assess_gas(intake, casing_id_in=6, equipment_od_in=4.5, has_vsd=True, cfg=cfg, thresholds=GasThresholds())
    result = compute_tdh(setting_depth_tvd_ft=8000, pip_psi=intake.pip_psi, wellhead_pressure_psi=100,
        total_liquid_rate_bpd=intake.total_liquid_intake_bpd, tubing_id_in=2.441, tubing_length_ft=8000,
        mixture_sg=intake.mixture_sg, mixture_viscosity_cp=intake.mixture_viscosity_cp)
    assert 900 < intake.pip_psi < 1900
    assert 0.02 < intake.free_gas_fraction < 0.25
    assert 1500 < result.tdh_ft < 4500
    assert result == compute_tdh(setting_depth_tvd_ft=8000, pip_psi=intake.pip_psi, wellhead_pressure_psi=100,
        total_liquid_rate_bpd=intake.total_liquid_intake_bpd, tubing_id_in=2.441, tubing_length_ft=8000,
        mixture_sg=intake.mixture_sg, mixture_viscosity_cp=intake.mixture_viscosity_cp)
    assert gas.fgvf_entering_pump <= intake.free_gas_fraction
