"""Explicitly fictional equipment and wells. Never a fallback vendor catalog."""
from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache

from .catalog import Catalog, PumpModel, MotorModel, CableModel, SealModel, GasHandlingModel


@lru_cache(maxsize=1)
def synthetic_catalog() -> Catalog:
    pumps = []
    for flow, h0, eta, kind, stage_type in [
        (1800.,28.,.68,"floater","radial"),
        (2600.,25.,.74,"floater","mixed_flow"),
        (3600.,23.,.70,"compression","mixed_flow"),
    ]:
        runout = 2*flow
        # H=H0(1-x²), eta=4*eta_peak*x(1-x). These algebraic
        # boundary conditions are exact; no unconstrained polynomial fit.
        hc=[-h0/runout**2,0.,h0]
        ec=[-400*eta/runout**2,400*eta/runout,0.]
        pc=[h0/(4*eta*135771.428571),h0*runout/(4*eta*135771.428571)]
        points=[]
        for i in range(21):
            q=runout*i/20; x=q/runout
            points.append({"q_bpd":q,"head_ft":h0*(1-x*x),"eff_pct":400*eta*x*(1-x),"bhp_hp":pc[0]*q+pc[1]})
        pumps.append(PumpModel(
            id=f"synthetic-{int(flow)}",manufacturer="SYNTHETIC DEMO / not a vendor",
            model=f"SYN-{int(flow)}",series=400,housing_od_in=4.,min_casing_id_in=5.5,
            stage_type=stage_type,pump_type=kind,shaft_diameter_in=.75,
            impeller_thrust_lb_per_stage=2.5 if kind=="compression" else None,
            price_per_stage=20+flow/100,
            frequency_ref_hz=60,bep_flow_bpd=flow,recommended_range_bpd=(.65*flow,1.35*flow),
            downthrust_limit_bpd=.65*flow,upthrust_limit_bpd=1.35*flow,
            max_stages=300,shaft_hp_limit=250,thrust_bearing_capacity_lb=1800,
            curve={"points":points},curve_fit={"head_coeffs":hc,"eff_coeffs":ec,"bhp_coeffs":pc},
            data_quality="synthetic_demo",synthetic=True,source_urls=[],
            notes="Entirely fictional curve and dimensions. Algebraic endpoint conditions, not vendor test data.",
        ))
    motors=[MotorModel(
        id=f"synthetic-motor-{hp}",manufacturer="SYNTHETIC DEMO",series=400,od_in=4.,
        min_casing_id_in=5.5,hp=hp,volts=1500,
        amps=hp*746/(math.sqrt(3)*1500*.88*.90),length_ft=14,
        max_winding_temp_f=400,motor_type="induction",power_factor=.88,efficiency=.90,
        synthetic=True,data_quality="synthetic_demo",notes="Fictional; not an ESP vendor rating.",
    ) for hp in (50,55,75,100,150)]
    cables=[CableModel(
        id=f"synthetic-cable-{awg}",awg=awg,conductor_area_cmil=area,ampacity_a=amp,
        resistance_ohm_per_1000ft_at_77f=res,od_flat_in=od,max_temp_f=temp,
        price_per_ft=price,synthetic=True,data_quality="synthetic_demo",
        notes="Fictional demo-only ESP cable rating and price. Not an NEC table or purchasable cable.",
    ) for awg,area,amp,res,od,temp,price in [(6,26240,35,.45,.6,230,3),(4,41740,65,.28,.7,280,5),(2,66360,100,.18,.85,350,8)]]
    seal=SealModel(id="synthetic-seal",series=400,od_in=4,thrust_bearing_capacity_lb=400,max_shaft_hp=250,length_ft=5,chamber_type="labyrinth/bag",synthetic=True,data_quality="synthetic_demo")
    gas=GasHandlingModel(id="synthetic-separator",type="rotary_separator",series=400,od_in=4,hp_consumed=3,max_free_gas_fraction_handled=.45,synthetic=True,data_quality="synthetic_demo",notes="No separation-efficiency credit: no vendor map.")
    payload={"pumps":[p.model_dump() for p in pumps],"motors":[m.model_dump() for m in motors],"cables":[c.model_dump() for c in cables],"seals":[seal.model_dump()],"gas_handling":[gas.model_dump()],"casing":[]}
    version="synthetic-"+hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    return Catalog(**payload,version=version,has_estimated_data=True)


