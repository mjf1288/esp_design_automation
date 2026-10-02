"""Mechanical post-hoc gate tests; reference: physics-reference.md §5.5."""
from __future__ import annotations

from esp_engine.config import MechanicalThresholds
from esp_engine.mechanical import check_mechanical


def test_mechanical_failure_is_actionable(catalog):
    result = check_mechanical(
        pump=catalog.pump("fixture-pump"), seal=catalog.seals_for_series(400)[0],
        stages=101, bhp_total=110, gas_handling_hp=5, frequency_hz=60,
        thresholds=MechanicalThresholds(max_stages_per_housing=100),
    )
    assert result.passed is False
    assert result.shaft_hp_required == 115
    assert result.shaft_hp_available == 95
    assert any("reduce stages" in message for message in result.failures)
    assert any("exceeds pump" in message for message in result.failures)
    assert result.thrust_capacity_lb == 900
    assert result.thrust_load_lb is None


def test_mechanical_is_deterministic(catalog):
    kwargs = dict(pump=catalog.pump("fixture-pump"), seal=None, stages=10, bhp_total=20, gas_handling_hp=0, frequency_hz=50, thresholds=MechanicalThresholds())
    assert check_mechanical(**kwargs).model_dump_json() == check_mechanical(**kwargs).model_dump_json()
