"""Tenant-scoped SQLAlchemy access with a fail-closed query guard.

``with_loader_criteria`` is documented by SQLAlchemy as adding WHERE criteria to
all occurrences of an entity, including subqueries, joins, and relationship
loads: https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html .  This module
uses it as a second line of defense; the public API exposes only a scoped wrapper
that has no unscoped ``execute`` method.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from .orm import Base, DerivedRuleRow, ObservationRow

if TYPE_CHECKING:  # pragma: no cover
    from esp_perimeter import PerimeterStoreRegistry


class TenantScopeRequiredError(PermissionError):
    """Raised before a tenant-owned query can execute without a tenant identity."""


class TenantIsolationError(PermissionError):
    """Raised when a caller tries to write a row owned by another tenant."""


class TenantContext(BaseModel):
    """Identity required for every empirical repository session."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str

    @field_validator("tenant_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tenant_id must be non-empty.")
        return value

    @classmethod
    def for_perimeter(cls, perimeter: Any) -> "TenantContext":
        """Build a context from an :class:`esp_perimeter.Perimeter`.

        The row stamp is the perimeter key. It is a misrouting tripwire, not the
        isolation mechanism -- the store this context is opened against was
        already selected by perimeter (framework v0.3 §9.4).
        """
        return cls(tenant_id=perimeter.key)


class _GuardedSession(Session):
    """Session implementation used only inside a ``TenantScopedSession``."""


@event.listens_for(_GuardedSession, "do_orm_execute")
def _require_tenant_criteria(execute_state: Any) -> None:
    """Fail closed and inject scope even if future repository code omits WHERE.

    SQLAlchemy's event hook is deliberately attached to this private session
    subclass instead of the application's global Session class, so importing the
    empirical package cannot change query behavior in the frozen engine.
    """
    if not execute_state.is_select:
        return
    tenant_id = execute_state.session.info.get("esp_empirical_tenant_id")
    if tenant_id is None:
        raise TenantScopeRequiredError(
            "Empirical SELECT blocked: a TenantContext is required before querying tenant data."
        )
    # Binding a literal value avoids relying on lambda closure caching while still
    # ensuring every ORM load of either tenant-owned entity receives its scope.
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            ObservationRow,
            ObservationRow.tenant_id == tenant_id,
            include_aliases=True,
            propagate_to_loaders=True,
        ),
        with_loader_criteria(
            DerivedRuleRow,
            DerivedRuleRow.tenant_id == tenant_id,
            include_aliases=True,
            propagate_to_loaders=True,
        ),
    )


class TenantScopedSession:
    """Narrow persistence capability bound to one immutable ``TenantContext``.

    The underlying SQLAlchemy Session is intentionally private and this wrapper
    provides no generic ``execute``/``scalars`` methods.  Consequently all public
    empirical query paths start with a tenant scope rather than hoping a caller
    remembers a filter.
    """

    __slots__ = ("__session", "context")

    def __init__(self, session: _GuardedSession, context: TenantContext) -> None:
        self.__session = session
        self.context = context
        self.__session.info["esp_empirical_tenant_id"] = context.tenant_id

    def add(self, row: ObservationRow | DerivedRuleRow) -> None:
        if row.tenant_id != self.context.tenant_id:
            raise TenantIsolationError(
                "Cannot write an empirical row for a different tenant through this session."
            )
        self.__session.add(row)

    def flush(self) -> None:
        self.__session.flush()

    def commit(self) -> None:
        self.__session.commit()

    def rollback(self) -> None:
        self.__session.rollback()

    def observations(self, *, pump_model: str | None = None) -> list[ObservationRow]:
        from sqlalchemy import select

        statement = select(ObservationRow).order_by(ObservationRow.install_date, ObservationRow.observation_id)
        if pump_model is not None:
            statement = statement.where(ObservationRow.pump_model == pump_model)
        # The statement deliberately does not carry a tenant WHERE clause. The
        # guarded Session adds the mandatory global criterion, proving omission at
        # call sites cannot become a cross-tenant read.
        return list(self.__session.scalars(statement))

    def observation_by_id(self, observation_id: str) -> ObservationRow | None:
        from sqlalchemy import select

        return self.__session.scalar(
            select(ObservationRow).where(ObservationRow.observation_id == observation_id)
        )

    def rules(self, *, pump_model: str | None = None) -> list[DerivedRuleRow]:
        from sqlalchemy import select

        statement = select(DerivedRuleRow).order_by(DerivedRuleRow.rule_id)
        if pump_model is not None:
            statement = statement.where(DerivedRuleRow.pump_model == pump_model)
        return list(self.__session.scalars(statement))


class EmpiricalStore:
    """Database boundary that only opens repositories with a ``TenantContext``."""

    def __init__(self, database_url: str = "sqlite+pysqlite:///:memory:") -> None:
        self._engine: Engine = create_engine(database_url, future=True)
        self._session_factory = sessionmaker(
            bind=self._engine,
            class_=_GuardedSession,
            expire_on_commit=False,
            future=True,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self._engine)

    @contextmanager
    def for_tenant(self, context: TenantContext) -> Iterator["ObservationRepository"]:
        """Open the only supported empirical query capability for ``context``."""
        from .repository import ObservationRepository

        raw_session = self._session_factory()
        scoped = TenantScopedSession(raw_session, context)
        repository = ObservationRepository(scoped)
        try:
            yield repository
            scoped.commit()
        except Exception:
            scoped.rollback()
            raise
        finally:
            raw_session.close()


class PerimeterEmpiricalStores:
    """One :class:`EmpiricalStore` per perimeter, selected before any query.

    The pre-§9 layer already refused unscoped SELECTs via a fail-closed session
    guard, which is a strong defence against a forgotten WHERE clause. It is not
    a defence against a missing enforcement point: the rows of every operator
    still lived in one file, so the guard was the only thing between them.

    Here the store is chosen from the perimeter, and the session guard remains on
    top of it. Two independent mechanisms now have to fail before one operator's
    run-life history can reach another.
    """

    def __init__(self, registry: "PerimeterStoreRegistry") -> None:
        self._registry = registry
        self._stores: dict[str, EmpiricalStore] = {}
        self._lock = threading.Lock()

    def store_for(self, perimeter: Any) -> EmpiricalStore:
        key = perimeter.key
        with self._lock:
            store = self._stores.get(key)
            if store is not None:
                return store
        store = EmpiricalStore(self._registry.url_for(perimeter))
        store.create_schema()
        with self._lock:
            self._stores.setdefault(key, store)
            return self._stores[key]

    @contextmanager
    def for_perimeter(self, perimeter: Any) -> Iterator[Any]:
        """Open the empirical repository for ``perimeter``'s own store."""
        store = self.store_for(perimeter)
        with store.for_tenant(TenantContext.for_perimeter(perimeter)) as repository:
            yield repository
