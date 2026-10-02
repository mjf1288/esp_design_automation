"""Reproducible, explicitly fictional demonstration summary."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/"backend"))
from esp_engine.synthetic import demo_cases, synthetic_catalog
from esp_engine.pipeline import DesignEngine

rows=[]
for case in demo_cases():
    result=DesignEngine(catalog=synthetic_catalog()).run(case)
    candidate=result.candidates[0]
    rows.append({
        "case":case.metadata.case_id,"synthetic":True,"branch":case.branch.value,
        "scenario_input_confidence":result.input_confidence,
        "configuration":candidate.configuration.model_dump(mode="json"),
        "zone_duration_months":candidate.zone_duration_months,
        "day_one_head_ft":candidate.design_point.head_developed_ft,
        "day_one_tdh_ft":candidate.design_point.head_required_ft,
        "day_one_head_margin_frac":candidate.design_point.head_margin_frac,
        "efficiency_frac":candidate.design_point.efficiency_frac,
        "intake_gvf":candidate.design_point.free_gas_fraction_at_intake,
        "warnings":[v.model_dump(mode="json") for v in candidate.soft_violations],
        "engineering":candidate.engineering,
        "verdict":result.verdict.value,
        "engine_version":result.provenance.engine_version,
    })
print(json.dumps(rows,indent=2))
