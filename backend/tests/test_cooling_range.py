"""Framework v0.6 §6D.2: the cooling velocity range, bounded on both sides.

The tests that matter here are the ones that stop the range from collapsing back
into a single number. §6D.2's central claim is that the two bounds are different
physics with OPPOSITE remedies -- below, overheating, fix by raising velocity;
above, erosion and lost gas separation, fix by lowering it. A single "velocity
out of range" message would send an engineer to fit a shroud for a problem a
shroud makes worse.

The three refusals under test:
  - the viscous floor is a published BAND, so no midpoint is manufactured
  - no numeric upper bound is invented where the framework publishes none
  - "viscous" is not inferred from a viscosity number
"""

from __future__ import annotations

import pytest

from esp_engine.config import EngineConfig, MotorThresholds
from esp_engine.cooling import (
    CoolingFloorBasis,
    CoolingVerdict,
    assess_cooling,
)
from esp_engine.models import Complication, ComplicationType, Severity
from esp_engine.pipeline import DesignEngine

from tests.test_pipeline import _base_case


@pytest.fixture
def thresholds() -> MotorThresholds:
    return EngineConfig().thresholds.motor


def _viscous_case():
    base = _base_case()
    items = list(base.complications.items) + [
        Complication(type=ComplicationType.VISCOUS_OIL, severity=Severity.SEVERE)
    ]
    return base.model_copy(
        update={"complications": base.complications.model_copy(update={"items": items})}
    )


# --- the standard floor is unchanged -------------------------------------


def test_standard_floor_is_one_ft_per_sec(thresholds):
    assert thresholds.min_cooling_velocity_ft_s == 1.0
    result = assess_cooling(velocity_ft_s=1.5, thresholds=thresholds)
    assert result.verdict is CoolingVerdict.ADEQUATE
    assert result.adequate
    assert result.floor_basis is CoolingFloorBasis.STANDARD
    assert result.floor_applied_ft_s == 1.0


def test_below_standard_floor_warns_about_overheating(thresholds):
    result = assess_cooling(velocity_ft_s=0.7, thresholds=thresholds)
    assert result.verdict is CoolingVerdict.BELOW_MINIMUM
    assert not result.adequate
    warning = next(w for w in result.warnings if "BELOW MINIMUM" in w)
    assert "OVERHEATING" in warning
    assert "shroud" in warning
    # Rate is explicitly not a lever in §6D.2 -- it is the customer's.
    assert "not available as a lever" in warning


# --- the viscous floor is a band, not a value ----------------------------


def test_viscous_declaration_raises_the_floor(thresholds):
    """1.5 ft/s clears the standard floor and fails the viscous one."""
    standard = assess_cooling(velocity_ft_s=1.5, thresholds=thresholds)
    viscous = assess_cooling(
        velocity_ft_s=1.5, thresholds=thresholds, viscous_oil_declared=True
    )
    assert standard.adequate
    assert not viscous.adequate
    assert viscous.floor_basis is CoolingFloorBasis.VISCOUS_DECLARED


def test_viscous_floor_requires_the_conservative_end_of_the_band(thresholds):
    """For a floor, higher is safer; the band's low end is not guaranteed."""
    result = assess_cooling(
        velocity_ft_s=5.0, thresholds=thresholds, viscous_oil_declared=True
    )
    assert result.floor_applied_ft_s == pytest.approx(2.8)
    assert result.viscous_band_ft_s == (2.6, 2.8)


def test_inside_the_band_is_indeterminate_not_pass_or_fail(thresholds):
    """The published range does not resolve a well landing inside it.

    This is the test that stops someone "simplifying" the band to a 2.7 ft/s
    midpoint. A midpoint would silently convert an unresolved question into a
    confident verdict in one direction or the other.
    """
    result = assess_cooling(
        velocity_ft_s=2.7, thresholds=thresholds, viscous_oil_declared=True
    )
    assert result.verdict is CoolingVerdict.WITHIN_VISCOUS_BAND
    assert not result.adequate, "unresolved must not read as cleared"
    warning = next(w for w in result.warnings if "INDETERMINATE" in w)
    assert "no midpoint has been substituted" in warning.lower()
    assert "2.6-2.8" in warning
    assert result.data_requests


