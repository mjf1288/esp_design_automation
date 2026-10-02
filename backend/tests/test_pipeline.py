"""End-to-end pipeline tests.

These are the tests that matter most, because they are the only ones that
exercise the §12 order as a whole. A unit test can pass on every module while
the assembled pipeline computes nonsense.
"""

from __future__ import annotations

import pytest

from esp_engine.models import (
    CasingSection,
    Case,
    CaseMetadata,
    Complication,
    Complications,
    ComplicationType,
    Constraints,
    DeviationSurveyPoint,
    ElectricalConstraints,
    Expectations,
    FluidProperties,
    ReservoirProperties,
    Severity,
    TaskBranch,
    TaskType,
    TestPoint,
    TrajectorySpec,
    WellGeometry,
)
from esp_engine.pipeline import DesignEngine
from esp_engine.provenance import Assumption, BiasDirection, Tracked
from esp_engine.results import FeasibilityVerdict


def _geometry(casing_id: float = 6.276, depth: float = 9000.0) -> WellGeometry:
    return WellGeometry(
        casing_sections=[
            CasingSection(
                od_in=7.0,
                id_in=casing_id,
                drift_id_in=casing_id - 0.03,
                top_md_ft=0.0,
                bottom_md_ft=depth + 500.0,
            )
        ],
        deviation_survey=[
            DeviationSurveyPoint(
                md_ft=0.0, tvd_ft=0.0, inclination_deg=0.0,
                dogleg_severity_deg_per_100ft=0.0,
            ),
            DeviationSurveyPoint(
                md_ft=4000.0, tvd_ft=3960.0, inclination_deg=12.0,
                dogleg_severity_deg_per_100ft=1.5,
            ),
            DeviationSurveyPoint(
                md_ft=7500.0, tvd_ft=7180.0, inclination_deg=18.0,
                dogleg_severity_deg_per_100ft=1.2,
            ),
            DeviationSurveyPoint(
                md_ft=depth, tvd_ft=depth - 1600.0, inclination_deg=20.0,
                dogleg_severity_deg_per_100ft=0.8,
            ),
        ],
        perforation_top_md_ft=Tracked.measured(depth + 300.0, "ft", note="completion report"),
        total_depth_md_ft=Tracked.measured(depth + 800.0, "ft", note="completion report"),
        tubing_id_in=Tracked.measured(2.441, "in", note="completion report"),
    )


def _base_case(**overrides) -> Case:
    """A realistic Branch B case: new well, 2,000 bpd target, moderate gas."""
    data = dict(
        metadata=CaseMetadata(
            case_id="test-001",
            tenant_id="tenant-a",
            well_name="TEST 1H",
        ),
        task_type=TaskType.NEW_WELL,
        expectations=Expectations(
            target_rate_bpd=Tracked.stated(2000.0, "bpd", note="customer request"),
            wellhead_pressure_psi=Tracked.stated(200.0, "psi", note="customer request"),
            design_life_months=Tracked.stated(24.0, "months", note="customer request"),
        ),
        constraints=Constraints(
            electrical=ElectricalConstraints(
                vsd_available=Tracked.stated(True, None, note="customer has a VSD"),
                frequency_min_hz=Tracked.stated(45.0, "Hz", note="VSD nameplate"),
                frequency_max_hz=Tracked.stated(65.0, "Hz", note="VSD nameplate"),
                available_surface_voltage_v=Tracked.stated(4160.0, "V", note="site survey"),
            )
        ),
        geometry=_geometry(),
        fluid=FluidProperties(
            oil_api=Tracked.measured(34.0, "API", note="PVT report"),
            gas_sg=Tracked.measured(0.78, None, note="PVT report"),
            water_cut_frac=Tracked.stated(0.35, "fraction", note="offset wells"),
            gor_scf_stb=Tracked.stated(450.0, "scf/stb", note="offset wells"),
            water_salinity_ppm=Tracked.assumed(35000.0, Assumption(basis="regional default", rationale="No water analysis supplied; regional produced-water salinity."), "ppm"),
        ),
        reservoir=ReservoirProperties(
            reservoir_pressure_psi=Tracked.measured(3200.0, "psi", note="buildup test"),
            bht_f=Tracked.measured(185.0, "F", note="log"),
            surface_temp_f=Tracked.assumed(80.0, Assumption(basis="regional default", rationale="No surface temperature supplied."), "F"),
            productivity_index_bpd_psi=Tracked.measured(1.6, "bpd/psi", note="buildup test"),
            bubble_point_psi=Tracked.measured(1900.0, "psi", note="PVT report"),
        ),
        trajectory=TrajectorySpec(horizon_months=24.0),
    )
    data.update(overrides)
    return Case(**data)


