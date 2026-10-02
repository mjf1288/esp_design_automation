"""Fit degree-5 q_bpd polynomials to seed pump curves and validate their behavior."""
import json
from pathlib import Path
import numpy as np

PATH=Path('/home/user/workspace/esp/data/catalog/pumps.json')
REPORT=Path('/home/user/workspace/esp/data/catalog/fit_report.json')

def coeffs_and_error(q,y,degree=5):
    c=np.polyfit(q,y,degree)
    pred=np.polyval(c,q)
    # All synthesized values are positive; use a relative max error for a transparent check.
    rel=float(np.max(np.abs(pred-y)/np.maximum(np.abs(y),1e-9)))
    return [float(v) for v in c],rel

def main():
    pumps=json.loads(PATH.read_text())
    report=[]
    failures=[]
    for p in pumps:
        pts=p['curve']['points']
        q=np.array([x['q_bpd'] for x in pts],dtype=float)
        h=np.array([x['head_ft'] for x in pts],dtype=float)
        e=np.array([x['eff_pct'] for x in pts],dtype=float)
        b=np.array([x['bhp_hp'] for x in pts],dtype=float)
        hc,he=coeffs_and_error(q,h)
        ec,ee=coeffs_and_error(q,e)
        bc,be=coeffs_and_error(q,b)
        p['curve_fit']={'head_coeffs':hc,'eff_coeffs':ec,'bhp_coeffs':bc,
                        'form':'polynomial_in_q_bpd_descending'}
        lo,hi=p['recommended_range_bpd']
        dense=np.linspace(lo,hi,201)
        fitted_h=np.polyval(hc,dense)
        monotonic=bool(np.all(np.diff(fitted_h)<=1e-7))
        maxerr=max(he,ee,be)
        row={'id':p['id'],'degree':5,'head_max_relative_error':he,'eff_max_relative_error':ee,
             'bhp_max_relative_error':be,'max_relative_error':maxerr,
             'head_monotonic_decreasing_on_recommended_range':monotonic}
        report.append(row)
        if maxerr>0.03 or not monotonic:
            failures.append(row)
    PATH.write_text(json.dumps(pumps,indent=2)+'\n')
    REPORT.write_text(json.dumps({'fit_degree':5,'records':report,'failures':failures},indent=2)+'\n')
    print(f'Fitted {len(pumps)} pump curves at degree 5.')
    for r in report:
        print(f"{r['id']}: max fit error={r['max_relative_error']:.4%}; head decreasing={r['head_monotonic_decreasing_on_recommended_range']}")
    if failures:
        raise SystemExit(f'Fit validation failed for {len(failures)} pump(s)')

if __name__=='__main__': main()
