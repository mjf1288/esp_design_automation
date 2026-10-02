"""Pump-curve evaluation, affinity scaling, zones, and stage sizing.

Implements physics-reference.md §5 (especially §§5.1–5.4) for framework §6
candidate selection and trajectory scoring.  Curve evaluation deliberately
rejects polynomial extrapolation outside its vendor-fitted point domain.
"""

from __future__ import annotations

from math import ceil
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from .catalog import PumpModel
from .config import (
    DesignThresholds,
    FrequencyCurveMethod,
    ThrustZoneMethod,
    ViscosityCorrectionMethod,
)
from .models import OperatingZone
from .units import require_fraction, require_positive


class CurvePoint(BaseModel):
    """Evaluated per-stage performance at an admissible operating flow."""

    model_config = ConfigDict(frozen=True)

    q_bpd: float
    head_ft_per_stage: float
    efficiency_frac: float
    bhp_per_stage: float


class ScaledCurve(BaseModel):
    """Analytically frequency-scaled polynomial curve and its valid flow domain."""

    model_config = ConfigDict(frozen=True)

    pump_id: str
    reference_frequency_hz: float
    frequency_hz: float
    frequency_ratio: float
    head_coeffs: tuple[float, ...]
    eff_coeffs: tuple[float, ...]
    bhp_coeffs: tuple[float, ...]
    q_min_bpd: float
    q_max_bpd: float
    bep_q_bpd: float
    recommended_min_bpd: float
    recommended_max_bpd: float
    downthrust_limit_bpd: float | None = None
    upthrust_limit_bpd: float | None = None


class ZoneClassification(BaseModel):
    """Categorical zone plus a continuous scoreable displacement from BEP."""

    model_config = ConfigDict(frozen=True)

    zone: OperatingZone
    q_over_qbep: float
    distance_from_bep_frac: float
    severity: float
    rationale: str


def _scale_polynomial(coefficients: Sequence[float], ratio: float, output_power: int) -> tuple[float, ...]:
    """Transform ``P(q)`` to ``ratio**output_power * P(q / ratio)`` exactly."""
    degree = len(coefficients) - 1
    return tuple(
        coefficient * ratio ** (output_power - (degree - index))
        for index, coefficient in enumerate(coefficients)
    )


def _prebuilt_curve_for_frequency(pump: PumpModel, frequency_hz: float) -> ScaledCurve | None:
    """Read an optional future catalog extension without pretending it is standard."""
    if pump.prebuilt_curves is None:
        return None
    record = pump.prebuilt_curves.get(str(frequency_hz))
    if not isinstance(record, dict):
        return None
    required = {"head_coeffs", "eff_coeffs", "bhp_coeffs"}
    if not required.issubset(record):
        raise ValueError(
            f"pump {pump.id} prebuilt curve at {frequency_hz:g} Hz lacks {sorted(required)}"
        )
    ratio = frequency_hz / pump.frequency_ref_hz
    point_flows = [point.q_bpd * ratio for point in pump.curve.points]
    return ScaledCurve(
        pump_id=pump.id,
        reference_frequency_hz=pump.frequency_ref_hz,
        frequency_hz=frequency_hz,
        frequency_ratio=ratio,
        head_coeffs=tuple(record["head_coeffs"]),
        eff_coeffs=tuple(record["eff_coeffs"]),
        bhp_coeffs=tuple(record["bhp_coeffs"]),
        q_min_bpd=float(record.get("q_min_bpd", min(point_flows))),
        q_max_bpd=float(record.get("q_max_bpd", max(point_flows))),
        bep_q_bpd=float(record.get("bep_q_bpd", pump.bep_flow_bpd * ratio)),
        recommended_min_bpd=pump.recommended_range_bpd[0] * ratio,
        recommended_max_bpd=pump.recommended_range_bpd[1] * ratio,
        downthrust_limit_bpd=(None if pump.downthrust_limit_bpd is None else pump.downthrust_limit_bpd * ratio),
        upthrust_limit_bpd=(None if pump.upthrust_limit_bpd is None else pump.upthrust_limit_bpd * ratio),
    )


