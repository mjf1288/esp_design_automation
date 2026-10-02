"""One physical store per perimeter (framework v0.3 §9.4).

Tenant-scoped queries against a shared table protect against a *logic* error --
a WHERE clause someone forgot. They do not protect against a missing enforcement
point, because the rows are physically reachable and only a predicate stands
between them and the caller.

This module removes the predicate from the trust path. The database is selected
from the perimeter before any statement exists, so ``SELECT * FROM api_cases``
issued inside operator A's session returns operator A's cases and cannot be
written in a way that returns operator B's. That is the framework's acceptance
criterion: forgetting a check is not technically possible.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .model import Perimeter


class PerimeterStoreRegistry:
    """Lazily opens and caches one SQLAlchemy engine per perimeter.

    ``root=None`` selects in-memory stores, used by tests. Each perimeter still
    gets its own engine, so an in-memory run exercises the same isolation
    property as a deployed one rather than a weaker approximation of it.
    """

    def __init__(
        self,
        root: Path | None,
        *,
        initializer: Callable[[Engine], None] | None = None,
        session_class: type[Session] | None = None,
    ) -> None:
        self._root = Path(root).resolve() if root is not None else None
        self._initializer = initializer
        self._session_class = session_class
        self._engines: dict[str, Engine] = {}
        self._factories: dict[str, sessionmaker] = {}
        self._lock = threading.Lock()

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def is_in_memory(self) -> bool:
        return self._root is None

    # --- store resolution -------------------------------------------------

    def path_for(self, perimeter: Perimeter) -> Path:
        """Resolve this perimeter's database file, refusing to escape the root."""
        if self._root is None:
            raise RuntimeError("In-memory registry has no filesystem path.")
        org_segment, file_segment = perimeter.storage_segments()
        candidate = (self._root / org_segment / file_segment).resolve()
        # Perimeter ids are already restricted to a traversal-free token set. This
        # check is the second, independent guarantee: if the token pattern were
        # ever loosened, a path outside the deployment root still cannot be opened.
        if not candidate.is_relative_to(self._root):
            raise ValueError(
                f"Perimeter '{perimeter.key}' resolves outside the storage root; refusing to open it."
            )
        return candidate

    def url_for(self, perimeter: Perimeter) -> str:
        if self._root is None:
            return "sqlite+pysqlite:///:memory:"
        return f"sqlite+pysqlite:///{self.path_for(perimeter)}"

    def engine_for(self, perimeter: Perimeter) -> Engine:
        key = perimeter.key
        with self._lock:
            engine = self._engines.get(key)
            if engine is not None:
                return engine
            if self._root is not None:
                path = self.path_for(perimeter)
                path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                self.url_for(perimeter),
                future=True,
                connect_args={"check_same_thread": False},
            )
            if self._initializer is not None:
                self._initializer(engine)
            self._engines[key] = engine
            return engine

    def _factory_for(self, perimeter: Perimeter) -> sessionmaker:
        key = perimeter.key
        with self._lock:
            factory = self._factories.get(key)
        if factory is not None:
            return factory
        engine = self.engine_for(perimeter)
        kwargs = {"bind": engine, "expire_on_commit": False, "future": True}
        if self._session_class is not None:
            kwargs["class_"] = self._session_class
        factory = sessionmaker(**kwargs)
        with self._lock:
            self._factories[key] = factory
        return factory

    def raw_session(self, perimeter: Perimeter) -> Session:
        """A session already bound to this perimeter's store, uncommitted."""
        return self._factory_for(perimeter)()

    @contextmanager
    def session(self, perimeter: Perimeter) -> Iterator[Session]:
        session = self.raw_session(perimeter)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # --- org-level metadata ----------------------------------------------

    def operators_of(self, org: Perimeter) -> list[Perimeter]:
        """Operators with a store under ``org``.

        This is org-level metadata: the service company legitimately knows which
        operators it serves. It deliberately exposes no operator *data*, and
        there is no equivalent listing across orgs.
        """
        if self._root is None:
            with self._lock:
                keys = sorted(self._engines)
            opened = [_from_key(key) for key in keys]
            return [p for p in opened if p.org_id == org.org_id and p.operator_id is not None]
        org_dir = (self._root / org.org_id).resolve()
        if not org_dir.is_dir():
            return []
        found = []
        for child in sorted(org_dir.glob("*.db")):
            stem = child.stem
            if stem.startswith("_"):
                continue
            found.append(Perimeter(org_id=org.org_id, operator_id=stem))
        return found

    def dispose(self) -> None:
        with self._lock:
            for engine in self._engines.values():
                engine.dispose()
            self._engines.clear()
            self._factories.clear()


def _from_key(key: str) -> Perimeter:
    org, _, operator = key.partition("/")
    return Perimeter(org_id=org, operator_id=None if operator in {"", "_org"} else operator)
