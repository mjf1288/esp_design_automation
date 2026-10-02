"""Adversarial tests for the narrative numeric guard.

These tests exist because the guard is the single component standing between a
fluent language model and a fabricated number in an engineering document. The
first implementation passed its own unit tests while admitting 61% of arbitrary
integers, so this file measures the guard rather than merely exercising it.
"""

from __future__ import annotations

import pytest

from esp_agents.contracts import NarrativeJudgment, NarrativeLLMResponse
from esp_agents.narrative import validate_narrative_numbers
from esp_agents.numeric_guard import ScopedNumericGuard
from esp_engine.pipeline import DesignEngine
from esp_engine.results import FeasibilityVerdict

from tests.test_pipeline import _base_case


@pytest.fixture(scope="module")
def result():
    design = DesignEngine().run(_base_case())
    assert design.verdict is FeasibilityVerdict.FEASIBLE_WITH_CAVEATS
    assert design.candidates, "guard tests need a feasible design to validate against"
    return design


@pytest.fixture(scope="module")
def guard(result):
    return ScopedNumericGuard(result, candidate_rank=1)


def test_recovery_narrative_scope_keeps_boundary_evidence_and_full_schedule(result):
    from esp_agents.numeric_guard import narrative_scope
    candidate = narrative_scope(result)["candidate"]
    recovery = candidate["vsd_recovery"]
    assert [p["month"] for p in recovery["verified_points"]] == [3, 14]
    assert len(recovery["frequency_schedule"]) == 12
    assert "cells_sampled" not in candidate


def test_length_rounding_does_not_allow_ten_foot_fabrications(guard):
    assert guard._matches(2008, [2008.0108], "ft")
    assert not guard._matches(2018, [2008.0108], "ft")


def _narrative(**kwargs) -> NarrativeLLMResponse:
    return NarrativeLLMResponse(
        fact_summary=kwargs.get("fact_summary", "Design summary."),
        configuration_facts=kwargs.get("configuration_facts", []),
        validity_boundary_facts=kwargs.get("validity_boundary_facts", []),
        watch_facts=kwargs.get("watch_facts", []),
        judgments=kwargs.get(
            "judgments",
            [NarrativeJudgment(statement="Reasonable design.", references_facts=[])],
        ),
    )


# --- true values must survive -------------------------------------------------


def test_true_configuration_numbers_pass(result):
    config = result.candidates[0].configuration
    narrative = _narrative(
        fact_summary=(
            f"The selected pump runs {config.stages} stages at "
            f"{config.frequency_hz:.0f} Hz."
        ),
        configuration_facts=[
            f"Setting depth is {config.setting_depth_md_ft:,.0f} ft measured depth.",
            f"The motor is rated {config.motor_hp:.0f} hp.",
        ],
    )
    assert validate_narrative_numbers(narrative, result) == []


def test_stored_fraction_may_be_written_as_a_percentage(result):
    efficiency = result.candidates[0].score.efficiency_avg_frac
    narrative = _narrative(
        configuration_facts=[f"Average efficiency is {efficiency * 100:.1f}%."]
    )
    assert validate_narrative_numbers(narrative, result) == []


# --- fabrications must be caught ---------------------------------------------


@pytest.mark.parametrize(
    "field,text",
    [
        ("fact_summary", "The pump runs 44 stages at 65 Hz."),
        ("configuration_facts", "Set at 9,437 ft measured depth."),
        ("configuration_facts", "Driven by a 75 hp motor."),
        ("configuration_facts", "Pump efficiency is 71.3% at the design point."),
        ("watch_facts", "Expect roughly 1,450 days of run life."),
        # Month 37 is beyond the 24-month horizon, so no timeline row can supply
        # it. An earlier version of this case used month 17, which passed only
        # because a dimension-inference bug put every real timeline month into
        # the unknown bucket. Month 17 is a legitimate row and must stay
        # quotable; asserting otherwise pinned a bug in place as a requirement.
        ("validity_boundary_facts", "The design falls out of range at month 37."),
    ],
)
def test_fabricated_numbers_are_blocked(result, field, text):
    narrative = _narrative(**{field: text if field == "fact_summary" else [text]})
    violations = validate_narrative_numbers(narrative, result)
    assert violations, f"guard failed to catch a fabricated number in {field}: {text}"


