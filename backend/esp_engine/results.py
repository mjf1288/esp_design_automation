"""Design result models — the fact/judgment boundary made structural.

Framework refs: §1 (deterministic vs agentic separation), §6.4 (candidate
scoring and validity boundary), §7 (empirical overlay adds judgment on top of
computed results), §12 (process flow output).

Every number in this module is a FACT: produced by the deterministic engine,
reproducible from ``case_hash + config_hash + catalog_version`` forever.
Judgments live in a separate list, reference facts by ID, and can never modify
them. The API serializes the two blocks separately and the UI styles them
differently — an engineer must always be able to tell computation from opinion.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import OperatingZone, TaskBranch
from .cable_thermal import CableSelfHeating
from .electrical_load import OperatingCurrent
from .motor_thermal import MotorSelfHeating
from .responsibility import TrustRegisterEntry
from .provenance import Judgment, Tracked
from .tolerance import RankingStability, ToleranceAssessment


# =============================================================================
# Configuration identity
# =============================================================================


class PumpConfiguration(BaseModel):
    """One candidate ESP configuration — the unit of enumeration (§6.3)."""

    model_config = ConfigDict(frozen=True)

    config_id: str
    pump_id: str
    pump_model: str
    manufacturer: str
    series: int
    stages: int
    frequency_hz: float
    setting_depth_md_ft: float

    # Selected support equipment, filled in as the design chain proceeds
    gas_handling_id: str | None = None
    gas_handling_type: str | None = None
    motor_id: str | None = None
    motor_hp: float | None = None
    seal_id: str | None = None
    cable_id: str | None = None
    cable_awg: str | None = None

    @property
    def label(self) -> str:
        return f"{self.pump_model}, {self.stages} stages, {self.frequency_hz:g} Hz"


# =============================================================================
# Scenario points — the time axis (§6)
# =============================================================================


class EnvelopeCase(str, Enum):
    MIN = "min"
    BASE = "base"
    MAX = "max"


class ScenarioPoint(BaseModel):
    """Reservoir and fluid state at one instant on the life trajectory.

    Depends on the scenario, NOT on the configuration — which is why PVT is
    solved once per scenario point and reused across all candidate configs. That
    caching is the single largest performance win in the engine (§6.3).
    """

    model_config = ConfigDict(frozen=True)

    point_id: str
    month: float
    envelope: EnvelopeCase

    reservoir_pressure_psi: float
    productivity_index_bpd_psi: float
    water_cut_frac: float
    gor_scf_stb: float
    bht_f: float

    achievable_rate_bpd: float = Field(
        description="Rate the reservoir can deliver at the design drawdown; may "
        "be below the target as the well depletes"
    )
    target_rate_bpd: float
    evaluation_rate_bpd: float | None = None

    drift_note: str | None = None

    @property
    def rate_shortfall_frac(self) -> float:
        if self.target_rate_bpd <= 0:
            return 0.0
        return max(0.0, 1.0 - self.achievable_rate_bpd / self.target_rate_bpd)


# =============================================================================
# Cell — one config at one scenario point
# =============================================================================


class GateStatus(str, Enum):
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    FAILED_SOFT = "failed_soft"  # commercial; can be ordered
    FAILED_HARD = "failed_hard"  # engineering; return to selection
    FAILED_ABSOLUTE = "failed_absolute"  # physics; config is dead


class ConstraintViolation(BaseModel):
    model_config = ConfigDict(frozen=True)

    constraint_type: Literal[
        "geometry", "electrical", "mechanical", "availability", "hydraulic", "thermal"
    ]
    rigidity: Literal["absolute", "hard", "soft"]
    physical_impossibility: bool = False
    margin: float | None = None
    policy: str = "expert_signoff_2026-10-02"

    @model_validator(mode="before")
    @classmethod
    def warning_policy(cls, values):
        values = dict(values)
        # Calculated exceedances never remove a selectable candidate. Historical
        # persisted JSON is not rewritten; new calculations use this policy.
        if values.get("policy") != "legacy" and not values.get("physical_impossibility", False):
            values["rigidity"] = "soft"
        a, b = values.get("actual_value"), values.get("limit_value")
        if a is not None and b is not None:
            values["margin"] = a - b
        return values
    message: str = Field(
        description="Actionable: what was violated, by how much, and what to "
        "change in selection to fix it"
    )
    actual_value: float | None = None
    limit_value: float | None = None
    unit: str | None = None
    remedy_hint: str | None = None


class CellResult(BaseModel):
    evaluated_below_target: bool = False
    """One candidate configuration evaluated at one scenario point.

    This is the atomic unit of computation. A full run produces tens of
    thousands of these; only aggregates and a sampled subset are persisted.
    """

    model_config = ConfigDict(frozen=True)

    config_id: str
    point_id: str
    month: float
    envelope: EnvelopeCase

    # Hydraulics
    pip_psi: float
    pwf_psi: float
    intake_temp_f: float
    tdh_ft: float
    total_fluid_intake_bpd: float
    liquid_rate_bpd: float
    mixture_sg: float

    # Gas
    free_gas_fraction_at_intake: float
    free_gas_fraction_entering_pump: float
    gas_strategy: str
    head_degradation_factor: float
    turpin_stable: bool

    # Pump performance
    head_developed_ft: float
    head_required_ft: float
    head_margin_frac: float = Field(
        description="(developed - required) / required. Near zero means the pump "
        "is exactly matched; large positive means it is oversized and will be "
        "throttled or run off-curve."
    )
    efficiency_frac: float
    bhp_hp: float

    # Zone — the scoring input (§6.4)
    zone: OperatingZone
    q_over_qbep: float
    distance_from_bep_frac: float
    zone_severity: float = Field(
        description="Continuous 0 (at BEP) to 1 (curve edge). Makes the four "
        "categorical zones scoreable over time."
    )

    # Equipment loading
    motor_loading_frac: float | None = None
    equipment_checks_performed: bool = False
    motor_type: Literal["induction", "permanent_magnet"] | None = None
    motor_vsd_required: bool = False
    motor_amps_basis: str | None = Field(
        default=None,
        description="Which form produced operating_amps: the B.14.1 identity "
        "from published data, the same identity from operator-supplied data, or "
        "the nameplate-scaling fallback. Propagates into cable gauge.",
    )
    motor_uses_operator_supplied_data: bool = False
    motor_nameplate_amps_conflict: bool = False
    motor_magnet_gate: str | None = Field(
        default=None,
        description="B.16 demagnetization gate outcome for this cell. Never "
        "'pass': intake temperature is a lower bound on magnet temperature, so "
        "the gate can be falsified but not cleared without a vendor thermal model.",
    )
    shaft_hp_utilization: float | None = None
    # Framework 6C.4 shaft check. Reported separately from
    # shaft_hp_utilization so a consumer can tell whether the governing
    # nameplate check ran at all: shaft_nameplate_check_possible False means
    # shaft_hp_utilization is against the operating load and is NOT the
    # governing check. A skipped safety check must not read as a passed one.
    shaft_nameplate_utilization: float | None = None
    shaft_nameplate_check_possible: bool = False
    shaft_fracture_risk: bool = False
    motor_nameplate_hp: float | None = None
    shaft_hp_operating_load: float | None = None
    cable_voltage_drop_frac: float | None = None
    surface_voltage_v: float | None = None
    # Framework 6D.2 cooling range. The verdict is carried instead of a
    # boolean because four of the five outcomes are not "pass" or "fail":
    # inside the published viscous band is unresolved, and an active erosion
    # or separation mechanism with no cataloged bound is UNCHECKED. Collapsing
    # these to a boolean is what the enum exists to prevent.
    cooling_velocity_ft_s: float | None = None
    cooling_verdict: str | None = None
    cooling_floor_applied_ft_s: float | None = None
    cooling_floor_basis: str | None = None
    cooling_upper_bound_checked: bool = False
    motor_self_heating: MotorSelfHeating | None = None
    cable_self_heating: CableSelfHeating | None = None
    motor_operating_current: OperatingCurrent | None = Field(
        default=None,
        description="Framework §6 phasor load-current decomposition for "
        "this cell's motor. Populated whenever the phasor form ran; None "
        "if the linear fallback was taken because no PF or override was "
        "available. The scalar current is still on ``motor_amps_basis`` "
        "and the cell's operating_amps in the sizing record.",
    )

    # Gate
    gate_status: GateStatus = GateStatus.PASSED
    violations: list[ConstraintViolation] = Field(default_factory=list)

    converged: bool = True
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_acceptable(self) -> bool:
        return (
            self.gate_status
            in {GateStatus.PASSED, GateStatus.PASSED_WITH_WARNINGS, GateStatus.FAILED_SOFT}
            and self.zone
            in {OperatingZone.BEP, OperatingZone.OPERATING_RANGE}
            and self.converged
            # A flow point near BEP is not proof of sufficient head. Keep
            # trajectory shortfalls reportable without calling them operable.
            and self.head_developed_ft >= self.head_required_ft - 1e-6
            and self.turpin_stable
            and not self.shaft_fracture_risk
            and self.equipment_checks_performed
            and (self.motor_loading_frac is None or self.motor_loading_frac <= 1)
            and not any(v.constraint_type=="mechanical" and v.unit=="lb" and v.margin is not None and v.margin>0 for v in self.violations)
            # Selection is permitted despite these warnings; a claim of
            # verified operation/recovery is a distinct, stricter assertion.
            and not any(v.constraint_type in {"thermal", "electrical"} for v in self.violations)
        )


# =============================================================================
# Validity boundary — the headline output (§6.4)
# =============================================================================


class ValidityBoundary(BaseModel):
    """When the design stops working, and why.

    Framework §6.4: "The report doesn't say 'the design works' — it says WHEN the
    design will stop working. An explicit validity boundary, which customers
    normally never receive."
    """

    model_config = ConfigDict(frozen=True)

    valid_until_months: float | None = Field(
        description="Months until the design leaves acceptable operation at the "
        "base case. None means it remains acceptable across the whole horizon."
    )
    valid_until_months_min: float | None = Field(
        default=None, description="Earliest boundary across the scenario envelope"
    )
    valid_until_months_max: float | None = Field(
        default=None, description="Latest boundary across the scenario envelope"
    )
    limiting_mechanism: str = Field(
        description="Engineer-readable causal chain, e.g. 'water cut rise -> "
        "PI-driven rate decline -> downthrust'"
    )
    limiting_zone: OperatingZone | None = None
    limiting_violation: ConstraintViolation | None = None
    valid_through_horizon: bool = False
    horizon_months: float = 24.0


class VSDRecoveryPoint(BaseModel):
    """Auditable evidence at a tested month and frequency, not interpolation."""

    month: float
    frequency_hz: float
    head_developed_ft: float
    head_required_ft: float
    motor_loading_frac: float
    cable_voltage_drop_frac: float
    motor_winding_temp_f: float
    cable_conductor_temp_f: float


class VSDRecovery(BaseModel):
    """What frequency can reclaim (§6.4).

    Omitted entirely when no VSD is present — the absence is itself information,
    and showing an empty section invites the reader to assume it was checked.
    """

    model_config = ConfigDict(frozen=True)

    vsd_available: bool
    assessment: Literal["verified", "not_needed", "no_recovery", "unverified"] = "unverified"
    frequency_band_hz: tuple[float, float] | None = None
    extended_validity_months: float | None = None
    recovered_horizon_frac: float | None = Field(
        default=None,
        description="Fraction of the out-of-range horizon that frequency changes "
        "can bring back into acceptable operation",
    )
    frequency_schedule: list[tuple[float, float]] = Field(
        default_factory=list,
        description="Recommended (month, frequency_hz) operating schedule",
    )
    verified_points: list[VSDRecoveryPoint] = Field(default_factory=list)
    note: str | None = None


# =============================================================================
# Timeline — the report format of §6.4
# =============================================================================


class TimelineEntry(BaseModel):
    """One row of the target report format from framework §6.4:

        Day 1   -> operating range, near BEP
        +2 mo   -> operating range
        +6 mo   -> [warn] left zone (down thrust)
        +1 yr   -> [warn] left zone, deep
    """

    model_config = ConfigDict(frozen=True)

    month: float
    label: str = Field(description="'Day 1', '+2 mo', '+1 yr'")
    zone: OperatingZone
    zone_description: str
    q_over_qbep: float
    severity: float
    flagged: bool
    rate_bpd: float
    pip_psi: float
    efficiency_frac: float
    motor_loading_frac: float | None = None
    head_developed_ft: float | None = None
    head_required_ft: float | None = None
    note: str | None = None


# =============================================================================
# Ranked candidate
# =============================================================================


class ScoreBreakdown(BaseModel):
    """Transparent scoring — an opaque rank is not actionable."""

    model_config = ConfigDict(frozen=True)

    total_score: float
    objective: str
    zone_quality_score: float
    time_coverage_frac: float = Field(
        description="Fraction of the horizon spent in acceptable zones"
    )
    bep_time_frac: float
    efficiency_avg_frac: float
    envelope_robustness: float = Field(
        description="How consistently the config performs across min/base/max. A "
        "config that only works in the base case is not a robust design."
    )
    vsd_dependence_penalty: float = 0.0
    soft_constraint_penalty: float = 0.0
    energy_cost_index: float | None = None
    explanation: str = ""


class CandidateResult(BaseModel):
    comparable_cost: float | None = None
    zone_duration_months: float = 0.0
    target_bep_distance: float | None = None
    ranking_mode: str = "run_life"
    efficiency_rank: int | None = None
    bep_rank: int | None = None
    run_life_rank: int | None = None
    synthetic: bool = False
    engineering: dict = Field(default_factory=dict)
    """A configuration that survived the gates, scored across the full trajectory."""

    model_config = ConfigDict(frozen=True)

    rank: int
    configuration: PumpConfiguration
    score: ScoreBreakdown
    validity: ValidityBoundary
    vsd_recovery: VSDRecovery | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)

    design_point: CellResult = Field(
        description="The month-0 base-case cell — the classical 'design point'"
    )
    cells_sampled: list[CellResult] = Field(
        default_factory=list,
        description="Representative cells across the trajectory, for charting",
    )

    soft_violations: list[ConstraintViolation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    depends_on_estimated_catalog_data: bool = False
    motor_type: Literal["induction", "permanent_magnet"] | None = Field(
        default=None,
        description="B.16 fork. Drives VSD necessity and which thermal gates apply.",
    )
    requires_vsd: bool = Field(
        default=False,
        description="B.16: a permanent magnet motor cannot start across the line, "
        "so a VSD with PMM rotor-position control is required hardware rather "
        "than an optimization option.",
    )
    uses_operator_supplied_motor_data: bool = Field(
        default=False,
        description="Operator-supplied vendor motor data was substituted for "
        "absent catalog values on this candidate. Not a defect flag: it means "
        "the electrical result is better grounded than the catalog alone allows. "
        "Disclosed because the numbers are then not reproducible from the "
        "catalog version in the provenance triple alone.",
    )
    amps_basis: str | None = Field(
        default=None,
        description="B.14.1 basis for the operating current on this candidate.",
    )
    shaft_fracture_risk: bool = Field(
        default=False,
        description="Framework 6C.4: motor nameplate power exceeds the weakest "
        "shaft/bearing rating in the string. Per 6C.4 this candidate is "
        "reported and warned rather than rejected, so that the large-shaft "
        "build option stays visible -- it is NOT cleared for install.",
    )
    tolerance: ToleranceAssessment | None = Field(
        default=None,
        description="Framework 6B.2: whether any decision this candidate rests "
        "on changes somewhere inside the API RP 11S2 acceptance band, and "
        "therefore whether loading the unit test report is worth the time. "
        "Never a violation and never scored -- a tolerance band is uncertainty "
        "about a number, not a defect in a design.",
    )
    cooling_verdict: str | None = Field(
        default=None,
        description="Framework 6D.2 cooling verdict at the design point.",
    )
    cooling_velocity_ft_s: float | None = None
    cooling_floor_applied_ft_s: float | None = None
    cooling_upper_bound_checked: bool = Field(
        default=False,
        description=(
            "False means no numeric upper bound was available, so the "
            "erosion/separation bound is UNCHECKED -- not satisfied."
        ),
    )
    self_heating: MotorSelfHeating | None = Field(
        default=None,
        description="Framework §6D.2 two-term display at the design point: "
        "fluid temperature and calculated self-heating rise, with the "
        "underlying load / velocity / efficiency / gas factors. Shown "
        "separately so the engineer sees which lever helps -- a shroud "
        "raises velocity, a high-temperature build raises the rating.",
    )
    cable_self_heating: CableSelfHeating | None = Field(
        default=None,
        description="Framework §6D.2 row 4 -- calculated cable temperature "
        "at the design point: fluid temperature plus I\u00b2R rise removed "
        "by annular convection. Compared against the cable insulation "
        "class rating (B.14.5). Shown as two terms alongside the motor "
        "analog so a shroud (helps the rise term) can be distinguished "
        "from a high-temperature MLE / insulation build (helps the "
        "rating term).",
    )
    motor_operating_current: OperatingCurrent | None = Field(
        default=None,
        description="Framework \u00a76 phasor load-current decomposition "
        "at the design point. Populated when the phasor form ran "
        "(cataloged PF or operator override); ``None`` means the "
        "linear fallback was taken because no PF/override was "
        "available. The decomposition (I_\u03bc + I_L) makes it visible "
        "whether the current sizing the cable is dominated by the "
        "magnetizing branch (light load) or the load branch "
        "(nameplate load).",
    )
    shaft_nameplate_check_performed: bool = Field(
        default=False,
        description="Whether the governing 6C.4 nameplate check could run. "
        "False means shaft utilization is against the operating load only.",
    )
    nameplate_amps_conflict: bool = Field(
        default=False,
        description="The B.14.1 current and the cataloged nameplate current for "
        "the selected motor disagree beyond tolerance. The conservative (higher) "
        "current was used, so the cable is not undersized, but an unresolved "
        "electrical data contradiction is outstanding.",
    )
    magnet_thermal_limit_unverified: bool = Field(
        default=False,
        description="B.16 magnet demagnetization gate could not be cleared for "
        "this permanent magnet motor. A disclosure flag, not a scored penalty: "
        "the gap is in the catalog rather than the design, and scoring it would "
        "bias selection toward induction motors purely on data availability.",
    )

    string_summary: str | None = Field(
        default=None,
        description="ESP string assembly per §12: tubing -> head -> pump sections "
        "-> GH/GS/intake -> seal -> motor -> sensor",
    )


class RejectedConfiguration(BaseModel):
    """Why a candidate died.

    Framework §12's ``failed -> return to selection`` edge. Surfacing rejections
    with reasons gives the engineer a "here's what we ruled out and why" view
    that no current tool provides — and it is how a junior learns the constraint
    landscape without a mentor (§2.2).
    """

    model_config = ConfigDict(frozen=True)

    configuration: PumpConfiguration
    stage_rejected: Literal[
        "geometry_prescreen",
        "electrical_prescreen",
        "no_motor_available",
        "no_cable_available",
        "mechanical_gate",
        "hydraulic_infeasible",
        "gas_infeasible",
        "non_convergence",
        "zone_never_acceptable",
        "constraints_gate_absolute",
        "constraints_gate_hard",
    ]
    reason: str
    violations: list[ConstraintViolation] = Field(default_factory=list)
    remedy_hints: list[str] = Field(
        default_factory=list,
        description="Deduplicated, actionable fixes. A rejection that names the "
        "fix teaches the constraint landscape; a bare rejection does not.",
    )


# =============================================================================
# Assumption ledger
# =============================================================================


class AssumptionLedgerEntry(BaseModel):
    """Framework §2.2: "Explicit, flagged assumptions instead of silent ones."

    Every assumption that materially affected the design, with its bias and
    reason, plus a sensitivity note where the engine can compute one.
    """

    model_config = ConfigDict(frozen=True)

    field_path: str
    value: str
    unit: str | None
    source: str
    confidence: float
    basis: str | None = None
    bias: str | None = None
    rationale: str | None = None
    unbiased_value: str | None = None
    materiality: Literal["critical", "significant", "minor"] = "significant"
    sensitivity_note: str | None = Field(
        default=None,
        description="What changes if this assumption is wrong, e.g. 'if GOR is "
        "actually 550 rather than 800, a gas handler is no longer required'",
    )


# =============================================================================
# The complete result
# =============================================================================


class RunProvenance(BaseModel):
    """Everything needed to reproduce this design byte-for-byte.

    Framework §1 requires reproducibility to be model-independent. A case can be
    replayed with the engine alone from these four identifiers, with no LLM in
    the loop — which is what makes the system defensible when a design is
    questioned two years later.
    """

    model_config = ConfigDict(frozen=True)

    case_hash: str
    config_hash: str
    catalog_version: str
    engine_version: str
    computed_at: str | None = None
    compute_ms: float | None = None
    cells_evaluated: int = 0
    configs_enumerated: int = 0
    configs_surviving: int = 0


class FeasibilityVerdict(str, Enum):
    FEASIBLE = "feasible"
    FEASIBLE_WITH_CAVEATS = "feasible_with_caveats"
    TARGET_UNACHIEVABLE = "target_unachievable"
    NO_VIABLE_CONFIGURATION = "no_viable_configuration"
    BLOCKED_MISSING_DATA = "blocked_missing_data"


class DesignResult(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def preserve_historical_policy(cls, values):
        if not isinstance(values,dict) or "decision_policy" in values:
            return values
        provenance=values.get("provenance")
        if not isinstance(provenance,dict) or provenance.get("engine_version") in (None,"0.2.0"):
            return values
        import copy
        values=copy.deepcopy(values)
        values["decision_policy"]="legacy"
        def walk(value):
            if isinstance(value,dict):
                if "rigidity" in value and "constraint_type" in value:
                    value["policy"]="legacy"
                for child in value.values():walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        walk(values)
        return values
    """The complete deterministic output. Immutable, hashable, replayable.

    FACTS live here. JUDGMENTS live in ``judgments`` and reference facts by ID.
    The separation is structural, not stylistic (§1, §7).
    """

    model_config = ConfigDict(frozen=True)
    ranking_comparison: list[dict] = Field(default_factory=list)
    decision_policy: str = "expert_signoff_2026-10-02"
    synthetic: bool = False

    design_id: str
    case_id: str
    tenant_id: str

    verdict: FeasibilityVerdict
    verdict_explanation: str

    branch: TaskBranch
    branch_rationale: str

    candidates: list[CandidateResult] = Field(default_factory=list)
    rejected: list[RejectedConfiguration] = Field(default_factory=list)
    rejection_summary: dict[str, int] = Field(
        default_factory=dict,
        description="Count of rejections by stage — shows the engineer where the "
        "design space is tight",
    )

    scenario_points: list[ScenarioPoint] = Field(default_factory=list)
    assumption_ledger: list[AssumptionLedgerEntry] = Field(default_factory=list)
    # Framework 3.0. Kept separate from the assumption ledger on purpose: the
    # ledger is where the system stands behind its own choices and invites
    # challenge, while this is where it states which numbers it used as given
    # and is not in a position to check. One list of "uncertain inputs" would
    # blur who is answerable for each.
    trust_register: list[TrustRegisterEntry] = Field(
        default_factory=list,
        description=(
            "Every externally-owned value the design rests on, with the party "
            "answerable for its accuracy (3.0)."
        ),
    )
    responsibility_boundary_crossings: list[str] = Field(
        default_factory=list,
        description=(
            "Field paths where a 3.0 customer-owned quantity was supplied by "
            "the system or replaced by an engineer override."
        ),
    )

    # Framework §5.1 — hard stops. Non-empty means verdict is BLOCKED_MISSING_DATA.
    blocking_data_gaps: list[str] = Field(default_factory=list)
    recommended_data_requests: list[str] = Field(
        default_factory=list,
        description="Specific questions to ask the customer, in priority order",
    )

    input_confidence: float = Field(
        default=0.0,
        description="Confidence of the weakest load-bearing input. A design is "
        "only as trustworthy as its weakest input, so this is a minimum rather "
        "than a mean.",
    )
    ranking_stability: RankingStability | None = Field(
        default=None,
        description="Framework 6B.2 + 3.5: whether the presented order of "
        "candidates survives the acceptance tolerance that can be propagated "
        "into the score. Carries its own statement of what was NOT propagated.",
    )
    uses_estimated_catalog_data: bool = False
    has_nameplate_amps_conflict: bool = Field(
        default=False,
        description="At least one candidate's B.14.1 current contradicts its "
        "cataloged nameplate current.",
    )
    has_shaft_fracture_risk: bool = Field(
        default=False,
        description="At least one presented candidate fails the framework 6C.4 "
        "shaft-vs-nameplate check. Presented deliberately (6C.4 warns rather "
        "than rejects) so the build-variant remedy stays visible.",
    )
    uses_operator_supplied_motor_data: bool = Field(
        default=False,
        description="At least one candidate used operator-supplied motor data, "
        "so the electrical results are not reproducible from the catalog version "
        "alone -- the case is part of the input.",
    )
    requires_magnet_thermal_verification: bool = Field(
        default=False,
        description="At least one surviving candidate uses a permanent magnet "
        "motor whose B.16 demagnetization gate is unresolved.",
    )

    provenance: RunProvenance

    # --- The agentic overlay, strictly separated ----------------------------
    judgments: list[Judgment] = Field(
        default_factory=list,
        description="§7 empirical overlay. References facts by ID; never modifies "
        "them. Rendered with distinct visual treatment.",
    )

    engine_warnings: list[str] = Field(default_factory=list)

    @property
    def best(self) -> CandidateResult | None:
        return self.candidates[0] if self.candidates else None

    @property
    def is_actionable(self) -> bool:
        return self.verdict in {
            FeasibilityVerdict.FEASIBLE,
            FeasibilityVerdict.FEASIBLE_WITH_CAVEATS,
        }

    def facts_index(self) -> dict[str, float | str]:
        """Flat map of fact_id -> value, for grounding the narrative agent.

        The narrative agent receives values only through this index, and its
        output is checked for numbers not present here. A numeric hallucination
        fails the response and regenerates rather than reaching an engineer —
        the cheapest possible guard against the failure mode that would destroy
        trust fastest.
        """
        facts: dict[str, float | str] = {
            "verdict": self.verdict.value,
            "branch": self.branch.value,
            "input_confidence": round(self.input_confidence, 3),
            "n_candidates": len(self.candidates),
            "n_rejected": len(self.rejected),
        }
        for cand in self.candidates:
            p = f"candidate_{cand.rank}"
            facts[f"{p}.pump"] = cand.configuration.pump_model
            facts[f"{p}.stages"] = cand.configuration.stages
            facts[f"{p}.frequency_hz"] = cand.configuration.frequency_hz
            facts[f"{p}.score"] = round(cand.score.total_score, 4)
            facts[f"{p}.tdh_ft"] = round(cand.design_point.tdh_ft, 1)
            facts[f"{p}.pip_psi"] = round(cand.design_point.pip_psi, 1)
            facts[f"{p}.efficiency"] = round(cand.design_point.efficiency_frac, 4)
            facts[f"{p}.free_gas_frac"] = round(
                cand.design_point.free_gas_fraction_at_intake, 4
            )
            facts[f"{p}.q_over_qbep"] = round(cand.design_point.q_over_qbep, 4)
            facts[f"{p}.zone"] = cand.design_point.zone.value
            if cand.configuration.motor_hp is not None:
                facts[f"{p}.motor_hp"] = cand.configuration.motor_hp
            if cand.design_point.motor_loading_frac is not None:
                facts[f"{p}.motor_loading"] = round(
                    cand.design_point.motor_loading_frac, 4
                )
            if cand.validity.valid_until_months is not None:
                facts[f"{p}.valid_until_months"] = round(
                    cand.validity.valid_until_months, 2
                )
            facts[f"{p}.limiting_mechanism"] = cand.validity.limiting_mechanism
        return facts
