"""Inflow-performance relationships implementing physics-reference.md §1 for the
ESP framework's well/inflow stage (§1 and recommended solution order step 2)."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict

from .config import IPRModel
from .units import require_positive


class InflowSpec(BaseModel):
    """Absolute-pressure IPR inputs.  Rates are oil stock-tank bpd."""

    model_config = ConfigDict(frozen=True)

    reservoir_pressure_psi: float
    productivity_index_bpd_psi: float | None
    bubble_point_psi: float
    test_rate_bpd: float | None = None
    test_pwf_psi: float | None = None
    model: IPRModel = IPRModel.COMPOSITE


def _validate_spec(spec: InflowSpec) -> float:
    require_positive("reservoir_pressure_psi", spec.reservoir_pressure_psi)
    if spec.bubble_point_psi < 0.0:
        raise ValueError(f"bubble_point_psi must be non-negative, got {spec.bubble_point_psi}")
    if spec.productivity_index_bpd_psi is not None:
        require_positive("productivity_index_bpd_psi", spec.productivity_index_bpd_psi)
    if (spec.test_rate_bpd is None) != (spec.test_pwf_psi is None):
        raise ValueError("test_rate_bpd and test_pwf_psi must be supplied together for IPR calibration")
    if spec.test_rate_bpd is not None:
        if spec.test_rate_bpd < 0.0:
            raise ValueError(f"test_rate_bpd must be non-negative, got {spec.test_rate_bpd}")
        if not 0.0 <= spec.test_pwf_psi <= spec.reservoir_pressure_psi:
            raise ValueError("test_pwf_psi must be between 0 and reservoir_pressure_psi")
    if spec.productivity_index_bpd_psi is not None:
        return spec.productivity_index_bpd_psi
    if spec.test_rate_bpd is None or spec.test_pwf_psi is None:
        raise ValueError("provide productivity_index_bpd_psi or both test_rate_bpd and test_pwf_psi")
    return productivity_index_from_test(
        spec.test_rate_bpd,
        spec.test_pwf_psi,
        spec.reservoir_pressure_psi,
        spec.bubble_point_psi,
        spec.model,
    )


def _vogel_fraction(pwf_psi: float, reservoir_pressure_psi: float) -> float:
    x = max(0.0, min(1.0, pwf_psi / reservoir_pressure_psi))
    return 1.0 - 0.2 * x - 0.8 * x * x


def productivity_index_from_test(
    rate_bpd: float,
    pwf_psi: float,
    reservoir_pressure_psi: float,
    bubble_point_psi: float,
    model: IPRModel,
) -> float:
    """Back-calculate PI from a test under the selected IPR convention.

    For a Vogel-only test, the returned PI is the conventional equivalent
    ``qmax/pR``; direct Vogel forward calculations retain the calibrated qmax.
    """

    if rate_bpd < 0.0:
        raise ValueError(f"rate_bpd must be non-negative, got {rate_bpd}")
    require_positive("reservoir_pressure_psi", reservoir_pressure_psi)
    if not 0.0 <= pwf_psi < reservoir_pressure_psi:
        raise ValueError("pwf_psi must be in [0, reservoir_pressure_psi) to calculate productivity index")
    if not 0.0 <= bubble_point_psi <= reservoir_pressure_psi:
        raise ValueError("bubble_point_psi must be in [0, reservoir_pressure_psi]")
    drawdown = reservoir_pressure_psi - pwf_psi
    if model is IPRModel.PI_LINEAR or pwf_psi >= bubble_point_psi:
        if drawdown <= 0.0:
            raise ValueError("test drawdown must be positive to calculate productivity index")
        return rate_bpd / drawdown
    test_fraction = _vogel_fraction(pwf_psi, reservoir_pressure_psi)
    if test_fraction <= 0.0:
        raise ValueError("Vogel test fraction is zero; cannot calculate productivity index")
    qmax = rate_bpd / test_fraction
    if model is IPRModel.VOGEL:
        return qmax / reservoir_pressure_psi
    pb_fraction = _vogel_fraction(bubble_point_psi, reservoir_pressure_psi)
    if bubble_point_psi >= reservoir_pressure_psi or pb_fraction <= 0.0:
        # Reservoir is already saturated: no above-bubble PI segment exists.
        return qmax / reservoir_pressure_psi
    return qmax * pb_fraction / (reservoir_pressure_psi - bubble_point_psi)


def _vogel_aof(spec: InflowSpec, pi: float) -> float:
    if spec.test_rate_bpd is not None and spec.test_pwf_psi is not None:
        frac = _vogel_fraction(spec.test_pwf_psi, spec.reservoir_pressure_psi)
        if frac <= 0.0:
            raise ValueError("test point has zero Vogel deliverability")
        return spec.test_rate_bpd / frac
    # With only PI, the only unambiguous stated convention is PI's q at pwf=0.
    return pi * spec.reservoir_pressure_psi


def absolute_open_flow_bpd(spec: InflowSpec) -> float:
    """The selected model's zero-Pwf deliverability (AOF-like theoretical rate)."""

    pi = _validate_spec(spec)
    pr, pb = spec.reservoir_pressure_psi, spec.bubble_point_psi
    if spec.model is IPRModel.PI_LINEAR:
        return pi * pr
    if spec.model is IPRModel.VOGEL:
        return _vogel_aof(spec, pi)
    if pb <= 0.0:
        return pi * pr
    if pb >= pr:
        # Fully saturated reservoir: the composite degenerates to Vogel.
        return _vogel_aof(spec, pi)
    qb = pi * (pr - pb)
    return qb / _vogel_fraction(pb, pr)


