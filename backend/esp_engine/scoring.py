"""Trajectory scoring, ranking, and the validity boundary.

Framework refs: §6.3 (the inverted logic: enumerate configurations, score each
across the full range, rank), §6.4 (candidate scoring criterion and the target
report format), §13 (the "best configuration" criterion is an open question).

This module is the product's differentiator. A conventional ESP sizing tool
answers "does this pump work at the design point?". This one answers "how long
will this pump keep working, what kills it, and can frequency save it?" —
framework §6.4's explicit validity boundary, "which customers normally never
receive".

The scoring objective is deliberately pluggable. §13 leaves the criterion open,
and it is a business decision (maximum coverage vs early-period priority vs
minimum failure risk) dressed as a technical one. Hard-coding it would bury the
most consequential choice in the system inside a constant.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .config import ScoringConfig, ScoringObjective
from .models import OperatingZone
from .results import (
    CellResult,
    ConstraintViolation,
    EnvelopeCase,
    GateStatus,
    ScoreBreakdown,
    TimelineEntry,
    ValidityBoundary,
    VSDRecovery,
    VSDRecoveryPoint,
)
from .trajectory import label_for_month

# =============================================================================
# Zone quality
# =============================================================================

_ZONE_DESCRIPTIONS: dict[OperatingZone, str] = {
    OperatingZone.BEP: "at best efficiency point",
    OperatingZone.OPERATING_RANGE: "within recommended operating range",
    OperatingZone.DOWNTHRUST: "downthrust (left of range) — underloaded",
    OperatingZone.UPTHRUST: "upthrust (right of range) — overloaded",
    OperatingZone.OFF_CURVE_LEFT: "off curve to the left — pump shut in / no flow",
    OperatingZone.OFF_CURVE_RIGHT: "off curve to the right — beyond pump capacity",
}

_ACCEPTABLE_ZONES = {OperatingZone.BEP, OperatingZone.OPERATING_RANGE}


def zone_quality(cell: CellResult, cfg: ScoringConfig) -> float:
    """Quality of one cell's operating position, in [0, 1].

    Starts from the configured zone weight, then modulates continuously by the
    cell's ``zone_severity``. The continuous term is what makes a trajectory
    scoreable rather than merely classifiable: a config sitting at Q/Qbep = 0.98
    all year should outrank one drifting from 1.24 to 0.76, even though both
    spend 100% of the horizon nominally "in range".
    """
    base = cfg.zone_weights.get(cell.zone.value, 0.0)
    if cell.zone in _ACCEPTABLE_ZONES:
        # Within range, penalize distance from BEP gently — being off-BEP costs
        # efficiency and thrust margin but is not a failure.
        return base * (1.0 - 0.35 * min(1.0, cell.zone_severity))
    # Outside range, severity compounds: deep downthrust is far worse than
    # marginal downthrust.
    return base * (1.0 - 0.7 * min(1.0, cell.zone_severity))


def _time_weights(months: list[float], cfg: ScoringConfig) -> list[float]:
    """Weight per scenario point, per the scoring objective."""
    if not months:
        return []
    if cfg.objective is ScoringObjective.EARLY_PRIORITY:
        half_life = max(0.5, cfg.early_priority_half_life_months)
        weights = [math.exp(-math.log(2.0) * m / half_life) for m in months]
    else:
        weights = [1.0] * len(months)
    total = sum(weights)
    return [w / total for w in weights] if total > 0 else weights


# =============================================================================
# Validity boundary (§6.4)
# =============================================================================


def compute_validity_boundary(
    cells_by_envelope: dict[EnvelopeCase, list[CellResult]],
    horizon_months: float,
) -> ValidityBoundary:
    """First month at which the design leaves acceptable operation.

    Reported per envelope so the output is a range, not a false point estimate.
    Framework §4 requires Branch B results to be delivered as a range; being
    honest about the boundary's own uncertainty is the same discipline applied
    one level down.
    """
    boundaries: dict[EnvelopeCase, float | None] = {}
    limiting: dict[EnvelopeCase, CellResult | None] = {}

    for envelope, cells in cells_by_envelope.items():
        ordered = sorted(cells, key=lambda c: c.month)
        boundary: float | None = None
        cause: CellResult | None = None
        for cell in ordered:
            if not cell.is_acceptable:
                boundary = cell.month
                cause = cell
                break
        boundaries[envelope] = boundary
        limiting[envelope] = cause

    base_boundary = boundaries.get(EnvelopeCase.BASE)
    base_cause = limiting.get(EnvelopeCase.BASE)

    numeric = [b for b in boundaries.values() if b is not None]
    b_min = min(numeric) if numeric else None
    b_max = max(numeric) if numeric else None

    # If some envelopes never fail, the upper bound is the horizon, not the worst
    # observed failure — otherwise the range would understate the good case.
    if any(b is None for b in boundaries.values()):
        b_max = horizon_months

    cause_cell = base_cause or next((c for c in limiting.values() if c), None)
    mechanism = _describe_limiting_mechanism(cause_cell, cells_by_envelope)

    return ValidityBoundary(
        valid_until_months=base_boundary,
        valid_until_months_min=b_min,
        valid_until_months_max=b_max,
        limiting_mechanism=mechanism,
        limiting_zone=cause_cell.zone if cause_cell else None,
        limiting_violation=(
            next(
                (v for v in cause_cell.violations if v.constraint_type == "hydraulic"),
                cause_cell.violations[0],
            ) if cause_cell and cause_cell.violations else None
        ),
        valid_through_horizon=base_boundary is None,
        horizon_months=horizon_months,
    )


def _describe_limiting_mechanism(
    cause: CellResult | None,
    cells_by_envelope: dict[EnvelopeCase, list[CellResult]],
) -> str:
    """The causal chain, in an engineer's language.

    A boundary month without a mechanism is not actionable — the engineer needs
    to know whether to expect a watering-out problem, a gas problem, or a
    depletion problem, because each has a different operational response.
    """
    if cause is None:
        return "design remains within acceptable operation across the full horizon"
    if cause.head_developed_ft < cause.head_required_ft - 1e-6:
        return (
            f"Insufficient head at month {cause.month:g}: pump develops "
            f"{cause.head_developed_ft:.0f} ft against TDH {cause.head_required_ft:.0f} ft. "
            "Proximity to BEP does not establish head sufficiency."
        )

    base = cells_by_envelope.get(EnvelopeCase.BASE, [])
    first = next((c for c in sorted(base, key=lambda c: c.month)), None)

    if cause.zone is OperatingZone.DOWNTHRUST or cause.zone is OperatingZone.OFF_CURVE_LEFT:
        drivers = []
        if first and cause.liquid_rate_bpd < first.liquid_rate_bpd * 0.95:
            drivers.append("declining inflow (PI and reservoir pressure)")
        if first and cause.mixture_sg > first.mixture_sg * 1.02:
            drivers.append("rising water cut increasing fluid density and head demand")
        if first and cause.tdh_ft > first.tdh_ft * 1.03:
            drivers.append("rising TDH requirement")
        driver_text = " + ".join(drivers) if drivers else "falling achievable rate"
        return (
            f"{driver_text} -> operating point migrates left of the recommended "
            f"range into downthrust at Q/Qbep = {cause.q_over_qbep:.2f}"
        )

    if cause.zone is OperatingZone.UPTHRUST or cause.zone is OperatingZone.OFF_CURVE_RIGHT:
        return (
            f"rising throughput or falling head demand -> operating point moves "
            f"right of the recommended range into upthrust at Q/Qbep = "
            f"{cause.q_over_qbep:.2f}"
        )

    if cause.violations:
        v = cause.violations[0]
        return f"{v.constraint_type} constraint violated: {v.message}"

    if not cause.converged:
        return (
            "hydraulic solution did not converge at this scenario point — the "
            "operating condition is numerically unstable and needs expert review"
        )

    if not cause.turpin_stable:
        return (
            f"free gas at intake reaches {cause.free_gas_fraction_at_intake:.1%} "
            f"and the Turpin stability criterion fails -> gas locking risk"
        )

    return f"design leaves acceptable operation ({cause.zone.value})"


# =============================================================================
# VSD recovery (§6.4)
# =============================================================================


def compute_vsd_recovery(
    *,
    vsd_available: bool,
    frequency_band_hz: tuple[float, float] | None,
    base_validity: ValidityBoundary,
    recovered_cells_by_frequency: dict[float, list[CellResult]] | None = None,
    horizon_months: float = 24.0,
) -> VSDRecovery | None:
    """What portion of the range frequency can reclaim.

    Framework §6.4: "With a VSD present, the report additionally shows what
    portion of the range can be recovered through frequency."

    Returns ``None`` when no VSD is present. Deliberately not an empty section:
    an empty panel invites the reader to assume the check was run and came back
    clean.
    """
    if not vsd_available:
        return None

    if base_validity.valid_through_horizon:
        return VSDRecovery(
            vsd_available=True,
            assessment="not_needed",
            frequency_band_hz=frequency_band_hz,
            extended_validity_months=None,
            recovered_horizon_frac=None,
            note=(
                "Design is already valid across the full horizon at the design "
                "frequency; VSD provides operational margin rather than required "
                "recovery."
            ),
        )

    base_boundary = base_validity.valid_until_months or 0.0
    lost_horizon = max(0.0, horizon_months - base_boundary)

    if not recovered_cells_by_frequency or lost_horizon <= 0:
        return VSDRecovery(
            vsd_available=True,
            assessment="unverified" if not recovered_cells_by_frequency else "no_recovery",
            frequency_band_hz=frequency_band_hz,
            extended_validity_months=base_boundary,
            recovered_horizon_frac=0.0,
            note=(
                "Frequency recovery has not been verified; no trial results are available."
                if not recovered_cells_by_frequency else
                "No tested frequency restores acceptable operation."
            ),
        )

    # For each month past the boundary, find the lowest-deviation frequency that
    # brings the cell back into an acceptable zone.
    schedule: list[tuple[float, float]] = []
    recovered_months: set[float] = set()
    months_seen: set[float] = set()

    for freq, cells in recovered_cells_by_frequency.items():
        for cell in cells:
            months_seen.add(cell.month)
            if cell.month >= base_boundary and cell.is_acceptable and cell.equipment_checks_performed:
                recovered_months.add(cell.month)

    for month in sorted(m for m in months_seen if m >= base_boundary):
        candidates = [
            (freq, cell)
            for freq, cells in recovered_cells_by_frequency.items()
            for cell in cells
            if cell.month == month and cell.is_acceptable and cell.equipment_checks_performed
        ]
        if candidates:
            # Prefer the frequency that lands closest to BEP, not merely the
            # lowest — running at the edge of the range to stay "acceptable" just
            # moves the boundary a few weeks.
            freq, _ = min(candidates, key=lambda fc: abs(fc[1].q_over_qbep - 1.0))
            schedule.append((month, freq))

    if not recovered_months:
        extended = base_boundary
        frac = 0.0
    else:
        # Extended validity runs until the first month past the boundary that no
        # frequency can rescue. Contiguity matters: a gap means the well needs
        # intervention, not a frequency change.
        contiguous = base_boundary
        for month in sorted(m for m in months_seen if m >= base_boundary):
            if month in recovered_months:
                contiguous = month
            else:
                break
        extended = contiguous
        frac = min(1.0, max(0.0, (extended - base_boundary) / lost_horizon))

    # No recovery can jump over an intervening failed month. Do not publish
    # isolated successful trials after the first gap as an operating schedule.
    schedule = [(m, f) for m, f in schedule if base_boundary <= m <= extended]
    if base_boundary not in recovered_months:
        schedule = []
        extended, frac = base_boundary, 0.0
    verified = bool(schedule)
    evidence = []
    for month, freq in schedule:
        cell = next(c for c in recovered_cells_by_frequency[freq] if c.month == month)
        evidence.append(VSDRecoveryPoint(
            month=month, frequency_hz=freq,
            head_developed_ft=cell.head_developed_ft,
            head_required_ft=cell.head_required_ft,
            motor_loading_frac=cell.motor_loading_frac,
            cable_voltage_drop_frac=cell.cable_voltage_drop_frac,
            motor_winding_temp_f=cell.motor_self_heating.winding_temp_f,
            cable_conductor_temp_f=cell.cable_self_heating.conductor_temp_f,
        ))
    return VSDRecovery(
        vsd_available=True,
        assessment="verified" if verified else "no_recovery",
        frequency_band_hz=frequency_band_hz,
        extended_validity_months=extended,
        recovered_horizon_frac=frac,
        frequency_schedule=schedule,
        verified_points=evidence,
        note=(
            f"Frequency adjustment extends acceptable operation from "
            f"{base_boundary:.1f} to {extended:.1f} months, recovering "
            f"{frac:.0%} of the out-of-range horizon. "
            "Verified at sampled months on the original motor and cable under current model limits; "
            "not a field-release approval or proof between samples."
        ) if verified else "No tested frequency restores acceptable operation at the first failed month on the installed equipment.",
    )


# =============================================================================
# Scoring
# =============================================================================


def score_candidate(
    cells: list[CellResult],
    cfg: ScoringConfig,
    *,
    validity: ValidityBoundary,
    vsd_recovery: VSDRecovery | None,
    horizon_months: float,
    has_soft_violations: bool = False,
) -> ScoreBreakdown:
    """Aggregate a configuration's performance across the whole trajectory.

    Framework §6.3: score each configuration across the full range, then rank.
    """
    if not cells:
        return ScoreBreakdown(
            total_score=0.0,
            objective=cfg.objective.value,
            zone_quality_score=0.0,
            time_coverage_frac=0.0,
            bep_time_frac=0.0,
            efficiency_avg_frac=0.0,
            envelope_robustness=0.0,
            explanation="no evaluable scenario points",
        )

    by_envelope: dict[EnvelopeCase, list[CellResult]] = defaultdict(list)
    for cell in cells:
        by_envelope[cell.envelope].append(cell)

    envelope_scores: dict[EnvelopeCase, float] = {}
    envelope_coverage: dict[EnvelopeCase, float] = {}

    for envelope, env_cells in by_envelope.items():
        ordered = sorted(env_cells, key=lambda c: c.month)
        months = [c.month for c in ordered]
        weights = _time_weights(months, cfg)
        envelope_scores[envelope] = sum(
            w * zone_quality(c, cfg) for w, c in zip(weights, ordered)
        )
        envelope_coverage[envelope] = sum(
            w for w, c in zip(weights, ordered) if c.is_acceptable
        )

    weighted = 0.0
    weight_total = 0.0
    for envelope, score in envelope_scores.items():
        w = cfg.envelope_weights.get(envelope.value, 0.0)
        weighted += w * score
        weight_total += w
    zone_score = weighted / weight_total if weight_total > 0 else 0.0

    base_cells = sorted(
        by_envelope.get(EnvelopeCase.BASE, cells), key=lambda c: c.month
    )
    base_months = [c.month for c in base_cells]
    base_weights = _time_weights(base_months, cfg)

    coverage = sum(w for w, c in zip(base_weights, base_cells) if c.is_acceptable)
    bep_frac = sum(
        w for w, c in zip(base_weights, base_cells) if c.zone is OperatingZone.BEP
    )
    eff_avg = (
        sum(w * c.efficiency_frac for w, c in zip(base_weights, base_cells))
        if base_cells
        else 0.0
    )

    # Robustness: how little the score varies across the envelope. A config that
    # only works in the base case is not a design.
    if len(envelope_scores) > 1:
        values = list(envelope_scores.values())
        spread = max(values) - min(values)
        robustness = max(0.0, 1.0 - spread)
    else:
        robustness = 0.75  # unknown, not proven robust — do not reward

    # --- Objective-specific total -------------------------------------------

    if cfg.objective is ScoringObjective.MAX_COVERAGE:
        total = 0.65 * zone_score + 0.25 * coverage + 0.10 * robustness

    elif cfg.objective is ScoringObjective.EARLY_PRIORITY:
        total = 0.75 * zone_score + 0.15 * coverage + 0.10 * robustness

    elif cfg.objective is ScoringObjective.MAX_VALIDITY_HORIZON:
        horizon_frac = (
            1.0
            if validity.valid_through_horizon
            else min(1.0, (validity.valid_until_months or 0.0) / max(1.0, horizon_months))
        )
        total = 0.55 * horizon_frac + 0.30 * zone_score + 0.15 * robustness

    elif cfg.objective is ScoringObjective.MIN_FAILURE_RISK:
        # Penalize the deep-severity tail rather than the average: one month of
        # severe downthrust does more damage than six months of mild off-BEP
        # operation, and an average would hide it.
        worst = max((c.zone_severity for c in base_cells if not c.is_acceptable), default=0.0)
        total = 0.50 * zone_score + 0.25 * (1.0 - worst) + 0.25 * robustness

    elif cfg.objective is ScoringObjective.MAX_EFFICIENCY:
        total = 0.45 * eff_avg + 0.40 * zone_score + 0.15 * robustness

    else:  # pragma: no cover
        total = zone_score

    # --- Penalties ----------------------------------------------------------

    vsd_penalty = 0.0
    if (
        vsd_recovery is not None
        and vsd_recovery.recovered_horizon_frac
        and vsd_recovery.recovered_horizon_frac > 0.0
        and not validity.valid_through_horizon
    ):
        # This config only stays in range through active frequency management,
        # which assumes surveillance the customer may not have.
        vsd_penalty = cfg.penalize_vsd_dependence
        total -= vsd_penalty

    soft_penalty = 0.03 if has_soft_violations else 0.0
    total -= soft_penalty

    total = max(0.0, min(1.0, total))

    return ScoreBreakdown(
        total_score=total,
        objective=cfg.objective.value,
        zone_quality_score=zone_score,
        time_coverage_frac=coverage,
        bep_time_frac=bep_frac,
        efficiency_avg_frac=eff_avg,
        envelope_robustness=robustness,
        vsd_dependence_penalty=vsd_penalty,
        soft_constraint_penalty=soft_penalty,
        explanation=_score_explanation(
            cfg, zone_score, coverage, bep_frac, eff_avg, robustness, validity
        ),
    )


def _score_explanation(
    cfg: ScoringConfig,
    zone_score: float,
    coverage: float,
    bep_frac: float,
    eff_avg: float,
    robustness: float,
    validity: ValidityBoundary,
) -> str:
    """Plain-language reason for the score. An opaque rank is not actionable."""
    validity_text = (
        f"valid through the full {validity.horizon_months:g}-month horizon"
        if validity.valid_through_horizon
        else f"valid for {validity.valid_until_months:.1f} months"
        if validity.valid_until_months is not None
        else "validity boundary not determined"
    )
    return (
        f"Objective '{cfg.objective.value}': spends {coverage:.0%} of the "
        f"weighted horizon in acceptable zones ({bep_frac:.0%} near BEP), average "
        f"efficiency {eff_avg:.1%}, envelope robustness {robustness:.2f}. "
        f"{validity_text.capitalize()}."
    )


# =============================================================================
# Timeline rendering (§6.4 target report format)
# =============================================================================


def build_timeline(
    base_cells: list[CellResult], report_months: list[float] | None = None
) -> list[TimelineEntry]:
    """The report rows of framework §6.4.

    Defaults to the framework's own discrete checkpoints — day 1, +2 mo, +6 mo,
    +1 yr, +2 yr — because those are the intervals engineers already reason in
    (§6.2). The continuous sweep underneath is what produces them; the discrete
    presentation is what makes them readable.
    """
    if not base_cells:
        return []

    ordered = sorted(base_cells, key=lambda c: c.month)
    if report_months is None:
        horizon = ordered[-1].month
        candidates = [0.0, 2.0, 6.0, 12.0, 18.0, 24.0, 36.0]
        report_months = [m for m in candidates if m <= horizon + 1e-6]
        if horizon not in report_months:
            report_months.append(horizon)

    entries: list[TimelineEntry] = []
    for target in report_months:
        cell = min(ordered, key=lambda c: abs(c.month - target))
        entries.append(
            TimelineEntry(
                month=cell.month,
                label=label_for_month(cell.month),
                zone=cell.zone,
                zone_description=_ZONE_DESCRIPTIONS.get(cell.zone, cell.zone.value),
                q_over_qbep=cell.q_over_qbep,
                severity=cell.zone_severity,
                flagged=not cell.is_acceptable,
                rate_bpd=cell.liquid_rate_bpd,
                pip_psi=cell.pip_psi,
                efficiency_frac=cell.efficiency_frac,
                motor_loading_frac=cell.motor_loading_frac,
                head_developed_ft=cell.head_developed_ft,
                head_required_ft=cell.head_required_ft,
                note=(
                    "; ".join(v.message for v in sorted(
                        cell.violations,
                        key=lambda v: (v.constraint_type != "hydraulic", v.rigidity == "soft"),
                    )[:2])
                    if cell.violations
                    else (
                        "hydraulic solution did not converge"
                        if not cell.converged
                        else None
                    )
                ),
            )
        )
    return entries


def render_timeline_text(entries: list[TimelineEntry]) -> str:
    """Framework §6.4's literal target report format, as text.

        Day 1   -> operating range, near BEP
        +2 mo   -> operating range
        +6 mo   -> [!] left zone (down thrust)
        +1 yr   -> [!] left zone, deep
    """
    lines = []
    for e in entries:
        flag = "[!] " if e.flagged else ""
        detail = e.zone_description
        if e.zone is OperatingZone.BEP:
            detail = "operating range, near BEP"
        elif e.zone is OperatingZone.DOWNTHRUST:
            detail = "left zone (down thrust)" + (", deep" if e.severity > 0.6 else "")
        elif e.zone is OperatingZone.UPTHRUST:
            detail = "right zone (up thrust)" + (", deep" if e.severity > 0.6 else "")
        lines.append(f"{e.label:<8}-> {flag}{detail}  (Q/Qbep = {e.q_over_qbep:.2f})")
    return "\n".join(lines)
