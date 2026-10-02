"""Framework v0.6 §6C.4: the shaft is checked against motor nameplate power.

The point of these tests is not that the numbers come out a particular way. It
is that three specific reasoning errors stay impossible:

1. Checking the shaft against the operating load. The demo wells run at ~10% of
   the pump shaft limit, so a load-referenced check passes trivially and the
   change looks vacuous. It is not: the nameplate is 4x the operating load, and
   three cataloged pumps have shaft limits below the largest motor that fits
   their housing.
2. Rejecting an overloaded configuration. §6C.4 says warn and display; §3.2 says
   resolve through build variants. Rejecting destroys the information that a
   large-shaft build of the SAME pump would fix it.
3. Letting a skipped check read as a passed one. If no motor was selected there
   is no nameplate, and that must be visible in the output rather than inferred
   from a utilization number that silently means something else.
"""

from __future__ import annotations

import pytest

from esp_engine.catalog import load_catalog
from esp_engine.config import EngineConfig
from esp_engine.mechanical import check_mechanical
from esp_engine.pipeline import DesignEngine

from tests.test_pipeline import _base_case


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def thresholds():
    return EngineConfig().thresholds.mechanical


def _pump(catalog, model: str):
    return next(p for p in catalog.pumps if p.model == model)


def _check(catalog, thresholds, *, model="RC2500", nameplate, bhp=20.0, stages=40):
    pump = _pump(catalog, model)
    return check_mechanical(
        pump=pump,
        seal=None,
        stages=stages,
        bhp_total=bhp,
        gas_handling_hp=0.0,
        frequency_hz=pump.frequency_ref_hz,
        thresholds=thresholds,
        motor_nameplate_hp=nameplate,
    )


# --- 1. the reference quantity is the nameplate ---------------------------


def test_required_hp_is_the_nameplate_not_the_operating_load(catalog, thresholds):
    """A 240 hp plate on a 20 hp load must be checked as 240, not as 20."""
    check = _check(catalog, thresholds, nameplate=240.0, bhp=20.0)
    assert check.shaft_hp_required == pytest.approx(240.0)
    assert check.shaft_hp_operating_load == pytest.approx(20.0)
    assert check.motor_nameplate_hp == pytest.approx(240.0)


def test_operating_load_alone_would_have_passed_where_nameplate_fails(catalog, thresholds):
    """The two references must be capable of disagreeing on the verdict.

    Without this, a nameplate check that happens never to bind is
    indistinguishable from no check at all.
    """
    pump = _pump(catalog, "GN3200")  # shaft limit 256 hp
    check = _check(catalog, thresholds, model="GN3200", nameplate=400.0, bhp=30.0)
    assert check.shaft_hp_operating_load < pump.shaft_hp_limit, "load reference passes"
    assert check.shaft_nameplate_utilization > 1.0, "nameplate reference fails"


def test_nameplate_utilization_scales_with_the_plate(catalog, thresholds):
    a = _check(catalog, thresholds, nameplate=120.0)
    b = _check(catalog, thresholds, nameplate=240.0)
    assert b.shaft_nameplate_utilization == pytest.approx(
        2.0 * a.shaft_nameplate_utilization
    )


def test_load_does_not_move_the_nameplate_verdict(catalog, thresholds):
    """Hydraulic load must not influence the fracture check at all."""
    light = _check(catalog, thresholds, model="GN3200", nameplate=400.0, bhp=5.0)
    heavy = _check(catalog, thresholds, model="GN3200", nameplate=400.0, bhp=200.0)
    assert light.shaft_nameplate_utilization == heavy.shaft_nameplate_utilization


# --- 2. warn, do not reject ----------------------------------------------


def test_exceeded_nameplate_warns_and_does_not_fail(catalog, thresholds):
    check = _check(catalog, thresholds, model="GN3200", nameplate=400.0)
    assert check.passed, "6C.4 warns rather than rejecting"
    assert not any("FRACTURE" in f for f in check.failures)
    assert any("SHAFT FRACTURE RISK" in w for w in check.warnings)


def test_warning_names_the_failure_mode_and_the_remedy(catalog, thresholds):
    check = _check(catalog, thresholds, model="GN3200", nameplate=400.0)
    warning = next(w for w in check.warnings if "SHAFT FRACTURE RISK" in w)
    assert "fractures" in warning, "the failure mode is breakage, not overload"
    assert "large-shaft" in warning or "high-strength" in warning
    assert "not cleared for install" in warning
    # The warning must carry both numbers so the engineer can verify rather
    # than trust -- §6C.4's "a verdict without numbers is a request for trust".
    assert "400.0" in warning and "256.0" in warning


def test_build_variant_gap_is_disclosed_on_overload(catalog, thresholds):
    """§3.2 Principle 2's escalation path does not exist in this catalog."""
    check = _check(catalog, thresholds, model="GN3200", nameplate=400.0)
    assert any("build variants" in w for w in check.warnings)
    assert check.build_variants_available == ()


