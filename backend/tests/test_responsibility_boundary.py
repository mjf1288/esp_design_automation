"""Framework v0.6 §3.0 — the MEASURED class and the responsibility boundary.

The tests worth having here are the ones that stop the boundary from being
"simplified" away, because every simplification available is superficially
reasonable:

  - adding ``Source.MEASURED`` (collapses two orthogonal axes into one)
  - deriving the class from ``Source`` alone (same source, opposite obligations)
  - merging the trust register into the assumption ledger (implies the system
    stands behind numbers it never produced)
  - letting a bias policy apply to a measurement (silent overwrite of measured
    data, reported with the measurement's own provenance)
"""

from __future__ import annotations

import pytest

from esp_engine.models import ComplicationType
from esp_engine.pipeline import DesignEngine
from esp_engine.provenance import (
    DEFAULT_BIAS_POLICIES,
    Assumption,
    BiasDirection,
    ResponsibilityClass,
    Source,
    Tracked,
    responsibility_of,
    transcription_owned_by_system,
)
from esp_engine.responsibility import (
    BiasRefused,
    DataClass,
    apply_bias,
    build_trust_register,
    data_class_of,
)

from tests.test_pipeline import _base_case


# --- the two axes stay separate -----------------------------------------------


def test_measured_is_not_a_source_member():
    """A responsibility class must not be smuggled into a trust ordering.

    Source carries rank and base_confidence. MEASURED spans the whole range --
    telemetry at 0.97 and customer_stated at 0.70 are both MEASURED-class -- so a
    Source.MEASURED member would have to be given one rank for a class that has
    none, and would make TELEMETRY and MEASURED look mutually exclusive.
    """
    assert "MEASURED" not in Source.__members__
    assert not any(m.value == "measured" for m in Source)


def test_every_source_has_an_accuracy_owner():
    """No source may exist without a decision about whose fault a bad value is."""
    for source in Source:
        owner = responsibility_of(source)
        assert isinstance(owner, ResponsibilityClass)


def test_no_unknown_responsibility_member():
    assert "UNKNOWN" not in ResponsibilityClass.__members__


def test_measured_class_spans_multiple_confidences():
    telemetry = Tracked.measured(2400.0, "psi", source=Source.TELEMETRY)
    stated = Tracked.stated(2400.0, "psi")
    assert telemetry.confidence != stated.confidence
    assert responsibility_of(telemetry.source) is ResponsibilityClass.CUSTOMER
    assert responsibility_of(stated.source) is ResponsibilityClass.CUSTOMER


# --- the class cannot come from Source alone -----------------------------------


def test_same_source_opposite_obligations():
    """The crux of §3.0 vs §3.1.

    A customer-stated reservoir pressure must not be argued with; a
    customer-stated target rate must be checked against well capability. Same
    source, same data sheet, opposite duties -- so the class is a property of the
    field, not of the source.
    """
    pressure = data_class_of("reservoir.reservoir_pressure_psi")
    target = data_class_of("expectations.target_rate_bpd")
    assert pressure is DataClass.MEASURED
    assert target is DataClass.EXPECTATION
    assert pressure is not target


def test_complications_are_measured_class():
    """§3.3 states this outright."""
    assert data_class_of("complications") is DataClass.MEASURED
    assert data_class_of("complications.items[0].severity") is DataClass.MEASURED


def test_framework_pressure_list_governs_over_model_grouping():
    """§3.0 lists wellhead and casing pressure as measured; the model files them
    under Expectations alongside genuine intent quantities."""
    assert data_class_of("expectations.wellhead_pressure_psi") is DataClass.MEASURED
    assert data_class_of("expectations.casing_pressure_psi") is DataClass.MEASURED
    # Neighbours in the same model that really are intent.
    assert data_class_of("expectations.target_pip_psi") is DataClass.EXPECTATION
    assert data_class_of("expectations.desired_drawdown_psi") is DataClass.EXPECTATION


def test_productivity_index_is_not_measured_class():
    """§5.2 requires PI to be swept because error either way is dangerous.

    Classifying it MEASURED would make the framework's own instruction look like
    the system arguing with supplied data.
    """
    assert data_class_of("reservoir.productivity_index_bpd_psi") is DataClass.DERIVED
    # ...while its siblings under the same prefix stay measured.
    assert data_class_of("reservoir.bht_f") is DataClass.MEASURED


def test_longest_prefix_wins_regardless_of_table_order():
    assert data_class_of("reservoir.productivity_index_bpd_psi") is DataClass.DERIVED
    assert data_class_of("reservoir.anything_else") is DataClass.MEASURED


def test_unclassified_path_is_derived_not_measured():
    """An unmapped field must not inherit §3.0's protection by accident."""
    assert data_class_of("metadata.case_id") is DataClass.DERIVED
    assert data_class_of("") is DataClass.DERIVED


# --- the invariant: the system does not improve what it was given -------------


