"""Thin deterministic-first orchestration: intake -> engine -> narrative."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from esp_engine.models import Case
from esp_engine.provenance import Source
from esp_engine.results import DesignResult

from .contracts import IntakeResult, NarrativeResult
from .intake import IntakeAgent
from .narrative import NarrativeAgent
from .trust import TrustDecision, TrustPolicy


class EngineRunner(Protocol):
    """The only engine capability agents are allowed to use."""

    def run(self, case: Case) -> DesignResult:
        """Compute immutable deterministic facts from a validated Case."""


class OrchestrationResult(BaseModel):
    """Unedited engine facts plus separately generated agent outputs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    intake: IntakeResult
    design_result: DesignResult | None = None
    narrative: NarrativeResult | None = None
    trust: TrustDecision | None = None


class AgenticOrchestrator:
    """Coordinates the framework §12 hand-off without penetrating the engine."""

    def __init__(
        self,
        *,
        intake_agent: IntakeAgent,
        engine: EngineRunner,
        narrative_agent: NarrativeAgent,
        trust_policy: TrustPolicy,
    ) -> None:
        self._intake_agent = intake_agent
        self._engine = engine
        self._narrative_agent = narrative_agent
        self._trust_policy = trust_policy

    def run(
        self,
        raw_request: str,
        *,
        case_id: str,
        tenant_id: str,
        source_documents: list[str] | None = None,
        input_source: Source = Source.TEXT_EXTRACTION,
    ) -> OrchestrationResult:
        """Run every valid Case through the engine, including blocked cases.

        The engine call is not conditional on trust level: L1-L3 govern release
        and review, not whether deterministic facts are computed.  If malformed
        LLM output prevents forming a Case, there is deliberately nothing to run.
        """

        intake = self._intake_agent.run(
            raw_request,
            case_id=case_id,
            tenant_id=tenant_id,
            source_documents=source_documents,
            input_source=input_source,
        )
        if intake.case is None:
            return OrchestrationResult(intake=intake)

        case: Case = intake.case
        design_result = self._engine.run(case)
        # Exact identity is preserved. The orchestrator neither copies nor edits
        # deterministic output before handing it to the narrative agent.
        narrative = self._narrative_agent.run(design_result)
        trust = self._trust_policy.decide(case, design_result)
        return OrchestrationResult(
            intake=intake,
            design_result=design_result,
            narrative=narrative,
            trust=trust,
        )
