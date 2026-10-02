# ESP Design Automation — Implementation Roadmap

Status of v0.1 and what it takes to reach field use. Written against the built
system, not the original framework document.

## Latest correction: VSD validity, 2026-10-01

This update supersedes older full-horizon demo claims below. Engine 0.1.1 keeps
the 84-stage RC2500 / 55 Hz leader, but identifies the first baseline head
shortfall at month 3 rather than treating BEP proximity as proof of operation.
At month 6, baseline head is 1,634 ft against 1,890 ft TDH. A recalculated 60 Hz
trial provides 2,008 ft on the same installed motor, protector and cable.

Verified VSD samples run from months 3 through 14 at 57.5–65 Hz. Month 15 fails
the head requirement even at the tested maximum 65 Hz. This is sampled,
current-model recovery, not a continuous operating guarantee or field approval.
The timeline now plots head versus TDH and evidence-backed recovery points;
legacy runs are labeled unverified and require recalculation.

Motor/cable/thermal/mechanical checks now run on fixed equipment throughout the
trajectory and frequency trials. Narrative scope retains recovery boundary
evidence and the full frequency schedule; nearest-foot numeric matching prevents
the richer evidence from weakening the existing guard ceilings. The full backend
suite passes 420 tests; the frontend production build passes.

The live demo engine facts were stored in 0.86 seconds. Its separate narrative
was withheld by the guard after interpreting the English preposition “in”
following a dimensionless score value as inches. That narrative parsing issue
remains open; the stored calculation facts and corrected timeline are unaffected.

Vendor curves, motor-data contradictions, thrust-bearing limits, surface/start-up
verification and between-sample uncertainty remain unresolved. The electrical
model is more thoroughly applied, not a complete validated field design.

## What exists and works

| Layer | State | Evidence |
| --- | --- | --- |
| Deterministic engine | 21 modules, no LLM import path | `grep -rn "esp_agents\|anthropic\|openai" backend/esp_engine/` returns nothing |
| Trajectory + validity boundary | Corrected for head sufficiency | Demo baseline first failure at month 3; verdict `feasible_with_caveats` |
| Head-shortfall gate (§6.3) | HARD at M0, SOFT along trajectory | Under-staged configs never rank; validity boundary still reports later-month shortfall |
| Scoring | Working, with honest tie handling | Top three separated by 1e-3, presented as tied |
| Empirical layer | Kaplan-Meier, derived rules, bias flags | Verified against a seeded 10-observation tenant |
| Agent layer | Intake + narrative, both guarded | Verified end to end against a real model |
| Numeric guard | Working after six defect fixes | False-accept 0.1–2% on most dimensions, 6% on `ft`, 10% on `bpd` |
| Deployment posture (§9.7) | Local vs external model endpoint classification | `/api/deployment` reports posture + contradiction |
| API | Queued facts-first runs, perimeter-scoped | Full backend suite: 420 tests passing |
| Frontend | Six views, live data | Deployed preview reads the live backend, danger-red posture-contradiction banner |

The architectural invariant holds: the engine produces facts, the agent layer
produces judgments, and a judgment can annotate a fact but never replace it.
Every run is reproducible from `case_hash + config_hash + catalog_version +
engine_version`.

## The blocker: this cannot size a real well yet

All 13 pump curves are `parametric_estimate`. No vendor multi-point curve file
was publicly obtainable, so head, efficiency, and thrust boundaries are fitted
approximations. The fitted efficiency polynomial is not even constrained to zero
at shut-in — it reports 0.327 at zero flow.

**Nothing else on this list matters until this is fixed.** The engine's mechanics
are sound and the workflow is real, but a design produced from an estimated curve
is not a design. This is a licensing and data-acquisition problem, not an
engineering one, and it needs a commercial conversation with pump vendors or a
paid catalog source.

Second-order data gaps, in priority order:

1. **Series 400 has two motors (one PMM, one induction estimate).** Depth of
   coverage is still thin -- the induction record is a parametric estimate,
   not vendor-verified, and picker-level candidate variety is still narrow.
   Real vendor motor datasheets at additional HP points would let the picker
   discriminate on more than housing OD.
2. **ANSI/HI 9.6.7 viscous correction** is refused, not approximated. The
   equations are licensed. A viscous well cannot be sized correctly today.
3. **`MechanicalCheck.thrust_load_lb` stays `None`** — no thrust data source.
*(No open electrical placeholders remain. The last — linear operating amps
— was closed by the §6 phasor form; see closed defects below. Row 1–4 of
§6D.2 is complete: motor thermal, cable calculated temperature, and the
load-scaled phasor current the cable is sized against.)*

