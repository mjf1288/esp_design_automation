"""Computed evidence, refusal, and fact-preserving overlay tests."""

from __future__ import annotations

from datetime import date, timedelta

from esp_empirical.application import OverlayTarget, apply_rules_as_overlay
from esp_empirical.database import EmpiricalStore, TenantContext
from esp_empirical.models import (
    DerivedEmpiricalRule,
    ObservationDraft,
    ObservedOperatingConditions,
    RuleDerivationRefusal,
    RuleHypothesis,
)
from esp_empirical.rules import RuleDeriver
from esp_engine.provenance import Tracked


def _observation(*, index: int, exposed: bool, early_failure: bool) -> ObservationDraft:
    install_date = date(2024, 1, 1) + timedelta(days=index)
    duration = 45 if early_failure else 300
    return ObservationDraft(
        pump_model="P-900",
        region="Permian",
        field_id=f"Field-{index % 3}",
        case_inputs_snapshot={"target_rate_bpd": 1000.0},
        selected_configuration={"pump_model": "P-900", "stages": 90},
        install_date=install_date,
        pull_date=install_date + timedelta(days=duration),
        still_running=False,
        outcome_observed_date=install_date + timedelta(days=duration),
        is_failure=early_failure,
        failure_mode="gas_lock" if early_failure else None,
        operating_conditions=ObservedOperatingConditions(
            setting_depth_md_ft=Tracked.measured(6200.0 if exposed else 7800.0, "ft"),
            gfv_frac=Tracked.measured(0.35 if exposed else 0.05, "frac"),
            water_cut_frac=Tracked.measured(0.4, "frac"),
        ),
        source=Tracked.measured(f"telemetry-{index}"),
    )


def _hypothesis() -> RuleHypothesis:
    return RuleHypothesis(
        hypothesis_id="depth-and-gvf-early-failure",
        pump_model="P-900",
        setting_depth_below_ft=7000.0,
        gfv_at_or_above_frac=0.2,
        early_failure_within_days=90,
    )


def test_rule_refuses_three_case_anecdote() -> None:
    store = EmpiricalStore()
    store.create_schema()
    with store.for_tenant(TenantContext(tenant_id="tenant-a")) as repository:
        for index in range(3):
            repository.add_observation(_observation(index=index, exposed=True, early_failure=True))
        outcome = RuleDeriver(repository).derive_early_failure_rule(_hypothesis())

    assert isinstance(outcome, RuleDerivationRefusal)
    assert outcome.n_observations_eligible == 3
    assert "below the 20-observation minimum" in outcome.warnings[-1]


def test_rule_evidence_is_computed_and_overlay_cannot_mutate_facts() -> None:
    store = EmpiricalStore()
    store.create_schema()
    with store.for_tenant(TenantContext(tenant_id="tenant-a")) as repository:
        # Deliberately separated groups: 9/10 versus 1/10 early failures.  These
        # are synthetic test data, not a vendor or field-performance assertion.
        for index in range(10):
            repository.add_observation(
                _observation(index=index, exposed=True, early_failure=index < 9)
            )
        for index in range(10, 20):
            repository.add_observation(
                _observation(index=index, exposed=False, early_failure=index == 10)
            )
        outcome = RuleDeriver(repository).derive_early_failure_rule(_hypothesis())
        assert isinstance(outcome, DerivedEmpiricalRule)
        rule = repository.save_derived_rule(outcome)

        assert rule.evidence.n_observations == 20
        assert rule.evidence.n_exposed == 10
        assert rule.evidence.n_comparison == 10
        assert rule.evidence.effect_risk_difference == 0.8
        assert rule.evidence.risk_difference_ci_95_low > 0.0
        assert rule.evidence.p_value < 0.05
        assert rule.evidence.evidence_strength < 1.0
        assert rule.evidence.confidence_basis.startswith("computed evidence strength")
        assert rule.bias_flags  # Biases are always surfaced, not silently cleared.

        target = OverlayTarget(
            design_id="design-1",
            tenant_id="tenant-a",
            pump_model="P-900",
            setting_depth_md_ft=6500.0,
            gfv_frac=0.3,
            fact_ids=("candidate_1.zone", "candidate_1.q_over_qbep"),
        )
        overlay = apply_rules_as_overlay(target, repository)

    assert overlay.design_id == "design-1"
    assert len(overlay.judgments) == 1
    assert overlay.judgments[0].references_facts == ["candidate_1.zone", "candidate_1.q_over_qbep"]
    assert "does not alter any deterministic calculation" in overlay.judgments[0].statement
    assert target.setting_depth_md_ft == 6500.0  # The input fact context was never mutated.


def test_other_tenant_cannot_contribute_rules_to_overlay() -> None:
    store = EmpiricalStore()
    store.create_schema()
    with store.for_tenant(TenantContext(tenant_id="tenant-a")) as repository_a:
        for index in range(10):
            repository_a.add_observation(_observation(index=index, exposed=True, early_failure=index < 9))
        for index in range(10, 20):
            repository_a.add_observation(_observation(index=index, exposed=False, early_failure=index == 10))
        outcome = RuleDeriver(repository_a).derive_early_failure_rule(_hypothesis())
        assert isinstance(outcome, DerivedEmpiricalRule)
        repository_a.save_derived_rule(outcome)

    with store.for_tenant(TenantContext(tenant_id="tenant-b")) as repository_b:
        target = OverlayTarget(
            design_id="design-b",
            tenant_id="tenant-b",
            pump_model="P-900",
            setting_depth_md_ft=6500.0,
            gfv_frac=0.3,
            fact_ids=("candidate_1.zone",),
        )
        overlay = apply_rules_as_overlay(target, repository_b)

    assert overlay.judgments == ()
    assert "No tenant-local empirical rule" in overlay.warnings[0]