def test_judgments_are_checked_as_strictly_as_facts(result):
    """A judgment may be an opinion. It may not contain an invented number.

    A reader cannot tell which numbers in a document were computed and which were
    improvised, so the distinction between fact and judgment does not license
    looser numeric handling.
    """
    narrative = _narrative(
        judgments=[
            NarrativeJudgment(
                statement="I would derate this to 58 Hz to protect the bearing.",
                references_facts=[],
            )
        ]
    )
    violations = validate_narrative_numbers(narrative, result)
    assert violations and violations[0].value == pytest.approx(58.0)


def test_correct_number_in_the_wrong_dimension_is_blocked(result):
    """41 is a real stage count. It is not a real intake pressure.

    Unit awareness is what separates this guard from a substring search. Without
    it, every number in the result vouches for every other number.
    """
    config = result.candidates[0].configuration
    narrative = _narrative(
        configuration_facts=[f"Pump intake pressure is {config.stages} psi."]
    )
    assert validate_narrative_numbers(narrative, result)


def test_rejected_configurations_are_out_of_scope(result):
    """Numbers from rejected designs must not vouch for the narrative.

    The rejection list holds hundreds of configurations the narrative never
    mentions. Admitting their numbers is what made the original guard permissive.
    """
    assert result.rejected, "this test needs at least one rejected configuration"
    stage_counts = {
        r.configuration.stages
        for r in result.rejected
        if r.configuration.stages not in {0, result.candidates[0].configuration.stages}
    }
    if not stage_counts:
        pytest.skip("no distinct rejected stage count available in this run")
    borrowed = sorted(stage_counts)[0]
    narrative = _narrative(fact_summary=f"The pump runs {borrowed} stages.")
    assert validate_narrative_numbers(narrative, result)


# --- measured strength, not just behaviour -----------------------------------


@pytest.mark.parametrize(
    "unit,max_false_accept_frac",
    [
        ("Hz", 0.02),
        ("stages", 0.02),
        ("hp", 0.02),
        # ft is the second-densest whitelisted dimension after bpd: each
        # candidate contributes a head value per month per envelope plus the
        # design-point TDH, so a 10-candidate feasible run legitimately produces
        # several hundred distinct ft numbers. The ceiling was 5% under the
        # pre-Finding-1 base case (which surfaced a single under-staged design
        # repeated three times); with the head-shortfall gate closed the base
        # case now surfaces ten hydraulically distinct candidates and the
        # whitelist grows accordingly. 6% still keeps the guard 10x stronger
        # than the unit-blind baseline (61%).
        ("ft", 0.06),
        ("psi", 0.05),
        ("days", 0.02),
        ("months", 0.05),
        ("in", 0.02),
        # bpd is the weakest physical dimension and is asserted as such rather
        # than quietly excluded. A 24-month trajectory across three envelopes
        # legitimately contains many distinct rate values, so the whitelist is
        # genuinely dense here. Tightening this requires binding a number to the
        # quantity named beside it, not a tolerance change.
        ("bpd", 0.10),
    ],
)
def test_false_accept_rate_stays_low(guard, unit, max_false_accept_frac):
    """Guard strength is a measurable property and is asserted as one.

    Baseline for comparison: the original flat, scope-free, unit-blind whitelist
    accepted 61% of these same integers.
    """
    trials = range(1, 2001)
    accepted = sum(
        1 for i in trials if not guard.check_text(f"value is {i} {unit}", field="t")
    )
    rate = accepted / len(trials)
    assert rate <= max_false_accept_frac, (
        f"{unit}: {rate:.1%} of arbitrary integers accepted, "
        f"above the {max_false_accept_frac:.0%} ceiling"
    )


