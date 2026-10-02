"""Schema/completeness and conservative physical sanity checks for the seed ESP catalog."""
import json, math
from pathlib import Path
import numpy as np
ROOT=Path('/home/user/workspace/esp/data/catalog')
errors=[]; warnings=[]; counts={}

def load(name):
    try:
        x=json.loads((ROOT/name).read_text())
        if not isinstance(x,list): err(name,'top level must be array')
        counts[name]=len(x); return x
    except Exception as exc:
        err(name,f'not valid JSON: {exc}'); return []
def err(where,msg): errors.append(f'{where}: {msg}')
def req(rec, fields, where):
    for f in fields:
        if f not in rec: err(where,f'missing {f}')
def pos(value, where, nullable=False):
    if value is None and nullable: return
    if not isinstance(value,(int,float)) or isinstance(value,bool) or value<=0: err(where,'must be positive')
def urls(rec,where):
    u=rec.get('source_urls')
    if not isinstance(u,list) or not u or not all(isinstance(x,str) and x.startswith(('http://','https://')) for x in u): err(where,'must have nonempty public source_urls')

pumps=load('pumps.json')
for p in pumps:
    w='pumps/'+str(p.get('id'))
    req(p,['id','manufacturer','model','series','housing_od_in','min_casing_id_in','stage_type','frequency_ref_hz','bep_flow_bpd','recommended_range_bpd','downthrust_limit_bpd','upthrust_limit_bpd','max_stages','shaft_hp_limit','thrust_bearing_capacity_lb','curve','curve_fit','source_urls','data_quality','notes'],w)
    for k in ['series','housing_od_in','min_casing_id_in','frequency_ref_hz','bep_flow_bpd','max_stages','shaft_hp_limit']: pos(p.get(k),w+'/'+k)
    if p.get('stage_type') not in ['mixed_flow','radial','axial']: err(w,'invalid stage_type')
    if p.get('data_quality') not in ['digitized_from_datasheet','published_table','parametric_estimate']: err(w,'invalid data_quality')
    rr=p.get('recommended_range_bpd',[])
    if not isinstance(rr,list) or len(rr)!=2 or not all(isinstance(x,(int,float)) for x in rr) or rr[0]>=rr[1]: err(w,'invalid recommended_range_bpd')
    else:
        if not (rr[0]<=p['bep_flow_bpd']<=rr[1]): err(w,'BEP outside recommended range')
        if p['downthrust_limit_bpd']!=rr[0] or p['upthrust_limit_bpd']!=rr[1]: err(w,'thrust limits do not equal stated operating range')
    c=p.get('curve',{}); req(c,['basis','sg_basis','points'],w+'/curve')
    pts=c.get('points',[])
    if len(pts)<5: err(w,'need >=5 curve points')
    q=np.array([x.get('q_bpd',-1) for x in pts],dtype=float); h=np.array([x.get('head_ft',-1) for x in pts],dtype=float); e=np.array([x.get('eff_pct',-1) for x in pts],dtype=float); bhp=np.array([x.get('bhp_hp',-1) for x in pts],dtype=float)
    if np.any(np.diff(q)<=0): err(w,'curve flows must rise')
    if np.any(np.diff(h)>=1e-9): err(w,'head must decrease with flow')
    if np.any(h<=0) or np.any(e<=0) or np.any(bhp<=0): err(w,'curve head/eff/BHP must be positive')
    if len(pts):
        peak=q[np.argmax(e)]
        if abs(peak-p['bep_flow_bpd'])>0.05*p['bep_flow_bpd']: err(w,'efficiency peak not near BEP')
    fit=p.get('curve_fit',{})
    req(fit,['head_coeffs','eff_coeffs','bhp_coeffs','form'],w+'/curve_fit')
    if fit.get('form')!='polynomial_in_q_bpd_descending': err(w,'unexpected curve_fit form')
    if any(len(fit.get(k,[])) not in [4,5,6] for k in ['head_coeffs','eff_coeffs','bhp_coeffs']): err(w,'fit coefficient degree must be 3-5')
    if len(pts) and fit:
        for y,k in [(h,'head_coeffs'),(e,'eff_coeffs'),(bhp,'bhp_coeffs')]:
            pred=np.polyval(fit[k],q); rel=np.max(np.abs(pred-y)/np.maximum(np.abs(y),1e-9))
            if rel>0.03: err(w,f'{k} does not reproduce points within 3% ({rel:.2%})')
    urls(p,w)
if len(pumps)<12: err('pumps','fewer than 12 records')

