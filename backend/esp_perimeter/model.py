"""Perimeter identity: the nested capsule of framework v0.3 §9.2."""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

LEGACY_TENANT_HEADER = "X-Tenant-Id"

# Perimeter ids become path segments in the storage root, so the character set is
# a security boundary rather than a style preference. Traversal sequences, path
# separators, Windows reserved characters, leading dots and NUL are all outside
# the pattern by construction; there is no sanitising step that could be skipped.
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")

# A single reserved token names the org-level store. It is not a legal perimeter
# id, so an operator can never collide with it.
ORG_STORE_TOKEN = "_org"


class PerimeterViolation(PermissionError):
    """Raised when an operation would cross a capsule boundary (§9.3)."""


class PerimeterLevelError(ValueError):
    """Raised when data is addressed at the wrong capsule level (§9.2)."""


class PerimeterLevel(str, Enum):
    """Which capsule owns a class of data."""

    ORG = "org"
    """Outer capsule: the oilfield service company. Equipment catalog, internal
    engineering standards, fleet. Shared downward to every operator it serves."""

    OPERATOR = "operator"
    """Inner capsule: one operator served by the org. Wells, run-life history,
    teardown findings, derived empirical rules. Never shared sideways."""


class Perimeter(BaseModel):
    """An addressable confidentiality capsule.

    ``operator_id is None`` denotes the org-level capsule. Org level is not a
    wildcard: it cannot read operator data, it only owns the data the org itself
    owns. Reading across the boundary in either direction requires a human
    (§9.3), not a query.
    """

    model_config = ConfigDict(frozen=True)

    org_id: str
    operator_id: str | None = None

    @field_validator("org_id", "operator_id")
    @classmethod
    def _valid_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip().lower()
        if not _TOKEN.match(token):
            raise ValueError(
                f"perimeter id {value!r} is not a valid token: lowercase letters, digits, "
                "'.', '_' and '-' only, must start alphanumeric, max 63 characters"
            )
        return token

    @model_validator(mode="after")
    def _operator_is_not_reserved(self) -> "Perimeter":
        if self.operator_id == ORG_STORE_TOKEN:
            raise ValueError(f"{ORG_STORE_TOKEN!r} is reserved for the org-level store.")
        return self

    # --- identity ---------------------------------------------------------

    @property
    def level(self) -> PerimeterLevel:
        return PerimeterLevel.ORG if self.operator_id is None else PerimeterLevel.OPERATOR

    @property
    def key(self) -> str:
        """Stable string identity, stamped on every row this perimeter owns.

        The stamp is a tripwire for misrouted writes, not the isolation
        mechanism. Isolation is the store selection in
        :mod:`esp_perimeter.store`; if this value were dropped entirely, rows
        would still be unreachable from another perimeter.
        """
        return f"{self.org_id}/{self.operator_id or ORG_STORE_TOKEN}"

    @property
    def org(self) -> "Perimeter":
        """The enclosing org capsule."""
        return Perimeter(org_id=self.org_id)

    def storage_segments(self) -> tuple[str, str]:
        """Path segments for this perimeter's own store. Both are validated tokens."""
        return self.org_id, f"{self.operator_id or ORG_STORE_TOKEN}.db"

    # --- boundaries -------------------------------------------------------

    def contains(self, other: "Perimeter") -> bool:
        """Whether ``other``'s data may be surfaced to a caller in this perimeter.

        An org contains itself and its operators. An operator contains only
        itself -- never a sibling, even under the same org (§9.2). Containment is
        deliberately not symmetric and deliberately not transitive across
        siblings.
        """
        if self.org_id != other.org_id:
            return False
        if self.level is PerimeterLevel.ORG:
            return True
        return self.operator_id == other.operator_id

    def require_operator(self, *, what: str) -> "Perimeter":
        """Assert this perimeter can own operator-level data, or explain why not.

        Well data, run-life history and empirical rules live in the inner capsule
        (§9.2). Accepting them at org level would place one operator's history
        where every operator the org serves could read it.
        """
        if self.level is not PerimeterLevel.OPERATOR:
            raise PerimeterLevelError(
                f"{what} is operator-level data and cannot be addressed at org level. "
                f"Org '{self.org_id}' owns the equipment catalog and internal standards; "
                "wells, run-life history and empirical rules belong to a named operator. "
                "Supply an operator perimeter."
            )
        return self

    def require_contains(self, other: "Perimeter", *, what: str) -> None:
        """Refuse to surface ``other``'s data here, naming both perimeters."""
        if not self.contains(other):
            raise PerimeterViolation(
                f"{what} originates in perimeter '{other.key}' and cannot be surfaced in "
                f"'{self.key}'. A rule inherits the perimeter of the data it was derived "
                "from (§9.3); crossing that boundary requires a human, not a query."
            )

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.key


def parse_perimeter(
    *,
    org_id: str | None,
    operator_id: str | None,
    legacy_tenant_id: str | None = None,
    default_org: str = "demo",
    default_operator: str | None = None,
) -> Perimeter:
    """Build a perimeter from request headers.

    ``legacy_tenant_id`` accepts the flat ``X-Tenant-Id`` of the pre-§9 API. A
    flat tenant is interpreted as an *operator* capsule under ``default_org``,
    because everything the flat API stored -- cases, designs, observations -- was
    operator-level data. Mapping it to org level would silently widen the
    perimeter of existing rows.

    Defaults apply only when the caller names no perimeter at all. Once a caller
    names one, nothing is substituted into it: an explicit ``X-Org-Id`` with no
    operator is the org capsule, not the default operator under that org. This
    rule is deliberately kept here rather than reconstructed at call sites --
    defaulting is what decides which capsule a write lands in, so it has exactly
    one implementation.
    """
    if org_id or operator_id:
        return Perimeter(org_id=(org_id or default_org), operator_id=operator_id)
    if legacy_tenant_id and legacy_tenant_id.strip():
        return Perimeter(org_id=default_org, operator_id=legacy_tenant_id)
    return Perimeter(org_id=default_org, operator_id=default_operator)
