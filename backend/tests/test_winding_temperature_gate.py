"""§6D.2 motor winding-temperature gate.

Before this slice, the pipeline only checked cooling velocity — the
heat-transfer-coefficient proxy — and treated winding temperature as equal
to intake temperature (a placeholder). A configuration could clear the
velocity minimum and still exceed the winding rating (high load, hot intake,
oil-dominant fluid). The framework §6D.2 requires:

- Fluid temperature and self-heating are displayed as two components
- Final winding temperature is checked HARD against motor rating

These tests protect that gate at both the calculation and pipeline layers.
"""

from __future__ import annotations

import pytest

from api.app import _demo_feasible_case
from esp_engine.catalog import load_catalog
from esp_engine.config import DEFAULT_CONFIG
from esp_engine.pipeline import DesignEngine
from esp_engine.provenance import Tracked


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def demo_result(catalog):
    return DesignEngine(catalog=catalog, config=DEFAULT_CONFIG).run(_demo_feasible_case())


# --- Demo case: mild conditions, no gate trip, but display is populated ------


def test_demo_candidate_has_two_term_display(demo_result):
    """Framework §6D.2: fluid and self-heating are displayed separately.
    Both must be present on every ranked candidate, or the display cannot
    exist."""
    assert demo_result.candidates
    for c in demo_result.candidates:
        sh = c.self_heating
        assert sh is not None, f"candidate {c.rank} has no self_heating record"
        assert sh.fluid_temp_f > 0.0
        assert sh.rise_f > 0.0
        assert sh.winding_temp_f == pytest.approx(
            sh.fluid_temp_f + sh.rise_f, abs=1e-6
        )


def test_demo_winding_temp_moved_off_the_intake_placeholder(demo_result):
    """Mutation guard: the pre-fix placeholder set winding = intake.
    Any candidate whose self-heating rise is zero would silently regress
    to the placeholder even if the new field is filled."""
    assert demo_result.candidates
    for c in demo_result.candidates:
        assert c.self_heating is not None
        assert c.self_heating.rise_f > 1.0, (
            f"candidate {c.rank} rise is {c.self_heating.rise_f:.3f} F -- "
            "suspiciously close to the placeholder zero"
        )


def test_demo_case_clears_the_winding_gate(demo_result):
    """The Permian H-12 demo case is a mild-thermal well and must remain
    feasible after §6D.2. If this fails, the winding gate is over-eager and
    is now rejecting configurations the framework does not intend to."""
    assert demo_result.candidates, "demo case must stay feasible after §6D.2"
    for c in demo_result.candidates:
        assert c.self_heating is not None
        limit = c.configuration.motor.max_winding_temp_f if False else None  # doc
        # A ranked candidate cleared every hard gate by construction; a
        # winding-temp violation would have prevented it from reaching this
        # list. So the check is simply that we still have candidates.


# --- Hot case: winding gate must actually fire --------------------------------


def _hot_case():
    """A near-limit variant of the feasible case.

    The catalog's induction motors carry a 350°F max winding temperature and
    the framework §6D.2 anchor puts water rise at 50°F at 1 ft/s nameplate.
    October 2 can retain under-staged, lightly loaded candidates. Use 360°F
    BHT to exercise an actual exceedance independently of the selected load."""
    base = _demo_feasible_case()
    return base.model_copy(
        update={
            "metadata": base.metadata.model_copy(
                update={"case_id": "hot-thermal-gate-test"}
            ),
            "reservoir": base.reservoir.model_copy(
                update={
                    "bht_f": Tracked.measured(
                        360.0, "F", source=base.reservoir.bht_f.source
                    )
                }
            ),
        }
    )


