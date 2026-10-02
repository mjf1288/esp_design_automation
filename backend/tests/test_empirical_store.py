"""Tenant isolation tests for the empirical SQLAlchemy repository."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from esp_empirical.database import EmpiricalStore, TenantContext, TenantIsolationError, TenantScopeRequiredError
from esp_empirical.models import ObservationDraft, ObservedOperatingConditions
from esp_empirical.orm import ObservationRow
from esp_engine.provenance import Tracked


def _draft(*, failure: bool = True, day: int = 30, pump_model: str = "TEST-PUMP") -> ObservationDraft:
    install_date = date(2025, 1, 1)
    return ObservationDraft(
        external_case_id="case-1",
        pump_model=pump_model,
        region="Permian",
        field_id="Field-A",
        case_inputs_snapshot={"target_rate_bpd": 900.0},
        selected_configuration={"pump_model": pump_model, "stages": 100},
        install_date=install_date,
        pull_date=install_date + timedelta(days=day),
        still_running=False,
        outcome_observed_date=install_date + timedelta(days=day),
        is_failure=failure,
        failure_mode="gas_lock" if failure else None,
        operating_conditions=ObservedOperatingConditions(
            setting_depth_md_ft=Tracked.measured(6200.0, "ft"),
            gfv_frac=Tracked.measured(0.32, "frac"),
        ),
        source=Tracked.measured("telemetry-run-1"),
    )


def test_cross_tenant_observation_leakage_is_impossible() -> None:
    """A tenant B repository cannot read a tenant A observation even by its UUID."""
    store = EmpiricalStore()
    store.create_schema()
    with store.for_tenant(TenantContext(tenant_id="tenant-a")) as repository_a:
        observation = repository_a.add_observation(_draft())
        assert len(repository_a.list_observations()) == 1

    with store.for_tenant(TenantContext(tenant_id="tenant-b")) as repository_b:
        assert repository_b.list_observations() == []
        assert repository_b.get_observation(observation.observation_id) is None


def test_empirical_raw_query_without_tenant_fails_closed() -> None:
    """The guarded session rejects SELECT before an unscoped query can run."""
    store = EmpiricalStore()
    store.create_schema()
    raw_session = store._session_factory()  # Deliberate attempted bypass in this proof test.
    try:
        with pytest.raises(TenantScopeRequiredError, match="TenantContext"):
            raw_session.scalars(select(ObservationRow)).all()
    finally:
        raw_session.close()


def test_scoped_session_rejects_cross_tenant_write() -> None:
    """Rows cannot be attached to a scoped session under a different tenant identity."""
    store = EmpiricalStore()
    store.create_schema()
    with store.for_tenant(TenantContext(tenant_id="tenant-a")) as repository:
        foreign_row = ObservationRow(
            tenant_id="tenant-b",
            pump_model="TEST-PUMP",
            case_inputs_snapshot={},
            selected_configuration={},
            install_date=date(2025, 1, 1),
            still_running=True,
            outcome_observed_date=date(2025, 1, 2),
            is_failure=False,
            run_life_days=1,
            operating_conditions={},
            source={},
        )
        with pytest.raises(TenantIsolationError, match="different tenant"):
            repository._scoped.add(foreign_row)


def test_observation_requires_reproducible_censoring_date() -> None:
    """Still-running records need an explicit as-of date rather than the current clock."""
    install_date = date(2025, 1, 1)
    with pytest.raises(ValueError, match="cannot be before"):
        ObservationDraft(
            external_case_id="case-running",
            pump_model="TEST-PUMP",
            case_inputs_snapshot={},
            selected_configuration={},
            install_date=install_date,
            pull_date=None,
            still_running=True,
            outcome_observed_date=install_date - timedelta(days=1),
            is_failure=False,
            operating_conditions=ObservedOperatingConditions(),
            source=Tracked.measured("telemetry-running"),
        )
