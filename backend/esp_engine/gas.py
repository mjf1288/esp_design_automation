"""Gas-risk screening implementing physics-reference.md §3 for the ESP framework
complication and gas-handling decision stage (§3.3).

Natural-separation credit is deliberately zero for ``NONE`` and the generic
vendor-rule selection: a vendor map/credible vent path is absent from the
binding interface.  The selectable Alhanati screening option uses the published
structure with stated air/water reference-fluid assumptions.
"""

from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, ConfigDict

from .config import CorrelationConfig, GasThresholds, NaturalSeparationModel
from .intake import IntakeConditions
from .units import annulus_area_ft2, bpd_to_ft3_per_day, require_fraction, require_positive


class GasStrategy(str, Enum):
    NONE = "none"
    STATIC_SEPARATOR = "static_separator"
    ROTARY_SEPARATOR = "rotary_separator"
    GAS_HANDLER = "gas_handler"
    SEPARATOR_PLUS_HANDLER = "separator_plus_handler"
    ADVANCED_GAS_HANDLER = "advanced_gas_handler"
    NOT_ESP_CANDIDATE = "not_esp_candidate"


class GasAssessment(BaseModel):
    """Gas fractions before any proposed device and auditable screening decision."""

    model_config = ConfigDict(frozen=True)

    fgvf_at_intake: float
    natural_separation_efficiency: float
    fgvf_after_natural_separation: float
    device_separation_efficiency: float
    fgvf_entering_pump: float
    turpin_parameter: float
    turpin_stable: bool
    recommended_strategy: GasStrategy
    head_degradation_factor: float
    kq: float | None = None
    kh: float | None = None
    correction_source: str = "expert_table_indicative"
    correction_missing: bool = False
    rationale: str
    warnings: list[str]


def natural_separation_efficiency(
    *, liquid_rate_bpd: float, gas_rate_bpd: float, casing_id_in: float, equipment_od_in: float, model: NaturalSeparationModel
) -> float:
    """Natural annular separation efficiency (physics-reference.md §3.3).

    The Alhanati option calculates the published drift-flux form using a stated
    air/water reference set (sigma=0.072 N/m, rho_l=998 kg/m3, rho_g=1.2 kg/m3)
    because the binding interface has no PVT/surface-tension inputs.  It is a
    screening surrogate, not a completion-specific vendor prediction.
    """

    if liquid_rate_bpd < 0.0 or gas_rate_bpd < 0.0:
        raise ValueError("liquid_rate_bpd and gas_rate_bpd must be non-negative")
    area_ft2 = annulus_area_ft2(casing_id_in, equipment_od_in)
    if model in {NaturalSeparationModel.NONE, NaturalSeparationModel.VENDOR_RULE_OF_THUMB}:
        return 0.0
    if liquid_rate_bpd == 0.0:
        return 0.0
    jsl_ft_s = bpd_to_ft3_per_day(liquid_rate_bpd) / 86400.0 / area_ft2
    jsg_ft_s = bpd_to_ft3_per_day(gas_rate_bpd) / 86400.0 / area_ft2
    alpha = jsg_ft_s / (jsl_ft_s + jsg_ft_s) if jsl_ft_s + jsg_ft_s > 0.0 else 0.0
    # Alhanati §3.3, evaluated in SI then converted to ft/s.  n=0 corresponds to
    # the cited slug/churn screening regime, so (1-alpha)^n = 1.
    v_inf_m_s = 1.414 * ((0.072 * (998.0 - 1.2) * 9.80665) / 998.0**2) ** 0.25
    v_inf_ft_s = v_inf_m_s * 3.280839895
    n = 0.0
    numerator = v_inf_ft_s * (1.0 - alpha) ** n
    return max(0.0, min(1.0, numerator / (numerator + jsl_ft_s)))


def turpin_parameter(*, free_gas_rate_bpd: float, liquid_rate_bpd: float, pip_psi: float) -> float:
    """Turpin stability parameter phi (physics-reference.md §3.2)."""

    if free_gas_rate_bpd < 0.0:
        raise ValueError(f"free_gas_rate_bpd must be non-negative, got {free_gas_rate_bpd}")
    require_positive("liquid_rate_bpd", liquid_rate_bpd)
    require_positive("pip_psi", pip_psi)
    return 2000.0 / pip_psi**3 * (free_gas_rate_bpd / liquid_rate_bpd)


def head_degradation_factor(fgvf: float) -> float:
    """Expert-signed Kh; no invented endpoint beyond the supplied table."""
    _, kh = gas_corrections(fgvf)
    if kh is None:
        raise ValueError("Kq/Kh require an engineer override above 25% intake GVF; the 30%+ row gives inequalities, not coefficients.")
    return kh


def gas_corrections(beta: float) -> tuple[float | None, float | None]:
    require_fraction("beta", beta)
    table = [(0.0, 1., 1.), (.10, .90, .95), (.20, .75, .85), (.25, .60, .75)]
    if beta == 0:
        return 1.0, 1.0
    for (a, qa, ha), (b, qb, hb) in zip(table, table[1:]):
        if a <= beta <= b:
            t = (beta - a) / (b - a)
            return qa + t * (qb - qa), ha + t * (hb - ha)
    return None, None


def _strategy(fgvf: float, turpin_stable: bool, has_vsd: bool, thresholds: GasThresholds) -> GasStrategy:
    if fgvf <= thresholds.standard_pump_max_fgvf:
        return GasStrategy.NONE
    if fgvf <= thresholds.separator_recommended_fgvf:
        return GasStrategy.GAS_HANDLER if has_vsd else GasStrategy.STATIC_SEPARATOR
    if fgvf <= thresholds.gas_handler_max_fgvf:
        return GasStrategy.ROTARY_SEPARATOR if turpin_stable else GasStrategy.SEPARATOR_PLUS_HANDLER
    if fgvf <= thresholds.advanced_handling_max_fgvf:
        return GasStrategy.ADVANCED_GAS_HANDLER
    return GasStrategy.NOT_ESP_CANDIDATE


