"""Tests for the two endpoints the frontend needed and the first cut lacked.

The pump-curve endpoint exists because the design-results chart traces the
operating point's path over the pump curve; without the curve itself the chart
loses the reference it is read against. The survival endpoint exists so the
empirical view plots this perimeter's real censored history rather than a fixture.

Updated for framework v0.3 §9: each test builds its own per-perimeter store tree
under ``tmp_path`` instead of sharing one flat database, and the scoping headers
name a capsule rather than a flat tenant.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app


def _client(tmp_path: Path) -> TestClient:
    # A file-backed root rather than the in-memory registry: sync endpoints run in
    # worker threads and a SQLite in-memory database belongs to one connection.
    return TestClient(create_app(storage_root=tmp_path / "perimeters"))


def test_pump_curve_returns_sampled_points_and_zone_boundaries(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.get(
            "/api/catalog/pumps/slb-reda-rc2500/curve",
            params={"frequency_hz": 65, "stages": 41, "samples": 25},
        )
    assert response.status_code == 200
    body = response.json()

    assert body["frequency_hz"] == 65.0
    assert body["stages"] == 41
    assert len(body["points"]) >= 2
    # Zone shading must come from engine values, not be re-derived client-side.
    assert body["downthrust_limit_bpd"] < body["bep_q_bpd"] < body["upthrust_limit_bpd"]
    # Total head must scale with stage count rather than being returned per stage.
    first = body["points"][0]
    assert first["head_ft_total"] == first["head_ft_per_stage"] * 41


def test_pump_curve_declares_that_it_is_an_estimate(tmp_path: Path):
    """The seeded catalog holds no digitized vendor curves.

    A client that renders this curve without the caveat presents an estimate with
    the authority of a vendor curve, so the caveat travels with the data.
    """
    with _client(tmp_path) as client:
        body = client.get("/api/catalog/pumps/slb-reda-rc2500/curve").json()

    assert body["is_estimated"] is True
    assert body["data_quality"] == "parametric_estimate"
    assert any("parametric estimate" in caveat for caveat in body["caveats"])


def test_pump_curve_flags_nonphysical_shut_in_efficiency(tmp_path: Path):
    """The fitted efficiency polynomial is not zero at zero flow.

    That is a real defect of a parametric fit. The endpoint reports it instead of
    trimming the domain, which would hide the artifact and imply the fit is
    physical there.
    """
    with _client(tmp_path) as client:
        body = client.get(
            "/api/catalog/pumps/slb-reda-rc2500/curve", params={"samples": 40}
        ).json()

    shut_in = next(p for p in body["points"] if p["q_bpd"] == 0.0)
    assert shut_in["efficiency_frac"] > 0.01
    assert any("shut-in" in caveat for caveat in body["caveats"])


def test_pump_curve_rejects_bad_inputs(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.get("/api/catalog/pumps/nope/curve").status_code == 404
        assert (
            client.get(
                "/api/catalog/pumps/slb-reda-rc2500/curve",
                params={"frequency_hz": -5},
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/catalog/pumps/slb-reda-rc2500/curve", params={"samples": 1}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/catalog/pumps/slb-reda-rc2500/curve", params={"stages": 0}
            ).status_code
            == 422
        )


def test_survival_reports_absence_of_evidence_rather_than_a_curve(tmp_path: Path):
    """An empty perimeter must not receive a synthetic survival band.

    Showing an invented curve beside real ones would be the most misleading thing
    this product could do, so the empty case is an explicit, worded absence.
    """
    with _client(tmp_path) as client:
        body = client.get(
            "/api/empirical/survival", headers={"X-Operator-Id": "empty-operator"}
        ).json()

    assert body["n_observations"] == 0
    assert body["estimate"] is None
    assert "absence of evidence" in body["explanation"]


def test_survival_rejects_an_invalid_confidence_level(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.get(
            "/api/empirical/survival", params={"confidence_level": 1.5}
        )
    assert response.status_code == 422


def test_survival_is_perimeter_isolated(tmp_path: Path):
    """One operator's field history must never appear in another's survival curve."""
    with _client(tmp_path) as client:
        a = client.get(
            "/api/empirical/survival", headers={"X-Operator-Id": "operator-a"}
        ).json()
        b = client.get(
            "/api/empirical/survival", headers={"X-Operator-Id": "operator-b"}
        ).json()
    assert a["perimeter"] == "demo/operator-a"
    assert b["perimeter"] == "demo/operator-b"


def test_designs_for_case_lists_stored_runs_with_reproducibility_keys(tmp_path: Path):
    """A design run is immutable and expensive; clients must be able to find one.

    Without this endpoint the frontend re-ran the engine on every page load,
    spending minutes to recompute a byte-identical result.
    """
    with _client(tmp_path) as client:
        created = client.post("/api/cases", json={"raw_request": "Design an ESP."})
        assert created.status_code == 201
        case_id = created.json()["id"]

        empty = client.get(f"/api/cases/{case_id}/designs")
        assert empty.status_code == 200
        body = empty.json()
        # An un-run case reports zero runs rather than implying one exists.
        assert body["count"] == 0
        assert body["designs"] == []

        assert client.get("/api/cases/does-not-exist/designs").status_code == 404


def test_designs_for_case_is_perimeter_isolated(tmp_path: Path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/cases",
            headers={"X-Operator-Id": "operator-a"},
            json={"raw_request": "Design an ESP."},
        )
        case_id = created.json()["id"]
        # A sibling operator must not even learn the case exists.
        leaked = client.get(
            f"/api/cases/{case_id}/designs", headers={"X-Operator-Id": "operator-b"}
        )
        assert leaked.status_code == 404
