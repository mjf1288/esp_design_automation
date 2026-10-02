# ESP Design Automation — System Architecture

**Version:** 0.1 → implementation
**Derived from:** ESP Sizing & Design Automation Framework v0.1 (expert interview, SLB / Baker Hughes ESP Application Engineering)
**Status:** implementation architecture for the v0.1 framework skeleton

---

## 0. The one architectural rule

Everything in this system obeys a single invariant, taken directly from §1 of the framework:

> **The deterministic layer produces facts. The agentic layer produces judgments. A judgment may annotate a fact. A judgment may never replace, modify, or override a fact.**

This is enforced structurally, not by convention:

- The calculation engine (`esp_engine/`) has **no LLM client, no network access, and no import path to one**. It is a pure function of `(Case, Catalog, Config) → DesignResult`. Same inputs, same bytes out, forever.
- The agentic layer runs **before** the engine (intake: dirty text → structured case) and **after** the engine (interpretation: results → narrative, risk, recommendations). It never runs *inside* it.
- Every value that reaches the engine carries a `Provenance` record saying where it came from. Every value that leaves the engine carries the assumption set it depended on.
- The API response separates `facts` from `judgments` at the schema level. The frontend renders them with different visual treatment. A judgment is never presented in a way that could be mistaken for a computed result.

If a future contributor is tempted to "let the model adjust the TDH a bit," the architecture must make that physically awkward to do. That is the point.

---

## 1. System decomposition

```
┌──────────────────────────────────────────────────────────────────────┐
│  INTAKE (agentic)                                                     │
│  email / datasheet / PDF / transcript / form                          │
│    → extraction  → normalization  → classification                    │
│    → assumption filling  → confidence assignment                      │
│  OUTPUT: Case (fully typed, every field with Provenance)              │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  Case is frozen here. Hash recorded.
┌────────────────────────────▼─────────────────────────────────────────┐
│  DECISION GATE 1 (deterministic rules, not a model)                   │
│  Branch A: replacement, history known  │  Branch B: no anchor         │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  SCENARIO EXPANSION (deterministic)                                   │
│  Case + trajectory model → N ScenarioPoints across the life horizon   │
│  (PI, Pr, WC, GOR drift; min / base / max envelope)                   │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  DETERMINISTIC ENGINE — per (candidate config × scenario point)        │
│  IPR → Pwf → PIP → PVT → free gas → gas handling → TDH                │
│  → stages → BHP → motor → seal → cable → surface                      │
│  → CONSTRAINTS GATE (geometry / electrical / mechanical / stock)       │
│  → curve-zone classification (BEP / range / downthrust / upthrust)     │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  TRAJECTORY SCORING (deterministic)                                   │
│  score each surviving config across the FULL scenario range → rank    │
│  compute VALIDITY BOUNDARY: when does this design stop working        │
│  compute VSD RECOVERY: what portion of range frequency can reclaim    │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  DesignResult — immutable, hashable, replayable
┌────────────────────────────▼─────────────────────────────────────────┐
│  EMPIRICAL OVERLAY (learned, flagged)                                 │
│  retrieve rules matching (pump, region, fluid, complication, zone)    │
│  compute rule confidence from context → attach as Judgment            │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  NARRATIVE (agentic)                                                  │
│  facts + judgments → engineer-facing explanation, report, Q&A         │
│  grounded strictly in the DesignResult; cannot introduce new numbers   │
└──────────────────────────────────────────────────────────────────────┘
                             │
                    REVIEW & FEEDBACK LOOP
        engineer accepts / overrides → agreement statistics
        → trust level progression (§11) + new empirical observations (§8)
```

---

## 2. Why the calculation loop is inverted

Framework §6.3 specifies the inversion, and it drives the whole compute design:

```
WAS:  size for a point  →  check at other points
IS:   enumerate configurations  →  score each across the full range  →  rank
```

Implementation consequence: the inner unit of work is not "a design" but a **cell** — one candidate configuration evaluated at one scenario point.

```
cells = |candidate configs| × |scenario points|
```

A realistic enumeration is ~40 pump models × ~6 stage counts × ~5 frequencies ≈ **1,200 configs**, evaluated at ~24 scenario points (monthly over 2 years) ≈ **29,000 cells**. Each cell requires an iterative PIP/PVT solve.

