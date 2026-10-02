"""Enforced progressive-trust policy for review and autonomous delivery."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from esp_engine.models import Case, TaskBranch
from esp_engine.results import DesignResult


class TrustLevel(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class TrustDecision(BaseModel):
    """Concrete gates consumed by the orchestrator and UI."""

    model_config = ConfigDict(frozen=True)

    level: TrustLevel
    may_run_engine_unreviewed: bool = True
    may_generate_narrative_unreviewed: bool = True
    requires_engineer_review: bool
    may_release_autonomously: bool
    escalation_reasons: list[str] = Field(default_factory=list)


class TrustPolicy(BaseModel):
    """Framework §11 as executable rules rather than a display-only label.

    Agreement statistics are supplied by the persistence layer through the chosen
    ``level``.  This pure object performs case-by-case routing; it does not claim
    that elapsed time creates trust.
    """

    model_config = ConfigDict(frozen=True)

    level: TrustLevel = TrustLevel.L1
    low_confidence_threshold: float = Field(default=0.70, ge=0.0, le=1.0)

    def decide(self, case: Case, result: DesignResult | None = None) -> TrustDecision:
        reasons = self._nonroutine_reasons(case, result)
        if self.level is TrustLevel.L1:
            return TrustDecision(
                level=self.level,
                requires_engineer_review=True,
                may_release_autonomously=False,
                escalation_reasons=["L1 requires review of every case.", *reasons],
            )
        if self.level is TrustLevel.L2:
            return TrustDecision(
                level=self.level,
                requires_engineer_review=bool(reasons),
                may_release_autonomously=not reasons,
                escalation_reasons=reasons,
            )
        # L3 is deliberately narrower than L2: only a high-confidence, anchored,
        # no-assumption routine case is autonomous. All non-standard cases escalate.
        return TrustDecision(
            level=self.level,
            requires_engineer_review=bool(reasons),
            may_release_autonomously=not reasons,
            escalation_reasons=reasons,
        )

    def _nonroutine_reasons(
        self, case: Case, result: DesignResult | None
    ) -> list[str]:
        reasons: list[str] = []
        if case.missing_hard_stops():
            reasons.append("Required hard-stop data is missing.")
        if case.branch is TaskBranch.B_NEW:
            reasons.append("Branch B has no usable previous-installation anchor.")
        if case.overall_input_confidence < self.low_confidence_threshold:
            reasons.append("A load-bearing input is below the confidence threshold.")
        if case.assumed_fields():
            reasons.append("The case contains an assumption or derived input.")
        if result is not None and not result.is_actionable:
            reasons.append("The deterministic engine did not produce an actionable design.")
        return reasons
