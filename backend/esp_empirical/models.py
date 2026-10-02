"""Pydantic domain types for tenant-scoped ESP field observations.

The names of all quantities carry US field units.  Values that originated
outside this package retain the deterministic layer's ``Tracked`` provenance
rather than creating a second source/confidence vocabulary.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from esp_engine.provenance import Source, Tracked


class ObservedOperatingConditions(BaseModel):
    """Observed conditions, with units in field names and provenance per value."""

    model_config = ConfigDict(frozen=True)

    setting_depth_md_ft: Tracked[float] | None = None
    setting_depth_tvd_ft: Tracked[float] | None = None
    gfv_frac: Tracked[float] | None = Field(
        default=None, description="Observed gas-volume fraction, expressed as a fraction."
    )
    water_cut_frac: Tracked[float] | None = None
    intake_pressure_psi: Tracked[float] | None = None
    intake_temp_f: Tracked[float] | None = None
    frequency_hz: Tracked[float] | None = None
    liquid_rate_bpd: Tracked[float] | None = None
    operating_zone: str | None = None
    complication_tags: tuple[str, ...] = ()

    @field_validator("gfv_frac", "water_cut_frac")
    @classmethod
    def _fractions_are_not_percentages(cls, value: Tracked[float] | None) -> Tracked[float] | None:
        if value is not None and not 0.0 <= value.value <= 1.0:
            raise ValueError("Fractions must be in [0, 1], not percentages.")
        return value


class ObservationDraft(BaseModel):
    """One real installed ESP outcome before its tenant is attached by a repository.

    ``run_life_days`` is computed from dates on creation rather than accepted as
    an untraceable hand-entered result.  For a unit that is still running,
    ``outcome_observed_date`` is the explicit censoring date; the caller must
    supply it so the result is reproducible without relying on a clock.
    """

    model_config = ConfigDict(frozen=True)

    external_case_id: str | None = None
    pump_model: str
    manufacturer: str | None = None
    pump_series: str | None = None
    region: str | None = None
    field_id: str | None = None
    formation: str | None = None
    fluid_type: str | None = None

    case_inputs_snapshot: dict[str, Any]
    selected_configuration: dict[str, Any]
    install_date: date
    pull_date: date | None = None
    still_running: bool
    outcome_observed_date: date
    is_failure: bool
    failure_mode: str | None = None
    failure_location: str | None = None
    teardown_findings: str | None = None
    operating_conditions: ObservedOperatingConditions
    source: Tracked[str] = Field(
        description="Record-level provenance, normally a telemetry identifier or report reference."
    )
    engineer_commentary: str | None = None
    entered_by: str | None = None

    @model_validator(mode="after")
    def _outcome_is_coherent(self) -> "ObservationDraft":
        if self.install_date > self.outcome_observed_date:
            raise ValueError("outcome_observed_date cannot be before install_date.")
        if self.still_running:
            if self.pull_date is not None:
                raise ValueError("still_running observations cannot have a pull_date.")
            if self.is_failure or self.failure_mode is not None:
                raise ValueError("A still-running installation cannot be recorded as a failure.")
        else:
            if self.pull_date is None:
                raise ValueError("A stopped installation must have a pull_date.")
            if self.pull_date != self.outcome_observed_date:
                raise ValueError("For a stopped installation, outcome_observed_date must equal pull_date.")
        if self.is_failure and not self.failure_mode:
            raise ValueError("A failure outcome requires failure_mode.")
        if not self.is_failure and self.failure_mode is not None:
            raise ValueError("failure_mode is only valid when is_failure is true.")
        return self

    @property
    def run_life_days(self) -> int:
        """Observed follow-up in days; a still-running record is right-censored."""
        return (self.outcome_observed_date - self.install_date).days

    @property
    def is_right_censored(self) -> bool:
        return not self.is_failure


class EmpiricalObservation(ObservationDraft):
    """Persisted observation with its tenant-controlled identity."""

    model_config = ConfigDict(frozen=True)

    observation_id: str
    tenant_id: str


class BiasFlag(str, Enum):
    """Alternative explanations the product must surface under framework §8.2."""

    SURVIVORSHIP_BIAS = "survivorship_bias"
    SELECTION_BIAS = "selection_bias"
    CONFOUNDING = "confounding"
    SMALL_SAMPLE_OVERFITTING = "small_sample_overfitting"
    SINGLE_FIELD_GENERALIZATION = "single_field_generalization"
    RIGHT_CENSORING_LIMITATION = "right_censoring_limitation"


class RuleHypothesis(BaseModel):
    """A pre-specified, testable early-failure comparison.

    Thresholds are inputs to an auditable hypothesis, not values selected after
    inspecting outcomes.  This intentionally refuses automatic threshold mining:
    it would multiply comparisons and turn noisy field history into a rule.
    """

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str
    authored_in_perimeter: str | None = None
    """Perimeter the hypothesis was authored in. ``field_id`` below names somebody's
    field, so an unattributed hypothesis can carry one operator's field into
    another's project (§9.3)."""
    pump_model: str
    setting_depth_below_ft: float
    gfv_at_or_above_frac: float
    early_failure_within_days: int = Field(gt=0)
    reference_fact_ids: tuple[str, ...] = ()
    field_id: str | None = None
    region: str | None = None
    population_completeness_confirmed: bool = False
    prespecified: bool = True

    @field_validator("setting_depth_below_ft")
    @classmethod
    def _depth_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("setting_depth_below_ft must be positive.")
        return value

    @field_validator("gfv_at_or_above_frac")
    @classmethod
    def _gvf_is_fraction(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("gfv_at_or_above_frac must be in [0, 1], not percent.")
        return value

    def statement(self) -> str:
        context = f" in field {self.field_id}" if self.field_id else ""
        return (
            f"For {self.pump_model}{context}, installations set below "
            f"{self.setting_depth_below_ft:g} ft MD with observed GVF at or above "
            f"{self.gfv_at_or_above_frac:.3f} had a higher observed early-failure risk "
            f"within {self.early_failure_within_days} days than the comparison group."
        )


class RuleEvidence(BaseModel):
    """All quantities behind a derived association; no confidence is user-entered.

    ``source_perimeter`` closes the leak described in framework v0.3 §9.3: a rule
    stripped of identifying names still discloses the operating regime of the
    field it was derived from, and this object is the disclosure.
    ``n_distinct_fields``, ``n_distinct_regions`` and the effect sizes describe
    somebody's wells. Evidence therefore travels with the perimeter it came from
    and is refused outside it, so detaching it from its rule cannot launder it.
    """

    model_config = ConfigDict(frozen=True)

    source_perimeter: str
    n_observations: int
    n_exposed: int
    n_comparison: int
    n_early_failures_exposed: int
    n_early_failures_comparison: int
    n_distinct_fields: int
    n_distinct_regions: int
    effect_risk_difference: float
    effect_risk_ratio: float | None
    risk_difference_ci_95_low: float
    risk_difference_ci_95_high: float
    statistical_test: str
    p_value: float
    source_quality_weight: float
    evidence_strength: float = Field(ge=0.0, le=1.0)
    confidence_basis: str
    warnings: tuple[str, ...] = ()


class DerivedEmpiricalRule(BaseModel):
    """A perimeter-local, derived advisory association -- never a deterministic fact.

    ``tenant_id`` holds the perimeter key of the data the rule was derived from.
    Per framework v0.3 §9.3 a rule inherits the perimeter of its source and is
    never promoted automatically: the org level does not aggregate its operators'
    rules, and a sibling operator never sees them.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    tenant_id: str
    statement: str
    hypothesis: RuleHypothesis
    supporting_observation_ids: tuple[str, ...]
    evidence: RuleEvidence
    bias_flags: tuple[BiasFlag, ...]
    bias_explanations: dict[BiasFlag, str]
    survival_summary: dict[str, Any] | None = None

    def evidence_visible_in(self, perimeter_key: str) -> bool:
        """Whether this rule's evidence may be shown to a caller in ``perimeter_key``.

        Checked at the presentation boundary rather than trusted at the storage
        boundary. A rule read from its own store is in-perimeter by construction;
        this catches the case where a rule object is passed between contexts in
        process -- an overlay, a report, a cached response -- which no store
        selection can prevent.
        """
        return self.evidence.source_perimeter == perimeter_key


class RuleDerivationRefusal(BaseModel):
    """An honest failure to produce a rule when data cannot sustain one."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    hypothesis_id: str
    warnings: tuple[str, ...]
    n_observations_considered: int
    n_observations_eligible: int