These refusals are deliberate and should stay refusals until real data exists.
Replacing them with plausible correlations would make the tool confidently wrong,
which is worse than incomplete.

## Next engineering work, in order

### 1. Live narrative refused at the numeric guard's rounding boundary
On the demo case the live narrative is withheld after two attempts on a single
token, `68.0%`. Design-point efficiency is 68.04%, average efficiency 68.056%.
Since quantity-name binding (`ba17d47`), "average efficiency of 68.0%" is checked
against `efficiency_avg_frac` alone, and 68.056 is 0.056 from 68.0 against a
0.05 tolerance, so the sentence is refused. Before binding it passed, because
68.04 sat in the shared percentage pool. Strictly the model mis-rounded (68.056
rounds to 68.1), but the refusal costs the whole narrative for a 0.006-point
rounding miss, which is the v2 failure mode the roadmap warns about. The fix is
a tolerance decision rather than code: either allow truncation (0.1 points for
one-decimal percentages) or tell the writer the exact rounding on retry. The
exact sentence the model wrote was not captured; the guard-level reproduction
above is what is verified.

### 2. Case-hash invalidation in the UI
`GET /api/cases/{id}/designs` returns the reproducibility triple per run, but the
frontend just takes the newest. It should compare against the current case,
config, and catalog and tell the user when a stored run is stale rather than
displaying it as current.

### 3. Real multi-tenant auth
Tenancy is an `X-Tenant-Id` header with no verification. Every query is correctly
tenant-scoped and tenant isolation is tested, so the enforcement point is the only
missing piece — but it is missing entirely and must be closed before any external
user touches this.

### 4. Empirical layer needs data volume
The rule engine refuses automatic threshold mining by design, requiring
pre-specified hypotheses. That is correct and it means the layer produces nothing
until real field history accumulates. Plan for a data-ingestion path from
customer teardown reports and telemetry, not for a cleverer algorithm.

### 5. Viscous, gassy, and deviated coverage
Gas handling exists but the natural-separation model is a vendor rule of thumb.
Deviated-well intake behaviour is not modelled beyond geometry checks.

## What not to do

- Do not let the LLM compute. Every number a narrative states must be traceable to
  an engine field; arithmetic on real values is rejected on purpose.
- Do not resolve a refusal by substituting a correlation. `None` is a valid
  engineering answer and the product's credibility depends on it.
- Do not present ranked candidates when the score margin is numerical noise. The
  1e-3 relative tie tolerance exists because inputs are estimates.
- Do not render an estimated curve without its caveats. The curve endpoint returns
  them so every consumer inherits them.

## Recently closed defects

- **Design runs moved off the request path.** The roadmap attributed the
  2m20s synchronous run to enumeration. Measured, it was not: the engine
  stage takes 0.4 s on the demo case and the narrative model loop about
  120 s. A run is now a two-stage job (`api/jobs.py`) on two separately
  bounded thread pools. The engine stage stores the design's facts and hands
  off; the narrative stage rebuilds the result from storage (exact, tested)
  and attaches prose to the narrative slot only, so `facts_json` is written
  once and never touched again. `POST /api/cases/{id}/design` returns 202
  with a job in 0.03 s (was 119 s); facts are readable at 0.3-0.6 s; job
  polls stay under 45 ms while the model runs. Admission is bounded globally
  (`ESP_MAX_ACTIVE_JOBS`, default 32) and per perimeter
  (`ESP_MAX_ACTIVE_JOBS_PER_PERIMETER`, default 4) with 429 + Retry-After.
  An identical submission while one is active returns that job. Cancel works
  queued (never runs) and in flight (no further model calls; a reply already
  in flight is discarded; facts kept; the job reports `cancel_requested`
  until the call returns). Jobs persist in the perimeter's own store, so a
  job orphaned by a restart reads as `interrupted`, and
  `POST /api/designs/{id}/narrative` retries prose without recomputing facts.
  `?wait=true` keeps the blocking contract for scripts and tests. Threads,
  not processes, on measurement: the CPU stage is sub-second and `Case` does
  not pickle (generic `Tracked[...]` fields). The UI shows facts as soon as
  they exist, marks the narrative pending/cancelling/cancelled in the top
  bar, and resumes polling after a reload. 12 new tests; 7 of 7 guard
  mutations killed.
