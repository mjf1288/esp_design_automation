"""HTTP-layer tests: deterministic engine outputs, perimeter boundaries, and no LLM network.

Reworked for framework v0.3 §9: requests are scoped by a two-level perimeter with
one physical store per capsule, so the flat ``X-Tenant-Id`` tenant these tests
originally asserted no longer exists as an isolation mechanism. The header is
still accepted and is interpreted as an *operator* under the default org, because
everything the flat API stored was operator-level data.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import (
    _demo_feasible_case,
    _demo_missing_data_case,
    create_app,
)
from esp_empirical.models import ObservationDraft, ObservedOperatingConditions
from esp_engine.provenance import Tracked


class FakeLLM:
    """A schema-aware fake that proves API tests never need a network credential."""

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        if schema_name == "esp_narrative":
            # No numeric tokens: the agent's numeric guard accepts only grounded prose.
            return json.dumps(
                {
                    "fact_summary": "The deterministic design result is available.",
                    "configuration_facts": [],
                    "validity_boundary_facts": [],
                    "watch_facts": [],
                    "judgments": [],
                }
            )
        if schema_name == "esp_intake":
            return json.dumps(
                {
                    "task_type": "new_well",
                    "case": {},
                    "fields": [],
                    "could_not_determine": ["Required structured design inputs were absent."],
                    "decision_gate_rationale": "The request does not include the hard-stop evidence.",
                }
            )
        raise AssertionError(f"Unexpected LLM schema: {schema_name}")


@pytest.fixture
def client(tmp_path: Any) -> TestClient:
    # A real per-perimeter store tree on disk, not an in-memory registry: sync
    # endpoints run in worker threads and SQLite in-memory databases are
    # per-connection, so a file root is what exercises the deployed shape.
    app = create_app(
        storage_root=tmp_path / "perimeters",
        llm=FakeLLM(),
        seed=True,
    )
    with TestClient(app) as test_client:
        yield test_client


def _case_payload(case: Any, case_id: str) -> dict[str, Any]:
    payload = case.model_dump(mode="json")
    payload["metadata"]["case_id"] = case_id
    return payload


def test_design_returns_separate_facts_and_judgments_with_reproducibility(
    client: TestClient,
) -> None:
    headers = {"X-Operator-Id": "operator-a"}
    create = client.post(
        "/api/cases",
        headers=headers,
        json=_case_payload(_demo_feasible_case(), "api-feasible"),
    )
    assert create.status_code == 201
    assert create.json()["perimeter"] == "demo/operator-a"
    # The Case's single isolation string now holds the perimeter key, so a stored
    # Case names the capsule that owns it rather than a flat tenant.
    assert create.json()["case"]["metadata"]["tenant_id"] == "demo/operator-a"

    design = client.post("/api/cases/api-feasible/design?wait=true", headers=headers)
    assert design.status_code == 200
    body = design.json()
    assert body["facts"]["verdict"] == "feasible_with_caveats"
    assert body["facts"]["candidates"]
    assert "judgments" not in body["facts"]
    assert set(body["judgments"]) == {
        "empirical",
        "narrative",
        # Both added by §9: evidence dropped at the presentation boundary is named
        # rather than silently omitted, and a severed narrative layer is stated.
        "narrative_disabled_reason",
        "evidence_refusals",
    }
    assert body["judgments"]["narrative_disabled_reason"] is None
    assert body["judgments"]["evidence_refusals"] == []
    assert all(body["reproducibility"].values())

    stored = client.get(f"/api/designs/{body['id']}", headers=headers)
    assert stored.status_code == 200
    assert stored.json()["facts"] == body["facts"]


def test_refusal_is_a_200_and_perimeter_boundaries_are_enforced(client: TestClient) -> None:
    """Renamed from the flat-tenant version: the boundary is now the perimeter.

    The legacy ``X-Tenant-Id`` header is still exercised here on purpose, because
    it must keep working and must land in an *operator* capsule under the default
    org -- mapping it to org level would widen the perimeter of pre-§9 rows.
    """
    headers = {"X-Tenant-Id": "tenant-a"}
    create = client.post(
        "/api/cases",
        headers=headers,
        json=_case_payload(_demo_missing_data_case(), "api-blocked"),
    )
    assert create.status_code == 201

    refusal = client.post("/api/cases/api-blocked/design?wait=true", headers=headers)
    assert refusal.status_code == 200
    assert refusal.json()["facts"]["verdict"] == "blocked_missing_data"
    assert not refusal.json()["facts"]["candidates"]

    assert client.get("/api/cases/api-blocked", headers={"X-Tenant-Id": "tenant-b"}).status_code == 404
    catalog = client.get("/api/catalog/pumps", headers=headers)
    assert catalog.status_code == 200
    assert catalog.json()["perimeter"] == "demo/tenant-a"
    assert catalog.json()["perimeter_level"] == "operator"
    assert catalog.json()["items"]


def test_intake_and_empirical_endpoints_are_scoped_and_network_free(client: TestClient) -> None:
    headers = {"X-Operator-Id": "operator-a"}
    raw_case = client.post("/api/cases", headers=headers, json={"raw_request": "Please design an ESP."})
    assert raw_case.status_code == 201
    intake = client.post(f"/api/cases/{raw_case.json()['id']}/intake", headers=headers)
    assert intake.status_code == 200
    assert intake.json()["intake"]["attempts"] == 1

    observation = ObservationDraft(
        external_case_id="field-observation-a",
        pump_model="ESP-TEST",
        case_inputs_snapshot={"units": "US field"},
        selected_configuration={"pump_model": "ESP-TEST"},
        install_date=date(2024, 1, 1),
        still_running=True,
        outcome_observed_date=date(2024, 6, 1),
        is_failure=False,
        operating_conditions=ObservedOperatingConditions(),
        source=Tracked.stated("installation report", None, note="field record"),
    )
    added = client.post(
        "/api/empirical/observations",
        headers=headers,
        json=observation.model_dump(mode="json"),
    )
    assert added.status_code == 201
    # The empirical row stamp is the perimeter key: a misrouting tripwire on top
    # of the store selection that already isolated the write.
    assert added.json()["tenant_id"] == "demo/operator-a"

    own_rules = client.get("/api/empirical/rules", headers=headers)
    other_rules = client.get("/api/empirical/rules", headers={"X-Operator-Id": "operator-b"})
    assert own_rules.status_code == 200
    assert other_rules.status_code == 200
    assert own_rules.json()["perimeter"] == "demo/operator-a"
    assert other_rules.json()["perimeter"] == "demo/operator-b"
    assert other_rules.json()["rules"] == []


def test_cors_allows_the_separate_frontend_origin(client: TestClient) -> None:
    response = client.options(
        "/api/catalog",
        headers={
            "Origin": "https://frontend.example.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
