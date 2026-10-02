"""Orchestration — the only module that knows the full §12 process order.

Framework ref: §12 process flow.

    request -> extraction/normalization -> classification -> Gate 1 -> TDH
    -> pump selection -> gas handling -> motor -> seal/protector -> cable
    -> sensor -> CONSTRAINTS GATE -> (failed: return to selection)
    -> trajectory scoring -> empirical overlay -> string assembly
    -> compatibility check -> output

Every other engine module is a pure function of its inputs and knows nothing
about what runs before or after it. That is deliberate: sequencing knowledge
concentrated in one file can be audited against the framework line by line,
whereas sequencing knowledge spread across twelve files cannot be audited at all.

Cost management. The naive loop is ``configs x scenario_points`` full hydraulic
solves — roughly 1,200 x 24 = 29,000 PVT/intake solves, which is seconds to
minutes and destroys the interactivity that framework §2.1 identifies as the
core complaint ("engineers run 3-4 scenarios instead of 20"). Two structural
fixes:

    1. PVT/intake state is cached per (scenario_point, setting_depth). Intake
       conditions depend on the well and the fluid, NOT on which pump is
       installed. ~1,200 configs collapse onto ~20 distinct solves.
    2. Staged pruning. Every candidate is evaluated at month-0 base case first.
       Only survivors of the constraints gate pay for a full trajectory.

Fix 1 is the important one, and it is available only because the physics was
factored to keep well state separate from pump state.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from .cable import CableSizing, size_cable
from .catalog import Catalog, PumpModel, is_estimated_record, load_catalog
from .tolerance import assess_candidate_tolerance, assess_ranking_stability
from .cooling import CoolingFloorBasis, CoolingVerdict
from .config import DEFAULT_CONFIG, ENGINE_VERSION, EngineConfig
from .constraints import (
    evaluate_gate,
    gate_allows_ranking,
    prescreen_geometry,
    prescreen_availability,
    summarize_remedies,
)
from .curves import (
    ScaledCurve,
    apply_viscosity_correction,
    classify_zone,
    evaluate_stage,
    rate_within_domain,
    stages_required,
    total_bhp,
    total_head_ft,
)
from .gas import GasAssessment, GasStrategy, assess_gas
from .inflow import (
    InflowSpec,
    absolute_open_flow_bpd,
    productivity_index_from_test,
    pwf_for_rate,
)
from .intake import IntakeConditions, solve_intake_conditions
from .mechanical import MechanicalCheck, check_mechanical
from .models import (
    Case,
    ComplicationType,
    OperatingZone,
    Severity,
    TaskBranch,
)
from .motor import MotorSizing, size_motor
from .pvt import FluidSpec
from .responsibility import build_trust_register
from .results import (
    AssumptionLedgerEntry,
    CandidateResult,
    CellResult,
    ConstraintViolation,
    DesignResult,
    EnvelopeCase,
    FeasibilityVerdict,
    GateStatus,
    PumpConfiguration,
    RejectedConfiguration,
    RunProvenance,
    ScenarioPoint,
)
from .scoring import (
    build_timeline,
    compute_validity_boundary,
    compute_vsd_recovery,
    score_candidate,
)
from .selection import (
    dedupe_by_hydraulic_equivalence,
    enumerate_candidates,
    resolve_setting_depth,
)
from .string import assemble_string
from .tdh import compute_tdh
from .trajectory import build_trajectory

# Fallback values, applied only when the case has no value AND no assumption was
# recorded upstream. Every one of these lands in the assumption ledger; none of
# them silently become facts.
_FALLBACK_OIL_API = 32.0
_FALLBACK_GAS_SG = 0.75
_FALLBACK_WATER_SG = 1.02
_FALLBACK_SALINITY_PPM = 30_000.0
_FALLBACK_SURFACE_TEMP_F = 80.0
_FALLBACK_WELLHEAD_PSI = 150.0
_FALLBACK_CASING_PSI = 100.0
_FALLBACK_TUBING_ID_IN = 2.441  # 2-7/8 in tubing, the most common ESP string


class DesignEngine:
    """The deterministic engine. Produces facts. Contains no LLM client.

    Framework §1.2: the deterministic layer must be reproducible and
    model-independent. Enforced structurally — this module's import graph has no
    path to an LLM client, so a future contributor cannot casually add "just one"
    model call inside the calculation.
    """

    def __init__(
        self,
        catalog: Catalog | None = None,
        config: EngineConfig | None = None,
    ) -> None:
        self.catalog = catalog or load_catalog()
        self.config = config or DEFAULT_CONFIG
        self._intake_cache: dict[tuple, IntakeConditions] = {}

    # =========================================================================
    # Entry point
    # =========================================================================

    def run(self, case: Case) -> DesignResult:
        started = time.perf_counter()
        self._intake_cache.clear()
        cfg = self.config
        geom = cfg.thresholds.geometry.model_copy(update={
            "max_dogleg_deg_per_100ft": case.constraints.geometry.max_dogleg_deg_per_100ft.value,
            "max_dogleg_at_setting_depth_deg_per_100ft": case.constraints.geometry.max_setting_dogleg_deg_per_100ft.value,
        })
        cfg = cfg.model_copy(update={"thresholds": cfg.thresholds.model_copy(update={"geometry": geom})})
        self.config = cfg
        warnings: list[str] = []

        # --- §12 step 3: classification / Decision Gate 1 --------------------
        branch = case.branch

        # --- Operator-supplied motor data must name a real motor -------------
        # Checked before anything expensive, and raised rather than warned. An
        # override keyed on a mistyped motor id would change nothing while the
        # report still said vendor data had been supplied, so the operator would
        # read a nameplate-scaled current as a B.14.1 result. That is a wrong
        # answer wearing the label of a right one, which is the one outcome this
        # engine must never produce.
        known_motor_ids = {m.id for m in self.catalog.motors}
        unknown = [
            o.motor_id
            for o in case.constraints.electrical.motor_data_overrides
            if o.motor_id not in known_motor_ids
        ]
        if unknown:
            raise ValueError(
                "motor_data_overrides reference motor ids that are not in catalog "
                f"version {self.catalog.version[:12]}: {sorted(unknown)}. "
                f"Known ids: {sorted(known_motor_ids)}. Fix the id or remove the "
                "override; an override that matches nothing is reported as "
                "supplied vendor data while having no effect on the calculation."
            )

        # --- §5.1 hard stops: no amount of cleverness substitutes ------------
        missing = case.missing_hard_stops()
        if missing:
            return self._blocked_result(case, missing, started)

        # --- §6.2 trajectory construction ------------------------------------
        scenario_points = build_trajectory(case)
        # The trajectory's 60% drawdown screen must use the actual IPR when
        # its linear PI estimate exceeds AOF. Keep the requested target intact
        # and disclose the lower evaluation rate instead of calling it missing data.
        from .inflow import rate_for_pwf
        adjusted=[]
        for point in scenario_points:
            spec=self._inflow_spec(case,point)
            oil_fraction=1-point.water_cut_frac
            if point.target_rate_bpd*oil_fraction > absolute_open_flow_bpd(spec):
                evaluation_rate=rate_for_pwf(spec,point.reservoir_pressure_psi*.4)/oil_fraction
                adjusted.append(point.model_copy(update={
                    "achievable_rate_bpd":min(point.achievable_rate_bpd,evaluation_rate),
                    "evaluation_rate_bpd":evaluation_rate,
                }))
                warnings.append("Trajectory evaluation rate is limited by the modeled IPR at the existing 60% drawdown screen; the requested target remains unchanged and is not established.")
            else: adjusted.append(point)
        scenario_points=adjusted
        base_month0 = self._find_point(scenario_points, 0.0, EnvelopeCase.BASE)
        if base_month0 is None:
            return self._blocked_result(
                case, ["Trajectory produced no base-case month-0 point."], started
            )

        target_rate = case.expectations.target_rate_bpd.value  # hard stop, present

        # --- Is the target above the well's absolute open flow? ---------------
        # AOF is the most the reservoir can produce at zero backpressure. A target
        # above it is unachievable by any pump, and saying so is a legitimate and
        # valuable output (§3.1) — far more useful than a list of rejected pumps.
        try:
            aof = absolute_open_flow_bpd(self._inflow_spec(case, base_month0))
        except Exception:  # noqa: BLE001
            aof = None
        if aof is not None and target_rate*(1-base_month0.water_cut_frac) > aof:
            warnings.append(f"Target oil component {target_rate*(1-base_month0.water_cut_frac):.0f} bpd exceeds calculated oil AOF {aof:.0f} bpd. Candidates remain selectable at the disclosed lower evaluation rate; target delivery is not established.")

        # --- Does ANY pump in the catalog physically fit? ---------------------
        # Checked before screening hydraulics, because "nothing fits this casing"
        # is a geometry answer, and routing it through a hydraulics exception
        # would report it as missing data — telling the engineer to go collect
        # information that would not change the outcome.
        min_id = case.geometry.min_casing_id_to_depth(
            resolve_setting_depth(case)[0]
        )
        clearance = cfg.thresholds.geometry.min_radial_clearance_in
        smallest_pump_od = min(
            (p.housing_od_in for p in self.catalog.pumps), default=None
        )
        if (
            min_id is not None
            and smallest_pump_od is not None
            and smallest_pump_od >= min_id
        ):
            return self._no_fit_result(
                case,
                started,
                min_id=min_id,
                smallest_pump_od=smallest_pump_od,
                clearance=clearance,
            )

        # --- §12 step 4: screening TDH, to size the stage-count bracket ------
        screening_depth, _ = resolve_setting_depth(case)
        # The screening gas assessment needs a nominal equipment OD before a pump
        # is chosen. Use the smallest pump that fits rather than a hardcoded
        # value, so a narrow casing does not fail screening on an OD no candidate
        # would ever have used.
        screening_od = min(
            (
                p.housing_od_in
                for p in self.catalog.pumps
                if min_id is None or p.housing_od_in < min_id
            ),
            default=smallest_pump_od or 5.62,
        )
        try:
            screening_intake = self._intake_for(case, base_month0, screening_depth)
            screening_gas = self._gas_for(
                case, screening_intake, screening_depth, screening_od
            )
            screening_tdh = self._tdh_for(case, screening_intake, screening_depth)
        except Exception as exc:  # noqa: BLE001 — a screening failure is a real answer
            return self._blocked_result(
                case,
                [
                    f"Screening hydraulics could not be solved: {exc}. The well "
                    f"and fluid description is inconsistent."
                ],
                started,
            )

        # --- TDH <= 0 is an answer, not an error -----------------------------
        # Total dynamic head at or below zero means the reservoir already delivers
        # the fluid to the wellhead at the required pressure. Either the well does
        # not need lift at this condition, or the rate the reservoir can actually
        # deliver is far below the target and the drawdown assumption collapsed.
        # Both are legitimate outputs (§3.1); neither is a crash.
        if screening_tdh.tdh_ft <= 0.0:
            return self._no_lift_required_result(
                case,
                started,
                tdh_ft=screening_tdh.tdh_ft,
                achievable_rate_bpd=base_month0.achievable_rate_bpd,
                target_rate_bpd=target_rate,
                pip_psi=screening_intake.pip_psi,
                depth_ft=screening_depth,
            )

        # --- §12 step 5: pump selection (enumeration) ------------------------
        enumeration = enumerate_candidates(
            case=case,
            catalog=self.catalog,
            cfg=cfg,
            tdh_estimate_ft=screening_tdh.tdh_ft,
            design_rate_bpd=target_rate,
            gas_degradation_estimate=screening_gas.head_degradation_factor or 1.0,
        )
        configs_enumerated = len(enumeration.candidates)
        candidates = dedupe_by_hydraulic_equivalence(
            enumeration.candidates,
            target_rate,
            reference_head_ft=screening_tdh.tdh_ft,
        )
        rejected = list(enumeration.rejected)

        if not candidates:
            return self._infeasible_result(
                case, enumeration, scenario_points, started, warnings
            )

        # --- Stage A: month-0 base case only, then prune ---------------------
        survivors: list[tuple[PumpConfiguration, PumpModel, ScaledCurve, CellResult]] = []
        cells_evaluated = 0

        for config, pump, curve in candidates:
            if (not case.constraints.electrical.vsd_available.value
                and self.catalog.motors and all(m.vsd_required for m in self.catalog.motors)):
                rejected.append(self._rejection(config,"constraints_gate_absolute",[
                    ConstraintViolation(constraint_type="electrical",rigidity="hard",
                        physical_impossibility=True,message="All available motors are permanent magnet and no VSD is available.",
                        remedy_hint="Provide a PMM-capable VSD or a compatible induction motor.")
                ]))
                continue
            restriction=case.geometry.min_casing_id_to_depth(config.setting_depth_md_ft)
            from .cable import _installed_od_in
            eligible_motors=[m for m in self.catalog.motors if case.constraints.electrical.vsd_available.value or not m.vsd_required]
            available_profiles=[_installed_od_in(c) for c in self.catalog.cables if not case.engineering.cable_id or c.id==case.engineering.cable_id]
            minimum_od=max(pump.housing_od_in,min((m.od_in for m in eligible_motors),default=pump.housing_od_in))
            known_nonfit=restriction is not None and (
                (bool(eligible_motors) and all(m.od_in>=restriction for m in eligible_motors))
                or (bool(available_profiles) and all(p is not None and minimum_od+p>=restriction for p in available_profiles))
            )
            if known_nonfit:
                rejected.append(self._rejection(config,"geometry_prescreen",[
                    ConstraintViolation(constraint_type="geometry",rigidity="absolute",physical_impossibility=True,
                        message="Known motor/cable envelope cannot pass the casing restriction; checked even when hydraulics are off-curve.",
                        actual_value=minimum_od+min(p for p in available_profiles if p is not None) if available_profiles and all(p is not None for p in available_profiles) else minimum_od,
                        limit_value=restriction,unit="in",
                        remedy_hint="Supply a physically fitting motor/cable assembly.")
                ]))
                continue
            cell, sizing = self._evaluate_cell(
                case=case,
                config=config,
                pump=pump,
                curve=curve,
                point=base_month0,
                size_equipment=True,
            )
            cells_evaluated += 1

            if not gate_allows_ranking(cell.gate_status):
                stage = (
                    "constraints_gate_absolute"
                    if cell.gate_status is GateStatus.FAILED_ABSOLUTE
                    else "constraints_gate_hard"
                )
                rejected.append(
                    self._rejection(config, stage, cell.violations)
                )
                continue

            # Carry the sized equipment forward onto the configuration so the
            # string is fully specified before trajectory evaluation.
            enriched = self._enrich_config(config, sizing)
            survivors.append((enriched, pump, curve, cell))

        if not survivors:
            return self._infeasible_result(
                case,
                enumeration,
                scenario_points,
                started,
                warnings,
                extra_rejected=rejected,
                cells_evaluated=cells_evaluated,
            )

        # Rank by month-0 proximity to BEP and keep the top slice for the
        # expensive full-trajectory pass. Framework §6.3 wants the trajectory to
        # decide the winner, so this cut is generous — it removes configurations
        # already off-curve at day one, not merely suboptimal ones.
        survivors.sort(key=lambda s: abs(s[3].q_over_qbep - 1.0))
        deep_pool = survivors

        # --- Stage B: full trajectory for the survivors ----------------------
        results: list[CandidateResult] = []
        horizon = case.trajectory.horizon_months

        for config, pump, curve, month0_cell in deep_pool:
            cells: list[CellResult] = []
            for point in scenario_points:
                if point.point_id == base_month0.point_id:
                    cells.append(month0_cell)
                    continue
                cell, _ = self._evaluate_cell(
                    case=case,
                    config=config,
                    pump=pump,
                    curve=curve,
                    point=point,
                    size_equipment=False,
                )
                cells.append(cell)
                cells_evaluated += 1

            by_envelope: dict[EnvelopeCase, list[CellResult]] = {}
            for cell in cells:
                by_envelope.setdefault(cell.envelope, []).append(cell)

            validity = compute_validity_boundary(by_envelope, horizon)

            # --- §6.4 VSD recovery -------------------------------------------
            vsd_recovery = None
            soft = prescreen_availability(
                manufacturer=pump.manufacturer, pump_model=pump.model, case=case
            )
            score = score_candidate(
                cells,
                cfg.scoring,
                validity=validity,
                vsd_recovery=vsd_recovery,
                horizon_months=horizon,
                has_soft_violations=bool(soft),
            )

            base_cells = by_envelope.get(EnvelopeCase.BASE, cells)
            string_summary = assemble_string(config, self.catalog)

            # --- B.16 motor-type fork, aggregated from the cells -------------
            # Motor selection can differ across the trajectory, so the fork is
            # reported as "any cell" rather than assumed constant. A PMM
            # anywhere in the trajectory means the design needs a VSD and
            # carries a magnet gate.
            cell_motor_types = {c.motor_type for c in cells if c.motor_type}
            candidate_motor_type = (
                cell_motor_types.pop() if len(cell_motor_types) == 1 else None
            )
            requires_vsd = any(c.motor_vsd_required for c in cells)
            magnet_unverified = any(
                c.motor_magnet_gate
                in ("limit_not_published", "indeterminate_needs_vendor_thermal_model")
                for c in cells
            )

            results.append(
                CandidateResult(
                    rank=0,  # assigned after the sort
                    configuration=config,
                    score=score,
                    validity=validity,
                    vsd_recovery=vsd_recovery,
                    timeline=build_timeline(base_cells),
                    design_point=month0_cell,
                    cells_sampled=cells,
                    soft_violations=soft + month0_cell.violations,
                    synthetic=case.synthetic or pump.synthetic,
                    warnings=sorted({w for c in cells for w in c.warnings}),
                    depends_on_estimated_catalog_data=is_estimated_record(pump),
                    motor_type=candidate_motor_type,
                    requires_vsd=requires_vsd,
                    uses_operator_supplied_motor_data=any(
                        c.motor_uses_operator_supplied_data for c in cells
                    ),
                    amps_basis=next(
                        (c.motor_amps_basis for c in cells if c.motor_amps_basis), None
                    ),
                    nameplate_amps_conflict=any(
                        c.motor_nameplate_amps_conflict for c in cells
                    ),
                    # Read from the design point, not from all cells. Equipment
                    # sizing runs once at the design point by design (see
                    # _evaluate_cell: the string is fixed and the well moves
                    # around it), so the other trajectory cells carry no
                    # mechanical check and an all()/any() over cells would
                    # report the check as never performed.
                    shaft_fracture_risk=month0_cell.shaft_fracture_risk,
                    shaft_nameplate_check_performed=month0_cell.shaft_nameplate_check_possible,
                    # Same reasoning for cooling: the annular velocity is a
                    # property of the sized string at the design point.
                    cooling_verdict=month0_cell.cooling_verdict,
                    cooling_velocity_ft_s=month0_cell.cooling_velocity_ft_s,
                    cooling_floor_applied_ft_s=month0_cell.cooling_floor_applied_ft_s,
                    cooling_upper_bound_checked=month0_cell.cooling_upper_bound_checked,
                    self_heating=month0_cell.motor_self_heating,
                    cable_self_heating=month0_cell.cable_self_heating,
                    motor_operating_current=month0_cell.motor_operating_current,
                    magnet_thermal_limit_unverified=magnet_unverified,
                    string_summary=string_summary,
                )
            )

        from .expert_decisions import rank_candidates, engineering_details
        results, ranking_comparison = rank_candidates(results, case, self.catalog, cfg)
        results = [
            r.model_copy(update={"rank": i})
            for i, r in enumerate(
                results[: cfg.enumeration.max_reported_candidates], start=1
            )
        ]

        # --- Framework 6B.2: is the catalog curve sufficient? -----------------
        # Runs after ranking, on the presented candidates only, and deliberately
        # after scoring: a tolerance band is uncertainty about a number, not a
        # defect in a design, so it must not reach the score or the gates. Its
        # single output is a recommendation about whether to spend the time
        # loading a unit test report.
        results = [self._with_tolerance(r) for r in results]
        annotated = []
        for r in results:
            pump = self.catalog.pump(r.configuration.pump_id)
            recovery = None
            if case.constraints.electrical.vsd_available.value:
                recovery = compute_vsd_recovery(
                    vsd_available=True,
                    frequency_band_hz=case.constraints.electrical.frequency_band(),
                    base_validity=r.validity,
                    recovered_cells_by_frequency=self._probe_frequency_recovery(
                        case=case, config=r.configuration, pump=pump,
                        scenario_points=scenario_points, validity_boundary=r.validity.valid_until_months,
                    ), horizon_months=horizon,
                )
            annotated.append(r.model_copy(update={
                "vsd_recovery": recovery,
                "engineering": engineering_details(self, case, r, base_month0),
            }))
        results = annotated
        ranking_stability = assess_ranking_stability(
            results, objective=cfg.scoring.objective
        )

        verdict, explanation = self._verdict(results, case)
        if base_month0.evaluation_rate_bpd is not None:
            verdict=FeasibilityVerdict.FEASIBLE_WITH_CAVEATS
            explanation=f"Requested target {target_rate:.0f} bpd exceeds modeled deliverability. Configurations are compared at a lower screening rate of {base_month0.evaluation_rate_bpd:.0f} bpd, not validated at the requested target. "+explanation
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        data_requests = self._data_requests(case)
        for candidate in results:
            request = self._test_report_request(candidate)
            if request is not None:
                data_requests.append(request)
        if any(r.magnet_thermal_limit_unverified for r in results):
            data_requests.append(
                "Magnet demagnetization temperature for the selected permanent "
                "magnet motor, and its partial-load efficiency and power factor "
                "curves. The demagnetization limit is a B.16 hard gate this "
                "catalog cannot evaluate, and demagnetization is irreversible "
                "rather than gradual. The PF and efficiency curves unlock the "
                "B.14.1 full-load-current form, which currently falls back to "
                "linear nameplate scaling and therefore propagates into cable "
                "gauge and surface voltage."
            )

        if any(r.nameplate_amps_conflict for r in results):
            data_requests.append(
                "Vendor full-load current at the design voltage for the selected "
                "motor. The cataloged HP / volts / amps triple back-solves to a "
                "power factor x efficiency well below any plausible ESP value, so "
                "the B.14.1 current computed from real PF and efficiency data "
                "lands roughly 35% below the cataloged nameplate current. The two "
                "cannot both be full-load quantities for the same machine. The "
                "higher was used so the cable is not undersized, but that means "
                "supplying PF and efficiency currently buys no improvement in "
                "cable gauge or surface voltage -- resolving this contradiction "
                "is what unlocks it."
            )

        return DesignResult(
            design_id=str(uuid.uuid4()),
            case_id=case.metadata.case_id,
            tenant_id=case.metadata.tenant_id,
            verdict=verdict,
            verdict_explanation=explanation,
            branch=branch,
            branch_rationale=case.branch_rationale,
            candidates=results,
            rejected=rejected[: cfg.enumeration.max_reported_rejections],
            rejection_summary=self._summarize(rejected),
            scenario_points=scenario_points,
            assumption_ledger=self._build_ledger(case),
            trust_register=self._trust_register(case),
            responsibility_boundary_crossings=self._boundary_crossings(case),
            ranking_stability=ranking_stability,
            blocking_data_gaps=[],
            recommended_data_requests=data_requests,
            input_confidence=case.overall_input_confidence,
            uses_estimated_catalog_data=any(
                r.depends_on_estimated_catalog_data for r in results
            ),
            uses_operator_supplied_motor_data=any(
                r.uses_operator_supplied_motor_data for r in results
            ),
            has_nameplate_amps_conflict=any(
                r.nameplate_amps_conflict for r in results
            ),
            has_shaft_fracture_risk=any(r.shaft_fracture_risk for r in results),
            requires_magnet_thermal_verification=any(
                r.magnet_thermal_limit_unverified for r in results
            ),
            provenance=RunProvenance(
                case_hash=case.content_hash(),
                config_hash=cfg.config_hash(),
                catalog_version=self.catalog.version,
                engine_version=ENGINE_VERSION,
                computed_at=datetime.now(timezone.utc).isoformat(),
                compute_ms=elapsed_ms,
                cells_evaluated=cells_evaluated,
                configs_enumerated=configs_enumerated,
                configs_surviving=len(survivors),
            ),
            engine_warnings=warnings + self._catalog_warnings(results),
            ranking_comparison=ranking_comparison,
            synthetic=case.synthetic or any(p.synthetic for p in self.catalog.pumps),
        )

    # =========================================================================
    # One cell — the innermost loop of the whole system
    # =========================================================================

    def _with_tolerance(self, candidate: CandidateResult) -> CandidateResult:
        """Attach the 6B.2 acceptance-band assessment to a ranked candidate.

        The curve is rebuilt rather than threaded through from ``_evaluate_cell``
        so that the assessment reads exactly what the catalog publishes today,
        with no chance of inheriting a value that was adjusted mid-run.
        """
        from .curves import scale_curve_to_frequency

        cfg = self.config
        try:
            pump = self.catalog.pump(candidate.configuration.pump_id)
            curve = scale_curve_to_frequency(
                pump,
                candidate.configuration.frequency_hz,
                cfg.correlations.frequency_curves,
            )
        except (KeyError, ValueError):
            # A candidate whose curve cannot be rebuilt gets no assessment
            # rather than an empty one that reads as "nothing found".
            return candidate
        assessment = assess_candidate_tolerance(
            candidate,
            pump=pump,
            curve=curve,
            thresholds=cfg.thresholds,
            thrust_zone_method=cfg.correlations.thrust_zones,
            objective=cfg.scoring.objective,
            zone_weights=cfg.scoring.zone_weights,
        )
        return candidate.model_copy(update={"tolerance": assessment})

    @staticmethod
    def _test_report_request(candidate: CandidateResult) -> str | None:
        """Turn a material tolerance finding into a specific, justified ask."""
        assessment = candidate.tolerance
        if assessment is None or not assessment.test_report_recommended:
            return None
        channels = ", ".join(c.value for c in assessment.material_channels)
        return (
            f"Unit test report (S/N, not model) for {assessment.pump_id} on "
            f"candidate rank {candidate.rank}. Inside the "
            f"{assessment.standard} acceptance band the catalog curve does not "
            f"settle: {channels}. A pump that passes acceptance testing can "
            f"land on either side of these, so the actual curve of the unit "
            f"being installed is what decides them (6B.2)."
        )

    def _evaluate_cell(
        self,
        *,
        case: Case,
        config: PumpConfiguration,
        pump: PumpModel,
        curve: ScaledCurve,
        point: ScenarioPoint,
        size_equipment: bool,
    ) -> tuple[CellResult, dict]:
        """Evaluate one configuration at one scenario point.

        Runs §12 steps 4 through 11 for a single (config, time, envelope) cell,
        ending at the constraints gate. Equipment sizing (motor/seal/cable) is
        performed once at the design point and reused across the trajectory —
        re-sizing per month would model a string that gets swapped as the well
        depletes, which is not what happens; the string is fixed and the well
        moves around it. That is precisely the risk §6 is about.
        """
        cfg = self.config
        violations: list[ConstraintViolation] = []
        warnings: list[str] = []
        sizing: dict = {}
        depth = config.setting_depth_md_ft
        violations.extend(prescreen_geometry(
            equipment_od_in=pump.housing_od_in, setting_depth_md_ft=depth,
            geometry=case.geometry, thresholds=cfg.thresholds,
        ))

        # --- Intake conditions (cached per well state, not per pump) ---------
        try:
            intake = self._intake_for(case, point, depth)
        except Exception as exc:  # noqa: BLE001
            return (
                self._nonconverged_cell(config, point, f"intake solve failed: {exc}"),
                sizing,
            )

        # --- §12 step 6: gas handling ----------------------------------------
        gas = self._gas_for(case, intake, depth, pump.housing_od_in, pump.stage_type)
        if gas.correction_missing:
            return self._nonconverged_cell(config, point, "Gas correction is uncomputed: supply Kq/Kh above the numeric table range."), {}
        if gas.recommended_strategy is GasStrategy.NOT_ESP_CANDIDATE:
            violations.append(
                ConstraintViolation(
                    constraint_type="hydraulic",
                    rigidity="absolute",
                    message=(
                        f"Free gas at intake reaches {gas.fgvf_at_intake:.1%}; no "
                        f"available gas-handling strategy makes centrifugal lift "
                        f"viable at this condition. {gas.rationale}"
                    ),
                    remedy_hint=(
                        "Set the pump deeper to raise intake pressure, or evaluate "
                        "gas lift / a different lift method."
                    ),
                )
            )
        warnings.extend(gas.warnings)

        # --- §12 step 4: TDH -------------------------------------------------
        tdh = self._tdh_for(case, intake, depth)

        # --- Pump performance at this condition ------------------------------
        q = intake.total_fluid_intake_bpd
        # Gas-corrected Q-H curve: H_g(Q) = Kh * H_water(Q/Kq).
        q_curve = q / gas.kq

        if not rate_within_domain(curve, q_curve):
            # The operating point has drifted off the fitted curve entirely. This
            # is a real, reportable outcome -- "this pump runs out of curve at
            # month 14" -- and the one thing that must not happen is filling the
            # cell with an extrapolated number.
            side = (
                OperatingZone.OFF_CURVE_LEFT
                if q < curve.q_min_bpd
                else OperatingZone.OFF_CURVE_RIGHT
            )
            return (
                self._off_curve_cell(
                    case=case,
                    config=config,
                    point=point,
                    intake=intake,
                    tdh=tdh,
                    gas=gas,
                    zone=side,
                    q_bpd=q,
                    curve=curve,
                    warnings=warnings,
                ),
                {},
            )

        stage_point = evaluate_stage(curve, q_curve)
        stage_point = apply_viscosity_correction(
            stage_point,
            q_bpd=q,
            viscosity_cp=intake.mixture_viscosity_cp,
            head_ft_per_stage=stage_point.head_ft_per_stage,
            bep_q_bpd=curve.bep_q_bpd,
            method=cfg.correlations.viscosity_correction,
        )
        degradation = gas.head_degradation_factor
        head_developed = total_head_ft(curve, q_curve, config.stages, degradation)
        bhp = total_bhp(curve, q_curve, config.stages, intake.mixture_sg, degradation)

        zone = classify_zone(curve, q_curve, cfg.correlations.thrust_zones, cfg.thresholds)

        head_margin = (
            (head_developed - tdh.tdh_ft) / tdh.tdh_ft if tdh.tdh_ft > 0 else 0.0
        )

        # --- §12 steps 7-9: motor, seal, cable -------------------------------
        motor: MotorSizing | None = None
        cable: CableSizing | None = None
        mech: MechanicalCheck | None = None
        casing_id = case.geometry.min_casing_id_to_depth(depth) or pump.min_casing_id_in

        # Recheck the ORIGINAL installed equipment at every trajectory point
        # and VSD trial. A restricted catalog prevents silent motor/cable swaps.
        equipment_catalog = self.catalog
        if not size_equipment:
            equipment_catalog = self.catalog.model_copy(update={
                "motors": [m for m in self.catalog.motors if m.id == config.motor_id],
                "cables": [c for c in self.catalog.cables if c.id == config.cable_id],
                "seals": [s for s in self.catalog.seals if s.id == config.seal_id],
            })
        if size_equipment:
            gh_hp = self._gas_handling_hp(gas, pump.series)
        else:
            device = next((d for d in self.catalog.gas_handling if d.id == config.gas_handling_id), None)
            gh_hp = device.hp_consumed if device is not None else 0.0
            if gas.recommended_strategy is not GasStrategy.NONE and device is None:
                violations.append(ConstraintViolation(
                    constraint_type="hydraulic", rigidity="hard",
                    message="Gas handling is required at this point but no gas device is installed.",
                ))
        seal = next(iter(equipment_catalog.seals_for_series(pump.series)), None)
        protector_hp = 2.0 if seal else 1.0

        motor_kwargs = dict(
            catalog=equipment_catalog,
            bhp_pump=bhp,
            gas_handling_hp=gh_hp,
            protector_loss_hp=protector_hp,
            max_od_in=casing_id,
            frequency_hz=config.frequency_hz,
            casing_id_in=casing_id,
            total_fluid_rate_bpd=q,
            intake_temp_f=intake.intake_temp_f,
            thresholds=cfg.thresholds.motor,
            # B.14.1 / B.16: operator-held vendor data the public catalog
            # does not publish. Passed as the whole constraint block rather
            # than a single value because power factor is a property of a
            # specific machine, and size_motor does not know which motor it
            # will pick until after it has picked it.
            overrides=case.constraints.electrical,
            # Framework 6D.2 cooling range. Viscosity classification comes
            # from the DECLARED complication, not from the viscosity number:
            # the cp threshold at which the 1 ft/s rule stops holding is
            # vendor-specific and uncataloged (framework 13, open). The
            # number is passed too so that a viscosity on file with no
            # declaration is disclosed rather than quietly ignored.
            viscous_oil_declared=case.complications.has(
                ComplicationType.VISCOUS_OIL
            ),
            oil_viscosity_cp=(
                case.fluid.oil_viscosity_cp.value
                if case.fluid.oil_viscosity_cp is not None
                else None
            ),
            # The two mechanisms that SET the upper bound. These do not move
            # the verdict on their own; they record whether an uncataloged
            # upper bound is material for this well or merely hypothetical.
            solids_present=(
                case.complications.has(ComplicationType.SOLIDS)
                or (
                    case.fluid.sand_pptb is not None
                    and case.fluid.sand_pptb.value > 0
                )
            ),
            gas_separated_to_annulus=(
                gas.fgvf_at_intake > 0.0
                and gas.natural_separation_efficiency > 0.0
            ),
            # Framework §6D.2 self-heating anchor: water cut sets the
            # water/oil rise blend, and FGVF at intake reduces the liquid
            # mass past the stator. Both are already computed at this
            # point; passing them here keeps the thermal calculation as a
            # pure function of the point-wise fluid state rather than
            # letting motor.py reach back into pipeline internals.
            water_cut_frac=point.water_cut_frac if point.water_cut_frac is not None else 1.0,
            free_gas_fraction_at_intake=gas.fgvf_at_intake,
        )
        motor = size_motor(series=pump.series, **motor_kwargs)
        motor_series_unverified = False
        if motor is None:
            # No same-series motor. Rather than declare the design impossible
            # on the strength of a compatibility table this catalog does not
            # contain, fall back to a physical OD fit and label the series
            # mismatch as unverified. The engineer then knows exactly what to
            # check with the vendor, instead of being told "no motor exists"
            # when what is actually missing is a coupling datasheet.
            motor = size_motor(series=None, **motor_kwargs)
            motor_series_unverified = motor is not None

        if motor is not None and motor_series_unverified:
            violations.append(
                ConstraintViolation(
                    constraint_type="availability",
                    rigidity="soft",
                    message=(
                        f"No series-{pump.series} motor in the catalog fits this "
                        f"casing at the required {motor.hp_required:.1f} hp, so a "
                        f"series-{motor.motor.series} motor "
                        f"({motor.motor.od_in:.2f} in OD) was selected on "
                        "physical fit alone. Pump-to-motor series compatibility "
                        "is NOT verified — no public datasheet in this catalog "
                        "states cross-series coupling or adapter availability."
                    ),
                    remedy_hint=(
                        "Confirm with the vendor that this pump and motor "
                        "series couple, or extend the catalog with a "
                        "same-series motor."
                    ),
                )
            )

        if motor is None:
            violations.append(
                ConstraintViolation(
                    constraint_type="mechanical",
                    rigidity="hard",
                    message=(
                        f"No series-{pump.series} motor in the catalog can "
                        f"deliver the required {bhp + gh_hp + protector_hp:.1f} "
                        f"hp within the {casing_id:.3f} in casing at "
                        f"{config.frequency_hz:g} Hz."
                    ),
                    remedy_hint=(
                        "Reduce stage count, use a tandem motor, or select a "
                        "higher-efficiency pump."
                    ),
                )
            )
        else:
            warnings.extend(motor.warnings)
            if not motor.loading_in_target_band:
                band = cfg.thresholds.motor
                violations.append(
                    ConstraintViolation(
                        constraint_type="mechanical",
                        rigidity="soft"
                        if motor.loading_frac < band.target_loading_min
                        else "hard",
                        message=(
                            f"Motor loading is {motor.loading_frac:.0%}, outside "
                            f"the {band.target_loading_min:.0%}-"
                            f"{band.target_loading_max:.0%} target band (§12)."
                        ),
                        actual_value=motor.loading_frac,
                        limit_value=band.target_loading_max,
                        unit="fraction",
                        remedy_hint=(
                            "Select the next motor size down"
                            if motor.loading_frac < band.target_loading_min
                            else "Select a larger motor or reduce stage count"
                        ),
                    )
                )
            if not motor.cooling_adequate:
                cool = motor.cooling
                # constraint_type is "thermal", not "mechanical". Inadequate
                # cooling is a heat-rejection failure; filing it under
                # mechanical put it in the same bucket as shaft and stage
                # limits, so a thermal filter over the violations missed the
                # one constraint that most often eliminates a whole
                # candidate set.
                if cool is not None and cool.verdict is CoolingVerdict.WITHIN_VISCOUS_BAND:
                    assert cool.viscous_band_ft_s is not None
                    low, high = cool.viscous_band_ft_s
                    message = (
                        f"Motor cooling is INDETERMINATE for a viscous fluid: "
                        f"annular velocity is {cool.velocity_ft_s:.2f} ft/s, "
                        f"inside the published {low:g}-{high:g} ft/s band rather "
                        f"than above it. The source publishes a range, so no "
                        f"midpoint was substituted and adequacy is unresolved "
                        f"at {intake.intake_temp_f:.0f} F intake temperature."
                    )
                else:
                    floor_text = (
                        f"the {cool.floor_applied_ft_s:g} ft/s viscous-oil floor "
                        f"(the 1 ft/s rule is inadequate for viscous fluids)"
                        if cool is not None
                        and cool.floor_basis is CoolingFloorBasis.VISCOUS_DECLARED
                        else (
                            f"the {cool.floor_applied_ft_s:g} ft/s minimum"
                            if cool is not None
                            else "the configured minimum"
                        )
                    )
                    message = (
                        f"Motor cooling velocity is "
                        f"{motor.cooling_velocity_ft_s:.2f} ft/s, below "
                        f"{floor_text} for adequate heat rejection at "
                        f"{intake.intake_temp_f:.0f} F intake temperature."
                    )
                violations.append(
                    ConstraintViolation(
                        constraint_type="thermal",
                        rigidity="hard",
                        message=message,
                        actual_value=motor.cooling_velocity_ft_s,
                        limit_value=(
                            cool.floor_applied_ft_s if cool is not None else None
                        ),
                        unit="ft/s",
                        remedy_hint=(
                            "Add a motor shroud -- the primary measure, "
                            "particularly in deviated and horizontal wells with "
                            "perforations above the pump -- or select a larger "
                            "motor OD to narrow the annular clearance. Production "
                            "rate is not a lever: it is set by the customer."
                        ),
                    )
                )

            # --- §6D.2 motor winding temperature -----------------------
            # Framework: "Check against motor temperature rating" is the
            # HARD gate row of the §6D.2 display table. Before this slice
            # the pipeline only checked cooling velocity, which is a
            # heat-transfer-coefficient proxy, not the temperature itself.
            # A configuration can clear the velocity minimum and still
            # exceed the winding rating (high load, high intake temp,
            # oil-dominant fluid, gas present). The winding-temperature
            # gate closes that.
            thermal_limit_f = min(
                motor.motor.max_winding_temp_f,
                cfg.thresholds.motor.max_winding_temp_f,
            )
            if motor.estimated_winding_temp_f > thermal_limit_f:
                sh = motor.self_heating
                violations.append(
                    ConstraintViolation(
                        constraint_type="thermal",
                        rigidity="hard",
                        message=(
                            f"Motor winding temperature {motor.estimated_winding_temp_f:.0f} F "
                            f"exceeds the {thermal_limit_f:.0f} F rating: "
                            f"fluid {sh.fluid_temp_f:.0f} F + self-heating "
                            f"{sh.rise_f:.0f} F (§6D.2). Displayed as two "
                            f"components because a shroud helps the "
                            f"self-heating term and a high-temperature "
                            f"build helps the rating term."
                        ),
                        actual_value=motor.estimated_winding_temp_f,
                        limit_value=thermal_limit_f,
                        unit="F",
                        remedy_hint=(
                            "If self-heating is the dominant contribution, "
                            "add a motor shroud to raise annular velocity. "
                            "If the fluid is already near the rating, "
                            "select a high-temperature (Class H) build."
                        ),
                    )
                )

            # --- B.16 permanent-magnet fork --------------------------
            # A PMM cannot start across the line. If the case says no VSD
            # is available, this configuration is not runnable as specified
            # regardless of how well it performs hydraulically.
            if motor.vsd_required and not case.constraints.electrical.vsd_available.value:
                violations.append(
                    ConstraintViolation(
                        constraint_type="electrical",
                        rigidity="hard",
                        physical_impossibility=True,
                        message=(
                            f"{motor.motor.id} is a permanent magnet motor and "
                            "cannot start across the line, but this case states "
                            "no VSD is available. B.16 makes a VSD with PMM "
                            "rotor-position control mandatory hardware for this "
                            "motor, not an optimization."
                        ),
                        remedy_hint=(
                            "Provide a PMM-capable VSD, or select an induction "
                            "motor that can run on a switchboard."
                        ),
                    )
                )
            if motor.magnet_gate == "exceeded_at_intake_temperature":
                violations.append(
                    ConstraintViolation(
                        constraint_type="thermal",
                        rigidity="hard",
                        message=(
                            f"{motor.motor.id} magnet demagnetization limit "
                            f"{motor.magnet_demag_limit_f:.0f} F is exceeded by "
                            f"intake temperature {intake.intake_temp_f:.0f} F. "
                            "Per B.16 demagnetization is irreversible, unlike "
                            "induction winding insulation which degrades "
                            "gradually, so this is not a margin to be accepted."
                        ),
                        actual_value=intake.intake_temp_f,
                        limit_value=motor.magnet_demag_limit_f,
                        unit="F",
                        remedy_hint=(
                            "Select a higher-temperature magnet option, an "
                            "induction motor, or set the pump shallower."
                        ),
                    )
                )

            cable_reference_current=max(motor.motor.amps,motor.full_load_amps_b14_1 or 0,motor.operating_amps)
            if size_equipment and case.constraints.electrical.vsd_available.value:
                from .curves import scale_curve_to_frequency
                maximum=case.constraints.electrical.frequency_band()[1]
                high_curve=scale_curve_to_frequency(pump,maximum,cfg.correlations.frequency_curves)
                if rate_within_domain(high_curve,q_curve):
                    high_kwargs={**motor_kwargs,
                        "catalog":equipment_catalog.model_copy(update={"motors":[motor.motor]}),
                        "frequency_hz":maximum,
                        "bhp_pump":total_bhp(high_curve,q_curve,config.stages,intake.mixture_sg,degradation)}
                    high_motor=size_motor(series=None,**high_kwargs)
                    if high_motor:
                        cable_reference_current=max(cable_reference_current,high_motor.operating_amps)
                else:
                    warnings.append("Maximum-frequency motor current is uncomputed outside the curve domain; cable worst-case coverage is incomplete.")
            cable = size_cable(
                catalog=equipment_catalog,
                motor=motor,
                setting_depth_md_ft=depth,
                casing_id_in=casing_id,
                pump_od_in=pump.housing_od_in,
                fluid_temp_f=intake.intake_temp_f,
                cooling_velocity_ft_s=motor.cooling_velocity_ft_s,
                water_cut_frac=point.water_cut_frac,
                thresholds=cfg.thresholds.electrical,
                sizing_current_a=cable_reference_current if size_equipment else None,
                selected_cable_id=case.engineering.cable_id if size_equipment else config.cable_id,
                available_surface_voltage_v=(
                    case.constraints.electrical.available_surface_voltage_v.value
                    if case.constraints.electrical.available_surface_voltage_v
                    else None
                ),
            )
            if cable is None:
                from .cable import _installed_od_in
                profiles=[_installed_od_in(c) for c in equipment_catalog.cables
                    if not case.engineering.cable_id or c.id==case.engineering.cable_id]
                known_nonfit=bool(profiles) and all(p is not None and max(pump.housing_od_in,motor.motor.od_in)+p >= casing_id for p in profiles)
                violations.append(
                    ConstraintViolation(
                        constraint_type="electrical",
                        rigidity="hard",
                        physical_impossibility=known_nonfit,
                        message=(
                            "No cable has a verified physical fit in this assembly; "
                            "this is not rejection for current, voltage drop or temperature."
                        ),
                        actual_value=min(profiles)+max(pump.housing_od_in,motor.motor.od_in) if known_nonfit else None,
                        limit_value=casing_id,unit="in",
                        remedy_hint=(
                            "Select a higher-voltage / lower-current motor to "
                            "reduce cable size, or add a shroud to raise annular "
                            "cooling velocity so the conductor rise drops."
                        ),
                    )
                )
            else:
                warnings.extend(cable.warnings)
                chosen_row=next(x for x in cable.decision_surface if x["selected"])
                for finding in chosen_row["warnings"]:
                    violations.append(ConstraintViolation(
                        constraint_type="thermal" if finding["quantity"]=="insulation_buffer" else "electrical",
                        rigidity="soft", message=f"Cable {finding['quantity']}: advisory limit exceeded; engineer may retain the selected gauge.",
                        actual_value=finding["actual"],limit_value=finding["limit"],unit=finding["unit"],
                    ))
                # --- §6D.2 cable calculated temperature -----------------
                # Framework: "Calculated cable temperature | Check against
                # insulation class (B.14.5)". The screen inside size_cable
                # already rejects candidates whose conductor exceeds the
                # cable rating, so a returned cable is always inside its
                # own rating. This gate additionally enforces the
                # engine-wide insulation ceiling (configured max) as a
                # HARD constraint, so a case-level override of the cable
                # temperature limit stays visible in violations rather
                # than passing silently.
                csh = cable.self_heating
                if csh.conductor_temp_f > cable.cable.max_temp_f:
                    # Defensive: size_cable's screen should have caught
                    # this. Preserved as a hard gate so a future edit
                    # cannot regress the invariant.
                    violations.append(
                        ConstraintViolation(
                            constraint_type="thermal",
                            rigidity="hard",
                            message=(
                                f"Cable conductor temperature "
                                f"{csh.conductor_temp_f:.0f} F exceeds the "
                                f"{cable.cable.max_temp_f:.0f} F insulation "
                                f"class rating: fluid {csh.fluid_temp_f:.0f} F "
                                f"+ self-heating {csh.rise_f:.0f} F (§6D.2). "
                                f"Displayed as two components because a "
                                f"shroud helps the self-heating term and a "
                                f"high-temperature insulation build helps "
                                f"the rating term."
                            ),
                            actual_value=csh.conductor_temp_f,
                            limit_value=cable.cable.max_temp_f,
                            unit="F",
                            remedy_hint=(
                                "If self-heating is the dominant contribution, "
                                "add a motor shroud (raises annular velocity, "
                                "drops rise). If the fluid is already near the "
                                "rating, select a high-temperature MLE / cable "
                                "insulation build."
                            ),
                        )
                    )
                if cable.voltage_drop_frac > cfg.thresholds.electrical.max_cable_voltage_drop_frac:
                    violations.append(
                        ConstraintViolation(
                            constraint_type="electrical",
                            rigidity="hard",
                            message=(
                                f"Cable {cable.cable.awg} voltage drop is "
                                f"{cable.voltage_drop_frac:.1%}, exceeding the "
                                f"allowable limit."
                            ),
                            actual_value=cable.voltage_drop_frac,
                            limit_value=cfg.thresholds.electrical.max_cable_voltage_drop_frac,
                            unit="fraction",
                            remedy_hint="Use a larger conductor or a higher motor voltage.",
                        )
                    )

        mech = check_mechanical(
            pump=pump,
            seal=seal,
            stages=config.stages,
            bhp_total=bhp,
            gas_handling_hp=gh_hp,
            frequency_hz=config.frequency_hz,
            thresholds=cfg.thresholds.mechanical,
            head_developed_ft=head_developed,
            mixture_sg=intake.mixture_sg,
            thrust_path_override=case.engineering.seal_thrust_paths.get("main"),
            # Framework 6C.4 reference quantity. Nameplate, not operating
            # load: the motor's own capability is the threat to the shaft.
            # Under a VSD above 60 Hz the motor can deliver MORE than its
            # plate value, so plate hp alone understates the threat. Take
            # whichever is larger: the true capability is what matters.
            motor_nameplate_hp=(
                max(motor.motor.hp, motor.hp_nameplate_at_frequency)
                if motor is not None
                else None
            ),
        )
        warnings.extend(mech.warnings)
        if mech.thrust_load_lb is not None and mech.thrust_capacity_lb is not None and mech.thrust_load_lb > mech.thrust_capacity_lb:
            violations.append(ConstraintViolation(
                constraint_type="mechanical",rigidity="soft",
                message="Protector thrust exceeds single-section capacity. Tandem recommended; engineer may retain the single section.",
                actual_value=mech.thrust_load_lb,limit_value=mech.thrust_capacity_lb,unit="lb",
            ))
        for failure in mech.failures:
            stage_limit=min(pump.max_stages,cfg.thresholds.mechanical.max_stages_per_housing) if "stages required" in failure else None
            violations.append(
                ConstraintViolation(
                    constraint_type="mechanical",
                    rigidity="hard",
                    message=failure,
                    actual_value=config.stages if stage_limit is not None else None,
                    limit_value=stage_limit,unit="stages" if stage_limit is not None else None,
                    remedy_hint=(
                        "Reduce stage count, split into tandem housings, or "
                        "select a high-strength shaft option."
                    ),
                )
            )
        if mech.shaft_nameplate_check_possible and mech.shaft_hp_required > mech.shaft_hp_available:
            violations.append(ConstraintViolation(constraint_type="mechanical",rigidity="soft",
                message="Motor nameplate capability exceeds shaft/bearing strength; selectable, not cleared for installation.",
                actual_value=mech.shaft_hp_required,limit_value=mech.shaft_hp_available,unit="hp"))

        sizing = {"motor": motor, "cable": cable, "mechanical": mech, "gas": gas, "seal": seal}

        # --- Head sufficiency (§6.3 / §12 step 6) ----------------------------
        # A pump that develops less head than the well requires at the design
        # point is not a design; it is an under-staged proposal. Reporting it
        # as FEASIBLE would repeat the failure mode §6.3 exists to eliminate:
        # "size for a point, don't verify". Downstream months may legitimately
        # go head-short as the well waters out (that is what the validity
        # boundary reports), but month 0 shortfall is a hard rejection so the
        # engineer never sees a ranked candidate that cannot lift on day one.
        #
        # Small negative margins are inevitable from the discrete stage-count
        # bracket -- 79.4 stages required, 79 available, ~0.5% short. A narrow
        # tolerance band is treated as soft so the enumerator does not have to
        # generate every integer stage count around the nominal.
        HEAD_SHORTFALL_HARD_FRAC = -0.02  # 2% below required is a real shortfall
        if head_developed < tdh.tdh_ft - 1e-6:
            shortfall_ft = tdh.tdh_ft - head_developed
            violations.append(
                ConstraintViolation(
                    constraint_type="hydraulic",
                    rigidity="hard" if point.month == 0.0 and head_margin < HEAD_SHORTFALL_HARD_FRAC else "soft",
                    message=(
                        f"At {point.month:.0f} months the pump develops "
                        f"{head_developed:.0f} ft against a required TDH of "
                        f"{tdh.tdh_ft:.0f} ft -- a {abs(head_margin)*100:.1f}% "
                        f"shortfall ({shortfall_ft:.0f} ft). The well cannot be "
                        f"lifted at the target rate with this configuration."
                    ),
                    actual_value=head_developed,
                    limit_value=tdh.tdh_ft,
                    unit="ft",
                    remedy_hint=(
                        "Increase stage count, increase frequency, or select a "
                        "pump with higher head per stage."
                    ),
                )
            )

        # --- Zone violations (trajectory-relevant, not gate-fatal) -----------
        if zone.zone in {OperatingZone.OFF_CURVE_LEFT, OperatingZone.OFF_CURVE_RIGHT}:
            violations.append(
                ConstraintViolation(
                    constraint_type="mechanical",
                    rigidity="hard" if point.month == 0.0 else "soft",
                    message=(
                        f"At {point.month:.0f} months the operating point is off the "
                        f"pump curve ({zone.zone.value}, Q/Qbep = "
                        f"{zone.q_over_qbep:.2f}). {zone.rationale}"
                    ),
                    actual_value=zone.q_over_qbep,
                    unit="Q/Qbep",
                    remedy_hint="Select a pump whose range covers the full trajectory.",
                )
            )

        if not gas.turpin_stable:
            warnings.append(
                f"Turpin criterion not satisfied at {point.month:.0f} months "
                f"(parameter {gas.turpin_parameter:.2f}); gas locking risk."
            )

        # --- §12 step 11: THE CONSTRAINTS GATE -------------------------------
        gate_status = evaluate_gate(violations)

        cell = CellResult(
            evaluated_below_target=point.evaluation_rate_bpd is not None,
            config_id=config.config_id,
            point_id=point.point_id,
            month=point.month,
            envelope=point.envelope,
            pip_psi=intake.pip_psi,
            # Stock-tank basis. Passing the intake VOLUMETRIC rate here would be a
            # basis error: intake bbl exceed stock-tank bbl by the formation volume
            # factor, so the IPR would be asked for a rate above the well's AOF.
            pwf_psi=pwf_for_rate(
                self._inflow_spec(case, point), (point.evaluation_rate_bpd or point.target_rate_bpd)*(1-point.water_cut_frac)
            ),
            intake_temp_f=intake.intake_temp_f,
            tdh_ft=tdh.tdh_ft,
            total_fluid_intake_bpd=q,
            liquid_rate_bpd=intake.total_liquid_intake_bpd,
            mixture_sg=intake.mixture_sg,
            free_gas_fraction_at_intake=gas.fgvf_at_intake,
            free_gas_fraction_entering_pump=gas.fgvf_entering_pump,
            gas_strategy=gas.recommended_strategy.value,
            head_degradation_factor=degradation,
            turpin_stable=gas.turpin_stable,
            head_developed_ft=head_developed,
            head_required_ft=tdh.tdh_ft,
            head_margin_frac=head_margin,
            efficiency_frac=stage_point.efficiency_frac,
            bhp_hp=bhp,
            zone=zone.zone,
            q_over_qbep=zone.q_over_qbep,
            distance_from_bep_frac=zone.distance_from_bep_frac,
            zone_severity=zone.severity,
            motor_loading_frac=motor.loading_frac if motor else None,
            equipment_checks_performed=motor is not None and cable is not None and mech is not None,
            motor_type=motor.motor_type if motor else None,
            motor_vsd_required=bool(motor and motor.vsd_required),
            motor_magnet_gate=motor.magnet_gate if motor else None,
            motor_amps_basis=motor.amps_basis if motor else None,
            motor_uses_operator_supplied_data=bool(
                motor and motor.uses_operator_supplied_motor_data
            ),
            motor_nameplate_amps_conflict=bool(
                motor and motor.nameplate_amps_conflict
            ),
            shaft_hp_utilization=mech.shaft_hp_utilization if mech else None,
            shaft_nameplate_utilization=mech.shaft_nameplate_utilization if mech else None,
            shaft_nameplate_check_possible=bool(mech and mech.shaft_nameplate_check_possible),
            shaft_fracture_risk=bool(
                mech
                and mech.shaft_nameplate_check_possible
                and mech.shaft_nameplate_utilization is not None
                and mech.shaft_nameplate_utilization > 1.0
            ),
            motor_nameplate_hp=mech.motor_nameplate_hp if mech else None,
            shaft_hp_operating_load=mech.shaft_hp_operating_load if mech else None,
            cable_voltage_drop_frac=cable.voltage_drop_frac if cable else None,
            surface_voltage_v=cable.surface_voltage_required_v if cable else None,
            cooling_velocity_ft_s=(
                motor.cooling_velocity_ft_s if motor is not None else None
            ),
            cooling_verdict=(
                motor.cooling.verdict.value
                if motor is not None and motor.cooling is not None
                else None
            ),
            cooling_floor_applied_ft_s=(
                motor.cooling.floor_applied_ft_s
                if motor is not None and motor.cooling is not None
                else None
            ),
            cooling_floor_basis=(
                motor.cooling.floor_basis.value
                if motor is not None and motor.cooling is not None
                else None
            ),
            cooling_upper_bound_checked=bool(
                motor is not None
                and motor.cooling is not None
                and motor.cooling.upper_bound_ft_s is not None
            ),
            motor_self_heating=motor.self_heating if motor is not None else None,
            cable_self_heating=cable.self_heating if cable is not None else None,
            motor_operating_current=motor.operating_current if motor is not None else None,
            gate_status=gate_status,
            violations=violations,
            converged=intake.converged,
            warnings=sorted(set(warnings)),
        )
        return cell, sizing

    # =========================================================================
    # Cached well-state solve
    # =========================================================================

    def _intake_for(
        self, case: Case, point: ScenarioPoint, depth_md_ft: float
    ) -> IntakeConditions:
        """Intake conditions, cached per (scenario point, setting depth).

        The cache key deliberately excludes the pump. Intake conditions are a
        property of the well and the fluid at a moment in time; what is bolted
        above the intake does not change the pressure below it. This is the single
        optimization that makes exhaustive enumeration affordable.
        """
        key = (point.point_id, round(depth_md_ft, 1))
        cached = self._intake_cache.get(key)
        if cached is not None:
            return cached

        tvd = case.geometry.tvd_at_md(depth_md_ft)
        perf_tvd = (
            case.geometry.tvd_at_md(case.geometry.perforation_top_md_ft.value)
            if case.geometry.perforation_top_md_ft
            else tvd + 200.0
        )
        casing_id = case.geometry.min_casing_id_to_depth(depth_md_ft) or 6.0

        result = solve_intake_conditions(
            case_geometry_casing_id_in=casing_id,
            setting_depth_md_ft=depth_md_ft,
            setting_depth_tvd_ft=tvd,
            target_rate_bpd=point.evaluation_rate_bpd or point.target_rate_bpd,
            inflow=self._inflow_spec(case, point),
            fluid_spec=self._fluid_spec(case, point),
            perforation_tvd_ft=perf_tvd,
            bht_f=point.bht_f,
            surface_temp_f=(
                case.reservoir.surface_temp_f.value
                if case.reservoir.surface_temp_f
                else _FALLBACK_SURFACE_TEMP_F
            ),
            casing_pressure_psi=(
                case.expectations.casing_pressure_psi.value
                if case.expectations.casing_pressure_psi
                else _FALLBACK_CASING_PSI
            ),
            cfg=self.config.correlations,
        )
        self._intake_cache[key] = result
        return result

    def _inflow_spec(self, case: Case, point: ScenarioPoint) -> InflowSpec:
        pi = point.productivity_index_bpd_psi
        bubble = (
            case.fluid.bubble_point_psi.value
            if case.fluid.bubble_point_psi
            else point.reservoir_pressure_psi * 0.6
        )
        return InflowSpec(
            reservoir_pressure_psi=point.reservoir_pressure_psi,
            productivity_index_bpd_psi=pi,
            bubble_point_psi=bubble,
            model=case.reservoir.ipr_model,  # type: ignore[arg-type]
        )

    def _fluid_spec(self, case: Case, point: ScenarioPoint) -> FluidSpec:
        f = case.fluid
        return FluidSpec(
            oil_api=f.oil_api.value if f.oil_api else _FALLBACK_OIL_API,
            gas_sg=f.gas_sg.value if f.gas_sg else _FALLBACK_GAS_SG,
            water_sg=f.water_sg.value if f.water_sg else _FALLBACK_WATER_SG,
            gor_scf_stb=point.gor_scf_stb,
            water_cut_frac=point.water_cut_frac,
            salinity_ppm=(
                f.water_salinity_ppm.value
                if f.water_salinity_ppm
                else _FALLBACK_SALINITY_PPM
            ),
            bubble_point_psi=f.bubble_point_psi.value if f.bubble_point_psi else None,
        )

    def _gas_for(
        self, case: Case, intake: IntakeConditions, depth: float, equipment_od_in: float,
        stage_type: str = "radial",
    ) -> GasAssessment:
        casing_id = case.geometry.min_casing_id_to_depth(depth) or 6.0
        return assess_gas(
            intake,
            casing_id_in=casing_id,
            equipment_od_in=equipment_od_in,
            has_vsd=case.constraints.electrical.vsd_available.value,
            cfg=self.config.correlations,
            thresholds=self.config.thresholds.gas,
            kq_override=case.engineering.gas_kq_override.value if case.engineering.gas_kq_override else None,
            kh_override=case.engineering.gas_kh_override.value if case.engineering.gas_kh_override else None,
            stage_type=stage_type,
        )

    def _tdh_for(self, case: Case, intake: IntakeConditions, depth: float):
        tubing_id = (
            case.geometry.tubing_id_in.value
            if case.geometry.tubing_id_in
            else _FALLBACK_TUBING_ID_IN
        )
        whp = (
            case.expectations.wellhead_pressure_psi.value
            if case.expectations.wellhead_pressure_psi
            else _FALLBACK_WELLHEAD_PSI
        )
        return compute_tdh(
            setting_depth_tvd_ft=intake.setting_depth_tvd_ft,
            pip_psi=intake.pip_psi,
            wellhead_pressure_psi=whp,
            total_liquid_rate_bpd=intake.total_liquid_intake_bpd,
            tubing_id_in=tubing_id,
            tubing_length_ft=depth,
            mixture_sg=intake.mixture_sg,
            mixture_viscosity_cp=intake.mixture_viscosity_cp,
            method=self.config.correlations.friction,
        )

    # =========================================================================
    # VSD recovery probe (§6.4)
    # =========================================================================

    def _probe_frequency_recovery(
        self,
        *,
        case: Case,
        config: PumpConfiguration,
        pump: PumpModel,
        scenario_points: list[ScenarioPoint],
        validity_boundary: float | None,
    ) -> dict[float, list[CellResult]] | None:
        """Re-evaluate the post-boundary months at alternative frequencies.

        Only the months past the validity boundary are probed, and only in the
        base envelope. Probing everything would multiply the cost by the size of
        the frequency band for information the engineer did not ask for.
        """
        if validity_boundary is None:
            return None

        elec = case.constraints.electrical
        band = elec.frequency_band()
        if band is None:
            return None

        lo, hi = band
        step = self.config.enumeration.frequency_step_hz
        probe_freqs = []
        f = lo
        while f <= hi + 1e-9:
            if abs(f - config.frequency_hz) > 1e-9:
                probe_freqs.append(round(f, 2))
            f += step

        targets = [
            p
            for p in scenario_points
            if p.envelope is EnvelopeCase.BASE and p.month >= validity_boundary
        ]
        if not targets or not probe_freqs:
            return None

        from .curves import scale_curve_to_frequency

        out: dict[float, list[CellResult]] = {}
        for freq in probe_freqs:
            try:
                curve = scale_curve_to_frequency(
                    pump, freq, self.config.correlations.frequency_curves
                )
            except (ValueError, KeyError):
                continue
            probe_config = config.model_copy(update={"frequency_hz": freq})
            cells = []
            for point in targets:
                cell, _ = self._evaluate_cell(
                    case=case,
                    config=probe_config,
                    pump=pump,
                    curve=curve,
                    point=point,
                    size_equipment=False,
                )
                cells.append(cell)
            out[freq] = cells
        return out

    # =========================================================================
    # Result assembly
    # =========================================================================

    def _enrich_config(
        self, config: PumpConfiguration, sizing: dict
    ) -> PumpConfiguration:
        motor: MotorSizing | None = sizing.get("motor")
        cable: CableSizing | None = sizing.get("cable")
        gas: GasAssessment | None = sizing.get("gas")
        updates: dict = {}
        if motor:
            updates["motor_id"] = motor.motor.id
            updates["motor_hp"] = motor.motor.hp
        if cable:
            updates["cable_id"] = cable.cable.id
            updates["cable_awg"] = f"{cable.cable.awg} AWG"
        if sizing.get("seal") is not None:
            updates["seal_id"] = sizing["seal"].id
        if gas and gas.recommended_strategy is not GasStrategy.NONE:
            devices = self.catalog.gas_handling_for(
                gas.recommended_strategy, config.series
            )
            if devices:
                updates["gas_handling_id"] = devices[0].id
                updates["gas_handling_type"] = devices[0].type
            updates.setdefault("gas_handling_type", gas.recommended_strategy.value)
        return config.model_copy(update=updates) if updates else config

    def _gas_handling_hp(self, gas: GasAssessment, series: int) -> float:
        """Parasitic horsepower of the gas-handling device.

        Taken from the catalog's ``hp_consumed`` where the vendor publishes it,
        rather than from a percentage-of-BHP heuristic. A rotary separator or
        helico-axial charge pump is a real mechanical load; omitting it undersizes
        the motor, which is exactly the failure this exercise exists to prevent.
        """
        strategy = gas.recommended_strategy
        if strategy in {GasStrategy.NONE, GasStrategy.NOT_ESP_CANDIDATE}:
            return 0.0

        devices = self.catalog.gas_handling_for(strategy, series)
        published = [d.hp_consumed for d in devices if d.hp_consumed > 0]
        if published:
            # Conservative within the matching devices: undersizing the motor is
            # the expensive error, oversizing is merely inefficient.
            return max(published)

        # No published figure. Framework §5.2 asymmetric conservatism: assume a
        # load rather than assume none, and say so.
        return 3.0 if strategy is GasStrategy.SEPARATOR_PLUS_HANDLER else 2.0

    def _rejection(
        self, config: PumpConfiguration, stage: str, violations: list[ConstraintViolation]
    ):
        from .results import RejectedConfiguration

        # The reason must name the constraint that actually BLOCKED, not
        # whichever violation happens to be first in the list. Those differ
        # routinely: a configuration can carry a soft motor-loading note ahead
        # of the hard cooling failure that eliminated it, and reporting the
        # soft one as the reason -- while remedy_hints silently aggregates the
        # hard one's remedy -- tells the engineer to fit a shroud for a
        # loading problem. Absolute outranks hard, and both outrank soft.
        _order = {"absolute": 0, "hard": 1, "soft": 2}
        blocking = sorted(
            violations, key=lambda v: _order.get(v.rigidity, 3)
        )
        primary = blocking[0].message if blocking else "constraints gate failed"
        return RejectedConfiguration(
            configuration=config,
            stage_rejected=stage,  # type: ignore[arg-type]
            reason=primary,
            violations=violations,
            remedy_hints=summarize_remedies(violations),
        )

    def _summarize(self, rejected: list) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rejected:
            out[r.stage_rejected] = out.get(r.stage_rejected, 0) + 1
        return out

    def _verdict(
        self, results: list[CandidateResult], case: Case
    ) -> tuple[FeasibilityVerdict, str]:
        if not results:
            return (
                FeasibilityVerdict.NO_VIABLE_CONFIGURATION,
                "No configuration satisfies the stated constraints.",
            )
        best = results[0]
        if best.validity.valid_through_horizon:
            if best.score.total_score >= 0.75:
                return (
                    FeasibilityVerdict.FEASIBLE,
                    (
                        f"{best.configuration.pump_model} at "
                        f"{best.configuration.stages} stages / "
                        f"{best.configuration.frequency_hz:g} Hz passes operating checks "
                        f"at the evaluated samples across the "
                        f"{best.validity.horizon_months:g}-month horizon; this is not field run-life validation."
                    ),
                )
            return (
                FeasibilityVerdict.FEASIBLE_WITH_CAVEATS,
                (
                    f"A workable configuration exists and remains valid across the "
                    f"horizon, but operates away from BEP for much of it "
                    f"(score {best.score.total_score:.2f})."
                ),
            )

        boundary = best.validity.valid_until_months
        recovered = (
            best.vsd_recovery.extended_validity_months
            if best.vsd_recovery and best.vsd_recovery.extended_validity_months
            else None
        )
        if recovered and boundary and recovered > boundary:
            return (
                FeasibilityVerdict.FEASIBLE_WITH_CAVEATS,
                (
                    f"The ranking leader first fails baseline model checks at sampled month {boundary:.1f}. "
                    f"Fixed-equipment VSD recovery is verified at sampled points through month {recovered:.1f}. "
                    f"No continuous or later recovery is claimed. Baseline limitation: {best.validity.limiting_mechanism}."
                ),
            )
        if boundary is not None and boundary < 6.0:
            return (
                FeasibilityVerdict.FEASIBLE_WITH_CAVEATS,
                (
                    f"The ranking leader fails baseline operating checks at sampled month {boundary:.1f}; alternatives may differ. "
                    f"{best.validity.limiting_mechanism}. Reconsider the target rate, "
                    f"the setting depth, or plan a staged completion."
                ),
            )
        return (
            FeasibilityVerdict.FEASIBLE_WITH_CAVEATS,
            (
                f"Best configuration is valid for {boundary:.1f} months of the "
                f"{best.validity.horizon_months:g}-month horizon. "
                f"{best.validity.limiting_mechanism}."
            ),
        )

    def _trust_register(self, case: Case) -> list:
        """Framework 3.0 disclosure of everything taken on trust."""
        return build_trust_register(case)

    def _boundary_crossings(self, case: Case) -> list[str]:
        return [
            e.field_path
            for e in self._trust_register(case)
            if e.boundary_note is not None
        ]

    def _build_ledger(self, case: Case) -> list[AssumptionLedgerEntry]:
        """Every assumed value, with its bias and what breaks if it is wrong.

        Framework §5.2: soft inputs are fillable by explicit assumption. "Explicit"
        is the operative word — an assumption that does not appear in the output is
        indistinguishable from a fabrication.
        """
        entries: list[AssumptionLedgerEntry] = []
        for path, tracked in case.assumed_fields():
            entries.append(
                AssumptionLedgerEntry(
                    field_path=path,
                    value=f"{tracked.value}",
                    unit=tracked.unit,
                    source=tracked.source.value,
                    confidence=tracked.confidence,
                    basis=(
                        tracked.assumption.basis
                        if tracked.assumption
                        else tracked.note
                    ),
                    bias=(
                        tracked.assumption.bias.value
                        if tracked.assumption and tracked.assumption.bias
                        else None
                    ),
                    rationale=(
                        tracked.assumption.rationale if tracked.assumption else None
                    ),
                    unbiased_value=(
                        f"{tracked.assumption.unbiased_value}"
                        if tracked.assumption
                        and tracked.assumption.unbiased_value is not None
                        else None
                    ),
                    materiality=_materiality(path),
                    sensitivity_note=_sensitivity_note(path, tracked.value),
                )
            )
        return sorted(
            entries,
            key=lambda e: {"critical": 0, "significant": 1, "minor": 2}[e.materiality],
        )

    def _data_requests(self, case: Case) -> list[str]:
        """What to ask the customer, in priority order.

        Framework §5.3 lists bonus data that materially improves a design. Asking
        for all of it wastes the relationship; asking for the two items that would
        actually change the answer is what a good engineer does.
        """
        requests: list[str] = []
        if case.branch is TaskBranch.B_NEW and case.task_type.branch is TaskBranch.A_REPLACEMENT:
            requests.append(
                "Performance data from the previous installation (rate, PIP, "
                "frequency, amps at any date). This is the single highest-value "
                "input: it converts the design from an estimate to an anchored "
                "calculation."
            )
        if case.reservoir.productivity_index_bpd_psi is None and not (
            case.reference and case.reference.test_points
        ):
            requests.append(
                "A well test (rate and flowing bottomhole pressure, or rate and "
                "PIP) to establish productivity index. Currently assumed, and it "
                "controls the achievable rate directly."
            )
        if case.fluid.gor_scf_stb is None:
            requests.append(
                "Producing GOR. Assumed values are biased upward for safety, which "
                "may be specifying gas-handling equipment the well does not need."
            )
        if case.fluid.water_cut_frac is None:
            requests.append(
                "Current water cut and its trend. Water cut drives fluid density "
                "and therefore the head requirement over time."
            )
        if not case.geometry.deviation_survey:
            requests.append(
                "Directional survey. Without it, dogleg severity at the setting "
                "depth cannot be validated and the setting depth is provisional."
            )
        if case.reference and case.reference.failure_mode is None:
            requests.append(
                "Failure mode and teardown findings from the previous installation. "
                "This is what lets the system learn from this well rather than just "
                "size for it."
            )
        return requests

    def _catalog_warnings(self, results: list[CandidateResult]) -> list[str]:
        """Surface catalog provenance where it affects the answer.

        The seed catalog's pump curves are parametric estimates fitted to
        published metadata, not digitized vendor curves. A tool that presents an
        estimated curve with the same confidence as a vendor curve is worse than
        no tool, because it launders an estimate into an authority.
        """
        if any(r.depends_on_estimated_catalog_data for r in results):
            return [
                "One or more ranked configurations use PARAMETRIC ESTIMATE pump "
                "curves rather than digitized vendor curves. Head, efficiency, and "
                "thrust-zone boundaries are approximate. Confirm against the "
                "vendor's published curve before any field commitment."
            ]
        return []

    # =========================================================================
    # Early exits
    # =========================================================================

    def _blocked_result(
        self, case: Case, gaps: list[str], started: float
    ) -> DesignResult:
        """§5.1 hard stop. Refuses to calculate rather than inventing an anchor.

        This is a feature. The framework is explicit that target rate and geometry
        cannot be assumed, and a tool that produces a confident-looking design from
        nothing is the most dangerous possible failure mode for this product.
        """
        return DesignResult(
            design_id=str(uuid.uuid4()),
            case_id=case.metadata.case_id,
            tenant_id=case.metadata.tenant_id,
            verdict=FeasibilityVerdict.BLOCKED_MISSING_DATA,
            verdict_explanation=(
                "Calculation not attempted: mandatory inputs are missing. Per §5.1 "
                "these cannot be filled by assumption — a design built on an assumed "
                "target rate or assumed casing ID is not a design."
            ),
            branch=case.branch,
            branch_rationale=case.branch_rationale,
            blocking_data_gaps=gaps,
            recommended_data_requests=self._data_requests(case),
            input_confidence=case.overall_input_confidence,
            assumption_ledger=self._build_ledger(case),
            trust_register=self._trust_register(case),
            responsibility_boundary_crossings=self._boundary_crossings(case),
            provenance=RunProvenance(
                case_hash=case.content_hash(),
                config_hash=self.config.config_hash(),
                catalog_version=self.catalog.version,
                engine_version=ENGINE_VERSION,
                computed_at=datetime.now(timezone.utc).isoformat(),
                compute_ms=(time.perf_counter() - started) * 1000.0,
            ),
        )

    def _infeasible_result(
        self,
        case: Case,
        enumeration,
        scenario_points: list[ScenarioPoint],
        started: float,
        warnings: list[str],
        extra_rejected: list | None = None,
        cells_evaluated: int = 0,
    ) -> DesignResult:
        rejected = list(enumeration.rejected) + list(extra_rejected or [])
        remedies = summarize_remedies(
            [v for r in rejected for v in r.violations]
        )
        return DesignResult(
            design_id=str(uuid.uuid4()),
            case_id=case.metadata.case_id,
            tenant_id=case.metadata.tenant_id,
            verdict=FeasibilityVerdict.NO_VIABLE_CONFIGURATION,
            verdict_explanation=(
                "No configuration in the catalog satisfies the stated constraints. "
                "This is a legitimate engineering answer (§3.1): the expectations "
                "and the constraints are incompatible."
            ),
            branch=case.branch,
            branch_rationale=case.branch_rationale,
            rejected=rejected[: self.config.enumeration.max_reported_rejections],
            rejection_summary=self._summarize(rejected),
            scenario_points=scenario_points,
            assumption_ledger=self._build_ledger(case),
            trust_register=self._trust_register(case),
            responsibility_boundary_crossings=self._boundary_crossings(case),
            recommended_data_requests=remedies + self._data_requests(case),
            input_confidence=case.overall_input_confidence,
            provenance=RunProvenance(
                case_hash=case.content_hash(),
                config_hash=self.config.config_hash(),
                catalog_version=self.catalog.version,
                engine_version=ENGINE_VERSION,
                computed_at=datetime.now(timezone.utc).isoformat(),
                compute_ms=(time.perf_counter() - started) * 1000.0,
                cells_evaluated=cells_evaluated,
                configs_enumerated=len(enumeration.candidates),
                configs_surviving=0,
            ),
            engine_warnings=warnings,
        )

    def _nonconverged_cell(
        self, config: PumpConfiguration, point: ScenarioPoint, reason: str
    ) -> CellResult:
        """A cell that could not be solved.

        Recorded as non-converged rather than dropped. A silently missing scenario
        point would make the trajectory look better than it is, because the scorer
        would simply not see the condition that broke.
        """
        return CellResult(
            config_id=config.config_id,
            point_id=point.point_id,
            month=point.month,
            envelope=point.envelope,
            pip_psi=0.0,
            pwf_psi=0.0,
            intake_temp_f=point.bht_f,
            tdh_ft=0.0,
            total_fluid_intake_bpd=0.0,
            liquid_rate_bpd=0.0,
            mixture_sg=1.0,
            free_gas_fraction_at_intake=0.0,
            free_gas_fraction_entering_pump=0.0,
            gas_strategy="unknown",
            head_degradation_factor=1.0,
            turpin_stable=False,
            head_developed_ft=0.0,
            head_required_ft=0.0,
            head_margin_frac=-1.0,
            efficiency_frac=0.0,
            bhp_hp=0.0,
            zone=OperatingZone.OFF_CURVE_LEFT,
            q_over_qbep=0.0,
            distance_from_bep_frac=1.0,
            zone_severity=1.0,
            gate_status=GateStatus.FAILED_SOFT,
            violations=[
                ConstraintViolation(
                    constraint_type="hydraulic",
                    rigidity="hard",
                    message=f"Scenario point could not be solved: {reason}",
                    remedy_hint="Review the fluid and reservoir description for consistency.",
                )
            ],
            converged=False,
        )

    def _no_fit_result(
        self,
        case: Case,
        started: float,
        *,
        min_id: float,
        smallest_pump_od: float,
        clearance: float,
    ) -> DesignResult:
        """No pump in the catalog fits the casing.

        A distinct verdict from "missing data". The data is complete; the answer
        is that this well cannot take an ESP from this catalog. Reporting it as a
        data gap would send the engineer to collect information that cannot
        change the outcome.
        """
        reason = (
            f"The tightest restriction above setting depth is {min_id:.3f} in ID. "
            f"The smallest pump in the catalog is {smallest_pump_od:.2f} in OD, "
            f"which needs {smallest_pump_od + clearance:.2f} in to run with the "
            f"{clearance:.2f} in minimum clearance. No configuration is possible."
        )
        stub = PumpConfiguration(
            config_id="no-fit",
            pump_id="(none)",
            pump_model="(none)",
            manufacturer="(none)",
            series=0,
            stages=0,
            frequency_hz=60.0,
            setting_depth_md_ft=resolve_setting_depth(case)[0],
        )
        return DesignResult(
            design_id=str(uuid.uuid4()),
            case_id=case.metadata.case_id,
            tenant_id=case.metadata.tenant_id,
            verdict=FeasibilityVerdict.NO_VIABLE_CONFIGURATION,
            verdict_explanation=reason,
            branch=case.branch,
            branch_rationale=case.branch_rationale,
            rejected=[
                RejectedConfiguration(
                    configuration=stub,
                    stage_rejected="geometry_prescreen",
                    reason=reason,
                    remedy_hints=[
                        "Consider a slimline / thru-tubing ESP, or an alternative "
                        "lift method such as gas lift, jet pump, or rod pump.",
                        "Confirm the restriction: if it is a single short nipple, a "
                        "workover to remove it may open the well to a standard ESP.",
                    ],
                )
            ],
            rejection_summary={"geometry_prescreen": 1},
            recommended_data_requests=self._data_requests(case)
            or [
                "Confirm the casing/tubing restriction profile above the intended "
                "setting depth — the design is blocked by geometry alone."
            ],
            input_confidence=case.overall_input_confidence,
            assumption_ledger=self._build_ledger(case),
            trust_register=self._trust_register(case),
            responsibility_boundary_crossings=self._boundary_crossings(case),
            uses_estimated_catalog_data=self.catalog.has_estimated_data,
            engine_warnings=self._catalog_warnings([]),
            provenance=RunProvenance(
                case_hash=case.content_hash(),
                config_hash=self.config.config_hash(),
                catalog_version=self.catalog.version,
                engine_version=ENGINE_VERSION,
                computed_at=datetime.now(timezone.utc).isoformat(),
                compute_ms=(time.perf_counter() - started) * 1000.0,
            ),
        )

    def _no_lift_required_result(
        self,
        case: Case,
        started: float,
        *,
        tdh_ft: float,
        achievable_rate_bpd: float,
        target_rate_bpd: float,
        pip_psi: float,
        depth_ft: float,
    ) -> DesignResult:
        """Screening TDH came out at or below zero.

        Two physically distinct situations produce this, and conflating them would
        mislead the engineer, so the verdict is chosen on the rate shortfall:

        - The reservoir delivers the target rate to the wellhead unaided. The
          honest answer is that an ESP is not required, not that the design failed.
        - The reservoir cannot approach the target, so the solved drawdown is small
          and no head is needed to lift the little fluid that arrives. The honest
          answer is that the target is unachievable.
        """
        shortfall = (
            (target_rate_bpd - achievable_rate_bpd) / target_rate_bpd
            if target_rate_bpd > 0
            else 0.0
        )
        target_missed = shortfall > 0.10

        if target_missed:
            verdict = FeasibilityVerdict.TARGET_UNACHIEVABLE
            explanation = (
                f"The reservoir can deliver about {achievable_rate_bpd:,.0f} bpd "
                f"against the {target_rate_bpd:,.0f} bpd target "
                f"({shortfall:.0%} short). At that rate the required total dynamic "
                f"head computes to {tdh_ft:,.0f} ft, meaning no lift is needed to "
                "produce the small volume the reservoir actually supplies. The "
                "constraint is inflow, not the pump: no ESP selection can close "
                "this gap."
            )
            hints = [
                "Revisit the target rate against the measured productivity index.",
                "If the target is firm, the well needs stimulation or additional "
                "perforations before any lift design is meaningful.",
            ]
        else:
            verdict = FeasibilityVerdict.NO_VIABLE_CONFIGURATION
            explanation = (
                f"Required total dynamic head is {tdh_ft:,.0f} ft at "
                f"{target_rate_bpd:,.0f} bpd with pump intake pressure of "
                f"{pip_psi:,.0f} psi at {depth_ft:,.0f} ft. The reservoir already "
                "delivers this rate to the wellhead at the required pressure, so "
                "no artificial lift is required at this condition. An ESP would be "
                "throttling a well that flows on its own."
            )
            hints = [
                "Confirm the well is not already flowing naturally.",
                "If lift is wanted for future depletion, re-run with the reservoir "
                "pressure and water cut expected at that later date.",
                "Check the wellhead pressure requirement — a higher required "
                "wellhead pressure may make lift necessary.",
            ]

        stub = PumpConfiguration(
            config_id="no-lift-required",
            pump_id="(none)",
            pump_model="(none)",
            manufacturer="(none)",
            series=0,
            stages=0,
            frequency_hz=60.0,
            setting_depth_md_ft=depth_ft,
        )
        return DesignResult(
            design_id=str(uuid.uuid4()),
            case_id=case.metadata.case_id,
            tenant_id=case.metadata.tenant_id,
            verdict=verdict,
            verdict_explanation=explanation,
            branch=case.branch,
            branch_rationale=case.branch_rationale,
            rejected=[
                RejectedConfiguration(
                    configuration=stub,
                    stage_rejected="hydraulic_infeasible",
                    reason=explanation,
                    remedy_hints=hints,
                )
            ],
            rejection_summary={"hydraulic_infeasible": 1},
            recommended_data_requests=self._data_requests(case) or hints,
            input_confidence=case.overall_input_confidence,
            assumption_ledger=self._build_ledger(case),
            trust_register=self._trust_register(case),
            responsibility_boundary_crossings=self._boundary_crossings(case),
            uses_estimated_catalog_data=self.catalog.has_estimated_data,
            engine_warnings=self._catalog_warnings([]),
            provenance=RunProvenance(
                case_hash=case.content_hash(),
                config_hash=self.config.config_hash(),
                catalog_version=self.catalog.version,
                engine_version=ENGINE_VERSION,
                computed_at=datetime.now(timezone.utc).isoformat(),
                compute_ms=(time.perf_counter() - started) * 1000.0,
            ),
        )

    def _target_above_aof_result(
        self,
        case: Case,
        started: float,
        *,
        aof_bpd: float,
        target_rate_bpd: float,
    ) -> DesignResult:
        """The target rate exceeds the reservoir's absolute open flow.

        Reported as TARGET_UNACHIEVABLE rather than as missing data or a list of
        rejected pumps, because the binding constraint is the reservoir. Handing
        the engineer 400 rejected configurations here would obscure the single
        fact that matters.
        """
        explanation = (
            f"The {target_rate_bpd:,.0f} bpd target exceeds the well's absolute "
            f"open flow of {aof_bpd:,.0f} bpd — the most this reservoir can "
            "deliver at zero backpressure, with the pump intake drawn down to "
            "nothing. No pump selection can achieve the target; the binding "
            "constraint is inflow, not lift."
        )
        hints = [
            f"The achievable ceiling is {aof_bpd:,.0f} bpd. Re-run with a target "
            "at or below roughly 80% of that for a design with usable drawdown.",
            "Verify the productivity index and reservoir pressure — the AOF "
            "follows directly from them, so an error there moves this ceiling.",
            "If the target is firm, the well requires stimulation or additional "
            "completion before any lift design is meaningful.",
        ]
        stub = PumpConfiguration(
            config_id="target-above-aof",
            pump_id="(none)",
            pump_model="(none)",
            manufacturer="(none)",
            series=0,
            stages=0,
            frequency_hz=60.0,
            setting_depth_md_ft=resolve_setting_depth(case)[0],
        )
        return DesignResult(
            design_id=str(uuid.uuid4()),
            case_id=case.metadata.case_id,
            tenant_id=case.metadata.tenant_id,
            verdict=FeasibilityVerdict.TARGET_UNACHIEVABLE,
            verdict_explanation=explanation,
            branch=case.branch,
            branch_rationale=case.branch_rationale,
            rejected=[
                RejectedConfiguration(
                    configuration=stub,
                    stage_rejected="hydraulic_infeasible",
                    reason=explanation,
                    remedy_hints=hints,
                )
            ],
            rejection_summary={"hydraulic_infeasible": 1},
            recommended_data_requests=self._data_requests(case) or hints,
            input_confidence=case.overall_input_confidence,
            assumption_ledger=self._build_ledger(case),
            trust_register=self._trust_register(case),
            responsibility_boundary_crossings=self._boundary_crossings(case),
            uses_estimated_catalog_data=self.catalog.has_estimated_data,
            engine_warnings=self._catalog_warnings([]),
            provenance=RunProvenance(
                case_hash=case.content_hash(),
                config_hash=self.config.config_hash(),
                catalog_version=self.catalog.version,
                engine_version=ENGINE_VERSION,
                computed_at=datetime.now(timezone.utc).isoformat(),
                compute_ms=(time.perf_counter() - started) * 1000.0,
            ),
        )

    def _off_curve_cell(
        self,
        *,
        case: Case,
        config: PumpConfiguration,
        point: ScenarioPoint,
        intake: Any,
        tdh: Any,
        gas: Any,
        zone: OperatingZone,
        q_bpd: float,
        curve: Any,
        warnings: list[str],
    ) -> CellResult:
        """A converged cell whose operating point lies off the pump curve.

        Distinct from ``_failed_cell``: everything upstream of the pump solved
        correctly, so the PIP, temperature, gas fraction and TDH are all real
        numbers worth reporting. Only the pump performance is unavailable, and it
        is unavailable because reporting it would require extrapolation.

        Marked converged so the scorer sees a genuine off-curve condition at this
        month rather than treating the point as a solver failure.
        """
        detail = (
            f"{q_bpd:.0f} bpd at intake is outside the pump's fitted curve domain "
            f"({curve.q_min_bpd:.0f}-{curve.q_max_bpd:.0f} bpd). Pump performance "
            "is not reported here because obtaining it would require "
            "extrapolating the curve fit."
        )
        return CellResult(
            config_id=config.config_id,
            point_id=point.point_id,
            month=point.month,
            envelope=point.envelope,
            pip_psi=intake.pip_psi,
            # Stock-tank basis. Passing the intake VOLUMETRIC rate here would be a
            # basis error: intake bbl exceed stock-tank bbl by the formation volume
            # factor, so the IPR would be asked for a rate above the well's AOF.
            pwf_psi=pwf_for_rate(
                self._inflow_spec(case, point), point.achievable_rate_bpd
            ),
            intake_temp_f=intake.intake_temp_f,
            tdh_ft=tdh.tdh_ft,
            total_fluid_intake_bpd=q_bpd,
            liquid_rate_bpd=intake.total_liquid_intake_bpd,
            mixture_sg=intake.mixture_sg,
            free_gas_fraction_at_intake=gas.fgvf_at_intake,
            free_gas_fraction_entering_pump=gas.fgvf_entering_pump,
            gas_strategy=gas.recommended_strategy.value,
            head_degradation_factor=gas.head_degradation_factor,
            turpin_stable=gas.turpin_stable,
            head_developed_ft=0.0,
            head_required_ft=tdh.tdh_ft,
            head_margin_frac=-1.0,
            efficiency_frac=0.0,
            bhp_hp=0.0,
            zone=zone,
            q_over_qbep=(q_bpd / curve.bep_q_bpd if curve.bep_q_bpd else 0.0),
            distance_from_bep_frac=1.0,
            zone_severity=1.0,
            gate_status=GateStatus.FAILED_SOFT,
            violations=[
                ConstraintViolation(
                    constraint_type="hydraulic",
                    rigidity="hard",
                    message=detail,
                    remedy_hint=(
                        "Choose a pump whose range covers this rate, or change "
                        "stage count / frequency to move the operating point."
                    ),
                )
            ],
            warnings=list(warnings),
            converged=True,
        )

    @staticmethod
    def _find_point(
        points: list[ScenarioPoint], month: float, envelope: EnvelopeCase
    ) -> ScenarioPoint | None:
        for p in points:
            if abs(p.month - month) < 1e-9 and p.envelope is envelope:
                return p
        return None


# =============================================================================
# Assumption materiality — which assumptions actually move the answer
# =============================================================================

_CRITICAL_PATHS = {
    "expectations.target_rate_bpd",
    "reservoir.productivity_index_bpd_psi",
    "reservoir.reservoir_pressure_psi",
    "fluid.water_cut_frac",
    "fluid.gor_scf_stb",
}

_MINOR_PATHS = {
    "fluid.water_sg",
    "fluid.gas_sg",
    "reservoir.surface_temp_f",
    "expectations.casing_pressure_psi",
}


def _materiality(path: str):
    if path in _CRITICAL_PATHS:
        return "critical"
    if path in _MINOR_PATHS:
        return "minor"
    return "significant"


def _sensitivity_note(path: str, value) -> str | None:
    """What changes if this assumption is wrong.

    Framework §5.2's asymmetric conservatism is only auditable if the reader can
    see which direction the error runs and what it costs.
    """
    notes = {
        "fluid.gor_scf_stb": (
            "Biased upward. If actual GOR is materially lower, gas-handling "
            "equipment may be specified unnecessarily, adding cost and a "
            "parasitic horsepower load."
        ),
        "fluid.water_cut_frac": (
            "Drives fluid density and therefore TDH. A higher actual water cut "
            "increases head demand and shifts the operating point left, toward "
            "downthrust."
        ),
        "reservoir.productivity_index_bpd_psi": (
            "Controls achievable rate directly. If PI is lower than assumed, the "
            "target rate is unreachable at any stage count and the entire design "
            "premise changes."
        ),
        "reservoir.reservoir_pressure_psi": (
            "Sets the starting drawdown. An overestimate produces optimistic "
            "intake pressure and understates free gas at the intake."
        ),
        "expectations.setting_depth_md_ft": (
            "Trades submergence and gas separation against dogleg and temperature "
            "limits. A shallower actual depth increases free gas at the intake."
        ),
        "reservoir.bht_f": (
            "Biased upward. Affects fluid properties, motor cooling margin, and "
            "cable temperature derating."
        ),
        "fluid.oil_api": (
            "Affects density and viscosity. Heavier oil than assumed increases "
            "head demand and may require viscosity correction."
        ),
    }
    return notes.get(path)
