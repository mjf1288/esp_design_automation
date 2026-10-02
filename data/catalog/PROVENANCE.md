# ESP Seed Catalog — Provenance

## Purpose and method

This is a **seed dataset for software development**, built only from public manufacturer/product literature and public API casing tables. Every URL below was read while compiling the record. Published data were transcribed only where explicitly visible in those documents. The pump curves are deliberately marked `parametric_estimate`: their shape points were synthesized from a documented standard centrifugal-pump form and fitted in `fit_curves.py`; they are not vendor curve points.

Where a source publishes nominal minimum casing size rather than an internal diameter, `min_casing_id_in` is a conservative mapping to a common casing ID from the casing table. It is explicitly not a vendor fit guarantee. `null` means the public source did not provide a numerical value.

## Pump records

| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |
|---|---|---|---|
| `slb-reda-an550` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=4.0 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-d460n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; BEP Q=486 bpd, H=35.9 ft/stage, efficiency=53.0%, BHP=0.24 hp were also published. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=4.892 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-d1050n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; BEP Q=1032 bpd, H=26.07 ft/stage, efficiency=66.51%, BHP=0.3 hp were also published. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=4.892 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-rc2500` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; BEP Q=2526 bpd, H=23.92 ft/stage, efficiency=68.06%, BHP=0.65 hp were also published. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=4.892 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-gn3200` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=6.0 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-sn3600` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=6.276 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-s6000n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=6.276 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-s8000n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=6.276 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-hn13500` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=6.276 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-h15500n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=6.276 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-j7000n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=7.921 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-j8500n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; BEP Q=8643 bpd, H=71.97 ft/stage, efficiency=73.59%, BHP=6.23 hp were also published. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=7.921 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |
| `slb-reda-j12000n` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Published catalog inputs digitized: normal housing OD/series, nominal minimum casing size, operating range, shaft rating and maximum stages; The source does not publish a BEP point for this model in the extracted table; BEP Q is the midpoint of the published operating range and head/efficiency/BHP are explicit parametric estimates. All seven curve points and stage-type classification are synthesized using stated standard dimensionless centrifugal-pump shape assumptions; they are not vendor curve points. min_casing_id_in=7.921 is a conservative mapping from the vendor's nominal minimum casing size to a common API casing ID, not a vendor-published internal-diameter limit. |

## Motor records

| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |
|---|---|---|---|
| `mps-pmesp-375-50` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `slb-maximus-375-14` | `digitized_from_datasheet` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-456-50` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-456-100` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-456-200` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-456-400` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-562-100` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-562-250` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-562-400` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-738-250` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `mps-pmesp-738-500` | `digitized_from_datasheet` | https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Electrical values are a single published voltage/current option for this HP row. min_casing_id_in is a conservative casing-ID mapping, not a manufacturer motor fit statement. |
| `novomet-pmm-400-family-estimate` | `parametric_estimate` | https://www.novometgroup.com/assets/files/2019/Cases/Cases%20ENG/bro-esp-permanent-magnet-motor.pdf<br>https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Novomet public PMM literature identifies a 400-series family but publishes no numerical nameplate values. OD, HP, volts, amps, synchronous speed, length, temperature and casing mapping are conservative parametric estimates scaled from comparable published MPS PMESP records; not vendor data. |
| `baker-hughes-centrilift-450sp-estimate` | `parametric_estimate` | https://dam.bakerhughes.com/m/b0868a18e68df9b/original/CENtrilift-SP-superior-performance-series-motors.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Baker Hughes publishes 450SP: 4.50-in OD, 264 hp, 4,500 V, and 450F conductor-temperature capability. Amps, synchronous speed, length, winding-temperature interpretation and casing mapping are conservative estimates because the public datasheet omits them. |
| `novomet-pmm-540-family-estimate` | `parametric_estimate` | https://www.novometgroup.com/assets/files/2019/Cases/Cases%20ENG/bro-esp-permanent-magnet-motor.pdf<br>https://www.magneticpumpingsolutions.com/catalogues/MPS%20Motor%20Catalogue%20-%20Jan%202019.pdf | HP, selected voltage/current option, OD, length and insulation temperature were digitized. Novomet public PMM literature identifies a 540-series family but publishes no numerical nameplate values. OD, HP, volts, amps, synchronous speed, length, temperature and casing mapping are conservative parametric estimates scaled from comparable published MPS PMESP records; not vendor data. |

