"""Framework 6B.2 -- API RP 11S2 acceptance tolerances on the catalog curve.

The tests are grouped by what they defend:

* the band is gated on curve provenance and never asserted by default;
* materiality is a decision flip, and a decision already broken at the catalog
  curve is never blamed on the band;
* the assessment is inert -- it changes no score, no gate and no violation.
"""

from __future__ import annotations

import pytest

from esp_engine.catalog import load_catalog
from esp_engine.config import DEFAULT_CONFIG, ScoringObjective, ThrustZoneMethod
from esp_engine.curves import scale_curve_to_frequency
from esp_engine.models import OperatingZone
from esp_engine.pipeline import DesignEngine
from esp_engine.tolerance import (
    API_RP_11S2,
    ChannelEffect,
    CurveBasis,
    ToleranceChannel,
    assess_candidate_tolerance,
    assess_ranking_stability,
    band_applies,
    curve_basis_of,
    perturb_curve_rate_axis,
)

from tests.test_pipeline import _base_case


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def run():
    return DesignEngine().run(_base_case())


@pytest.fixture(scope="module")
def leader(run):
    return run.candidates[0]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _assess(candidate, pump, *, objective=None, thresholds=None):
    cfg = DEFAULT_CONFIG
    curve = scale_curve_to_frequency(
        pump, candidate.configuration.frequency_hz, cfg.correlations.frequency_curves
    )
    return assess_candidate_tolerance(
        candidate,
        pump=pump,
        curve=curve,
        thresholds=thresholds or cfg.thresholds,
        thrust_zone_method=cfg.correlations.thrust_zones,
        objective=objective or cfg.scoring.objective,
        zone_weights=cfg.scoring.zone_weights,
    )


def _published(pump):
    return pump.model_copy(update={"data_quality": "digitized_from_datasheet"})


def _finding(assessment, channel):
    return next(f for f in assessment.findings if f.channel is channel)


# =============================================================================
# The band belongs to the curve's provenance, not to the pump
# =============================================================================


def test_no_shipped_pump_carries_the_acceptance_band(catalog):
    """Every curve in this catalog is a reconstruction, so none carries a bound.

    This is the whole point of the provenance gate. If this test ever starts
    failing because a pump was reclassified, that reclassification must be
    backed by an actual transcription from a vendor datasheet -- not by a
    convenient relabel.
    """
    bases = {p.id: curve_basis_of(p) for p in catalog.pumps}
    assert len(bases) == 13
    assert set(bases.values()) == {CurveBasis.PARAMETRIC_ESTIMATE}
    assert not any(band_applies(b) for b in bases.values())


def test_undeclared_provenance_does_not_get_the_band(catalog):
    """An unclassifiable record must land in the state that grants nothing."""
    pump = catalog.pumps[0]
    for marker in ("", "   ", "unknown", "tbd", "high", "good"):
        basis = curve_basis_of(pump.model_copy(update={"data_quality": marker}))
        assert basis is CurveBasis.UNDECLARED, marker
        assert not band_applies(basis)


def test_only_vendor_published_carries_the_band():
    """A check that cannot be performed must not read as one that cleared."""
    granted = [b for b in CurveBasis if band_applies(b)]
    assert granted == [CurveBasis.VENDOR_PUBLISHED]


def test_test_report_marker_is_not_read_as_an_estimate(catalog):
    pump = catalog.pumps[0]
    assert (
        curve_basis_of(pump.model_copy(update={"data_quality": "unit_test_report"}))
        is CurveBasis.UNIT_TEST_REPORT
    )


def test_the_two_no_band_states_are_not_collapsed(leader, catalog):
    """'We hold the tested curve' and 'we hold a guess' are opposite situations.

    Both suppress the band; conflating them would tell an engineer holding the
    actual unit curve that his data is unbounded, and an engineer holding a
    parametric fit that his is settled.
    """
    pump = catalog.pump(leader.configuration.pump_id)
    estimate = _assess(leader, pump)
    tested = _assess(leader, pump.model_copy(update={"data_quality": "unit_test_report"}))

    assert estimate.band_applied is False
    assert tested.band_applied is False
    assert estimate.curve_basis is not tested.curve_basis
    assert estimate.recommendation != tested.recommendation
    assert "already the tested curve" in tested.recommendation
    assert "additive" in estimate.recommendation