This is why the engine is written as a **vectorized, pure, cache-friendly kernel**:

1. **Staged pruning.** Cheap constraints first. Geometry and electrical constraints (§3.2, "checked at input") eliminate most configs before any PVT math runs. Series/casing-ID incompatibility is a set-membership test, not a calculation.
2. **Scenario-point PVT caching.** PVT properties depend on the *scenario*, not the *configuration*. Solve fluid properties once per scenario point and reuse across all 1,200 configs. This is the single largest win — it collapses the expensive part from `configs × points` to `points`.
3. **Curve evaluation as polynomial coefficients**, not table interpolation. Affinity-law frequency scaling is applied analytically to the coefficients.
4. **NumPy across configs**, plain Python across the outer loop. Vectorizing the axis with 1,200 elements matters; vectorizing the axis with 24 does not.

Target: full run under 2 seconds for a single well on one core. The framework's core promise is "20 scenarios instead of 3–4" (§2.1) — that promise is a *performance* requirement, so it is a first-class architectural constraint, not an optimization to defer.

---

## 3. Data model — the three input classes are types, not tags

Framework §3 insists the split is not mandatory/optional but three classes living in different logic branches. So they are three distinct types with different behavior:

| Class | Type | Behavior |
|---|---|---|
| `Expectations` | targets | Drives the design. May be proven unrealistic by the calculation — that is a *legitimate output*, not an error. |
| `Constraints` | hard limits | Enforced by the constraints gate. Each carries a `rigidity` (`absolute` / `hard` / `soft`) and a `check_stage` (`at_input` / `after_selection`). |
| `Complications` | operating challenges | Modify both calculation and selection. Each carries a `bias_direction` implementing asymmetric conservatism (§5). |
| `Reference` | anchor | Presence/absence sets the Decision Gate 1 branch. |

### Provenance is mandatory on every soft input

Framework §5.2 and §10 both demand it, and §8.2's error-class analysis makes it non-negotiable. Every field is a `Tracked[T]`:

```python
Tracked(
    value = 1200.0,
    unit = "bpd",
    source = Source.TELEMETRY | REPORT | TEXT_EXTRACTION | ASSUMPTION | ENGINEER_OVERRIDE | CATALOG,
    confidence = 0.9,
    assumption = None | Assumption(basis="regional typical", bias="conservative_upward", rationale="..."),
    extracted_from = None | "<quote from source document>",
)
```

The confidence hierarchy from §10 is encoded in `Source`, and it is **ordered**: `TELEMETRY > REPORT > TEXT_EXTRACTION > ASSUMPTION`. "The engineer said 8 months" and "the system logged 243 days" are different facts with different weight, and the type system knows it.

### Asymmetric conservatism is a first-class object

§5's principle — err toward margin, not toward the middle — is implemented as an explicit `BiasPolicy` per parameter, not scattered `if` statements:

```
GOR, gassy reservoir     → bias UPWARD    (underestimating gas kills the installation)
Temperature              → bias UPWARD    (thermal derating)
Fluid viscosity          → bias UPWARD    (head degradation)
Productivity index       → bias RANGE     (both directions are dangerous: low = no rate, high = downthrust)
Setting depth            → bias DEEPER, bounded by geometry validation
```

Each biased assumption is recorded with its unbiased value alongside it, so the report can state: *"GOR assumed 800 scf/stb (regional typical 550, biased upward per gassy-reservoir policy)."* The engineer sees the bias, its size, and its reason. Silent conservatism is as bad as silent optimism.

---

## 4. Deterministic engine structure

