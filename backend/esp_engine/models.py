"""Domain model — the ESP design case.

Framework refs: §3 (input data classification), §4 (decision gate 1),
§5 (input data priority), §6 (time trajectory).

The three input classes of §3 are three distinct types, not tags on a flat
record, because they live in different branches of the logic:

    Expectations  — what we aim for; may be proven unrealistic
    Constraints   — hard limits; enforced by the constraints gate
    Complications — operating challenges; modify calculation AND selection
    Reference     — the anchor; its presence selects the Decision Gate 1 branch

These Pydantic models serve three roles at once: the engine's domain model, the
API contract, and the LLM structured-extraction schema. One definition of what
an ESP case is means the intake agent cannot produce a shape the engine rejects.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .provenance import Source, Tracked


# =============================================================================
# Enums
# =============================================================================


class TaskBranch(str, Enum):
    """Framework §4 — Decision Gate 1. Everything downstream depends on this."""

    A_REPLACEMENT = "A_replacement"  # history known, anchored, high confidence
    B_NEW = "B_new"  # no anchor, creative, range output


class TaskType(str, Enum):
    """The specific request type. Maps onto a branch."""

    ESP_REPLACEMENT = "esp_replacement"  # → Branch A
    ESP_REDESIGN = "esp_redesign"  # → Branch A (optimizing an existing install)
    NEW_WELL = "new_well"  # → Branch B
    GAS_LIFT_CONVERSION = "gas_lift_conversion"  # → Branch B
    NATURAL_FLOW_CONVERSION = "natural_flow_conversion"  # → Branch B
    ROD_PUMP_CONVERSION = "rod_pump_conversion"  # → Branch B
    POST_STIMULATION = "post_stimulation"  # → Branch B

    @property
    def branch(self) -> TaskBranch:
        return (
            TaskBranch.A_REPLACEMENT
            if self in {TaskType.ESP_REPLACEMENT, TaskType.ESP_REDESIGN}
            else TaskBranch.B_NEW
        )


class DataAccessLevel(str, Enum):
    """Framework §10 — operating modes by data access level."""

    FULL = "full"  # trends, monitoring, history
    PARTIAL = "partial"  # reports, teardowns, files on request
    NONE = "none"  # correspondence and calls only


class ComplicationType(str, Enum):
    """Framework §3.3."""

    GAS = "gas"  # critical — underestimation kills the installation
    HIGH_TEMPERATURE = "high_temperature"
    VISCOUS_OIL = "viscous_oil"
    SOLIDS = "solids"
    CORROSION = "corrosion"
    SCALE = "scale"
    ASPHALTENE = "asphaltene"
    EMULSION = "emulsion"
    H2S = "h2s"
    CO2 = "co2"


class Severity(str, Enum):
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class Rigidity(str, Enum):
    """Framework §3.2 — how hard a constraint is."""

    ABSOLUTE = "absolute"  # physics. cannot be violated, ever.
    HARD = "hard"  # engineering limit. violation → return to selection.
    SOFT = "soft"  # commercial. can be ordered, carries lead time.


class CheckStage(str, Enum):
    """Framework §3.2 — when a constraint is evaluated."""

    AT_INPUT = "at_input"  # prunes candidates before any calculation
    AFTER_SELECTION = "after_selection"  # post-hoc gate; failure returns to selection


class LiftMethod(str, Enum):
    ESP = "esp"
    GAS_LIFT = "gas_lift"
    NATURAL_FLOW = "natural_flow"
    ROD_PUMP = "rod_pump"
    PCP = "pcp"
    JET_PUMP = "jet_pump"
    NONE = "none"


class FailureLocation(str, Enum):
    PUMP = "pump"
    INTAKE = "intake"
    GAS_SEPARATOR = "gas_separator"
    SEAL = "seal"
    MOTOR = "motor"
    CABLE = "cable"
    SENSOR = "sensor"
    SURFACE = "surface"
    UNKNOWN = "unknown"


class OperatingZone(str, Enum):
    """Framework §6.4 — position of the operating point on the pump curve."""

    BEP = "bep"  # target zone
    OPERATING_RANGE = "operating_range"  # acceptable
    DOWNTHRUST = "downthrust"  # left — underload
    UPTHRUST = "upthrust"  # right — overload
    OFF_CURVE_LEFT = "off_curve_left"  # beyond the curve entirely
    OFF_CURVE_RIGHT = "off_curve_right"


# =============================================================================
# Well geometry — a HARD STOP input (§5.1)
# =============================================================================


class DeviationSurveyPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    md_ft: float = Field(description="Measured depth")
    tvd_ft: float = Field(description="True vertical depth")
    inclination_deg: float = 0.0
    azimuth_deg: float | None = None
    dogleg_severity_deg_per_100ft: float | None = None


class CasingSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    od_in: float
    weight_lb_per_ft: float | None = None
    id_in: float
    drift_id_in: float | None = None
    top_md_ft: float = 0.0
    bottom_md_ft: float


class WellGeometry(BaseModel):
    """Framework §5.1: a HARD STOP. Not a calculation parameter but a validation
    one — it guarantees the ESP will run in hole.

    The engine will refuse to produce a design without casing ID. This is
    deliberate: the one thing worse than no design is a design for equipment
    that physically cannot reach setting depth.
    """

    casing_sections: list[CasingSection] = Field(default_factory=list)
    tubing_id_in: Tracked[float] | None = None
    tubing_od_in: Tracked[float] | None = None
    deviation_survey: list[DeviationSurveyPoint] = Field(default_factory=list)
    is_vertical: bool = Field(
        default=False,
        description="If true, a deviation survey is not required (§5.1)",
    )
    perforation_top_md_ft: Tracked[float] | None = None
    perforation_bottom_md_ft: Tracked[float] | None = None
    total_depth_md_ft: Tracked[float] | None = None

    def casing_id_at_md(self, md_ft: float) -> float | None:
        """Casing ID at a given measured depth. Returns None if not covered."""
        for section in self.casing_sections:
            if section.top_md_ft <= md_ft <= section.bottom_md_ft:
                return section.drift_id_in or section.id_in
            return None
        return None

    def min_casing_id_to_depth(self, md_ft: float) -> float | None:
        """Tightest restriction the string must pass through to reach md_ft.

        This — not the ID at setting depth — is what limits equipment OD.
        Equipment has to get *past* every restriction above it.
        """
        ids = [
            (s.drift_id_in or s.id_in)
            for s in self.casing_sections
            if s.top_md_ft < md_ft
        ]
        return min(ids) if ids else None

    def max_dogleg_to_depth(self, md_ft: float) -> float | None:
        dls = [
            p.dogleg_severity_deg_per_100ft
            for p in self.deviation_survey
            if p.md_ft <= md_ft and p.dogleg_severity_deg_per_100ft is not None
        ]
        return max(dls) if dls else None

    def tvd_at_md(self, md_ft: float) -> float:
        """Linear interpolation of TVD from the survey. Falls back to MD when the
        well is vertical or unsurveyed."""
        if self.is_vertical or not self.deviation_survey:
            return md_ft
        survey = sorted(self.deviation_survey, key=lambda p: p.md_ft)
        if md_ft <= survey[0].md_ft:
            return survey[0].tvd_ft
        if md_ft >= survey[-1].md_ft:
            return survey[-1].tvd_ft
        for lo, hi in zip(survey, survey[1:]):
            if lo.md_ft <= md_ft <= hi.md_ft:
                span = hi.md_ft - lo.md_ft
                if span == 0:
                    return lo.tvd_ft
                frac = (md_ft - lo.md_ft) / span
                return lo.tvd_ft + frac * (hi.tvd_ft - lo.tvd_ft)
        return md_ft

    @property
    def has_hard_stop_data(self) -> bool:
        """§5.1: casing ID plus deviation survey (unless vertical)."""
        if not self.casing_sections:
            return False
        return self.is_vertical or bool(self.deviation_survey)

    def missing_hard_stop_data(self) -> list[str]:
        missing: list[str] = []
        if not self.casing_sections:
            missing.append("casing program (ID vs depth)")
        if not self.is_vertical and not self.deviation_survey:
            missing.append(
                "deviation survey (or explicit confirmation the well is vertical)"
            )
        return missing


# =============================================================================
# §3.1 EXPECTATIONS — what is expected from the well
# =============================================================================


class Expectations(BaseModel):
    """Framework §3.1. What we aim for. If expectations are unrealistic, the
    calculation reveals it — that is a legitimate output, not an error."""

    target_rate_bpd: Tracked[float] | None = Field(
        default=None,
        description="THE primary parameter, anchor of the entire design (§3.1, §5.1)",
    )
    target_rate_basis: Literal["total_liquid", "oil", "gross"] = "total_liquid"

    min_acceptable_rate_bpd: Tracked[float] | None = None
    max_acceptable_rate_bpd: Tracked[float] | None = None

    wellhead_pressure_psi: Tracked[float] | None = None
    casing_pressure_psi: Tracked[float] | None = None
    target_pip_psi: Tracked[float] | None = Field(
        default=None, description="Desired pump intake pressure, if specified"
    )
    desired_drawdown_psi: Tracked[float] | None = None

    setting_depth_md_ft: Tracked[float] | None = None

    design_life_months: Tracked[float] | None = Field(
        default=None,
        description="Horizon the design must remain valid over. Drives the "
        "trajectory scoring window (§6).",
    )

    anticipated_complications: list[ComplicationType] = Field(default_factory=list)

    @property
    def has_anchor(self) -> bool:
        return self.target_rate_bpd is not None


# =============================================================================
# §3.2 CONSTRAINTS — hard limits
# =============================================================================


class GeometryConstraints(BaseModel):
    """Rigidity: ABSOLUTE (physics). Checked: AT INPUT."""

    max_equipment_od_in: Tracked[float] | None = None
    min_casing_id_in: Tracked[float] | None = None
    max_dogleg_deg_per_100ft: Tracked[float] = Field(
        default_factory=lambda: Tracked(
            value=6.0,
            unit="deg/100ft",
            source=Source.DEFAULT,
            note="Common industry limit for running ESP equipment; vendor- and "
            "length-dependent. Configurable.",
        )
    )
    max_setting_depth_md_ft: Tracked[float] | None = None
    max_setting_dogleg_deg_per_100ft: Tracked[float] = Field(
        default_factory=lambda: Tracked(value=2.0, unit="deg/100ft", source=Source.DEFAULT)
    )
    max_string_length_ft: Tracked[float] | None = None


class MotorElectricalOverride(BaseModel):
    """Vendor motor data the operator holds but the public catalog does not.

    Framework B.14.1 needs power factor and efficiency to compute full-load
    current; B.16 needs a magnet demagnetization temperature to evaluate the
    permanent-magnet thermal gate. No public datasheet for any cataloged ESP
    motor publishes any of the three, so the catalog carries None and the engine
    falls back to labelled approximations.

    An operator who bought the motor usually has the real numbers on a purchase
    datasheet. This is how they get in without the catalog claiming values it
    does not have. Two properties matter:

    - It is keyed on a specific catalog motor id, because power factor is a
      property of a machine, not of a well. A single case-level power factor
      applied to whichever motor the enumerator happened to pick would be
      exactly the kind of plausible-looking wrong number this engine exists to
      avoid producing.
    - Each value is Tracked, so it carries its own source and confidence. A
      figure read off a purchase order is not the same evidence as one measured
      on a test stand, and B.15 transformer sizing will inherit whichever it is.
    """

    model_config = ConfigDict(frozen=True)

    motor_id: str = Field(
        description="Catalog motor id this data belongs to. Must exist in the "
        "loaded catalog: an override for an id that is not there would silently "
        "do nothing while the operator believed B.14.1 had been engaged."
    )
    power_factor: Tracked[float] | None = None
    efficiency: Tracked[float] | None = None
    demag_temp_f: Tracked[float] | None = Field(
        default=None,
        description="Magnet demagnetization temperature. Only meaningful on a "
        "permanent magnet motor; supplying it for an induction motor is "
        "rejected rather than ignored.",
    )

    @field_validator("power_factor")
    @classmethod
    def _check_pf(cls, v: Tracked[float] | None) -> Tracked[float] | None:
        if v is not None and not 0.0 < v.value <= 1.0:
            raise ValueError(
                f"power_factor {v.value} is outside (0, 1]. A power factor above "
                "unity is not a conservative input, it is a sign the number was "
                "read from the wrong column."
            )
        return v

    @field_validator("efficiency")
    @classmethod
    def _check_eff(cls, v: Tracked[float] | None) -> Tracked[float] | None:
        if v is not None and not 0.0 < v.value < 1.0:
            raise ValueError(
                f"efficiency {v.value} is outside (0, 1). Supply a fraction, not "
                "a percentage: 0.94, not 94."
            )
        return v

    @field_validator("demag_temp_f")
    @classmethod
    def _check_demag(cls, v: Tracked[float] | None) -> Tracked[float] | None:
        if v is not None and v.value <= 0.0:
            raise ValueError(f"demag_temp_f {v.value} must be above 0 F")
        return v

    @property
    def is_empty(self) -> bool:
        """An override that overrides nothing is a configuration mistake, not a
        no-op: it signals someone intended to supply data and did not."""
        return (
            self.power_factor is None
            and self.efficiency is None
            and self.demag_temp_f is None
        )

    def unlocks_b14_1(self) -> bool:
        """B.14.1 needs BOTH factors. One of the two is not a partial unlock --
        there is no defensible way to compute I_FL from power factor alone."""
        return self.power_factor is not None and self.efficiency is not None


class ElectricalConstraints(BaseModel):
    """Rigidity: HARD. Checked: AT INPUT."""

    vsd_available: Tracked[bool] = Field(
        default_factory=lambda: Tracked(
            value=False,
            source=Source.DEFAULT,
            note="Absence of a VSD removes the frequency degree of freedom and "
            "eliminates the VSD recovery section of the report (§6.4).",
        )
    )
    frequency_min_hz: Tracked[float] | None = None
    frequency_max_hz: Tracked[float] | None = None
    fixed_frequency_hz: Tracked[float] | None = Field(
        default=None, description="If no VSD, the fixed line frequency"
    )
    available_surface_voltage_v: Tracked[float] | None = None
    max_surface_kva: Tracked[float] | None = None
    existing_cable_awg: Tracked[str] | None = None
    existing_cable_length_ft: Tracked[float] | None = None
    motor_data_overrides: tuple[MotorElectricalOverride, ...] = Field(
        default=(),
        description="Operator-supplied motor electrical data, keyed on catalog "
        "motor id. Unlocks B.14.1 full-load current and the B.16 magnet gate "
        "for the motors it covers.",
    )

    @field_validator("motor_data_overrides")
    @classmethod
    def _check_overrides(
        cls, v: tuple[MotorElectricalOverride, ...]
    ) -> tuple[MotorElectricalOverride, ...]:
        seen: set[str] = set()
        for override in v:
            if override.motor_id in seen:
                raise ValueError(
                    f"two overrides supplied for motor {override.motor_id!r}. "
                    "Which one wins would be decided by list order, which is not "
                    "a defensible way to resolve conflicting vendor data."
                )
            seen.add(override.motor_id)
            if override.is_empty:
                raise ValueError(
                    f"the override for motor {override.motor_id!r} supplies no "
                    "values. Remove it, or fill it in -- an empty override reads "
                    "as 'vendor data provided' in the report while changing "
                    "nothing in the calculation."
                )
        return v

    def override_for(self, motor_id: str) -> MotorElectricalOverride | None:
        for override in self.motor_data_overrides:
            if override.motor_id == motor_id:
                return override
        return None

    def achievable_frequencies(self, default: float = 60.0) -> list[float]:
        """The frequency degrees of freedom available to the enumerator."""
        if not self.vsd_available.value:
            fixed = (
                self.fixed_frequency_hz.value
                if self.fixed_frequency_hz is not None
                else default
            )
            return [fixed]
        lo = self.frequency_min_hz.value if self.frequency_min_hz else 40.0
        hi = self.frequency_max_hz.value if self.frequency_max_hz else 65.0
        step = 2.5
        freqs: list[float] = []
        f = lo
        while f <= hi + 1e-9:
            freqs.append(round(f, 2))
            f += step
        return freqs

    def frequency_band(self, default: float = 60.0) -> tuple[float, float] | None:
        """The continuous frequency band, or ``None`` without a VSD.

        ``None`` rather than a degenerate ``(60, 60)`` band: the absence of a
        frequency degree of freedom is a different fact from a band that happens
        to be one point wide, and §6.4's VSD recovery section must not be
        rendered at all when no VSD exists.
        """
        if not self.vsd_available.value:
            return None
        lo = self.frequency_min_hz.value if self.frequency_min_hz else 40.0
        hi = self.frequency_max_hz.value if self.frequency_max_hz else 65.0
        return (lo, hi)


class MechanicalConstraints(BaseModel):
    """Rigidity: HARD. Checked: AFTER SELECTION (gate).

    Framework §3.2 note: mechanical constraints are a post-hoc check. If the
    shaft can't carry the load — return to selection and change the config.
    """

    max_shaft_hp: Tracked[float] | None = None
    max_thrust_lb: Tracked[float] | None = None
    shaft_safety_factor: Tracked[float] = Field(
        default_factory=lambda: Tracked(value=1.0, source=Source.DEFAULT)
    )


class AvailabilityConstraints(BaseModel):
    """Rigidity: SOFT (can be ordered). Checked: AFTER SELECTION."""

    restrict_to_stock: Tracked[bool] = Field(
        default_factory=lambda: Tracked(value=False, source=Source.DEFAULT)
    )
    allowed_manufacturers: list[str] = Field(default_factory=list)
    preferred_pump_models: list[str] = Field(
        default_factory=list,
        description="Fleet standardization — what the customer already runs (§13)",
    )
    excluded_pump_models: list[str] = Field(default_factory=list)
    max_lead_time_days: Tracked[float] | None = None


class Constraints(BaseModel):
    """Framework §3.2. Applied with equal rigor at all times, regardless of task
    type."""

    geometry: GeometryConstraints = Field(default_factory=GeometryConstraints)
    electrical: ElectricalConstraints = Field(default_factory=ElectricalConstraints)
    mechanical: MechanicalConstraints = Field(default_factory=MechanicalConstraints)
    availability: AvailabilityConstraints = Field(
        default_factory=AvailabilityConstraints
    )


# =============================================================================
# §3.3 COMPLICATIONS — operating challenges
# =============================================================================


class Complication(BaseModel):
    """One operating challenge, with severity and its own provenance.

    Affects both the calculation and equipment selection (§3.3).
    """

    model_config = ConfigDict(frozen=True)

    type: ComplicationType
    severity: Severity = Severity.MODERATE
    evidence: Tracked[str] | None = Field(
        default=None,
        description="Why we believe this complication is present, e.g. a "
        "teardown finding or a produced-solids measurement",
    )
    quantity: Tracked[float] | None = Field(
        default=None,
        description="Quantified where possible: sand in pptb, H2S in ppm, "
        "temperature in degF",
    )
    quantity_unit: str | None = None

    @property
    def is_critical(self) -> bool:
        """Framework §3.3: gas is critical — underestimation means the
        installation will not work."""
        return self.type is ComplicationType.GAS


class Complications(BaseModel):
    items: list[Complication] = Field(default_factory=list)

    def has(self, ctype: ComplicationType) -> bool:
        return any(c.type is ctype for c in self.items)

    def get(self, ctype: ComplicationType) -> Complication | None:
        return next((c for c in self.items if c.type is ctype), None)

    def severity_of(self, ctype: ComplicationType) -> Severity:
        c = self.get(ctype)
        return c.severity if c else Severity.NONE

    @property
    def is_gassy(self) -> bool:
        """Triggers the upward GOR bias policy (§3.3, §5.2)."""
        return self.severity_of(ComplicationType.GAS) in {
            Severity.MODERATE,
            Severity.SEVERE,
        }

    @property
    def is_abrasive(self) -> bool:
        return self.severity_of(ComplicationType.SOLIDS) in {
            Severity.MODERATE,
            Severity.SEVERE,
        }


# =============================================================================
# §3.4 REFERENCE — the anchor
# =============================================================================


class TestPoint(BaseModel):
    """§5.3 BONUS data. PIP drifts with drawdown, so the date matters."""

    model_config = ConfigDict(frozen=True)

    date: str | None = None
    rate_bpd: Tracked[float] | None = None
    pip_psi: Tracked[float] | None = None
    water_cut_frac: Tracked[float] | None = None
    frequency_hz: Tracked[float] | None = None
    motor_amps: Tracked[float] | None = None
    wellhead_pressure_psi: Tracked[float] | None = None
    intake_temp_f: Tracked[float] | None = None


class PreviousInstallation(BaseModel):
    """Framework §3.4 — performance of the previous installation. Determines the
    logic branch (§4) and anchors Branch A calculations."""

    pump_model: Tracked[str] | None = None
    manufacturer: Tracked[str] | None = None
    stage_count: Tracked[int] | None = None
    motor_hp: Tracked[float] | None = None
    setting_depth_md_ft: Tracked[float] | None = None
    operating_frequency_hz: Tracked[float] | None = None
    gas_handling: Tracked[str] | None = None

    # Outcome — §5.3 bonus data, and the empirical layer's raw signal
    run_life_days: Tracked[float] | None = None
    still_running: bool = Field(
        default=False,
        description="If true, run_life_days is right-censored — the install has "
        "not failed yet. Critical for unbiased survival statistics (§8.2 "
        "survivorship).",
    )
    failure_mode: Tracked[str] | None = None
    failure_location: Tracked[FailureLocation] | None = None
    teardown_findings: Tracked[str] | None = None

    test_points: list[TestPoint] = Field(default_factory=list)
    lift_method: LiftMethod = LiftMethod.ESP

    @property
    def is_usable_anchor(self) -> bool:
        """Enough history to anchor a Branch A calculation.

        Requires a pump model AND at least one performance observation. A model
        name with no performance data is not an anchor — it is a hint.
        """
        return self.pump_model is not None and bool(self.test_points)


# =============================================================================
# §5.2 Reservoir & fluid properties — SOFT inputs
# =============================================================================


class FluidProperties(BaseModel):
    """Framework §5.2. Every field is SOFT — fillable by explicit assumption."""

    oil_api: Tracked[float] | None = None
    oil_sg: Tracked[float] | None = None
    water_sg: Tracked[float] | None = None
    gas_sg: Tracked[float] | None = None

    water_cut_frac: Tracked[float] | None = Field(
        default=None, description="Changes over time → handled by scenario (§5.2)"
    )
    gor_scf_stb: Tracked[float] | None = Field(
        default=None,
        description="From previous installation experience; for gassy reservoirs "
        "bias high (§5.2)",
    )
    bubble_point_psi: Tracked[float] | None = None

    oil_viscosity_cp: Tracked[float] | None = None
    oil_viscosity_at_temp_f: Tracked[float] | None = None

    h2s_ppm: Tracked[float] | None = None
    co2_mol_frac: Tracked[float] | None = None
    water_salinity_ppm: Tracked[float] | None = None
    sand_pptb: Tracked[float] | None = None


class ReservoirProperties(BaseModel):
    reservoir_pressure_psi: Tracked[float] | None = Field(
        default=None,
        description="Almost never provided, changes over time (§5.2)",
    )
    datum_depth_ft: Tracked[float] | None = None
    bht_f: Tracked[float] | None = Field(
        default=None, description="Bottomhole temperature"
    )
    temp_gradient_f_per_100ft: Tracked[float] | None = None
    surface_temp_f: Tracked[float] | None = None

    productivity_index_bpd_psi: Tracked[float] | None = None
    bubble_point_psi: Tracked[float] | None = None
    ipr_model: Literal["pi_linear", "vogel", "composite"] = "composite"

    formation: Tracked[str] | None = None
    region: Tracked[str] | None = None


# =============================================================================
# §6 Trajectory — drift over the well's life
# =============================================================================


class DriftModel(BaseModel):
    """How one parameter changes over the design horizon (§6.1).

    An interface, not a commitment: reservoir-simulation-grade forecasting is
    out of v0.1 scope, but the shape is pluggable so a better model drops in.
    """

    model_config = ConfigDict(frozen=True)

    parameter: str
    form: Literal["constant", "linear", "exponential", "harmonic", "logistic"] = (
        "constant"
    )
    rate_per_year: float = Field(
        default=0.0,
        description="Fractional change per year for exponential/harmonic, "
        "absolute change per year for linear",
    )
    terminal_value: float | None = Field(
        default=None, description="Asymptote, e.g. water cut ceiling of 0.95"
    )
    provenance: Tracked[str] | None = Field(
        default=None,
        description="Where this drift model came from: decline-curve fit on "
        "actual history (Branch A) vs offset-well analog or forecast (Branch B)",
    )
    uncertainty_frac: float = Field(
        default=0.3,
        description="Fractional uncertainty on rate_per_year, used to build the "
        "min/max envelope",
    )


class TrajectorySpec(BaseModel):
    """Framework §6 — the design must work across the well's life horizon, not at
    a single point."""

    horizon_months: float = 24.0
    n_points: int = Field(
        default=25,
        description="Scenario points across the horizon. 25 gives monthly "
        "resolution over 2 years.",
    )
    drift_models: dict[str, DriftModel] = Field(default_factory=dict)
    build_envelope: bool = Field(
        default=True,
        description="Build min/base/max scenarios rather than base only (§6.3)",
    )

    def evaluation_months(self) -> list[float]:
        if self.n_points <= 1:
            return [0.0]
        step = self.horizon_months / (self.n_points - 1)
        return [round(i * step, 4) for i in range(self.n_points)]


# =============================================================================
# The Case
# =============================================================================


class CaseMetadata(BaseModel):
    case_id: str
    project_id: str | None = None
    tenant_id: str = Field(description="§9 data isolation boundary")
    well_name: str | None = None
    field_name: str | None = None
    operator: str | None = None
    region: str | None = None
    requested_by: str | None = None
    created_at: str | None = None
    data_access_level: DataAccessLevel = DataAccessLevel.PARTIAL
    raw_request: str | None = Field(
        default=None,
        description="The original unstructured input, kept verbatim so every "
        "extracted value can be traced back to it (§10)",
    )
    source_documents: list[str] = Field(default_factory=list)


class EngineeringOptions(BaseModel):
    ranking_mode: Literal["run_life", "bep_target"] = "run_life"
    gas_kq_override: Tracked[float] | None = None
    gas_kh_override: Tracked[float] | None = None
    cable_id: str | None = None
    operating_ceiling_hz: Tracked[float] | None = None
    seal_thrust_paths: dict[str, Literal["floater", "compression"]] = Field(default_factory=dict)
    tapered_sections: list[dict] = Field(default_factory=list)
    apply_project_preference: bool = False
    applied_project_preference: dict | None = None
    # Preferences never arise from an implicit selection. This explicit record
    # is stored within the same operator/project case perimeter.
    preference_confirmed: bool = False
    preference_note: str | None = None

    @field_validator("gas_kq_override", "gas_kh_override")
    @classmethod
    def gas_fraction(cls, value):
        if value is not None and not 0 < value.value <= 1:
            raise ValueError("Gas correction overrides must be in (0, 1].")
        return value

    @field_validator("tapered_sections")
    @classmethod
    def section_inputs(cls, values):
        if len(values)>6:
            raise ValueError("At most six screening sections may be configured.")
        for item in values:
            if not isinstance(item.get("stages"),int) or not 1 <= item["stages"] <= 1000:
                raise ValueError("Each section needs an integer stage count from 1 to 1000.")
        return values


class Case(BaseModel):
    engineering: EngineeringOptions = Field(default_factory=EngineeringOptions)
    synthetic: bool = False
    """A complete ESP design case.

    Frozen before the engine runs, and hashed — so any design can be replayed
    exactly from the case that produced it, independent of any model.
    """

    metadata: CaseMetadata
    task_type: TaskType

    expectations: Expectations = Field(default_factory=Expectations)
    constraints: Constraints = Field(default_factory=Constraints)
    complications: Complications = Field(default_factory=Complications)
    reference: PreviousInstallation | None = None

    geometry: WellGeometry = Field(default_factory=WellGeometry)
    fluid: FluidProperties = Field(default_factory=FluidProperties)
    reservoir: ReservoirProperties = Field(default_factory=ReservoirProperties)
    trajectory: TrajectorySpec = Field(default_factory=TrajectorySpec)

    # --- Decision Gate 1 (§4) ------------------------------------------------

    @property
    def branch(self) -> TaskBranch:
        """Framework §4. The task type proposes a branch; a usable anchor
        confirms it.

        A stated 'replacement' with no performance history is really a Branch B
        problem wearing a Branch A label — and treating it as anchored would
        produce false confidence. So the anchor decides.
        """
        proposed = self.task_type.branch
        if proposed is TaskBranch.A_REPLACEMENT:
            if self.reference is None or not self.reference.is_usable_anchor:
                return TaskBranch.B_NEW
        return proposed

    @property
    def branch_rationale(self) -> str:
        proposed = self.task_type.branch
        actual = self.branch
        if actual is proposed:
            if actual is TaskBranch.A_REPLACEMENT:
                return (
                    "Branch A: replacement with usable performance history from "
                    "the previous installation. Design is anchored to actual "
                    "measured behaviour; confidence is high."
                )
            return (
                f"Branch B: {self.task_type.value} has no previous-installation "
                "anchor. Design proceeds from analogs and forecasts; result is "
                "delivered as a range and needs expert oversight."
            )
        return (
            f"Branch B (downgraded from A): task was submitted as "
            f"{self.task_type.value}, but the previous installation lacks usable "
            "performance data (no test points). Treating this as anchored would "
            "produce false confidence, so it is handled as an unanchored case "
            "with a scenario range."
        )

    # --- Hard stop validation (§5.1) ----------------------------------------

    def missing_hard_stops(self) -> list[str]:
        """Framework §5.1 — calculation is impossible without these.

        Deliberately NOT fillable by assumption. The intake agent must raise
        these as questions to the customer rather than guess. Guessing the
        anchor parameter is the one unrecoverable failure mode.
        """
        missing: list[str] = []
        if self.expectations.target_rate_bpd is None:
            missing.append(
                "target production rate — the primary parameter and anchor of "
                "the entire design"
            )
        missing.extend(self.geometry.missing_hard_stop_data())
        return missing

    @property
    def is_calculable(self) -> bool:
        return not self.missing_hard_stops()

    # --- Review surface -----------------------------------------------------

    def assumed_fields(self) -> list[tuple[str, Tracked]]:
        """Every value in the case that was assumed or defaulted rather than
        supplied. This is the engineer's review queue (§11 level 1-2)."""
        found: list[tuple[str, Tracked]] = []

        def walk(obj, path: str) -> None:
            if isinstance(obj, Tracked):
                if obj.needs_review or obj.source is Source.ENGINEER_OVERRIDE:
                    found.append((path, obj))
                return
            if isinstance(obj, BaseModel):
                for name, _ in obj.__class__.model_fields.items():
                    walk(getattr(obj, name), f"{path}.{name}" if path else name)
                return
            if isinstance(obj, (list, tuple)):
                for i, item in enumerate(obj):
                    walk(item, f"{path}[{i}]")
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{path}[{k!r}]")

        walk(self, "")
        return found

    @property
    def overall_input_confidence(self) -> float:
        """Confidence-weighted view of input quality.

        Uses the minimum rather than the mean: a case is only as trustworthy as
        its weakest load-bearing input, and averaging hides exactly the values
        an engineer needs to see.
        """
        tracked = [t for _, t in self.assumed_fields()]
        if not tracked:
            return 0.9
        return min(t.confidence for t in tracked)

    # --- Reproducibility ----------------------------------------------------

    def content_hash(self) -> str:
        """Stable hash of the case content, for exact design replay."""
        payload = self.model_dump(mode="json", exclude_none=True)
        payload.get("metadata", {}).pop("created_at", None)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @model_validator(mode="after")
    def _reference_consistency(self) -> "Case":
        if self.task_type.branch is TaskBranch.A_REPLACEMENT and self.reference is None:
            # Not an error — the branch property handles the downgrade. But the
            # case should record that it happened rather than fail silently.
            pass
        return self
