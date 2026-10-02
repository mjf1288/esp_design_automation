"""Derive tenant-local early-failure advisories from observations.

The association test is Fisher's exact test on a 2x2 table.  SciPy documents its
null for a 2x2 table as an odds ratio of one with fixed observed margins:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.fisher_exact.html .
A p-value is not treated as a probability that the rule is true; it is reported
alongside a risk difference and a 95% interval, and no causal conclusion is made.
The component Wilson score intervals use the NIST formula:
https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm .

The rule generator intentionally accepts only pre-specified thresholds.  It does
not search every depth/GVF split for the most favorable p-value because that
would silently create a multiple-comparisons and overfitting problem.
"""

from __future__ import annotations

from math import sqrt
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from scipy.stats import fisher_exact, norm

from .models import (
    BiasFlag,
    DerivedEmpiricalRule,
    EmpiricalObservation,
    RuleDerivationRefusal,
    RuleEvidence,
    RuleHypothesis,
)
from .repository import ObservationRepository
from .survival import RunLifeObservation, kaplan_meier


class _EligibleOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    observation: EmpiricalObservation
    exposed: bool
    early_failure: bool


# These are deliberately conservative product policy thresholds, not claims about
# ESP reliability.  They prevent the framework's explicit "3 cases" failure mode.
_MIN_ELIGIBLE_OBSERVATIONS = 20
_MIN_GROUP_OBSERVATIONS = 8
_MIN_EARLY_FAILURES = 3
_RULE_ALPHA = 0.05


def _wilson_interval(successes: int, n: int, confidence_level: float = 0.95) -> tuple[float, float]:
    """Score interval for a single observed proportion.

    This is a descriptive uncertainty interval; the association's formal test is
    explicitly reported as Fisher exact, rather than mislabeling the interval as
    an independent causal test.
    """
    if n <= 0:
        raise ValueError("Wilson interval requires n > 0.")
    z_value = float(norm.ppf(1.0 - (1.0 - confidence_level) / 2.0))
    z_squared = z_value**2
    proportion = successes / n
    denominator = 1.0 + z_squared / n
    center = (proportion + z_squared / (2.0 * n)) / denominator
    half_width = z_value * sqrt(
        (proportion * (1.0 - proportion) + z_squared / (4.0 * n)) / n
    ) / denominator
    return max(0.0, center - half_width), min(1.0, center + half_width)


def _risk_difference_interval(
    exposed_events: int, exposed_n: int, comparison_events: int, comparison_n: int
) -> tuple[float, float]:
    """Conservative Newcombe-style interval formed from separate Wilson limits."""
    exposed_low, exposed_high = _wilson_interval(exposed_events, exposed_n)
    comparison_low, comparison_high = _wilson_interval(comparison_events, comparison_n)
    return exposed_low - comparison_high, exposed_high - comparison_low


def _is_eligible_for_early_failure(
    observation: EmpiricalObservation, cutoff_days: int
) -> bool:
    """Exclude censored records that end before the early-failure cutoff.

    A running pump observed for 30 days cannot honestly be classified as either
    a 90-day early failure or a 90-day non-failure.  Treating it as either would
    bias the contingency table.
    """
    if observation.is_failure:
        return True
    return observation.run_life_days >= cutoff_days


