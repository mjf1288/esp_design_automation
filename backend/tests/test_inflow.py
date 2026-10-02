"""IPR validation against physics-reference.md §1."""
from __future__ import annotations

import pytest

from esp_engine.config import IPRModel
from esp_engine.inflow import InflowSpec, absolute_open_flow_bpd, pwf_for_rate, rate_for_pwf


def test_vogel_published_equation_example_and_inverse() -> None:
    """Vogel (JPT 1970), equation reproduced in physics-reference.md §1.2:
    qmax=1000 bpd at Pwf/Pr=.5 gives q=700 bpd, and its positive-root inverse
    returns Pwf=1500 psi."""
    spec = InflowSpec(reservoir_pressure_psi=3000, productivity_index_bpd_psi=None, bubble_point_psi=2500,
                      test_rate_bpd=700, test_pwf_psi=1500, model=IPRModel.VOGEL)
    assert absolute_open_flow_bpd(spec) == pytest.approx(1000)
    assert rate_for_pwf(spec, 1500) == pytest.approx(700)
    assert pwf_for_rate(spec, 700) == pytest.approx(1500)


def test_composite_is_continuous_at_bubble_point() -> None:
    spec = InflowSpec(reservoir_pressure_psi=3000, productivity_index_bpd_psi=2.0, bubble_point_psi=2000)
    just_above = rate_for_pwf(spec, 2000.001)
    at_pb = rate_for_pwf(spec, 2000)
    just_below = rate_for_pwf(spec, 1999.999)
    assert just_above == pytest.approx(at_pb, abs=.01)
    assert just_below == pytest.approx(at_pb, abs=.01)


def test_aof_error_reveals_unachievable_expectation() -> None:
    spec = InflowSpec(reservoir_pressure_psi=3000, productivity_index_bpd_psi=1, bubble_point_psi=0, model=IPRModel.PI_LINEAR)
    with pytest.raises(ValueError, match=r"target rate of 3001 bpd exceeds the well's absolute open flow of 3000 bpd; the expectation is not achievable regardless of pump selection"):
        pwf_for_rate(spec, 3001)


def test_pi_and_saturated_reservoir_edges_and_determinism() -> None:
    pi = InflowSpec(reservoir_pressure_psi=2500, productivity_index_bpd_psi=1.5, bubble_point_psi=1500, model=IPRModel.PI_LINEAR)
    assert rate_for_pwf(pi, 1500) == pytest.approx(1500)
    assert pwf_for_rate(pi, 1500) == pytest.approx(1500)
    saturated = InflowSpec(reservoir_pressure_psi=2000, productivity_index_bpd_psi=1.5, bubble_point_psi=2300)
    assert 0 < pwf_for_rate(saturated, 600) < 2000
    assert rate_for_pwf(saturated, 1000) == rate_for_pwf(saturated, 1000)