def test_guard_rejects_negative_tolerances(result):
    with pytest.raises(ValueError):
        ScopedNumericGuard(result, absolute_tolerance=-1.0)


def test_violation_names_the_field_and_reason(result):
    narrative = _narrative(configuration_facts=["Driven by a 75 hp motor."])
    violations = validate_narrative_numbers(narrative, result)
    assert violations
    assert "configuration_facts" in violations[0].context
    assert "hp" in violations[0].context


def test_percentage_false_accept_ceiling_unbound(guard):
    """Percentages with NO quantity name are checked over 1-100.

    A percentage above 100 is implausible in narrative prose, so measuring over
    1-2000 would flatter the guard by averaging in a range it never has to
    defend. The honest measurement is over 1-100.

    This ceiling is looser than the physical dimensions because the engine's
    own scenario-point descriptions legitimately mention roughly thirty
    distinct integer percentages (water-cut progressions across three
    envelopes). Without a quantity name, the guard cannot tell which field a
    number ought to belong to and falls back to the wide frac_pct pool. The
    quantity-bound path -- see test_percentage_false_accept_ceiling_bound --
    is where the tightening actually lives.
    """
    trials = range(1, 101)
    accepted = sum(
        1 for i in trials if not guard.check_text(f"value is {i}%", field="t")
    )
    assert accepted / len(trials) <= 0.35


@pytest.mark.parametrize(
    "quantity_name,max_false_accept_frac",
    [
        # The high-value target the roadmap called out: naming a quantity next
        # to a percentage must tighten the check to that quantity's own values.
        # The demo case's motor_loading value is a single number (65.2%), so
        # naming "motor loading" restricts the acceptable integers to that
        # narrow band -- three integers pass (65 rejected as too far from
        # 65.2, but 65.2 itself and neighbours within the 0.05 window count).
        ("efficiency", 0.10),
        ("motor loading", 0.10),
        ("voltage drop", 0.10),
        ("head margin", 0.10),
        ("coverage", 0.10),
        ("q/qbep", 0.10),
        # water cut is looser: the demo trajectory publishes 74 distinct
        # water-cut values covering 35-58%, and every one of those is a real
        # value the narrative may legitimately quote. The binding still cuts
        # off 55%+ of the range (everything below 35 and above 58) and drops
        # the false-accept rate about 12%, which is the honest number.
        ("water cut", 0.30),
    ],
)
def test_percentage_false_accept_ceiling_bound(
    guard, quantity_name, max_false_accept_frac
):
    """Quantity-named percentages match only their own field's values.

    Baseline before binding: 31% of arbitrary integers accepted regardless of
    quantity name (the label was ignored). The binding tightens that to the
    values the schema actually holds for the named field, which is a small
    fraction of the wider frac_pct pool for every field except the ones the
    engine explores densely in its own trajectory (water cut).
    """
    trials = range(1, 101)
    accepted = sum(
        1
        for i in trials
        if not guard.check_text(f"{quantity_name} is {i}%", field="t")
    )
    rate = accepted / len(trials)
    assert rate <= max_false_accept_frac, (
        f"{quantity_name}: {rate:.1%} of arbitrary percentages accepted, "
        f"above the {max_false_accept_frac:.0%} ceiling. The quantity-name "
        f"binding has weakened; check the alias table and per-field walk."
    )


