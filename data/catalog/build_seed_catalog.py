"""Build source-traceable seed ESP catalog JSON. Curves are deliberately parametric unless stated otherwise."""
import json, math
from pathlib import Path
ROOT=Path('/home/user/workspace/esp/data/catalog')
SLB='https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf'
MPS='https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf'
NOVOMET_MOTOR='https://www.novometgroup.com/assets/files/2019/Cases/Cases%20ENG/bro-esp-permanent-magnet-motor.pdf'
BAKER_450='https://dam.bakerhughes.com/m/b0868a18e68df9b/original/CENtrilift-SP-superior-performance-series-motors.pdf'
MARMON='https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf'
ALK='https://www.alkhorayefpetroleum.com/Alkhorayef/media/AlkhorayefMedia/PDF/SPECTRUM-Vortex-Gas-Separator.pdf'
LEV='https://levare.com/storage/app/uploads/public/64d/a31/438/64da31438ea37707606711.pdf'
SLB_GAS='https://www.slb.com/products-and-services/innovating-in-oil-and-gas/completions/artificial-lift/electrical-submersible-pumps/reda-esp-pump-system/esp-gas-devices'
CASING='https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf'
CASING_7625='https://www.zc-pipe.com/API-5CT-Casing-Sizes-Dimensions-Weight-Tables-id46268065.html'

# Pairs below are source-recorded BEP data. All curve points in this seed are synthesized.
PUMPS=[
 # id, series, model, range, BEP Q, H, eta, bhp, maxstages, shafthp, min casing internal estimate, published_bep
 ('slb-reda-an550',338,'AN550',(400,700),550,55.0,50.0,None,190,94,4.000,False),
 ('slb-reda-d460n',400,'D460N',(200,650),486,35.90,53.00,0.24,183,116,4.892,True),
 ('slb-reda-d1050n',400,'D1050N',(300,1650),1032,26.07,66.51,0.30,126,154,4.892,True),
 ('slb-reda-rc2500',400,'RC2500',(1000,3200),2526,23.92,68.06,0.65,120,240,4.892,True),
 ('slb-reda-gn3200',513,'GN3200',(2200,4100),3150,28.0,68.0,None,100,256,6.000,False),
 ('slb-reda-sn3600',538,'SN3600',(2400,4600),3500,35.0,70.0,None,97,256,6.276,False),
 ('slb-reda-s6000n',538,'S6000N',(3500,7800),5650,32.0,72.0,None,83,487,6.276,False),
 ('slb-reda-s8000n',538,'S8000N',(3500,10500),7000,30.0,73.0,None,81,463,6.276,False),
 ('slb-reda-hn13500',562,'HN13500',(5000,18000),11500,35.0,74.0,None,46,375,6.276,False),
 ('slb-reda-h15500n',562,'H15500N',(11000,20000),15500,30.0,74.0,None,46,637,6.276,False),
 ('slb-reda-j7000n',675,'J7000N',(4500,9000),6750,70.0,73.0,None,44,637,7.921,False),
 ('slb-reda-j8500n',675,'J8500N',(6000,11250),8643,71.97,73.59,6.23,44,637,7.921,True),
 ('slb-reda-j12000n',675,'J12000N',(8000,18500),13000,55.0,75.0,None,44,637,7.921,False),
]
def make_curve(qb,hb,eta,bhp):
    # Standard dimensionless centrifugal-pump forms: head decreases quadratically,
    # efficiency peaks at BEP; BHP increases with rate. Not vendor curve data.
    qs=[0.0,0.4,0.7,1.0,1.3,1.6,1.9]
    if bhp is None:
        bhp=hb*(qb/34.285714)/3960/(eta/100)
    pts=[]
    for x in qs:
        head=hb*(1.20-0.20*x*x)
        eff=eta*max(0.18,1-0.52*(x-1)**2)
        power=bhp*(0.40+0.60*x**1.35)
        pts.append({'q_bpd':round(qb*x,3),'head_ft':round(head,4),'eff_pct':round(eff,4),'bhp_hp':round(power,5)})
    return pts

def stage_type(series):
    return 'radial' if series<=400 else 'mixed_flow'
