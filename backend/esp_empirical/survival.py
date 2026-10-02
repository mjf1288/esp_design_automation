"""Kaplan-Meier run-life estimator with Greenwood pointwise confidence bands.

Kaplan-Meier uses right-censored observations in the at-risk set until their
censoring time, rather than treating them as failures or dropping them.  The
product-limit estimator and Greenwood variance are stated in Zee & Xie's
method description: https://pmc.ncbi.nlm.nih.gov/articles/PMC6141203/ .
That source also identifies log-log confidence intervals as standard practice.

This implementation intentionally reports pointwise bands, not simultaneous
bands, and it does not claim censoring is independent: that assumption cannot
be verified from installation history alone.
"""

from __future__ import annotations

from collections import defaultdict
from math import exp, log, sqrt
from typing import Iterable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scipy.stats import norm


class RunLifeObservation(BaseModel):
    """One ESP duration; ``event_occurred=False`` is a right-censored run life."""

    model_config = ConfigDict(frozen=True)

    observation_id: str
    run_life_days: float = Field(ge=0.0)
    event_occurred: bool


class KaplanMeierPoint(BaseModel):
    """One step of the survival curve, including a Greenwood 95% confidence band."""

    model_config = ConfigDict(frozen=True)

    time_days: float
    n_at_risk: int
    n_events: int
    n_censored: int
    survival_probability: float = Field(ge=0.0, le=1.0)
    greenwood_variance: float | None = Field(default=None, ge=0.0)
    ci_95_lower: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_95_upper: float | None = Field(default=None, ge=0.0, le=1.0)


class KaplanMeierEstimate(BaseModel):
    """Complete nonparametric run-life estimate and limitations relevant to ESPs."""

    model_config = ConfigDict(frozen=True)

    n_observations: int
    n_failures: int
    n_right_censored: int
    points: tuple[KaplanMeierPoint, ...]
    warnings: tuple[str, ...] = ()

    def survival_at(self, time_days: float) -> float | None:
        """Return the right-continuous curve estimate at an explicit duration."""
        if not self.points:
            return None
        probability = 1.0
        for point in self.points:
            if point.time_days > time_days:
                break
            probability = point.survival_probability
        return probability


def _log_log_confidence_interval(
    survival: float, greenwood_sum: float, confidence_level: float
) -> tuple[float, float] | tuple[None, None]:
    """Use the Greenwood log-log transform to stay inside [0, 1]."""
    if survival == 1.0:
        return 1.0, 1.0
    if survival == 0.0:
        return 0.0, 0.0
    if greenwood_sum <= 0.0:
        return None, None

    z_value = float(norm.ppf(1.0 - (1.0 - confidence_level) / 2.0))
    # Var(log(-log(S))) is Greenwood's accumulated term divided by log(S)^2.
    standard_error = sqrt(greenwood_sum) / abs(log(survival))
    transformed = log(-log(survival))
    lower = exp(-exp(transformed + z_value * standard_error))
    upper = exp(-exp(transformed - z_value * standard_error))
    return max(0.0, lower), min(1.0, upper)


def kaplan_meier(
    observations: Iterable[RunLifeObservation], *, confidence_level: float = 0.95
) -> KaplanMeierEstimate:
    """Estimate event-free ESP run-life with correct handling of right censoring.

    At a tied time, failures are processed before censoring: both groups remain
    at risk immediately before that time, while censored units leave only after
    the time point.  The Greenwood term is undefined if every remaining unit
    fails at once; survival is then known to be zero and the implementation
    reports the degenerate [0, 0] band rather than inventing a finite variance.
    """
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between 0 and 1.")

    rows = tuple(observations)
    if not rows:
        return KaplanMeierEstimate(
            n_observations=0,
            n_failures=0,
            n_right_censored=0,
            points=(),
            warnings=("No run-life observations are available; survival cannot be estimated.",),
        )

    by_time: dict[float, list[RunLifeObservation]] = defaultdict(list)
    for observation in rows:
        by_time[observation.run_life_days].append(observation)

    at_risk = len(rows)
    survival = 1.0
    greenwood_sum = 0.0
    points: list[KaplanMeierPoint] = []
    exhausted = False

    for time_days in sorted(by_time):
        tied = by_time[time_days]
        events = sum(row.event_occurred for row in tied)
        censored = len(tied) - events
        if events > at_risk:
            raise ValueError("Invalid survival data: events exceed units at risk.")

        if events:
            survival *= 1.0 - events / at_risk
            if at_risk > events:
                greenwood_sum += events / (at_risk * (at_risk - events))
                variance: float | None = survival**2 * greenwood_sum
                lower, upper = _log_log_confidence_interval(
                    survival, greenwood_sum, confidence_level
                )
            else:
                exhausted = True
                variance = 0.0
                lower, upper = 0.0, 0.0
        else:
            variance = survival**2 * greenwood_sum if greenwood_sum else 0.0
            lower, upper = _log_log_confidence_interval(survival, greenwood_sum, confidence_level)

        points.append(
            KaplanMeierPoint(
                time_days=time_days,
                n_at_risk=at_risk,
                n_events=events,
                n_censored=censored,
                survival_probability=survival,
                greenwood_variance=variance,
                ci_95_lower=lower,
                ci_95_upper=upper,
            )
        )
        at_risk -= len(tied)

    warnings: list[str] = [
        "Confidence bands are pointwise Greenwood log-log intervals, not simultaneous bands.",
        "Kaplan-Meier assumes censoring is non-informative; installation history alone cannot verify that assumption.",
    ]
    if not any(row.event_occurred for row in rows):
        warnings.append("No failures were observed; the curve cannot establish a failure distribution.")
    if exhausted:
        warnings.append(
            "All remaining units failed at one time point; the terminal Greenwood band is degenerate."
        )
    if np.isclose(sum(row.event_occurred for row in rows), 0):
        # Make absence of observed events explicit rather than letting an all-1
        # curve look like a claim of reliable long-term performance.
        warnings.append("All available observations are censored, so apparent survival is incomplete follow-up.")

    return KaplanMeierEstimate(
        n_observations=len(rows),
        n_failures=sum(row.event_occurred for row in rows),
        n_right_censored=sum(not row.event_occurred for row in rows),
        points=tuple(points),
        warnings=tuple(warnings),
    )
