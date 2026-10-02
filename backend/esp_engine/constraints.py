"""The CONSTRAINTS GATE — a single choke point.

Framework refs: §3.2 (constraint types, rigidity, when checked), §12 (the
``failed -> return to selection`` edge in the process flow).

Design decision: there is exactly ONE function that decides whether a
configuration is admissible. Nothing bypasses it. Scattering constraint checks
across the selection, motor, and cable modules is how a violated limit
eventually ships in a design — so all of them report *findings*, and this module
alone renders the verdict.

Rigidity determines what happens on violation (§3.2):

    ABSOLUTE (physics)    -> config is dead. Discard.
    HARD (engineering)    -> return to selection, with an actionable remedy.
    SOFT (commercial)     -> config survives, flagged, with a lead-time note.

The remedy hints matter as much as the verdicts. Framework §2.1 notes that
onboarding a junior engineer is effectively impossible without a mentor; a
rejection that explains what to change is a small piece of that mentor,
available from day one.
"""

from __future__ import annotations

from .config import DesignThresholds
from .models import CasingSection, Case, WellGeometry
from .results import ConstraintViolation, GateStatus, PumpConfiguration


# =============================================================================
# Pre-screen — checked AT INPUT (§3.2), before any calculation
# =============================================================================


def prescreen_geometry(
    *,
    equipment_od_in: float,
    setting_depth_md_ft: float,
    geometry: WellGeometry,
    thresholds: DesignThresholds,
    max_equipment_od_override_in: float | None = None,
) -> list[ConstraintViolation]:
    """Geometry pre-screen. Rigidity ABSOLUTE — this is physics, not preference.

    The controlling dimension is the TIGHTEST restriction above setting depth,
    not the ID at setting depth. Equipment has to get *past* everything above it.
    Getting this wrong is how a string gets stuck at 3,000 ft on the way to
    8,000 ft.
    """
    violations: list[ConstraintViolation] = []
    g = thresholds.geometry

    restriction = geometry.min_casing_id_to_depth(setting_depth_md_ft)
    if restriction is None:
        # No casing program covering this depth. Framework §5.1 makes casing ID a
        # HARD STOP, so this is a blocking data gap, not a soft warning.
        violations.append(
            ConstraintViolation(
                constraint_type="geometry",
                rigidity="absolute",
                message=(
                    f"No casing program data covering setting depth "
                    f"{setting_depth_md_ft:.0f} ft MD. Casing ID is a hard-stop "
                    f"input: run-in-hole cannot be validated without it."
                ),
                remedy_hint="Obtain the casing tally / wellbore schematic.",
            )
        )
        return violations

    clearance = restriction - equipment_od_in
    if clearance < g.min_radial_clearance_in:
        violations.append(
            ConstraintViolation(
                constraint_type="geometry",
                rigidity="absolute",
                physical_impossibility=clearance <= 0,
                message=(
                    f"Equipment OD {equipment_od_in:.2f} in leaves only "
                    f"{clearance:.3f} in clearance in the tightest restriction "
                    f"above setting depth ({restriction:.3f} in ID); minimum "
                    f"running clearance is {g.min_radial_clearance_in:.2f} in."
                ),
                actual_value=clearance,
                limit_value=g.min_radial_clearance_in,
                unit="in",
                remedy_hint=(
                    f"Select a smaller ESP series (equipment OD below "
                    f"{restriction - g.min_radial_clearance_in:.2f} in)."
                ),
            )
        )

    if max_equipment_od_override_in is not None and equipment_od_in > max_equipment_od_override_in:
        violations.append(
            ConstraintViolation(
                constraint_type="geometry",
                rigidity="absolute",
                physical_impossibility=False,
                message=(
                    f"Equipment OD {equipment_od_in:.2f} in exceeds the "
                    f"customer-specified maximum of "
                    f"{max_equipment_od_override_in:.2f} in."
                ),
                actual_value=equipment_od_in,
                limit_value=max_equipment_od_override_in,
                unit="in",
                remedy_hint="Select a smaller ESP series.",
            )
        )

    # Dogleg severity: the limit for RUNNING through a bend is looser than the
    # limit for SITTING in one. A string can pass a 6 deg/100ft dogleg but must
    # not be set in it — the housing sees a permanent bending moment.
    dls_running = geometry.max_dogleg_to_depth(setting_depth_md_ft)
    if dls_running is not None and dls_running > g.max_dogleg_deg_per_100ft:
        violations.append(
            ConstraintViolation(
                constraint_type="geometry",
                rigidity="absolute",
                message=(
                    f"Maximum dogleg severity above setting depth is "
                    f"{dls_running:.2f} deg/100ft, exceeding the running limit of "
                    f"{g.max_dogleg_deg_per_100ft:.2f} deg/100ft."
                ),
                actual_value=dls_running,
                limit_value=g.max_dogleg_deg_per_100ft,
                unit="deg/100ft",
                remedy_hint=(
                    "Shorter housings / more sections, a shallower setting depth "
                    "above the dogleg, or vendor confirmation of a higher limit "
                    "for this equipment length."
                ),
            )
        )

    dls_at_set = _dls_near_depth(geometry, setting_depth_md_ft)
    if (
        dls_at_set is not None
        and dls_at_set > g.max_dogleg_at_setting_depth_deg_per_100ft
    ):
        violations.append(
            ConstraintViolation(
                constraint_type="geometry",
                rigidity="hard",
                message=(
                    f"Dogleg severity at the setting depth is {dls_at_set:.2f} "
                    f"deg/100ft, exceeding the {g.max_dogleg_at_setting_depth_deg_per_100ft:.2f} "
                    f"deg/100ft limit for the set interval. The string must sit in "
                    f"a straight section."
                ),
                actual_value=dls_at_set,
                limit_value=g.max_dogleg_at_setting_depth_deg_per_100ft,
                unit="deg/100ft",
                remedy_hint="Move the setting depth to a straighter interval.",
            )
        )

    inc = _inclination_near_depth(geometry, setting_depth_md_ft)
    if inc is not None and inc > g.max_inclination_at_setting_depth_deg:
        violations.append(
            ConstraintViolation(
                constraint_type="geometry",
                rigidity="hard",
                message=(
                    f"Inclination at setting depth is {inc:.1f} deg, exceeding the "
                    f"{g.max_inclination_at_setting_depth_deg:.1f} deg limit. High "
                    f"inclination degrades gas separation and seal-chamber "
                    f"performance."
                ),
                actual_value=inc,
                limit_value=g.max_inclination_at_setting_depth_deg,
                unit="deg",
                remedy_hint=(
                    "Set shallower in a lower-inclination interval, or confirm a "
                    "deviated-well configuration with the vendor."
                ),
            )
        )

    perf_top = geometry.perforation_top_md_ft
    if perf_top is not None and setting_depth_md_ft > perf_top.value:
        violations.append(
            ConstraintViolation(
                constraint_type="geometry",
                rigidity="absolute",
                message=(
                    f"Setting depth {setting_depth_md_ft:.0f} ft MD is below the "
                    f"top perforation at {perf_top.value:.0f} ft MD. The pump "
                    f"must be set above the producing interval."
                ),
                actual_value=setting_depth_md_ft,
                limit_value=perf_top.value,
                unit="ft",
                remedy_hint="Set the pump above the top perforation.",
            )
        )

    return violations


