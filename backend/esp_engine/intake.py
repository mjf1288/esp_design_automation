"""Pump-intake state solver implementing physics-reference.md §§1.4, 2.7--2.8,
and 3.1 for the ESP framework's intake-condition stage.

The solver deliberately uses a homogeneous static annular gradient as the
configured first-release screening model.  It is not a slip-aware pressure
traverse and must not be presented as one.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict

from .config import CorrelationConfig
from .inflow import InflowSpec, pwf_for_rate
from .pvt import FluidSpec, FluidState, solve_fluid_state
from .units import PSI_PER_FT_WATER, require_positive


class IntakeConditions(BaseModel):
    """Coupled PIP, PVT, and in-situ intake-rate result."""

    model_config = ConfigDict(frozen=True)

    pip_psi: float
    intake_temp_f: float
    setting_depth_md_ft: float
    setting_depth_tvd_ft: float
    fluid_over_pump_ft: float
    submergence_ft: float
    fluid: FluidState
    oil_rate_intake_bpd: float
    water_rate_intake_bpd: float
    free_gas_rate_intake_bpd: float
    total_liquid_intake_bpd: float
    total_fluid_intake_bpd: float
    free_gas_fraction: float
    mixture_density_lb_ft3: float
    mixture_sg: float
    mixture_viscosity_cp: float
    converged: bool
    iterations: int


def _intake_temp_f(*, setting_tvd_ft: float, perforation_tvd_ft: float, bht_f: float, surface_temp_f: float) -> float:
    """Linear TVD temperature interpolation; avoids using MD in thermal gradient."""

    if perforation_tvd_ft <= 0.0:
        return bht_f
    frac = max(0.0, min(1.0, setting_tvd_ft / perforation_tvd_ft))
    return surface_temp_f + frac * (bht_f - surface_temp_f)


def _volumes(
    fluid: FluidState, fluid_spec: FluidSpec, target_rate_bpd: float
) -> tuple[float, float, float, float, float, float, float, float]:
    """Return in-situ phase rates, gas fraction, and no-slip mixture properties."""

    qo_st = target_rate_bpd * (1.0 - fluid_spec.water_cut_frac)
    qw_st = target_rate_bpd * fluid_spec.water_cut_frac
    free_gas_std_scf_d = qo_st * fluid.free_gas_scf_stb
    vo = qo_st * fluid.bo_rb_stb
    vw = qw_st * fluid.bw_rb_stb
    vg = free_gas_std_scf_d * fluid.bg_ft3_scf
    vl = vo + vw
    vt = vl + vg
    if vt <= 0.0:
        raise ValueError("intake total in-situ fluid volume must be positive")
    fgvf = vg / vt
    rho = (vo * fluid.oil_density_lb_ft3 + vw * fluid.water_density_lb_ft3 + vg * fluid.gas_density_lb_ft3) / vt
    # No-slip volume-weighted screen.  At zero gas this is the usual liquid mix;
    # free gas is included to make the stated homogeneous-gradient assumption.
    mu = (vo * fluid.oil_viscosity_cp + vw * fluid.water_viscosity_cp + vg * fluid.gas_viscosity_cp) / vt
    return qo_st, qw_st, vg, vl, vt, fgvf, rho, mu


def solve_intake_conditions(
    *,
    case_geometry_casing_id_in: float,
    setting_depth_md_ft: float,
    setting_depth_tvd_ft: float,
    target_rate_bpd: float,
    inflow: InflowSpec,
    fluid_spec: FluidSpec,
    perforation_tvd_ft: float,
    bht_f: float,
    surface_temp_f: float,
    casing_pressure_psi: float,
    cfg: CorrelationConfig,
    max_iter: int = 50,
    tol_psi: float = 0.5,
) -> IntakeConditions:
    """Solve coupled PIP with damped substitution and a bounded bisection fallback.

    The IPR rate is oil rate, whereas ``target_rate_bpd`` is the framework's total
    liquid target.  Consequently the IPR is evaluated at ``q_o=q_L(1-WC)``.
    """

    require_positive("case_geometry_casing_id_in", case_geometry_casing_id_in)
    require_positive("setting_depth_md_ft", setting_depth_md_ft)
    require_positive("setting_depth_tvd_ft", setting_depth_tvd_ft)
    require_positive("target_rate_bpd", target_rate_bpd)
    require_positive("perforation_tvd_ft", perforation_tvd_ft)
    if bht_f <= -459.67 or surface_temp_f <= -459.67:
        raise ValueError("bht_f and surface_temp_f must be above absolute zero")
    if casing_pressure_psi < 0.0:
        raise ValueError(f"casing_pressure_psi must be non-negative, got {casing_pressure_psi}")
    if max_iter < 1:
        raise ValueError(f"max_iter must be at least 1, got {max_iter}")
    require_positive("tol_psi", tol_psi)

    q_oil_bpd = target_rate_bpd * (1.0 - fluid_spec.water_cut_frac)
    # A 100% water-cut case cannot use an oil IPR to determine Pwf.  Its limiting
    # drawdown is zero by construction, so use reservoir pressure at the datum.
    pwf_psi = inflow.reservoir_pressure_psi if q_oil_bpd == 0.0 else pwf_for_rate(inflow, q_oil_bpd)
    intake_temp_f = _intake_temp_f(
        setting_tvd_ft=setting_depth_tvd_ft,
        perforation_tvd_ft=perforation_tvd_ft,
        bht_f=bht_f,
        surface_temp_f=surface_temp_f,
    )
    delta_tvd_ft = setting_depth_tvd_ft - perforation_tvd_ft

    def state_at(pip_psi: float) -> tuple[FluidState, tuple[float, float, float, float, float, float, float, float], float]:
        fluid = solve_fluid_state(fluid_spec, max(0.01, pip_psi), intake_temp_f, cfg)
        values = _volumes(fluid, fluid_spec, target_rate_bpd)
        rho = values[6]
        sg = rho / 62.4
        target_pip = pwf_psi + PSI_PER_FT_WATER * sg * delta_tvd_ft
        return fluid, values, target_pip

    # Start at a liquid-gradient estimate.  The lower bound is strictly positive
    # to protect every PVT correlation's absolute-pressure requirement.
    liquid_sg_guess = (
        (1.0 - fluid_spec.water_cut_frac) * (141.5 / (fluid_spec.oil_api + 131.5))
        + fluid_spec.water_cut_frac * fluid_spec.water_sg
    )
    estimate = max(0.01, pwf_psi + PSI_PER_FT_WATER * liquid_sg_guess * delta_tvd_ft)
    best_pip = estimate
    best_residual = float("inf")
    best_payload: tuple[FluidState, tuple[float, float, float, float, float, float, float, float], float] | None = None
    converged = False
    used_iterations = 0

    # Damped fixed point phase; its rate is stable in ordinary wells and gives a
    # useful initial value for bisection in the nonlinear gas-breakout range.
    for iteration in range(1, max_iter + 1):
        payload = state_at(estimate)
        residual = estimate - payload[2]
        if abs(residual) < abs(best_residual):
            best_pip, best_residual, best_payload = estimate, residual, payload
        used_iterations = iteration
        if abs(residual) <= tol_psi:
            converged = True
            break
        candidate = max(0.01, payload[2])
        estimate = max(0.01, 0.5 * estimate + 0.5 * candidate)

    # Bracketed fallback is capped by the remaining budget.  It is attempted even
    # after damped failure, never changes the function's termination guarantee.
    if not converged and used_iterations < max_iter:
        low = 0.01
        high = max(1.0, pwf_psi + abs(delta_tvd_ft) * PSI_PER_FT_WATER * 1.5)
        low_payload = state_at(low)
        high_payload = state_at(high)
        flo, fhi = low - low_payload[2], high - high_payload[2]
        if abs(flo) < abs(best_residual):
            best_pip, best_residual, best_payload = low, flo, low_payload
        if abs(fhi) < abs(best_residual):
            best_pip, best_residual, best_payload = high, fhi, high_payload
        if flo * fhi <= 0.0:
            for iteration in range(used_iterations + 1, max_iter + 1):
                mid = (low + high) / 2.0
                mid_payload = state_at(mid)
                fmid = mid - mid_payload[2]
                used_iterations = iteration
                if abs(fmid) < abs(best_residual):
                    best_pip, best_residual, best_payload = mid, fmid, mid_payload
                if abs(fmid) <= tol_psi:
                    converged = True
                    break
                if flo * fmid <= 0.0:
                    high, fhi = mid, fmid
                else:
                    low, flo = mid, fmid

    if best_payload is None:
        # Mathematically unreachable after input validation, retained so a future
        # PVT correlation cannot turn non-convergence into a dropped scenario.
        best_payload = state_at(best_pip)
    fluid, values, _ = best_payload
    qo, qw, vg, vl, vt, fgvf, density, viscosity = values
    mixture_sg = density / 62.4
    gradient = PSI_PER_FT_WATER * mixture_sg
    fluid_over_pump = (best_pip - casing_pressure_psi) / gradient

    return IntakeConditions(
        pip_psi=best_pip,
        intake_temp_f=intake_temp_f,
        setting_depth_md_ft=setting_depth_md_ft,
        setting_depth_tvd_ft=setting_depth_tvd_ft,
        fluid_over_pump_ft=fluid_over_pump,
        submergence_ft=fluid_over_pump,
        fluid=fluid,
        oil_rate_intake_bpd=qo * fluid.bo_rb_stb,
        water_rate_intake_bpd=qw * fluid.bw_rb_stb,
        free_gas_rate_intake_bpd=vg,
        total_liquid_intake_bpd=vl,
        total_fluid_intake_bpd=vt,
        free_gas_fraction=fgvf,
        mixture_density_lb_ft3=density,
        mixture_sg=mixture_sg,
        mixture_viscosity_cp=viscosity,
        converged=converged,
        iterations=used_iterations,
    )
