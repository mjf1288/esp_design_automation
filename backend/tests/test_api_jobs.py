"""Design runs off the request path: two-stage job queue.

Measured before the queue existed, on the demo case: the deterministic engine
takes about 0.4 s and the narrative model loop about 120 s. These tests pin the
properties that measurement motivates:

* the POST returns immediately even when the model never answers;
* facts are readable as soon as the engine stage finishes, while the narrative
  is still pending, and are never rewritten afterwards;
* identical concurrent submissions are one run;
* cancellation works queued and in flight, and never destroys stored facts;
* admission is bounded globally and per perimeter;
* a job orphaned by a restart reads as interrupted, and its narrative can be
  retried without recomputing the facts;
* jobs are perimeter-isolated like every other row.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

import api.app as app_module
from api.app import _demo_feasible_case, create_app
from api.jobs import RunnerLimits
from esp_engine.pipeline import DesignEngine
from esp_engine.results import DesignResult

OP_A = {"X-Operator-Id": "operator-a"}
OP_B = {"X-Operator-Id": "operator-b"}

_CLEAN_NARRATIVE = json.dumps(
    {
        "fact_summary": "The deterministic design result is available.",
        "configuration_facts": [],
        "validity_boundary_facts": [],
        "watch_facts": [],
        "judgments": [],
    }
)


class GatedLLM:
    """A narrative model that blocks until released, standing in for a slow endpoint."""

    def __init__(self, *, gated: bool = True) -> None:
        self.release = threading.Event()
        if not gated:
            self.release.set()
        self.entered = threading.Event()
        self.calls = 0
        self._lock = threading.Lock()

    def generate_json(self, *, system_prompt: str, user_prompt: str, schema: dict[str, Any], schema_name: str) -> str:
        assert schema_name == "esp_narrative", schema_name
        with self._lock:
            self.calls += 1
        self.entered.set()
        assert self.release.wait(20), "test never released the gated model"
        return _CLEAN_NARRATIVE


def _client(tmp_path: Path, llm: Any, limits: RunnerLimits | None = None, **kw: Any) -> TestClient:
    app = create_app(storage_root=tmp_path / "perimeters", llm=llm, seed=False,
                     runner_limits=limits or RunnerLimits(), **kw)
    return TestClient(app)


def _create_case(client: TestClient, case_id: str, headers: dict[str, str] = OP_A) -> None:
    payload = _demo_feasible_case().model_dump(mode="json")
    payload["metadata"]["case_id"] = case_id
    assert client.post("/api/cases", headers=headers, json=payload).status_code == 201


def _poll(client: TestClient, job_id: str, until: Callable[[dict[str, Any]], bool],
          headers: dict[str, str] = OP_A, timeout_s: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        job = client.get(f"/api/jobs/{job_id}", headers=headers).json()["job"]
        if until(job):
            return job
        assert time.monotonic() < deadline, f"job never reached the expected state: {job}"
        time.sleep(0.02)


def test_post_returns_immediately_and_facts_arrive_before_the_narrative(tmp_path: Path) -> None:
    llm = GatedLLM()
    with _client(tmp_path, llm) as client:
        _create_case(client, "case-1")
        started = time.perf_counter()
        response = client.post("/api/cases/case-1/design", headers=OP_A)
        post_s = time.perf_counter() - started
        assert response.status_code == 202
        assert response.headers["location"].startswith("/api/jobs/")
        job = response.json()["job"]
        assert job["status"] in {"queued", "running"} and job["kind"] == "design"
        # The model is blocked indefinitely; the request must not wait on it.
        assert post_s < 2.0, f"POST took {post_s:.2f}s while the model was blocked"

        job = _poll(client, job["job_id"], lambda j: j["facts_ready"])
        assert job["phase"] == "narrative" and not job["terminal"]
        assert llm.entered.wait(5)

        design = client.get(f"/api/designs/{job['design_id']}", headers=OP_A).json()
        assert design["facts"]["verdict"] == "feasible_with_caveats"
        assert design["facts"]["candidates"]
        assert design["judgments"]["narrative"]["status"] == "pending"
        facts_before = design["facts"]

        llm.release.set()
        job = _poll(client, job["job_id"], lambda j: j["terminal"])
        assert job["status"] == "succeeded" and job["narrative_status"] == "ok"
        assert job["seconds_to_facts"] is not None and job["seconds_to_facts"] <= job["elapsed_s"]

        design = client.get(f"/api/designs/{job['design_id']}", headers=OP_A).json()
        assert design["judgments"]["narrative"]["status"] == "ok"
        # Facts are immutable once readable: attaching prose did not touch them.
        assert design["facts"] == facts_before


def test_identical_submission_while_active_is_the_same_run(tmp_path: Path) -> None:
    llm = GatedLLM()
    with _client(tmp_path, llm) as client:
        _create_case(client, "case-1")
        first = client.post("/api/cases/case-1/design", headers=OP_A).json()["job"]
        second = client.post("/api/cases/case-1/design", headers=OP_A)
        assert second.status_code == 200
        assert second.json()["deduplicated"] is True
        assert second.json()["job"]["job_id"] == first["job_id"]
        llm.release.set()
        _poll(client, first["job_id"], lambda j: j["terminal"])
        # Once the first run is terminal, a new submission is a new run.
        third = client.post("/api/cases/case-1/design", headers=OP_A)
        assert third.status_code == 202
        assert third.json()["job"]["job_id"] != first["job_id"]
        _poll(client, third.json()["job"]["job_id"], lambda j: j["terminal"])


def test_cancel_in_flight_narrative_discards_prose_and_keeps_facts(tmp_path: Path) -> None:
    llm = GatedLLM()
    with _client(tmp_path, llm) as client:
        _create_case(client, "case-1")
        job = client.post("/api/cases/case-1/design", headers=OP_A).json()["job"]
        job = _poll(client, job["job_id"], lambda j: j["facts_ready"])
        assert llm.entered.wait(5)

        cancel = client.post(f"/api/jobs/{job['job_id']}/cancel", headers=OP_A)
        assert cancel.status_code == 200
        # The in-flight call has not returned, so the job is still running but
        # reports that cancellation is pending.
        assert cancel.json()["job"]["status"] == "running"
        assert cancel.json()["job"]["cancel_requested"] is True
        # The in-flight call now returns clean prose, which must be discarded.
        llm.release.set()
        job = _poll(client, job["job_id"], lambda j: j["terminal"])
        assert job["status"] == "cancelled" and job["narrative_status"] == "cancelled"
        assert llm.calls == 1, "a cancelled narrative made another model call"

        design = client.get(f"/api/designs/{job['design_id']}", headers=OP_A).json()
        assert design["judgments"]["narrative"]["status"] == "cancelled"
        assert design["facts"]["verdict"] == "feasible_with_caveats"
        # Terminal jobs cannot be cancelled again.
        assert client.post(f"/api/jobs/{job['job_id']}/cancel", headers=OP_A).status_code == 409


def test_cancel_queued_narrative_never_calls_the_model(tmp_path: Path) -> None:
    llm = GatedLLM()
    limits = RunnerLimits(narrative_workers=1)
    with _client(tmp_path, llm, limits) as client:
        _create_case(client, "case-a")
        _create_case(client, "case-b")
        a = client.post("/api/cases/case-a/design", headers=OP_A).json()["job"]
        assert llm.entered.wait(5)  # case-a holds the only narrative worker
        b = client.post("/api/cases/case-b/design", headers=OP_A).json()["job"]
        b = _poll(client, b["job_id"], lambda j: j["facts_ready"])
        # case-b's facts are ready even though the narrative pool is saturated.
        assert client.get(f"/api/designs/{b['design_id']}", headers=OP_A).json()["facts"]["candidates"]

        cancelled = client.post(f"/api/jobs/{b['job_id']}/cancel", headers=OP_A).json()["job"]
        assert cancelled["status"] == "cancelled"
        llm.release.set()
        _poll(client, a["job_id"], lambda j: j["terminal"])
        assert llm.calls == 1, "the cancelled queued narrative still reached the model"
        design_b = client.get(f"/api/designs/{b['design_id']}", headers=OP_A).json()
        assert design_b["judgments"]["narrative"]["status"] == "cancelled"


def test_admission_is_bounded_per_perimeter_and_globally(tmp_path: Path) -> None:
    llm = GatedLLM()
    limits = RunnerLimits(max_active_jobs=2, max_active_jobs_per_perimeter=1)
    with _client(tmp_path, llm, limits) as client:
        _create_case(client, "case-a1")
        _create_case(client, "case-a2")
        _create_case(client, "case-b1", OP_B)
        _create_case(client, "case-b2", OP_B)
        a1 = client.post("/api/cases/case-a1/design", headers=OP_A)
        assert a1.status_code == 202
        refused = client.post("/api/cases/case-a2/design", headers=OP_A)
        assert refused.status_code == 429
        assert int(refused.headers["retry-after"]) > 0
        assert "per-perimeter limit" in refused.json()["detail"]
        # Another perimeter is not starved by operator-a's run.
        assert client.post("/api/cases/case-b1/design", headers=OP_B).status_code == 202
        # Global limit reached (2 active).
        _create_case(client, "case-c1", {"X-Operator-Id": "operator-c"})
        full = client.post("/api/cases/case-c1/design", headers={"X-Operator-Id": "operator-c"})
        assert full.status_code == 429 and "queue is full" in full.json()["detail"]
        llm.release.set()
        _poll(client, a1.json()["job"]["job_id"], lambda j: j["terminal"])
        # A finished run frees its slot.
        again = client.post("/api/cases/case-a2/design", headers=OP_A)
        assert again.status_code == 202
        _poll(client, again.json()["job"]["job_id"], lambda j: j["terminal"])


def test_jobs_are_perimeter_isolated(tmp_path: Path) -> None:
    llm = GatedLLM(gated=False)
    with _client(tmp_path, llm) as client:
        _create_case(client, "case-1")
        job = client.post("/api/cases/case-1/design", headers=OP_A).json()["job"]
        assert client.get(f"/api/jobs/{job['job_id']}", headers=OP_B).status_code == 404
        assert client.post(f"/api/jobs/{job['job_id']}/cancel", headers=OP_B).status_code == 404
        assert client.get("/api/cases/case-1/jobs", headers=OP_B).status_code == 404
        done = _poll(client, job["job_id"], lambda j: j["terminal"])
        assert done["status"] == "succeeded"
        listed = client.get("/api/cases/case-1/jobs", headers=OP_A).json()
        assert [j["job_id"] for j in listed["jobs"]] == [job["job_id"]]
        assert client.get("/api/cases/case-1/jobs?active=true", headers=OP_A).json()["count"] == 0


def test_restart_marks_orphans_interrupted_and_narrative_can_be_retried(tmp_path: Path) -> None:
    llm = GatedLLM(gated=False)
    with _client(tmp_path, llm) as client:
        _create_case(client, "case-1")
        job = client.post("/api/cases/case-1/design?wait=true", headers=OP_A).json()["job"]
        design_id = job["design_id"]
        # Simulate a crash mid-narrative: the stored rows say "still running"
        # and "narrative pending", and then the process disappears.
        service = client.app.state.service
        perimeter = app_module._perimeter(x_org_id=None, x_operator_id="operator-a", x_tenant_id=None)
        app_module._update_job(service, perimeter, job["job_id"], status="running",
                               phase="narrative", narrative_status="pending", finished_at=None)
        app_module._attach_narrative(service, perimeter, design_id, app_module._pending_narrative(job["job_id"]))
        facts_before = client.get(f"/api/designs/{design_id}", headers=OP_A).json()["facts"]

    llm2 = GatedLLM(gated=False)
    with _client(tmp_path, llm2) as restarted:
        orphan = restarted.get(f"/api/jobs/{job['job_id']}", headers=OP_A).json()["job"]
        assert orphan["status"] == "interrupted" and orphan["terminal"]
        assert "restarted" in orphan["error"]
        design = restarted.get(f"/api/designs/{design_id}", headers=OP_A).json()
        assert design["judgments"]["narrative"]["status"] == "interrupted"

        retry = restarted.post(f"/api/designs/{design_id}/narrative?wait=true", headers=OP_A)
        assert retry.status_code == 200
        body = retry.json()
        assert body["job"]["kind"] == "narrative" and body["job"]["status"] == "succeeded"
        assert body["judgments"]["narrative"]["status"] == "ok"
        # Retrying prose recomputed nothing: same design, same facts.
        assert body["id"] == design_id and body["facts"] == facts_before
        assert llm2.calls == 1


def test_wait_mode_preserves_the_blocking_contract(tmp_path: Path) -> None:
    with _client(tmp_path, GatedLLM(gated=False)) as client:
        _create_case(client, "case-1")
        response = client.post("/api/cases/case-1/design?wait=true", headers=OP_A)
        assert response.status_code == 200
        body = response.json()
        assert body["facts"]["verdict"] == "feasible_with_caveats"
        assert body["judgments"]["narrative"]["status"] == "ok"
        assert body["job"]["status"] == "succeeded"


def test_agentic_off_finishes_in_one_stage_and_refuses_narrative_retry(tmp_path: Path) -> None:
    with _client(tmp_path, None, agentic=False) as client:
        _create_case(client, "case-1")
        job = client.post("/api/cases/case-1/design", headers=OP_A).json()["job"]
        job = _poll(client, job["job_id"], lambda j: j["terminal"])
        assert job["status"] == "succeeded" and job["narrative_status"] == "disabled"
        retry = client.post(f"/api/designs/{job['design_id']}/narrative", headers=OP_A)
        assert retry.status_code == 409


def test_engine_failure_is_reported_on_the_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: DesignEngine, case: Any) -> DesignResult:
        raise ValueError("motor_data_overrides reference motor ids that are not in catalog")

    monkeypatch.setattr(app_module.DesignEngine, "run", boom)
    with _client(tmp_path, GatedLLM(gated=False)) as client:
        _create_case(client, "case-1")
        job = client.post("/api/cases/case-1/design", headers=OP_A).json()["job"]
        job = _poll(client, job["job_id"], lambda j: j["terminal"])
        assert job["status"] == "failed" and job["design_id"] is None
        assert "motor_data_overrides" in job["error"]
        blocking = client.post("/api/cases/case-1/design?wait=true", headers=OP_A)
        assert blocking.status_code == 422 and "motor_data_overrides" in blocking.json()["detail"]


def test_case_without_structured_input_is_refused_at_submission(tmp_path: Path) -> None:
    with _client(tmp_path, GatedLLM(gated=False)) as client:
        created = client.post("/api/cases", headers=OP_A, json={"raw_request": "Design an ESP."})
        case_id = created.json()["id"]
        response = client.post(f"/api/cases/{case_id}/design", headers=OP_A)
        assert response.status_code == 409
        assert client.get(f"/api/cases/{case_id}/jobs", headers=OP_A).json()["count"] == 0


def test_stored_design_rebuilds_exactly_for_the_narrative_stage() -> None:
    """The narrative stage reads the design back from storage; the rebuild must be exact."""
    result = DesignEngine().run(_demo_feasible_case())
    facts = result.model_dump(mode="json", exclude={"judgments"})
    empirical = [j.model_dump(mode="json") for j in result.judgments]
    rebuilt = DesignResult.model_validate(json.loads(json.dumps({**facts, "judgments": empirical})))
    assert rebuilt.model_dump(mode="json") == result.model_dump(mode="json")