```
esp_engine/
  units.py          # dimensioned quantities, conversion constants, unit-trap guards
  provenance.py     # Tracked[T], Source, Assumption, BiasPolicy
  models.py         # Case, Expectations, Constraints, Complications, Reference
  pvt.py            # Standing / Vazquez-Beggs / Beggs-Robinson; selectable correlations
  inflow.py         # PI, Vogel, composite IPR; Pwf ← target rate
  intake.py         # PIP solve, free gas fraction, Turpin, natural separation
  gas.py            # gas handling decision, head degradation vs free gas
  tdh.py            # net lift + friction + discharge head
  curves.py         # pump curve eval, affinity scaling, viscosity correction, zone classification
  selection.py      # candidate enumeration, stage count, config generation
  mechanical.py     # shaft HP, thrust load — the post-hoc gate (§3.2 note)
  motor.py          # HP requirement, loading %, cooling velocity, thermal
  cable.py          # voltage drop, ampacity, AWG selection, surface voltage
  string.py         # ESP string assembly, make-up, coupling/adapter compatibility
  constraints.py    # CONSTRAINTS GATE — the single choke point
  trajectory.py     # scenario expansion, drift models
  scoring.py        # per-config trajectory score, ranking, validity boundary, VSD recovery
  pipeline.py       # orchestration — the only module that knows the full order
```

Hard rules for this package:
- **No I/O, no network, no clock, no randomness.** Determinism is the product.
- Every public function is annotated with its unit contract and returns `Tracked` values where a caller could reasonably need provenance.
- Every correlation is selectable via `Config`, defaulting to industry standard, with the choice recorded in the result. Framework §13 lists unresolved methodology choices (thrust zone boundaries: curve DB vs BEP-derived formula; frequency curves: computed vs pre-built). These are `Config` switches, so the open questions are *parameterized rather than prematurely decided*.

### The constraints gate is one function

§3.2's rigidity/timing matrix plus §12's `failed → return to selection` loop are implemented as a single `evaluate(config, context) → GateResult`. Nothing bypasses it. `GateResult` distinguishes:

- `absolute` violation (geometry/physics) → config is dead, discard
- `hard` violation (shaft, thrust, electrical) → **return to selection** with a structured reason the enumerator can act on (e.g. "shaft HP exceeded → try higher shaft rating or fewer stages")
- `soft` violation (stock, couplings) → config survives, carries a flag and a lead-time note

The `return to selection` arrow is a real feedback edge in the enumerator, not a retry loop. Because the enumerator generates *all* configs up front, a hard failure is a filter with an explanation attached — which is strictly better than iterative repair, and it gives the engineer a "here's what we rejected and why" view that no current tool provides.

---

## 5. Trajectory scoring and the validity boundary

This is the product's differentiator, so it gets an explicit design.

**Scenario expansion.** From the `Case`, build a `Trajectory`: a set of `ScenarioPoint`s over the life horizon. Each point carries drifted values for PI, reservoir pressure, water cut, and GOR (§6.1). Three envelopes — `min`, `base`, `max` — bound the uncertainty, and each drifting parameter's drift model is itself provenanced (from decline history in Branch A, from analogs/forecasts in Branch B).

**Per-cell zone classification.** For each config at each point, locate the operating point on the frequency-scaled curve and classify: `BEP` / `OPERATING_RANGE` / `DOWNTHRUST` / `UPTHRUST`, with a signed distance from BEP so severity is quantified, not just categorical. §6.4 gives the zones; distance-from-BEP is what makes them scoreable.

**Score.** A config's score aggregates zone quality over time, weighted by a configurable objective. §13 explicitly leaves the "best configuration" criterion open, so it is a pluggable `ScoringObjective`:

- `max_coverage` — maximize time spent in acceptable zones
- `early_priority` — weight the first N months most heavily
- `min_failure_risk` — penalize zones the empirical layer associates with short run life
- `max_validity_horizon` — maximize time until first hard violation

The default is `max_coverage`; the choice is recorded in the result and exposed in the UI. This is a business decision disguised as a technical one, and it must stay visible.

**Validity boundary.** The headline output. Rather than a boolean "the design works," the engine reports the timestamp at which the design leaves acceptable operation, the reason, and the severity trajectory:

```
Configuration #1 — REDA D1350N, 142 stages, 55 Hz
  Day 1   → operating range, near BEP        (Q/Q_bep = 1.04)
  +2 mo   → operating range                  (Q/Q_bep = 0.94)
  +6 mo   → ⚠ downthrust                     (Q/Q_bep = 0.78)
  +1 yr   → ⚠ downthrust, deep               (Q/Q_bep = 0.61)

  VALIDITY BOUNDARY: ~5.2 months at base case (3.8–7.9 months across envelope)
  LIMITING MECHANISM: water cut rise → PI-driven rate decline → downthrust
  VSD RECOVERY: 48→52 Hz extends acceptable operation to ~14 months
                (recovers 61% of the out-of-range horizon)
```

