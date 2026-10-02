"""Kaplan-Meier behavior for right-censored ESP run-life observations."""

from __future__ import annotations

import pytest

from esp_empirical.survival import RunLifeObservation, kaplan_meier


def test_kaplan_meier_keeps_still_running_unit_in_risk_set_until_censoring() -> None:
    """Product-limit and Greenwood variance follow https://pmc.ncbi.nlm.nih.gov/articles/PMC6141203/."""
    estimate = kaplan_meier(
        [
            RunLifeObservation(observation_id="failed-1", run_life_days=10, event_occurred=True),
            RunLifeObservation(observation_id="running", run_life_days=20, event_occurred=False),
            RunLifeObservation(observation_id="failed-2", run_life_days=30, event_occurred=True),
        ]
    )

    assert estimate.n_failures == 2
    assert estimate.n_right_censored == 1
    assert estimate.points[0].n_at_risk == 3
    assert estimate.points[0].survival_probability == pytest.approx(2.0 / 3.0)
    assert estimate.points[0].greenwood_variance == pytest.approx(2.0 / 27.0)
    assert estimate.points[1].n_at_risk == 2  # The running unit was at risk through day 20.
    assert estimate.points[1].n_events == 0
    assert estimate.points[2].n_at_risk == 1
    assert estimate.points[2].survival_probability == 0.0
    assert estimate.survival_at(20) == pytest.approx(2.0 / 3.0)


def test_empty_survival_input_refuses_to_imply_reliability() -> None:
    estimate = kaplan_meier([])
    assert estimate.points == ()
    assert estimate.survival_at(100) is None
    assert "No run-life observations" in estimate.warnings[0]
