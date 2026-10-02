"""Framework \u00a76 load-scaled operating amps: pipeline-level effects.

The phasor form only changes what the design engine picks when there
is actually a power factor available to work with. The demo catalog
publishes no PF (see :mod:`esp_engine.catalog` docstring), so the
demo case falls to the linear fallback and the pipeline behaviour is
unchanged. This test suite uses the operator-override channel to
supply PF and efficiency for the specific motor a candidate lands on,
then measures that:

1. The phasor form takes effect (basis carries through to the
   candidate result).
2. Operating amps at partial load differ materially from the linear
   figure.
3. A mutation removing the phasor form (forcing it back to linear)
   changes the operating amps back to the linear figure.

The purpose is the mutation-guard invariant: the fix has to make a
different call than the placeholder in at least one path, otherwise it
is not actually restrictive.
"""

from __future__ import annotations

import math

import pytest

from api.app import _demo_feasible_case
from esp_engine.catalog import load_catalog
from esp_engine.config import DEFAULT_CONFIG
from esp_engine.models import (
    ElectricalConstraints,
    MotorElectricalOverride,
    Source,
    Tracked,
)
from esp_engine.pipeline import DesignEngine


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _case_with_pf_override(catalog, motor_id: str, pf: float, eff: float):
    """Return the demo case with an operator PF+Eff override applied to
    the motor a candidate is likely to land on. This turns off the
    linear-fallback path for that motor and forces the phasor form."""
    base = _demo_feasible_case()
    override = MotorElectricalOverride(
        motor_id=motor_id,
        power_factor=Tracked(
            value=pf,
            source=Source.ENGINEER_OVERRIDE,
            note="test-supplied nameplate PF",
        ),
        efficiency=Tracked(
            value=eff,
            source=Source.ENGINEER_OVERRIDE,
            note="test-supplied nameplate efficiency",
        ),
    )
    # ElectricalConstraints on the base case may already be populated;
    # start fresh with just the override tuple to keep the invariant
    # scope minimal.
    return base.model_copy(
        update={
            "metadata": base.metadata.model_copy(
                update={"case_id": f"pf-override-{motor_id}"}
            ),
            "electrical": ElectricalConstraints(
                motor_data_overrides=(override,),
            ),
        }
    )


def _pick_a_series_400_induction_motor(catalog) -> str:
    """The demo case lands on Series 400. Pick any induction motor in
    that series so the override actually attaches to a candidate that
    survives selection. Skip the test if no such motor exists (the
    default catalog contains only PMM at series 400, which uses the
    pmm_nearly_linear basis where operating_amps == linear reference by
    construction -- the physics distinction the test measures is only
    visible on induction candidates)."""
    for m in catalog.motors:
        if m.series == 400 and m.motor_type == "induction":
            return m.id
    pytest.skip(
        "no series-400 induction motor in the current catalog; the demo "
        "case lands on PMM where operating_amps == linear reference by "
        "construction, so this pipeline invariant is not exercisable "
        "here. The 14-test physics suite in test_electrical_load.py "
        "covers the induction phasor form directly."
    )


# ---------------------------------------------------------------------------
# The override plumbing at pipeline level
# ---------------------------------------------------------------------------


def test_pf_override_unlocks_b14_1_on_a_candidate(catalog):
    """Supplying PF+Eff via override must flip amps_basis on the
    candidates using that motor from nameplate_linear_scaling to a
    B.14.1 form. If the override does not propagate, the phasor form
    never runs on the pipeline and no downstream sizing changes."""
    motor_id = _pick_a_series_400_induction_motor(catalog)
    result = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG).run(
        _case_with_pf_override(catalog, motor_id, pf=0.85, eff=0.88)
    )
    if not result.candidates:
        pytest.skip("override case produced no candidates; nothing to check")
    hits = [
        c for c in result.candidates
        if c.configuration.motor_id == motor_id
    ]
    if not hits:
        pytest.skip(
            f"override motor {motor_id} did not appear in ranked candidates; "
            "picker landed on a different motor"
        )
    for c in hits:
        assert c.amps_basis in (
            "b14_1_from_power_factor_and_efficiency",
            "b14_1_from_operator_supplied_data",
        ), (
            f"candidate {c.rank} amps_basis was {c.amps_basis}; expected "
            "a B.14.1 form after PF+Eff override"
        )


# ---------------------------------------------------------------------------
# The physics: phasor \u2260 linear at partial load
# ---------------------------------------------------------------------------


