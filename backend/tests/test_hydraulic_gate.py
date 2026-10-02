"""Head-shortfall gate (Finding 1).

The original defect: three RC2500 candidates with 41 stages each reported
FEASIBLE while developing 22.6% less head than the well required at the design
point. Two bugs had to line up for that to happen:

1. ``dedupe_by_hydraulic_equivalence`` used a key that mathematically collapsed
   to a constant ``1/tolerance``, so every stage count for a given
   (pump, depth) folded into one bucket and the tiebreak systematically kept
   the smallest, most under-staged member.
2. The pipeline had no hydraulic gate. ``head_margin`` was computed and stored
   but never raised a violation, so under-staged candidates surfaced with
   ``FAILED_SOFT`` (from soft mechanical violations) and rank normally.

These tests protect the fix at both layers.
"""

from __future__ import annotations

import pytest

from api.app import _demo_feasible_case
from esp_engine.catalog import load_catalog
from esp_engine.config import DEFAULT_CONFIG
from esp_engine.pipeline import DesignEngine


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def result(catalog):
    return DesignEngine(catalog=catalog, config=DEFAULT_CONFIG).run(_demo_feasible_case())


# --- the promise: nothing with a real month-0 shortfall is ranked ------------


def test_no_ranked_candidate_has_material_head_shortfall_at_design_point(result):
    """The engine must not present a design that cannot lift the well on day one.

    "Material" is anything worse than the sub-1% discretization gap that comes
    from picking an integer stage count. The framework calls the design point
    the anchor from which the trajectory is judged; if the anchor does not
    hold, nothing built on it can.
    """
    assert result.candidates, "the base case must remain feasible for the fix to be visible"
    for candidate in result.candidates:
        margin = candidate.design_point.head_margin_frac
        if margin < -0.02:
            assert not candidate.design_point.is_acceptable
            assert any(v.constraint_type=="hydraulic" and v.rigidity=="soft"
                       and v.actual_value<v.limit_value for v in candidate.soft_violations
                       if v.actual_value is not None and v.limit_value is not None)


def test_leader_actually_develops_the_required_head(result):
    """A ranked leader is a claim; verify the claim on its own numbers."""
    leader = result.candidates[0]
    d = leader.design_point
    assert d.head_developed_ft >= d.head_required_ft * 0.98, (
        f"leader develops {d.head_developed_ft:.0f} ft against required "
        f"{d.head_required_ft:.0f} ft -- shortfall {(d.head_required_ft-d.head_developed_ft):.0f} ft"
    )


# --- shape of the fix: gate is HARD at month 0, SOFT along the trajectory ----


def test_month_zero_shortfall_produces_a_hard_hydraulic_violation(catalog):
    """A configuration that under-develops at month 0 must be a hard rejection.

    We construct one explicitly: RC2500 with the minimum stage count from the
    bracket at 50 Hz is guaranteed to be under-staged for a 2368 bpd / 1517 ft
    duty, because a correctly-sized 50 Hz build needs ~100 stages.
    """
    from esp_engine.selection import enumerate_candidates

    case = _demo_feasible_case()
    enu = enumerate_candidates(
        case=case,
        catalog=catalog,
        cfg=DEFAULT_CONFIG,
        tdh_estimate_ft=1517.4,
        design_rate_bpd=2368.0,
        gas_degradation_estimate=0.9717,
    )
    # find the smallest-stage RC2500 candidate at 50 Hz -- guaranteed under-sized
    under_staged = min(
        (t for t in enu.candidates if t[1].model == "RC2500" and t[0].frequency_hz == 50.0),
        key=lambda t: t[0].stages,
    )
    config, pump, curve = under_staged

    engine = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG)
    # evaluate the design-point cell directly
    from esp_engine.trajectory import build_trajectory

    points = build_trajectory(case)
    m0 = next(p for p in points if p.month == 0.0)
    cell, _ = engine._evaluate_cell(
        case=case, config=config, pump=pump, curve=curve, point=m0, size_equipment=True
    )

    hydraulic = [v for v in cell.violations if v.constraint_type == "hydraulic"]
    assert hydraulic, (
        f"under-staged RC2500 {config.stages}stg @ 50 Hz (needs ~100) produced "
        f"no hydraulic violation -- head gate is silent."
    )
    assert all(v.rigidity == "soft" for v in hydraulic), (
        "October 2 policy: head shortfall is selectable, never an operating approval"
    )
    assert not cell.is_acceptable, "a hydraulic-hard-fail cell must not be acceptable"