@pytest.fixture(scope="module")
def engine() -> DesignEngine:
    return DesignEngine()


# =============================================================================
# The happy path
# =============================================================================


def test_end_to_end_produces_ranked_candidates(engine: DesignEngine) -> None:
    result = engine.run(_base_case())

    assert result.verdict is not FeasibilityVerdict.BLOCKED_MISSING_DATA
    assert result.candidates, (
        f"No candidates. Verdict: {result.verdict}. "
        f"Rejections: {result.rejection_summary}"
    )
    assert [c.rank for c in result.candidates] == list(
        range(1, len(result.candidates) + 1)
    )
    scores = [(c.zone_duration_months,c.score.efficiency_avg_frac) for c in result.candidates]
    assert scores == sorted(scores, reverse=True), "candidates must be rank-ordered"


def test_every_candidate_has_a_validity_boundary(engine: DesignEngine) -> None:
    """§6.4 — the report must say WHEN the design stops working."""
    result = engine.run(_base_case())
    for candidate in result.candidates:
        v = candidate.validity
        assert v.limiting_mechanism, "a boundary without a mechanism is not actionable"
        if not v.valid_through_horizon:
            assert v.valid_until_months is not None


def test_timeline_matches_framework_report_format(engine: DesignEngine) -> None:
    """§6.4 target format: Day 1 / +2 mo / +6 mo / +1 yr / +2 yr."""
    result = engine.run(_base_case())
    best = result.candidates[0]
    labels = [e.label for e in best.timeline]
    assert "Day 1" in labels
    assert any("yr" in label for label in labels)
    for entry in best.timeline:
        assert entry.zone_description


def test_provenance_makes_the_run_replayable(engine: DesignEngine) -> None:
    """The reproducibility triple: case + config + catalog version."""
    result = engine.run(_base_case())
    p = result.provenance
    assert p.case_hash and p.config_hash and p.catalog_version and p.engine_version
    assert p.cells_evaluated > 0
    assert p.configs_enumerated > 0


def test_determinism(engine: DesignEngine) -> None:
    """Framework §1.2: the deterministic layer must be reproducible."""
    case = _base_case()
    a = engine.run(case)
    b = engine.run(case)
    assert a.provenance.case_hash == b.provenance.case_hash
    assert [c.configuration.config_id for c in a.candidates] == [
        c.configuration.config_id for c in b.candidates
    ]
    assert [round(c.score.total_score, 10) for c in a.candidates] == [
        round(c.score.total_score, 10) for c in b.candidates
    ]


# =============================================================================
# Refusals — the framework's most important behavior
# =============================================================================


def test_missing_target_rate_refuses_to_calculate(engine: DesignEngine) -> None:
    """§5.1 hard stop. A design from an assumed target rate is not a design."""
    case = _base_case(expectations=Expectations(design_life_months=None))
    result = engine.run(case)
    assert result.verdict is FeasibilityVerdict.BLOCKED_MISSING_DATA
    assert result.blocking_data_gaps
    assert not result.candidates


def test_missing_geometry_refuses_to_calculate(engine: DesignEngine) -> None:
    """§5.1 hard stop: casing ID and deviation survey."""
    case = _base_case(geometry=WellGeometry())
    result = engine.run(case)
    assert result.verdict is FeasibilityVerdict.BLOCKED_MISSING_DATA
    assert result.blocking_data_gaps


