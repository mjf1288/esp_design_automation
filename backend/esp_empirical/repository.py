"""Only supported repository for tenant-owned observation and rule data."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .database import TenantScopedSession
from .models import DerivedEmpiricalRule, EmpiricalObservation, ObservationDraft
from .orm import DerivedRuleRow, ObservationRow


def _jsonable(model: Any) -> Any:
    """Serialize Pydantic values with dates and enums in a portable JSON shape."""
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def _observation_from_row(row: ObservationRow) -> EmpiricalObservation:
    data = {
        "observation_id": row.observation_id,
        "tenant_id": row.tenant_id,
        "external_case_id": row.external_case_id,
        "pump_model": row.pump_model,
        "manufacturer": row.manufacturer,
        "pump_series": row.pump_series,
        "region": row.region,
        "field_id": row.field_id,
        "formation": row.formation,
        "fluid_type": row.fluid_type,
        "case_inputs_snapshot": row.case_inputs_snapshot,
        "selected_configuration": row.selected_configuration,
        "install_date": row.install_date,
        "pull_date": row.pull_date,
        "still_running": row.still_running,
        "outcome_observed_date": row.outcome_observed_date,
        "is_failure": row.is_failure,
        "failure_mode": row.failure_mode,
        "failure_location": row.failure_location,
        "teardown_findings": row.teardown_findings,
        "operating_conditions": row.operating_conditions,
        "source": row.source,
        "engineer_commentary": row.engineer_commentary,
        "entered_by": row.entered_by,
    }
    return EmpiricalObservation.model_validate(data)


def _rule_from_row(row: DerivedRuleRow) -> DerivedEmpiricalRule:
    return DerivedEmpiricalRule.model_validate(
        {
            "rule_id": row.rule_id,
            "tenant_id": row.tenant_id,
            "statement": row.statement,
            "hypothesis": row.hypothesis,
            "supporting_observation_ids": row.supporting_observation_ids,
            "evidence": row.evidence,
            "bias_flags": row.bias_flags,
            "bias_explanations": row.bias_explanations,
            "survival_summary": row.survival_summary,
        }
    )


class ObservationRepository:
    """Tenant-bound operations; construction requires a scoped session."""

    __slots__ = ("_scoped",)

    def __init__(self, scoped: TenantScopedSession) -> None:
        self._scoped = scoped

    @property
    def tenant_id(self) -> str:
        return self._scoped.context.tenant_id

    def add_observation(self, draft: ObservationDraft) -> EmpiricalObservation:
        row = ObservationRow(
            tenant_id=self.tenant_id,
            external_case_id=draft.external_case_id,
            pump_model=draft.pump_model,
            manufacturer=draft.manufacturer,
            pump_series=draft.pump_series,
            region=draft.region,
            field_id=draft.field_id,
            formation=draft.formation,
            fluid_type=draft.fluid_type,
            case_inputs_snapshot=draft.case_inputs_snapshot,
            selected_configuration=draft.selected_configuration,
            install_date=draft.install_date,
            pull_date=draft.pull_date,
            still_running=draft.still_running,
            outcome_observed_date=draft.outcome_observed_date,
            is_failure=draft.is_failure,
            failure_mode=draft.failure_mode,
            failure_location=draft.failure_location,
            teardown_findings=draft.teardown_findings,
            run_life_days=Decimal(draft.run_life_days),
            operating_conditions=_jsonable(draft.operating_conditions),
            source=_jsonable(draft.source),
            engineer_commentary=draft.engineer_commentary,
            entered_by=draft.entered_by,
        )
        self._scoped.add(row)
        self._scoped.flush()
        return _observation_from_row(row)

    def list_observations(self, *, pump_model: str | None = None) -> list[EmpiricalObservation]:
        return [_observation_from_row(row) for row in self._scoped.observations(pump_model=pump_model)]

    def get_observation(self, observation_id: str) -> EmpiricalObservation | None:
        row = self._scoped.observation_by_id(observation_id)
        return _observation_from_row(row) if row is not None else None

    def save_derived_rule(self, rule: DerivedEmpiricalRule) -> DerivedEmpiricalRule:
        if rule.tenant_id != self.tenant_id:
            # A rule can only be saved in the dataset that supplied its evidence.
            raise PermissionError("Cannot save a derived rule in another tenant's dataset.")
        row = DerivedRuleRow(
            rule_id=rule.rule_id,
            tenant_id=rule.tenant_id,
            pump_model=rule.hypothesis.pump_model,
            statement=rule.statement,
            hypothesis=_jsonable(rule.hypothesis),
            supporting_observation_ids=list(rule.supporting_observation_ids),
            evidence=_jsonable(rule.evidence),
            bias_flags=[flag.value for flag in rule.bias_flags],
            bias_explanations={flag.value: text for flag, text in rule.bias_explanations.items()},
            survival_summary=rule.survival_summary,
        )
        self._scoped.add(row)
        self._scoped.flush()
        return _rule_from_row(row)

    def list_derived_rules(self, *, pump_model: str | None = None) -> list[DerivedEmpiricalRule]:
        return [_rule_from_row(row) for row in self._scoped.rules(pump_model=pump_model)]