def rate_for_pwf(spec: InflowSpec, pwf_psi: float) -> float:
    """Evaluate the selected IPR at a physically valid flowing pressure."""

    pi = _validate_spec(spec)
    pr, pb = spec.reservoir_pressure_psi, spec.bubble_point_psi
    if not 0.0 <= pwf_psi <= pr:
        raise ValueError(f"pwf_psi must be between 0 and reservoir pressure ({pr} psi), got {pwf_psi}")
    if spec.model is IPRModel.PI_LINEAR:
        return pi * (pr - pwf_psi)
    if spec.model is IPRModel.VOGEL:
        return _vogel_aof(spec, pi) * _vogel_fraction(pwf_psi, pr)
    if pb < pr and pwf_psi >= pb:
        return pi * (pr - pwf_psi)
    return absolute_open_flow_bpd(spec) * _vogel_fraction(pwf_psi, pr)


def pwf_for_rate(spec: InflowSpec, rate_bpd: float) -> float:
    """Invert the IPR and clearly reject target rates exceeding absolute open flow."""

    _validate_spec(spec)
    if rate_bpd < 0.0:
        raise ValueError(f"rate_bpd must be non-negative, got {rate_bpd}")
    aof = absolute_open_flow_bpd(spec)
    if rate_bpd > aof + max(1e-9, aof * 1e-12):
        raise ValueError(
            f"target rate of {rate_bpd:g} bpd exceeds the well's absolute open flow of {aof:g} bpd; "
            "the expectation is not achievable regardless of pump selection"
        )
    pr, pb = spec.reservoir_pressure_psi, spec.bubble_point_psi
    if rate_bpd == 0.0:
        return pr
    if spec.model is IPRModel.PI_LINEAR:
        return max(0.0, pr - rate_bpd / _validate_spec(spec))
    if spec.model is IPRModel.COMPOSITE and pb < pr:
        qb = _validate_spec(spec) * (pr - pb)
        if rate_bpd <= qb:
            return pr - rate_bpd / _validate_spec(spec)
    qmax = aof
    term = 0.04 + 3.2 * (1.0 - min(1.0, rate_bpd / qmax))
    return max(0.0, min(pr, pr * (-0.2 + math.sqrt(max(0.0, term))) / 1.6))


def max_rate_bpd(spec: InflowSpec) -> float:
    """Alias for AOF retained as the engine's explicit rate-limit API."""

    return absolute_open_flow_bpd(spec)
