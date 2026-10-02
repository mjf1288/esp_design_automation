# ESP physics reference — deterministic sizing engine

**Purpose.** This is an implementation specification for a black-oil Electrical Submersible Pump (ESP) sizing engine in US oilfield units. It separates (a) deterministic governing equations from (b) empirical vendor-/well-specific models. Do not silently substitute assumptions for missing PVT, pump curve, motor, cable, casing, or completion data. The normal design sequence used by SPE references is: well/inflow → intake-condition fluid volumes and gas → TDH → pump → motor/seal → cable/surface equipment. [SPE PetroWiki](https://petrowiki.spe.org/PEH:Electrical_Submersible_Pumps)

> **Safety and scope boundary.** This reference supports engineering calculations, not a release authority. A released design requires the selected manufacturer’s curve, motor derating data, cable data sheet, ESP assembly OD/length, completion tally, survey, material compatibility review, and the operator’s design standard. Free-gas, natural-separation, and multiphase head models are empirical and should never be represented as universal physics.

## 0. Conventions, units, and engine-wide rules

### 0.1 Required units

| Quantity | Engine unit | Notes |
|---|---:|---|
| Oil/water stock-tank rate | STB/d or bpd | `qo`, `qw`; 1 STB = 42 US gal. |
| Liquid rate | bpd | `ql = qo + qw` at stock-tank basis unless labelled in-situ. |
| Gas rate | scf/d or Mscf/d | Standard conditions must be stored with the data; use 14.7 psia and 60°F only when that is the declared standard. |
| Pressure | psia internally | Convert `psig + 14.696 = psia`; never use psig in PVT equations. |
| Temperature | °F input, °R in gas equations | `T_R = T_F + 459.67`. |
| Length/depth | ft TVD | Store MD separately; do not use MD for hydrostatic head. |
| Viscosity | cP | `ν(cSt)=μ(cP)/ρ(g/cm³)`. |
| Density | lbm/ft³ | Specific gravity `SG = ρ/62.4`. |
| Power | hp and kW | `1 hp = 0.745699872 kW`; `1 kW = 1.34102209 hp`. |
| Electric | V line-to-line, A line current, kVA | Three-phase unless explicitly otherwise. |

### 0.2 Constants

| Constant | Value |
|---|---:|
| Water pressure gradient | `0.433 psi/ft` (use 0.4335 if desired; configure once) |
| Pressure-head conversion | `1 psi = 2.31 ft of water`; `H(ft liquid)=P(psi)/(0.433×SG)` |
| Water density | `62.4 lbm/ft³` (reference) |
| 1 bbl | `5.614583 ft³` |
| 1 bpd | `0.003898` ft³/min |
| Hydraulic power constant | `HHP = Q_bpd × H_ft × SG / 136,000` hp; the SPE compendium uses 136,000 for bpd-ft-hp conversion. [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf) |
| Ideal-gas constant | `R = 10.732 psia·ft³/(lbmol·°R)` [DAK reference](https://app.ingemmet.gob.pe/biblioteca/pdf/CLG12-73.pdf) |
| Air molecular weight | `28.97 lbm/lbmol` |
| Standard gas density | `0.0764 γg lbm/ft³` at 14.7 psia, 60°F (derived from ideal gas) |
| Gravity | `g = 32.174 ft/s²` |
| Copper temperature coefficient | ESP cable vendor form: `0.00214/°F` referenced to 77°F. [Kerite ESP cable brochure](https://marmoniei.com/wp-content/uploads/2025/02/Kerite-Pump-Cable-Brochure-12.23.pdf) |

### 0.3 Basis discipline

1. Keep each volume tagged `stock_tank`, `standard_gas`, or `in_situ(P,T)`. Never add rates with different bases.
2. `GOR` in this document means producing gas-oil ratio on a standard-gas / stock-tank-oil basis (`scf/STB`). `Rs` is solution GOR on the same basis.
3. Set `Rs = min(Rs_correlation(P,T), Rsb)`; gas cannot redissolve above the measured bubble-point solution GOR in a simple black-oil model.
4. Oil PVT correlations need **absolute** pressure. Do not extrapolate them without warning outside their source-data range.
5. All curves are per the exact pump model, stage type, test speed, test fluid, and test conditions. A generic series curve is not a substitute.

---

# 1. INFLOW PERFORMANCE

## 1.1 Productivity Index (PI): undersaturated/single-phase straight-line IPR

When flowing bottomhole pressure is above bubble point and the reservoir flow is single phase, use the straight-line PI relation:

\[
q_o = J\,(p_R-p_{wf})
\]

\[
J = \frac{q_{o,test}}{p_R-p_{wf,test}},\qquad p_{wf}=p_R-\frac{q_o}{J}
\]

* `qo` = oil rate, STB/d; `J` = productivity index, STB/d/psi; `pR` = current average reservoir pressure, psia; `pwf` = flowing bottomhole pressure at the **perforation datum**, psia.
* Validity: use only while the relevant sandface/wellbore flow remains substantially single phase (`pwf ≥ pb`) and pseudo-steady/steady-state assumptions match the test. The straight-line relation is the standard PI interpretation for single-phase inflow; SPE describes Vogel as required after solution-gas effects create curvature. [SPE PetroWiki IPR](https://petrowiki.spe.org/Oil_well_performance)
* Engine rule: if the computed `pwf > pR`, fail input validation; if `pwf < pb`, switch to composite/Vogel rather than continue PI.

### PI decline/drift

`J` is not a physical constant. It changes as pressure, mobility, skin, water cut, relative permeability, fines/scale, completion condition, and drawdown change. Model it as a time-indexed calibration parameter rather than forecasting one universal decline law. Recommended engine inputs:

* `J_tested(t)` with test date, test duration, gauge datum, rate allocation, `pR`, and confidence.
* optional `J_multiplier(t)` or `skin(t)` scenario, not an implicit fixed decline.
* alert if a new stable test differs from the active calibrated PI by more than configurable 10–20%; that is an engineering review trigger, **not** a universal physical threshold.

For planning, many operators fit exponential/hyperbolic rate decline, but decline-curve behavior is not a PI correlation and must not be used to overwrite a measured IPR. Recalibrate at each pressure-survey/PBU/production test.

## 1.2 Vogel IPR for saturated solution-gas-drive oil

For an oil well producing below bubble point under the assumptions of Vogel’s simulation-derived correlation:

\[
\frac{q_o}{q_{o,max}}=1-0.2x-0.8x^2,\qquad x=\frac{p_{wf}}{p_R}
\]

\[
q_{o,max}=\frac{q_{o,test}}{1-0.2x_t-0.8x_t^2}
\]

\[
p_{wf}=p_R\;\frac{-0.2+\sqrt{0.04+3.2(1-q_o/q_{o,max})}}{1.6}
\]

* `qomax` = AOF-like theoretical rate at `pwf=0`, STB/d; `xt=pwf,test/pR`.
* The positive root is required; clamp `0 ≤ qo/qomax ≤ 1` and `0 ≤ pwf ≤ pR`.
* Vogel was developed for solution-gas-drive oil flow under pseudo-steady conditions; SPE’s original-paper record gives the variable definitions and physical conditions. [Vogel, *JPT*](https://onepetro.org/JPT/article/23/09/1141/163905/Concerning-the-Calculation-of-Inflow-Performance) The explicit dimensionless equation is also reproduced by [Texas A&M](https://blasingame.engr.tamu.edu/0_TAB_Public/TAB_Publications/SPE_110821_(Ilk)_IPR_for_Sol_Gas_Drive_Res_Analytical_Considerations_(wPres).pdf).

## 1.3 Composite IPR (above and below bubble point)

Use a PI line from `pR` down to `pb`, and Vogel curvature below `pb`:

\[
q_b=J(p_R-p_b)
\]

\[
q_{o,max}=\frac{q_b}{1-0.2(p_b/p_R)-0.8(p_b/p_R)^2}
\]

\[
q_o(p_{wf})=
\begin{cases}
J(p_R-p_{wf}),&p_{wf}\ge p_b\\
q_{o,max}\left[1-0.2(p_{wf}/p_R)-0.8(p_{wf}/p_R)^2\right],&0\le p_{wf}<p_b
\end{cases}
\]

This construction is continuous at `pb`. It is the usual engineering composite form; make its use selectable because some clients instead calibrate a Fetkovich, Jones, Darcy radial-flow, or numerical reservoir IPR.

## 1.4 `Pwf` and PIP are different pressure nodes

* `pwf` is sandface/perforation pressure used by IPR.
* `PIP` (also `pin`) is pressure at the pump intake ports.
* If the pump is **above** the perforations, calculate `PIP` from a multiphase pressure traverse from perforation datum to intake datum. A liquid-only approximation is:

\[
PIP \approx p_{wf}-0.433\,SG_{ann}\,(TVD_{pump}-TVD_{perf})-\Delta p_{fr,ann}
\]

The sign reverses if the intake is below the pressure datum. `SGann` must be the annular fluid mixture appropriate to that interval—not stock-tank oil SG. For gassy wells, use a mechanistic multiphase correlation or a calibrated pressure survey; a homogeneous static gradient is only a screening approximation.

---

# 2. PVT / FLUID PROPERTY CORRELATIONS

## 2.1 Oil and gas input properties

\[
\gamma_o=\frac{141.5}{API+131.5}
\]

`API` = stock-tank API gravity; `γo` = stock-tank oil SG relative to water. `γg` = gas SG relative to air. Use measured separator gas gravity and separator pressure/temperature where the selected correlation calls for them.

**Hierarchy:** use a laboratory black-oil PVT table/interpolator if supplied; otherwise use one selectable correlation family consistently for `pb`, `Rs`, `Bo`, and viscosity. The documented Standing source data are California oils (105 data from 22 mixtures); its published table range is `T=60–260°F` for `pb`, `pb=200–6000 psia`, `Rs=20–1425 scf/STB`, `γg=0.5–1.5`, and `API=16.5–63.8`. [S&P Global correlation documentation](https://www.ihsenergy.ca/support/documentation_ca/WellTest/content/html_files/reference_materials/calculations_correlations/oil_correlations.htm)

## 2.2 Standing: bubble point, solution gas, and saturated oil FVF

Use field units `pb` psia, `T` °F, `Rs` scf/STB, `γg` air=1, `API` degrees API, and `Bo` rb/STB:

\[
p_b=18.2\left[\left(\frac{R_{sb}}{\gamma_g}\right)^{0.83}10^{(0.00091T-0.0125API)}-1.4\right]
\]

\[
R_s(p)=\gamma_g\left[\left(\frac{p}{18.2}+1.4\right)10^{(0.0125API-0.00091T)}\right]^{1.2048},\quad p\le p_b
\]

\[
R_s(p)=R_{sb},\quad p>p_b
\]

\[
B_{ob}=0.9759+0.00012\left[R_s\left(\frac{\gamma_g}{\gamma_o}\right)^{0.5}+1.25T\right]^{1.2}
\]

These equations and variables are reproduced in the ESP calculation teaching reference. [KSU ESP systems notes](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf) The source-data range and origin are documented by [S&P Global](https://www.ihsenergy.ca/support/documentation_ca/WellTest/content/html_files/reference_materials/calculations_correlations/oil_correlations.htm).

**Above bubble point:** `Bo(p)` must be supplied by lab PVT or an explicit selected undersaturated-oil compressibility model; do not hold `Bo` constant without declaring it. A common deterministic update is `Bo(p)=Bob exp[-co(p-pb)]`, where `co` is 1/psi and must come from a selected/cited correlation or PVT table.

## 2.3 Vazquez–Beggs (VB): `Rs`, `pb`, and `Bo`

VB uses corrected separator gas gravity:

\[
\gamma_{gs}=\gamma_g\left[1+5.912\times10^{-5}\,API\,T_{sep}\log_{10}\left(\frac{p_{sep}}{114.7}\right)\right]
\]

Use `Tsep` °F and `psep` psia. If separator conditions are unknown, set `γgs=γg` **only with a warning**.

For the API group, select constants:

| API group | `C1` | `C2` | `C3` for `Rs/pb` | `A1` | `A2` | `A3` for `Bo` |
|---|---:|---:|---:|---:|---:|---:|
| `API ≤ 30` | 0.0362 | 1.0937 | 25.7240 | `4.677e-4` | `1.751e-5` | `-1.811e-8` |
| `API > 30` | 0.0178 | 1.1870 | 23.9310 | `4.670e-4` | `1.100e-5` | `1.337e-9` |

The coefficient sets are documented by [S&P Global](https://www.ihsenergy.ca/support/documentation_ca/WellTest/content/html_files/reference_materials/calculations_correlations/oil_correlations.htm). **Data-governance note:** some rendered secondary tables print the final high-API `Bo` coefficient as `1.377e-9`; the commonly implemented original-form value in the table above is `1.337e-9`. Store the coefficient set as a named/versioned dataset and validate it against the operator’s approved VB implementation before production release. Implement:

\[
R_s(p)=C_1\gamma_{gs}p^{C_2}\exp\left(\frac{C_3 API}{T+459.67}\right),\quad p\le p_b
\]

\[
p_b=\left[\frac{R_{sb}}{C_1\gamma_{gs}\exp(C_3API/(T+459.67))}\right]^{1/C_2}
\]

\[
B_{ob}=1+A_1R_s+A_2(T-60)\frac{API}{\gamma_{gs}}+A_3R_s(T-60)\frac{API}{\gamma_{gs}}
\]

Set `Rs=min(Rs(p),Rsb)` and use `Rsb` in the bubble-point equation. VB was built from more than 600 worldwide PVT analyses and split at 30 API; documented ranges include `p=140.7–9514.7 psia`, `γg=0.511–1.351`, and `API=15.3–59.5`. [S&P Global](https://www.ihsenergy.ca/support/documentation_ca/WellTest/content/html_files/reference_materials/calculations_correlations/oil_correlations.htm) The original VB paper should be retained in the product’s source library for formal validation.

## 2.4 Beggs–Robinson oil viscosity

Use `T` in °F and viscosity in cP. Dead-oil viscosity:

\[
z=3.0324-0.02023API,
\quad y=10^z,
\quad x=yT^{-1.163},
\quad \mu_{od}=10^x-1
\]

At/below bubble point (live-oil correlation):

\[
a=10.715(R_s+100)^{-0.515},\qquad b=5.44(R_s+150)^{-0.338}
\]
\[
\mu_{ob}=a\,\mu_{od}^{b}
\]

Use the selected `Rs(P,T)`. The relationship and coefficients are published in the correlation documentation/searchable technical literature. [S&P Global Beggs–Robinson documentation](https://www.ihsenergy.ca/support/documentation_ca/Harmony_Enterprise/2019_3/content/html_files/ref_materials/calculations/oil_correlations.htm)

For undersaturated oil, do **not** apply the saturated `μob` unchanged by default. Either interpolate lab viscosity vs pressure, or enable the classic Beggs–Robinson pressure correction:

\[
\mu_o(p)=\mu_{ob}(p/p_b)^m,
\quad m=2.6p^{1.187}\exp(-11.513-8.98\times10^{-5}p)
\]

with `p` and `pb` in psia. Treat this above-`pb` extension as a separate selectable model and regression-test it against a trusted PVT package; it becomes numerically extreme if pressure units are wrong.

## 2.5 Gas `z`, gas FVF, and density

### Pseudocritical properties (Sutton)

If detailed gas composition is unavailable, the Sutton field-gas approximation is:

\[
p_{pc}=756.8-131.0\gamma_g-3.6\gamma_g^2\quad[psia]
\]
\[
T_{pc}=169.2+349.5\gamma_g-74.0\gamma_g^2\quad[^\circ R]
\]
\[
p_{pr}=p/p_{pc},\qquad T_{pr}=T_R/T_{pc}
\]

The real-gas law, reduced variables, Sutton equations, and `R=10.732` field-unit constant are documented in this [DAK technical reference](https://app.ingemmet.gob.pe/biblioteca/pdf/CLG12-73.pdf). Correct for CO₂/H₂S/N₂ or use a compositional EOS when acid gas/nonhydrocarbon content is known; Sutton is a sweet-gas screening correlation.

### Dranchuk–Abou-Kassem (DAK) `z`

Solve for `z>0` using reduced density:

\[
\rho_r=\frac{0.27p_{pr}}{zT_{pr}}
\]
\[
z=1+c_1\rho_r+c_2\rho_r^2-c_3\rho_r^5+c_4
\]
\[
c_1=A_1+A_2/T_{pr}+A_3/T_{pr}^2+A_4/T_{pr}^3+A_5/T_{pr}^4
\]
\[
c_2=A_6+A_7/T_{pr}+A_8/T_{pr}^2,
\quad c_3=\frac{A_9}{T_{pr}^3}\left(A_7/T_{pr}+A_8/T_{pr}^2\right)
\]
\[
c_4=\frac{A_{10}}{T_{pr}^3}\rho_r^2(1+A_{11}\rho_r^2)\exp(-A_{11}\rho_r^2)
\]

| `A1` | `A2` | `A3` | `A4` | `A5` | `A6` | `A7` | `A8` | `A9` | `A10` | `A11` |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| .32650 | -1.07000 | -.53390 | .01569 | -.05165 | .54750 | -.73610 | .18440 | .10560 | .61340 | .72100 |

Use a bracketed root (`z ∈ [0.2, 2.0]`, expand safely) or safeguarded Newton iteration on `F(z)=z-RHS(z)`. The DAK coefficients, residual form and Newton/Secant approach are given in the [technical reference](https://app.ingemmet.gob.pe/biblioteca/pdf/CLG12-73.pdf). **Important:** that source’s OCR’d stated reduced-temperature range is internally implausible; retain DAK as a configurable `z` model and establish tested product validity from the original 1975 paper/benchmark grid before enforcing hard range gates.

### Gas FVF and gas density

\[
B_g=\frac{0.00504zT_R}{p}\quad [rb/scf]
\]

\[
\rho_g=\frac{28.97\gamma_g p}{z(10.732)T_R} =\frac{2.699\gamma_g p}{zT_R}\quad[lbm/ft^3]
\]

`Bg` field-unit form is documented in the [ESP calculation reference](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf); density follows directly from the cited real-gas law. A common trap is the alternate unit `Bg = 0.02827zT_R/p` in `ft³/scf`; divide by `5.614583` to obtain `0.005035` rb/scf.

### Hall–Yarborough / Standing–Katz

Standing–Katz is a chart and Hall–Yarborough is an implicit fit. Provide it as a selectable model only after validation against a trusted implementation. For a deterministic first release, DAK above is more straightforward because all coefficients are stated in one source. Do not digitize a chart at runtime. The Hall–Yarborough method represents Standing–Katz and uses a Newton solve for reduced density. [Hall–Yarborough implementation notes](https://f0nzie.github.io/zFactor/articles/Hall-Yarborough.html)

## 2.6 Water/brine FVF, density, and viscosity

### McCain water FVF

Use `p` psia and `T` °F:

\[
B_w=(1+\Delta V_{wT})(1+\Delta V_{wp})
\]
\[
\Delta V_{wT}=-1.0001\times10^{-2}+1.33391\times10^{-4}T+5.50654\times10^{-7}T^2
\]
\[
\Delta V_{wp}=-1.95301\times10^{-9}pT-1.72834\times10^{-13}p^2T-3.58922\times10^{-7}p-2.25341\times10^{-10}p^2
\]

This complete field-unit form is reproduced in [Petroleum Reservoir Engineering Practice](https://dokumen.pub/petroleum-reservoir-engineering-practice-9780132485210-0132485214-0137152833-9780137152834.html). Use as a configurable low/moderate-temperature screening correlation; it does not explicitly include salinity.

Published applicability quoted for this McCain `Bw` form is the full salinity range of the underlying data, temperatures to 260°F and pressures to 5,000 psia. [Pengtools water-FVF reference](https://wiki.pengtools.com/index.php?title=Water_formation_volume_factor) If the engine has a salinity input and the result will drive a high-temperature/high-pressure design, select a brine correlation that explicitly uses salinity rather than treating this correlation as universal.

### Water density

For salinity `S` in **weight percent NaCl equivalent**:

\[
\rho_{w,sc}=62.368+0.438603S+1.60074\times10^{-3}S^2\quad[lbm/ft^3]
\]
\[
\rho_w(P,T)\approx \rho_{w,sc}/B_w
\]

The standard-condition correlation and units are documented in [Petroleum Reservoir Engineering Practice](https://dokumen.pub/petroleum-reservoir-engineering-practice-9780132485210-0132485214-0137152833-9780137152834.html). If brine contains material dissolved gas, use a full brine PVT model, not this approximation.

### Water viscosity

McCain/Mathews–Russell form, with `S` in wt% and `T` °F:

\[
\mu_{w,1atm}=A T^{-B}
\]
\[
A=109.574-8.40564S+0.313314S^2+8.72213\times10^{-5}S^3
\]
\[
B=-1.12166+2.63951\times10^{-2}S-6.79461\times10^{-4}S^2-5.47119\times10^{-5}S^3+1.55586\times10^{-6}S^4
\]
\[
\mu_w=\mu_{w,1atm}\left(0.9994+4.0295\times10^{-5}p+3.1062\times10^{-9}p^2\right)
\]

The pressure correction and correlation provenance are documented by [Penn State](https://www.e-education.psu.edu/png301/node/838) and the complete coefficients/form appear in [AAPG’s reservoir-engineering reference](https://wiki.aapg.org/Reservoir_engineering). For high salinity/high temperature/high pressure brine, expose Spivey–McCain–North as a selectable advanced correlation; its published range is 68–572°F, 0.1–200 MPa, and 0–25 wt% NaCl. [OnePetro abstract](https://onepetro.org/JCPT/article/doi/10.2118/04-07-05/31920/Estimating-Density-Formation-Volume-Factor)

**Validity caveat:** the public sources used here preserve the equations but not a complete original-data range for Beggs–Robinson dead/live oil viscosity or the McCain/Mathews–Russell water-viscosity form. The product shall expose `validity_range = unknown_from_retrieved_source`, issue a warning outside the operator's approved range, and obtain/check the original papers or laboratory PVT before treating either as a release limit. This is preferable to inventing a range.

## 2.7 Oil, liquid, and mixture density

\[
\rho_{o,sc}=62.4\gamma_o
\]
\[
\rho_o(P,T)=\frac{62.4\gamma_o+0.0136R_s\gamma_g}{B_o}\quad[lbm/ft^3]
\]

`0.0136 = 0.0764/5.6146` converts standard-gas mass per STB to formation volume. This live-oil mass balance, the cited real-gas law, and phase-volume definitions are the basis for the density and volumetric mixture equations below. [DAK real-gas-law reference](https://app.ingemmet.gob.pe/biblioteca/pdf/CLG12-73.pdf) [ESP intake-volume workflow](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf) Liquid mixture density (no free gas):

\[
\rho_l=\frac{V_o\rho_o+V_w\rho_w}{V_o+V_w},\qquad SG_l=\rho_l/62.4
\]

No-slip three-phase mixture:

\[
\rho_m=\frac{V_o\rho_o+V_w\rho_w+V_g\rho_g}{V_o+V_w+V_g},\qquad SG_m=\rho_m/62.4
\]

Use this only for local mixture density / homogeneous screening. It is **not** a substitute for a slip-aware tubing or annulus pressure traverse.

## 2.8 Pump-intake volumes

Given `WC` (water cut fraction):

\[
q_o=q_L(1-WC),\quad q_w=q_LWC
\]
\[
Q_{g,std}=GOR\,q_o\quad[scf/d]
\]
\[
Q_{g,sol}=R_s(PIP,TIP)q_o\quad[scf/d]
\]
\[
Q_{g,free,std}=\max(0,Q_{g,std}-Q_{g,sol})
\]
\[
V_o=q_oB_o,\quad V_w=q_wB_w,\quad V_g=Q_{g,free,std}B_g
\]
\[
V_l=V_o+V_w,\quad V_t=V_l+V_g,\quad \lambda_g=V_g/V_t
\]

All volumes are rb/d at intake `PIP,TIP`. This is the industry ESP gas-calculation workflow. [SPE PetroWiki ESP design](https://petrowiki.spe.org/ESP_design) A direct formula summary is also given by [Production Technology](https://production-technology.org/esp-design-gas-calculations/).

---

# 3. GAS CALCULATIONS AT PUMP INTAKE

## 3.1 Definition and basic calculation

`λg` is the **no-slip free-gas volume fraction**, not total GOR and not the gas fraction after a separator unless explicitly labelled. From §2.8:

\[
\lambda_g=\frac{q_o(GOR-R_s)B_g}{q_oB_o+q_wB_w+q_o(GOR-R_s)B_g}
\]

where the numerator is zero if `GOR≤Rs`. Free gas is generated by pressure falling below bubble point; higher PIP reduces `Bg` and generally increases `Rs`, both reducing `λg`.

**Algorithm:** `PIP` affects PVT and gas volume, gas affects pump head and often `PIP`; solve the coupled system iteratively. Never compute free-gas percent from standard-condition gas volume divided by stock-tank liquid volume.

## 3.2 Turpin head correlation and Turpin stability parameter

A commonly reproduced Turpin form for a particular experimental pump family is:

\[
H_{2\phi}=H_{sp}\exp\left[-\frac{q_g}{q_l}\left(\frac{346430}{(0.145PIP)^2}\frac{q_g}{q_l}-\frac{410}{0.145PIP}\right)\right]
\]

with `H2φ,Hsp` ft; `qg,ql` bpd at pump conditions; `PIP` psi. The Turpin stability parameter is:

\[
\phi=\frac{2000}{PIP^3}\frac{q_g}{q_l};\qquad \phi<1\ \text{is the stated stable-flow criterion}
\]

The review gives these equations, definitions and `φ<1` restriction. [Review of gas–liquid ESP flow](https://pdfs.semanticscholar.org/7299/df660becc71d3bc54a4d8ae9db8f8bb601e4.pdf)

> **Implementation warning — source conflict.** A later paper reproduces the last coefficient in the exponential as `3410` rather than `410`. [Field-data paper](https://link.springer.com/article/10.1007/s13202-021-01392-y) Do **not** hardcode either as a universal “Turpin correlation.” Implement `turpin_1986_variant` with source version, pump applicability, and golden test cases; default it to *disabled unless customer-approved*. The equation was developed for specific I-42B/K-70 experimental pumps, and is not vendor-curve replacement physics.

## 3.3 Natural annular gas separation

Natural gas separation efficiency (NGSE) is the fraction of available annular free gas vented upward rather than ingested:

\[
E_{nat}=\frac{Q_{g,vent}}{Q_{g,available}},\qquad 0\le E_{nat}\le1
\]

A simplified Alhanati drift-flux expression is:

\[
E_{nat}=\frac{V_\infty(1-\alpha)^n}{V_\infty(1-\alpha)^n+J_{sl}}
\]

\[
V_\infty=1.414\left[\frac{\sigma(\rho_l-\rho_g)g}{\rho_l^2}\right]^{1/4}
\]

\[
J_i=\frac{Q_i}{\pi(D_c^2-D_s^2)/4}
\]

* `α` = annular void fraction, `n` = flow-regime exponent (use `n=0` for slug/churn only as stated), `Jsl` = superficial liquid velocity, `σ` = surface tension, `Dc` = casing ID, `Ds` = ESP OD, `Qi` = in-situ phase volume rate.
* The equation, variable definitions, and Alhanati test regime are documented in this [NGSE study](https://www.tandfonline.com/doi/abs/10.1080/19942060.2024.2395452). Its underlying air/water tests had `0.25<α<0.70`; do not extrapolate to every oil/emulsion system.
* Natural separation improves with larger annular area and decreases with higher liquid velocity; gas can be entrained in a narrow annulus. The model itself has reported material errors versus TUALP data; make `nat_sep_model = none | alhanati | marquez_prado | vendor` configurable. [Comparison paper](https://oilproduction.net/files/predicting_downhole_natural.pdf)

Apply:

\[
Q_{g,after\;nat}=Q_{g,free}(1-E_{nat})
\]

If a completion is not configured for credible vent flow (packer geometry, shroud, intake location, annular path), set `Enat=0` rather than assuming separation.

## 3.4 Rotary separator and gas-handler calculation

For a rotary separator with vendor test efficiency `Esep` at the actual liquid rate, viscosity, PIP, frequency, and GVF:

\[
Q_{g,pump}=Q_{g,after\;nat}(1-E_{sep})
\]
\[
\lambda_{g,pump}=\frac{Q_{g,pump}}{Q_{g,pump}+V_l}
\]

Separator efficiency is gas removed to casing divided by available gas; it does **not** prove the residual void fraction is acceptable. [Tandem separator paper](https://www.swpshortcourse.org/ppdl/2079)

* Use vendor-specific performance maps in production. A broad published rule of thumb is rotary separation ~75–90% under favorable conditions; older test results achieved close to 90%, while advanced systems may claim higher only in a defined operating envelope. [Production Technology](https://production-technology.org/esp-pump-intake/) [DOE OSTI record](https://www.osti.gov/biblio/7053002)
* Separator efficiency depends materially on rate, GVF, viscosity, speed, gas discharge pressure and geometry. Do not use a fixed 90% outside an explicit scenario; a field-data study assumed 75% for its calculations, illustrating that assumptions vary. [Field-data study](https://link.springer.com/article/10.1007/s13202-021-01392-y)
* A gas **handler** is not automatically a separator: it conditions/pressurizes gas-liquid flow and may be used with a separator. Model gas-handler incremental shaft power and allowable inlet GVF from the exact vendor map.

## 3.5 Configurable decision thresholds

There is no universal free-gas cutoff: radial/mixed-flow stage geometry, PIP, liquid rate relative to BEP, viscosity and bubbles control stability. The historical rule of thumb is ≤10% free gas at the pump intake for little degradation; many current references frame 10–20% as allowable ingested gas depending on design, and <35% as an overall management target. [Tandem separator paper](https://www.swpshortcourse.org/ppdl/2079) [NGSE study](https://www.tandfonline.com/doi/abs/10.1080/19942060.2024.2395452)

Use these **screening defaults only; expose all as per-pump/vendor configuration**:

| `λg,pump` after all separation | Default action | Required engine behavior |
|---:|---|---|
| 0–0.10 | Standard pump can be considered | Still require curve operating point in recommended range. |
| 0.10–0.20 | Gas-aware selection | Evaluate mixed-flow first stages / gas handler; warn. |
| 0.20–0.35 | Separator + gas-tolerant/tapered system | Require vendor two-phase/GH map or an approved derate model. |
| 0.35–0.60 | High-risk | Require validated separator/AGH/tapered design, stage-by-stage model and expert release. |
| >0.60 | Redesign case | Increase PIP / set deeper / alter completion / reduce drawdown; do not auto-release standard ESP. |

The generic 10–15% gas-interference warning and 1 ft/s cooling recommendation are stated in [KSU ESP material](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf), but manufacturer qualification remains controlling.

## 3.6 Head degradation

Preferred order:

1. manufacturer-supplied gas-performance map for exact stage / intake pressure / frequency;
2. qualified field-calibrated model for comparable equipment;
3. an explicitly selected academic correlation, only within its calibration domain;
4. no automatic design credit outside that domain.

One field-data correlation is:

\[
\frac{\Delta p_{stage,2\phi}}{\Delta p_{stage,water\;catalog}}=0.9717-1.5727\lambda_g
\]

It reported `R²=0.933` using three field wells, but is recommended only for similar reservoir conditions. [Field-data correlation](https://link.springer.com/article/10.1007/s13202-021-01392-y) Constrain factor to `[0,1]`; never extrapolate linear head to negative values. The same paper reports approximately 16% degradation at 30% free gas in one well, 50% at 45% in another, and 55% at 65% in another—evidence of non-universality.

At minimum, track `gas_head_factor`, `gas_efficiency_factor`, model provenance, validity domain, and reason for fallback. Pump stages may have mild loss followed by abrupt surge/gas-lock behavior; a smooth equation must not conceal a stability transition. [SPE PetroWiki harsh-environment guidance](https://petrowiki.spe.org/Use_of_ESPs_in_harsh_environments)

---

# 4. TOTAL DYNAMIC HEAD (TDH)

## 4.1 Definition

For conventional lumped ESP design at the selected liquid rate:

\[
TDH=H_{lift}+H_f+H_{wh}+H_{surface}+H_{margin}
\]

where all terms are ft of the **pumped reference liquid**. The standard ESP expression is `TDH = net lift + tubing friction + wellhead backpressure`; it is stated in [SPE design guidance](https://www.swpshortcourse.org/ppdl/1790) and the [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf).

### Net vertical lift / fluid over pump

Under the liquid-column approximation:

\[
H_{sub} = \frac{PIP}{0.433\,SG_{ann}}\quad[ft]
\]
\[
H_{lift}=TVD_{pump}-H_{sub}
\]

`Hsub` is fluid over pump (submergence); `TVDpump` is measured from the same datum as the dynamic fluid level/wellhead. **Correction:** some legacy notes print “fluid over pump = PIP × gradient”; dimensions require *division* by gradient. The correct pressure/head relationship is `head=P/gradient`; the same source gives `H=P/gradient` for pressure head. [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf)

If a measured dynamic fluid level (DFL) is supplied, prefer `Hlift=TVD_DFL` relative to wellhead datum and cross-check it against PIP. If PIP/DFL disagree materially, do not average them: flag datum, fluid-gradient, or gauge-quality inconsistency.

### Wellhead/backpressure head

\[
H_{wh}=\frac{P_{wh}}{0.433\,SG_{ref}}
\]

Use `Pwh` as required discharge pressure at the wellhead (psig converted to differential psi as appropriate). `SGref` should match the liquid basis of the pump curve. The field-unit conversion is shown in [KSU ESP material](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf).

## 4.2 Tubing friction

### Darcy–Weisbach (recommended physics base)

\[
H_f=f\frac{L}{D}\frac{v^2}{2g}
\]
\[
v=\frac{Q}{A},\qquad A=\pi D^2/4
\]
\[
Re=\frac{\rho vD}{\mu}
\]

Use `L,D` ft, `v` ft/s, `g=32.174 ft/s²`, `ρ` lbm/ft³ with consistent English-unit Reynolds conversion (or calculate in SI internally). `f` is Darcy friction factor. The Darcy head-loss equation is documented by [ACPPA](https://acppa.org/wp-content/uploads/2023/09/4-ACPPA-FlowFrictionCharacteristics.pdf).

* Laminar: `f=64/Re`.
* Turbulent explicit approximation (Swamee–Jain):
\[
f=\frac{0.25}{\left[\log_{10}(\epsilon/(3.7D)+5.74/Re^{0.9})\right]^2}
\]
* For a single liquid, pressure loss `Δpf(psi)=Hf×0.433×SG`.
* Add minor losses: `Hm=ΣK v²/(2g)`; keep `K` tables configurable.

**ESP practice:** Darcy–Weisbach is preferred for oil/brine/viscous liquid because it accommodates density, viscosity, ID and roughness. For multiphase tubing, use a configured mechanistic pressure-gradient model (Beggs–Brill, Hagedorn–Brown, Ansari, OLGA/vendor), not Darcy on a no-slip mixture. The SPE design paper explicitly requires stage-by-stage/flow modeling for gassy fluids. [SPE design of submersible systems](https://www.swpshortcourse.org/ppdl/1790)

### Hazen–Williams (water-only screening)

US customary form:

\[
H_f=4.52\,L\frac{Q_{gpm}^{1.85}}{C^{1.85}d_{in}^{4.8655}}\quad[ft\ of\ water]
\]

An equivalent form is `0.002083 L(100/C)^1.85(Qgpm^1.85/din^4.8655)`. [Pipe Flow Software reference](https://www.pipeflow.com/public/documents/Hazen_Williams_Formula.pdf)

`C` is empirical roughness; values vary with pipe/age (e.g., new steel ~120, smooth plastic ~140–150). This formula was fit for water and does not include viscosity/density. Therefore: **do not use Hazen–Williams as the primary tubing friction model for oil, emulsions, brine mixtures or multiphase ESP design.** Retain only for water-injection / water-dominant configurable cases.

## 4.3 TDH vs pressure-addition method

For high free gas, calculate pressure rather than a single lumped head:

\[
P_{dis,req}=P_{wh}+\Delta P_{tubing}(P,T,\dot m)+\Delta P_{surface}
\]
\[
\Delta P_{pump,req}=P_{dis,req}-PIP
\]

Then compare `ΔPpump,available` from a stage-by-stage pump model using local density/head and gas correction. Converting this `ΔP` to “feet” at one SG is only a reporting convention. This avoids the error of assuming density and volume are constant through a gas-compressing pump.

---

# 5. PUMP CURVE PHYSICS

## 5.1 Curves and operating point

For each pump/stage, ingest digitized/vendor tabulated water-test curves at reference frequency: `Q`, `H_stage`, `η`, `BHP_stage`, allowed min/max, BEP, shut-in head, and test speed/fluid. At a selected operating rate:

\[
N_{stage}=\left\lceil\frac{TDH}{H_{stage}(Q)}\right\rceil
\]

This is the accepted first-pass stage calculation. [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf) It is valid only after selecting the curve appropriate to actual rate/frequency and applying approved viscosity/gas corrections.

For a direct energy check:

\[
HHP=\frac{Q_{bpd}\,TDH\,SG}{136000}
\]
\[
BHP_{pump}=\frac{HHP}{\eta_{pump}}
\]

or, from a vendor curve:

\[
BHP_{pump}=N_{stage}\,BHP_{stage}(Q)\,SG_{liquid}
\]

The stage-BHP formula is standard ESP sizing practice. [KSU ESP material](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf)

## 5.2 Frequency affinity laws

For same impeller diameter and similar flow regime, speed `N` proportional to frequency `f`:

\[
Q_2=Q_1(f_2/f_1),\qquad H_2=H_1(f_2/f_1)^2,\qquad BHP_2=BHP_1(f_2/f_1)^3
\]

[SPE’s design reference](https://www.swpshortcourse.org/ppdl/1790) and the [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf) state these relationships. Shift every tabulated curve point, then interpolate; do not shift only BEP.

* Affinity laws are approximations: they do not capture motor slip change, viscosity, gas interference, cavitation, stage wear or VSD harmonic effects.
* Frequency needs a lower/upper motor, pump, cable, VSD and thrust limit from the vendor catalog.

## 5.3 BEP, operating range, and thrust

* `BEP` is max-efficiency rate on the specific tested curve.
* Use **vendor-defined** recommended/allowable ranges as authoritative. A common general guideline is preferred 70–120% BEP and allowable 50–125% BEP, but this must be a configurable fallback—not a catalog substitute. [Pump operating-range training reference](https://kh.aquaenergyexpo.com/wp-content/uploads/2022/11/Submersible-Pump-Maintenance-and-Repair-.pdf)
* At low rate / shutoff, stages tend toward **downthrust**; increasing rate crosses a hydraulic balance point and then enters **upthrust**. The qualitative thrust behavior and need to use manufacturer bounds are described in [Takacs manual excerpt](https://studylib.net/doc/26028610/electrical-submersible-pumps-manual).
* The exact downthrust/upthrust boundaries, permissible duration, shaft load and protector thrust capacity are **pump-series-specific inputs**. Do not infer them as a fixed percentage of BEP.
* For gassy operation, stable operation is commonly better from BEP toward the high-rate end than at low rate; stage geometry matters. [SPE PetroWiki](https://petrowiki.spe.org/Use_of_ESPs_in_harsh_environments)

## 5.4 Viscosity correction (ANSI/HI 9.6.7)

The standard transforms a water curve using correction factors:

\[
Q_{vis}=C_QQ_w,\qquad H_{vis}=C_HH_w,\qquad \eta_{vis}=C_\eta\eta_w
\]
\[
BHP_{vis}=\frac{Q_{vis}H_{vis}SG}{136000\eta_{vis}}
\]

ANSI/HI 9.6.7 applies to Newtonian liquids of about 1–4000 cSt; it requires water-curve BEP flow/head, speed, and liquid viscosity. [AFT documentation of ANSI/HI 9.6.7](https://docs.aft.com/fathom13/ViscosityCorrection.html)

**Implementation requirement:** license/obtain the current HI standard and encode its complete `B`, `CQ`, `CH`, `Cη` equations/charts only under a documented license. The public sources confirm factor definitions and scope but are not enough to reproduce the copyrighted current standard verbatim. Store correction factors/provenance, apply point-by-point, and reject non-Newtonian emulsions/heavy-oil behavior unless a selected vendor curve/model is available.

## 5.5 Shaft power, shaft strength, and thrust bearing

At every candidate operating point calculate:

\[
P_{shaft,total}=BHP_{pump}+BHP_{separator}+BHP_{handler}+P_{protector,loss}
\]

Check the selected shaft’s allowable hp at actual frequency, total stages, torque, expected down-/upthrust and protector thrust-bearing rating. The required limits are vendor component data; there is no defensible generic shaft-strength equation from OD alone. Check maximum housing pressure at shut-in:

\[
MHP=H_{shut,in,stage,60}\,N_{stage}\,(0.433SG_m)\,(f/60)^2\quad[psi]
\]

[SPE teaching material](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf) gives this catalog-screening form. Actual stage/taper pressure distribution governs gassy designs.

---

# 6. MOTOR SIZING

## 6.1 Required motor horsepower and loading

\[
HP_{req}=BHP_{pump}+HP_{separator}+HP_{handler}+HP_{protector/loss}
\]
\[
Loading=HP_{req}/HP_{motor,nameplate}
\]

Select a motor whose hp, voltage, current, temperature, OD, shaft, protector and frequency curves all pass. ESP practice requires separator power to be added to pump/seal power. [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf)

**Configurable loading target:** many application practices target roughly 75–85% nominal motor loading to retain life/operating margin; use it only as `target_load_low/high`, not a universal acceptance limit. The hard constraints should be manufacturer min/max loading, service factor (if expressly allowed), maximum continuous temperature, and actual current curve. A design that is 75% loaded can still fail due to inadequate cooling or voltage.

**Nameplate amps and service factor:** obtain `I_nameplate`, `PF(load)`, `ηm(load)`, and any allowable service-factor loading from the exact motor catalog. Gate continuous current against `I_max_continuous` supplied by that catalog, not a guessed `I_nameplate × 1.15`. A “service factor” is not a license to exceed cable ampacity, protector/shaft limits, temperature rating, or VSD current rating. This is particularly important because ESP operating current, power factor and efficiency vary materially with percent nameplate load. [SPE design paper](https://www.swpshortcourse.org/ppdl/1790)

### Electrical input consistency

\[
P_{in,kW}=\frac{\sqrt3\,V_{LL}I PF}{1000}
\]
\[
HP_{shaft}\approx\frac{P_{in,kW}\eta_m}{0.7457}
\]

Use motor performance-table `PF`, `ηm`, current and slip at the calculated load. Do not estimate nameplate amps as `HP×746/V` without PF/efficiency.

## 6.2 Slip and frequency / constant V/Hz

For a two-pole motor, synchronous speed `Ns=120f/2=60f rpm`; actual speed is lower by slip:

\[
N=(1-s)60f
\]

Typical 60-Hz ESP actual speed is approximately 3500 rpm vs 3600 rpm synchronous, i.e., roughly 3% slip. [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf)

In the constant-flux region, command approximately constant `V/f`:

\[
V_{motor,target}(f)=V_{nameplate}\,(f/f_{base})
\]

then add cable drop to determine surface/VSD output. Above base frequency, voltage is limited and motor field-weakening/torque capability must use vendor VSD/motor curves. Do not apply `HP∝f³` to motor capability; that is pump absorbed-power scaling, while motor derating is a manufacturer constraint.

## 6.3 Motor cooling

\[
v_{motor}=\frac{Q_{cool}}{A_{annulus,motor}}
\]
\[
A_{annulus,motor}=\frac{\pi}{4}(ID_{casing}^2-OD_{motor}^2)
\]

with `Qcool` in ft³/s. A frequently stated minimum design target is `v_motor ≥ 1 ft/s`; below it, a shroud/jacket may be necessary. [KSU ESP material](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf)

* Use **actual flow past the motor**, not production rate blindly. In a top-intake configuration much flow may bypass the motor; a shroud changes the flow path.
* Calculate temperature with manufacturer thermal model if available. A simple velocity gate is not a temperature-rise calculation.
* Keep motor temperature against the chosen motor’s continuous rating; SPE PetroWiki reports around 400°F as a general maximum recommended ESP motor operating temperature, while actual product ratings vary. [SPE PetroWiki](https://petrowiki.spe.org/PEH:Electrical_Submersible_Pumps)

---

# 7. CABLE AND SURFACE EQUIPMENT

## 7.1 Three-phase cable voltage drop

Use the full AC form when cable reactance/power factor data are available:

\[
\Delta V_{LL}=\sqrt3\,I\,L\,(R_T\cos\theta+X\sin\theta)
\]

For ESP cable where reactance is negligible per the cable vendor, use:

\[
\Delta V_{LL}\approx1.732I R_T
\]

where `RT` is the resistance of one phase over the **one-way** deployed cable length. The ESP cable vendor’s formula and approximation are documented in the [Kerite brochure](https://marmoniei.com/wp-content/uploads/2025/02/Kerite-Pump-Cable-Brochure-12.23.pdf).

### Temperature correction

Given `R77` in Ω/kft and deployed `L` ft:

\[
TCF=1+0.00214(T_{cable,F}-77)
\]
\[
R_T=(L/1000)R_{77}TCF
\]
\[
\Delta V=1.732IR_T
\]

The vendor publishes this field-unit correction and a recommended `ΔV<30 V/kft` screening threshold. [Kerite brochure](https://marmoniei.com/wp-content/uploads/2025/02/Kerite-Pump-Cable-Brochure-12.23.pdf)

\[
V_{surface,required}=V_{motor,terminal,target}+\Delta V+V_{other\;drops}
\]

`Vother_drops` can include MLE, splice, transformer/VSD output and surface lead losses if modeled. Evaluate at worst anticipated current and hottest cable temperature, as well as startup/transient limits from vendor controls data.

## 7.2 Ampacity / AWG selection

Select the smallest cable satisfying simultaneously:

1. continuous ampacity at downhole ambient temperature, installation geometry and insulation rating ≥ design current with operator margin;
2. voltage-drop limit at maximum run length/current/temperature;
3. cable + bands/guards + coupling clearance through the tightest drift/restriction;
4. chemical, pressure/gas migration, H₂S/CO₂, temperature, armor and handling requirements.

Resistance at 20°C from conductor area:

\[
R_{dc}=\rho L/A
\]

The vendor gives example stranded-conductor ampacities of #4=121 A, #2=164 A and #1=191 A, but **do not make them global engine limits**: ampacity comes from the exact cable data sheet and thermal environment. [Kerite brochure](https://marmoniei.com/wp-content/uploads/2025/02/Kerite-Pump-Cable-Brochure-12.23.pdf) The same source presents Neher–McGrath thermal-resistance equations; use vendor published ampacity tables rather than reconstructing thermal models without all installation assumptions.

**Configurable default voltage-drop gates:** conservative `≤5%` of motor nameplate V; generic ESP teaching references also cite `<30 V/kft` or `<15%` nameplate V. [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf) [KSU ESP material](https://faculty.ksu.edu.sa/sites/default/files/4-electricalsubmersiblepumps.pdf)

## 7.3 Transformer, switchboard and VSD kVA

\[
kVA_{motor} = \frac{\sqrt3V_{LL}I}{1000}
\]

For a VSD at maximum frequency:

\[
kVA_{design}=\frac{\sqrt3\,[V_{motor,60}(f_{max}/60)+\Delta V_{cable}]I_{max}}{1000}
\]

This maximum-frequency ESP sizing expression is published by [SPE’s design paper](https://www.swpshortcourse.org/ppdl/1790). Apply configurable design margin/derating for transformer impedance, harmonics, ambient, overload/start strategy, upstream supply, and utility/operator requirements. VSD, switchboard and transformer must be rated for voltage, current, kVA, frequency range, harmonic duty and protection coordination—not only motor nameplate hp.

---

# 8. GEOMETRY / RUN-IN-HOLE VALIDATION

## 8.1 Clearance

At every depth/restriction:

\[
Clearance_{diam}=ID_{drift/min}-OD_{assembly,max}
\]
\[
Clearance_{radial}=Clearance_{diam}/2
\]

`ODassembly,max` includes pump, motor, protector, intake/separator/AGH, MLE, flat cable, bands, guards, coupling upset and any shroud—not merely nominal pump OD. Require `clearance_diam ≥ configured_min`; set a hard failure for nonpositive clearance and a review failure below an operator/vendor clearance margin. There is no universal clearance in inches because it depends on dogleg, string length, cable profile and tool joint geometry.

## 8.2 Casing and ESP series reference table

Nominal casing OD does not uniquely determine ID/drift: weight, grade, connection and wear determine it. Resolve the actual `ID` and drift from the casing tally / API table for the exact weight; the published drift chart illustrates why values vary by nominal weight. [Casing size and drift chart](https://www.flowtechenergy.com/charts/casing-size-and-drift-chart/)

| Nominal casing OD (in) | Typical ESP series that may fit | Series nominal OD (in) | Published minimum casing (in) |
|---:|---|---:|---:|
| 4.500 | 338 | 3.38 | 4.50 |
| 5.500 | 400 | 4.00 | 5.50 |
| 6.625 | 513 | 5.13 | 6.63 |
| 7.000 | 538 / 562 | 5.38 / 5.62 | 7.00 / 7.00 |
| 8.625 | 675 | 6.75 | 8.63 |
| 10.750 | 862 | 8.62 | 10.75 |
| 13.375 | larger series | vendor-specific | vendor-specific |

The series OD/min-casing table is from a manufacturer catalog. [Al Khorayef Spectrum catalog](https://alkhorayefpetroleum.com/wp-content/uploads/2025/03/SPECTRUM-Centrifugal-Pump-Series.pdf) These are **not clearance guarantees** and do not include cable/guards.

For a first engine library, store nominal casing ODs `[4.5, 5.5, 6.625, 7.0, 8.625, 9.625, 10.75, 13.375]` and require a keyed `CasingSpec(weight, grade, connection, drift_id, drift_length)` record. Do not default a drift ID from nominal OD for release calculations.

## 8.3 Dogleg severity (DLS), deviation, and setting interval

\[
DLS\;(deg/100ft)=\frac{\Delta\theta\;(deg)}{\Delta MD\;(ft)}\times100
\]

Screen every 100-ft (or survey-station) interval from surface through setting depth and calculate a straight/tangent interval around the final ESP location.

Published rules of thumb vary: conventional guidance often uses max DLS around 6°/100 ft for passage and a much straighter setting section; a SPE workshop summary says stay below 10°/100 ft and set in straight pipe below 2°/100 ft, while other completion guidance triggers supplier consultation at >3°/100 ft. [SPE-GCS workshop summary](https://www.spegcs.org/media/files/files/cebfcc3a/2013-ESP-Workshop-Summary-of-Presentations.pdf) [ESP preparation guidance](https://drillingforgas.com/completion/equipment-completion/emergency-submersible-pumps-esp-preparation-of-the-well/)

**Engine policy:** defaults may screen `passage DLS ≤6°/100 ft`, `setting-zone DLS ≤2°/100 ft`, `minimum tangent=150 ft`; all three must be configuration values and vendor exceptions can override only with documented stress analysis. Check restrictions, liner tops, PBRs, packers, scale, casing wear, and dogleg **above** the setting depth, not just at final depth.

---

# 9. DESIGN VALIDATION GATES

Every gate must output `PASS | WARN | FAIL | NEEDS_VENDOR_DATA`, the value, units, limit source/configuration key, and message. Suggested release gates:

| Gate | Calculation/check | Default numeric screen | Status policy |
|---|---|---:|---|
| Input basis | P/T absolute; rate bases tagged | mandatory | FAIL if missing/ambiguous |
| IPR | `0≤pwf≤pR`, `q≤qmax` | mandatory | FAIL |
| IPR regime | PI used only above `pb` | mandatory | WARN/FAIL based on requested mode |
| PIP | PIP positive and datum-consistent | `PIP>0 psia` | FAIL |
| PVT range | Inputs within selected correlation range | correlation-specific | WARN + expert review outside |
| Free gas | `λg,pump` after actual separation | standard screen ≤10% | WARN above; see configured gas strategy |
| Turpin | `φ<1` only if Turpin model enabled | `<1` | WARN/FAIL per model validity |
| Natural separation | completion has credible annular vent path | mandatory if credit taken | FAIL if no geometry |
| Separator | residual GVF uses map at actual rate/PIP/viscosity | mandatory if selected | NEEDS_VENDOR_DATA if map absent |
| Pump flow | Q within manufacturer recommended range | catalog range | FAIL outside recommended; allow excursion only override |
| BEP location | fractional rate to BEP | preferred 0.70–1.20; generic fallback | WARN; catalog controls |
| Thrust | down/up thrust within pump/protector limits | vendor only | NEEDS_VENDOR_DATA if absent |
| Stages | `ceil(TDH/Hstage)` and housing capacity | mandatory | FAIL if housing insufficient |
| Shut-in housing pressure | §5.5 `MHP` vs housing rating | vendor rating | FAIL |
| Shaft hp/torque | actual vs max at frequency | vendor rating | FAIL |
| Motor loading | `HPreq/HPnameplate` | target 0.75–0.85 configurable | WARN outside target; FAIL at catalog limit |
| Motor cooling | `v_motor` | ≥1 ft/s screening | WARN/FAIL or require shroud |
| Motor temperature | thermal model / rating | vendor rating | FAIL |
| Cable ampacity | current vs derated ampacity | exact cable table | FAIL |
| Cable voltage drop | ΔV and terminal V at max current | ≤5% configurable; <30 V/kft screen | WARN/FAIL based on standard |
| Voltage unbalance | surface/motor supply | ≤5% | FAIL/derate; cited ESP guidance notes severe current impact. [SPE Compendium](http://superb.org/pubs-paper/other/spe95-comp.pdf) |
| Surface equipment | kVA/current/frequency/harmonics | vendor/device rating | FAIL |
| Casing clearance | max assembly OD + cable protection vs min drift | positive plus configured margin | FAIL / review |
| DLS | all passage and setting-zone stations | ≤6°/100 ft passage; ≤2°/100 ft setting default | WARN/FAIL configurable |
| Materials | temperature, H₂S/CO₂, solids, elastomer, cable rating | vendor compatibility | NEEDS_VENDOR_DATA |
| Tubing | velocity/friction/pressure rating | friction ideally <10% of total system energy | WARN; source guideline [SPE design paper](https://www.swpshortcourse.org/ppdl/1790) |

**Threshold policy:** Numerical defaults above are screening settings, not vendor guarantees. Store them in a versioned `DesignPolicy` and report the policy version with every design. Pump/vendor data override generic gates.

---

# IMPLEMENTATION NOTES

## Selectable/configurable correlations and data

1. **IPR:** PI, Vogel, composite PI/Vogel, Fetkovich/custom tabular IPR.
2. **Oil PVT:** lab table (preferred), Standing, Vazquez–Beggs; separately select above-bubble `Bo` and `μo` models.
3. **Gas z:** lab/EOS, DAK, Hall–Yarborough; Sutton vs compositional pseudocritical treatment.
4. **Water:** lab brine table, McCain screen, Spivey–McCain–North.
5. **Tubing/annulus pressure:** liquid Darcy, homogeneous, Beggs–Brill/Hagedorn–Brown/Ansari or vendor multiphase solver. Do not bury this choice.
6. **Gas handling:** no natural separation, Alhanati, Marquez–Prado, vendor test map; fixed separator efficiency should be permitted only as scenario input and labelled.
7. **Pump degradation:** manufacturer two-phase map preferred; Turpin variant/field-linear correlation only opt-in with validity metadata.
8. **Viscosity:** vendor viscous curve or licensed ANSI/HI implementation; never an undocumented single correction factor.
9. **Pump/motor/cable:** versioned catalog datasets with model, revision, fluid/test condition and provenance.
10. **Policy:** gas thresholds, BEP windows, voltage-drop limit, motor-load target, cooling velocity, clearance and DLS gates must all be versioned configuration.

## Numerical pitfalls and defensive practices

* **Pressure:** PVT requires psia; head/wellhead operating constraints often arrive as psig. Convert once at data ingress.
* **Temperature:** only convert to °R for gas equations; using °F in `Bg` makes a ~460/temperature error.
* **Gas FVF:** confirm rb/scf (`0.00504`) vs ft³/scf (`0.02827`); do not mix Mscf with scf.
* **Water cut:** define whether `WC=qw/(qo+qw)` on stock-tank basis. Reject `WC<0` or `>1`.
* **PIP–gas coupling:** use a bracketed solve in PIP or system rate. At each iteration recompute `Rs,Bo,Bg,λg`, separation, pump gas derate, discharge requirement, and PIP residual. Use damping; retain last physically valid state.
* **Root solves:** DAK requires positive `z`; Vogel needs the positive quadratic root. Apply residual and iteration caps; fail loudly on non-convergence.
* **Bounds:** clamp efficiencies `[0,1]`, void fractions `[0,1)`, `Qfree≥0`; never clamp an invalid intermediate silently—log reason.
* **Interpolation:** use monotone interpolation of curves; reject extrapolation beyond vendor allowed range. Curve grids must preserve units and reference frequency.
* **Frequency:** scale curve **points**, and subsequently apply motor/cable limits at actual frequency. Do not assume motor hp follows affinity law.
* **Head vs pressure:** stage head in ft is a liquid-energy measure; pressure increment changes with density. For high-GVF/tapered systems, simulate stage-by-stage rather than `TDH/head-per-stage` only.
* **PIP location:** all gauge pressures require a datum/TVD; reject computations mixing MD and TVD.
* **Casing:** nominal OD is not drift ID. Use actual casing tally/restriction survey.
* **Turpin formula:** published secondary sources disagree on a coefficient; do not activate without approved source/version tests.

## Recommended solution order

1. Validate units/bases, depth datums, test date, PVT source and component data.
2. Determine `pR`, `pb`, IPR model and solve `pwf(q_target)`.
3. Pressure-traverse from perforation datum to tentative intake to obtain initial `PIP`; use measured PIP if trustworthy.
4. At `PIP,TIP`, calculate `Rs`, `Bo`, `Bw`, `z`, `Bg`, densities, `Vo,Vw,Vg`, `λg`.
5. Apply configured natural and mechanical separation; calculate residual `λg,pump`.
6. Calculate tubing/outflow requirement and TDH or pump discharge-pressure requirement.
7. Choose candidate pump from casing fit and rate/BEP range. Shift curves to frequency; apply licensed viscosity and validated gas correction.
8. Solve coupled operating point / PIP residual iteratively. For high gas, use stage-by-stage pressure/volume updates and check stability.
9. Calculate stages, housing pressure, BHP, shaft power, thrust and protector selection.
10. Select motor; evaluate loading, current, slip, temperature/cooling and frequency limits.
11. Select cable; calculate temperature-corrected ampacity and voltage drop; size VSD/transformer/switchboard at max frequency/current.
12. Run geometry/DLS/material/release gates; emit assumptions, correlation versions, warnings and non-converged diagnostics.

## Minimum regression-test set

* Straight PI and Vogel inverse solve at `pwf=0,pb,pR`.
* Standing/VB round-trip: `Rsb → pb → Rs(pb)`.
* PVT boundary continuity (`Rs` at `pb`, `qfree=0` where `GOR=Rs`).
* Gas `z` benchmark grid and DAK non-convergence paths.
* `λg=0`, modest GVF, and high-GVF cases with separation on/off.
* Pump affinity 50/60/90-Hz point transformations.
* Cable #4/#2/#1 published resistance and temperature-correction examples.
* Geometry case where nominal casing fits but drift/cable guards do not.
* Coupled PIP solve with damping and a deliberately impossible system curve.

---

## Source provenance (principal primary/technical references)

* SPE PetroWiki, [Electrical Submersible Pumps](https://petrowiki.spe.org/PEH:Electrical_Submersible_Pumps), [ESP design](https://petrowiki.spe.org/ESP_design), and [harsh-environment gas behavior](https://petrowiki.spe.org/Use_of_ESPs_in_harsh_environments).
* M. B. Standing and M. E. Vazquez/H. D. Beggs correlation provenance/ranges documented by [S&P Global upstream technical documentation](https://www.ihsenergy.ca/support/documentation_ca/WellTest/content/html_files/reference_materials/calculations_correlations/oil_correlations.htm).
* DAK equation/coefficient implementation reference: [Dranchuk–Abou-Kassem technical document](https://app.ingemmet.gob.pe/biblioteca/pdf/CLG12-73.pdf).
* SPE, *Compendium of Electrical Submersible Pump Systems*: [SPE 29506](http://superb.org/pubs-paper/other/spe95-comp.pdf).
* Turpin/head-degradation review: [Review of gas–liquid flow in ESPs](https://pdfs.semanticscholar.org/7299/df660becc71d3bc54a4d8ae9db8f8bb601e4.pdf); compare cautionary field model [Springer paper](https://link.springer.com/article/10.1007/s13202-021-01392-y).
* Natural separation: [Alhanati-derived NGSE paper](https://www.tandfonline.com/doi/abs/10.1080/19942060.2024.2395452) and [TUALP comparison](https://oilproduction.net/files/predicting_downhole_natural.pdf).
* Cable equations/data: [Kerite ESP cable brochure](https://marmoniei.com/wp-content/uploads/2025/02/Kerite-Pump-Cable-Brochure-12.23.pdf).