def test_band_edges_are_classified_consistently(thresholds):
    below = assess_cooling(
        velocity_ft_s=2.59, thresholds=thresholds, viscous_oil_declared=True
    )
    inside = assess_cooling(
        velocity_ft_s=2.6, thresholds=thresholds, viscous_oil_declared=True
    )
    above = assess_cooling(
        velocity_ft_s=2.8, thresholds=thresholds, viscous_oil_declared=True
    )
    assert below.verdict is CoolingVerdict.BELOW_MINIMUM
    assert inside.verdict is CoolingVerdict.WITHIN_VISCOUS_BAND
    assert above.verdict is CoolingVerdict.ADEQUATE


# --- viscous is declared, never inferred from a number -------------------


def test_viscosity_number_alone_does_not_raise_the_floor(thresholds):
    """The cp threshold is vendor-specific and uncataloged (framework §13)."""
    result = assess_cooling(
        velocity_ft_s=1.5, thresholds=thresholds, oil_viscosity_cp=250.0
    )
    assert result.floor_basis is CoolingFloorBasis.STANDARD
    assert result.adequate
    warning = next(w for w in result.warnings if "250" in w)
    assert "vendor-specific" in warning
    assert "does not classify the well from the number alone" in warning
    assert result.data_requests, "the missing threshold is a data request"


def test_declared_viscous_with_a_number_does_not_double_warn(thresholds):
    result = assess_cooling(
        velocity_ft_s=5.0,
        thresholds=thresholds,
        viscous_oil_declared=True,
        oil_viscosity_cp=250.0,
    )
    assert not any("does not classify" in w for w in result.warnings)


# --- the upper bound: distinct mechanisms, opposite remedy ---------------


def test_no_numeric_upper_bound_is_cataloged_by_default(thresholds):
    assert thresholds.max_cooling_velocity_ft_s is None


def test_high_velocity_with_no_active_mechanism_is_adequate(thresholds):
    """Velocity alone is not a problem; the mechanisms are what bound it."""
    result = assess_cooling(velocity_ft_s=25.0, thresholds=thresholds)
    assert result.verdict is CoolingVerdict.ADEQUATE
    assert not result.erosion_mechanism_active
    assert not result.separation_mechanism_active


def test_active_mechanism_with_no_bound_reports_unchecked_not_passed(thresholds):
    result = assess_cooling(
        velocity_ft_s=12.0, thresholds=thresholds, solids_present=True
    )
    assert result.verdict is CoolingVerdict.UPPER_BOUND_NOT_CATALOGED
    # Still clears the MINIMUM -- an uncataloged upper bound must not start
    # rejecting configurations §6D.2 says to warn about.
    assert result.adequate
    warning = next(w for w in result.warnings if "UNBOUNDED" in w)
    assert "UNCHECKED rather than satisfied" in warning
    assert "erosion with solids present" in warning
    assert result.data_requests


def test_both_mechanisms_are_named_when_both_are_active(thresholds):
    result = assess_cooling(
        velocity_ft_s=12.0,
        thresholds=thresholds,
        solids_present=True,
        gas_separated_to_annulus=True,
    )
    warning = next(w for w in result.warnings if "UNBOUNDED" in w)
    assert "erosion with solids present" in warning
    assert "gas separation into the annulus" in warning
    assert result.erosion_mechanism_active
    assert result.separation_mechanism_active


def test_upper_bound_warning_gives_the_opposite_remedy(thresholds):
    """The whole point of §6D.2's "different warnings" instruction."""
    configured = thresholds.model_copy(update={"max_cooling_velocity_ft_s": 8.0})
    result = assess_cooling(
        velocity_ft_s=10.0, thresholds=configured, solids_present=True
    )
    assert result.verdict is CoolingVerdict.ABOVE_UPPER_BOUND
    warning = next(w for w in result.warnings if "ABOVE UPPER BOUND" in w)
    assert "NOT a cooling problem" in warning
    assert "higher velocity always cools better" in warning
    assert "Do not fit a shroud" in warning
    assert "widens the annulus" in warning


def test_lower_and_upper_warnings_share_no_remedy_language(thresholds):
    """A single merged message would be a factual error about the physics."""
    configured = thresholds.model_copy(update={"max_cooling_velocity_ft_s": 8.0})
    low = assess_cooling(velocity_ft_s=0.5, thresholds=configured)
    high = assess_cooling(
        velocity_ft_s=10.0, thresholds=configured, solids_present=True
    )
    low_text = " ".join(low.warnings)
    high_text = " ".join(high.warnings)
    assert "OVERHEATING" in low_text and "OVERHEATING" not in high_text
    assert "erosion" in high_text.lower() and "erosion" not in low_text.lower()
    # The low-velocity fix is a shroud; the high-velocity text must forbid it.
    assert "Add a motor shroud" in low_text or "fitting or verifying a shroud" in low_text
    assert "Do not fit a shroud" in high_text


