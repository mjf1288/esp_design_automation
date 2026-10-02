"""Tests for the bounded unstructured-input intake agent."""

from __future__ import annotations

import json
from typing import Any

from esp_agents.intake import IntakeAgent
from esp_engine.models import TaskBranch
from esp_engine.provenance import BiasDirection, Source


class FakeLLM:
    """A deterministic structured-output fake; no test contacts a model service."""

    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def generate_json(self, **_: Any) -> str:
        self.calls += 1
        response = next(self.responses)
        return response if isinstance(response, str) else json.dumps(response)


RAW = (
    "New well Alpha-1: target 1,200 bpd. Run 7.0 in OD casing with 6.276 in ID "
    "from 0 to 9,500 ft. The well is vertical."
)


def _field(
    path: str,
    span: str,
    *,
    unit: str | None = None,
    input_class: str = "constraints",
    rigidity: str | None = "absolute",
) -> dict[str, Any]:
    return {
        "path": path,
        "input_class": input_class,
        "rigidity": rigidity,
        "confidence": 0.81,
        "source_span": span,
        "unit": unit,
        "classification_rationale": "Explicitly stated in the customer request.",
    }


def _geometry_case() -> dict[str, Any]:
    casing_span = "Run 7.0 in OD casing with 6.276 in ID from 0 to 9,500 ft."
    return {
        "expectations": {
            "target_rate_bpd": {
                "value": 1200.0,
                "unit": "bpd",
                "source": "text_extraction",
                "confidence": 0.81,
                "extracted_from": "target 1,200 bpd",
            }
        },
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
        },
    }


def _valid_response() -> dict[str, Any]:
    casing_span = "Run 7.0 in OD casing with 6.276 in ID from 0 to 9,500 ft."
    fields = [
        _field(
            "expectations.target_rate_bpd",
            "target 1,200 bpd",
            unit="bpd",
            input_class="expectations",
            rigidity=None,
        ),
        _field("geometry.casing_sections[0].od_in", casing_span, unit="in"),
        _field("geometry.casing_sections[0].id_in", casing_span, unit="in"),
        _field("geometry.casing_sections[0].top_md_ft", casing_span, unit="ft"),
        _field("geometry.casing_sections[0].bottom_md_ft", casing_span, unit="ft"),
        _field("geometry.is_vertical", "The well is vertical."),
    ]
    return {
        "task_type": "new_well",
        "case": _geometry_case(),
        "fields": fields,
        "could_not_determine": ["Reservoir pressure", "GOR"],
        "decision_gate_rationale": "The request identifies a new well without an installation history.",
    }


def test_intake_builds_case_with_evidence_classification_and_hard_stop_check() -> None:
    result = IntakeAgent(FakeLLM([_valid_response()])).run(
        RAW, case_id="case-intake-1", tenant_id="tenant-a"
    )

    assert result.is_valid
    assert result.case is not None
    assert result.case.branch is TaskBranch.B_NEW
    assert result.case.expectations.target_rate_bpd is not None
    assert result.case.expectations.target_rate_bpd.source is Source.TEXT_EXTRACTION
    assert result.case.expectations.target_rate_bpd.extracted_from == "target 1,200 bpd"
    assert result.case.constraints.electrical.vsd_available.source is Source.ASSUMPTION
    assert result.blocking_data_requests == []
    assert result.field_confidence["expectations.target_rate_bpd"] == 0.81
    assert {field.input_class.value for field in result.fields} == {
        "expectations",
        "constraints",
    }
    assert "GOR" in result.could_not_determine


def test_intake_reports_missing_hard_stop_instead_of_assuming_target_rate() -> None:
    response = _valid_response()
    response["case"].pop("expectations")
    response["fields"] = [
        field
        for field in response["fields"]
        if field["path"] != "expectations.target_rate_bpd"
    ]
    result = IntakeAgent(FakeLLM([response])).run(
        RAW, case_id="case-intake-2", tenant_id="tenant-a"
    )

    assert result.is_valid
    assert result.case is not None
    assert not result.case.is_calculable
    assert any("target total-liquid" in request for request in result.blocking_data_requests)
    assert any("target production rate" in item for item in result.could_not_determine)


def test_intake_rejects_an_assumed_target_rate() -> None:
    response = _valid_response()
    tracked = response["case"]["expectations"]["target_rate_bpd"]
    tracked.update(
        {
            "source": "assumption",
            "extracted_from": None,
            "assumption": {
                "basis": "regional practice",
                "bias": "neutral",
                "rationale": "placeholder",
                "unbiased_value": 1200.0,
            },
        }
    )
    result = IntakeAgent(FakeLLM([response])).run(
        RAW, case_id="case-intake-3", tenant_id="tenant-a"
    )

    assert result.case is None
    assert any(issue.code == "assumed_hard_stop" for issue in result.issues)


def test_intake_preserves_the_caller_selected_report_source() -> None:
    response = _valid_response()
    response["case"]["expectations"]["target_rate_bpd"]["source"] = "report"
    result = IntakeAgent(FakeLLM([response])).run(
        RAW,
        case_id="case-intake-report",
        tenant_id="tenant-a",
        input_source=Source.REPORT,
    )

    assert result.is_valid
    assert result.case is not None
    assert result.case.expectations.target_rate_bpd is not None
    assert result.case.expectations.target_rate_bpd.source is Source.REPORT


def test_intake_enforces_asymmetric_gor_assumption() -> None:
    response = _valid_response()
    response["case"]["fluid"] = {
        "gor_scf_stb": {
            "value": 800.0,
            "unit": "scf/stb",
            "source": "assumption",
            "confidence": 0.3,
            "assumption": {
                "basis": "regional prior",
                "bias": "upward",
                "rationale": "Gas underestimation can prevent an ESP installation from working.",
                "unbiased_value": 550.0,
                "policy_id": "gor_gassy",
                "scenario_swept": False,
            },
        }
    }
    response["case"]["complications"] = {
        "items": [
            {
                "type": "gas",
                "severity": "severe",
                "evidence": {
                    "value": "gassy well",
                    "source": "text_extraction",
                    "confidence": 0.81,
                    "extracted_from": "New well Alpha-1",
                },
            }
        ]
    }
    response["fields"].extend(
        [
            _field(
                "fluid.gor_scf_stb",
                "New well Alpha-1",
                unit="scf/stb",
                input_class="complications",
                rigidity=None,
            ),
            _field(
                "complications.items[0].type",
                "New well Alpha-1",
                input_class="complications",
                rigidity=None,
            ),
            _field(
                "complications.items[0].severity",
                "New well Alpha-1",
                input_class="complications",
                rigidity=None,
            ),
            _field(
                "complications.items[0].evidence",
                "New well Alpha-1",
                input_class="complications",
                rigidity=None,
            ),
        ]
    )
    result = IntakeAgent(FakeLLM([response])).run(
        RAW, case_id="case-intake-4", tenant_id="tenant-a"
    )

    assert result.is_valid
    assert result.case is not None
    gor = result.case.fluid.gor_scf_stb
    assert gor is not None and gor.assumption is not None
    assert gor.assumption.bias is BiasDirection.UPWARD
    assert gor.assumption.unbiased_value == 550.0


def test_malformed_intake_is_retried_then_returned_as_a_normal_result() -> None:
    llm = FakeLLM(["not-json", "still-not-json"])
    result = IntakeAgent(llm, max_attempts=2).run(
        RAW, case_id="case-intake-5", tenant_id="tenant-a"
    )

    assert result.case is None
    assert result.attempts == 2
    assert llm.calls == 2
    assert len(result.issues) == 2