@pytest.mark.parametrize(
    "source",
    [
        Source.TELEMETRY,
        Source.MEASUREMENT,
        Source.CUSTOMER_STATED,
        Source.REPORT,
    ],
)
def test_customer_owned_value_cannot_carry_an_assumption(source):
    with pytest.raises(ValueError, match="must not carry an Assumption"):
        Tracked(
            value=2400.0,
            unit="psi",
            source=source,
            confidence=0.9,
            assumption=Assumption(basis="regional typical", rationale="margin"),
        )


def test_extracted_value_cannot_carry_an_assumption():
    with pytest.raises(ValueError, match="must not carry an Assumption"):
        Tracked(
            value=2400.0,
            source=Source.TEXT_EXTRACTION,
            extracted_from="reservoir pressure 2400 psi",
            assumption=Assumption(basis="regional typical", rationale="margin"),
        )


def test_system_supplied_value_may_carry_an_assumption():
    tracked = Tracked.assumed(
        800.0,
        Assumption(
            basis="regional typical for gassy carbonate",
            bias=BiasDirection.UPWARD,
            rationale="gas underestimation kills the installation",
            unbiased_value=550.0,
        ),
        unit="scf/stb",
    )
    assert tracked.assumption is not None
    assert responsibility_of(tracked.source) is ResponsibilityClass.SYSTEM


def test_engineer_override_may_carry_an_assumption():
    """The engineer is a party to the design, not data the system must not touch."""
    tracked = Tracked(
        value=300.0,
        source=Source.ENGINEER_OVERRIDE,
        confidence=0.85,
        assumption=Assumption(basis="offset well", rationale="best available"),
    )
    assert tracked.assumption is not None


# --- transcription is a separate, system-owned risk --------------------------


def test_extraction_splits_content_from_transcription():
    assert transcription_owned_by_system(Source.TEXT_EXTRACTION)
    assert not transcription_owned_by_system(Source.CUSTOMER_STATED)
    assert not transcription_owned_by_system(Source.TELEMETRY)
    # The content is still the customer's -- the system did not measure the well.
    assert responsibility_of(Source.TEXT_EXTRACTION) is ResponsibilityClass.CUSTOMER


def test_extraction_still_requires_a_verbatim_span():
    """The transcription risk is only reviewable if the span is there."""
    with pytest.raises(ValueError, match="extracted_from"):
        Tracked(value=1.0, source=Source.TEXT_EXTRACTION)


# --- bias must not cross the boundary ----------------------------------------


def test_bias_applies_to_an_assumption():
    policy = DEFAULT_BIAS_POLICIES["gor_gassy"]
    neutral = Tracked(value=550.0, unit="scf/stb", source=Source.DEFAULT)
    biased = apply_bias(policy, neutral, basis="regional typical")
    assert biased.value == pytest.approx(550.0 * 1.45)
    assert biased.source is Source.ASSUMPTION
    assert biased.assumption is not None
    assert biased.assumption.unbiased_value == pytest.approx(550.0)
    assert biased.assumption.policy_id == "gor_gassy"


@pytest.mark.parametrize(
    "source",
    [Source.TELEMETRY, Source.MEASUREMENT, Source.CUSTOMER_STATED, Source.REPORT],
)
def test_bias_refuses_customer_owned_data(source):
    """Biasing a lab PVT viscosity up 25% is not conservatism, it is overwriting
    a measurement with a larger number of the system's own invention."""
    policy = DEFAULT_BIAS_POLICIES["viscosity"]
    measured = Tracked(value=180.0, unit="cp", source=source, confidence=0.9)
    with pytest.raises(BiasRefused, match="§3.0"):
        apply_bias(policy, measured, basis="lab PVT")


def test_bias_refuses_vendor_and_engineer_data():
    policy = DEFAULT_BIAS_POLICIES["temperature"]
    for source in (Source.CATALOG, Source.ENGINEER_OVERRIDE):
        with pytest.raises(BiasRefused):
            apply_bias(
                policy,
                Tracked(value=200.0, source=source, confidence=0.85),
                basis="x",
            )


def test_bias_refusal_is_loud_not_a_silent_passthrough():
    """A bias that quietly declines looks identical to one that applied."""
    policy = DEFAULT_BIAS_POLICIES["viscosity"]
    measured = Tracked(value=180.0, unit="cp", source=Source.TELEMETRY)
    try:
        apply_bias(policy, measured, basis="lab")
    except BiasRefused:
        return
    pytest.fail("refusal must raise, not return the unbiased value")


def test_bias_refuses_non_numeric():
    policy = DEFAULT_BIAS_POLICIES["gor_gassy"]
    with pytest.raises(BiasRefused, match="numeric"):
        apply_bias(
            policy, Tracked(value=True, source=Source.DEFAULT), basis="x"
        )


# --- the trust register -------------------------------------------------------


def test_register_lists_high_confidence_values_too():
    """§3.0's disclosure duty is not conditional on confidence.

    A telemetry pressure at 0.97 was still taken on trust. This is the difference
    from the assumption ledger, which only ever holds low-confidence entries.
    """
    register = build_trust_register(_base_case())
    telemetry = [e for e in register if e.source == Source.TELEMETRY.value]
    assert telemetry
    assert all(e.confidence > 0.9 for e in telemetry)


