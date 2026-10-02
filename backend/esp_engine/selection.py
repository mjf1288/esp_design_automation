"""Candidate enumeration — the inverted design loop.

Framework refs: §6.3 (WAS: size for a point, check at other points. IS:
enumerate configurations, score each across the full range, rank), §12 (pump
selection: model / stages / frequency), §2.1 (engineers run 3-4 scenarios
instead of 20 because the software is slow).

This module answers "what could work?" not "what is best?". Ranking happens in
``scoring.py``. Keeping them separate matters: the enumerator must stay dumb and
exhaustive, because any cleverness here silently removes options the engineer
never learns existed.

Cost control strategy — staged pruning, cheapest test first:

    1. Geometry (set-membership on casing ID)   -> kills most candidates, ~free
    2. Rate plausibility (BEP within reach)     -> arithmetic, ~free
    3. Availability policy (allow/exclude)      -> ~free
    4. Stage count / frequency expansion        -> combinatorial, still cheap
    ---- everything above happens before a single PVT solve ----
    5. Per-cell hydraulics                      -> expensive, in pipeline.py

Framework §3.2 places geometry and electrical constraints "at input" precisely
because they are the cheap ones. The physics of the design and the economics of
computing it happen to agree.
"""

from __future__ import annotations

from .catalog import Catalog, PumpModel
from .config import EngineConfig
from .constraints import (
    prescreen_availability,
    prescreen_electrical,
    prescreen_geometry,
)
from .curves import (
    ScaledCurve,
    rate_within_domain,
    scale_curve_to_frequency,
    stages_required,
    try_evaluate_stage,
)
from .models import Case
from .results import (
    ConstraintViolation,
    PumpConfiguration,
    RejectedConfiguration,
)