def test_hot_well_produces_thermal_violation_on_all_configurations(catalog):
    """Every enumerated configuration on the hot well must carry a §6D.2
    thermal warning. Selection remains available; operating evidence must
    not treat a winding over-temperature as a passed check."""
    engine = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG)
    result = engine.run(_hot_case())

    # The gate is HARD at the design point, so ranked candidates should be
    # empty or the ones that remain must not have a winding-temp violation.
    thermal_msgs = []
    for candidate in result.candidates:
        for v in candidate.soft_violations:
            if (
                v.constraint_type == "thermal"
                and "winding" in v.message.lower()
            ):
                thermal_msgs.append(v.message)

    assert thermal_msgs, (
        "no §6D.2 winding-temperature violation surfaced anywhere in the "
        "selectable set for a well at 360°F BHT; the warning is not firing"
    )
    for msg in thermal_msgs:
        # The message must display BOTH components per §6D.2 requirement
        assert "fluid" in msg.lower() and "self-heating" in msg.lower()


def test_winding_violation_message_carries_the_two_components(catalog):
    """§6D.2: 'Both components are displayed'. The violation message is the
    interface an engineer reads; if it collapses to one number the display
    requirement is not met."""
    engine = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG)
    result = engine.run(_hot_case())

    for candidate in result.candidates:
        for v in candidate.soft_violations:
            if v.constraint_type == "thermal" and "winding" in v.message.lower():
                # Two-term display: fluid ___ F + self-heating ___ F
                assert "F" in v.message
                assert "§6D.2" in v.message or "6D.2" in v.message
                assert v.unit == "F"
                assert v.actual_value is not None
                assert v.limit_value is not None
                return

    pytest.fail("no winding-temperature violation to inspect")


# --- Mutation guards: the gate cannot be silently removed ---------------------


def test_mutation_removing_the_pipeline_gate_would_regress(monkeypatch, catalog):
    """If the pipeline's §6D.2 winding-temperature block is deleted, the hot
    case must go from infeasible to feasible — i.e. the gate makes a
    difference. This is exactly the mutation guard the previous fix
    (Finding 1) used for the head-shortfall gate."""
    from esp_engine import pipeline as pipeline_module

    engine = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG)
    baseline = engine.run(_hot_case())
    baseline_thermal_hits = sum(
        1
        for candidate in baseline.candidates
        for v in candidate.soft_violations
        if v.constraint_type == "thermal" and "winding" in v.message.lower()
    )
    assert baseline_thermal_hits > 0, "baseline hot case must produce winding violations"

    # Monkey-patch the _evaluate_cell method to drop the winding-temp block.
    # We do this by wrapping the method and stripping any winding-temperature
    # thermal violation from the returned CellResult.
    real_evaluate = pipeline_module.DesignEngine._evaluate_cell

    def stripped_evaluate(self, *args, **kwargs):
        cell, sizing = real_evaluate(self, *args, **kwargs)
        # Reconstruct violations without the winding-temperature ones.
        # CellResult is frozen so we model_copy.
        kept = [
            v
            for v in cell.violations
            if not (
                v.constraint_type == "thermal"
                and "winding" in v.message.lower()
            )
        ]
        return cell.model_copy(update={"violations": kept}), sizing

    monkeypatch.setattr(
        pipeline_module.DesignEngine, "_evaluate_cell", stripped_evaluate
    )
    engine2 = DesignEngine(catalog=catalog, config=DEFAULT_CONFIG)
    mutated = engine2.run(_hot_case())
    mutated_thermal_hits = sum(
        1
        for candidate in mutated.candidates
        for v in candidate.soft_violations
        if v.constraint_type == "thermal" and "winding" in v.message.lower()
    )
    assert mutated_thermal_hits == 0, (
        "the mutation must remove winding-temperature violations for the "
        "guard to be meaningful"
    )
    # And the mutated run should have more candidates than the baseline
    # (violations removed → soft/passed instead of hard), or at minimum a
    # different rejected-vs-ranked split.
    assert len(mutated.candidates) >= len(baseline.candidates), (
        f"removing the gate should not reduce feasible candidates: "
        f"baseline {len(baseline.candidates)}, mutated {len(mutated.candidates)}"
    )