**VSD recovery.** With a VSD present (§3.2 electrical constraint), re-solve each out-of-range point over the achievable frequency band and report what fraction of the lost horizon frequency can reclaim (§6.4). Without a VSD, this section is omitted rather than shown empty — the absence is itself information.

---

## 6. Empirical layer — §8.2's error classes drive the schema

The framework is unusually clear-eyed here: the model inherits *a different error class* than a human, and the mitigation is structural. So the record is an object with context, never a flat key-value:

```python
EmpiricalObservation(
    id, tenant_id,
    subject       = {pump_model, series, manufacturer},
    context       = {region, field, formation, fluid_type, wc_pct, gor,
                     temp_f, complications[], operating_zone, frequency_hz},
    outcome       = {run_life_days, failure_mode, failure_location,
                     teardown_findings, still_running: bool},
    source        = Source.TELEMETRY | REPORT | TEXT_EXTRACTION | ENGINEER_COMMENT,
    observed_at, entered_by, source_document_ref,
    engineer_commentary = "...",
)
```

Rules are **derived from** observations, never entered directly, and their confidence is **computed**, never asserted:

```python
EmpiricalRule(
    statement            = "D1350N tolerates sustained downthrust to Q/Qbep ≈ 0.75",
    supporting_obs_ids   = [...],
    n_observations       = 3,
    n_distinct_fields    = 1,          # ← systematic-bias detector
    n_distinct_operators = 1,
    confounders_present  = ["low_temperature", "clean_fluid"],   # ← confounding detector
    survivorship_note    = "only pulled wells are in the dataset",
    confidence           = 0.31,       # COMPUTED
    confidence_basis     = "3 obs, 1 field, 2 unresolved confounders → low",
)
```

Confidence is a transparent, auditable function — sample size, source-diversity penalty (few distinct fields → heavy penalty), source-weight by `Source` rank, confounder penalty, and recency. Every rule surfaced in the UI shows its basis on hover. §8.2's example — *"Rule based on 3 cases, all from one field — low confidence"* — is a literal rendering of `confidence_basis`.

**Survivorship** gets explicit treatment: still-running installations are recorded as **right-censored** observations, not omitted. A pump with 15 wells still running at 900 days is powerful evidence, and a naive "average run life of failed pumps" would throw it away and bias every rule pessimistically. Run-life statistics use survival analysis (Kaplan-Meier), not arithmetic means.

**Overlay contract.** The overlay reads the `DesignResult` and emits `Judgment` objects that *reference* facts by ID. It cannot mutate them. A judgment renders as: *"Physics: at +6 months the operating point enters downthrust (fact). Empirical: this model has operated reliably in the left zone under similar conditions — moderate risk (judgment, confidence 0.31, based on 3 observations from 1 field)."*

---

## 7. Data isolation (§9)

Per-tenant isolation is modeled from the first commit because retrofitting it is how data leaks happen.

- Every empirical row carries `tenant_id`, non-nullable, indexed.
- All empirical access goes through a repository layer that **requires** a `TenantContext`. There is no query path that omits it. Enforced by a test that fails if any empirical query is constructed without one.
- Physics (Layer 1) and Catalog (Layer 2) are global and immutable. Empirical (Layer 3) is tenant-scoped. The three-layer split of §7 maps exactly onto the isolation boundary — which is a strong signal the framework's decomposition is right.
- **The open question (shared anonymized layer) is deliberately left open in code**, exposed as a `shared_layer_enabled` tenant flag defaulting to `off`, with a separate `FactoryPreset` table for expert-seeded, open-source-derived rules. This lets you ship the "factory preset" alternative immediately and answer the harder anonymization question later, with real data and real customer conversations, rather than guessing now.

On Postgres this becomes Row-Level Security. The SQLite prototype uses the repository guard; the schema is identical, so the migration is a policy addition, not a rewrite.

---

## 8. Agentic layer — narrow, bounded, testable

