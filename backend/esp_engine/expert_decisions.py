"""Expert sign-off 2026-10-02: disclosed alternatives, never hidden approval."""
from __future__ import annotations

import math

from .curves import scale_curve_to_frequency
from .models import OperatingZone
from .results import EnvelopeCase
from .gas import gas_corrections


def zone_duration(cells, horizon, envelope_weights):
    """Left-endpoint interval integration, not a fabricated failure probability."""
    total, weights = 0.0, 0.0
    for envelope in EnvelopeCase:
        rows = sorted((c for c in cells if c.envelope == envelope), key=lambda c:c.month)
        if not rows:
            continue
        duration = sum(
            max(0, b.month-a.month) for a,b in zip(rows,rows[1:])
            if a.converged and a.zone in {OperatingZone.BEP, OperatingZone.OPERATING_RANGE}
        )
        w = envelope_weights.get(envelope.value, 0)
        total += w * duration
        weights += w
    return total/weights if weights else 0.0


def rank_candidates(candidates, case, catalog, config):
    decorated = []
    for c in candidates:
        pump = catalog.pump(c.configuration.pump_id)
        kq, _ = gas_corrections(c.design_point.free_gas_fraction_at_intake)
        if case.engineering.gas_kq_override and c.design_point.free_gas_fraction_at_intake > 0:
            kq = case.engineering.gas_kq_override.value
        bep = pump.bep_flow_bpd * c.configuration.frequency_hz / pump.frequency_ref_hz
        # Use the volumetric rate at pump intake, not surface stock-tank
        # liquid, against a gas-derated pump curve.
        distance = abs(c.design_point.total_fluid_intake_bpd/(bep*kq)-1) if kq and c.design_point.converged and not c.design_point.evaluated_below_target else None
        duration = zone_duration(c.cells_sampled, case.trajectory.horizon_months, config.scoring.envelope_weights)
        cable=next((x for x in catalog.cables if x.id==c.configuration.cable_id),None)
        cost=pump.price_per_stage*c.configuration.stages+cable.price_per_ft*(c.configuration.setting_depth_md_ft+100) if pump.price_per_stage is not None and cable and cable.price_per_ft is not None else None
        decorated.append(c.model_copy(update={
            "zone_duration_months": duration,
            "target_bep_distance": distance,
            "ranking_mode":case.engineering.ranking_mode,
            "comparable_cost":cost,
        }))
    life = sorted(decorated, key=lambda c:(-c.zone_duration_months, -c.score.efficiency_avg_frac, len(c.soft_violations), c.configuration.config_id))
    bep = sorted(decorated, key=lambda c:(
        c.target_bep_distance if c.target_bep_distance is not None else math.inf,
        c.design_point.shaft_nameplate_utilization if c.design_point.shaft_nameplate_utilization is not None else math.inf,
        -c.score.efficiency_avg_frac, c.configuration.config_id,
    ))
    efficient = sorted(decorated,key=lambda c:(-c.score.efficiency_avg_frac,-c.zone_duration_months,c.configuration.config_id))
    ranks = [{c.configuration.config_id:i+1 for i,c in enumerate(seq)} for seq in (life,bep,efficient)]
    order = life if case.engineering.ranking_mode == "run_life" else bep
    preference=case.engineering.applied_project_preference or {}
    if preference.get("confirmed") and preference.get("prefer_lower_cost"):
        order=sorted(order,key=lambda c:(c.comparable_cost if c.comparable_cost is not None else math.inf, -c.zone_duration_months,-c.score.efficiency_avg_frac))
    result = [c.model_copy(update={"run_life_rank":ranks[0][c.configuration.config_id],"bep_rank":ranks[1][c.configuration.config_id],"efficiency_rank":ranks[2][c.configuration.config_id]}) for c in order]
    # Keep every non-dominated duration/efficiency alternative plus the other
    # mode's leader, even if outside the UI's main ten-candidate slice.
    frontier = [c for c in decorated if not any(
        d.zone_duration_months >= c.zone_duration_months
        and d.score.efficiency_avg_frac >= c.score.efficiency_avg_frac
        and (d.zone_duration_months > c.zone_duration_months or d.score.efficiency_avg_frac > c.score.efficiency_avg_frac)
        for d in decorated
    )]
    chosen = {c.configuration.config_id:c for c in frontier + life[:1] + bep[:1] + efficient[:1] + order[:1]}
    comparison = [{
        "config_id": c.configuration.config_id,
        "pump":c.configuration.pump_model,"stages":c.configuration.stages,
        "frequency_hz":c.configuration.frequency_hz,"zone_duration_months":c.zone_duration_months,
        "efficiency_frac":c.score.efficiency_avg_frac if any(cell.converged for cell in c.cells_sampled) else None, "target_bep_distance":c.target_bep_distance,
        "run_life_rank":ranks[0][c.configuration.config_id],
        "bep_rank":ranks[1][c.configuration.config_id],
        "synthetic":c.synthetic,
        "head_margin_frac":c.design_point.head_margin_frac if c.design_point.converged else None,
        "comparable_cost":c.comparable_cost,
        "cost_basis":"Pump stages plus cable only; motor, protector, installation and energy excluded. No cost inferred when prices are absent.",
    } for c in chosen.values()]
    return result, sorted(comparison,key=lambda c:(c["run_life_rank"],c["bep_rank"]))