def demo_cases():
    from .models import Case, CaseMetadata, CasingSection, Constraints, ElectricalConstraints, Expectations, FluidProperties, ReservoirProperties, TrajectorySpec, WellGeometry, TaskType, PreviousInstallation, TestPoint, DriftModel
    from .provenance import Tracked, Source
    def t(value,unit=None,confidence=.95):
        return Tracked(value=value,unit=unit,source=Source.DEFAULT,confidence=confidence,note="SYNTHETIC demo input. Not a field measurement.")
    base=Case(
        metadata=CaseMetadata(case_id="synthetic-replacement",project_id="synthetic-demo",tenant_id="demo/demo-operator",well_name="SYN-A / Replacement",field_name="Synthetic demonstration only"),
        synthetic=True,task_type=TaskType.NEW_WELL,
        expectations=Expectations(target_rate_bpd=t(2000.,"bpd"),wellhead_pressure_psi=t(200.,"psi"),setting_depth_md_ft=t(8000.,"ft")),
        constraints=Constraints(electrical=ElectricalConstraints(vsd_available=t(True),frequency_min_hz=t(45.,"Hz"),frequency_max_hz=t(65.,"Hz"),available_surface_voltage_v=t(4160.,"V"))),
        geometry=WellGeometry(casing_sections=[CasingSection(top_md_ft=0,bottom_md_ft=10000,od_in=7.,id_in=6.276)],is_vertical=True,perforation_top_md_ft=t(8500.,"ft"),total_depth_md_ft=t(9500.,"ft"),tubing_id_in=t(2.441,"in")),
        fluid=FluidProperties(oil_api=t(34.),gas_sg=t(.78),water_sg=t(1.03),water_cut_frac=t(.35),gor_scf_stb=t(300.,"scf/stb"),water_salinity_ppm=t(35000.,"ppm")),
        reservoir=ReservoirProperties(reservoir_pressure_psi=t(3500.,"psi"),bht_f=t(185.,"F"),surface_temp_f=t(80.,"F"),productivity_index_bpd_psi=t(1.6,"bpd/psi"),bubble_point_psi=t(1900.,"psi")),
        trajectory=TrajectorySpec(horizon_months=24),
    )
    # Previous installation anchors Branch A, without inventing real run history.
    replacement=base.model_copy(update={"task_type":TaskType.ESP_REPLACEMENT,"reference":PreviousInstallation(pump_model=t("SYN-2600"),stage_count=t(100),setting_depth_md_ft=t(8000.,"ft"),operating_frequency_hz=t(55.,"Hz"),run_life_days=t(540.,"days"),still_running=True,test_points=[TestPoint(date="synthetic history",rate_bpd=t(2000.,"bpd"),pip_psi=t(2100.,"psi"),frequency_hz=t(55.,"Hz"))])})
    replacement=replacement.model_copy(update={"trajectory":TrajectorySpec(horizon_months=24,drift_models={
        name:DriftModel(parameter=name,form="constant",rate_per_year=0,uncertainty_frac=0,provenance=t("Synthetic stable production history"))
        for name in ("productivity_index_bpd_psi","reservoir_pressure_psi","water_cut_frac","gor_scf_stb")
    })})
    new=base.model_copy(update={
        "metadata":base.metadata.model_copy(update={"case_id":"synthetic-new","well_name":"SYN-B / New well"}),
        "reservoir":base.reservoir.model_copy(update={"reservoir_pressure_psi":t(3200.,"psi",.65),"productivity_index_bpd_psi":t(1.6,"bpd/psi",.60)}),
    })
    gassy=base.model_copy(update={
        "metadata":base.metadata.model_copy(update={"case_id":"synthetic-gassy","well_name":"SYN-C / Gassy well"}),
        "fluid":base.fluid.model_copy(update={"gor_scf_stb":t(750.,"scf/stb",.70)}),
        "reservoir":base.reservoir.model_copy(update={"reservoir_pressure_psi":t(2800.,"psi",.70)}),
    })
    result=[]
    for case in (replacement,new,gassy):
        payload=case.model_dump(mode="json")
        def label_defaults(value):
            if isinstance(value,dict):
                if "value" in value and "source" in value:
                    if value.get("confidence",1)<.5:
                        value["confidence"]=.95 if case is replacement else .60
                    value["note"]="SYNTHETIC scenario confidence only, not field-data confidence. "+(value.get("note") or "")
                for child in value.values(): label_defaults(child)
            elif isinstance(value,list):
                for child in value: label_defaults(child)
        label_defaults(payload)
        result.append(Case.model_validate(payload))
    return result
