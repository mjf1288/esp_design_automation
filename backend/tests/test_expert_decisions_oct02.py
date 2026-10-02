"""Acceptance tests for the signed October 2 policy, using fictional data only."""
import math
import pytest
from fastapi.testclient import TestClient
from api.app import create_app
from esp_engine.synthetic import synthetic_catalog, demo_cases
from esp_engine.pipeline import DesignEngine
from esp_engine.gas import gas_corrections
from esp_engine.curves import scale_curve_to_frequency, evaluate_stage
from esp_engine.config import DEFAULT_CONFIG
from esp_engine.constraints import prescreen_geometry, evaluate_gate, gate_allows_ranking
from esp_engine.results import ConstraintViolation
from esp_engine.models import Case, EngineeringOptions
from esp_engine.provenance import Tracked, Source
from esp_engine.expert_decisions import thrust_assessment, rank_candidates
from esp_agents.rounded_facts import rounded, rounded_narrative_scope
from esp_agents.numeric_guard import ScopedNumericGuard

@pytest.fixture(scope="module")
def runs():
    return [DesignEngine(catalog=synthetic_catalog()).run(c) for c in demo_cases()]

@pytest.mark.parametrize("beta,kq,kh",[(0,1,1),(.1,.9,.95),(.2,.75,.85),(.25,.6,.75),(.15,.825,.9)])
def test_table(beta,kq,kh):
    q,h=gas_corrections(beta)
    assert q==pytest.approx(kq) and h==pytest.approx(kh)

@pytest.mark.parametrize("beta",[.251,.3,.5])
def test_no_invented_high_gas_coefficients(beta):
    assert gas_corrections(beta)==(None,None)

def test_synthetic_curve_boundaries():
    for p in synthetic_catalog().pumps:
        curve=scale_curve_to_frequency(p,60,DEFAULT_CONFIG.correlations.frequency_curves)
        rows=[evaluate_stage(curve,curve.q_max_bpd*i/100) for i in range(101)]
        assert rows[0].efficiency_frac==rows[-1].efficiency_frac==0
        assert rows[0].head_ft_per_stage>0
        assert all(a.head_ft_per_stage>=b.head_ft_per_stage for a,b in zip(rows,rows[1:]))
        assert all(math.isfinite(r.bhp_per_stage) and r.bhp_per_stage>0 for r in rows)
        assert p.synthetic

def test_exceedances_do_not_reject_but_physical_does():
    v=ConstraintViolation(constraint_type="electrical",rigidity="hard",message="Cable",actual_value=50,limit_value=30)
    assert v.rigidity=="soft" and v.margin==20
    assert gate_allows_ranking(evaluate_gate([v]))
    p=ConstraintViolation(constraint_type="geometry",rigidity="absolute",physical_impossibility=True,message="Nonfit")
    assert not gate_allows_ranking(evaluate_gate([p]))

def test_clearance_margin_is_not_physical_impossibility():
    c=demo_cases()[0]
    vs=prescreen_geometry(equipment_od_in=6.2,setting_depth_md_ft=8000,geometry=c.geometry,thresholds=DEFAULT_CONFIG.thresholds,max_equipment_od_override_in=5)
    assert vs and all(not v.physical_impossibility for v in vs)
    vs=prescreen_geometry(equipment_od_in=6.3,setting_depth_md_ft=8000,geometry=c.geometry,thresholds=DEFAULT_CONFIG.thresholds)
    assert any(v.physical_impossibility for v in vs)

def test_three_scenarios(runs):
    assert all(r.synthetic and r.candidates for r in runs)
    assert demo_cases()[0].branch.value=="A_replacement"
    assert demo_cases()[1].branch.value.startswith("B_")
    assert runs[0].input_confidence>runs[1].input_confidence
    assert len(runs[0].candidates[0].soft_violations)<=2
    for r in runs:
        order=[(c.zone_duration_months,c.score.efficiency_avg_frac) for c in r.candidates]
        assert order==sorted(order,reverse=True)
    c=runs[2].candidates[0]
    assert c.design_point.free_gas_fraction_at_intake>.1
    assert c.engineering["tapered"]["sections"][0]["volumetric_rate_bpd"]>c.engineering["tapered"]["sections"][1]["volumetric_rate_bpd"]
    assert any(v.constraint_type=="hydraulic" for v in c.soft_violations)
    assert not c.design_point.is_acceptable

