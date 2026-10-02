"""API-level tests for §9.7: the deployment endpoint reports where the LLM
endpoint actually points, and any posture contradiction is visible in the
payload rather than buried in prose.

The classifier tests in ``test_deployment_posture_9_7.py`` cover the pure
function. These tests hold the wiring: that ``/api/deployment`` publishes the
classifier's result correctly and that the payload shape is stable for a
security reviewer to depend on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


ORG = "acme"
OPERATOR_A = {"X-Org-Id": ORG, "X-Operator-Id": "operator-a"}


class FakeLLM:
    """Minimal ``StructuredLLM`` fake so ``ApiService`` constructs a client."""

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema: Mapping[str, Any], schema_name: str) -> str:  # noqa: D401
        raise AssertionError("not called in these tests")


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(storage_root=tmp_path / "p", seed=False, llm=FakeLLM()))


def _setenv(monkeypatch: pytest.MonkeyPatch, **kwargs: str | None) -> None:
    for key, value in kwargs.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_missing_optional_endpoint_names_required_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setenv(
        monkeypatch,
        ESP_AGENTIC_LAYER="on",
        ESP_AGENTIC_MODE=None,
        PPLX_LLM_API_ADDRESS=None,
        ESP_LLM_INSIDE_PERIMETER=None,
    )
    with _client(tmp_path) as client:
        payload = client.get("/api/deployment", headers=OPERATOR_A).json()
    assert payload["agentic_layer"]["model_endpoint"] is None
    channel = payload["egress_channels"][0]
    assert channel["endpoint"] == "PPLX_LLM_API_ADDRESS (not configured)"
    assert channel["endpoint_location"] == "misconfigured"
    assert channel["crosses_perimeter"] is True


def test_endpoint_reports_declared_and_effective_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§9.7: the payload states declared_mode and effective_mode side by side."""
    _setenv(
        monkeypatch,
        ESP_AGENTIC_LAYER="on",
        ESP_AGENTIC_MODE="external_api",
        PPLX_LLM_API_ADDRESS="http://127.0.0.1:11434/v1",
    )
    with _client(tmp_path) as client:
        payload = client.get("/api/deployment", headers=OPERATOR_A).json()
    agentic = payload["agentic_layer"]
    assert agentic["declared_mode"] == "external_api"
    # 127.0.0.1 is inside the perimeter regardless of what was declared.
    assert agentic["effective_mode"] == "local"
    assert agentic["endpoint_location"] == "inside_perimeter"
    assert agentic["posture_contradiction"] is True
    assert agentic["posture_contradiction_reason"]


def test_endpoint_local_model_does_not_report_third_party_vendor_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local endpoint must not print the 'third-party vendor' paragraph.

    That paragraph is written for §9.7 mode 2. A customer running Ollama on
    their own hardware reading that paragraph would be a documentation defect.
    """
    _setenv(
        monkeypatch,
        ESP_AGENTIC_LAYER="on",
        ESP_AGENTIC_MODE=None,
        PPLX_LLM_API_ADDRESS="http://192.168.10.20:8080/v1",
    )
    with _client(tmp_path) as client:
        payload = client.get("/api/deployment", headers=OPERATOR_A).json()
    assert payload["egress_channels"][0]["channel"] == "local_model_endpoint"
    assert payload["egress_channels"][0]["crosses_perimeter"] is False
    assert "third-party" in payload["vendor_access"] or "third party" in payload["vendor_access"]
    # But it must be the LOCAL variant of the prose that names the customer as
    # the operator of the endpoint.
    assert "customer, not the tool developer" in payload["vendor_access"]


def test_endpoint_external_model_reports_egress_and_third_party_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An external endpoint declares egress and a third-party access channel."""
    _setenv(
        monkeypatch,
        ESP_AGENTIC_LAYER="on",
        ESP_AGENTIC_MODE=None,
        PPLX_LLM_API_ADDRESS="https://api.openai.com/v1",
    )
    with _client(tmp_path) as client:
        payload = client.get("/api/deployment", headers=OPERATOR_A).json()
    channel = payload["egress_channels"][0]
    assert channel["channel"] == "external_model_endpoint"
    assert channel["crosses_perimeter"] is True
    assert payload["network_egress_from_design_path"] is True
    assert "third party" in payload["vendor_access"]
    assert "under the customer's enterprise agreement" in payload["vendor_access"]


def test_endpoint_deterministic_mode_reports_no_egress_and_no_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§9.7 mode 3 fallback: no endpoint, no egress, no third-party vendor."""
    _setenv(
        monkeypatch,
        ESP_AGENTIC_LAYER="off",
        # Env still has an endpoint value: this is realistic on a redeploy.
        PPLX_LLM_API_ADDRESS="https://api.openai.com/v1",
    )
    with _client(tmp_path) as client:
        payload = client.get("/api/deployment", headers=OPERATOR_A).json()
    agentic = payload["agentic_layer"]
    assert agentic["enabled"] is False
    assert agentic["effective_mode"] == "deterministic"
    assert agentic["endpoint_location"] == "not_applicable"
    assert agentic["model_endpoint"] is None
    assert payload["egress_channels"] == []
    assert payload["network_egress_from_design_path"] is False
    assert "deterministic" in payload["vendor_access"]


def test_endpoint_publishes_env_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer needs the full list of env vars that change the posture."""
    _setenv(
        monkeypatch,
        ESP_AGENTIC_LAYER="on",
        PPLX_LLM_API_ADDRESS="http://127.0.0.1:11434/v1",
    )
    with _client(tmp_path) as client:
        payload = client.get("/api/deployment", headers=OPERATOR_A).json()
    surface_names = {entry["name"] for entry in payload["agentic_layer"]["config_env_surface"]}
    assert "PPLX_LLM_API_ADDRESS" in surface_names
    assert "ESP_AGENTIC_LAYER" in surface_names
    assert "ESP_AGENTIC_MODE" in surface_names
    assert "ESP_LLM_INSIDE_PERIMETER" in surface_names