def prescreen_electrical(
    *,
    frequency_hz: float,
    case: Case,
) -> list[ConstraintViolation]:
    """Electrical pre-screen. Rigidity HARD, checked AT INPUT (§3.2).

    Without a VSD the frequency degree of freedom does not exist, and a design
    that quietly assumes one is unbuildable.
    """
    violations: list[ConstraintViolation] = []
    e = case.constraints.electrical

    if not e.vsd_available.value:
        fixed = e.fixed_frequency_hz.value if e.fixed_frequency_hz else 60.0
        if abs(frequency_hz - fixed) > 0.01:
            violations.append(
                ConstraintViolation(
                    constraint_type="electrical",
                    rigidity="hard",
                    message=(
                        f"No VSD available: operation is fixed at {fixed:g} Hz, "
                        f"but this configuration requires {frequency_hz:g} Hz."
                    ),
                    actual_value=frequency_hz,
                    limit_value=fixed,
                    unit="Hz",
                    remedy_hint=(
                        "Adjust stage count instead of frequency, or add a VSD to "
                        "the scope."
                    ),
                )
            )
        return violations

    lo = e.frequency_min_hz.value if e.frequency_min_hz else None
    hi = e.frequency_max_hz.value if e.frequency_max_hz else None
    if lo is not None and frequency_hz < lo - 1e-9:
        violations.append(
            ConstraintViolation(
                constraint_type="electrical",
                rigidity="hard",
                message=(
                    f"Frequency {frequency_hz:g} Hz is below the available VSD "
                    f"minimum of {lo:g} Hz."
                ),
                actual_value=frequency_hz,
                limit_value=lo,
                unit="Hz",
                remedy_hint="Increase stage count to reduce the required frequency.",
            )
        )
    if hi is not None and frequency_hz > hi + 1e-9:
        violations.append(
            ConstraintViolation(
                constraint_type="electrical",
                rigidity="hard",
                message=(
                    f"Frequency {frequency_hz:g} Hz exceeds the available VSD "
                    f"maximum of {hi:g} Hz."
                ),
                actual_value=frequency_hz,
                limit_value=hi,
                unit="Hz",
                remedy_hint="Add stages so the target rate is met at a lower frequency.",
            )
        )
    return violations


