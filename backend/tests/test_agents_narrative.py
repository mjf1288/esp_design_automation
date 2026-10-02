"""Tests for numeric grounding and fact/judgment separation in narratives."""

from __future__ import annotations

import json
from typing import Any

from esp_agents.contracts import NarrativeLLMResponse
from esp_agents.narrative import NarrativeAgent, validate_narrative_numbers
from esp_engine.models import OperatingZone, TaskBranch
from esp_engine.results import (
    CandidateResult,
    CellResult,
    DesignResult,
    EnvelopeCase,
    FeasibilityVerdict,
    PumpConfiguration,
    RunProvenance,
    ScoreBreakdown,
    ValidityBoundary,
)


class FakeLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def generate_json(self, **_: Any) -> str:
        self.calls += 1
        return json.dumps(next(self.responses))


def _result() -> DesignResult:
    cell = CellResult(
        config_id="cfg-1",
        point_id="base-0",
        month=0.0,
        envelope=EnvelopeCase.BASE,
        pip_psi=1200.0,
        pwf_psi=1150.0,
        intake_temp_f=185.0,
        tdh_ft=6800.0,
        total_fluid_intake_bpd=1200.0,
        liquid_rate_bpd=1200.0,
        mixture_sg=0.9,
        free_gas_fraction_at_intake=0.12,
        free_gas_fraction_entering_pump=0.08,
        gas_strategy="gas_handler",
        head_degradation_factor=0.95,
        turpin_stable=True,
        head_developed_ft=7000.0,
        head_required_ft=6800.0,
        head_margin_frac=0.03,
        efficiency_frac=0.67,
        bhp_hp=90.0,
        zone=OperatingZone.OPERATING_RANGE,
        q_over_qbep=1.02,
        distance_from_bep_frac=0.02,
        zone_severity=0.02,
    )
    candidate = CandidateResult(
        rank=1,
        configuration=PumpConfiguration(
            config_id="cfg-1",
            pump_id="pump-1",
            pump_model="Pump-X",
            manufacturer="Vendor",
            series=400,
            stages=120,
            frequency_hz=60.0,
            setting_depth_md_ft=8000.0,
        ),
        score=ScoreBreakdown(
            total_score=0.88,
            objective="max_coverage",
            zone_quality_score=0.85,
            time_coverage_frac=0.9,
            bep_time_frac=0.5,
            efficiency_avg_frac=0.67,
            envelope_robustness=0.8,
        ),
        validity=ValidityBoundary(
            valid_until_months=18.0,
            limiting_mechanism="rate decline leads to downthrust",
            limiting_zone=OperatingZone.DOWNTHRUST,
            horizon_months=24.0,
        ),
        design_point=cell,
    )
    return DesignResult(
        design_id="design-1",
        case_id="case-1",
        tenant_id="tenant-1",
        verdict=FeasibilityVerdict.FEASIBLE_WITH_CAVEATS,
        verdict_explanation="A candidate passed with caveats.",
        branch=TaskBranch.B_NEW,
        branch_rationale="No usable history exists.",
        candidates=[candidate],
        provenance=RunProvenance(
            case_hash="casehash",
            config_hash="confighash",
            catalog_version="catalog",
            engine_version="engine",
        ),
    )


def _narrative(summary: str) -> dict[str, Any]:
    return {
        "fact_summary": summary,
        "configuration_facts": [],
        "validity_boundary_facts": [],
        "watch_facts": [],
        "judgments": [
            {
                "statement": "Monitor the reported deterministic operating boundary.",
                "references_facts": ["candidate_1.valid_until_months"],
                "confidence": 0.8,
            }
        ],
    }


def test_numeric_guard_catches_injected_fake_engineering_number() -> None:
    narrative = NarrativeLLMResponse.model_validate(
        _narrative("The configuration needs 9999 psi at intake.")
    )

    violations = validate_narrative_numbers(narrative, _result())

    assert [violation.token for violation in violations] == ["9999"]


def test_narrative_retries_unsafe_number_and_only_returns_safe_structured_sections() -> None:
    llm = FakeLLM(
        [
            _narrative("This pump will last 9999 months."),
            _narrative("The selected configuration has a deterministic validity boundary."),
        ]
    )
    output = NarrativeAgent(llm, max_attempts=2).run(_result())

    assert output.status == "ok"
    assert output.narrative is not None
    assert output.attempts == 2
    assert llm.calls == 2
    assert output.narrative.judgments


def test_narrative_refuses_after_repeated_fake_number() -> None:
    llm = FakeLLM(
        [
            _narrative("This pump will last 9999 months."),
            _narrative("This pump will last 9999 months."),
        ]
    )
    output = NarrativeAgent(llm, max_attempts=2).run(_result())

    assert output.status == "refused"
    assert output.narrative is None
    assert output.numeric_violations[0].token == "9999"
