"""Phasor load-current model tests.

Framework \u00a76 (last remaining electrical placeholder). Verifies the
physics identities in :mod:`esp_engine.electrical_load` and the
provenance / disclosure surface, and includes a mutation guard proving
that the phasor form makes a materially different call than the
linear approximation at partial load.
"""

from __future__ import annotations

import math

import pytest

from esp_engine.electrical_load import (
    OperatingCurrent,
    compute_operating_current,
)


# ---------------------------------------------------------------------------
# Physics identities
# ---------------------------------------------------------------------------


def test_full_load_agrees_with_i_fl_regardless_of_form() -> None:
    """At load = 1.0 all four forms must return I_FL. The phasor
    decomposition should reduce to the nameplate reference by
    construction; any drift here is a bug in the phasor identity."""
    for pf in [0.70, 0.85, 0.92]:
        oc = compute_operating_current(
            load_fraction=1.0,
            full_load_amps=100.0,
            nameplate_amps=100.0,
            power_factor=pf,
            is_permanent_magnet=False,
        )
        assert oc.operating_amps == pytest.approx(100.0, rel=1e-9)
        assert oc.linear_reference_amps == pytest.approx(100.0)
    # PMM branch
    oc = compute_operating_current(
        load_fraction=1.0,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=None,
        is_permanent_magnet=True,
    )
    assert oc.operating_amps == pytest.approx(100.0, rel=1e-9)
    # Linear fallback
    oc = compute_operating_current(
        load_fraction=1.0,
        full_load_amps=None,
        nameplate_amps=100.0,
        power_factor=None,
        is_permanent_magnet=False,
    )
    assert oc.operating_amps == pytest.approx(100.0, rel=1e-9)


def test_phasor_identity_at_partial_load_matches_hand_derivation() -> None:
    """At load = 0.5, PF_FL = 0.85, I_FL = 100 A: I_mu = 100*sqrt(1-0.85\u00b2)
    = 52.68, I_L = 0.5*100*0.85 = 42.5, I = sqrt(52.68\u00b2 + 42.5\u00b2) = 67.7."""
    oc = compute_operating_current(
        load_fraction=0.5,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=0.85,
        is_permanent_magnet=False,
    )
    expected_i_mu = 100.0 * math.sqrt(1.0 - 0.85**2)
    expected_i_l = 0.5 * 100.0 * 0.85
    expected_i = math.sqrt(expected_i_mu**2 + expected_i_l**2)
    assert oc.magnetizing_amps == pytest.approx(expected_i_mu, rel=1e-9)
    assert oc.load_amps == pytest.approx(expected_i_l, rel=1e-9)
    assert oc.operating_amps == pytest.approx(expected_i, rel=1e-9)


def test_phasor_form_disagrees_with_linear_at_partial_load() -> None:
    """The whole point of \u00a76: phasor and linear are not the same at
    partial load. At load = 0.5 with PF = 0.85, phasor returns 67.7 A
    while linear returns 50.0 A \u2014 a 35% correction upward."""
    oc = compute_operating_current(
        load_fraction=0.5,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=0.85,
        is_permanent_magnet=False,
    )
    assert oc.operating_amps > oc.linear_reference_amps
    delta_frac = (
        oc.operating_amps - oc.linear_reference_amps
    ) / oc.linear_reference_amps
    assert delta_frac > 0.30, (
        f"phasor correction was {delta_frac:.1%}; expected >30% at "
        "PF=0.85, load=0.5"
    )


def test_apparent_power_factor_drops_at_partial_load() -> None:
    """Framework B.16: 'power factor drops off-BEP'. Reproduced by the
    identity below \u2014 apparent PF at load 0.5 with nameplate PF 0.85
    should come in around 0.63."""
    oc = compute_operating_current(
        load_fraction=0.5,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=0.85,
        is_permanent_magnet=False,
    )
    assert oc.apparent_power_factor < 0.85
    assert oc.apparent_power_factor == pytest.approx(0.628, abs=0.01)


def test_pmm_current_is_nearly_linear_when_residual_is_zero() -> None:
    """With residual magnetizing = 0 (the default), a PMM's operating
    current equals load \u00b7 I_FL exactly. This is by construction and
    the framework's B.16 statement that PMM PF is near unity."""
    for load in [0.3, 0.5, 0.8, 1.0]:
        oc = compute_operating_current(
            load_fraction=load,
            full_load_amps=100.0,
            nameplate_amps=100.0,
            power_factor=None,
            is_permanent_magnet=True,
        )
        assert oc.operating_amps == pytest.approx(100.0 * load, rel=1e-9)
        assert oc.apparent_power_factor == pytest.approx(1.0, rel=1e-9)
        assert oc.basis == "pmm_nearly_linear"