def test_register_is_not_the_assumption_ledger():
    result = DesignEngine().run(_base_case())
    ledger_paths = {e.field_path for e in result.assumption_ledger}
    register_paths = {e.field_path for e in result.trust_register}
    assert register_paths
    assert ledger_paths
    # The register must not be a superset or a copy -- it holds measured values
    # the ledger never sees.
    assert register_paths - ledger_paths


def test_register_names_the_accuracy_owner_for_every_entry():
    register = build_trust_register(_base_case())
    assert register
    for entry in register:
        assert entry.accuracy_owner
        assert entry.data_class


def test_measured_owner_text_disclaims_the_engineers_responsibility():
    register = build_trust_register(_base_case())
    pressure = next(
        e for e in register if e.field_path == "reservoir.reservoir_pressure_psi"
    )
    assert pressure.data_class == DataClass.MEASURED.value
    assert "outside the ESP design engineer's responsibility" in pressure.accuracy_owner


def test_expectation_owner_text_says_it_is_checked_not_taken_as_fact():
    register = build_trust_register(_base_case())
    target = next(e for e in register if e.field_path == "expectations.target_rate_bpd")
    assert target.data_class == DataClass.EXPECTATION.value
    assert "checked against well capability" in target.accuracy_owner


# --- boundary crossings -------------------------------------------------------


def test_system_supplied_measured_field_is_flagged_as_a_gap_not_trust():
    """The inverse of §3.0: data does NOT exist and the system invented it."""
    result = DesignEngine().run(_base_case())
    crossings = result.responsibility_boundary_crossings
    assert "reservoir.surface_temp_f" in crossings
    entry = next(
        e for e in result.trust_register if e.field_path == "reservoir.surface_temp_f"
    )
    assert entry.boundary_note is not None
    assert "nobody measured" in entry.boundary_note
    assert "not data taken on trust" in entry.boundary_note


def test_engineer_override_of_a_measured_field_is_disclosed():
    case = _base_case()
    reservoir = case.reservoir.model_copy(
        update={
            "reservoir_pressure_psi": Tracked.overridden(
                2500.0, "psi", note="engineer believes the gauge reads low"
            )
        }
    )
    result = DesignEngine().run(case.model_copy(update={"reservoir": reservoir}))
    assert "reservoir.reservoir_pressure_psi" in (
        result.responsibility_boundary_crossings
    )
    entry = next(
        e
        for e in result.trust_register
        if e.field_path == "reservoir.reservoir_pressure_psi"
    )
    assert entry.boundary_note is not None
    assert "no longer reflects the data the customer supplied" in entry.boundary_note


def test_engineer_override_of_an_expectation_is_not_a_crossing():
    """Overriding intent is routine; only measured data raises the boundary."""
    case = _base_case()
    expectations = case.expectations.model_copy(
        update={"target_rate_bpd": Tracked.overridden(1500.0, "bpd")}
    )
    result = DesignEngine().run(case.model_copy(update={"expectations": expectations}))
    assert "expectations.target_rate_bpd" not in (
        result.responsibility_boundary_crossings
    )


def test_assumption_on_a_non_measured_field_is_not_a_crossing():
    """It is an assumption, and belongs to the ledger rather than the register."""
    register = build_trust_register(_base_case())
    paths = {e.field_path for e in register}
    assert "constraints.mechanical.shaft_safety_factor" not in paths


def test_clean_case_has_no_crossings():
    case = _base_case()
    reservoir = case.reservoir.model_copy(
        update={"surface_temp_f": Tracked.stated(70.0, "F")}
    )
    fluid = case.fluid.model_copy(
        update={"water_salinity_ppm": Tracked.stated(30000.0, "ppm")}
    )
    result = DesignEngine().run(
        case.model_copy(update={"reservoir": reservoir, "fluid": fluid})
    )
    assert result.responsibility_boundary_crossings == []


# --- the register does not disturb the design --------------------------------


def test_register_does_not_change_the_result(monkeypatch):
    result = DesignEngine().run(_base_case())
    assert result.candidates
    # The 84-stage / 55 Hz leader remains adequate at installation. Its score
    # now includes future head deficits rather than false full-horizon coverage.
    monkeypatch.setattr(DesignEngine,"_trust_register",lambda self,case: [])
    without=DesignEngine().run(_base_case())
    assert [c.score for c in result.candidates]==[c.score for c in without.candidates]
    assert result.provenance.case_hash == without.provenance.case_hash


def test_register_present_on_an_infeasible_run():
    """Disclosure must survive the paths that return early."""
    case = _base_case()
    expectations = case.expectations.model_copy(
        update={"target_rate_bpd": Tracked.stated(500000.0, "bpd")}
    )
    result = DesignEngine().run(case.model_copy(update={"expectations": expectations}))
    assert result.verdict.value=="feasible_with_caveats"
    assert "not validated at the requested target" in result.verdict_explanation
    assert result.trust_register, "an infeasible run still rests on trusted inputs"