def scale_curve_to_frequency(
    pump: PumpModel, frequency_hz: float, method: FrequencyCurveMethod
) -> ScaledCurve:
    """Scale curve *coefficients* by the affinity laws, without resampling.

    For a polynomial term ``a q**p``, the scaled coefficient is
    ``a * (f/f_ref)**(y-p)``, where ``y`` is 2 for head, 3 for BHP, and 0 for
    efficiency.  Consequently evaluating the scaled curve at ``q * f/f_ref``
    is algebraically identical to scaling the reference output.
    """
    require_positive("frequency_hz", frequency_hz)
    ratio = frequency_hz / pump.frequency_ref_hz
    if method is FrequencyCurveMethod.PREBUILT_CURVES:
        prebuilt = _prebuilt_curve_for_frequency(pump, frequency_hz)
        if prebuilt is not None:
            return prebuilt
        if frequency_hz != pump.frequency_ref_hz:
            raise ValueError(
                f"pump {pump.id} has no vendor prebuilt curve at {frequency_hz:g} Hz; "
                "use FrequencyCurveMethod.AFFINITY_LAWS or add validated prebuilt data"
            )
    if method not in {FrequencyCurveMethod.AFFINITY_LAWS, FrequencyCurveMethod.PREBUILT_CURVES}:
        raise ValueError(f"unsupported frequency curve method {method!r}")

    q_values = [point.q_bpd for point in pump.curve.points]
    if not q_values:
        raise ValueError(f"pump {pump.id} has no reference curve points")
    return ScaledCurve(
        pump_id=pump.id,
        reference_frequency_hz=pump.frequency_ref_hz,
        frequency_hz=frequency_hz,
        frequency_ratio=ratio,
        head_coeffs=_scale_polynomial(pump.curve_fit.head_coeffs, ratio, 2),
        eff_coeffs=_scale_polynomial(pump.curve_fit.eff_coeffs, ratio, 0),
        bhp_coeffs=_scale_polynomial(pump.curve_fit.bhp_coeffs, ratio, 3),
        q_min_bpd=min(q_values) * ratio,
        q_max_bpd=max(q_values) * ratio,
        bep_q_bpd=pump.bep_flow_bpd * ratio,
        recommended_min_bpd=pump.recommended_range_bpd[0] * ratio,
        recommended_max_bpd=pump.recommended_range_bpd[1] * ratio,
        downthrust_limit_bpd=(None if pump.downthrust_limit_bpd is None else pump.downthrust_limit_bpd * ratio),
        upthrust_limit_bpd=(None if pump.upthrust_limit_bpd is None else pump.upthrust_limit_bpd * ratio),
    )


def _polyval(coefficients: Sequence[float], value: float) -> float:
    result = 0.0
    for coefficient in coefficients:
        result = result * value + coefficient
    return result


def evaluate_stage(curve: ScaledCurve, q_bpd: float) -> CurvePoint:
    """Evaluate within the fitted domain; extrapolation is a hard error."""
    if q_bpd < 0:
        raise ValueError(f"q_bpd must be nonnegative, got {q_bpd}")
    if q_bpd < curve.q_min_bpd or q_bpd > curve.q_max_bpd:
        side = "left" if q_bpd < curve.q_min_bpd else "right"
        raise ValueError(
            f"q_bpd {q_bpd:g} is off the {side} of pump {curve.pump_id}'s fitted curve "
            f"[{curve.q_min_bpd:g}, {curve.q_max_bpd:g}] bpd; polynomial extrapolation is prohibited"
        )
    head = _polyval(curve.head_coeffs, q_bpd)
    efficiency = _polyval(curve.eff_coeffs, q_bpd) / 100.0
    bhp = _polyval(curve.bhp_coeffs, q_bpd)
    if abs(head) < 1e-10: head = 0.0
    if abs(efficiency) < 1e-12: efficiency = 0.0
    if head < 0 or bhp <= 0 or not 0 <= efficiency <= 1:
        raise ValueError(
            f"pump {curve.pump_id} polynomial returned nonphysical values at {q_bpd:g} bpd "
            f"(head={head:g} ft/stage, efficiency={efficiency:g}, bhp={bhp:g}); "
            "catalog curve fit must be corrected rather than clamped"
        )
    return CurvePoint(
        q_bpd=q_bpd,
        head_ft_per_stage=head,
        efficiency_frac=efficiency,
        bhp_per_stage=bhp,
    )


