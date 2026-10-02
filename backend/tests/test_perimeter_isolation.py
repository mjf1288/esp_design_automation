"""Acceptance tests for the two-level perimeter of framework v0.3 §9.

The load-bearing test here is not that a sibling operator gets a 404. It is that
a **raw, unscoped** ``SELECT * FROM api_cases`` executed against operator B's
store returns none of operator A's rows. §9.4 requires that forgetting an access
check be technically impossible rather than merely unlikely, and a test that
asserts isolation through a WHERE clause can only ever demonstrate that the
clause was remembered this time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.app import _demo_feasible_case, create_app
from esp_empirical.models import (
    BiasFlag,
    DerivedEmpiricalRule,
    RuleEvidence,
    RuleHypothesis,
)
from esp_perimeter import Perimeter, PerimeterStoreRegistry

ORG = "acme-esp"
OPERATOR_A = {"X-Org-Id": ORG, "X-Operator-Id": "operator-a"}
OPERATOR_B = {"X-Org-Id": ORG, "X-Operator-Id": "operator-b"}
ORG_LEVEL = {"X-Org-Id": ORG}


class FakeLLM:
    """Present so agentic-on tests never need a credential or a network call."""

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema: Any, schema_name: str) -> str:
        if schema_name == "esp_narrative":
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


def _app(tmp_path: Path, **kwargs: Any):
    # File-backed stores: sync endpoints run in worker threads, and a SQLite
    # in-memory database belongs to a single connection.
    return create_app(storage_root=tmp_path / "perimeters", seed=False, **kwargs)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    with TestClient(_app(tmp_path, llm=FakeLLM())) as test_client:
        yield test_client


def _create_case(client: TestClient, headers: dict[str, str], case_id: str) -> str:
    payload = _demo_feasible_case().model_dump(mode="json")
    payload["metadata"]["case_id"] = case_id
    response = client.post("/api/cases", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- §9.4: structural isolation, asserted without a WHERE clause -------------


def test_sibling_operator_cannot_see_a_case_and_its_raw_store_does_not_hold_it(
    client: TestClient,
) -> None:
    _create_case(client, OPERATOR_A, "operator-a-well")

    assert client.get("/api/cases/operator-a-well", headers=OPERATOR_B).status_code == 404

    registry: PerimeterStoreRegistry = client.app.state.service.registry
    perimeter_b = Perimeter(org_id=ORG, operator_id="operator-b")
    with registry.engine_for(perimeter_b).connect() as connection:
        # No predicate of any kind. This is the §9.4 acceptance criterion: the
        # statement has no syntax available to name operator A's rows, because
        # they are not in this database at all.
        rows = connection.execute(text("SELECT * FROM api_cases")).mappings().all()
    assert rows == []

    perimeter_a = Perimeter(org_id=ORG, operator_id="operator-a")
    with registry.engine_for(perimeter_a).connect() as connection:
        rows_a = connection.execute(text("SELECT case_id, perimeter_key FROM api_cases")).mappings().all()
    assert [row["case_id"] for row in rows_a] == ["operator-a-well"]
    # The stamp exists only so a misrouted store can be detected on read.
    assert rows_a[0]["perimeter_key"] == "acme-esp/operator-a"


def test_each_perimeter_gets_its_own_database_file(client: TestClient) -> None:
    _create_case(client, OPERATOR_A, "operator-a-well")
    _create_case(client, OPERATOR_B, "operator-b-well")

    registry: PerimeterStoreRegistry = client.app.state.service.registry
    path_a = registry.path_for(Perimeter(org_id=ORG, operator_id="operator-a"))
    path_b = registry.path_for(Perimeter(org_id=ORG, operator_id="operator-b"))
    assert path_a != path_b
    assert path_a.is_file() and path_b.is_file()


def test_observations_and_rules_are_isolated_per_operator(client: TestClient) -> None:
    observation = {
        "external_case_id": "a-1",
        "pump_model": "ESP-TEST",
        "case_inputs_snapshot": {"units": "US field"},
        "selected_configuration": {"pump_model": "ESP-TEST"},
        "install_date": "2024-01-01",
        "still_running": True,
        "outcome_observed_date": "2024-06-01",
        "is_failure": False,
        "operating_conditions": {},
        "source": {"value": "installation report", "unit": None, "source": "report", "confidence": 0.9},
    }
    added = client.post("/api/empirical/observations", headers=OPERATOR_A, json=observation)
    assert added.status_code == 201

    a_survival = client.get("/api/empirical/survival", headers=OPERATOR_A).json()
    b_survival = client.get("/api/empirical/survival", headers=OPERATOR_B).json()
    assert a_survival["n_observations"] == 1
    assert b_survival["n_observations"] == 0
    assert "absence of evidence" in b_survival["explanation"]


# --- §9.2: the capsules are nested, and org level is not a wildcard ---------


def test_org_level_cannot_read_or_write_operator_data_but_can_read_the_catalog(
    client: TestClient,
) -> None:
    """Org level owns the catalog, not the wells.

    Accepting operator data at org level would place one operator's history where
    every operator the org serves could read it, so the refusal is a 422 that
    names the level rather than a silent empty result.
    """
    payload = _demo_feasible_case().model_dump(mode="json")
    payload["metadata"]["case_id"] = "org-level-write"
    write = client.post("/api/cases", headers=ORG_LEVEL, json=payload)
    assert write.status_code == 422
    assert "org level" in write.json()["detail"]
    assert "operator-level data" in write.json()["detail"]

    _create_case(client, OPERATOR_A, "operator-a-well")
    read = client.get("/api/cases/operator-a-well", headers=ORG_LEVEL)
    assert read.status_code == 422
    assert "operator" in read.json()["detail"]

    for path in ("/api/catalog", "/api/catalog/pumps", "/api/catalog/motors"):
        response = client.get(path, headers=ORG_LEVEL)
        assert response.status_code == 200, path
        assert response.json()["perimeter"] == "acme-esp/_org"
        assert response.json()["perimeter_level"] == "org"
    assert client.get("/api/catalog/pumps", headers=ORG_LEVEL).json()["items"]

    # Run-life history and derived rules are inner-capsule data too.
    assert client.get("/api/empirical/rules", headers=ORG_LEVEL).status_code == 422
    assert client.get("/api/empirical/survival", headers=ORG_LEVEL).status_code == 422
    assert client.get("/api/trust", headers=ORG_LEVEL).status_code == 422


def test_containment_is_asymmetric_and_does_not_reach_sideways() -> None:
    a = Perimeter(org_id=ORG, operator_id="operator-a")
    b = Perimeter(org_id=ORG, operator_id="operator-b")
    org = Perimeter(org_id=ORG)

    assert a.contains(b) is False
    assert b.contains(a) is False
    assert a.contains(a) is True
    assert org.contains(a) is True
    assert org.contains(b) is True
    # An operator does not contain the org: the inner capsule cannot claim the
    # outer one's data either.
    assert a.contains(org) is False
    assert Perimeter(org_id="other-org", operator_id="operator-a").contains(a) is False


# --- perimeter ids are path segments, so the character set is a boundary ----


@pytest.mark.parametrize(
    "org_id, operator_id",
    [
        ("../../etc", None),
        ("acme", "../operator-b"),
        ("acme", "operator/b"),
        ("acme", ".."),
        ("acme", ".hidden"),
        ("acme", "_org"),
        ("acme", ""),
        ("/absolute", None),
    ],
)
def test_path_traversal_and_reserved_ids_are_rejected(org_id: str, operator_id: str | None) -> None:
    with pytest.raises(ValueError):
        Perimeter(org_id=org_id, operator_id=operator_id)


def test_a_traversal_id_in_a_header_is_a_422_not_a_file_outside_the_root(
    client: TestClient,
) -> None:
    response = client.get("/api/catalog", headers={"X-Org-Id": "../../etc"})
    assert response.status_code == 422
    assert "not a valid token" in response.json()["detail"]


# --- §9.3: a rule inherits the perimeter of its evidence --------------------


def _foreign_evidence_rule(*, owner_key: str, source_key: str) -> DerivedEmpiricalRule:
    return DerivedEmpiricalRule(
        rule_id="rule-foreign-evidence",
        tenant_id=owner_key,
        statement="Model ESP-TEST shows elevated early-failure risk shallow and gassy.",
        hypothesis=RuleHypothesis(
            hypothesis_id="hyp-1",
            authored_in_perimeter=source_key,
            pump_model="ESP-TEST",
            setting_depth_below_ft=8000.0,
            gfv_at_or_above_frac=0.2,
            early_failure_within_days=90,
        ),
        supporting_observation_ids=("obs-1", "obs-2"),
        evidence=RuleEvidence(
            source_perimeter=source_key,
            n_observations=24,
            n_exposed=12,
            n_comparison=12,
            n_early_failures_exposed=6,
            n_early_failures_comparison=1,
            n_distinct_fields=1,
            n_distinct_regions=1,
            effect_risk_difference=0.42,
            effect_risk_ratio=6.0,
            risk_difference_ci_95_low=0.1,
            risk_difference_ci_95_high=0.7,
            statistical_test="Fisher exact test (two-sided)",
            p_value=0.01,
            source_quality_weight=0.9,
            evidence_strength=0.4,
            confidence_basis="one field, 24 observations",
        ),
        bias_flags=(BiasFlag.SELECTION_BIAS,),
        bias_explanations={BiasFlag.SELECTION_BIAS: "Installations were chosen by engineers."},
    )


def test_evidence_from_another_perimeter_is_refused_at_the_presentation_boundary(
    client: TestClient,
) -> None:
    """A rule may sit in this store and still carry someone else's evidence.

    Store selection cannot catch this: the rule object is in-perimeter by
    storage. Its evidence describes another capsule's wells (§9.3), so the
    presentation boundary drops it and says so instead of restating the counts
    without names.
    """
    service = client.app.state.service
    owner = Perimeter(org_id=ORG, operator_id="operator-a")
    foreign = Perimeter(org_id=ORG, operator_id="operator-b")
    rule = _foreign_evidence_rule(owner_key=owner.key, source_key=foreign.key)
    with service.empirical(owner) as repository:
        repository.save_derived_rule(rule)

    body = client.get("/api/empirical/rules", headers=OPERATOR_A).json()
    assert len(body["rules"]) == 1
    presented = body["rules"][0]
    assert presented["evidence_withheld"] is True
    assert presented["evidence"] is None
    assert presented["supporting_observation_ids"] == []
    assert presented["survival_summary"] is None
    assert "acme-esp/operator-b" in presented["evidence_withheld_reason"]
    # The refusal is stated in the payload rather than left as a silent omission,
    # which would read as an absence of evidence.
    assert body["evidence_refusals"] == [presented["evidence_withheld_reason"]]
    # And no number from the foreign evidence survives anywhere in the response.
    assert "confidence_basis" not in json.dumps(presented)

    assert rule.evidence_visible_in(owner.key) is False
    assert rule.evidence_visible_in(foreign.key) is True


def test_a_design_overlay_refuses_a_rule_whose_evidence_is_out_of_perimeter(
    client: TestClient,
) -> None:
    """The overlay is the channel where a foreign rule would do real damage.

    An advisory judgment carries the rule's evidence strength and confidence
    basis, so attaching one would export the evidence in prose next to a
    deterministic fact. The judgment is dropped whole and the refusal is stated.
    """
    service = client.app.state.service
    owner = Perimeter(org_id=ORG, operator_id="operator-a")
    foreign = Perimeter(org_id=ORG, operator_id="operator-b")
    rule = _foreign_evidence_rule(owner_key=owner.key, source_key=foreign.key)
    # Matched to the deterministic selection for the seeded feasible case, so the
    # overlay really does try to attach this rule.
    matching = rule.model_copy(
        update={
            "hypothesis": rule.hypothesis.model_copy(
                update={
                    "pump_model": "RC2500",
                    "setting_depth_below_ft": 12000.0,
                    "gfv_at_or_above_frac": 0.0,
                }
            )
        }
    )
    with service.empirical(owner) as repository:
        repository.save_derived_rule(matching)

    _create_case(client, OPERATOR_A, "overlay-case")
    body = client.post("/api/cases/overlay-case/design?wait=true", headers=OPERATOR_A).json()

    assert body["facts"]["verdict"] == "feasible_with_caveats"
    assert body["judgments"]["empirical"] == []
    assert len(body["judgments"]["evidence_refusals"]) == 1
    assert "acme-esp/operator-b" in body["judgments"]["evidence_refusals"][0]
    assert "one field, 24 observations" not in json.dumps(body)

    report = client.get(f"/api/designs/{body['id']}/report", headers=OPERATOR_A).json()
    assert "Evidence withheld" in report["report_markdown"]


def test_in_perimeter_evidence_is_shown_in_full(client: TestClient) -> None:
    service = client.app.state.service
    owner = Perimeter(org_id=ORG, operator_id="operator-a")
    with service.empirical(owner) as repository:
        repository.save_derived_rule(_foreign_evidence_rule(owner_key=owner.key, source_key=owner.key))

    presented = client.get("/api/empirical/rules", headers=OPERATOR_A).json()["rules"][0]
    assert presented["evidence_withheld"] is False
    assert presented["evidence"]["n_observations"] == 24


# --- §9.1 and §6A.3: the agentic layer is severable -------------------------


def test_agentic_off_refuses_intake_with_a_200_and_still_designs(tmp_path: Path) -> None:
    """Deterministic design is unaffected by severing the agent layer.

    §6A.3 places the whole agentic layer after the deterministic TDH path, so
    removing it must cost prose and intake extraction and nothing else.
    """
    with TestClient(_app(tmp_path, agentic=False)) as client:
        raw = client.post("/api/cases", headers=OPERATOR_A, json={"raw_request": "Design an ESP."})
        assert raw.status_code == 201
        case_id = raw.json()["id"]

        refusal = client.post(f"/api/cases/{case_id}/intake", headers=OPERATOR_A)
        # A refusal, not an error: the request was well formed and the deployment
        # deliberately has no model endpoint to send it to.
        assert refusal.status_code == 200
        assert refusal.json()["refused"] is True
        assert refusal.json()["status"] == "refused_agentic_layer_disabled"
        assert "ESP_AGENTIC_LAYER=off" in refusal.json()["refusal_reason"]
        assert f"PUT /api/cases/{case_id}" in refusal.json()["next_action"]
        assert refusal.json()["intake"] is None

        # The structured route the refusal points at has to exist and work.
        payload = _demo_feasible_case().model_dump(mode="json")
        supplied = client.put(f"/api/cases/{case_id}", headers=OPERATOR_A, json=payload)
        assert supplied.status_code == 200

        design = client.post(f"/api/cases/{case_id}/design?wait=true", headers=OPERATOR_A)
        assert design.status_code == 200
        body = design.json()
        assert body["facts"]["verdict"] == "feasible_with_caveats"
        assert body["facts"]["candidates"]
        assert all(body["reproducibility"].values())
        assert body["judgments"]["narrative"] is None
        assert "ESP_AGENTIC_LAYER=off" in body["judgments"]["narrative_disabled_reason"]
        assert "§6A.3" in body["judgments"]["narrative_disabled_reason"]

        # No model client exists at all, so §9.1's "no access channel" is a
        # property of the object graph rather than of an unused code path.
        assert client.app.state.service.llm is None

        deployment = client.get("/api/deployment", headers=OPERATOR_A).json()
        assert deployment["agentic_layer"]["enabled"] is False
        assert deployment["agentic_layer"]["model_endpoint"] is None
        assert deployment["egress_channels"] == []
        assert deployment["network_egress_from_design_path"] is False
        assert "no network egress" in deployment["leaves_the_perimeter"][0]


def test_deployment_with_agentic_on_names_the_model_endpoint_as_egress(client: TestClient) -> None:
    """A deployment that calls a hosted model must not claim to have no egress.

    This endpoint is a security claim made to a customer's security function
    (§9.6/§9.7), so the one thing it may not do is describe an outbound model
    call as no outbound traffic. The channel is named per §9.7 as either a
    local or external model endpoint depending on where the URL resolves.
    """
    deployment = client.get("/api/deployment", headers=OPERATOR_A).json()

    assert deployment["agentic_layer"]["enabled"] is True
    assert deployment["network_egress_from_design_path"] is True
    channels = deployment["egress_channels"]
    assert len(channels) == 1
    # §9.7 channel taxonomy: local (endpoint inside the perimeter) or external
    # (endpoint outside). "hosted somewhere" is not an answer a reviewer can act on.
    assert channels[0]["channel"] in {"local_model_endpoint", "external_model_endpoint"}
    # Named specifically: either the configured address or, when it is not set,
    # the variable that configures it.
    endpoint = channels[0]["endpoint"]
    assert endpoint
    assert "://" in endpoint or "PPLX_LLM_API_ADDRESS" in endpoint
    assert channels[0]["client"] == "FakeLLM"
    # The endpoint_location claim is at the top of the channel so a reviewer
    # reads the classification before the prose.
    assert channels[0]["endpoint_location"] in {
        "inside_perimeter", "outside_perimeter", "unresolved", "misconfigured",
    }
    assert isinstance(channels[0]["crosses_perimeter"], bool)
    assert deployment["leaves_the_perimeter"] == [channels[0]["what_is_sent"]]


def test_deployment_reports_the_perimeter_model_and_per_perimeter_stores(client: TestClient) -> None:
    deployment = client.get("/api/deployment", headers=OPERATOR_A).json()

    assert deployment["perimeter_model"]["levels"] == 2
    assert deployment["perimeter_model"]["shape"] == "nested"
    assert deployment["perimeter_model"]["sibling_operators_never_merge"] is True
    assert deployment["caller"] == {
        "perimeter": "acme-esp/operator-a",
        "perimeter_level": "operator",
        "org_id": ORG,
        "operator_id": "operator-a",
    }
    assert deployment["storage"]["per_perimeter_physical_stores"] is True
    assert deployment["storage"]["in_memory"] is False
    assert deployment["storage"]["storage_root"].endswith("perimeters")
    assert "operator-a" in deployment["storage"]["store_for_caller"]

    org_view = client.get("/api/deployment", headers=ORG_LEVEL).json()
    assert org_view["caller"]["perimeter_level"] == "org"


def test_vendor_access_claim_does_not_contradict_the_egress_disclosure(
    client: TestClient, tmp_path: Path
) -> None:
    """The posture endpoint must not claim no vendor access while disclosing egress.

    §9.6 makes this endpoint the artifact a security reviewer reads. The failure
    worth guarding against is not a missing field but a self-contradicting one:
    an earlier revision asserted vendor access was "none by construction" in the
    same payload that named a hosted model endpoint receiving case text. Store
    access and data access are different claims and the endpoint must not
    conflate them.
    """
    on = client.get("/api/deployment", headers=OPERATOR_A).json()
    assert on["egress_channels"], "agentic-on deployment must disclose its egress"
    assert "not zero" in on["vendor_access"]
    assert "ESP_AGENTIC_LAYER=off" in on["vendor_access"]

    off_client = TestClient(create_app(storage_root=tmp_path / "off", seed=False, agentic=False))
    off = off_client.get("/api/deployment", headers=OPERATOR_A).json()
    assert off["egress_channels"] == []
    assert off["network_egress_from_design_path"] is False
    assert "None of either kind" in off["vendor_access"]
