"""SQLAlchemy 2.x persistence schema for the empirical layer.

All tenant-owned tables use string UUID primary keys and a non-null, indexed
``tenant_id``.  Generic SQLAlchemy ``String``, ``Numeric``, ``Date``, and
``JSON`` types keep the SQLite development schema portable to PostgreSQL.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, Date, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata owned only by the empirical package."""


class ObservationRow(Base):
    """Installed ESP outcome, including the explicit right-censoring state."""

    __tablename__ = "empirical_observations"
    __table_args__ = (
        Index("ix_empirical_observations_tenant_pump", "tenant_id", "pump_model"),
        Index("ix_empirical_observations_tenant_field", "tenant_id", "field_id"),
    )

    observation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    external_case_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pump_model: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pump_series: Mapped[str | None] = mapped_column(String(255), nullable=True)
    region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    field_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    formation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fluid_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    case_inputs_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    selected_configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    install_date: Mapped[date] = mapped_column(Date, nullable=False)
    pull_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    still_running: Mapped[bool] = mapped_column(Boolean, nullable=False)
    outcome_observed_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_failure: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_mode: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    teardown_findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_life_days: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    operating_conditions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    engineer_commentary: Mapped[str | None] = mapped_column(Text, nullable=True)
    entered_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DerivedRuleRow(Base):
    """Tenant-scoped, reproducible rule output; direct hand-authored rules have no row type."""

    __tablename__ = "empirical_derived_rules"
    __table_args__ = (Index("ix_empirical_rules_tenant_pump", "tenant_id", "pump_model"),)

    rule_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pump_model: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    supporting_observation_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    bias_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    bias_explanations: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    survival_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