def _zone_limits(
    curve: ScaledCurve, method: ThrustZoneMethod, thresholds: DesignThresholds
) -> tuple[float, float]:
    if method is ThrustZoneMethod.CATALOG_LIMITS:
        low = curve.downthrust_limit_bpd
        high = curve.upthrust_limit_bpd
        if low is None or high is None:
            raise ValueError(
                f"pump {curve.pump_id} lacks published thrust limits; "
                "use ThrustZoneMethod.BEP_FRACTION only as an explicit configured fallback"
            )
    elif method is ThrustZoneMethod.BEP_FRACTION:
        low = curve.bep_q_bpd * thresholds.downthrust_frac_of_bep
        high = curve.bep_q_bpd * thresholds.upthrust_frac_of_bep
    else:
        raise ValueError(f"unsupported thrust zone method {method!r}")
    if not 0 <= low < curve.bep_q_bpd < high:
        raise ValueError(
            f"invalid zone limits [{low:g}, {high:g}] for BEP {curve.bep_q_bpd:g}; "
            "limits must straddle BEP"
        )
    return low, high


def classify_zone(
    curve: ScaledCurve,
    q_bpd: float,
    method: ThrustZoneMethod,
    thresholds: DesignThresholds,
) -> ZoneClassification:
    """Classify flow with a continuous severity reaching one at operating limits.

    Severity uses the relevant published/configured thrust boundary—not category
    steps—so it remains continuous at BEP-band and thrust-zone label changes.
    It is saturated at one outside the operating range, including off-curve flow.
    """
    if q_bpd < 0:
        raise ValueError(f"q_bpd must be nonnegative, got {q_bpd}")
    low_limit, high_limit = _zone_limits(curve, method, thresholds)
    q_over = q_bpd / curve.bep_q_bpd
    distance = q_over - 1.0
    if q_bpd <= curve.bep_q_bpd:
        severity = (curve.bep_q_bpd - q_bpd) / (curve.bep_q_bpd - low_limit)
    else:
        severity = (q_bpd - curve.bep_q_bpd) / (high_limit - curve.bep_q_bpd)
    severity = min(1.0, max(0.0, severity))

    if q_bpd < curve.q_min_bpd:
        zone = OperatingZone.OFF_CURVE_LEFT
        rationale = f"{q_bpd:g} bpd is below fitted curve minimum {curve.q_min_bpd:g} bpd"
    elif q_bpd > curve.q_max_bpd:
        zone = OperatingZone.OFF_CURVE_RIGHT
        rationale = f"{q_bpd:g} bpd exceeds fitted curve maximum {curve.q_max_bpd:g} bpd"
    elif abs(distance) <= thresholds.bep_band_frac:
        zone = OperatingZone.BEP
        rationale = f"flow is within ±{thresholds.bep_band_frac:.0%} BEP band"
    elif q_bpd < low_limit:
        zone = OperatingZone.DOWNTHRUST
        rationale = f"flow is below downthrust boundary {low_limit:g} bpd"
    elif q_bpd > high_limit:
        zone = OperatingZone.UPTHRUST
        rationale = f"flow exceeds upthrust boundary {high_limit:g} bpd"
    else:
        zone = OperatingZone.OPERATING_RANGE
        rationale = f"flow is within configured operating limits {low_limit:g}–{high_limit:g} bpd"
    return ZoneClassification(
        zone=zone,
        q_over_qbep=q_over,
        distance_from_bep_frac=distance,
        severity=severity,
        rationale=rationale,
    )