class RuleDeriver:
    """Rule service bound to exactly one repository and therefore one tenant."""

    def __init__(self, repository: ObservationRepository) -> None:
        self._repository = repository

    def derive_early_failure_rule(
        self, hypothesis: RuleHypothesis
    ) -> DerivedEmpiricalRule | RuleDerivationRefusal:
        """Derive or honestly refuse an association rule for one explicit hypothesis.

        Minimum evidence policy: at least 20 classifiable observations, 8 in each
        comparison group, and 3 observed early failures.  The numbers are guard
        rails against unstable field anecdotes, not a claim that 20 wells prove a
        general reliability law.  Rules also require a positive risk difference,
        a two-sided Fisher p-value no larger than 0.05, and a positive lower 95%
        risk-difference bound.
        """
        observations = self._repository.list_observations(pump_model=hypothesis.pump_model)
        scoped = tuple(
            obs
            for obs in observations
            if (hypothesis.field_id is None or obs.field_id == hypothesis.field_id)
            and (hypothesis.region is None or obs.region == hypothesis.region)
        )
        if not hypothesis.prespecified:
            return self._refusal(
                hypothesis,
                len(scoped),
                0,
                "Rule refused: thresholds were not pre-specified; automatic outcome-driven threshold search is disabled to prevent overfitting.",
            )

        candidates: list[_EligibleOutcome] = []
        missing_context = 0
        censored_before_cutoff = 0
        for observation in scoped:
            condition = observation.operating_conditions
            if condition.setting_depth_md_ft is None or condition.gfv_frac is None:
                missing_context += 1
                continue
            if not _is_eligible_for_early_failure(observation, hypothesis.early_failure_within_days):
                censored_before_cutoff += 1
                continue
            exposed = (
                condition.setting_depth_md_ft.value < hypothesis.setting_depth_below_ft
                and condition.gfv_frac.value >= hypothesis.gfv_at_or_above_frac
            )
            candidates.append(
                _EligibleOutcome(
                    observation=observation,
                    exposed=exposed,
                    early_failure=(
                        observation.is_failure
                        and observation.run_life_days <= hypothesis.early_failure_within_days
                    ),
                )
            )

        warnings: list[str] = []
        if missing_context:
            warnings.append(
                f"{missing_context} matching observations lacked setting depth or GVF and were excluded."
            )
        if censored_before_cutoff:
            warnings.append(
                f"{censored_before_cutoff} censored observations ended before the cutoff and cannot be classified for early failure."
            )
        if len(candidates) < _MIN_ELIGIBLE_OBSERVATIONS:
            return self._refusal(
                hypothesis,
                len(scoped),
                len(candidates),
                *warnings,
                f"Rule refused: {len(candidates)} eligible observations is below the {_MIN_ELIGIBLE_OBSERVATIONS}-observation minimum needed to avoid presenting a field anecdote as a rule.",
            )

        exposed = [row for row in candidates if row.exposed]
        comparison = [row for row in candidates if not row.exposed]
        if len(exposed) < _MIN_GROUP_OBSERVATIONS or len(comparison) < _MIN_GROUP_OBSERVATIONS:
            return self._refusal(
                hypothesis,
                len(scoped),
                len(candidates),
                *warnings,
                "Rule refused: exposed and comparison groups each require at least "
                f"{_MIN_GROUP_OBSERVATIONS} eligible observations; found {len(exposed)} and {len(comparison)}.",
            )

        exposed_events = sum(row.early_failure for row in exposed)
        comparison_events = sum(row.early_failure for row in comparison)
        total_events = exposed_events + comparison_events
        if total_events < _MIN_EARLY_FAILURES:
            return self._refusal(
                hypothesis,
                len(scoped),
                len(candidates),
                *warnings,
                f"Rule refused: only {total_events} observed early failures; at least {_MIN_EARLY_FAILURES} are required before comparing groups.",
            )

        exposed_risk = exposed_events / len(exposed)
        comparison_risk = comparison_events / len(comparison)
        risk_difference = exposed_risk - comparison_risk
        risk_ratio = exposed_risk / comparison_risk if comparison_risk > 0.0 else None
        ci_low, ci_high = _risk_difference_interval(
            exposed_events, len(exposed), comparison_events, len(comparison)
        )
        fisher = fisher_exact(
            [[exposed_events, len(exposed) - exposed_events], [comparison_events, len(comparison) - comparison_events]],
            alternative="two-sided",
        )
        p_value = float(fisher.pvalue)

        if risk_difference <= 0.0:
            return self._refusal(
                hypothesis,
                len(scoped),
                len(candidates),
                *warnings,
                "Rule refused: the observed exposed-group early-failure risk is not higher than the comparison group.",
            )
        if p_value > _RULE_ALPHA:
            return self._refusal(
                hypothesis,
                len(scoped),
                len(candidates),
                *warnings,
                f"Rule refused: two-sided Fisher exact p={p_value:.4f} exceeds the {_RULE_ALPHA:.2f} emission threshold.",
            )
        if ci_low <= 0.0:
            return self._refusal(
                hypothesis,
                len(scoped),
                len(candidates),
                *warnings,
                "Rule refused: the 95% risk-difference interval includes no increase in early-failure risk.",
            )

        fields = {row.observation.field_id for row in candidates if row.observation.field_id}
        regions = {row.observation.region for row in candidates if row.observation.region}
        source_weight = sum(row.observation.source.confidence for row in candidates) / len(candidates)
        ci_width = ci_high - ci_low
        # This is deliberately called evidence strength rather than a posterior
        # probability.  Every term is derived from stored evidence and displayed.
        sample_component = min(1.0, sqrt(len(candidates) / 100.0))
        diversity_component = len(fields) / (len(fields) + 1.0)
        precision_component = max(0.0, 1.0 - ci_width / 2.0)
        effect_component = min(1.0, risk_difference)
        test_component = max(0.0, 1.0 - p_value)
        evidence_strength = (
            source_weight
            * sample_component
            * diversity_component
            * precision_component
            * effect_component
            * test_component
        )
        confidence_basis = (
            f"computed evidence strength={evidence_strength:.3f}: n={len(candidates)}, "
            f"exposed={len(exposed)}, comparison={len(comparison)}, risk difference="
            f"{risk_difference:.3f} (95% CI {ci_low:.3f} to {ci_high:.3f}), "
            f"Fisher exact two-sided p={p_value:.4f}, {len(fields)} distinct fields, "
            f"mean record-source confidence={source_weight:.3f}."
        )
        evidence = RuleEvidence(
            source_perimeter=self._repository.tenant_id,
            n_observations=len(candidates),
            n_exposed=len(exposed),
            n_comparison=len(comparison),
            n_early_failures_exposed=exposed_events,
            n_early_failures_comparison=comparison_events,
            n_distinct_fields=len(fields),
            n_distinct_regions=len(regions),
            effect_risk_difference=risk_difference,
            effect_risk_ratio=risk_ratio,
            risk_difference_ci_95_low=ci_low,
            risk_difference_ci_95_high=ci_high,
            statistical_test="Fisher exact test (two-sided), null odds ratio = 1 with fixed margins",
            p_value=p_value,
            source_quality_weight=source_weight,
            evidence_strength=evidence_strength,
            confidence_basis=confidence_basis,
            warnings=tuple(warnings),
        )
        biases, explanations = self._biases(
            hypothesis=hypothesis,
            candidates=candidates,
            distinct_fields=len(fields),
            censored_before_cutoff=censored_before_cutoff,
        )
        survival = kaplan_meier(
            RunLifeObservation(
                observation_id=row.observation.observation_id,
                run_life_days=float(row.observation.run_life_days),
                event_occurred=row.observation.is_failure,
            )
            for row in candidates
        )
        survival_summary = {
            "survival_at_early_failure_days": survival.survival_at(hypothesis.early_failure_within_days),
            "n_failures": survival.n_failures,
            "n_right_censored": survival.n_right_censored,
            "warnings": list(survival.warnings),
        }
        rule = DerivedEmpiricalRule(
            rule_id=str(uuid4()),
            tenant_id=self._repository.tenant_id,
            statement=hypothesis.statement(),
            hypothesis=hypothesis,
            supporting_observation_ids=tuple(row.observation.observation_id for row in candidates),
            evidence=evidence,
            bias_flags=tuple(biases),
            bias_explanations=explanations,
            survival_summary=survival_summary,
        )
        return rule

    def _refusal(
        self,
        hypothesis: RuleHypothesis,
        considered: int,
        eligible: int,
        *warnings: str,
    ) -> RuleDerivationRefusal:
        return RuleDerivationRefusal(
            tenant_id=self._repository.tenant_id,
            hypothesis_id=hypothesis.hypothesis_id,
            warnings=tuple(warnings),
            n_observations_considered=considered,
            n_observations_eligible=eligible,
        )

    @staticmethod
    def _biases(
        *,
        hypothesis: RuleHypothesis,
        candidates: list[_EligibleOutcome],
        distinct_fields: int,
        censored_before_cutoff: int,
    ) -> tuple[list[BiasFlag], dict[BiasFlag, str]]:
        flags: list[BiasFlag] = [BiasFlag.SELECTION_BIAS, BiasFlag.CONFOUNDING]
        explanations: dict[BiasFlag, str] = {
            BiasFlag.SELECTION_BIAS: (
                "Historical installations were selected by engineers rather than randomly assigned; "
                "the installed population may differ from designs they rejected."
            ),
            BiasFlag.CONFOUNDING: (
                "The observational comparison cannot isolate depth/GVF from fluid, temperature, "
                "operating practice, completion, and other conditions not controlled here."
            ),
        }
        if not hypothesis.population_completeness_confirmed:
            flags.append(BiasFlag.SURVIVORSHIP_BIAS)
            explanations[BiasFlag.SURVIVORSHIP_BIAS] = (
                "Dataset completeness was not confirmed; installations or outcomes that were never entered "
                "can change the apparent pattern."
            )
        if len(candidates) < 100:
            flags.append(BiasFlag.SMALL_SAMPLE_OVERFITTING)
            explanations[BiasFlag.SMALL_SAMPLE_OVERFITTING] = (
                "The rule cleared the minimum guard rail but has fewer than 100 eligible observations; "
                "effect size and interval should be read as unstable field evidence."
            )
        if distinct_fields <= 1 and hypothesis.field_id is None:
            flags.append(BiasFlag.SINGLE_FIELD_GENERALIZATION)
            explanations[BiasFlag.SINGLE_FIELD_GENERALIZATION] = (
                "All eligible observations come from one identified field, so the association must not be "
                "generalized to other fields."
            )
        if censored_before_cutoff:
            flags.append(BiasFlag.RIGHT_CENSORING_LIMITATION)
            explanations[BiasFlag.RIGHT_CENSORING_LIMITATION] = (
                "Some still-running or non-failure records had insufficient follow-up for early-failure classification "
                "and were excluded from the contingency table."
            )
        return flags, explanations