Three separate agents with tight contracts. None of them can write to the engine's inputs without passing through validation.

### Intake agent (§10 mode 3)
`unstructured input → Case`. Structured extraction against the `Case` schema. Rules:
- Every extracted field must carry `extracted_from`: the verbatim source span. No span, no value.
- Extraction never fills a `HARD_STOP` field by inference. §5.1's hard stops (target rate, casing ID + deviation survey) either exist or the case is blocked with a specific question for the customer. Guessing the anchor parameter is the one unrecoverable failure mode.
- Assumption filling is a **separate pass** from extraction, so "what the customer said" and "what we assumed" can never blur.
- The output is validated against the schema; anything failing validation becomes a clarifying question, not a silent default.

### Assumption agent (§5.2)
`incomplete Case + regional priors → filled Case`. Each fill emits an `Assumption` with basis, bias direction, and rationale. Priors come from the tenant's empirical layer where available, from `FactoryPreset` otherwise. Ordering matters: fill from the tenant's own field data before falling back to generic regional typicals, and record which was used.

### Narrative agent
`DesignResult + Judgments → prose`. Grounding is enforced mechanically: the agent receives values as referenced IDs and its output is checked for numbers not present in the result set. A numeric hallucination fails the response and triggers a regeneration, rather than reaching an engineer. This check is cheap and catches the failure mode that would destroy trust fastest.

### Model-independence
The framework's §1 requires reproducibility to be model-independent. So: the LLM boundary is an interface with a swappable implementation, every agent call is logged with model ID / prompt version / raw response, and **a case can be replayed with the engine alone from the frozen `Case`**. Regulatory-grade auditability comes free from the layer separation. Prompt versions are pinned and versioned like migrations.

---

## 9. Progressive trust delegation (§11)

Levels 1–3 as specified, with the crucial detail that progression is driven by **accumulated agreement statistics**, not elapsed time.

```python
AgreementRecord(case_id, tenant_id, engineer_id,
                system_recommendation, engineer_final_decision,
                agreed: bool, override_reason, delta_magnitude, reviewed_at)
```

Agreement is tracked **segmented** — by branch (A vs B), by complication class (gassy / viscous / high-temp / clean), and by confidence band. This matters: a system that is 96% agreed on Branch A replacements in clean wells and 55% agreed on Branch B gassy conversions should be trusted at Level 3 for the former and Level 1 for the latter. A single global trust number would be both wrong and dangerous. So `TrustLevel` is per-segment, and routing is per-case.

Every override is a labeled training signal — the highest-value data the system will ever receive. Overrides are captured with a required structured reason, because an unexplained override teaches nothing.

---

## 10. Backend and API

**Stack:** FastAPI · Pydantic v2 · SQLAlchemy 2.0 · SQLite (Postgres-ready) · NumPy/SciPy · pytest

Pydantic v2 does triple duty here: the domain model, the API contract, and the LLM structured-extraction schema are **the same definitions**. One source of truth for what an ESP case is means the intake agent literally cannot produce a shape the engine rejects.

```
POST   /api/cases                    create case (structured or raw text)
POST   /api/cases/{id}/intake        run intake agent on attached raw input
GET    /api/cases/{id}               case with all provenance
PATCH  /api/cases/{id}               engineer override (records AgreementRecord)
POST   /api/cases/{id}/design        run deterministic engine → DesignResult
GET    /api/designs/{id}             full result: facts + judgments, separated
GET    /api/designs/{id}/report      report artifact
POST   /api/designs/{id}/review      accept / override / escalate
GET    /api/catalog/*                pumps, motors, cables, gas handling, seals, casing
GET    /api/catalog/pumps/{id}/curve sampled curve + BEP + thrust boundaries + caveats
GET    /api/empirical/rules          tenant-scoped, with confidence basis
POST   /api/empirical/observations   add observation (from teardown, telemetry, comment)
GET    /api/empirical/survival       tenant-scoped Kaplan-Meier with confidence bands
GET    /api/trust                    per-segment trust levels and agreement stats
```