def test_legitimate_named_percentages_still_pass(result):
    """The roadmap guardrail: tightening the guard must not refuse correct prose.

    Every one of these is a real value computed for the narrated candidate,
    written with the quantity name the engine actually publishes it under.
    A refusal here means the binding is over-tight and would make the
    narrative agent's own honest reports unquotable, which is the exact
    failure mode this whole line of work is trying to avoid.
    """
    guard = ScopedNumericGuard(result, candidate_rank=1)
    c = result.candidates[0]
    dp = c.design_point
    lines = [
        f"Average efficiency is {c.score.efficiency_avg_frac * 100:.1f}%.",
        f"Efficiency at the design point is {dp.efficiency_frac * 100:.1f}%.",
        f"Motor loading is {dp.motor_loading_frac * 100:.1f}%.",
        f"Head margin at the design point is {dp.head_margin_frac * 100:.1f}%.",
        f"Time coverage is {c.score.time_coverage_frac * 100:.0f}%.",
        f"BEP time fraction is {c.score.bep_time_frac * 100:.0f}%.",
        f"Cable voltage drop is {dp.cable_voltage_drop_frac * 100:.1f}%.",
        f"q/qbep is {dp.q_over_qbep:.2f}.",
    ]
    for line in lines:
        findings = guard.check_text(line, field="t")
        assert not findings, (
            f"guard refused a legitimate named percentage: {line!r}\n"
            + "\n".join(f.reason for f in findings)
        )


def test_engine_prose_percentage_stays_quotable_under_binding(guard):
    """Band constants the engine wrote itself must survive quantity binding.

    An engine warning like ``motor loading is outside the 75%-85% target
    band`` mentions 75 and 85 next to the quantity name ``motor loading``,
    but neither 75 nor 85 is a real motor_loading_frac value in scope -- they
    are band boundaries. A pure quantity-name binding would refuse this line
    verbatim, which is the same refusal-of-truth failure as v2. The engine
    prose exemption (values the engine itself put on the page, without a
    quantity name adjacent) keeps them quotable.
    """
    assert not guard.check_text(
        "motor loading is outside the 75%-85% target band", field="t"
    )
    assert not guard.check_text(
        "cable voltage drop exceeded the 10% tolerance", field="t"
    )


def test_quantity_name_binding_catches_wrong_field_percentage(guard):
    """The measurement the roadmap called out: naming a quantity must catch a
    number that is real for a different quantity.

    The demo case's water_cut_frac reaches 58% by the end of the trajectory,
    so ``water cut 58%`` is a true value. Under the old unit-blind check
    ``efficiency 58%`` also passed because 58 was in scope as a frac_pct
    from water_cut. The binding must reject it: efficiency does not reach
    58% anywhere in the design point or in any timeline row.
    """
    # Positive control: water cut 58% must pass (it really is a value).
    assert not guard.check_text("water cut reaches 58%", field="t")
    # The regression: efficiency 58% must be refused.
    findings = guard.check_text("efficiency is 58%", field="t")
    assert findings, (
        "efficiency 58% was accepted even though efficiency never reaches 58%; "
        "the quantity name is being ignored"
    )
    assert findings[0].value == pytest.approx(58.0)
    assert "efficiency_frac" in findings[0].reason or "binds" in findings[0].reason


def test_numbers_inside_unit_tokens_are_not_treated_as_quantities(guard):
    """"8.0 deg/100ft" states one value, not two."""
    assert not guard.check_text("Max dogleg assumption: 8.0 deg/100ft.", field="t")


def test_hyphenated_time_compound_reads_as_months(guard):
    """"24-month horizon" must resolve to months, not to the amps alias "a"."""
    assert not guard.check_text("evaluated over a 24-month horizon", field="t")


def test_engine_prose_numbers_keep_their_units(guard):
    """A band quoted verbatim from an engine warning stays quotable.

    The engine writes "outside the 75%-85% target band" into its own warning
    text. Harvesting those numbers without units left them unavailable as
    percentages, so a narrative quoting the band verbatim was rejected.
    """
    assert not guard.check_text(
        "motor loading is outside the 75%-85% target band", field="t"
    )


def test_percent_sign_is_recognised_with_or_without_a_space(result):
    """"20.5 %" must be checked as a percentage, exactly like "20.5%".

    Otherwise the number falls through to the dimensionless path and is compared
    against every value in scope, which is materially weaker.
    """
    guard = ScopedNumericGuard(result, candidate_rank=1)
    spaced = guard.check_text("value is 913 %", field="t")
    attached = guard.check_text("value is 913%", field="t")
    assert bool(spaced) == bool(attached) is True