def thrust_assessment(pump, seal, *, head_ft, sg, stages, path_override=None):
    path = path_override or pump.pump_type
    delta_p = max(0.0, head_ft) * .433 * sg if head_ft is not None else None
    area = math.pi * pump.shaft_diameter_in**2/4 if pump.shaft_diameter_in else None
    shaft = delta_p * area if area is not None and delta_p is not None else None
    stack = pump.impeller_thrust_lb_per_stage * stages if pump.impeller_thrust_lb_per_stage is not None else None
    load = shaft if path == "floater" else shaft+stack if path == "compression" and shaft is not None and stack is not None else None
    capacity = seal.thrust_bearing_capacity_lb if seal else None
    return {
        "path":path, "source":"engineer_corrected" if path_override else "catalog",
        "delta_pressure_psi":delta_p, "shaft_area_in2":area,
        "shaft_thrust_lb":shaft, "impeller_stack_thrust_lb":stack if path=="compression" else None,
        "load_lb":load,"capacity_lb":capacity,
        "margin_lb":capacity-load if capacity is not None and load is not None else None,
        "recommend_tandem":bool(load is not None and capacity is not None and load > capacity),
        "missing":[name for name,v in [("pump type",path),("shaft diameter",area),("load",load),("bearing capacity",capacity)] if v is None],
        "assembly_note":"Shim shaft at coupling so the impeller does not contact the diffuser; stage thrust transfers to shaft." if path=="compression" else "Floater stage thrust is absorbed locally by stage washers.",
        "synthetic":pump.synthetic or bool(seal and seal.synthetic),
    }