def test_no_fracture_warning_when_within_the_limit(catalog, thresholds):
    check = _check(catalog, thresholds, nameplate=100.0)  # RC2500 limit 240
    assert not any("SHAFT FRACTURE RISK" in w for w in check.warnings)
    assert not any("build variants" in w for w in check.warnings)
    assert check.shaft_nameplate_utilization < 1.0


# --- 3. a skipped check must not read as a passed one --------------------


def test_absent_motor_marks_the_check_as_not_performed(catalog, thresholds):
    check = _check(catalog, thresholds, nameplate=None)
    assert check.shaft_nameplate_check_possible is False
    assert check.shaft_nameplate_utilization is None
    assert any("could not be performed" in w for w in check.warnings)
    assert any("not the governing check" in w for w in check.warnings)


def test_absent_motor_falls_back_to_load_for_the_populated_field(catalog, thresholds):
    check = _check(catalog, thresholds, nameplate=None, bhp=33.0)
    assert check.shaft_hp_required == pytest.approx(33.0)
    assert check.shaft_hp_operating_load == pytest.approx(33.0)


def test_present_motor_marks_the_check_as_performed(catalog, thresholds):
    check = _check(catalog, thresholds, nameplate=100.0)
    assert check.shaft_nameplate_check_possible is True
    assert not any("could not be performed" in w for w in check.warnings)


# --- weakest link across the string --------------------------------------


def test_seal_rating_governs_when_lower_than_the_pump(catalog, thresholds):
    """§6C.4: every shaft-bearing component; the weakest link governs."""
    pump = _pump(catalog, "RC2500")  # 240 hp
    seal = catalog.seals[0].model_copy(update={"max_shaft_hp": 90.0})
    check = check_mechanical(
        pump=pump,
        seal=seal,
        stages=40,
        bhp_total=20.0,
        gas_handling_hp=0.0,
        frequency_hz=pump.frequency_ref_hz,
        thresholds=thresholds,
        motor_nameplate_hp=120.0,
    )
    assert check.shaft_hp_available == pytest.approx(90.0)
    assert check.shaft_nameplate_utilization > 1.0
    warning = next(w for w in check.warnings if "SHAFT FRACTURE RISK" in w)
    assert "protector" in warning, "the engineer must know which part is weakest"


# --- end-to-end wiring ----------------------------------------------------


def test_pipeline_reports_the_nameplate_check_on_the_base_case():
    result = DesignEngine().run(_base_case())
    assert result.candidates
    candidate = result.candidates[0]
    assert candidate.shaft_nameplate_check_performed is True
    point = candidate.design_point
    assert point.shaft_nameplate_utilization is not None
    assert point.motor_nameplate_hp is not None
    assert point.shaft_hp_operating_load is not None
    # The nameplate is the larger reference on this well, so the governing
    # utilization must exceed the load-referenced one it replaced.
    assert point.motor_nameplate_hp > point.shaft_hp_operating_load
    assert point.shaft_nameplate_utilization > point.shaft_hp_utilization or (
        point.shaft_nameplate_utilization == pytest.approx(point.shaft_hp_utilization)
    )


def test_base_case_has_no_fracture_risk_and_says_so_explicitly():
    """The demo wells are lightly loaded; the flag must be False, not absent."""
    result = DesignEngine().run(_base_case())
    assert result.has_shaft_fracture_risk is False
    assert all(not c.shaft_fracture_risk for c in result.candidates)


def test_frequency_uplift_is_included_in_the_capability():
    """Above 60 Hz a motor can deliver more than its plate value.

    The base case's winner runs a 60 hp motor at 65 Hz, so the plate value
    alone understates by 8.3% what the motor can put into the shaft. Using the
    plate value is the more dangerous of the two errors here -- it makes the
    fracture check less conservative exactly where a VSD has been used to push
    the motor harder.
    """
    from esp_engine.provenance import Tracked
    case=_base_case()
    electrical=case.constraints.electrical.model_copy(update={"frequency_min_hz":Tracked.stated(65.,"Hz"),"frequency_max_hz":Tracked.stated(65.,"Hz")})
    case=case.model_copy(update={"constraints":case.constraints.model_copy(update={"electrical":electrical})})
    result = DesignEngine().run(case)
    catalog = {m.id: m for m in load_catalog().motors}
    checked = 0
    for candidate in result.candidates:
        frequency = candidate.configuration.frequency_hz
        motor = catalog.get(candidate.configuration.motor_id)
        if motor is None or frequency <= 60.0:
            continue
        used = candidate.design_point.motor_nameplate_hp
        assert used is not None
        assert used > motor.hp, (
            f"{motor.id} at {frequency:g} Hz: capability {used} must exceed "
            f"plate {motor.hp}"
        )
        assert used == pytest.approx(motor.hp * frequency / 60.0, rel=1e-6)
        checked += 1
    assert checked, "baseline no longer has an above-60 Hz candidate to check"
