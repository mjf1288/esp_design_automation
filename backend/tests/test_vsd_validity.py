"""No VSD recovery claim without head sufficiency and fixed-equipment checks."""
import pytest

from api.app import _demo_feasible_case
from esp_engine.config import DEFAULT_CONFIG
from esp_engine.curves import scale_curve_to_frequency
from esp_engine.pipeline import DesignEngine
from esp_engine.results import EnvelopeCase, GateStatus
from esp_engine.scoring import compute_vsd_recovery
from esp_engine.trajectory import build_trajectory


@pytest.fixture(scope="module")
def run():
    return DesignEngine().run(_demo_feasible_case())


@pytest.fixture
def leader(run):
    return run.candidates[0]


def recovery(leader, cells=None, **kwargs):
    return compute_vsd_recovery(
        vsd_available=True, frequency_band_hz=(45, 65),
        base_validity=leader.validity, recovered_cells_by_frequency=cells,
        horizon_months=24, **kwargs,
    )


def trial(leader, month, **updates):
    return leader.design_point.model_copy(update={
        "month": month, "equipment_checks_performed": True, **updates,
    })


def test_near_bep_and_soft_gate_cannot_hide_head_deficit(leader):
    cell = leader.design_point.model_copy(update={
        "head_developed_ft": leader.design_point.head_required_ft - 0.01,
        "gate_status": GateStatus.FAILED_SOFT,
    })
    assert not cell.is_acceptable


@pytest.mark.parametrize("updates", [
    {"turpin_stable": False},
    {"shaft_fracture_risk": True},
    {"gate_status": GateStatus.FAILED_HARD},
    {"converged": False},
])
def test_recovery_cannot_bypass_other_failure_modes(leader, updates):
    result = recovery(leader, {60: [trial(leader, 3, **updates)]})
    assert result.assessment == "no_recovery"
    assert result.verified_points == []


def test_no_vsd_has_no_recovery_claim(leader):
    assert compute_vsd_recovery(
        vsd_available=False, frequency_band_hz=None,
        base_validity=leader.validity,
    ) is None


def test_already_valid_does_not_fabricate_recovery(leader):
    result = compute_vsd_recovery(
        vsd_available=True, frequency_band_hz=(45, 65),
        base_validity=leader.validity.model_copy(update={
            "valid_through_horizon": True, "valid_until_months": None,
        }),
    )
    assert result.assessment == "not_needed"
    assert result.extended_validity_months is None
    assert result.verified_points == result.frequency_schedule == []


def test_missing_trials_are_unverified_not_failed_experiments(leader):
    result = recovery(leader)
    assert result.assessment == "unverified"
    assert result.verified_points == []
    assert "not been verified" in result.note


def test_unchecked_equipment_cannot_claim_recovery(leader):
    result = recovery(leader, {60: [
        trial(leader, 3, equipment_checks_performed=False),
    ]})
    assert result.assessment == "no_recovery"
    assert result.frequency_schedule == []


def test_failed_month_breaks_schedule_even_if_later_month_recovers(leader):
    result = recovery(leader, {60: [
        trial(leader, 3), trial(leader, 4, converged=False), trial(leader, 5),
    ]})
    assert result.assessment == "verified"
    assert result.frequency_schedule == [(3, 60)]
    assert result.extended_validity_months == 3


def test_first_failed_month_cannot_be_skipped(leader):
    result = recovery(leader, {60: [
        trial(leader, 3, converged=False), trial(leader, 4),
    ]})
    assert result.assessment == "no_recovery"
    assert result.frequency_schedule == result.verified_points == []


def test_demo_boundary_and_month_six_explain_the_old_contradiction(leader):
    # Recomputed under October 2 gas/ranking policy; historical 84-stage
    # screenshot remains an archived run, not today's numerical oracle.
    assert leader.configuration.seal_id is not None
    assert leader.validity.valid_until_months == 3
    assert leader.validity.limiting_violation.constraint_type == "hydraulic"
    assert not leader.validity.valid_through_horizon
    base = next(c for c in leader.cells_sampled if c.month == 6 and c.envelope == EnvelopeCase.BASE)
    assert base.head_developed_ft < base.head_required_ft
    assert not base.is_acceptable
    vsd = leader.vsd_recovery
    assert vsd.assessment == "verified"
    assert vsd.extended_validity_months == 14
    assert [p.month for p in vsd.verified_points] == list(range(3, 15))
    p = next(p for p in vsd.verified_points if p.month == 6)
    assert p.frequency_hz == 60
    assert p.head_developed_ft >= p.head_required_ft
    assert p.motor_loading_frac is not None and p.motor_loading_frac <= 1
    assert all(p.head_developed_ft >= p.head_required_ft for p in vsd.verified_points)


def evaluate(leader, *, frequency=60, month=6, motor_id=None, cable_id=None, catalog_change=None):
    engine = DesignEngine()
    if catalog_change:
        kind, identity, updates = catalog_change
        engine.catalog = engine.catalog.model_copy(update={
            kind: [
                item.model_copy(update=updates) if item.id == identity else item
                for item in getattr(engine.catalog, kind)
            ],
        })
    case = _demo_feasible_case()
    config = leader.configuration.model_copy(update={
        "frequency_hz": frequency,
        "motor_id": motor_id or leader.configuration.motor_id,
        "cable_id": cable_id or leader.configuration.cable_id,
    })
    pump = next(p for p in engine.catalog.pumps if p.id == config.pump_id)
    curve = scale_curve_to_frequency(pump, frequency, DEFAULT_CONFIG.correlations.frequency_curves)
    point = next(p for p in build_trajectory(case) if p.month == month and p.envelope == EnvelopeCase.BASE)
    return engine._evaluate_cell(
        case=case, config=config, pump=pump, curve=curve, point=point,
        size_equipment=False,
    )


def test_frequency_trial_keeps_installed_equipment(leader):
    cell, sizing = evaluate(leader)
    assert cell.equipment_checks_performed
    assert cell.is_acceptable
    assert sizing["motor"].motor.id == leader.configuration.motor_id
    assert sizing["cable"].cable.id == leader.configuration.cable_id
    assert sizing["seal"].id == leader.configuration.seal_id
    assert cell.motor_self_heating and cell.cable_self_heating


@pytest.mark.parametrize("changes", [
    {"motor_id": "not-in-catalog"}, {"cable_id": "not-in-catalog"},
])
def test_missing_installed_equipment_is_not_silently_replaced(leader, changes):
    cell, _ = evaluate(leader, **changes)
    assert not cell.equipment_checks_performed
    assert not cell.is_acceptable


def test_horizon_stops_when_even_max_frequency_cannot_supply_head(leader):
    cell, _ = evaluate(leader, frequency=65, month=15)
    assert cell.head_developed_ft < cell.head_required_ft
    assert not cell.is_acceptable


def test_vsd_cannot_swap_an_underpowered_installed_motor(leader):
    cell, _ = evaluate(leader, catalog_change=(
        "motors", leader.configuration.motor_id, {"hp": 10.0},
    ))
    assert not cell.is_acceptable
    assert any(v.rigidity=="soft" and v.constraint_type=="mechanical" for v in cell.violations)


def test_vsd_cannot_swap_a_cable_whose_insulation_limit_is_exceeded(leader):
    cell, _ = evaluate(leader, catalog_change=(
        "cables", leader.configuration.cable_id, {"max_temp_f": 180.0},
    ))
    assert not cell.is_acceptable
    assert any(v.constraint_type in {"electrical","thermal"} and v.rigidity == "soft" for v in cell.violations)