def test_pmm_residual_magnetizing_shifts_current_and_pf() -> None:
    """Some VSD-driven PMMs carry a small residual magnetizing branch
    (typically <10% of I_FL from controller and stator inductance). If
    a vendor supplies that figure it should be honored, not zeroed."""
    oc = compute_operating_current(
        load_fraction=0.5,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=None,
        is_permanent_magnet=True,
        pmm_residual_magnetizing_fraction=0.10,
    )
    # I_L_component_at_FL = 100 * sqrt(1 - 0.01) = 99.5
    # I_L(0.5) = 49.75, I_mu = 10, I = sqrt(49.75\u00b2 + 100) = 50.75
    assert oc.magnetizing_amps == pytest.approx(10.0, rel=1e-9)
    assert oc.operating_amps == pytest.approx(50.75, abs=0.1)
    assert oc.apparent_power_factor < 1.0


def test_linear_fallback_when_no_pf_and_no_override() -> None:
    """Framework \u00a73.5 / B.16: PF is not filled by midpoint. When the
    catalog omits PF and no operator override is supplied, the model
    must degrade to linear scaling rather than fabricate a PF."""
    oc = compute_operating_current(
        load_fraction=0.5,
        full_load_amps=None,
        nameplate_amps=100.0,
        power_factor=None,
        is_permanent_magnet=False,
    )
    assert oc.basis == "nameplate_linear_scaling"
    assert oc.operating_amps == pytest.approx(50.0, rel=1e-9)
    assert oc.magnetizing_amps == 0.0  # not fabricated


# ---------------------------------------------------------------------------
# Operator override path
# ---------------------------------------------------------------------------


def test_operator_magnetizing_fraction_override_wins_over_catalog_pf() -> None:
    """An operator holding measured no-load / full-load current data is
    a more direct source than the phasor identity applied to a
    cataloged nameplate PF. So the override path must take precedence
    when both are available."""
    with_pf = compute_operating_current(
        load_fraction=0.5,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=0.85,
        is_permanent_magnet=False,
    )
    with_override = compute_operating_current(
        load_fraction=0.5,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=0.85,
        is_permanent_magnet=False,
        operator_magnetizing_fraction=0.30,  # much lower than PF implies
    )
    assert with_override.basis == "phasor_from_operator_magnetizing_fraction"
    assert with_pf.basis == "phasor_from_cataloged_power_factor"
    assert with_override.magnetizing_amps == pytest.approx(30.0, rel=1e-9)
    assert with_override.operating_amps != pytest.approx(
        with_pf.operating_amps, rel=1e-3
    )


def test_operator_override_at_full_load_reduces_to_i_fl() -> None:
    """Same anchor identity as the PF path: at load 1.0 the override
    form must return exactly I_FL, otherwise the decomposition is
    inconsistent."""
    oc = compute_operating_current(
        load_fraction=1.0,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=None,
        is_permanent_magnet=False,
        operator_magnetizing_fraction=0.40,
    )
    assert oc.operating_amps == pytest.approx(100.0, rel=1e-9)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_pf", [0.0, -0.1, 1.5])
def test_invalid_power_factor_raises(bad_pf: float) -> None:
    with pytest.raises(ValueError, match="power_factor"):
        compute_operating_current(
            load_fraction=0.5,
            full_load_amps=100.0,
            nameplate_amps=100.0,
            power_factor=bad_pf,
            is_permanent_magnet=False,
        )


@pytest.mark.parametrize("bad_frac", [-0.1, 1.0, 1.5])
def test_invalid_magnetizing_fraction_raises(bad_frac: float) -> None:
    with pytest.raises(ValueError, match="magnetizing_fraction"):
        compute_operating_current(
            load_fraction=0.5,
            full_load_amps=100.0,
            nameplate_amps=100.0,
            power_factor=None,
            is_permanent_magnet=False,
            operator_magnetizing_fraction=bad_frac,
        )


def test_negative_load_fraction_raises() -> None:
    with pytest.raises(ValueError, match="load_fraction"):
        compute_operating_current(
            load_fraction=-0.1,
            full_load_amps=100.0,
            nameplate_amps=100.0,
            power_factor=0.85,
            is_permanent_magnet=False,
        )


# ---------------------------------------------------------------------------
# Boundary behavior
# ---------------------------------------------------------------------------


def test_zero_load_still_draws_magnetizing_current() -> None:
    """The characteristic physical difference between linear and phasor
    forms: at zero shaft load an induction motor still draws its
    magnetizing branch. Linear would report zero. The phasor form
    reports I_mu."""
    oc = compute_operating_current(
        load_fraction=0.0,
        full_load_amps=100.0,
        nameplate_amps=100.0,
        power_factor=0.85,
        is_permanent_magnet=False,
    )
    expected_i_mu = 100.0 * math.sqrt(1.0 - 0.85**2)
    assert oc.operating_amps == pytest.approx(expected_i_mu, rel=1e-9)
    assert oc.linear_reference_amps == 0.0