pumps=[]
for pid,series,model,rr,q,h,eff,bhp,maxst,hplim,minid,realbep in PUMPS:
    note=(f"Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; "
          + (f"BEP Q={q} bpd, H={h} ft/stage, efficiency={eff}%, BHP={bhp} hp were also published. " if realbep else
             f"The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. ")
          + "All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. "
          + f"min_casing_id_in={minid} is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit.")
    pumps.append({'id':pid,'manufacturer':'SLB (Schlumberger REDA)','model':model,'series':series,
      'housing_od_in':{338:3.38,400:4.00,513:5.13,538:5.38,562:5.62,675:6.75}[series], 'min_casing_id_in':minid,
      'stage_type':stage_type(series),'frequency_ref_hz':60,'bep_flow_bpd':q,'recommended_range_bpd':list(rr),
      'downthrust_limit_bpd':rr[0],'upthrust_limit_bpd':rr[1],'max_stages':maxst,'shaft_hp_limit':hplim,'thrust_bearing_capacity_lb':None,
      'curve':{'basis':'per_stage_at_60hz','sg_basis':1.0,'points':make_curve(q,h,eff,bhp)}, 'curve_fit':None,
      'source_urls':[SLB],'data_quality':'parametric_estimate','notes':note})

mot_specs=[
 ('mps-pmesp-375-50','PMESP 375 50 HP',375,3.75,50,541,87,7.62,356),
 ('slb-maximus-375-14','REDA Maximus RA 14.3 HP',375,3.75,14.3,386,27.1,5.7,350),
 ('mps-pmesp-456-50','PMESP 456 50 HP',456,4.56,50,400,95,4.13,356),
 ('mps-pmesp-456-100','PMESP 456 100 HP',456,4.56,100,810,95,6.52,356),
 ('mps-pmesp-456-200','PMESP 456 200 HP',456,4.56,200,1633,95,11.31,356),
 ('mps-pmesp-456-400','PMESP 456 400 HP',456,4.56,400,2337,95,20.89,356),
 ('mps-pmesp-562-100','PMESP 562 100 HP',562,5.62,100,902,107,4.73,356),
 ('mps-pmesp-562-250','PMESP 562 250 HP',562,5.62,250,2271,107,8.83,356),
 ('mps-pmesp-562-400','PMESP 562 400 HP',562,5.62,400,2896,107,12.93,356),
 ('mps-pmesp-738-250','PMESP 738 250 HP',738,7.38,250,2074,132,5.91,356),
 ('mps-pmesp-738-500','PMESP 738 500 HP',738,7.38,500,2082,132,9.19,356),
]
# Exact nameplate combination is represented by the selected voltage/current entry in each published row.
motors=[]
for mid,model,series,od,hp,v,a,l,t in mot_specs:
    url=SLB if mid.startswith('slb') else MPS
    motors.append({'id':mid,'manufacturer':'SLB (Schlumberger REDA)' if mid.startswith('slb') else 'Magnetic Pumping Solutions',
     'series':series,'od_in':od,'min_casing_id_in':4.000 if series==375 else (4.892 if series==456 else (6.276 if series==562 else 7.921)),
     'hp':hp,'volts':v,'amps':a,'rpm_synchronous_60hz':3600 if not mid.startswith('slb') else None,'length_ft':l,'max_winding_temp_f':t,
     'source_urls':[url],'data_quality':'digitized_from_datasheet',
     'notes':'Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement.'})