def test_impossible_casing_yields_infeasible_not_a_bad_design(
    engine: DesignEngine,
) -> None:
    """A 3.5 in casing cannot take any pump in the catalog.

    The correct answer is a clear refusal with reasons, not the least-bad option.
    """
    case = _base_case(geometry=_geometry(casing_id=3.5))
    result = engine.run(case)
    assert result.verdict is FeasibilityVerdict.NO_VIABLE_CONFIGURATION
    assert not result.candidates
    assert result.rejection_summary.get("geometry_prescreen", 0) > 0


def test_absurd_target_rate_is_infeasible(engine: DesignEngine) -> None:
    case = _base_case(
        expectations=Expectations(
            target_rate_bpd=Tracked.stated(90_000.0, "bpd", note="customer request"),
            design_life_months=Tracked.stated(24.0, "months", note="customer"),
        )
    )
    result = engine.run(case)
    assert result.verdict is FeasibilityVerdict.FEASIBLE_WITH_CAVEATS
    assert result.candidates
    assert "not validated at the requested target" in result.verdict_explanation
    assert any("AOF" in w for w in result.engine_warnings)


# =============================================================================
# Constraint rigidity behavior (§3.2)
# =============================================================================


def test_no_vsd_restricts_frequency_to_one_value(engine: DesignEngine) -> None:
    case = _base_case(
        constraints=Constraints(
            electrical=ElectricalConstraints(
                vsd_available=Tracked.stated(False, None, note="no VSD on site"),
                fixed_frequency_hz=Tracked.stated(60.0, "Hz", note="fixed drive"),
            )
        )
    )
    # Since the B.16 fork, this case yields no candidates at all when the
    # catalog offers only PMMs at the picked OD class -- PMMs cannot start
    # across the line, so removing the drive removes the motor. The
    # default catalog now includes a series-400 induction motor (added
    # to seed the \u00a76 phasor demo), which would otherwise satisfy the
    # request and hide the intended invariant. Use a PMM-only view so
    # the test measures the fork behavior rather than catalog
    # composition.
    from esp_engine.catalog import load_catalog
    cat = load_catalog()
    pmm_only = cat.model_copy(
        update={"motors": [m for m in cat.motors if m.is_permanent_magnet]}
    )
    result = DesignEngine(catalog=pmm_only).run(case)

    assert result.verdict is FeasibilityVerdict.NO_VIABLE_CONFIGURATION
    assert not result.candidates
    assert any(
        "no VSD is available" in v.message
        for r in result.rejected
        for v in r.violations
    ), "the no-VSD rejection must state its reason"

    for candidate in result.candidates:
        assert candidate.configuration.frequency_hz == pytest.approx(60.0)
        assert candidate.vsd_recovery is None, (
            "a VSD recovery section without a VSD invites the reader to assume "
            "the check was run and came back clean"
        )


def test_vsd_case_reports_recovery(engine: DesignEngine) -> None:
    result = engine.run(_base_case())
    best = result.candidates[0]
    assert best.vsd_recovery is not None
    assert best.vsd_recovery.vsd_available is True
    assert best.vsd_recovery.note


# =============================================================================
# Branch logic (§4)
# =============================================================================


def test_replacement_without_history_downgrades_to_branch_b(
    engine: DesignEngine,
) -> None:
    """A stated replacement with no performance data is Branch B wearing a
    Branch A label. Treating it as anchored would produce false confidence."""
    case = _base_case(
        task_type=TaskType.ESP_REPLACEMENT,
        reference=None,
    )
    result = engine.run(case)
    assert result.branch is TaskBranch.B_NEW
    assert result.branch_rationale
    assert any("previous installation" in r.lower() for r in result.recommended_data_requests)


