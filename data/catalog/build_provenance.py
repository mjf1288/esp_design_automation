import json
from pathlib import Path
R=Path('/home/user/workspace/esp/data/catalog')
files=['pumps.json','motors.json','cables.json','gas_handling.json','seals.json','casing.json']
data={f:json.loads((R/f).read_text()) for f in files}
def urls(r): return '<br>'.join(r.get('source_urls',[]))
def esc(x): return str(x).replace('|','\\|').replace('\n',' ')
lines=['# ESP Seed Catalog — Provenance','',
'## Purpose and method','',
'This is a **seed dataset for software development**, built only from public manufacturer/product literature and public API casing tables. Every URL below was read while compiling the record. Published data were transcribed only where explicitly visible in those documents. The pump curves are deliberately marked `parametric_estimate`: their shape points were synthesized from a documented standard centrifugal-pump form and fitted in `fit_curves.py`; they are not vendor curve points.','',
'Where a source publishes nominal minimum casing size rather than an internal diameter, `min_casing_id_in` is a conservative mapping to a common casing ID from the casing table. It is explicitly not a vendor fit guarantee. `null` means the public source did not provide a numerical value.','',
'## Pump records','',
'| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |','|---|---|---|---|']
for p in data['pumps.json']:
    lines.append(f"| `{p['id']}` | `{p['data_quality']}` | {urls(p)} | {esc(p['notes'])} |")
lines += ['', '## Motor records','', '| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |','|---|---|---|---|']
for m in data['motors.json']:
    lines.append(f"| `{m['id']}` | `{m['data_quality']}` | {urls(m)} | HP, selected voltage/current option, OD, length and insulation temperature were digitized. {esc(m.get('notes',''))} |")
lines += ['', '## Cable records','', '| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |','|---|---|---|---|']
for c in data['cables.json']:
    lines.append(f"| `{c['id']}` | `{c.get('data_quality','digitized_from_datasheet')}` | {urls(c)} | {esc(c.get('notes',''))} |")
lines += ['', '## Gas-handling records','', '| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |','|---|---|---|---|']
for g in data['gas_handling.json']:
    q='digitized_from_datasheet' if ('Published' in g['notes'] or 'catalog' in g['notes'] or 'source publishes' in g['notes']) else 'mixed_source_fields'
    lines.append(f"| `{g['id']}` | `{q}` | {urls(g)} | {esc(g['notes'])} |")
lines += ['', '## Seal/protector records','', '| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |','|---|---|---|---|']
for s in data['seals.json']:
    lines.append(f"| `{s['id']}` | `{s.get('data_quality','parametric_estimate')}` | {urls(s)} | {esc(s.get('notes',''))} |")
lines += ['', '## Casing records','', '| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |','|---|---|---|---|']
for c in data['casing.json']:
    lines.append(f"| `{c['size_in']} in / {c['weight_lb_per_ft']} lb/ft` | `{c.get('data_quality','published_table')}` | {urls(c)} | ID and drift ID transcribed from the cited public casing table. |")
lines += ['', '## Source notes','',
'* **SLB REDA Electric Submersible Pump Systems Technology Catalog:** https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf — pump series/ranges, the limited published BEP values, shaft limits, motor and protector tables, and gas-device capacities.',
'* **Magnetic Pumping Solutions PM Motor Catalogue (2019):** https://www.magneticpumpingsolutions.com/catalogues/MPS%20PM%20Motor%20Catalogue%20-%20Jan%202019.pdf — PMESP motor nameplate option rows and insulation ratings.',
'* **Kerite/Marmon ESP Cable Brochure:** https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf — cable dimensions, temperatures, conductor data, and manufacturer temperature-correction formula.',
'* **Alkhorayef SPECTRUM Vortex Gas Separator:** https://www.alkhorayefpetroleum.com/Alkhorayef/media/AlkhorayefMedia/PDF/SPECTRUM-Vortex-Gas-Separator.pdf — separator flow/GVF/efficiency claims.',
'* **SLB ESP Gas Devices:** https://www.slb.com/products-and-services/innovating-in-oil-and-gas/completions/artificial-lift/electrical-submersible-pumps/reda-esp-pump-system/esp-gas-devices — product-family AGH/MGH GVF capability.',
'* **Levare Gas Handling Devices:** https://levare.com/storage/app/uploads/public/64d/a31/438/64da31438ea37707606711.pdf — Vapro and vortex-device capability statements.',
'* **Versa-Line API casing handbook:** https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf and **ZC Steel 7-5/8 casing table:** https://www.zc-pipe.com/API-5CT-Casing-Sizes-Dimensions-Weight-Tables-id46268065.html — casing ID/drift rows.',
'', '## LIMITATIONS AND NEXT STEPS','',
'**NOT FOR FIELD DESIGN.** This seed catalog must not be used for actual ESP selection, equipment procurement, well design, operating envelopes, warranty decisions, or field deployment without current vendor validation.',
'',
'1. Obtain vendor data agreements and release-controlled technical catalogs for each manufacturer/series, including current datasheets, model revisions, material trims, and temperature/pressure derates.',
'2. Import official digital pump-curve files (for example CSV, XML, or vendor design-system exports) with full multi-point head, efficiency, BHP, thrust, axial-load, stage-count, viscosity, gas, and frequency corrections. Replace every parameterized curve with these files and preserve document revision metadata.',
'3. Validate all component interfaces by exact part number: motor/protector/pump shaft compatibility, MLE/cable voltage-drop and ampacity at actual temperature/depth, motor operating voltage/current, and casing/tubing drift through couplings and completion restrictions.',
'4. Add well-fluid PVT, solids, corrosive-service, scale, temperature, vibration, gas-separation, motor-cooling, and electrical-system limits. The public sources do not provide a complete engineering envelope.',
'5. Build automated source-revision monitoring, human technical approval, unit tests against vendor examples, and a clearly versioned change-control process before promoting any catalog content to production.',
'']
(R/'PROVENANCE.md').write_text('\n'.join(lines))
print('Wrote PROVENANCE.md with',sum(len(v) for v in data.values()),'record rows')