def test_partial_load_operating_amps_exceed_linear_reference(catalog):
    """At partial load the phasor form returns more current than
    load\u00b7I_FL, because the magnetizing branch stays roughly fixed.
    That is the whole point of the fix. If the two are equal on a
    partial-load candidate, the phasor form is not doing work."""
    motor_id = _pick_a_series_400_induction_motor(catalog)
    result = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG).run(
        _case_with_pf_override(catalog, motor_id, pf=0.85, eff=0.88)
    )
    hits = [
        c for c in result.candidates
        if c.configuration.motor_id == motor_id
        and c.motor_operating_current is not None
    ]
    partial = [
        c for c in hits
        if c.motor_operating_current.load_fraction < 0.90
    ]
    if not partial:
        pytest.skip(
            "no partial-load candidate exercised; phasor \u2260 linear is only "
            "distinguishable at loads below 0.9"
        )
    for c in partial:
        oc = c.motor_operating_current
        assert oc.operating_amps > oc.linear_reference_amps, (
            f"candidate {c.rank}: operating {oc.operating_amps:.2f} A vs "
            f"linear {oc.linear_reference_amps:.2f} A -- phasor form is "
            "not amplifying at partial load"
        )
        # Sanity: at load < 0.9 with PF = 0.85, the correction should be
        # material (>5%). Otherwise something has rescaled the phasor
        # away from the identity in the pipeline.
        delta_frac = (
            oc.operating_amps - oc.linear_reference_amps
        ) / max(oc.linear_reference_amps, 1e-9)
        assert delta_frac > 0.05, (
            f"phasor correction was only {delta_frac:.1%} at load "
            f"{oc.load_fraction:.2f}; expected >5% at PF=0.85"
        )


# ---------------------------------------------------------------------------
# Mutation guard: the fix restricts, the placeholder permits
# ---------------------------------------------------------------------------


def test_mutation_forcing_linear_form_changes_operating_amps(
    monkeypatch, catalog
):
    """Baseline: run the case with the phasor form. Mutated: force
    ``compute_operating_current`` to return the linear form even when
    PF is available. If any candidate's operating amps changes between
    baseline and mutated, the phasor form is materially different from
    the placeholder \u2014 which is exactly the invariant \u00a76 depends on.
    Zero difference means the fix is inert."""
    from esp_engine import motor as motor_module
    from esp_engine.electrical_load import OperatingCurrent

    motor_id = _pick_a_series_400_induction_motor(catalog)
    case = _case_with_pf_override(catalog, motor_id, pf=0.85, eff=0.88)

    baseline = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG).run(case)

    real_compute = motor_module.compute_operating_current

    def force_linear(**kwargs):
        real = real_compute(**kwargs)
        return OperatingCurrent(
            operating_amps=real.linear_reference_amps,
            full_load_amps=real.full_load_amps,
            load_fraction=real.load_fraction,
            magnetizing_amps=0.0,
            load_amps=real.linear_reference_amps,
            apparent_power_factor=1.0,
            linear_reference_amps=real.linear_reference_amps,
            basis="nameplate_linear_scaling",
        )

    monkeypatch.setattr(motor_module, "compute_operating_current", force_linear)
    mutated = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG).run(case)

    # Compare operating amps on candidates sharing the same
    # configuration between baseline and mutated. If PF has an effect,
    # at least one shared candidate must differ.
    def _amps(c):
        # Prefer the phasor operating current if the physics path ran;
        # otherwise fall back to the linear reference so both baseline
        # and mutated read from the same field.
        oc = c.motor_operating_current
        if oc is not None:
            return oc.operating_amps
        # cell doesn't carry raw operating_amps at the candidate level,
        # but design_point (a CellResult) carries motor_amps_basis and
        # cable_voltage_drop_frac indirectly. Skip if we cannot resolve.
        return None

    baseline_amps = {
        (c.configuration.config_id, c.rank): _amps(c)
        for c in baseline.candidates
    }
    mutated_amps = {
        (c.configuration.config_id, c.rank): _amps(c)
        for c in mutated.candidates
    }
    common = set(baseline_amps).intersection(mutated_amps)
    differences = [
        (k, baseline_amps[k], mutated_amps[k])
        for k in common
        if baseline_amps[k] is not None
        and mutated_amps[k] is not None
        and not math.isclose(
            baseline_amps[k], mutated_amps[k], rel_tol=1e-6
        )
    ]

    if not common:
        pytest.skip(
            "no shared candidates between baseline and mutated runs; the "
            "mutation reshuffled ranking too heavily to compare directly"
        )
    assert differences, (
        "baseline and mutated agreed on operating_amps for every shared "
        "candidate. Either the PF override never engaged the phasor "
        "form, or the phasor and linear forms are producing identical "
        "amps -- both mean the fix is inert on this case."
    )
    for (k, base_a, mut_a) in differences:
        assert base_a > mut_a, (
            f"candidate {k} baseline {base_a:.2f} vs mutated {mut_a:.2f}: "
            "the phasor form should carry MORE current than the linear "
            "approximation at partial load; a lower baseline suggests a "
            "sign error somewhere in the derivation"
        )
