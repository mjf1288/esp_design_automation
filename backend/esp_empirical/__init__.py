"""Tenant-isolated empirical ESP knowledge layer.

This package owns historical installation observations and produces advisory
judgments.  It deliberately does not import an LLM client and never alters the
frozen deterministic calculation package.
"""

from .application import AppliedEmpiricalOverlay, OverlayTarget, apply_rules_as_overlay
from .database import EmpiricalStore, TenantContext, TenantIsolationError, TenantScopeRequiredError
from .models import (
    BiasFlag,
    DerivedEmpiricalRule,
    ObservationDraft,
    ObservedOperatingConditions,
    RuleDerivationRefusal,
    RuleHypothesis,
)
from .repository import ObservationRepository
from .rules import RuleDeriver
from .survival import KaplanMeierEstimate, RunLifeObservation, kaplan_meier

__all__ = [
    "AppliedEmpiricalOverlay",
    "BiasFlag",
    "DerivedEmpiricalRule",
    "EmpiricalStore",
    "KaplanMeierEstimate",
    "ObservationDraft",
    "ObservationRepository",
    "ObservedOperatingConditions",
    "OverlayTarget",
    "RuleDerivationRefusal",
    "RuleDeriver",
    "RuleHypothesis",
    "RunLifeObservation",
    "TenantContext",
    "TenantIsolationError",
    "TenantScopeRequiredError",
    "apply_rules_as_overlay",
    "kaplan_meier",
]