motors=load('motors.json')
for m in motors:
    w='motors/'+str(m.get('id')); req(m,['id','manufacturer','series','od_in','min_casing_id_in','hp','volts','amps','rpm_synchronous_60hz','length_ft','max_winding_temp_f','motor_type','source_urls','data_quality'],w)
    for k in ['series','od_in','min_casing_id_in','hp','volts','amps','length_ft','max_winding_temp_f']: pos(m.get(k),w+'/'+k)
    # B.16: the induction/PM fork decides VSD necessity and which thermal gate
    # applies, so no record may be silent about which machine it is.
    if m.get('motor_type') not in ('induction','permanent_magnet'):
        err(w,f"motor_type={m.get('motor_type')!r} must be 'induction' or 'permanent_magnet'")
    # A demagnetization limit on an induction motor would make the magnet gate
    # evaluate a machine that has no magnets.
    if m.get('demag_temp_f') is not None:
        if m.get('motor_type')!='permanent_magnet':
            err(w,'demag_temp_f is set on a non-permanent-magnet motor')
        pos(m.get('demag_temp_f'),w+'/demag_temp_f')
    # PF and efficiency are optional, but a present value must be physical.
    # None means "not published" and must never be filled with a range midpoint:
    # a default silently changes B.14.1 I_FL and B.15.1 kVA on every design.
    if m.get('power_factor') is not None and not 0.0<m['power_factor']<=1.0:
        err(w,f"power_factor={m['power_factor']} outside (0,1]")
    if m.get('efficiency') is not None and not 0.0<m['efficiency']<1.0:
        err(w,f"efficiency={m['efficiency']} outside (0,1)")
    # hp*746/(sqrt(3)*V*I) is combined motor power factor * efficiency. 0.35-1.0 accommodates published PM nameplate selections.
    apparent=math.sqrt(3)*m['volts']*m['amps']; combined=m['hp']*746/apparent
    if not 0.35<=combined<=1.0: err(w,f'HP/volts/amps combined PF*eff={combined:.3f} outside 0.35-1.0')
    if m.get('rpm_synchronous_60hz') is not None: pos(m['rpm_synchronous_60hz'],w+'/rpm')
    urls(m,w)
if len(motors)<10: err('motors','fewer than 10 records')

cables=load('cables.json')
for c in cables:
    w='cables/'+str(c.get('id')); req(c,['id','awg','conductor_area_cmil','ampacity_a','resistance_ohm_per_1000ft_at_77f','temp_coeff_per_f','od_flat_in','od_round_in','max_temp_f','armor','source_urls'],w)
    for k in ['awg','conductor_area_cmil','ampacity_a','resistance_ohm_per_1000ft_at_77f','temp_coeff_per_f','max_temp_f']: pos(c.get(k),w+'/'+k)
    if c.get('od_flat_in') is None and c.get('od_round_in') is None: err(w,'needs flat or round OD')
    for k in ['od_flat_in','od_round_in']: pos(c.get(k),w+'/'+k,nullable=True)
    urls(c,w)
if not {1,2,4,6}.issubset({x.get('awg') for x in cables}): err('cables','does not cover AWG 1,2,4,6')
if not any(x.get('od_flat_in') for x in cables) or not any(x.get('od_round_in') for x in cables): err('cables','needs both flat and round')

gas=load('gas_handling.json')
for g in gas:
    w='gas/'+str(g.get('id')); req(g,['id','type','series','od_in','hp_consumed','max_free_gas_fraction_handled','separation_efficiency_pct_range','source_urls','notes'],w)
    if g.get('type') not in ['rotary_separator','gas_handler','advanced_gas_handler','static_separator']: err(w,'invalid type')
    pos(g.get('series'),w+'/series')
    for k in ['od_in','hp_consumed','max_free_gas_fraction_handled']: pos(g.get(k),w+'/'+k,nullable=True)
    x=g.get('max_free_gas_fraction_handled')
    if x is not None and x>1: err(w,'gas fraction must be <=1')
    r=g.get('separation_efficiency_pct_range')
    if r is not None and (not isinstance(r,list) or len(r)!=2 or min(r)<0 or max(r)>100 or r[0]>r[1]): err(w,'invalid separation efficiency range')
    urls(g,w)

seals=load('seals.json')
for s in seals:
    w='seals/'+str(s.get('id')); req(s,['id','series','od_in','thrust_bearing_capacity_lb','max_shaft_hp','length_ft','source_urls'],w)
    for k in ['series','od_in','length_ft']: pos(s.get(k),w+'/'+k)
    for k in ['thrust_bearing_capacity_lb','max_shaft_hp']: pos(s.get(k),w+'/'+k,nullable=True)
    urls(s,w)

cas=load('casing.json')
for c in cas:
    w='casing/'+str(c.get('size_in'))+'/'+str(c.get('weight_lb_per_ft')); req(c,['size_in','weight_lb_per_ft','id_in','drift_id_in'],w)
    for k in ['size_in','weight_lb_per_ft','id_in','drift_id_in']: pos(c.get(k),w+'/'+k)
    if c.get('drift_id_in',0)>=c.get('id_in',0): err(w,'drift must be smaller than ID')
if not {4.5,5.5,7.0,7.625,8.625,9.625}.issubset({x.get('size_in') for x in cas}): err('casing','missing common 4.5 through 9.625 sizes')

print('ESP seed catalog validation report')
for k,v in counts.items(): print(f'  {k}: {v} records')
if warnings:
    print('WARNINGS:'); [print('  '+x) for x in warnings]
if errors:
    print('FAILURES:'); [print('  '+x) for x in errors]; raise SystemExit(1)
print('PASS: all schemas and physical sanity checks passed.')