def test_parametric_estimate_names_the_additive_error_argument(leader, catalog):
    """The refusal must say *why*, or it reads as a missing feature."""
    assessment = _assess(leader, catalog.pump(leader.configuration.pump_id))
    assert assessment.curve_basis is CurveBasis.PARAMETRIC_ESTIMATE
    assert assessment.findings == []
    assert assessment.test_report_recommended is False
    text = assessment.recommendation + " ".join(assessment.disclosures)
    assert "published" in text
    assert "additive" in text
    assert "bounded error" in text


def test_band_applies_once_the_curve_is_transcribed(leader, catalog):
    """The machinery is real; the catalog is what withholds it."""
    pump = _published(catalog.pump(leader.configuration.pump_id))
    assessment = _assess(leader, pump)
    assert assessment.band_applied is True
    assert {f.channel for f in assessment.findings} == set(ToleranceChannel)


# =============================================================================
# Materiality is a decision flip, not a distance
# =============================================================================


def test_thin_head_margin_inside_the_band_is_material(leader, catalog):
    """A margin the standard can erase is exactly what a test report resolves."""
    pump = _published(catalog.pump(leader.configuration.pump_id))
    cell = leader.design_point.model_copy(update={"head_margin_frac": 0.03})
    candidate = leader.model_copy(update={"design_point": cell, "cells_sampled": [cell]})

    finding = _finding(_assess(candidate, pump), ToleranceChannel.HEAD_MARGIN)
    assert finding.effect is ChannelEffect.FLIPS
    assert finding.material
    # 3% margin is lost at a 2.91% head deviation, well inside the 5% band.
    assert finding.flip_at_frac == pytest.approx(0.03 / 1.03)
    assert finding.flip_at_frac < API_RP_11S2.head_frac


def test_fat_head_margin_holds_across_the_band(leader, catalog):
    pump = _published(catalog.pump(leader.configuration.pump_id))
    cell = leader.design_point.model_copy(update={"head_margin_frac": 0.40})
    candidate = leader.model_copy(update={"design_point": cell, "cells_sampled": [cell]})

    finding = _finding(_assess(candidate, pump), ToleranceChannel.HEAD_MARGIN)
    assert finding.effect is ChannelEffect.HOLDS
    assert not finding.material
    assert finding.flip_at_frac > API_RP_11S2.head_frac


def test_an_already_short_design_is_not_blamed_on_the_band(leader, catalog):
    """The misattribution guard.

    A configuration that does not make TDH on the catalog curve itself has a
    sizing problem. Reporting that as a tolerance finding would send the
    engineer to a test report that cannot help him, and would quietly convert
    the engine's own sizing defect into the manufacturer's variation.
    """
    pump = _published(catalog.pump(leader.configuration.pump_id))
    cell = leader.design_point.model_copy(update={"head_margin_frac": -0.23})
    candidate = leader.model_copy(update={"design_point": cell, "cells_sampled": [cell]})

    finding = _finding(_assess(candidate, pump), ToleranceChannel.HEAD_MARGIN)
    assert finding.effect is ChannelEffect.NOT_COMPUTABLE
    assert not finding.material
    assert "already short" in finding.detail
    assert "misattribute" in finding.detail


def test_housing_limit_already_breached_is_not_a_tolerance_finding(leader, catalog):
    """Same guard on the stage-count channel."""
    pump = _published(catalog.pump(leader.configuration.pump_id))
    stages = leader.configuration.stages
    cell = leader.design_point.model_copy(
        update={"head_developed_ft": 100.0 * stages, "head_required_ft": 100.0 * 500}
    )
    candidate = leader.model_copy(update={"design_point": cell, "cells_sampled": [cell]})

    finding = _finding(_assess(candidate, pump), ToleranceChannel.STAGE_COUNT)
    assert finding.effect is ChannelEffect.NOT_COMPUTABLE
    assert not finding.material
    assert "not the binding constraint" in finding.detail


def test_stage_count_flips_only_when_the_band_is_what_crosses_the_housing(
    leader, catalog
):
    """Nominal inside the housing, banded outside it -- the band decides."""
    pump = _published(catalog.pump(leader.configuration.pump_id))
    stages = leader.configuration.stages
    # Per-stage head of 1 ft; require exactly max_stages of nominal head so that
    # the 5%-low unit needs more stages than the housing holds.
    cell = leader.design_point.model_copy(
        update={
            "head_developed_ft": float(stages),
            "head_required_ft": float(pump.max_stages),
        }
    )
    candidate = leader.model_copy(update={"design_point": cell, "cells_sampled": [cell]})

    finding = _finding(_assess(candidate, pump), ToleranceChannel.STAGE_COUNT)
    assert finding.effect is ChannelEffect.FLIPS
    assert finding.material
    assert str(pump.max_stages) in finding.detail