def apply_viscosity_correction(
    point: CurvePoint,
    *,
    q_bpd: float,
    viscosity_cp: float,
    head_ft_per_stage: float,
    bep_q_bpd: float,
    method: ViscosityCorrectionMethod,
) -> CurvePoint:
    """Apply a licensed ANSI/HI correction when available; water is identity.

    The supplied reference explicitly says the complete ANSI/HI 9.6.7 equations
    require a license and must not be reconstructed from public summaries.  This
    engine therefore returns the identity for water and refuses to present an
    undocumented heavy-liquid approximation as an ANSI result.
    """
    if q_bpd < 0:
        raise ValueError(f"q_bpd must be nonnegative, got {q_bpd}")
    require_positive("viscosity_cp", viscosity_cp)
    require_positive("head_ft_per_stage", head_ft_per_stage)
    require_positive("bep_q_bpd", bep_q_bpd)
    if method is ViscosityCorrectionMethod.NONE:
        return point
    if method is not ViscosityCorrectionMethod.ANSI_HI_9_6_7:
        raise ValueError(f"unsupported viscosity correction method {method!r}")
    if viscosity_cp <= 1.1:
        return point
    raise ValueError(
        "ANSI/HI 9.6.7 viscosity correction for viscosity > 1.1 cP requires the "
        "licensed standard or a validated vendor viscous-performance curve; no "
        "undocumented correction factor is applied"
    )


def stages_required(
    tdh_ft: float, head_ft_per_stage: float, gas_degradation_factor: float = 1.0
) -> int:
    """Return ceiling stage count after the approved gas head degradation factor."""
    require_positive("tdh_ft", tdh_ft)
    require_positive("head_ft_per_stage", head_ft_per_stage)
    require_fraction("gas_degradation_factor", gas_degradation_factor)
    if gas_degradation_factor == 0:
        raise ValueError("gas_degradation_factor of zero supplies no usable head")
    return max(1, ceil(tdh_ft / (head_ft_per_stage * gas_degradation_factor)))


def total_head_ft(
    curve: ScaledCurve, q_bpd: float, stages: int, degradation_factor: float = 1.0
) -> float:
    if stages < 1:
        raise ValueError(f"stages must be at least 1, got {stages}")
    require_fraction("degradation_factor", degradation_factor)
    return evaluate_stage(curve, q_bpd).head_ft_per_stage * stages * degradation_factor


def total_bhp(
    curve: ScaledCurve,
    q_bpd: float,
    stages: int,
    mixture_sg: float,
    degradation_factor: float = 1.0,
) -> float:
    """Curve BHP times stages and liquid SG; gas derate is explicit for audit."""
    if stages < 1:
        raise ValueError(f"stages must be at least 1, got {stages}")
    require_positive("mixture_sg", mixture_sg)
    require_fraction("degradation_factor", degradation_factor)
    return evaluate_stage(curve, q_bpd).bhp_per_stage * stages * mixture_sg * degradation_factor


# =============================================================================
# Domain-safe evaluation (added by the orchestration layer)
# =============================================================================


def curve_domain(curve: ScaledCurve) -> tuple[float, float]:
    """The rate interval over which this curve may be evaluated at all."""
    return (curve.q_min_bpd, curve.q_max_bpd)


def try_evaluate_stage(curve: ScaledCurve, q_bpd: float) -> CurvePoint | None:
    """``evaluate_stage`` that returns ``None`` instead of raising off-domain.

    ``evaluate_stage`` prohibits extrapolation, which is correct: a degree-5
    polynomial fitted to a pump curve produces confident nonsense outside its
    domain. But over a 24-month trajectory the operating rate legitimately drifts
    off the curve, and that is a *result* the engineer needs to see ("this pump
    runs out of curve at month 14"), not an exception that aborts the run.

    So the prohibition stays and the caller decides. Callers that get ``None``
    must record an off-curve outcome rather than substituting a guess.
    """
    lo, hi = curve_domain(curve)
    if q_bpd < 0 or q_bpd < lo or q_bpd > hi:
        return None
    return evaluate_stage(curve, q_bpd)


def rate_within_domain(curve: ScaledCurve, q_bpd: float) -> bool:
    lo, hi = curve_domain(curve)
    return lo <= q_bpd <= hi
