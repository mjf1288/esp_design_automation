"""Black-oil PVT kernel implementing physics-reference.md §2 for the ESP framework
fluid-state and pump-intake workflow (§§2.8 and 3.1).

All public correlation functions accept absolute pressure in psia and temperature
in Rankine. ``solve_fluid_state`` is the deliberate boundary that accepts degF,
converts it once, and records the selected correlation family.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict

from .config import (
    BoCorrelation,
    BubblePointCorrelation,
    CorrelationConfig,
    RsCorrelation,
    ZFactorCorrelation,
)
from .units import (
    RANKINE_OFFSET,
    WATER_DENSITY_LB_FT3,
    api_to_sg,
    f_to_r,
    require_absolute_temperature,
    require_fraction,
    require_positive,
)


class FluidState(BaseModel):
    """PVT properties at one absolute pressure and temperature state."""

    model_config = ConfigDict(frozen=True)

    pressure_psi: float
    temp_f: float
    rs_scf_stb: float
    bo_rb_stb: float
    oil_density_lb_ft3: float
    oil_viscosity_cp: float
    oil_sg: float
    bubble_point_psi: float
    z_factor: float
    bg_ft3_scf: float
    gas_density_lb_ft3: float
    gas_viscosity_cp: float
    free_gas_scf_stb: float
    bw_rb_stb: float
    water_density_lb_ft3: float
    water_viscosity_cp: float
    correlations_used: dict[str, str]


class FluidSpec(BaseModel):
    """Stock-tank fluid inputs.  GOR is used as saturated solution GOR (Rsb)."""

    model_config = ConfigDict(frozen=True)

    oil_api: float
    gas_sg: float
    water_sg: float
    gor_scf_stb: float
    water_cut_frac: float
    salinity_ppm: float = 30000.0
    bubble_point_psi: float | None = None


def _temp_f(temp_r: float) -> float:
    require_absolute_temperature(temp_r)
    return temp_r - RANKINE_OFFSET


def _validate_oil_inputs(*, rs_scf_stb: float, gas_sg: float, oil_api: float) -> None:
    if rs_scf_stb < 0.0:
        raise ValueError(f"rs_scf_stb must be non-negative, got {rs_scf_stb}")
    require_positive("gas_sg", gas_sg)
    # api_to_sg contains the physically meaningful API lower bound.
    api_to_sg(oil_api)


def standing_bubble_point_psi(
    rsb_scf_stb: float, gas_sg: float, oil_api: float, temp_r: float
) -> float:
    """Standing bubble-point correlation (physics-reference.md §2.2)."""

    _validate_oil_inputs(rs_scf_stb=rsb_scf_stb, gas_sg=gas_sg, oil_api=oil_api)
    temp_f = _temp_f(temp_r)
    if rsb_scf_stb == 0.0:
        return 0.0
    bracket = (rsb_scf_stb / gas_sg) ** 0.83 * 10.0 ** (
        0.00091 * temp_f - 0.0125 * oil_api
    )
    return max(0.0, 18.2 * (bracket - 1.4))


def standing_rs_scf_stb(
    pressure_psi: float,
    gas_sg: float,
    oil_api: float,
    temp_r: float,
    rsb_scf_stb: float,
    bubble_point_psi: float,
) -> float:
    """Standing solution GOR, limited to the supplied saturated solution GOR."""

    require_positive("pressure_psi", pressure_psi)
    _validate_oil_inputs(rs_scf_stb=rsb_scf_stb, gas_sg=gas_sg, oil_api=oil_api)
    temp_f = _temp_f(temp_r)
    if rsb_scf_stb == 0.0 or bubble_point_psi <= 0.0:
        return 0.0
    if pressure_psi >= bubble_point_psi:
        return rsb_scf_stb
    term = (pressure_psi / 18.2 + 1.4) * 10.0 ** (
        0.0125 * oil_api - 0.00091 * temp_f
    )
    return min(rsb_scf_stb, max(0.0, gas_sg * term**1.2048))


def standing_bo_rb_stb(rs_scf_stb: float, gas_sg: float, oil_api: float, temp_r: float) -> float:
    """Standing saturated-oil FVF correlation (physics-reference.md §2.2)."""

    _validate_oil_inputs(rs_scf_stb=rs_scf_stb, gas_sg=gas_sg, oil_api=oil_api)
    temp_f = _temp_f(temp_r)
    oil_sg = api_to_sg(oil_api)
    x = rs_scf_stb * math.sqrt(gas_sg / oil_sg) + 1.25 * temp_f
    return 0.9759 + 0.00012 * x**1.2


def _vb_constants(oil_api: float) -> tuple[float, float, float, float, float, float]:
    if oil_api <= 30.0:
        return 0.0362, 1.0937, 25.7240, 4.677e-4, 1.751e-5, -1.811e-8
    return 0.0178, 1.1870, 23.9310, 4.670e-4, 1.100e-5, 1.337e-9


def vazquez_beggs_bubble_point_psi(
    rsb_scf_stb: float, gas_sg: float, oil_api: float, temp_r: float
) -> float:
    """Vazquez--Beggs bubble point using gamma_gs = gamma_g (unknown separator data)."""

    _validate_oil_inputs(rs_scf_stb=rsb_scf_stb, gas_sg=gas_sg, oil_api=oil_api)
    require_absolute_temperature(temp_r)
    if rsb_scf_stb == 0.0:
        return 0.0
    c1, c2, c3, *_ = _vb_constants(oil_api)
    denom = c1 * gas_sg * math.exp(c3 * oil_api / temp_r)
    return (rsb_scf_stb / denom) ** (1.0 / c2)


def vazquez_beggs_rs_scf_stb(
    pressure_psi: float,
    gas_sg: float,
    oil_api: float,
    temp_r: float,
    rsb_scf_stb: float,
    bubble_point_psi: float,
) -> float:
    """Vazquez--Beggs solution GOR using gamma_gs = gamma_g (reference §2.3)."""

    require_positive("pressure_psi", pressure_psi)
    _validate_oil_inputs(rs_scf_stb=rsb_scf_stb, gas_sg=gas_sg, oil_api=oil_api)
    require_absolute_temperature(temp_r)
    if rsb_scf_stb == 0.0 or bubble_point_psi <= 0.0:
        return 0.0
    if pressure_psi >= bubble_point_psi:
        return rsb_scf_stb
    c1, c2, c3, *_ = _vb_constants(oil_api)
    rs = c1 * gas_sg * pressure_psi**c2 * math.exp(c3 * oil_api / temp_r)
    return min(rsb_scf_stb, max(0.0, rs))


def vazquez_beggs_bo_rb_stb(
    rs_scf_stb: float, gas_sg: float, oil_api: float, temp_r: float
) -> float:
    """Vazquez--Beggs saturated-oil FVF (physics-reference.md §2.3)."""

    _validate_oil_inputs(rs_scf_stb=rs_scf_stb, gas_sg=gas_sg, oil_api=oil_api)
    temp_f = _temp_f(temp_r)
    *_, a1, a2, a3 = _vb_constants(oil_api)
    api_over_gas = oil_api / gas_sg
    return 1.0 + a1 * rs_scf_stb + a2 * (temp_f - 60.0) * api_over_gas + a3 * rs_scf_stb * (temp_f - 60.0) * api_over_gas


def beggs_robinson_dead_oil_viscosity_cp(oil_api: float, temp_r: float) -> float:
    """Beggs--Robinson dead-oil viscosity (physics-reference.md §2.4)."""

    temp_f = _temp_f(temp_r)
    if temp_f <= 0.0:
        raise ValueError(f"temperature must be above 0 degF for Beggs--Robinson, got {temp_f}")
    api_to_sg(oil_api)
    z = 3.0324 - 0.02023 * oil_api
    x = 10.0**z * temp_f**-1.163
    return 10.0**x - 1.0


def beggs_robinson_live_oil_viscosity_cp(
    dead_oil_viscosity_cp: float, rs_scf_stb: float, pressure_psi: float, bubble_point_psi: float
) -> float:
    """Beggs--Robinson live-oil viscosity plus its documented above-Pb extension."""

    require_positive("dead_oil_viscosity_cp", dead_oil_viscosity_cp)
    require_positive("pressure_psi", pressure_psi)
    if rs_scf_stb < 0.0:
        raise ValueError(f"rs_scf_stb must be non-negative, got {rs_scf_stb}")
    a = 10.715 * (rs_scf_stb + 100.0) ** -0.515
    b = 5.44 * (rs_scf_stb + 150.0) ** -0.338
    saturated = a * dead_oil_viscosity_cp**b
    if bubble_point_psi <= 0.0 or pressure_psi <= bubble_point_psi:
        return saturated
    m = 2.6 * pressure_psi**1.187 * math.exp(-11.513 - 8.98e-5 * pressure_psi)
    return saturated * (pressure_psi / bubble_point_psi) ** m


def sutton_pseudocriticals(gas_sg: float) -> tuple[float, float]:
    """Sutton pseudo-critical pressure [psia] and temperature [Rankine]."""

    require_positive("gas_sg", gas_sg)
    return 756.8 - 131.0 * gas_sg - 3.6 * gas_sg**2, 169.2 + 349.5 * gas_sg - 74.0 * gas_sg**2


def _dak_residual(z_factor: float, ppr: float, tpr: float) -> float:
    rho_r = 0.27 * ppr / (z_factor * tpr)
    a1, a2, a3, a4, a5 = 0.32650, -1.07000, -0.53390, 0.01569, -0.05165
    a6, a7, a8, a9, a10, a11 = 0.54750, -0.73610, 0.18440, 0.10560, 0.61340, 0.72100
    c1 = a1 + a2 / tpr + a3 / tpr**2 + a4 / tpr**3 + a5 / tpr**4
    c2 = a6 + a7 / tpr + a8 / tpr**2
    c3 = a9 / tpr**3 * (a7 / tpr + a8 / tpr**2)
    c4 = a10 / tpr**3 * rho_r**2 * (1.0 + a11 * rho_r**2) * math.exp(-a11 * rho_r**2)
    rhs = 1.0 + c1 * rho_r + c2 * rho_r**2 - c3 * rho_r**5 + c4
    return z_factor - rhs


def _bracketed_root(function, low: float, high: float, *, max_iter: int = 100, tol: float = 1e-10) -> float:
    """Deterministic bisection with a small scan for non-monotonic empirical roots."""

    last_x, last_f = low, function(low)
    best_x, best_abs = last_x, abs(last_f)
    for index in range(1, 401):
        x = low + (high - low) * index / 400.0
        fx = function(x)
        if abs(fx) < best_abs:
            best_x, best_abs = x, abs(fx)
        if last_f == 0.0:
            return last_x
        if fx == 0.0:
            return x
        if last_f * fx < 0.0:
            lo, hi, flo = last_x, x, last_f
            for _ in range(max_iter):
                mid = (lo + hi) / 2.0
                fm = function(mid)
                if abs(fm) < tol or hi - lo < tol:
                    return mid
                if flo * fm <= 0.0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            return (lo + hi) / 2.0
        last_x, last_f = x, fx
    # DAK can have difficult low-temperature regions.  A deterministic best
    # residual is preferable to an unbounded iteration, but callers receive a
    # physically positive value rather than a fake convergence claim.
    return best_x


def dak_z_factor(pressure_psi: float, temp_r: float, gas_sg: float) -> float:
    """Dranchuk--Abou-Kassem z-factor solved on a safeguarded positive bracket."""

    require_positive("pressure_psi", pressure_psi)
    require_absolute_temperature(temp_r)
    ppc, tpc = sutton_pseudocriticals(gas_sg)
    ppr, tpr = pressure_psi / ppc, temp_r / tpc
    return _bracketed_root(lambda z: _dak_residual(z, ppr, tpr), 0.2, 2.0)


def hall_yarborough_z_factor(pressure_psi: float, temp_r: float, gas_sg: float) -> float:
    """Hall--Yarborough Standing--Katz fit (physics-reference.md §2.5)."""

    require_positive("pressure_psi", pressure_psi)
    require_absolute_temperature(temp_r)
    ppc, tpc = sutton_pseudocriticals(gas_sg)
    ppr, tpr = pressure_psi / ppc, temp_r / tpc
    t = 1.0 / tpr
    a = 0.06125 * t * math.exp(-1.2 * (1.0 - t) ** 2)
    b = t * (14.76 - 9.76 * t + 4.58 * t**2)
    c = t * (90.7 - 242.2 * t + 42.4 * t**2)
    d = 2.18 + 2.82 * t

    def residual(y: float) -> float:
        return -a * ppr + (y + y**2 + y**3 - y**4) / (1.0 - y) ** 3 - b * y**2 + c * y**d

    y = _bracketed_root(residual, 1e-8, 0.999)
    return max(0.05, min(5.0, a * ppr / y))


def mccain_bw_rb_stb(pressure_psi: float, temp_r: float) -> float:
    """McCain water formation-volume factor (physics-reference.md §2.6)."""

    require_positive("pressure_psi", pressure_psi)
    temp_f = _temp_f(temp_r)
    dvwt = -1.0001e-2 + 1.33391e-4 * temp_f + 5.50654e-7 * temp_f**2
    dvwp = (-1.95301e-9 * pressure_psi * temp_f - 1.72834e-13 * pressure_psi**2 * temp_f
            - 3.58922e-7 * pressure_psi - 2.25341e-10 * pressure_psi**2)
    bw = (1.0 + dvwt) * (1.0 + dvwp)
    if bw <= 0.0:
        raise ValueError(f"McCain water FVF became non-positive ({bw}) at supplied pressure/temperature")
    return bw


def water_density_lb_ft3(pressure_psi: float, temp_r: float, salinity_ppm: float = 30000.0) -> float:
    """Brine density from McCain Bw and the §2.6 NaCl-equivalent density equation."""

    if salinity_ppm < 0.0:
        raise ValueError(f"salinity_ppm must be non-negative, got {salinity_ppm}")
    bw = mccain_bw_rb_stb(pressure_psi, temp_r)
    salinity_wt_pct = salinity_ppm / 10000.0
    rho_sc = 62.368 + 0.438603 * salinity_wt_pct + 1.60074e-3 * salinity_wt_pct**2
    return rho_sc / bw


def water_viscosity_cp(pressure_psi: float, temp_r: float, salinity_ppm: float = 30000.0) -> float:
    """McCain/Mathews--Russell brine-viscosity screen (physics-reference.md §2.6).

    The source equation is implemented in its physically consistent ``A*T**B``
    form; the rendered implementation reference has a sign ambiguity in ``B``.
    """

    require_positive("pressure_psi", pressure_psi)
    temp_f = _temp_f(temp_r)
    if temp_f <= 0.0:
        raise ValueError(f"temperature must be above 0 degF for water viscosity, got {temp_f}")
    if salinity_ppm < 0.0:
        raise ValueError(f"salinity_ppm must be non-negative, got {salinity_ppm}")
    s = salinity_ppm / 10000.0
    a = 109.574 - 8.40564 * s + 0.313314 * s**2 + 8.72213e-5 * s**3
    b = -1.12166 + 2.63951e-2 * s - 6.79461e-4 * s**2 - 5.47119e-5 * s**3 + 1.55586e-6 * s**4
    mu_1atm = a * temp_f**b
    return mu_1atm * (0.9994 + 4.0295e-5 * pressure_psi + 3.1062e-9 * pressure_psi**2)


def lee_gonzalez_gas_viscosity_cp(
    pressure_psi: float, temp_r: float, gas_sg: float, z_factor: float | None = None
) -> float:
    """Lee--Gonzalez--Eakin gas viscosity using gas density in g/cm3."""

    require_positive("pressure_psi", pressure_psi)
    require_absolute_temperature(temp_r)
    z = dak_z_factor(pressure_psi, temp_r, gas_sg) if z_factor is None else z_factor
    require_positive("z_factor", z)
    molecular_weight = 28.97 * gas_sg
    density_lb_ft3 = 2.699 * gas_sg * pressure_psi / (z * temp_r)
    density_g_cm3 = density_lb_ft3 * 0.016018463
    k = (9.379 + 0.01607 * molecular_weight) * temp_r**1.5 / (209.2 + 19.26 * molecular_weight + temp_r)
    x = 3.448 + 986.4 / temp_r + 0.01009 * molecular_weight
    y = 2.447 - 0.2224 * x
    return 1e-4 * k * math.exp(x * density_g_cm3**y)


def _selected_bubble_point(spec: FluidSpec, temp_r: float, cfg: CorrelationConfig) -> tuple[float, str]:
    if spec.bubble_point_psi is not None:
        require_positive("bubble_point_psi", spec.bubble_point_psi)
        return spec.bubble_point_psi, "measured_override"
    if spec.gor_scf_stb == 0.0:
        return 0.0, "zero_solution_gas"
    if cfg.bubble_point is BubblePointCorrelation.STANDING:
        return standing_bubble_point_psi(spec.gor_scf_stb, spec.gas_sg, spec.oil_api, temp_r), "standing"
    return vazquez_beggs_bubble_point_psi(spec.gor_scf_stb, spec.gas_sg, spec.oil_api, temp_r), "vazquez_beggs"


def solve_fluid_state(spec: FluidSpec, pressure_psi: float, temp_f: float, cfg: CorrelationConfig) -> FluidState:
    """Solve a coherent black-oil state using the explicit Config correlation choices."""

    require_positive("pressure_psi", pressure_psi)
    require_positive("gas_sg", spec.gas_sg)
    require_positive("water_sg", spec.water_sg)
    if spec.gor_scf_stb < 0.0:
        raise ValueError(f"gor_scf_stb must be non-negative, got {spec.gor_scf_stb}")
    if spec.salinity_ppm < 0.0:
        raise ValueError(f"salinity_ppm must be non-negative, got {spec.salinity_ppm}")
    require_fraction("water_cut_frac", spec.water_cut_frac)
    oil_sg = api_to_sg(spec.oil_api)
    temp_r = f_to_r(temp_f)
    bubble_point_psi, bubble_name = _selected_bubble_point(spec, temp_r, cfg)

    if cfg.rs is RsCorrelation.STANDING:
        rs = standing_rs_scf_stb(pressure_psi, spec.gas_sg, spec.oil_api, temp_r, spec.gor_scf_stb, bubble_point_psi)
        rs_name = "standing"
    else:
        rs = vazquez_beggs_rs_scf_stb(pressure_psi, spec.gas_sg, spec.oil_api, temp_r, spec.gor_scf_stb, bubble_point_psi)
        rs_name = "vazquez_beggs"

    if cfg.bo is BoCorrelation.STANDING:
        bo = standing_bo_rb_stb(rs, spec.gas_sg, spec.oil_api, temp_r)
        bo_name = "standing"
    else:
        bo = vazquez_beggs_bo_rb_stb(rs, spec.gas_sg, spec.oil_api, temp_r)
        bo_name = "vazquez_beggs"

    dead_mu = beggs_robinson_dead_oil_viscosity_cp(spec.oil_api, temp_r)
    oil_mu = beggs_robinson_live_oil_viscosity_cp(dead_mu, rs, pressure_psi, bubble_point_psi)
    if cfg.z_factor is ZFactorCorrelation.DRANCHUK_ABOU_KASSEM:
        z = dak_z_factor(pressure_psi, temp_r, spec.gas_sg)
        z_name = "dranchuk_abou_kassem"
    else:
        z = hall_yarborough_z_factor(pressure_psi, temp_r, spec.gas_sg)
        z_name = "hall_yarborough"
    bg = 0.00504 * z * temp_r / pressure_psi
    gas_density = 2.699 * spec.gas_sg * pressure_psi / (z * temp_r)
    gas_mu = lee_gonzalez_gas_viscosity_cp(pressure_psi, temp_r, spec.gas_sg, z)
    bw = mccain_bw_rb_stb(pressure_psi, temp_r)

    # The salinity correlation supplies standard-condition density.  ``water_sg``
    # is a measured/specification value, so rescale only when it differs from the
    # salinity-implied SG; this honors both contract inputs without double-counting.
    salinity_rho_sc = 62.368 + 0.438603 * (spec.salinity_ppm / 10000.0) + 1.60074e-3 * (spec.salinity_ppm / 10000.0) ** 2
    salinity_sg = salinity_rho_sc / WATER_DENSITY_LB_FT3
    water_density = water_density_lb_ft3(pressure_psi, temp_r, spec.salinity_ppm) * (spec.water_sg / salinity_sg)
    water_mu = water_viscosity_cp(pressure_psi, temp_r, spec.salinity_ppm)
    oil_density = (WATER_DENSITY_LB_FT3 * oil_sg + 0.0136 * rs * spec.gas_sg) / bo

    return FluidState(
        pressure_psi=pressure_psi,
        temp_f=temp_f,
        rs_scf_stb=rs,
        bo_rb_stb=bo,
        oil_density_lb_ft3=oil_density,
        oil_viscosity_cp=oil_mu,
        oil_sg=oil_sg,
        bubble_point_psi=bubble_point_psi,
        z_factor=z,
        bg_ft3_scf=bg,
        gas_density_lb_ft3=gas_density,
        gas_viscosity_cp=gas_mu,
        free_gas_scf_stb=max(0.0, spec.gor_scf_stb - rs),
        bw_rb_stb=bw,
        water_density_lb_ft3=water_density,
        water_viscosity_cp=water_mu,
        correlations_used={
            "bubble_point": bubble_name,
            "rs": rs_name,
            "bo": bo_name,
            "oil_viscosity": "beggs_robinson_with_documented_undersaturated_extension",
            "z_factor": z_name,
            "water_fvf": "mccain",
            "water_viscosity": "mccain_mathews_russell",
            "gas_viscosity": "lee_gonzalez_eakin",
            "separator_gas_gravity": "gamma_gs_equals_gamma_g_separator_conditions_unavailable",
        },
    )