def test_upper_bound_never_downgrades_the_minimum(thresholds):
    """A velocity below the floor is an overheating failure, full stop."""
    configured = thresholds.model_copy(update={"max_cooling_velocity_ft_s": 8.0})
    result = assess_cooling(
        velocity_ft_s=0.4,
        thresholds=configured,
        solids_present=True,
        gas_separated_to_annulus=True,
    )
    assert result.verdict is CoolingVerdict.BELOW_MINIMUM
    assert not result.adequate


def test_negative_velocity_is_rejected(thresholds):
    with pytest.raises(ValueError, match="nonnegative"):
        assess_cooling(velocity_ft_s=-1.0, thresholds=thresholds)


# --- end-to-end -----------------------------------------------------------


def test_base_case_still_feasible_on_the_standard_floor():
    result = DesignEngine().run(_base_case())
    assert result.candidates
    cooling = result.candidates[0].design_point.cooling_velocity_ft_s
    assert cooling is not None and cooling >= 1.0


def test_declaring_viscous_oil_eliminates_the_base_case_on_cooling():
    """The base case runs at ~1.1 ft/s: clears 1.0, nowhere near 2.8.

    This is a genuine engineering consequence rather than a regression -- the
    1 ft/s rule is inadequate for viscous fluids and this well relies on it.
    """
    result = DesignEngine().run(_viscous_case())
    assert result.candidates
    assert all(not c.design_point.is_acceptable for c in result.candidates)
    reasons = [v.message for c in result.candidates for v in c.soft_violations]
    cooling_reasons = [r for r in reasons if "cooling" in r.lower()]
    assert cooling_reasons, "cooling must remain a disclosed warning"
    assert any("2.8 ft/s viscous-oil floor" in r for r in cooling_reasons)


def test_the_binding_constraint_is_reported_not_the_first_one():
    """Regression: `reason` used to be violations[0] regardless of rigidity.

    On the viscous case that surfaced a soft 40% motor-loading note as the
    reason while remedy_hints silently carried the hard cooling failure's
    "add a shroud" -- telling the engineer to fit a shroud for a loading
    problem, and never naming the constraint that actually eliminated the
    configuration.
    """
    result = DesignEngine().run(_viscous_case())
    order = {"absolute": 0, "hard": 1, "soft": 2}
    checked = 0
    for rejection in result.rejected:
        if not rejection.violations or len(rejection.violations) < 2:
            continue
        ranks = [order.get(v.rigidity, 3) for v in rejection.violations]
        if min(ranks) == ranks[0]:
            continue  # first violation already was the binding one
        assert rejection.reason != rejection.violations[0].message
        assert rejection.reason in [
            v.message
            for v in rejection.violations
            if order.get(v.rigidity, 3) == min(ranks)
        ]
        checked += 1
    # Calculated warnings no longer create multi-violation rejections. Exercise
    # the reason selector directly with a preceding warning and true nonfit.
    from esp_engine.results import ConstraintViolation
    config=result.candidates[0].configuration
    warning=ConstraintViolation(constraint_type="thermal",rigidity="soft",message="Cooling warning")
    physical=ConstraintViolation(constraint_type="geometry",rigidity="absolute",physical_impossibility=True,message="Known casing nonfit")
    rejection=DesignEngine()._rejection(config,"geometry_prescreen",[warning,physical])
    assert rejection.reason==physical.message


def test_cooling_is_filed_as_thermal_not_mechanical():
    """A thermal filter over violations used to miss the cooling failure."""
    result = DesignEngine().run(_viscous_case())
    cooling_violations = [
        v
        for c in result.candidates
        for v in c.soft_violations
        if "cooling velocity" in v.message.lower()
    ]
    assert cooling_violations
    assert all(v.constraint_type == "thermal" for v in cooling_violations)
    assert all(v.unit == "ft/s" for v in cooling_violations)
    assert all(v.limit_value == pytest.approx(2.8) for v in cooling_violations)