# These records widen series-selector coverage. Their sources establish the product family
# but omit a full numerical nameplate row; every missing numerical field is explicitly
# estimated and the records remain unsuitable for field selection.
motors += [
 {'id':'novomet-pmm-400-family-estimate','manufacturer':'Novomet','series':400,'od_in':4.00,'min_casing_id_in':4.000,'hp':60,'volts':800,'amps':57,'rpm_synchronous_60hz':3600,'length_ft':7.0,'max_winding_temp_f':356,'source_urls':[NOVOMET_MOTOR,MPS],'data_quality':'parametric_estimate','notes':'Novomet public PMM literature identifies a 400-series family but publishes no numerical nameplate values. OD, HP, volts, amps, synchronous speed, length, temperature and casing mapping are conservative parametric estimates scaled from comparable published MPS PMESP records; not vendor data.'},
 {'id':'baker-hughes-centrilift-450sp-estimate','manufacturer':'Baker Hughes','series':450,'od_in':4.50,'min_casing_id_in':4.892,'hp':264,'volts':4500,'amps':50,'rpm_synchronous_60hz':3600,'length_ft':14.0,'max_winding_temp_f':450,'source_urls':[BAKER_450],'data_quality':'parametric_estimate','notes':'Baker Hughes publishes 450SP: 4.50-in OD, 264 hp, 4,500 V, and 450F conductor-temperature capability. Amps, synchronous speed, length, winding-temperature interpretation and casing mapping are conservative estimates because the public datasheet omits them.'},
 {'id':'novomet-pmm-540-family-estimate','manufacturer':'Novomet','series':540,'od_in':5.40,'min_casing_id_in':6.000,'hp':300,'volts':2500,'amps':90,'rpm_synchronous_60hz':3600,'length_ft':13.0,'max_winding_temp_f':356,'source_urls':[NOVOMET_MOTOR,MPS],'data_quality':'parametric_estimate','notes':'Novomet public PMM literature identifies a 540-series family but publishes no numerical nameplate values. OD, HP, volts, amps, synchronous speed, length, temperature and casing mapping are conservative parametric estimates scaled from comparable published MPS PMESP records; not vendor data.'},
]

# 77F resistance is publisher's 20C stranded conductor value (ohm/100ft) times 10 and temp corrected using publisher TCF 1+0.00214*(T-77).
awg_data={1:(83770,191,1.26,2.00,1.41),2:(66407,164,1.59,1.94,1.32),4:(41719,121,2.54,1.78,1.23),6:(26240,95,4.03,1.62,1.12)}
cables=[]
for awg,(cmil,amp,res,flat,roundod) in awg_data.items():
    cables.append({'id':f'kerite-mtf1-flat-awg-{awg}','awg':awg,'conductor_area_cmil':cmil,'ampacity_a':amp,
      'resistance_ohm_per_1000ft_at_77f':res,'temp_coeff_per_f':0.00214,'od_flat_in':flat,'od_round_in':None,'max_temp_f':400,
      'armor':'0.020 in galvanized steel tape','source_urls':[MARMON],
      'data_quality':'digitized_from_datasheet' if awg!=6 else 'parametric_estimate',
      'notes':'Dimensions and max temperature digitized from MTF1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6.'})
    cables.append({'id':f'kerite-mtr1-round-awg-{awg}','awg':awg,'conductor_area_cmil':cmil,'ampacity_a':amp,
      'resistance_ohm_per_1000ft_at_77f':res,'temp_coeff_per_f':0.00214,'od_flat_in':None,'od_round_in':roundod,'max_temp_f':400,
      'armor':'0.025 in galvanized steel tape','source_urls':[MARMON],
      'data_quality':'digitized_from_datasheet' if awg!=6 else 'parametric_estimate',
      'notes':'Dimensions and max temperature digitized from MTR1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6.'})