class EnumerationResult:
    """Candidates plus the audit trail of what was rejected and why.

    Rejections are a deliverable, not debris. Framework §2.1 notes that regional
    experience is undocumented and a junior cannot be onboarded without a mentor;
    "here are the 340 configurations we ruled out and the reason for each" is a
    piece of that mentor made durable.
    """

    def __init__(self) -> None:
        self.candidates: list[tuple[PumpConfiguration, PumpModel, ScaledCurve]] = []
        self.rejected: list[RejectedConfiguration] = []

    def reject(
        self,
        config: PumpConfiguration,
        stage: str,
        reason: str,
        violations: list[ConstraintViolation] | None = None,
    ) -> None:
        self.rejected.append(
            RejectedConfiguration(
                configuration=config,
                stage_rejected=stage,  # type: ignore[arg-type]
                reason=reason,
                violations=violations or [],
            )
        )

    @property
    def rejection_summary(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for r in self.rejected:
            summary[r.stage_rejected] = summary.get(r.stage_rejected, 0) + 1
        return summary


# =============================================================================
# Setting depth
# =============================================================================


def resolve_setting_depth(case: Case) -> tuple[float, bool]:
    """Setting depth in ft MD, and whether it was assumed.

    Framework §5.2: from the previous installation, or "deeper is better" — but
    must be validated against geometry. "Deeper is better" is true for
    submergence and gas handling, and false the moment it puts the pump in a
    dogleg or below the perforations, so the bias is applied and then hard-bounded.
    """
    if case.expectations.setting_depth_md_ft is not None:
        return case.expectations.setting_depth_md_ft.value, False

    if case.reference is not None and case.reference.setting_depth_md_ft is not None:
        # Branch A anchor: the previous installation's depth is the best available
        # evidence that a pump physically fits and runs there.
        return case.reference.setting_depth_md_ft.value, True

    perf_top = case.geometry.perforation_top_md_ft
    if perf_top is not None:
        # 300 ft above the top perforation: enough standoff for gas to break out
        # into the annulus rather than into the intake, while keeping submergence.
        return max(0.0, perf_top.value - 300.0), True

    td = case.geometry.total_depth_md_ft
    if td is not None:
        return td.value * 0.90, True

    return 6000.0, True


def candidate_setting_depths(case: Case, cfg: EngineConfig) -> list[float]:
    """Setting depths to explore.

    A single depth when the customer specified one — overriding a stated depth
    would be presumptuous. A small bracket when we assumed it, because setting
    depth trades directly against free gas at intake and an engineer needs to see
    that trade rather than accept our guess.
    """
    depth, was_assumed = resolve_setting_depth(case)
    if not was_assumed:
        return [depth]

    max_depth = depth
    perf_top = case.geometry.perforation_top_md_ft
    if perf_top is not None:
        max_depth = min(max_depth, perf_top.value - 50.0)

    options = [max_depth]
    for offset in (500.0, 1000.0):
        shallower = max_depth - offset
        if shallower > 500.0:
            options.append(shallower)
    return sorted({round(d, 0) for d in options}, reverse=True)


# =============================================================================
# Stage count
# =============================================================================


def nominal_stage_count(
    curve: ScaledCurve, tdh_ft: float, rate_bpd: float, degradation: float = 1.0
) -> int:
    """Stage count that develops the required TDH at the operating rate.

    Returns 0 when the rate falls outside the curve's fitted domain. That is a
    refusal, not a failure: the alternative is extrapolating a degree-5
    polynomial, which yields a confident number with no physical basis.
    """
    point = try_evaluate_stage(curve, rate_bpd)
    if point is None or point.head_ft_per_stage <= 0:
        return 0
    return stages_required(tdh_ft, point.head_ft_per_stage, degradation)


def candidate_stage_counts(nominal: int, pump: PumpModel, cfg: EngineConfig) -> list[int]:
    """Stage counts to explore around the nominal.

    Bracketing rather than committing to the exact nominal is the point of the
    §6.3 inversion. The nominal count is optimal at month zero; a slightly higher
    count may hold the operating point in range for another eight months as the
    well waters out, and only a sweep reveals that.
    """
    if nominal <= 0:
        return []
    span = max(1, int(nominal * cfg.enumeration.stage_count_span_frac))
    step = max(1, cfg.enumeration.stage_count_step)

    counts: set[int] = {nominal}
    offset = step
    while offset <= span:
        counts.add(nominal - offset)
        counts.add(nominal + offset)
        offset += step

    return sorted(
        c for c in counts if 1 <= c <= pump.max_stages
    )


# =============================================================================
# Rate plausibility pre-screen
# =============================================================================


def pump_rate_plausible(
    pump: PumpModel, rate_bpd: float, frequencies: list[float]
) -> bool:
    """Whether any achievable frequency puts the target rate near this pump's range.

    Cheap arithmetic screen using affinity scaling of the recommended range,
    applied before any curve object is built. Deliberately generous — a pump only
    marginally outside its published range at month zero may be exactly right at
    month twelve, and the enumerator must not make that call.
    """
    lo, hi = pump.recommended_range_bpd
    ref = pump.frequency_ref_hz or 60.0
    for freq in frequencies:
        ratio = freq / ref
        if lo * ratio * 0.70 <= rate_bpd <= hi * ratio * 1.30:
            return True
    return False


# =============================================================================
# Enumeration
# =============================================================================


def enumerate_candidates(
    *,
    case: Case,
    catalog: Catalog,
    cfg: EngineConfig,
    tdh_estimate_ft: float,
    design_rate_bpd: float,
    gas_degradation_estimate: float = 1.0,
) -> EnumerationResult:
    """Build the candidate configuration set.

    ``tdh_estimate_ft`` is a screening TDH from the month-0 base-case hydraulics.
    It sizes the stage-count bracket only; every candidate is re-solved properly
    per scenario point in the pipeline. Using a screening value here is what keeps
    enumeration cheap enough to be exhaustive.
    """
    result = EnumerationResult()
    frequencies = case.constraints.electrical.achievable_frequencies()
    depths = candidate_setting_depths(case, cfg)

    max_od_override = (
        case.constraints.geometry.max_equipment_od_in.value
        if case.constraints.geometry.max_equipment_od_in
        else None
    )

    counter = 0
    capped = False

    for depth in depths:
        casing_id = case.geometry.min_casing_id_to_depth(depth)

        for pump in catalog.pumps:
            # --- Stage 1: geometry (cheapest, kills the most) ----------------
            geom_violations = prescreen_geometry(
                equipment_od_in=pump.housing_od_in,
                setting_depth_md_ft=depth,
                geometry=case.geometry,
                thresholds=cfg.thresholds,
                max_equipment_od_override_in=max_od_override,
            )
            physical = [v for v in geom_violations if v.physical_impossibility]
            if physical:
                result.reject(
                    _stub_config(pump, depth, frequencies[0], counter),
                    "geometry_prescreen",
                    physical[0].message,
                    physical,
                )
                counter += 1
                continue

            # --- Stage 3: availability policy (soft, does not reject) ---------
            soft = prescreen_availability(
                manufacturer=pump.manufacturer, pump_model=pump.model, case=case
            )

            # --- Stage 4: frequency x stage count expansion -------------------
            for freq in frequencies:
                elec_violations = prescreen_electrical(frequency_hz=freq, case=case)
                if elec_violations:
                    continue  # not a pump rejection; the frequency is unavailable

                try:
                    curve = scale_curve_to_frequency(
                        pump, freq, cfg.correlations.frequency_curves
                    )
                except (ValueError, KeyError) as exc:
                    result.reject(
                        _stub_config(pump, depth, freq, counter),
                        "hydraulic_infeasible",
                        f"Curve could not be scaled to {freq:g} Hz: {exc}",
                    )
                    counter += 1
                    continue

                nominal = nominal_stage_count(
                    curve, tdh_estimate_ft, design_rate_bpd, gas_degradation_estimate
                )
                if nominal <= 0:
                    nominal = min(100, pump.max_stages)

                for stages in candidate_stage_counts(nominal, pump, cfg):
                    if counter >= cfg.enumeration.max_candidates:
                        capped = True
                        break

                    config = PumpConfiguration(
                        config_id=f"c{counter:05d}",
                        pump_id=pump.id,
                        pump_model=pump.model,
                        manufacturer=pump.manufacturer,
                        series=pump.series,
                        stages=stages,
                        frequency_hz=freq,
                        setting_depth_md_ft=depth,
                    )
                    result.candidates.append((config, pump, curve))
                    counter += 1

                if capped:
                    break
            if capped:
                break
        if capped:
            break

    if capped:
        result.reject(
            _stub_config(catalog.pumps[0], depths[0], frequencies[0], counter),
            "hydraulic_infeasible",
            (
                f"Enumeration capped at {cfg.enumeration.max_candidates} "
                f"configurations. Narrow the search by constraining the pump "
                f"series, the frequency band, or the setting depth."
            ),
        )

    return result


def _stub_config(
    pump: PumpModel, depth: float, freq: float, counter: int
) -> PumpConfiguration:
    """Minimal configuration record for a rejection, so the audit trail names
    exactly which pump/depth/frequency combination was ruled out."""
    return PumpConfiguration(
        config_id=f"r{counter:05d}",
        pump_id=pump.id,
        pump_model=pump.model,
        manufacturer=pump.manufacturer,
        series=pump.series,
        stages=0,
        frequency_hz=freq,
        setting_depth_md_ft=depth,
    )


def dedupe_by_hydraulic_equivalence(
    candidates: list[tuple[PumpConfiguration, PumpModel, ScaledCurve]],
    design_rate_bpd: float,
    reference_head_ft: float,
    tolerance: float = 0.015,
) -> list[tuple[PumpConfiguration, PumpModel, ScaledCurve]]:
    """Collapse configurations that are hydraulically indistinguishable.

    A given pump at 118 stages / 58 Hz and 122 stages / 57 Hz can develop head
    within 1% of each other over the whole trajectory. Ranking both wastes
    compute and, worse, floods the engineer's top-10 list with near-duplicates
    while genuinely different alternatives fall off the bottom.

    The bucket key is the total developed head at the design rate, snapped to a
    ``tolerance``-wide grid. Configurations within one grid cell are treated as
    equivalent. The earlier form of this function computed the key as
    ``total_head / (total_head * tolerance)`` which reduces to the constant
    ``1 / tolerance`` for every candidate — so every stage count of a given
    pump at a given depth collapsed into one bucket, and the tiebreak below
    systematically kept the LOWEST stage count regardless of hydraulic
    adequacy. That is why an under-staged pump could rank first with a large
    negative head margin at the design point.

    Keeps the lowest stage count within each equivalence class — same head at
    fewer stages means less shaft load, shorter string, and lower cost. But
    equivalence is now measured by head, not by "same pump at same depth", so a
    43-stage build and a 53-stage build of the same pump are NOT collapsed.
    """
    buckets: dict[tuple[str, int], tuple[PumpConfiguration, PumpModel, ScaledCurve]] = {}
    for config, pump, curve in candidates:
        point = try_evaluate_stage(curve, design_rate_bpd)
        if point is None:
            # Off its own fitted domain at the design rate. Keep it rather than
            # silently dropping it; the constraints gate will reject it with a
            # reason the engineer can read.
            buckets[(f"{pump.id}|offcurve|{config.config_id}", 0)] = (
                config,
                pump,
                curve,
            )
            continue
        total_head = point.head_ft_per_stage * config.stages
        if total_head <= 0 or reference_head_ft <= 0:
            key_head = 0
        else:
            # Grid cell is a fixed absolute head window in ft, tied to the
            # (screening) design TDH. Two configurations whose developed head
            # at the design rate falls within one grid cell are treated as
            # equivalent regardless of stage count or frequency; anything
            # further apart lands in different buckets. Using an absolute
            # reference is the point: the earlier form of this key
            # (``total_head / (total_head * tolerance)``) simplified to the
            # constant ``1 / tolerance``, which collapsed every stage count of
            # the same pump-at-depth into ONE bucket and let the tiebreak
            # below systematically keep the smallest, most under-staged member.
            grid_cell_ft = max(reference_head_ft * tolerance, 1e-6)
            key_head = int(round(total_head / grid_cell_ft))
        key = (f"{pump.id}|{config.setting_depth_md_ft:.0f}", key_head)
        existing = buckets.get(key)
        if existing is None or config.stages < existing[0].stages:
            buckets[key] = (config, pump, curve)
    return sorted(buckets.values(), key=lambda t: t[0].config_id)
