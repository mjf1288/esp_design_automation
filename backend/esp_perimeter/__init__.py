"""Structural confidentiality perimeters (framework v0.3 §9).

The framework distinguishes two ways to isolate data and accepts only one of
them (§9.4):

    access check         ``if user.can_see(data)``   one forgotten check leaks
    structural isolation separate stores per capsule  omission is not expressible

This package implements the second. A :class:`Perimeter` names a capsule, and
:class:`PerimeterStoreRegistry` gives each capsule its own physical database.
Because the store is selected by perimeter *before* a statement is built, a
query issued inside one capsule has no syntax available to name another
capsule's rows -- there is no discriminator column to forget a filter on.

The capsules are nested, not flat (§9.2):

    org (oilfield service company)   equipment catalog, internal standards, fleet
      +-- operator A                 wells, run-life history, empirical rules
      +-- operator B                 wells, run-life history, empirical rules

Sibling operators never merge, even for one engineer employed by the org
(§9.2). The tool developer has no capsule at all: nothing in this package can
open a store outside the deployment's own storage root, and no code path exports
one.
"""

from .model import (
    LEGACY_TENANT_HEADER,
    Perimeter,
    PerimeterLevel,
    PerimeterLevelError,
    PerimeterViolation,
    parse_perimeter,
)
from .store import PerimeterStoreRegistry

__all__ = [
    "LEGACY_TENANT_HEADER",
    "Perimeter",
    "PerimeterLevel",
    "PerimeterLevelError",
    "PerimeterStoreRegistry",
    "PerimeterViolation",
    "parse_perimeter",
]
