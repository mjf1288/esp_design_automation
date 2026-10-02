"""Engine configuration — every methodology choice made explicit.

Framework §13 lists methodology questions that are deliberately still open:
thrust-zone boundaries (curve database vs BEP-derived formula), frequency curve
recalculation (computed via affinity laws vs pre-built curves), and the criterion
for selecting the "best" configuration from the range.

Rather than silently picking one answer, every such choice is a field here. The
config used is recorded on every ``DesignResult``, so a design from six months
ago can be reproduced exactly even after defaults change — and so the open
questions stay visible instead of hardening into invisible convention.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Correlation selection
# =============================================================================


class BubblePointCorrelation(str, Enum):
    STANDING = "standing"
    VAZQUEZ_BEGGS = "vazquez_beggs"


class RsCorrelation(str, Enum):
    STANDING = "standing"
    VAZQUEZ_BEGGS = "vazquez_beggs"


class BoCorrelation(str, Enum):
    STANDING = "standing"
    VAZQUEZ_BEGGS = "vazquez_beggs"


class OilViscosityCorrelation(str, Enum):
    BEGGS_ROBINSON = "beggs_robinson"


class ZFactorCorrelation(str, Enum):
    DRANCHUK_ABOU_KASSEM = "dranchuk_abou_kassem"
    HALL_YARBOROUGH = "hall_yarborough"


class IPRModel(str, Enum):
    PI_LINEAR = "pi_linear"
    VOGEL = "vogel"
    COMPOSITE = "composite"


class FrictionMethod(str, Enum):
    DARCY_WEISBACH = "darcy_weisbach"  # physics base, recommended
    HAZEN_WILLIAMS = "hazen_williams"  # water-only screening, legacy


class NaturalSeparationModel(str, Enum):
    ALHANATI = "alhanati"
    VENDOR_RULE_OF_THUMB = "vendor_rule_of_thumb"
    NONE = "none"  # conservative: assume no natural separation


class ThrustZoneMethod(str, Enum):
    """Framework §13 open question, made explicit."""

    CATALOG_LIMITS = "catalog_limits"  # use vendor-published range limits
    BEP_FRACTION = "bep_fraction"  # derive from BEP: e.g. 0.75-1.25 x Q_bep


class FrequencyCurveMethod(str, Enum):
    """Framework §13 open question, made explicit."""

    AFFINITY_LAWS = "affinity_laws"  # compute from 60 Hz reference curve
    PREBUILT_CURVES = "prebuilt_curves"  # use vendor per-frequency curves if present


class ViscosityCorrectionMethod(str, Enum):
    ANSI_HI_9_6_7 = "ansi_hi_9_6_7"
    NONE = "none"


class CorrelationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    bubble_point: BubblePointCorrelation = BubblePointCorrelation.STANDING
    rs: RsCorrelation = RsCorrelation.STANDING
    bo: BoCorrelation = BoCorrelation.STANDING
    oil_viscosity: OilViscosityCorrelation = OilViscosityCorrelation.BEGGS_ROBINSON
    z_factor: ZFactorCorrelation = ZFactorCorrelation.DRANCHUK_ABOU_KASSEM
    ipr: IPRModel = IPRModel.COMPOSITE
    friction: FrictionMethod = FrictionMethod.DARCY_WEISBACH
    natural_separation: NaturalSeparationModel = NaturalSeparationModel.VENDOR_RULE_OF_THUMB
    thrust_zones: ThrustZoneMethod = ThrustZoneMethod.CATALOG_LIMITS
    frequency_curves: FrequencyCurveMethod = FrequencyCurveMethod.AFFINITY_LAWS
    viscosity_correction: ViscosityCorrectionMethod = (
        ViscosityCorrectionMethod.ANSI_HI_9_6_7
    )


# =============================================================================
# Design thresholds
# =============================================================================


class GasThresholds(BaseModel):
    """Framework §3.3 and physics reference §3.5.

    Free gas volume fraction at intake, after natural separation, decides the gas
    handling strategy. Vendor guidance varies, so these are configurable with
    industry-typical defaults and the source of each default noted.
    """

    model_config = ConfigDict(frozen=True)

    standard_pump_max_fgvf: float = Field(
        default=0.10,
        description="Above this free gas fraction a standard pump is not reliable",
    )
    gas_handler_max_fgvf: float = Field(
        default=0.45,
        description="Gas handler / charge pump territory up to this fraction",
    )
    separator_recommended_fgvf: float = Field(
        default=0.25,
        description="Above this, a rotary gas separator is recommended",
    )
    advanced_handling_max_fgvf: float = Field(
        default=0.75,
        description="Above this, ESP is likely the wrong lift method entirely",
    )
    turpin_stability_limit: float = Field(
        default=1.0,
        description="Turpin parameter above which the pump is gas-locked-prone",
    )
    min_submergence_ft: float = Field(
        default=200.0,
        description="Minimum fluid over pump; less than this and gas breakout at "
        "intake becomes unpredictable",
    )


class MotorThresholds(BaseModel):
    """Framework §12: motor at 75-85% loading, verified against documentation."""

    model_config = ConfigDict(frozen=True)

    target_loading_min: float = 0.75
    target_loading_max: float = 0.85
    pmm_target_loading_min: float | None = Field(
        default=None,
        description="Separate lower loading bound for permanent magnet motors. "
        "B.16 notes a PMM holds efficiency and near-unity power factor at "
        "partial load, so the induction-derived 75% floor overstates the "
        "underloading penalty. Left None because no public datasheet in this "
        "catalog publishes a PMM partial-load efficiency curve to derive a "
        "defensible floor from; when None the induction band is applied and "
        "the flag discloses that its provenance is induction practice. Set "
        "this from vendor data to calibrate it.",
    )
    nameplate_amps_tolerance_frac: float = Field(
        default=0.10,
        description="How far the B.14.1 full-load current may sit from the "
        "cataloged nameplate current before the pair is treated as "
        "contradictory. Not a physics constant: it is the point past which the "
        "two published numbers can no longer both be full-load quantities for "
        "the same machine. 10% covers round-off and winding-tap tolerance; on "
        "this catalog the real gap is 35-48%, which is why the reconciliation "
        "exists at all.",
    )
    absolute_loading_max: float = Field(
        default=1.0, description="Nameplate; above this the motor is overloaded"
    )
    min_cooling_velocity_ft_s: float = Field(
        default=1.0,
        description="Minimum annular fluid velocity past the motor for cooling "
        "under STANDARD conditions (framework 6D.2). Below it, the mechanism is "
        "motor overheating. Vendor-specific; 1 ft/s is the common conservative "
        "floor and framework 13 still lists minimum cooling velocity among the "
        "open vendor-specific boundaries, so this is a generic literature value "
        "rather than a property of any cataloged motor.",
    )
    min_cooling_velocity_viscous_ft_s_low: float = Field(
        default=2.6,
        description="Low end of the viscous-oil cooling floor band (framework "
        "6D.2, CFD work at Missouri S&T). The 1 ft/s rule is inadequate for "
        "viscous fluids. Below this end the velocity is established as "
        "inadequate.",
    )
    min_cooling_velocity_viscous_ft_s_high: float = Field(
        default=2.8,
        description="High end of the viscous-oil cooling floor band, and the "
        "value a viscous well must actually exceed to clear the minimum. The "
        "source publishes a RANGE, so a velocity landing between the two ends "
        "is reported as indeterminate rather than resolved against a "
        "manufactured midpoint -- the same prohibition 6B.1 places on deriving "
        "BEP from the midpoint of a published range.",
    )
    max_cooling_velocity_ft_s: float | None = Field(
        default=None,
        description="Upper bound of the 6D.2 cooling range. None -- the default "
        "-- means no numeric upper bound is cataloged. The framework names the "
        "two governing mechanisms (erosion where solids are present, degraded "
        "natural gas separation into the annulus) but publishes a velocity for "
        "neither, and an erosional velocity taken from API RP 14E is a "
        "pipeline-flow criterion with no standing in an ESP annulus. When this "
        "is None and a governing mechanism is active, the upper bound is "
        "reported as UNCHECKED plus a data request, never as satisfied.",
    )
    max_winding_temp_f: float = 350.0
    service_factor: float = 1.0


class MechanicalThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    shaft_hp_safety_factor: float = Field(
        default=1.0,
        description="Required shaft rating = computed shaft HP x this factor",
    )
    thrust_safety_factor: float = 1.0
    max_stages_per_housing: int = 100


class ElectricalThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_voltage_drop_v_per_1000ft: float = Field(
        default=30.0,
        description="PRIMARY ESP cable screen. Recommended practice is to select a "
        "cable size giving less than 30 V drop per 1000 ft at the motor amperage "
        "and downhole temperature. Source: "
        "https://production-technology.org/esp-design-step-6-electric-cables/",
    )
    max_cable_voltage_drop_frac: float = Field(
        default=0.30,
        description="Voltage drop as a fraction of motor operating voltage. This is "
        "a general power-distribution convention (5-8%), NOT the ESP criterion, and "
        "it is deliberately loose here. A 800 V downhole motor at 8000 ft has a "
        "large fractional drop by construction; that is normal and is why ESP "
        "practice screens on volts-per-1000-ft plus available surface voltage "
        "instead. Kept as a configurable backstop against absurd cases.",
    )
    cable_ampacity_derate: float = Field(
        default=1.0, description="Derating factor applied to published ampacity"
    )
    surface_voltage_margin_frac: float = 0.05


class GeometryThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_radial_clearance_in: float = Field(
        default=0.20,
        description="Minimum (casing_ID - equipment_OD) for running clearance",
    )
    max_dogleg_deg_per_100ft: float = Field(
        default=6.0,
        description="Maximum dogleg severity for running ESP equipment. Highly "
        "vendor- and length-dependent.",
    )
    max_dogleg_at_setting_depth_deg_per_100ft: float = Field(
        default=2.0,
        description="Doglegs tolerable while running are not tolerable at the "
        "set point — the string must sit in a straight section.",
    )
    max_inclination_at_setting_depth_deg: float = 60.0


class DesignThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    gas: GasThresholds = Field(default_factory=GasThresholds)
    motor: MotorThresholds = Field(default_factory=MotorThresholds)
    mechanical: MechanicalThresholds = Field(default_factory=MechanicalThresholds)
    electrical: ElectricalThresholds = Field(default_factory=ElectricalThresholds)
    geometry: GeometryThresholds = Field(default_factory=GeometryThresholds)

    bep_band_frac: float = Field(
        default=0.10,
        description="Within +/- this fraction of Q_bep counts as the BEP zone "
        "rather than merely 'operating range' (§6.4)",
    )
    downthrust_frac_of_bep: float = Field(
        default=0.75,
        description="Used when thrust_zones = BEP_FRACTION: below this multiple "
        "of Q_bep the stage is in downthrust",
    )
    upthrust_frac_of_bep: float = Field(
        default=1.25,
        description="Used when thrust_zones = BEP_FRACTION",
    )


# =============================================================================
# Scoring objective — framework §13 open question
# =============================================================================


class ScoringObjective(str, Enum):
    """Framework §13: "Criterion for selecting the 'best' configuration from the
    range (maximum coverage / early-period priority / minimum failure risk)."

    This is a business decision disguised as a technical one, so it is explicit,
    user-selectable, and displayed in the UI rather than buried in a constant.
    """

    MAX_COVERAGE = "max_coverage"  # maximize time in acceptable zones
    EARLY_PRIORITY = "early_priority"  # weight the first months heaviest
    MIN_FAILURE_RISK = "min_failure_risk"  # penalize empirically risky zones
    MAX_VALIDITY_HORIZON = "max_validity_horizon"  # maximize time to first violation
    MAX_EFFICIENCY = "max_efficiency"  # minimize energy cost over the horizon


class ScoringConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    objective: ScoringObjective = ScoringObjective.MAX_COVERAGE

    zone_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "bep": 1.0,
            "operating_range": 0.80,
            "upthrust": 0.15,
            "downthrust": 0.10,
            "off_curve_right": 0.0,
            "off_curve_left": 0.0,
        },
        description="Quality score per operating zone. Downthrust is scored below "
        "upthrust because sustained downthrust destroys thrust bearings faster "
        "than brief upthrust does — but both are penalized heavily.",
    )

    early_priority_half_life_months: float = Field(
        default=6.0,
        description="For EARLY_PRIORITY: exponential decay half-life of the time "
        "weighting",
    )

    envelope_weights: dict[str, float] = Field(
        default_factory=lambda: {"min": 0.25, "base": 0.50, "max": 0.25},
        description="How the min/base/max scenario envelope is aggregated. A "
        "config that only works in the base case is not a robust design.",
    )

    penalize_vsd_dependence: float = Field(
        default=0.05,
        description="Small penalty for configs that only stay in range via "
        "frequency changes, since that assumes active surveillance",
    )

    max_ranked_results: int = 10


# =============================================================================
# Enumeration bounds
# =============================================================================


class EnumerationConfig(BaseModel):
    """Controls the size of the candidate search (framework §6.3 inversion)."""

    model_config = ConfigDict(frozen=True)

    stage_count_step: int = Field(
        default=10, description="Granularity of stage-count enumeration"
    )
    stage_count_span_frac: float = Field(
        default=0.25,
        description="Explore +/- this fraction around the nominal stage count",
    )
    frequency_step_hz: float = 2.5
    max_candidates: int = Field(
        default=4000,
        description="Hard cap on enumerated configurations, to bound runtime",
    )
    allow_tapered_pumps: bool = False

    # --- Staged pruning (see pipeline.py) ------------------------------------
    # Every candidate is evaluated at month 0; only this many survivors pay for
    # a full trajectory sweep. Framework §6.3 wants the trajectory to pick the
    # winner, so this is set generously — it removes configurations already
    # off-curve on day one, not merely suboptimal ones.
    max_trajectory_configs: int = Field(
        default=60,
        description="Survivors that receive a full multi-point trajectory evaluation",
    )
    max_reported_candidates: int = Field(
        default=10,
        description="Ranked configurations returned to the engineer",
    )
    max_reported_rejections: int = Field(
        default=200,
        description="Cap on the rejection audit trail carried in the result payload",
    )


# =============================================================================
# Top-level config
# =============================================================================


ENGINE_VERSION = "0.2.0"


class EngineConfig(BaseModel):
    """Complete, hashable specification of how the engine behaves.

    Recorded on every DesignResult alongside the case hash and catalog version.
    Together these three make any historical design exactly reproducible — which
    is what makes the system defensible when a design is questioned years later.
    """

    model_config = ConfigDict(frozen=True)

    engine_version: str = ENGINE_VERSION
    correlations: CorrelationConfig = Field(default_factory=CorrelationConfig)
    thresholds: DesignThresholds = Field(default_factory=DesignThresholds)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    enumeration: EnumerationConfig = Field(default_factory=EnumerationConfig)

    apply_conservative_bias: bool = Field(
        default=True,
        description="Framework §5 asymmetric conservatism. Disable only for "
        "back-testing against known outcomes, never for a real design.",
    )

    def config_hash(self) -> str:
        blob = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


DEFAULT_CONFIG = EngineConfig()