The two curve/survival routes were added after the frontend was built, because
building it exposed that two of the five views could not be honest without them.
The design-results centerpiece traces the operating point's path *over the pump
curve*; with no curve endpoint the path had no reference to be read against. The
empirical view's survival chart had no source but a fixture, and a fixture
survival curve rendered beside real rules is precisely the kind of laundering
this architecture exists to prevent.

`/api/catalog/pumps/{id}/curve` returns its own `caveats` list rather than leaving
qualification to the client. Every consumer of the endpoint then inherits the
warning that the curve is a parametric estimate, and that the fitted efficiency
polynomial is not constrained to zero at shut-in. A chart is the most persuasive
artifact this product produces; the qualification must travel with the data.

Design runs are persisted with the `Case` hash, engine version, config, and catalog version — so any historical design is exactly reproducible. This is what makes the system defensible when a design is questioned two years later.

---

## 11. Frontend

**Stack:** Vue 3 · Vite · TypeScript · Pinia · Chart.js

Five views, each earning its place:

1. **Intake** — raw input pane beside the extracted case. Each field shows its source; hovering an extracted value highlights the source span it came from. Assumptions are visually distinct from stated values. Hard stops that are missing are blocking and loud.
2. **Case review** — the three input classes as three panels. Every assumption editable; editing records an override.
3. **Design results** — ranked configurations. The pump curve with the operating point's *path over time* traced on it, thrust zones shaded. This single chart is the product: it makes "when will this stop working" immediately legible in a way no table can.
4. **Validity timeline** — horizontal time axis, zone bands, the validity boundary marked, VSD recovery overlaid as a distinct band.
5. **Empirical** — rules with computed confidence and their basis; observation entry; run-life survival curves per pump model.

**Non-negotiable UI rule:** facts and judgments are never styled the same. Computed values are plain and authoritative. Judgments carry a confidence indicator and a distinct treatment. An engineer must be able to tell at a glance which is which, from across a room.

---

## 12. What v0.1 deliberately does not do

Honest scope boundaries, mirroring framework §13:

- **Not field-validated.** The seed catalog is digitized from public datasheets and partly parametric. Real designs require vendor curve data under agreement. This is stated in the UI, not buried in docs.
- **Simplified string assembly.** Compatibility and make-up checks are implemented at the series/OD level; full coupling and adapter matrices need vendor tables.
- **No surveillance half.** The design half is built; surveillance (§13) is the second half of the product and the source of the highest-quality empirical signal. The interfaces it will need (observation ingestion, right-censored run-life records) are already in place so it plugs in rather than bolts on.
- **Trajectory drift models are simple.** Exponential/harmonic decline and linear water-cut rise. Reservoir-simulation-grade forecasting is out of scope; the drift model is an interface so better models drop in.
- **No stock/inventory integration.** The `soft` constraint path exists and is checked; it needs a real inventory feed to be useful.

---

## 13. Build order

| Phase | Content | Why here |
|---|---|---|
| 1 | Units, provenance, domain model, catalog loader | Everything depends on these; getting `Tracked[T]` right early is what makes provenance pervasive instead of retrofitted |
| 2 | PVT, inflow, intake conditions, TDH | The physics floor |
| 3 | Curves, selection, mechanical/motor/cable, constraints gate | First complete design |
| 4 | Trajectory expansion + scoring + validity boundary | The differentiator |
| 5 | API + frontend | First engineer feedback — the highest-value input available |
| 6 | Intake agent + narrative agent | Handles the real-world dirty input of §10 mode 3 |
| 7 | Empirical layer + trust tracking | Compounds only after real usage, so it ships last but is schema-ready from phase 1 |

Phases 1–5 are the v0.1 vertical slice. Phase 6–7 are scaffolded with real schemas so that the moment real cases and real run-life data exist, the learning loop closes.


## Confidentiality perimeters

Every request resolves to a two-level `Perimeter` (org / operator) and each
perimeter has its own physical store under `data/perimeters/`. The flat
`tenant_id` column is gone; rows carry a `perimeter_key` stamp used only as a
misrouting tripwire. The agentic layer is severable with `ESP_AGENTIC_LAYER=off`,
which removes the only network egress from the design path.

See [PERIMETERS.md](PERIMETERS.md) for the model, the rationale against
check-based isolation, and the open items (notably: nothing yet authenticates the
perimeter headers).
