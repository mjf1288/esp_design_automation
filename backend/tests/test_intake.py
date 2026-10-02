"""Coupled PIP validation against physics-reference.md §§1.4 and 2.8."""
from __future__ import annotations

from esp_engine.config import CorrelationConfig
from esp_engine.inflow import InflowSpec
from esp_engine.intake import solve_intake_conditions
from esp_engine.pvt import FluidSpec


def _fluid(water_cut_frac: float = .6, gor_scf_stb: float = 500) -> FluidSpec:
    return FluidSpec(oil_api=30, gas_sg=.8, water_sg=1.02, gor_scf_stb=gor_scf_stb, water_cut_frac=water_cut_frac)


def _solve(*, setting_tvd_ft: float = 8000, setting_md_ft: float = 8000, wc: float = .6):
    return solve_intake_conditions(
        case_geometry_casing_id_in=6, setting_depth_md_ft=setting_md_ft, setting_depth_tvd_ft=setting_tvd_ft,
        target_rate_bpd=1500, inflow=InflowSpec(reservoir_pressure_psi=2000, productivity_index_bpd_psi=1.5, bubble_point_psi=2300),
        fluid_spec=_fluid(wc), perforation_tvd_ft=8500, bht_f=180, surface_temp_f=70, casing_pressure_psi=50,
        cfg=CorrelationConfig(),
    )


def test_intake_volume_workflow_published_equations() -> None:
    """The volume balance follows SPE PetroWiki ESP design, reproduced verbatim in
    physics-reference.md §2.8: Vo=qoBo, Vw=qwBw, Vg=QfreeBg, and FGVF=Vg/Vt."""
    intake = _solve()
    assert intake.total_liquid_intake_bpd == intake.oil_rate_intake_bpd + intake.water_rate_intake_bpd
    assert intake.total_fluid_intake_bpd == intake.total_liquid_intake_bpd + intake.free_gas_rate_intake_bpd
    assert intake.free_gas_fraction == intake.free_gas_rate_intake_bpd / intake.total_fluid_intake_bpd
    assert intake.converged and intake.iterations <= 50


def test_free_gas_decreases_with_higher_pip_and_tvd_not_md_controls_gradient() -> None:
    shallow = _solve(setting_tvd_ft=8000, setting_md_ft=8500)
    deeper = _solve(setting_tvd_ft=8800, setting_md_ft=9300)
    same_tvd_other_md = _solve(setting_tvd_ft=8000, setting_md_ft=11000)
    assert deeper.pip_psi > shallow.pip_psi
    assert deeper.free_gas_fraction < shallow.free_gas_fraction
    assert shallow.pip_psi == same_tvd_other_md.pip_psi


def test_water_cut_endpoints_and_determinism() -> None:
    oil = _solve(wc=0.0)
    water = _solve(wc=1.0)
    assert oil.water_rate_intake_bpd == 0
    assert water.oil_rate_intake_bpd == 0
    assert water.free_gas_rate_intake_bpd == 0
    assert _solve() == _solve()


def test_nonconvergence_is_reported_not_raised() -> None:
    result = solve_intake_conditions(
        case_geometry_casing_id_in=6, setting_depth_md_ft=8000, setting_depth_tvd_ft=8000, target_rate_bpd=1500,
        inflow=InflowSpec(reservoir_pressure_psi=2000, productivity_index_bpd_psi=1.5, bubble_point_psi=2300),
        fluid_spec=_fluid(), perforation_tvd_ft=8500, bht_f=180, surface_temp_f=70, casing_pressure_psi=50,
        cfg=CorrelationConfig(), max_iter=1, tol_psi=1e-12,
    )
    assert not result.converged
    assert result.iterations == 1
    assert result.pip_psi > 0