def tapered_preview(engine, case, c, point):
    """Editable two-section screening assembly, not a vendor multiphase solver.

    Gas compression uses constant Z,T and no new dissolution. The approximation
    is explicit so synthetic demonstration does not masquerade as PVT validation.
    """
    intake = engine._intake_for(case, point, c.configuration.setting_depth_md_ft)
    if intake.free_gas_fraction < .10:
        return None
    pressure = intake.pip_psi
    gas0 = intake.free_gas_rate_intake_bpd
    liquid = intake.total_liquid_intake_bpd
    n1 = max(1,c.configuration.stages//2)
    specs=case.engineering.tapered_sections
    counts = [int(s["stages"]) for s in specs] if specs else [n1,max(1,c.configuration.stages-n1)]
    rows = []
    for i,n in enumerate(counts):
        gas = gas0*intake.pip_psi/max(pressure,1)
        flow = liquid+gas
        beta=gas/flow
        kq,kh=gas_corrections(beta)
        if case.engineering.gas_kq_override: kq=case.engineering.gas_kq_override.value
        if case.engineering.gas_kh_override: kh=case.engineering.gas_kh_override.value
        candidates=[p for p in engine.catalog.pumps if p.housing_od_in < (case.geometry.min_casing_id_to_depth(c.configuration.setting_depth_md_ft) or 0)]
        if not candidates or kq is None or kh is None:
            return {"status":"uncomputed", "sections":rows, "note":"Section requires catalog geometry and numeric Kq/Kh; no coefficient extrapolation."}
        requested=specs[i].get("pump_id") if specs else None
        pump=next((p for p in candidates if p.id==requested),None) if requested else min(candidates,key=lambda p:abs(flow/(p.bep_flow_bpd*c.configuration.frequency_hz/p.frequency_ref_hz*kq)-1))
        if pump is None:
            return {"status":"uncomputed","sections":rows,"note":"Requested section pump is absent or cannot pass the known casing restriction."}
        from .curves import total_head_ft, rate_within_domain
        curve=scale_curve_to_frequency(pump,c.configuration.frequency_hz,engine.config.correlations.frequency_curves)
        head=total_head_ft(curve,flow/kq,n,kh) if rate_within_domain(curve,flow/kq) else None
        seal=next(iter(engine.catalog.seals_for_series(pump.series)),None)
        path=case.engineering.seal_thrust_paths.get(f"section-{i+1}")
        rows.append({
            "section_id":f"section-{i+1}","position":"lower" if i==0 else "upper",
            "pump_id":pump.id,"pump":pump.model,"stages":n,"volumetric_rate_bpd":flow,
            "gas_fraction":beta,"inlet_pressure_psi":pressure,"head_ft":head,
            "kq":kq,"kh":kh,"synthetic":pump.synthetic,
            "thrust":thrust_assessment(pump,seal,head_ft=head,sg=intake.mixture_sg,stages=n,path_override=path),
        })
        if head is not None: pressure += .433*intake.mixture_sg*head
    return {"status":"screening_only", "sections":rows, "note":"Two-section taper/overstage proposal. Gas compression: constant Z,T, no dissolution. Couplings, full interstage PVT and complete assembly not verified; not the ranked single-pump configuration."}


def engineering_details(engine, case, c, point):
    config=c.configuration
    pump=engine.catalog.pump(config.pump_id)
    curve=scale_curve_to_frequency(pump,config.frequency_hz,engine.config.correlations.frequency_curves)
    cell,sizing=engine._evaluate_cell(case=case,config=config,pump=pump,curve=curve,point=point,size_equipment=False)
    gas=sizing.get("gas")
    if not gas:
        return {"status":"uncomputed", "note":"No numerical performance claim. Resolve the disclosed inputs/curve-domain gap."}
    cable=sizing.get("cable")
    rows=cable.decision_surface if cable else []
    # Rebuild alternatives at the installed motor's worst nameplate/current.
    motor=sizing.get("motor")
    if motor:
        from .cable import size_cable
        trials=[]
        frequencies=case.constraints.electrical.achievable_frequencies()
        for frequency in frequencies:
            scaled=scale_curve_to_frequency(pump,frequency,engine.config.correlations.frequency_curves)
            _, trial=engine._evaluate_cell(case=case,config=config.model_copy(update={"frequency_hz":frequency}),pump=pump,curve=scaled,point=point,size_equipment=False)
            if trial.get("motor"): trials.append((frequency,trial["motor"]))
        current=max([motor.motor.amps,motor.full_load_amps_b14_1 or 0,motor.operating_amps]+[m.operating_amps for _,m in trials])
        assessed=size_cable(
            catalog=engine.catalog,motor=motor,
            setting_depth_md_ft=config.setting_depth_md_ft,
            casing_id_in=case.geometry.min_casing_id_to_depth(config.setting_depth_md_ft),
            pump_od_in=pump.housing_od_in,fluid_temp_f=cell.intake_temp_f,
            cooling_velocity_ft_s=motor.cooling_velocity_ft_s,water_cut_frac=point.water_cut_frac,
            thresholds=engine.config.thresholds.electrical,
            available_surface_voltage_v=case.constraints.electrical.available_surface_voltage_v.value if case.constraints.electrical.available_surface_voltage_v else None,
            sizing_current_a=current, selected_cable_id=config.cable_id,
        )
        rows=assessed.decision_surface if assessed else []
        for row in rows:
            cable_record=next(x for x in engine.catalog.cables if x.id==row["cable_id"])
            single=engine.catalog.model_copy(update={"cables":[cable_record]})
            passing=[]
            for f,m in trials:
                trial_cable=size_cable(
                    catalog=single,motor=m,setting_depth_md_ft=config.setting_depth_md_ft,
                    casing_id_in=case.geometry.min_casing_id_to_depth(config.setting_depth_md_ft),
                    pump_od_in=pump.housing_od_in,fluid_temp_f=cell.intake_temp_f,
                    cooling_velocity_ft_s=m.cooling_velocity_ft_s,water_cut_frac=point.water_cut_frac,
                    thresholds=engine.config.thresholds.electrical,
                    available_surface_voltage_v=case.constraints.electrical.available_surface_voltage_v.value if case.constraints.electrical.available_surface_voltage_v else None,
                )
                if trial_cable and not trial_cable.decision_surface[0]["warnings"]:
                    passing.append(f)
            row["verified_frequencies_hz"]=passing
            # Never imply continuous admissibility across missing/failed trials.
            contiguous=[]
            for f in frequencies:
                if f not in passing: break
                contiguous.append(f)
            row["last_contiguous_frequency_hz"]=contiguous[-1] if contiguous else None
            row["operating_ceiling_hz"]=case.engineering.operating_ceiling_hz.value if case.engineering.operating_ceiling_hz else None
            ceiling=row["operating_ceiling_hz"]
            required=[f for f in frequencies if ceiling is not None and f<=ceiling]
            row["ceiling_assessment"]=(
                "No ceiling committed" if ceiling is None else
                "All sampled frequencies through ceiling meet cable guidelines at this well state"
                if required and ceiling in frequencies and all(f in passing for f in required)
                else "Ceiling not verified across all required frequency samples"
            )
            row["frequency_note"]="Sampled at current well state; not a future-temperature guarantee."
            row["sizing_basis"]="Maximum of nameplate/B.14.1 current and modeled operating current across the drive frequency grid."
    return {
        "status":"calculated","gas":gas.model_dump(mode="json"),
        "thrust":thrust_assessment(pump,sizing.get("seal"),head_ft=cell.head_developed_ft,
            sg=cell.mixture_sg,stages=config.stages,
            path_override=case.engineering.seal_thrust_paths.get("main")),
        "cables":rows, "tapered":tapered_preview(engine,case,c,point),
        "bend_thresholds":{"running":case.constraints.geometry.max_dogleg_deg_per_100ft.value,
            "setting":case.constraints.geometry.max_setting_dogleg_deg_per_100ft.value,
            "note":"External bend/stress-analysis triggers, not a geometric-clearance calculation."},
        "limits_are_warnings":True,
        "seal_chamber":{"selected":getattr(sizing.get("seal"),"chamber_type",None),
            "status":"guidance_missing",
            "note":"No numeric inclination/chamber guidance is supplied by this catalog. Chamber suitability is unverified, not rejected; obtain the selected protector's modular combination/deviation guidance."},
        "preference":{"applied":case.engineering.applied_project_preference,
            "scope":"explicitly confirmed project preference in this operator's perimeter; opt-in application, never inferred"},
    }
