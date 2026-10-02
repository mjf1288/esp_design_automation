"""Pump affinity and curve-domain tests; reference: physics-reference.md §5.2."""
from __future__ import annotations

import math

import pytest

from esp_engine.catalog import load_catalog
from esp_engine.config import DesignThresholds, FrequencyCurveMethod, ThrustZoneMethod, ViscosityCorrectionMethod
from esp_engine.curves import apply_viscosity_correction, classify_zone, evaluate_stage, scale_curve_to_frequency, stages_required, total_head_ft
from esp_engine.models import OperatingZone


def test_affinity_coefficients_are_exact_at_corresponding_flow(catalog):
    pump = catalog.pump("fixture-pump")
    reference = scale_curve_to_frequency(pump, 60.0, FrequencyCurveMethod.AFFINITY_LAWS)
    scaled = scale_curve_to_frequency(pump, 50.0, FrequencyCurveMethod.AFFINITY_LAWS)
    ratio = 50.0 / 60.0
    base = evaluate_stage(reference, 500.0)
    moved = evaluate_stage(scaled, 500.0 * ratio)
    assert math.isclose(moved.head_ft_per_stage, base.head_ft_per_stage * ratio**2, rel_tol=1e-12)
    assert math.isclose(moved.bhp_per_stage, base.bhp_per_stage * ratio**3, rel_tol=1e-12)
    assert math.isclose(moved.efficiency_frac, base.efficiency_frac, rel_tol=1e-12)
    assert math.isclose(moved.head_ft_per_stage / base.head_ft_per_stage, 25.0 / 36.0, rel_tol=1e-12)


def test_zone_severity_is_continuous_monotonic_and_off_curve(catalog):
    curve = scale_curve_to_frequency(catalog.pump("fixture-pump"), 60, FrequencyCurveMethod.AFFINITY_LAWS)
    thresholds = DesignThresholds(bep_band_frac=0.1)
    left = [classify_zone(curve, q, ThrustZoneMethod.CATALOG_LIMITS, thresholds) for q in (500, 450, 400, 350, 300, 0)]
    right = [classify_zone(curve, q, ThrustZoneMethod.CATALOG_LIMITS, thresholds) for q in (500, 550, 600, 650, 700, 1000)]
    assert left[0].severity == 0.0 and right[0].severity == 0.0
    assert all(a.severity <= b.severity for a, b in zip(left, left[1:]))
    assert all(a.severity <= b.severity for a, b in zip(right, right[1:]))
    assert classify_zone(curve, 300 - 1e-9, ThrustZoneMethod.CATALOG_LIMITS, thresholds).severity == 1.0
    assert classify_zone(curve, 700 + 1e-9, ThrustZoneMethod.CATALOG_LIMITS, thresholds).severity == 1.0
    assert classify_zone(curve, 1100, ThrustZoneMethod.CATALOG_LIMITS, thresholds).zone is OperatingZone.OFF_CURVE_RIGHT
    assert classify_zone(curve, 0, ThrustZoneMethod.CATALOG_LIMITS, thresholds).zone is OperatingZone.DOWNTHRUST
    assert classify_zone(curve, 1100, ThrustZoneMethod.CATALOG_LIMITS, thresholds).severity == 1.0
    assert classify_zone(curve, 0, ThrustZoneMethod.BEP_FRACTION, thresholds).distance_from_bep_frac == -1.0


def test_curve_rejects_extrapolation_and_stages_account_for_gas(catalog):
    curve = scale_curve_to_frequency(catalog.pump("fixture-pump"), 60, FrequencyCurveMethod.AFFINITY_LAWS)
    assert evaluate_stage(curve, 0).head_ft_per_stage == 100.0
    with pytest.raises(ValueError, match="extrapolation"):
        evaluate_stage(curve, 1000.01)
    assert stages_required(181.0, 90.0) == 3
    assert stages_required(181.0, 90.0, 0.8) == 3
    assert stages_required(181.0, 90.0, 0.5) == 5
    assert total_head_ft(curve, 500, 1) == 90.0
    with pytest.raises(ValueError):
        total_head_ft(curve, 500, 0)


def test_real_catalog_heads_decrease_across_each_fitted_domain():
    real = load_catalog()
    for pump in real.pumps:
        curve = scale_curve_to_frequency(pump, pump.frequency_ref_hz, FrequencyCurveMethod.AFFINITY_LAWS)
        q_values = sorted(point.q_bpd for point in pump.curve.points)
        heads = [evaluate_stage(curve, q).head_ft_per_stage for q in q_values]
        assert all(a >= b for a, b in zip(heads, heads[1:])), pump.id


def test_unlicensed_viscosity_correction_does_not_claim_ansi(catalog):
    curve = scale_curve_to_frequency(catalog.pump("fixture-pump"), 60, FrequencyCurveMethod.AFFINITY_LAWS)
    point = evaluate_stage(curve, 500)
    assert apply_viscosity_correction(point, q_bpd=500, viscosity_cp=1.0, head_ft_per_stage=90, bep_q_bpd=500, method=ViscosityCorrectionMethod.ANSI_HI_9_6_7) == point
    with pytest.raises(ValueError, match="licensed"):
        apply_viscosity_correction(point, q_bpd=500, viscosity_cp=20, head_ft_per_stage=90, bep_q_bpd=500, method=ViscosityCorrectionMethod.ANSI_HI_9_6_7)