def assess_gas(
    intake: IntakeConditions,
    *,
    casing_id_in: float,
    equipment_od_in: float,
    has_vsd: bool,
    cfg: CorrelationConfig,
    thresholds: GasThresholds,
    kq_override: float | None = None,
    kh_override: float | None = None,
    stage_type: str = "radial",
) -> GasAssessment:
    """Assess free-gas risk and choose a screening strategy with numeric rationale."""

    require_positive("casing_id_in", casing_id_in)
    require_positive("equipment_od_in", equipment_od_in)
    require_fraction("intake.free_gas_fraction", intake.free_gas_fraction)
    if intake.total_liquid_intake_bpd <= 0.0:
        raise ValueError("intake.total_liquid_intake_bpd must be positive")
    e_nat = natural_separation_efficiency(
        liquid_rate_bpd=intake.total_liquid_intake_bpd,
        gas_rate_bpd=intake.free_gas_rate_intake_bpd,
        casing_id_in=casing_id_in,
        equipment_od_in=equipment_od_in,
        model=cfg.natural_separation,
    )
    gas_after = intake.free_gas_rate_intake_bpd * (1.0 - e_nat)
    after_nat = gas_after / (gas_after + intake.total_liquid_intake_bpd)
    # The API recommends a device but does not claim a vendor performance map is
    # installed.  Zero device credit is conservative and makes that distinction
    # explicit in output rather than assuming an undocumented 75--90% efficiency.
    e_device = 0.0
    entering = after_nat
    phi = turpin_parameter(
        free_gas_rate_bpd=gas_after,
        liquid_rate_bpd=intake.total_liquid_intake_bpd,
        pip_psi=intake.pip_psi,
    )
    stable = phi < thresholds.turpin_stability_limit
    strategy = _strategy(entering, stable, has_vsd, thresholds)
    rationale = (
        f"FGVF entering pump {entering:.3f}; standard-pump limit {thresholds.standard_pump_max_fgvf:.3f} "
        f"is {'exceeded' if entering > thresholds.standard_pump_max_fgvf else 'not exceeded'}; "
        f"rotary-separator recommendation threshold {thresholds.separator_recommended_fgvf:.3f} "
        f"is {'exceeded' if entering > thresholds.separator_recommended_fgvf else 'not exceeded'}; "
        f"gas-handler maximum {thresholds.gas_handler_max_fgvf:.3f} "
        f"is {'exceeded' if entering > thresholds.gas_handler_max_fgvf else 'not exceeded'}; "
        f"advanced-handling maximum {thresholds.advanced_handling_max_fgvf:.3f} "
        f"is {'exceeded' if entering > thresholds.advanced_handling_max_fgvf else 'not exceeded'}; "
        f"Turpin parameter {phi:.3g} versus stability limit {thresholds.turpin_stability_limit:.3g} "
        f"is {'unstable' if not stable else 'stable'} -> {strategy.value}."
    )
    warnings: list[str] = []
    if cfg.natural_separation is NaturalSeparationModel.VENDOR_RULE_OF_THUMB:
        warnings.append("No vendor natural-separation map/vent-path data were supplied; natural separation credit is conservatively zero.")
    if cfg.natural_separation is NaturalSeparationModel.ALHANATI:
        warnings.append("Alhanati result uses air/water reference-fluid assumptions because the interface lacks surface tension and phase-density inputs.")
    if not stable:
        warnings.append("Turpin stability parameter exceeds the configured limit; the cited experimental correlation is not a vendor pump-map substitute.")
    if strategy is GasStrategy.NOT_ESP_CANDIDATE:
        warnings.append("Residual FGVF exceeds the configured advanced-handling maximum; increase PIP, alter completion, reduce drawdown, or evaluate another lift method.")
    kq, kh = gas_corrections(intake.free_gas_fraction)
    corrected = kq_override is not None or kh_override is not None
    if intake.free_gas_fraction > 0:
        kq = kq_override if kq_override is not None else kq
        kh = kh_override if kh_override is not None else kh
    elif corrected:
        warnings.append("Zero free gas requires Kq=Kh=1 exactly; non-identity gas overrides have no effect at zero gas.")
    missing = kq is None or kh is None
    if missing:
        warnings.append("Gas table has no numeric endpoint above 25% GVF. Supply engineer-corrected Kq/Kh; gas-adjusted performance is uncomputed, not extrapolated.")
    threshold = .25 if "mixed" in stage_type.lower() else .10
    if intake.free_gas_fraction >= threshold:
        warnings.append(f"Gas separator recommended: intake GVF {intake.free_gas_fraction:.1%}, {stage_type} stage warning threshold {threshold:.1%}; stage type changes advice, not Kq/Kh.")
    return GasAssessment(
        fgvf_at_intake=intake.free_gas_fraction,
        natural_separation_efficiency=e_nat,
        fgvf_after_natural_separation=after_nat,
        device_separation_efficiency=e_device,
        fgvf_entering_pump=entering,
        turpin_parameter=phi,
        turpin_stable=stable,
        recommended_strategy=strategy,
        head_degradation_factor=kh if kh is not None else 0.0,
        kq=kq, kh=kh, correction_missing=missing,
        correction_source="engineer_corrected" if corrected else "expert_table_indicative",
        rationale=rationale,
        warnings=warnings,
    )