gas=[
 {'id':'alkhorayef-spectrum-vgs-400','type':'static_separator','series':400,'od_in':4.00,'hp_consumed':2,'max_free_gas_fraction_handled':0.80,'separation_efficiency_pct_range':[85,85],'source_urls':[ALK],'notes':'Published SPECTRUM vortex gas separator; source labels 2 @ 60 Hz under HP consumed (unit interpreted as hp). Tandem separation efficiency is published as 85%.'},
 {'id':'alkhorayef-spectrum-vgs-513','type':'static_separator','series':513,'od_in':5.38,'hp_consumed':7,'max_free_gas_fraction_handled':0.80,'separation_efficiency_pct_range':[85,85],'source_urls':[ALK],'notes':'Published SPECTRUM vortex gas separator; catalog labels this 513 series but gives 5.38-in OD. Source labels 7 @ 60 Hz under HP consumed (unit interpreted as hp).'},
 {'id':'slb-reda-ars-338','type':'rotary_separator','series':338,'od_in':3.38,'hp_consumed':None,'max_free_gas_fraction_handled':None,'separation_efficiency_pct_range':None,'source_urls':[SLB],'notes':'ARS rotary gas separator. Catalog publishes series and length but not HP consumed, free-gas fraction or separation efficiency.'},
 {'id':'slb-reda-drs-es-400','type':'rotary_separator','series':400,'od_in':4.00,'hp_consumed':None,'max_free_gas_fraction_handled':None,'separation_efficiency_pct_range':None,'source_urls':[SLB],'notes':'DRS-ES rotary gas separator; documented 2.6-ft length. Requested performance fields are not published.'},
 {'id':'slb-reda-agh-d5-21','type':'advanced_gas_handler','series':400,'od_in':4.00,'hp_consumed':None,'max_free_gas_fraction_handled':0.45,'separation_efficiency_pct_range':None,'source_urls':[SLB,SLB_GAS],'notes':'D5-21 capacity 500-2,100 bpd from catalog; the public SLB product page states AGH systems can handle up to 45% GVF at low intake pressure. This is product-family capability, not a device-specific test point.'},
 {'id':'slb-reda-mgh-d8-42','type':'gas_handler','series':400,'od_in':4.00,'hp_consumed':None,'max_free_gas_fraction_handled':0.75,'separation_efficiency_pct_range':None,'source_urls':[SLB,SLB_GAS],'notes':'D8-42 capacity 800-4,200 bpd from catalog; public MGH product-family capability up to 75% GVF. No HP consumed figure is published.'},
 {'id':'levare-400-vapro-2000','type':'gas_handler','series':400,'od_in':None,'hp_consumed':None,'max_free_gas_fraction_handled':0.70,'separation_efficiency_pct_range':None,'source_urls':[LEV],'notes':'Published capacity 717-2,038 bpd and max 70% free gas at pump intake; OD, HP consumption, and efficiency unavailable.'},
 {'id':'levare-538-vapro-12500','type':'gas_handler','series':538,'od_in':None,'hp_consumed':None,'max_free_gas_fraction_handled':0.65,'separation_efficiency_pct_range':None,'source_urls':[LEV],'notes':'Published capacity 4,000-13,000 bpd; source publishes 65% max free gas for 538 Vapro product family. OD, HP consumption, and efficiency unavailable.'},
]
seals=[]
for pid,ser,od,length,config in [
 ('slb-reda-protector-325-bsb',325,3.25,5.7,'BSB'),('slb-reda-protector-400-lsl',400,4.00,5.8,'LSL'),
 ('slb-reda-protector-540-bsb',540,5.13,6.5,'BSB'),('slb-reda-protector-738-66l-hl',738,7.38,6.4,'66L-HL')]:
 seals.append({'id':pid,'series':ser,'od_in':od,'thrust_bearing_capacity_lb':None,'max_shaft_hp':None,'length_ft':length,'source_urls':[SLB],
 'data_quality':'parametric_estimate','notes':f'Length and series/configuration are digitized from SLB catalog. The catalog does not publish OD, numerical thrust-bearing capacity, or max shaft HP; OD is a nominal-series proxy only. {config} nomenclature is as published.'})

casing=[]
for row in [
 (4.5,11.6,4.000,3.875),(4.5,13.5,3.920,3.795),(5.5,17.0,4.892,4.767),(5.5,20.0,4.778,4.653),
 (7.0,23.0,6.366,6.241),(7.0,26.0,6.276,6.151),(7.625,24.0,7.025,6.900),(7.625,26.4,6.969,6.844),
 (8.625,32.0,7.921,7.796),(8.625,36.0,7.825,7.700),(9.625,36.0,8.921,8.765),(9.625,47.0,8.681,8.525)]:
 size=row[0]; casing.append({'size_in':size,'weight_lb_per_ft':row[1],'id_in':row[2],'drift_id_in':row[3],
  'source_urls':[CASING_7625 if size==7.625 else CASING],'data_quality':'published_table'})

def dump(name,x):
 (ROOT/name).write_text(json.dumps(x,indent=2)+'\n')
dump('pumps.json',pumps); dump('motors.json',motors); dump('cables.json',cables); dump('gas_handling.json',gas); dump('seals.json',seals); dump('casing.json',casing)
print('Wrote',len(pumps),'pumps,',len(motors),'motors,',len(cables),'cables,',len(gas),'gas devices,',len(seals),'seals,',len(casing),'casing rows')