## Cable records

| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |
|---|---|---|---|
| `kerite-mtf1-flat-awg-1` | `digitized_from_datasheet` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTF1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |
| `kerite-mtr1-round-awg-1` | `digitized_from_datasheet` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTR1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |
| `kerite-mtf1-flat-awg-2` | `digitized_from_datasheet` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTF1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |
| `kerite-mtr1-round-awg-2` | `digitized_from_datasheet` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTR1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |
| `kerite-mtf1-flat-awg-4` | `digitized_from_datasheet` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTF1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |
| `kerite-mtr1-round-awg-4` | `digitized_from_datasheet` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTR1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |
| `kerite-mtf1-flat-awg-6` | `parametric_estimate` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTF1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |
| `kerite-mtr1-round-awg-6` | `parametric_estimate` | https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf | Dimensions and max temperature digitized from MTR1 table. Resistance converted from published stranded 20C/100ft resistance using manufacturer TCF. AWG 6 ampacity (95 A) and stranded cmil are conservative engineering estimates because brochure ampacity/resistance table omits AWG 6. |

## Gas-handling records

| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |
|---|---|---|---|
| `alkhorayef-spectrum-vgs-400` | `digitized_from_datasheet` | https://www.alkhorayefpetroleum.com/Alkhorayef/media/AlkhorayefMedia/PDF/SPECTRUM-Vortex-Gas-Separator.pdf | Published SPECTRUM vortex gas separator; source labels 2 @ 60 Hz under HP consumed (unit interpreted as hp). Tandem separation efficiency is published as 85%. |
| `alkhorayef-spectrum-vgs-513` | `digitized_from_datasheet` | https://www.alkhorayefpetroleum.com/Alkhorayef/media/AlkhorayefMedia/PDF/SPECTRUM-Vortex-Gas-Separator.pdf | Published SPECTRUM vortex gas separator; catalog labels this 513 series but gives 5.38-in OD. Source labels 7 @ 60 Hz under HP consumed (unit interpreted as hp). |
| `slb-reda-ars-338` | `mixed_source_fields` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | ARS rotary gas separator. Catalog publishes series and length but not HP consumed, free-gas fraction or separation efficiency. |
| `slb-reda-drs-es-400` | `mixed_source_fields` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | DRS-ES rotary gas separator; documented 2.6-ft length. Requested performance fields are not published. |
| `slb-reda-agh-d5-21` | `digitized_from_datasheet` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf<br>https://www.slb.com/products-and-services/innovating-in-oil-and-gas/completions/artificial-lift/electrical-submersible-pumps/reda-esp-pump-system/esp-gas-devices | D5-21 capacity 500-2,100 bpd from catalog; the public SLB product page states AGH systems can handle up to 45% GVF at low intake pressure. This is product-family capability, not a device-specific test point. |
| `slb-reda-mgh-d8-42` | `digitized_from_datasheet` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf<br>https://www.slb.com/products-and-services/innovating-in-oil-and-gas/completions/artificial-lift/electrical-submersible-pumps/reda-esp-pump-system/esp-gas-devices | D8-42 capacity 800-4,200 bpd from catalog; public MGH product-family capability up to 75% GVF. No HP consumed figure is published. |
| `levare-400-vapro-2000` | `digitized_from_datasheet` | https://levare.com/storage/app/uploads/public/64d/a31/438/64da31438ea37707606711.pdf | Published capacity 717-2,038 bpd and max 70% free gas at pump intake; OD, HP consumption, and efficiency unavailable. |
| `levare-538-vapro-12500` | `digitized_from_datasheet` | https://levare.com/storage/app/uploads/public/64d/a31/438/64da31438ea37707606711.pdf | Published capacity 4,000-13,000 bpd; source publishes 65% max free gas for 538 Vapro product family. OD, HP consumption, and efficiency unavailable. |

## Seal/protector records

| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |
|---|---|---|---|
| `slb-reda-protector-325-bsb` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Length and series/configuration are digitized from SLB catalog. The catalog does not publish OD, numerical thrust-bearing capacity, or max shaft HP; OD is a nominal-series proxy only. BSB nomenclature is as published. |
| `slb-reda-protector-400-lsl` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Length and series/configuration are digitized from SLB catalog. The catalog does not publish OD, numerical thrust-bearing capacity, or max shaft HP; OD is a nominal-series proxy only. LSL nomenclature is as published. |
| `slb-reda-protector-540-bsb` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Length and series/configuration are digitized from SLB catalog. The catalog does not publish OD, numerical thrust-bearing capacity, or max shaft HP; OD is a nominal-series proxy only. BSB nomenclature is as published. |
| `slb-reda-protector-738-66l-hl` | `parametric_estimate` | https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf | Length and series/configuration are digitized from SLB catalog. The catalog does not publish OD, numerical thrust-bearing capacity, or max shaft HP; OD is a nominal-series proxy only. 66L-HL nomenclature is as published. |

## Casing records

| Record | Data quality | Exact source URL(s) | What was digitized vs. estimated |
|---|---|---|---|
| `4.5 in / 11.6 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `4.5 in / 13.5 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `5.5 in / 17.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `5.5 in / 20.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `7.0 in / 23.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `7.0 in / 26.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `7.625 in / 24.0 lb/ft` | `published_table` | https://www.zc-pipe.com/API-5CT-Casing-Sizes-Dimensions-Weight-Tables-id46268065.html | ID and drift ID transcribed from the cited public casing table. |
| `7.625 in / 26.4 lb/ft` | `published_table` | https://www.zc-pipe.com/API-5CT-Casing-Sizes-Dimensions-Weight-Tables-id46268065.html | ID and drift ID transcribed from the cited public casing table. |
| `8.625 in / 32.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `8.625 in / 36.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `9.625 in / 36.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |
| `9.625 in / 47.0 lb/ft` | `published_table` | https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf | ID and drift ID transcribed from the cited public casing table. |

## Source notes

* **SLB REDA Electric Submersible Pump Systems Technology Catalog:** https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf — pump series/ranges, the limited published BEP values, shaft limits, motor and protector tables, and gas-device capacities.
* **Magnetic Pumping Solutions PM Motor Catalogue (2019):** https://www.magneticpumpingsolutions.com/catalogues/MPS%20PM%20Motor%20Catalogue%20-%20Jan%202019.pdf — PMESP motor nameplate option rows and insulation ratings.
* **Kerite/Marmon ESP Cable Brochure:** https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf — cable dimensions, temperatures, conductor data, and manufacturer temperature-correction formula.
* **Alkhorayef SPECTRUM Vortex Gas Separator:** https://www.alkhorayefpetroleum.com/Alkhorayef/media/AlkhorayefMedia/PDF/SPECTRUM-Vortex-Gas-Separator.pdf — separator flow/GVF/efficiency claims.
* **SLB ESP Gas Devices:** https://www.slb.com/products-and-services/innovating-in-oil-and-gas/completions/artificial-lift/electrical-submersible-pumps/reda-esp-pump-system/esp-gas-devices — product-family AGH/MGH GVF capability.
* **Levare Gas Handling Devices:** https://levare.com/storage/app/uploads/public/64d/a31/438/64da31438ea37707606711.pdf — Vapro and vortex-device capability statements.
* **Versa-Line API casing handbook:** https://versa-line.com/wp-content/uploads/2020/05/Versa-Line-Data-Hanbook.pdf and **ZC Steel 7-5/8 casing table:** https://www.zc-pipe.com/API-5CT-Casing-Sizes-Dimensions-Weight-Tables-id46268065.html — casing ID/drift rows.

## CORRECTIONS APPLIED AFTER DIGITIZATION

These changes were made after the initial digitization pass, during engine
integration, when computed results were physically implausible. Each is recorded
because a silent catalog edit is indistinguishable from a fabricated value.

### 1. Cable conductor resistance was 10x too high (all 8 cable records)

The [Kerite/Marmon ESP cable brochure](https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf)
table header reads "Ohms/100 ft", but the values it lists (#1 stranded 0.126,
#2 0.159, #4 0.254) are the standard **per-1000-ft** figures for stranded copper.
The digitizer trusted the header and scaled by 10, producing resistances that
made every ESP cable fail voltage-drop screening.

Corrected to ASTM/NEC stranded copper at 77 degF:

| AWG | Was (ohm/1000 ft) | Now (ohm/1000 ft) |
| --- | --- | --- |
| 1 | 1.264 | 0.1264 |
| 2 | 1.593 | 0.1593 |
| 4 | 2.535 | 0.2535 |
| 6 | 4.028 | 0.4028 |

Cross-checked against the [Philatron DC copper resistance chart](https://philatron.com/cable/copper/dc-copper-resistance.php)
and [Engineering ToolBox copper wire tables](https://www.engineeringtoolbox.com/copper-wire-d_1429.html).
The correction is also recorded in each affected record's `notes` field.

**Catalog version hash changed as a result of this correction.** Any stored design
referencing the prior hash was computed on the erroneous values and must be re-run.

### 2. Voltage-drop acceptance criterion was the wrong criterion (engine, not data)

The engine originally screened cables against a 5% fractional voltage drop. That
is a power-distribution convention and is not ESP practice. ESP practice screens
on **volts per 1000 ft**, with a recommended limit near 30 V/1000 ft, and computes
surface voltage as motor voltage plus cable drop. Verified against
[Production Technology's ESP cable design step](https://production-technology.org/esp-design-step-6-electric-cables/)
and the [ESP Expert cable presentation](https://espexpert.com/presentations/espexpert/09%20Cable.pdf),
neither of which uses a percentage criterion.

The 30 V/1000 ft figure is applied as a **preference, not a hard gate**: cables
meeting it are preferred, and if none does, the largest available conductor is
returned with an explicit warning. Treating a vendor guideline as a hard filter
reported "no cable exists" for wells that are routinely cabled in the field.

## KNOWN COVERAGE GAPS

These are gaps in what public sources made available, not defects. They are listed
because they change what the engine can honestly conclude.

### Motor series barely overlap pump series

Pump series present: 338, 400, 513, 538, 562, 675.
Motor series present: 375, 400, 450, 456, 540, 562, 738.
Only **400 and 562** overlap.

No public datasheet in this catalog states which cross-series pump/motor
couplings or adapters exist, so the engine does not invent a compatibility
mapping. When no same-series motor fits, it widens the search to any motor that
physically fits the casing and emits a **soft `availability` violation** stating
that pump-to-motor series compatibility is NOT verified. This must be resolved
with vendor data before any field use.

### Series 400 has exactly one motor (60 hp)

A moderate-load series-400 design therefore reports motor loading near 40%,
outside the 75-85% target band. This is a catalog coverage limit, not a sizing
error, and the engine flags the loading rather than hiding it. Real series-400
lines offer a range of horsepowers.

### All 13 pump curves are parametric estimates

No official vendor multi-point curve file was publicly obtainable for any pump in
this catalog. Every curve is marked `parametric_estimate`, and the engine raises a
run-level warning whenever a ranked configuration depends on one. Head, efficiency,
and thrust-zone boundaries are approximate.

## LIMITATIONS AND NEXT STEPS

**NOT FOR FIELD DESIGN.** This seed catalog must not be used for actual ESP selection, equipment procurement, well design, operating envelopes, warranty decisions, or field deployment without current vendor validation.

1. Obtain vendor data agreements and release-controlled technical catalogs for each manufacturer/series, including current datasheets, model revisions, material trims, and temperature/pressure derates.
2. Import official digital pump-curve files (for example CSV, XML, or vendor design-system exports) with full multi-point head, efficiency, BHP, thrust, axial-load, stage-count, viscosity, gas, and frequency corrections. Replace every parameterized curve with these files and preserve document revision metadata.
3. Validate all component interfaces by exact part number: motor/protector/pump shaft compatibility, MLE/cable voltage-drop and ampacity at actual temperature/depth, motor operating voltage/current, and casing/tubing drift through couplings and completion restrictions.
4. Add well-fluid PVT, solids, corrosive-service, scale, temperature, vibration, gas-separation, motor-cooling, and electrical-system limits. The public sources do not provide a complete engineering envelope.
5. Build automated source-revision monitoring, human technical approval, unit tests against vendor examples, and a clearly versioned change-control process before promoting any catalog content to production.
