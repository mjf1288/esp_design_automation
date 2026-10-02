"""Motor loading/cooling tests; reference: physics-reference.md §§6.1–6.3."""
from __future__ import annotations

import pytest

from esp_engine.config import MotorThresholds
from esp_engine.motor import size_motor


def test_motor_loading_target_and_constant_vhz(catalog):
    result = size_motor(catalog=catalog, bhp_pump=78, gas_handling_hp=0, protector_loss_hp=2, series=400, max_od_in=4.0, frequency_hz=60, casing_id_in=5.0, total_fluid_rate_bpd=5000, intake_temp_f=200, thresholds=MotorThresholds())
    assert result is not None
    assert result.motor.id == "fixture-motor-100"
    assert result.loading_frac == 0.8
    assert result.loading_in_target_band is True
    assert result.cooling_adequate is True
    at_50 = size_motor(catalog=catalog, bhp_pump=60, gas_handling_hp=0, protector_loss_hp=0, series=400, max_od_in=4.0, frequency_hz=50, casing_id_in=5.0, total_fluid_rate_bpd=5000, intake_temp_f=200, thresholds=MotorThresholds())
    assert at_50 is not None
    assert at_50.operating_volts == 500
    assert at_50.hp_nameplate_at_frequency == pytest.approx(100 * 50 / 60)


def test_motor_returns_none_when_catalog_cannot_fit(catalog):
    assert size_motor(catalog=catalog, bhp_pump=500, gas_handling_hp=0, protector_loss_hp=0, series=400, max_od_in=4.0, frequency_hz=60, casing_id_in=4.0, total_fluid_rate_bpd=1000, intake_temp_f=180, thresholds=MotorThresholds()) is None