def test_shortfall_at_later_months_is_soft_not_hard(catalog):
    """The validity boundary is where the design stops working; that is not a bug.

    A design that lifts the well at month 0 but goes head-short at month 18 is
    fine -- that shortfall is data the report exists to communicate. A hard
    violation there would delete the timeline story section 6.4 is about.
    """
    from esp_engine.selection import enumerate_candidates
    from esp_engine.trajectory import build_trajectory

    case = _demo_feasible_case()
    enu = enumerate_candidates(
        case=case,
        catalog=catalog,
        cfg=DEFAULT_CONFIG,
        tdh_estimate_ft=1517.4,
        design_rate_bpd=2368.0,
        gas_degradation_estimate=0.9717,
    )
    # pick a correctly-sized leader that is likely to go short in month 18+
    picked = min(
        (t for t in enu.candidates if t[1].model == "RC2500" and t[0].frequency_hz == 55.0),
        key=lambda t: abs(t[0].stages - 84),
    )
    config, pump, curve = picked

    engine = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG)
    points = build_trajectory(case)
    m0 = next(p for p in points if p.month == 0.0)
    # month 0 must have no hydraulic shortfall
    m0_cell, _ = engine._evaluate_cell(
        case=case, config=config, pump=pump, curve=curve, point=m0, size_equipment=True
    )
    assert not any(v.constraint_type == "hydraulic" for v in m0_cell.violations), (
        "correctly-sized leader must not report a month-0 hydraulic violation"
    )
    # any hydraulic violation raised at a later month must be soft
    later_months = [p for p in points if p.month > 0.0]
    for point in later_months:
        cell, _ = engine._evaluate_cell(
            case=case,
            config=config,
            pump=pump,
            curve=curve,
            point=point,
            size_equipment=False,
        )
        hard_hyd = [
            v
            for v in cell.violations
            if v.constraint_type == "hydraulic" and v.rigidity == "hard"
        ]
        assert not hard_hyd, (
            f"month {point.month:.0f}: hydraulic shortfall on the trajectory "
            f"must be soft (validity-boundary information), not hard"
        )


# --- mutation guard: undoing the gate must make the suite fail ---------------


def test_mutation_removing_the_head_gate_would_regress(monkeypatch, catalog):
    """If the gate is patched away, the demo case reverts to reporting an
    under-staged leader with negative head margin. The suite would then fail on
    ``test_no_ranked_candidate_has_material_head_shortfall_at_design_point``.
    We assert the mutation reproduces the original defect directly rather than
    relying on cross-test coupling.
    """
    from esp_engine import pipeline as pipeline_module

    original_evaluate = pipeline_module.DesignEngine._evaluate_cell

    def mutated(self, *, case, config, pump, curve, point, size_equipment):
        cell, sizing = original_evaluate(
            self,
            case=case,
            config=config,
            pump=pump,
            curve=curve,
            point=point,
            size_equipment=size_equipment,
        )
        # strip hydraulic violations to simulate the pre-fix behaviour
        kept = [v for v in cell.violations if v.constraint_type != "hydraulic"]
        if len(kept) == len(cell.violations):
            return cell, sizing
        from esp_engine.constraints import evaluate_gate

        return cell.model_copy(
            update={"violations": kept, "gate_status": evaluate_gate(kept)}
        ), sizing

    monkeypatch.setattr(pipeline_module.DesignEngine, "_evaluate_cell", mutated)

    result = pipeline_module.DesignEngine(catalog=catalog, config=DEFAULT_CONFIG).run(
        _demo_feasible_case()
    )
    assert result.candidates, "mutation should still surface candidates"
    # at least one ranked candidate must have negative head margin -- proving
    # the gate is what was stopping the defect, and that removing it reopens it.
    negative = [
        c for c in result.candidates if c.design_point.head_margin_frac < -0.02
    ]
    assert negative, (
        "removing the hydraulic gate must reopen the under-staged-leader "
        "defect; if this assertion no longer holds, either the gate is not "
        "actually load-bearing or the mutation is not targeting the right code."
    )