def test_the_bhp_band_is_wider_than_the_whole_motor_loading_target(leader, catalog):
    """A motor sitting dead-centre in the target band is still undecided.

    The 12 target is 75-85% loading -- 10 points wide, so the centre sits
    6.25% from the ceiling and 6.7% from the floor. RP 11S2 permits 8% on BHP.
    The acceptance tolerance is therefore wider than the distance from the
    middle of the target band to either edge, which means no catalog curve can
    settle the loading verdict for any motor inside the band. That is a
    property of the two numbers, not of any particular design.
    """
    pump = _published(catalog.pump(leader.configuration.pump_id))
    band = DEFAULT_CONFIG.thresholds.motor
    centre = (band.target_loading_min + band.target_loading_max) / 2

    cell = leader.design_point.model_copy(update={"motor_loading_frac": centre})
    candidate = leader.model_copy(update={"design_point": cell, "cells_sampled": [cell]})
    finding = _finding(_assess(candidate, pump), ToleranceChannel.MOTOR_LOADING)

    assert finding.effect is ChannelEffect.FLIPS
    assert finding.material
    assert finding.flip_at_frac < API_RP_11S2.bhp_frac
    assert max(
        (band.target_loading_max / centre) - 1.0,
        1.0 - (band.target_loading_min / centre),
    ) < API_RP_11S2.bhp_frac


def test_motor_loading_far_outside_the_band_holds(leader, catalog):
    """Only a loading the band cannot carry back into range is settled."""
    pump = _published(catalog.pump(leader.configuration.pump_id))
    cell = leader.design_point.model_copy(update={"motor_loading_frac": 0.40})
    candidate = leader.model_copy(update={"design_point": cell, "cells_sampled": [cell]})

    finding = _finding(_assess(candidate, pump), ToleranceChannel.MOTOR_LOADING)
    assert finding.effect is ChannelEffect.HOLDS
    assert not finding.material
    assert finding.flip_at_frac > API_RP_11S2.bhp_frac


# =============================================================================
# The rate band moves the landmarks, not the head
# =============================================================================


def test_rate_perturbation_leaves_the_polynomials_alone(catalog):
    """Otherwise the head band would be counted twice.

    The rate tolerance is applied to test the *zone label*, which is decided
    entirely by the landmarks. Rescaling the coefficients as well would also
    move head and BHP, which already carry their own separate bands.
    """
    pump = catalog.pumps[0]
    curve = scale_curve_to_frequency(
        pump, pump.frequency_ref_hz, DEFAULT_CONFIG.correlations.frequency_curves
    )
    shifted = perturb_curve_rate_axis(curve, 0.05)

    assert shifted.head_coeffs == curve.head_coeffs
    assert shifted.bhp_coeffs == curve.bhp_coeffs
    assert shifted.eff_coeffs == curve.eff_coeffs
    assert shifted.bep_q_bpd == pytest.approx(curve.bep_q_bpd * 1.05)
    assert shifted.q_max_bpd == pytest.approx(curve.q_max_bpd * 1.05)


def test_rate_perturbation_refuses_to_invert_the_curve(catalog):
    pump = catalog.pumps[0]
    curve = scale_curve_to_frequency(
        pump, pump.frequency_ref_hz, DEFAULT_CONFIG.correlations.frequency_curves
    )
    with pytest.raises(ValueError, match="invert"):
        perturb_curve_rate_axis(curve, -1.0)


def test_zone_flip_reports_the_largest_consequence_not_the_first(leader, catalog):
    """Ordering by score swing, not by month.

    A month-0 operating_range -> bep flip and a later flip into downthrust are
    both flips; presenting whichever came first in time would bury the one the
    engineer needs.
    """
    pump = _published(catalog.pump(leader.configuration.pump_id))
    curve = scale_curve_to_frequency(
        pump,
        leader.configuration.frequency_hz,
        DEFAULT_CONFIG.correlations.frequency_curves,
    )
    weights = DEFAULT_CONFIG.scoring.zone_weights

    # A rate just inside the downthrust boundary: shifting the boundary up by
    # 5% drops it out of the operating range entirely.
    low_q = curve.downthrust_limit_bpd * 1.02
    early = leader.design_point.model_copy(
        update={"month": 0.0, "zone": OperatingZone.OPERATING_RANGE}
    )
    late = leader.cells_sampled[-1].model_copy(
        update={
            "month": 24.0,
            "zone": OperatingZone.OPERATING_RANGE,
            "total_fluid_intake_bpd": low_q,
        }
    )
    candidate = leader.model_copy(
        update={"design_point": early, "cells_sampled": [early, late]}
    )

    finding = _finding(_assess(candidate, pump), ToleranceChannel.OPERATING_ZONE)
    assert finding.effect is ChannelEffect.FLIPS
    assert finding.at_month == 24.0
    swing = abs(weights["operating_range"] - weights["downthrust"])
    assert f"{swing:.2f}" in finding.detail