def test_thrust_paths_and_missing():
    p=synthetic_catalog().pumps[-1];s=synthetic_catalog().seals[0]
    f=thrust_assessment(p,s,head_ft=2000,sg=1,stages=100,path_override="floater")
    c=thrust_assessment(p,s,head_ft=2000,sg=1,stages=100,path_override="compression")
    assert f["load_lb"]==pytest.approx(2000*.433*math.pi*p.shaft_diameter_in**2/4)
    assert c["load_lb"]==pytest.approx(f["load_lb"]+250)
    assert c["recommend_tandem"]
    assert thrust_assessment(p,s,head_ft=None,sg=1,stages=100)["load_lb"] is None

def test_bep_toggle(runs):
    c=demo_cases()[0].model_copy(update={"engineering":EngineeringOptions(ranking_mode="bep_target")})
    ranked,comparison=rank_candidates(runs[0].candidates,c,synthetic_catalog(),DEFAULT_CONFIG)
    assert ranked[0].bep_rank==1
    assert comparison and any(r["run_life_rank"]==1 for r in comparison)

def test_rounded_input_and_strict_guard(runs):
    assert rounded(68.056,1)=="68.1"
    assert rounded(68.05,1)=="68.1"
    c=runs[0].candidates[0]
    updated=c.model_copy(update={"design_point":c.design_point.model_copy(update={"efficiency_frac":.68056})})
    r=runs[0].model_copy(update={"candidates":[updated]})
    assert "68.1%" in str(rounded_narrative_scope(r))
    guard=ScopedNumericGuard(r,candidate_rank=1)
    assert not guard.check_text("Efficiency is 68.1%.",field="test")
    assert guard.check_text("Efficiency is 68.0%.",field="test")

def test_manual_gas_override_enters_ledger():
    c=demo_cases()[2].model_copy(update={"engineering":EngineeringOptions(gas_kq_override=Tracked(value=.8,source=Source.ENGINEER_OVERRIDE,confidence=1))})
    r=DesignEngine(catalog=synthetic_catalog()).run(c)
    assert any("gas_kq_override" in a.field_path and a.source=="engineer_override" for a in r.assumption_ledger)
    assert r.candidates[0].engineering["gas"]["kq"]==.8

def test_explicit_cable_selection_and_ceiling():
    c=demo_cases()[0].model_copy(update={"engineering":EngineeringOptions(cable_id="synthetic-cable-2",operating_ceiling_hz=Tracked(value=55.,source=Source.ENGINEER_OVERRIDE))})
    r=DesignEngine(catalog=synthetic_catalog()).run(c)
    assert all(x.configuration.cable_id=="synthetic-cable-2" for x in r.candidates)
    rows=r.candidates[0].engineering["cables"]
    assert len(rows)==3 and sum(x["selected"] for x in rows)==1
    assert all(x["operating_ceiling_hz"]==55 and "ceiling_assessment" in x for x in rows)

def test_preferences_require_confirmation_and_do_not_cross_perimeters(tmp_path):
    app=create_app(storage_root=tmp_path,seed=False)
    with TestClient(app) as client:
        a={"X-Org-Id":"demo","X-Operator-Id":"a"}
        b={"X-Org-Id":"demo","X-Operator-Id":"b"}
        path="/api/projects/same-project/preferences"
        assert client.put(path,headers=a,json={"ranking_mode":"bep_target"}).status_code==422
        saved=client.put(path,headers=a,json={"ranking_mode":"bep_target","confirmed":True,"engineer_id":"Test engineer","prefer_lower_cost":True})
        assert saved.status_code==200
        assert client.get(path,headers=a).json()["preference"]["confirmed"]
        assert client.get(path,headers=b).json()["preference"] is None

def test_section_validation():
    with pytest.raises(ValueError):
        EngineeringOptions(tapered_sections=[{"stages":-1}])

def test_historical_policy_is_not_rewritten(runs):
    from esp_engine.results import DesignResult
    payload=runs[0].model_dump(mode="json")
    payload.pop("decision_policy")
    payload["provenance"]["engine_version"]="0.1.0"
    v=payload["candidates"][0]["soft_violations"][0]
    v["rigidity"]="hard";v.pop("policy",None)
    parsed=DesignResult.model_validate(payload)
    assert parsed.decision_policy=="legacy"
    assert parsed.candidates[0].soft_violations[0].rigidity=="hard"