def prescreen_availability(
    *,
    manufacturer: str,
    pump_model: str,
    case: Case,
) -> list[ConstraintViolation]:
    """Availability / fleet-standardization screen. Rigidity SOFT (§3.2).

    Framework §13 flags stock and fleet standardization as an open area. What is
    implemented here is the *policy* layer — allowed/excluded/preferred models.
    A real stock check needs an inventory feed, which is noted as a limitation
    rather than faked.
    """
    violations: list[ConstraintViolation] = []
    a = case.constraints.availability

    if a.allowed_manufacturers and manufacturer not in a.allowed_manufacturers:
        violations.append(
            ConstraintViolation(
                constraint_type="availability",
                rigidity="soft",
                message=(
                    f"{manufacturer} is not on the customer's approved vendor "
                    f"list ({', '.join(a.allowed_manufacturers)})."
                ),
                remedy_hint="Select an approved vendor, or seek an exception.",
            )
        )

    if pump_model in a.excluded_pump_models:
        violations.append(
            ConstraintViolation(
                constraint_type="availability",
                rigidity="soft",
                message=f"Pump model {pump_model} is explicitly excluded by the customer.",
                remedy_hint="Select an alternative model.",
            )
        )

    if a.restrict_to_stock.value and a.preferred_pump_models:
        if pump_model not in a.preferred_pump_models:
            violations.append(
                ConstraintViolation(
                    constraint_type="availability",
                    rigidity="soft",
                    message=(
                        f"{pump_model} is outside the customer's standardized "
                        f"fleet and the request is restricted to stock. Lead time "
                        f"applies."
                    ),
                    remedy_hint=(
                        "Prefer a fleet-standard model, or confirm the customer "
                        "will accept a procurement lead time."
                    ),
                )
            )
    return violations


# =============================================================================
# The gate — verdict rendering
# =============================================================================


def evaluate_gate(violations: list[ConstraintViolation]) -> GateStatus:
    """Render a single verdict from all findings.

    Precedence follows §3.2 rigidity: absolute beats hard beats soft. A config
    with both an absolute and a soft violation is dead; reporting it as merely
    "flagged" would be actively misleading.
    """
    if any(v.rigidity == "absolute" for v in violations):
        return GateStatus.FAILED_ABSOLUTE
    if any(v.rigidity == "hard" for v in violations):
        return GateStatus.FAILED_HARD
    if any(v.rigidity == "soft" for v in violations):
        return GateStatus.FAILED_SOFT
    return GateStatus.PASSED


def gate_allows_ranking(status: GateStatus) -> bool:
    """Whether a config with this status may appear in the ranked results.

    SOFT failures do appear — they are commercial, not physical, and the
    engineer needs to see "this is the right pump but it is not in stock" rather
    than have it silently vanish.
    """
    return status in {
        GateStatus.PASSED,
        GateStatus.PASSED_WITH_WARNINGS,
        GateStatus.FAILED_SOFT,
    }


def summarize_remedies(violations: list[ConstraintViolation]) -> list[str]:
    """Deduplicated remedy hints, for the ``return to selection`` edge (§12).

    This is what turns a rejection list into guidance. Framework §2.1: regional
    experience is undocumented and onboarding requires a mentor. A rejection
    that names the fix is a fragment of that mentor, encoded.
    """
    seen: dict[str, None] = {}
    for v in violations:
        if v.remedy_hint:
            seen.setdefault(v.remedy_hint, None)
    return list(seen.keys())


# =============================================================================
# Helpers
# =============================================================================


def _dls_near_depth(
    geometry: WellGeometry, md_ft: float, window_ft: float = 300.0
) -> float | None:
    """Worst dogleg within a window around the setting depth.

    The window exists because the ESP string is ~100 ft of rigid equipment: a
    dogleg 50 ft away is a dogleg the string is sitting in.
    """
    values = [
        p.dogleg_severity_deg_per_100ft
        for p in geometry.deviation_survey
        if p.dogleg_severity_deg_per_100ft is not None
        and abs(p.md_ft - md_ft) <= window_ft
    ]
    return max(values) if values else None


def _inclination_near_depth(
    geometry: WellGeometry, md_ft: float, window_ft: float = 300.0
) -> float | None:
    values = [
        p.inclination_deg
        for p in geometry.deviation_survey
        if abs(p.md_ft - md_ft) <= window_ft
    ]
    return max(values) if values else None


def default_casing_program(total_depth_ft: float, casing_id_in: float) -> list[CasingSection]:
    """Single-string casing program, for cases where only one ID is known.

    Used when a customer states "5.5 inch casing" with no tally. The resulting
    design is still valid, but the assumption ledger records that the casing
    program was flattened to a single section — because a real well with a
    tapered string could hide a tighter restriction than the stated ID.
    """
    return [
        CasingSection(
            od_in=casing_id_in + 0.4,
            id_in=casing_id_in,
            drift_id_in=casing_id_in - 0.03,
            top_md_ft=0.0,
            bottom_md_ft=total_depth_ft,
        )
    ]
