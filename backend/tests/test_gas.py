"""Gas-risk validation against physics-reference.md §3."""
from __future__ import annotations

import pytest

from esp_engine.config import CorrelationConfig, GasThresholds, NaturalSeparationModel
from esp_engine.gas import assess_gas, head_degradation_factor, natural_separation_efficiency, turpin_parameter
from .test_intake import _solve


def test_turpin_published_equation_example() -> None:
    """Turpin stability equation in the gas-liquid ESP review cited by
    physics-reference.md §3.2: phi=2000/PIP^3*(qg/ql)."""
    expected = 2000 / 1000**3 * (100 / 1000)
    assert turpin_parameter(free_gas_rate_bpd=100, liquid_rate_bpd=1000, pip_psi=1000) == pytest.approx(expected)


def test_natural_separation_geometry_and_none_model() -> None:
    none = natural_separation_efficiency(liquid_rate_bpd=1000, gas_rate_bpd=100, casing_id_in=6, equipment_od_in=4.5, model=NaturalSeparationModel.NONE)
    narrow = natural_separation_efficiency(liquid_rate_bpd=1000, gas_rate_bpd=100, casing_id_in=6, equipment_od_in=4.5, model=NaturalSeparationModel.ALHANATI)
    wide = natural_separation_efficiency(liquid_rate_bpd=1000, gas_rate_bpd=100, casing_id_in=7, equipment_od_in=4.5, model=NaturalSeparationModel.ALHANATI)
    assert none == 0
    assert 0 <= narrow < wide <= 1


def test_rationale_names_every_configured_threshold_and_is_deterministic() -> None:
    intake = _solve()
    assessment = assess_gas(intake, casing_id_in=6, equipment_od_in=4.5, has_vsd=True, cfg=CorrelationConfig(), thresholds=GasThresholds())
    for text in ("0.100", "0.250", "0.450", "0.750", "stability limit 1"):
        assert text in assessment.rationale
    assert assessment == assess_gas(intake, casing_id_in=6, equipment_od_in=4.5, has_vsd=True, cfg=CorrelationConfig(), thresholds=GasThresholds())


def test_head_factor_bounds_and_fraction_trap() -> None:
    assert head_degradation_factor(0.25) < head_degradation_factor(0.1) <= 1
    assert head_degradation_factor(0)==1
    with pytest.raises(ValueError,match="override"):
        head_degradation_factor(.3)
    with pytest.raises(ValueError, match="fraction"):
        head_degradation_factor(85)
