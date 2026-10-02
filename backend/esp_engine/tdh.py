"""Total-dynamic-head calculation implementing physics-reference.md §4 for the
ESP framework's hydraulic sizing stage (recommended solution order step 6)."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict

from .config import FrictionMethod
from .units import PSI_PER_FT_WATER, bpd_to_ft3_per_day, pipe_area_ft2, require_positive

# The binding interface has no Hazen--Williams C parameter.  This documented
# new-steel screening default is used only when that explicitly selected legacy
# water-only method is requested; Darcy--Weisbach remains the default.
_HAZEN_WILLIAMS_C = 120.0
_G_FT_S2 = 32.174
_CP_TO_LBM_FT_S = 0.000671968975


class TDHBreakdown(BaseModel):
    """Frozen hydraulic head result with friction diagnostics."""

    model_config = ConfigDict(frozen=True)

    net_lift_ft: float
    tubing_friction_ft: float
    wellhead_head_ft: float
    tdh_ft: float
    discharge_pressure_psi: float
    fluid_sg_used: float
    friction_method: FrictionMethod
    reynolds_number: float | None
    friction_factor: float | None
    velocity_ft_s: float


def _darcy_friction(*, rate_bpd: float, tubing_id_in: float, tubing_length_ft: float, mixture_sg: float, viscosity_cp: float, roughness_in: float) -> tuple[float, float, float, float]:
    area = pipe_area_ft2(tubing_id_in)
    diameter_ft = tubing_id_in / 12.0
    velocity = bpd_to_ft3_per_day(rate_bpd) / 86400.0 / area
    density = 62.4 * mixture_sg
    viscosity = viscosity_cp * _CP_TO_LBM_FT_S
    reynolds = density * velocity * diameter_ft / viscosity
    if reynolds <= 0.0:
        raise ValueError("computed Reynolds number must be positive")
    if reynolds < 2000.0:
        factor = 64.0 / reynolds
    else:
        roughness_ft = roughness_in / 12.0
        denominator = math.log10(roughness_ft / (3.7 * diameter_ft) + 5.74 / reynolds**0.9)
        factor = 0.25 / denominator**2
    head = factor * tubing_length_ft / diameter_ft * velocity**2 / (2.0 * _G_FT_S2)
    return head, reynolds, factor, velocity


def _hazen_williams_friction(*, rate_bpd: float, tubing_id_in: float, tubing_length_ft: float) -> tuple[float, float]:
    q_gpm = rate_bpd * 42.0 / 1440.0
    head = 4.52 * tubing_length_ft * q_gpm**1.85 / (_HAZEN_WILLIAMS_C**1.85 * tubing_id_in**4.8655)
    velocity = bpd_to_ft3_per_day(rate_bpd) / 86400.0 / pipe_area_ft2(tubing_id_in)
    return head, velocity


def compute_tdh(
    *,
    setting_depth_tvd_ft: float,
    pip_psi: float,
    wellhead_pressure_psi: float,
    total_liquid_rate_bpd: float,
    tubing_id_in: float,
    tubing_length_ft: float,
    mixture_sg: float,
    mixture_viscosity_cp: float,
    roughness_in: float = 0.0018,
    method: FrictionMethod = FrictionMethod.DARCY_WEISBACH,
) -> TDHBreakdown:
    """Compute TDH = net lift + tubing friction + wellhead backpressure head."""

    require_positive("setting_depth_tvd_ft", setting_depth_tvd_ft)
    require_positive("pip_psi", pip_psi)
    if wellhead_pressure_psi < 0.0:
        raise ValueError(f"wellhead_pressure_psi must be non-negative, got {wellhead_pressure_psi}")
    require_positive("total_liquid_rate_bpd", total_liquid_rate_bpd)
    require_positive("tubing_id_in", tubing_id_in)
    require_positive("tubing_length_ft", tubing_length_ft)
    require_positive("mixture_sg", mixture_sg)
    require_positive("mixture_viscosity_cp", mixture_viscosity_cp)
    if roughness_in < 0.0:
        raise ValueError(f"roughness_in must be non-negative, got {roughness_in}")

    if method is FrictionMethod.DARCY_WEISBACH:
        friction, reynolds, factor, velocity = _darcy_friction(
            rate_bpd=total_liquid_rate_bpd,
            tubing_id_in=tubing_id_in,
            tubing_length_ft=tubing_length_ft,
            mixture_sg=mixture_sg,
            viscosity_cp=mixture_viscosity_cp,
            roughness_in=roughness_in,
        )
    else:
        friction, velocity = _hazen_williams_friction(
            rate_bpd=total_liquid_rate_bpd,
            tubing_id_in=tubing_id_in,
            tubing_length_ft=tubing_length_ft,
        )
        reynolds, factor = None, None

    gradient = PSI_PER_FT_WATER * mixture_sg
    net_lift = setting_depth_tvd_ft - pip_psi / gradient
    wellhead_head = wellhead_pressure_psi / gradient
    tdh = net_lift + friction + wellhead_head
    # PIP + TDH*gradient is the required pump-discharge pressure at setting depth.
    discharge_pressure = pip_psi + tdh * gradient
    return TDHBreakdown(
        net_lift_ft=net_lift,
        tubing_friction_ft=friction,
        wellhead_head_ft=wellhead_head,
        tdh_ft=tdh,
        discharge_pressure_psi=discharge_pressure,
        fluid_sg_used=mixture_sg,
        friction_method=method,
        reynolds_number=reynolds,
        friction_factor=factor,
        velocity_ft_s=velocity,
    )