- **Quantity-name binding in the numeric guard.** Percentages named next
  to a quantity phrase ("water cut 58%", "efficiency 41%", "motor loading
  dropped to 44%") are now checked against that specific schema field's
  values only, not against the wider pool of every fraction in scope. The
  binding is a leading-phrase regex over a table of alias phrases mapping
  to field names (water_cut_frac, efficiency_frac, motor_loading_frac,
  cable_voltage_drop_frac, head_margin_frac, q_over_qbep, time_coverage_frac,
  bep_time_frac, distance_from_bep_frac, apparent_power_factor); multi-word
  aliases match longest-first, and up to three intervening words are
  allowed to accommodate verbal phrasings. Two guardrails preserve the
  roadmap's warning against narrowing scope into refusal-of-truth:
  (1) an unknown alias or a field with no values in scope falls back to
  the wider frac_pct check, so a legitimate narrative can never become a
  refusal by lack of binding; (2) an engine-prose exemption keeps
  band-boundary constants the engine wrote ("outside the 75%-85% target
  band", "beyond the 10% tolerance", "5% allowable") quotable regardless
  of adjacent quantity name -- restricted to values NOT already sitting
  in some per-field bucket, so a drift-note water-cut number cannot
  accidentally vouch for a fabricated efficiency at the same integer.
  Measured effect on the demo case: unbound percentages stay at 31%
  false-accept (fall-through path preserved), named-quantity percentages
  drop from 31% to a mean of 7.7% across efficiency, motor loading,
  voltage drop, head margin, coverage, q/qbep (4x tighter). Water cut is
  the honest outlier at 27% because the demo trajectory publishes 74
  distinct water-cut values covering integers 35-58; those are real, and
  the residual is the density of the trajectory rather than a guard
  weakness. 10 new tests: 7 parametrized ceiling tests for named
  quantities, one positive test proving every legitimate named
  percentage the engine publishes still passes, one engine-prose
  exemption test, one cross-field regression test (`efficiency is 58%`
  is refused now that water_cut and efficiency are properly separated).
- **§6 load-scaled operating amps (last electrical placeholder).**
  Motor operating current is no longer `load·I_FL`. Replaced with the
  induction phasor form `|I(load)| = √(I_μ² + (load·I_FL·PF)²)` with
  `I_μ = I_FL·√(1−PF²)` per §B.14.1 / B.16 (magnetizing branch does
  not scale with shaft load). Router in `electrical_load.py` picks:
  operator-supplied magnetizing fraction (highest precedence) → PMM
  near-linear with configurable residual → cataloged PF phasor form →
  linear nameplate scaling (fallback, disclosed). At PF 0.85 and 50%
  load the phasor form is ~35% above the linear figure, which is what
  the cable is actually sized against. No midpoint substitution for PF
  (framework §3.5 / B.16). Frontend renders the two-term decomposition
  (I_μ, I_L, apparent PF at load) alongside the existing motor and
  cable thermal disclosures. `AmpsBasis` (I_FL source) and
  `LoadCurrentBasis` (load-scaling form) are reported separately so
  provenance stays legible. Seventeen new tests (14 physics, 3
  pipeline).
- **Series-400 induction seed motor for the §6 phasor demo.** Added
  `centrilift-540ind-est` (series 400, 60 hp, 4.0 OD, PF 0.85, eff
  0.88) so the demo route actually exercises the phasor form live.
  Data quality is `parametric_estimate` -- HP/volts/amps are scaled
  from Baker Hughes Centrilift 540 series induction literature and
  are NOT vendor-verified for a 4.0 OD build. This motor now beats
  the Novomet series-400 PMM in the demo case at rank 1, so the
  live payload shows `basis=phasor_from_cataloged_power_factor`,
  operating current 42 A vs linear reference 36 A (+17% at 65%
  load), apparent PF dropping from 0.85 nameplate to 0.725. The
  three §6 pipeline tests that previously skipped for lack of a
  cataloged induction motor at that series now execute (378 total
  passing, 0 skipped). Test-side fallout: five tests that assumed
  the picker would land on a PMM in the demo case were updated to
  build PMM-only catalog views so their invariants (VSD hard gate,
  magnet gate disclosure, mandatory-VSD warning, PMM contradiction
  case) still measure what they were written to measure rather than
  accidental catalog composition. The catalog `notes` field on the
  new motor discloses the estimate provenance for anyone reading
  the assumption ledger.
- **§6D.2 cable calculated temperature (row 4).** Cable sizing no
  longer treats intake fluid temperature as the conductor temperature.
  The two-term display now shows `conductor = fluid + I²R rise` per the
  §6D.2 display table row 4. Rise is anchored to 20 F for water and 36 F
  for oil at cable ampacity and 1 ft/s annular velocity, scales with
  (I/I_amp)² on the loss side and Dittus-Boelter (v_ref/v)^0.8 on the
  fluid film, and includes a fixed-point resistance-amplification loop
  for the copper temperature coefficient. The insulation-class screen
  (framework B.14.5) now compares conductor temperature to
  `CableModel.max_temp_f` (a rated cable can no longer clear the screen
  while operating above its rating), and the voltage-drop calculation
  uses the calculated conductor temperature for the resistivity
  correction. Dielectric losses are neglected (medium voltage,
  negligible vs I²R) and multi-node radial conduction is not modeled
  (would need per-cable vendor thermal data). Nineteen new tests including
  a mutation guard proving the screen restricts more than the placeholder.
- **§6D.2 motor thermal self-heating.** Winding temperature is no longer
  set to intake temperature (the previous placeholder). Two-term
  display per framework: `winding = fluid + calculated self-heating`,
  anchored to the framework's cited field observation (50°F rise for
  water, 90°F for oil at 1 ft/s and nameplate load). Load scales
  linearly, velocity via Dittus-Boelter turbulent forced-convection
  exponent 0.8, gas augmentation as reduced-liquid-mass past the stator
  capped at 50% FGVF, efficiency rescaled by `(1-η)/(1-0.85)`. Winding
  temperature now feeds the B.14.5 insulation check as a hard
  ConstraintViolation, and the frontend renders both terms so the
  engineer can pick between a shroud (self-heating dominant) and a
  high-temperature build (fluid dominant). Missing-efficiency fallback
  is disclosed rather than silently applied.
- **Finding 1 — head-shortfall gate + dedupe key regression (commit `074fbc3`).**
  Two independent bugs let three RC2500 41-stg candidates rank first on the
  demo case with a −22.6% head margin at month 0. Dedupe's key reduced to
  the constant `1/tolerance`, folding every stage count for a
  (pump, depth) into one bucket and preferring the smallest member; and the
  pipeline never raised a hydraulic ConstraintViolation for negative head
  margin. Fixed both. Base score moved from 0.824204 to 0.992559; leader is
  now RC2500 84 stg @ 55 Hz with +9.3% head margin. Framework §6.3 ("size for
  the full range") is now actually enforced rather than merely observed.
- **§6B.2 API RP 11S2 tolerance band.** Ranked candidates now carry a
  per-pump `tolerance` assessment against the acceptance band; material
  channel flips generate specific unit-test-report data requests.
- **§9.7 base-URL provider abstraction (commit `370cec1`).** Model
  endpoint routing is classified as `local_model_endpoint` vs
  `external_model_endpoint`; posture contradictions (e.g. an operator
  declaring an air-gapped mode while pointing at an external host) surface
  as a danger-red banner in the frontend PostureView.

## Test and verification posture

378 tests, 0 skipped. Run from the repo root:

```
cd /home/user/workspace/esp/backend && python3 -m pytest tests -q
```

The narrative can be probed against a real model from the CLI:

```
python backend/scripts/probe_narrative.py   # needs llm-api credentials
```

That probe is what found all six guard defects. Synthetic-text testing had
reported the guard as hardened when it in fact refused every real narrative.
Keep probing against a real model before believing any claim about this layer.

## Catalog sources

Pumps and motors from the [SLB REDA ESP technology catalog](https://media.theofmp.com/media/sals/documents/schlumbergerredaesptechnologycatalog_v1.pdf)
and the [MPS PM Motor catalogue](https://www.magneticpumpingsolutions.com/catalogues/MPS%20PM%20Motor%20Catalogue%20-%20Jan%202019.pdf).
Cable from the [Kerite ESP cable brochure](https://marmoniei.com/wp-content/uploads/2023/11/Kerite-Pump-Cable-Brochure-12.23.pdf),
with conductor resistance from [Philatron](https://philatron.com/cable/copper/dc-copper-resistance.php)
and [Engineering ToolBox](https://www.engineeringtoolbox.com/copper-wire-d_1429.html).
Cable sizing method from [Production Technology](https://production-technology.org/esp-design-step-6-electric-cables/)
and [ESP Expert](https://espexpert.com/presentations/espexpert/09%20Cable.pdf).