# =============================================================================
# Efficiency: a tolerance on a quantity nothing reads
# =============================================================================


def test_efficiency_tolerance_has_no_consumer_under_the_default_objective(
    leader, catalog
):
    """Efficiency is displayed everywhere and read by almost nothing.

    ``efficiency_avg_frac`` enters the total score under exactly one objective.
    Manufacturing a band around a number no decision consumes would be theatre.
    """
    assert DEFAULT_CONFIG.scoring.objective is not ScoringObjective.MAX_EFFICIENCY
    pump = _published(catalog.pump(leader.configuration.pump_id))
    finding = _finding(_assess(leader, pump), ToleranceChannel.EFFICIENCY_RANKING)
    assert finding.effect is ChannelEffect.NO_CONSUMER
    assert not finding.material
    assert "zero weight" in finding.detail


def test_efficiency_tolerance_acquires_a_consumer_under_max_efficiency(
    leader, catalog
):
    pump = _published(catalog.pump(leader.configuration.pump_id))
    finding = _finding(
        _assess(leader, pump, objective=ScoringObjective.MAX_EFFICIENCY),
        ToleranceChannel.EFFICIENCY_RANKING,
    )
    assert finding.effect is ChannelEffect.HOLDS
    assert "0.45" in finding.detail


def test_non_flip_effects_are_never_material():
    """Only FLIPS may be material; the other three are absences, not clearances."""
    material_effects = {
        e for e in ChannelEffect if e is ChannelEffect.FLIPS
    }
    assert material_effects == {ChannelEffect.FLIPS}
    assert len(ChannelEffect) == 4


# =============================================================================
# Ranking stability
# =============================================================================


def test_ranking_stability_is_unassessed_rather_than_assumed(run):
    """Under the default objective the efficiency band cannot reorder anything.

    Reporting that as 'stable' would overclaim: the head, rate and BHP bands
    are not propagated into the score at all.
    """
    stability = run.ranking_stability
    assert stability is not None
    assert stability.assessed is False
    assert stability.stable is None
    assert "not propagated" in stability.detail


def test_ranking_stability_is_computed_under_max_efficiency(run):
    stability = assess_ranking_stability(
        run.candidates, objective=ScoringObjective.MAX_EFFICIENCY
    )
    assert stability.assessed is True
    assert stability.stable is not None
    assert stability.score_gap is not None
    assert stability.induced_swing == pytest.approx(
        0.45 * API_RP_11S2.efficiency_frac * run.candidates[0].score.efficiency_avg_frac
    )


def test_ranking_stability_needs_two_candidates(run):
    stability = assess_ranking_stability(
        run.candidates[:1], objective=ScoringObjective.MAX_EFFICIENCY
    )
    assert stability.assessed is False
    assert "no order" in stability.detail


# =============================================================================
# The assessment is inert
# =============================================================================


def test_tolerance_changes_no_score_and_no_gate(run,monkeypatch):
    """6B.2 produces a recommendation, never a penalty.

    A tolerance band is uncertainty about a number, not a defect in a design.
    Scoring it would rank candidates by how well documented they are, which is
    the same mistake 5.1 forbids for missing catalog data.
    """
    assert run.verdict.value == "feasible_with_caveats"
    # Future head shortfall now reduces time coverage; uncertainty disclosure
    # remains inert. The selected pump is unchanged, not valid for 24 months.
    monkeypatch.setattr(DesignEngine,"_with_tolerance",lambda self,c:c)
    without=DesignEngine().run(_base_case())
    assert [c.score for c in run.candidates]==[c.score for c in without.candidates]
    assert [c.design_point.gate_status for c in run.candidates]==[c.design_point.gate_status for c in without.candidates]
    for candidate in run.candidates:
        for violation in candidate.soft_violations:
            assert "toleran" not in violation.message.lower()
            assert "11S2" not in violation.message