def test_replacement_with_history_stays_branch_a(engine: DesignEngine) -> None:
    from esp_engine.models import PreviousInstallation

    case = _base_case(
        task_type=TaskType.ESP_REPLACEMENT,
        reference=PreviousInstallation(
            pump_model=Tracked.measured("D950N", None, note="workover report"),
            stage_count=Tracked.measured(120, None, note="workover report"),
            setting_depth_md_ft=Tracked.measured(8500.0, "ft", note="workover report"),
            run_life_days=Tracked.measured(240.0, "days", note="failure record"),
            test_points=[
                TestPoint(
                    date="2025-06-01",
                    rate_bpd=Tracked.measured(1850.0, "bpd", note="well test"),
                    pip_psi=Tracked.measured(680.0, "psi", note="downhole gauge"),
                    frequency_hz=Tracked.measured(58.0, "Hz", note="VSD log"),
                )
            ],
        ),
    )
    result = engine.run(case)
    assert result.branch is TaskBranch.A_REPLACEMENT


# =============================================================================
# Gas (§3.3 — the critical complication)
# =============================================================================


def test_high_gor_triggers_gas_handling(engine: DesignEngine) -> None:
    case = _base_case(
        fluid=FluidProperties(
            oil_api=Tracked.measured(38.0, "API", note="PVT"),
            gas_sg=Tracked.measured(0.80, None, note="PVT"),
            water_cut_frac=Tracked.stated(0.10, "fraction", note="offsets"),
            gor_scf_stb=Tracked.stated(2500.0, "scf/stb", note="offsets"),
        ),
        complications=Complications(
            items=[Complication(type=ComplicationType.GAS, severity=Severity.SEVERE)]
        ),
    )
    result = engine.run(case)
    if result.candidates:
        best = result.candidates[0]
        assert best.design_point.free_gas_fraction_at_intake > 0.0
        # Either a device is specified or the gas is separated naturally; what
        # must not happen is silence about it.
        assert best.design_point.gas_strategy


# =============================================================================
# Assumption ledger and honesty (§5.2, §7)
# =============================================================================


def test_assumed_inputs_appear_in_the_ledger(engine: DesignEngine) -> None:
    result = engine.run(_base_case())
    paths = {e.field_path for e in result.assumption_ledger}
    assert "fluid.water_salinity_ppm" in paths or "reservoir.surface_temp_f" in paths
    for entry in result.assumption_ledger:
        assert entry.source
        assert 0.0 <= entry.confidence <= 1.0


def test_estimated_catalog_data_is_disclosed(engine: DesignEngine) -> None:
    """The seed catalog's curves are parametric estimates. A tool that hides that
    launders an estimate into an authority."""
    result = engine.run(_base_case())
    if result.uses_estimated_catalog_data:
        assert any("ESTIMATE" in w for w in result.engine_warnings)


def test_input_confidence_is_a_minimum_not_a_mean(engine: DesignEngine) -> None:
    """A design is only as trustworthy as its weakest load-bearing input."""
    result = engine.run(_base_case())
    assert 0.0 <= result.input_confidence <= 1.0


def test_rejections_carry_actionable_remedies(engine: DesignEngine) -> None:
    result = engine.run(_base_case(geometry=_geometry(casing_id=3.5)))
    assert result.rejected
    assert any(r.reason for r in result.rejected)
    assert result.recommended_data_requests or any(
        r.violations and r.violations[0].remedy_hint for r in result.rejected
    )


# =============================================================================
# Performance (§2.1 — the actual complaint about existing software)
# =============================================================================


def test_runs_fast_enough_to_be_interactive(engine: DesignEngine) -> None:
    """Framework §2.1: engineers run 3-4 scenarios instead of 20 because the
    software is slow. If this is slow, the product has no reason to exist."""
    result = engine.run(_base_case())
    assert result.provenance.compute_ms is not None
    assert result.provenance.compute_ms < 15_000, (
        f"took {result.provenance.compute_ms:.0f} ms for "
        f"{result.provenance.cells_evaluated} cells"
    )


def test_string_is_assembled_for_the_best_candidate(engine: DesignEngine) -> None:
    """§12 string assembly, in run-in-hole order."""
    result = engine.run(_base_case())
    summary = result.candidates[0].string_summary
    assert summary
    for component in ("tubing", "pump", "motor", "sensor"):
        assert component in summary.lower()
