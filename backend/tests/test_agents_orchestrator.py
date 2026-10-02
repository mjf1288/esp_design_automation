"""Tests that agent orchestration cannot skip or edit deterministic facts."""

from __future__ import annotations

import json
from typing import Any

from esp_agents.intake import IntakeAgent
from esp_agents.narrative import NarrativeAgent
from esp_agents.orchestrator import AgenticOrchestrator
from esp_agents.trust import TrustLevel, TrustPolicy
from esp_engine.models import TaskBranch
from esp_engine.results import DesignResult, FeasibilityVerdict, RunProvenance


class FakeLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def generate_json(self, **_: Any) -> str:
        return json.dumps(self.response)


class RecordingEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.returned: DesignResult | None = None

    def run(self, case: Any) -> DesignResult:
        self.calls += 1
        self.returned = DesignResult(
            design_id="blocked-1",
            case_id=case.metadata.case_id,
            tenant_id=case.metadata.tenant_id,
            verdict=FeasibilityVerdict.BLOCKED_MISSING_DATA,
            verdict_explanation="The case is blocked.",
            branch=case.branch,
            branch_rationale=case.branch_rationale,
            blocking_data_gaps=case.missing_hard_stops(),
            provenance=RunProvenance(
                case_hash="case",
                config_hash="config",
                catalog_version="catalog",
                engine_version="engine",
            ),
        )
        return self.returned


def _intake_without_target() -> dict[str, Any]:
    span = "Casing ID is 6.276 in from 0 to 9,500 ft. The well is vertical."
    return {
        "task_type": "new_well",
        "case": {
            "geometry": {
                "casing_sections": [
                    {
                        "od_in": 7.0,
                        "id_in": 6.276,
                        "top_md_ft": 0.0,
                        "bottom_md_ft": 9500.0,
                    }
                ],
                "is_vertical": True,
            }
        },
        "fields": [
            {
                "path": path,
                "input_class": "constraints",
                "rigidity": "absolute",
                "confidence": 0.9,
                "source_span": span,
                "unit": unit,
                "classification_rationale": "Casing geometry is an absolute constraint.",
            }
            for path, unit in [
                ("geometry.casing_sections[0].od_in", "in"),
                ("geometry.casing_sections[0].id_in", "in"),
                ("geometry.casing_sections[0].top_md_ft", "ft"),
                ("geometry.casing_sections[0].bottom_md_ft", "ft"),
                ("geometry.is_vertical", None),
            ]
        ],
        "could_not_determine": ["target production rate"],
        "decision_gate_rationale": "No previous-installation performance is available.",
    }


def _safe_narrative() -> dict[str, Any]:
    return {
        "fact_summary": "The deterministic result is blocked pending required data.",
        "configuration_facts": [],
        "validity_boundary_facts": [],
        "watch_facts": [],
        "judgments": [],
    }


def test_orchestrator_always_calls_engine_for_a_valid_case_and_preserves_its_output() -> None:
    engine = RecordingEngine()
    orchestrator = AgenticOrchestrator(
        intake_agent=IntakeAgent(FakeLLM(_intake_without_target())),
        engine=engine,
        narrative_agent=NarrativeAgent(FakeLLM(_safe_narrative())),
        trust_policy=TrustPolicy(level=TrustLevel.L1),
    )
    raw = "Casing ID is 6.276 in from 0 to 9,500 ft. The well is vertical."

    outcome = orchestrator.run(raw, case_id="case-orch-1", tenant_id="tenant-a")

    assert engine.calls == 1
    assert outcome.design_result is engine.returned
    assert outcome.design_result is not None
    assert outcome.design_result.verdict is FeasibilityVerdict.BLOCKED_MISSING_DATA
    assert outcome.trust is not None
    assert outcome.trust.requires_engineer_review
    assert not outcome.trust.may_release_autonomously


def test_l3_escalates_nonstandard_branch_b_cases() -> None:
    engine = RecordingEngine()
    orchestrator = AgenticOrchestrator(
        intake_agent=IntakeAgent(FakeLLM(_intake_without_target())),
        engine=engine,
        narrative_agent=NarrativeAgent(FakeLLM(_safe_narrative())),
        trust_policy=TrustPolicy(level=TrustLevel.L3),
    )
    outcome = orchestrator.run(
        "Casing ID is 6.276 in from 0 to 9,500 ft. The well is vertical.",
        case_id="case-orch-2",
        tenant_id="tenant-a",
    )

    assert outcome.design_result is not None
    assert outcome.design_result.branch is TaskBranch.B_NEW
    assert outcome.trust is not None
    assert outcome.trust.requires_engineer_review
    assert any("Branch B" in reason for reason in outcome.trust.escalation_reasons)
