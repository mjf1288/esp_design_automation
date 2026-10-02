"""Apply empirical rules as immutable advisory overlays, never design mutations.

The only bridge to the deterministic package is its existing ``Judgment`` type.
This module returns an overlay object that contains references to fact IDs; it
never returns or mutates a ``DesignResult``.  That shape implements the system
invariant that judgment may annotate a fact but may not replace it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from esp_engine.provenance import Judgment

from .database import TenantIsolationError
from .repository import ObservationRepository


class OverlayTarget(BaseModel):
    """Empirical-match inputs copied from deterministic facts by the API boundary.

    The explicit references prove which immutable computed facts an advisory is
    interpreting.  The empirical layer cannot manufacture a replacement fact.
    """

    model_config = ConfigDict(frozen=True)

    design_id: str
    tenant_id: str
    pump_model: str
    setting_depth_md_ft: float
    gfv_frac: float
    fact_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _must_reference_facts(self) -> "OverlayTarget":
        if not self.fact_ids:
            raise ValueError("An empirical overlay must reference at least one deterministic fact ID.")
        if not 0.0 <= self.gfv_frac <= 1.0:
            raise ValueError("gfv_frac must be expressed as a fraction in [0, 1].")
        return self


class AppliedEmpiricalOverlay(BaseModel):
    """Separate advisory result; contains no writable deterministic facts."""

    model_config = ConfigDict(frozen=True)

    design_id: str
    tenant_id: str
    judgments: tuple[Judgment, ...] = ()
    warnings: tuple[str, ...] = ()


def _severity_for_risk_difference(risk_difference: float) -> str:
    """Presentation band only; the numeric effect remains the evidence of record."""
    if risk_difference >= 0.50:
        return "high"
    if risk_difference >= 0.20:
        return "moderate"
    return "low"


def apply_rules_as_overlay(
    target: OverlayTarget, repository: ObservationRepository
) -> AppliedEmpiricalOverlay:
    """Return judgments that annotate ``target`` without modifying engine output.

    Passing a repository instead of an arbitrary iterable is intentional: the
    function can obtain rules only from the target tenant's scoped repository.
    """
    if target.tenant_id != repository.tenant_id:
        raise TenantIsolationError(
            "Empirical rules cannot be applied across tenant boundaries without an explicit shared-layer product path."
        )

    judgments: list[Judgment] = []
    for rule in repository.list_derived_rules(pump_model=target.pump_model):
        hypothesis = rule.hypothesis
        matches = (
            target.setting_depth_md_ft < hypothesis.setting_depth_below_ft
            and target.gfv_frac >= hypothesis.gfv_at_or_above_frac
        )
        if not matches:
            continue
        judgments.append(
            Judgment(
                judgment_id=f"empirical:{rule.rule_id}:{target.design_id}",
                statement=(
                    f"Advisory empirical judgment only — {rule.statement} "
                    f"It does not alter any deterministic calculation or selection."
                ),
                kind="risk",
                references_facts=list(target.fact_ids),
                confidence=rule.evidence.evidence_strength,
                confidence_basis=rule.evidence.confidence_basis,
                supporting_rule_ids=[rule.rule_id],
                severity=_severity_for_risk_difference(rule.evidence.effect_risk_difference),
            )
        )

    warnings: list[str] = []
    if not judgments:
        warnings.append("No tenant-local empirical rule matched this deterministic fact context.")
    else:
        warnings.append(
            "Empirical output is advisory and is intentionally separate from deterministic facts and candidate ranking."
        )
    return AppliedEmpiricalOverlay(
        design_id=target.design_id,
        tenant_id=target.tenant_id,
        judgments=tuple(judgments),
        warnings=tuple(warnings),
    )