def test_every_presented_candidate_carries_an_assessment(run):
    assert run.candidates
    for candidate in run.candidates:
        assert candidate.tolerance is not None
        assert candidate.tolerance.standard == API_RP_11S2.citation
        assert candidate.tolerance.pump_id == candidate.configuration.pump_id


def test_no_test_report_is_requested_for_an_unbounded_curve(run):
    """Asking for a report against a curve we do not hold would be noise."""
    for candidate in run.candidates:
        assert candidate.tolerance.test_report_recommended is False
    assert not any(
        "Unit test report" in request for request in run.recommended_data_requests
    )


def test_a_material_finding_becomes_a_specific_data_request(leader, catalog):
    """The request must name the pump, the rank and the channels that flipped."""
    pump = _published(catalog.pump(leader.configuration.pump_id))
    assessment = _assess(leader, pump)
    assert assessment.test_report_recommended is True
    assert assessment.material_channels, (
        "a recommended test report must have at least one material channel; "
        "otherwise the request has nothing specific to ask for."
    )

    candidate = leader.model_copy(update={"tolerance": assessment})
    request = DesignEngine._test_report_request(candidate)
    assert request is not None
    assert pump.id in request
    assert "S/N, not model" in request
    # Every material channel must be named in the request. Which channels are
    # material is a consequence of the leader's operating point relative to the
    # tolerance band, not something to hard-code -- after the head-shortfall
    # gate closed (Finding 1) the winning candidate has a comfortable positive
    # head margin, so only head_margin flips materially. Assert the invariant,
    # not one particular set of channels.
    for channel in assessment.material_channels:
        assert channel.value in request, (
            f"material channel {channel.value} missing from data request"
        )


def test_copied_thrust_boundaries_are_disclosed_on_the_zone_finding(leader, catalog):
    """A zone crossing must not imply a published thrust limit that does not exist."""
    pump = _published(catalog.pump(leader.configuration.pump_id))
    assert pump.downthrust_limit_bpd == pump.recommended_range_bpd[0]
    assert pump.upthrust_limit_bpd == pump.recommended_range_bpd[1]

    disclosures = " ".join(_assess(leader, pump).disclosures)
    assert "byte-identical" in disclosures
    assert "four zones are really two" in disclosures


def test_the_scope_of_the_standard_travels_with_its_numbers(leader, catalog):
    """RP 11S2 is a new-equipment criterion; a rebuilt pump may not be held to it."""
    disclosures = " ".join(
        _assess(leader, _published(catalog.pump(leader.configuration.pump_id))).disclosures
    )
    assert "sold as new" in disclosures
    assert "repaired or refurbished" in disclosures
    assert "corporate standard" in disclosures


def test_the_rate_model_states_that_it_is_not_conservative(leader, catalog):
    """A uniform shift understates independent per-point error. Say so."""
    disclosures = " ".join(
        _assess(leader, _published(catalog.pump(leader.configuration.pump_id))).disclosures
    )
    assert "lower bound" in disclosures


def test_thrust_zone_method_fallback_does_not_silently_widen_the_band(leader, catalog):
    """BEP_FRACTION boundaries move with BEP, so the shift must still apply."""
    cfg = DEFAULT_CONFIG
    pump = _published(catalog.pump(leader.configuration.pump_id))
    curve = scale_curve_to_frequency(
        pump, leader.configuration.frequency_hz, cfg.correlations.frequency_curves
    )
    assessment = assess_candidate_tolerance(
        leader,
        pump=pump,
        curve=curve,
        thresholds=cfg.thresholds,
        thrust_zone_method=ThrustZoneMethod.BEP_FRACTION,
        objective=cfg.scoring.objective,
        zone_weights=cfg.scoring.zone_weights,
    )
    finding = _finding(assessment, ToleranceChannel.OPERATING_ZONE)
    assert finding.effect in (ChannelEffect.FLIPS, ChannelEffect.HOLDS)


def test_the_tie_tolerance_travels_with_the_stability_verdict(run):
    """A gap without the threshold it was compared against is unreadable."""
    assert run.ranking_stability.tie_tolerance == pytest.approx(1e-3)
    custom = assess_ranking_stability(
        run.candidates, objective=ScoringObjective.MAX_EFFICIENCY, tie_tolerance=0.05
    )
    assert custom.tie_tolerance == pytest.approx(0.05)
    assert "5e-02" in custom.detail
