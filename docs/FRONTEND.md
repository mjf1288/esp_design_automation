# ESP Design Automation — Vue Frontend

## Delivery

The Vue 3 + Vite + TypeScript frontend is in `frontend/`. `npm run build` writes the deployable static bundle to `frontend/dist/`.

The application uses Pinia only for runtime state. It does not use `localStorage`, `sessionStorage`, IndexedDB, or cookies.

`src/stores.ts` defines:

- `API` using the required `__PORT_8000__` placeholder fallback;
- case/design/rule response normalization for the API's fact/judgment envelopes;
- live catalog-curve and tenant-scoped survival loading;
- the local-port schema-aligned fixture fallback; and
- the auditable observation POST payload.

In a deployed bundle, an unreadable API response becomes an explicit service-error state. When the source placeholder resolves to `http://localhost:8000`, an unavailable local port instead shows an unmistakable fixture preview; fixture mode never substitutes a catalog curve or a survival curve.

## Component structure

```text
src/
  App.vue                         application shell, navigation, themes, loading/error state
  stores.ts                       Pinia API/runtime state
  lib/
    types.ts                      contract-shaped TypeScript types
    fixture.ts                    development-only schema-aligned fixture
  views/
    IntakeView.vue                raw request + provenance-traced extraction
    CaseReviewView.vue            expectations / constraints / complications review
    ResultsView.vue               ranked configurations and operating-path centerpiece
    TimelineView.vue              validity horizon and checkpoint table
    EmpiricalView.vue             rules, observation entry, survival evidence
  components/
    PumpCurve.vue                 live catalog head curve + deterministic operating path
    TimelineChart.vue             time bands, boundary, and VSD overlay
    SurvivalChart.vue             live Kaplan–Meier confidence band and censor marks
```

The product mark is an inline geometric SVG in `App.vue`; `public/favicon.svg` is the corresponding simplified favicon.

## Fact, judgment, assumption, and hard-stop system

The visual grammar is intentionally structural rather than cosmetic:

| Information type | Presentation |
| --- | --- |
| Deterministic computed fact | Plain slate surface, high-contrast text, tabular JetBrains Mono numeric values, no source tint or advisory icon. |
| LLM / empirical judgment | Cyan-tinted surface, cyan left border, explicit `judgment` source label, and a confidence value/basis. |
| Assumption / catalog qualification | Amber surface and left rule, visible basis and qualification. |
| Missing hard stop | Red blocking panel, `BLOCKED` language, required-data list, and a request action. It is never shown as an optional field. |
| Normal empirical absence | Neutral/informational slate surface with a cyan left rule. It is not styled as a failure. |

All numeric metrics, tables, scores, dates, and reproducibility IDs use JetBrains Mono with tabular numerals. UI prose uses Inter. Dark is the default; the header control switches to the equivalent light theme.

## Five required views

1. **Intake** displays raw request text next to recursively discovered `Tracked` fields. Hovering an extracted row highlights its recorded `extracted_from` span in the raw text. It combines engine `blocking_data_gaps` with structural requirements for target rate, casing sections, and either a deviation survey or explicit vertical-well confirmation.
2. **Case review** separates EXPECTATIONS, CONSTRAINTS, and COMPLICATIONS. Assumption/default rows are editable. Saving changes clones the complete current Case, changes the `Tracked` value to `engineer_override`, and PATCHes `{ case, override_reason, engineer_id }` so the backend records an auditable review event.
3. **Design results** shows candidate configurations, the estimated-catalog warning, an immutable reproducibility strip, fact metrics, VSD recovery, and advisory judgments separately. The engine retains its deterministic rank order for reproducibility, but the presentation uses the named `CANDIDATE_SCORE_RELATIVE_TOLERANCE = 1e-3`: candidates within 0.1% of the top total score are shown as a `#1 (tied)` group with three-decimal scores. This prevents a rank order from implying discrimination the estimated catalog and assumed-input data cannot support. Each non-top candidate names fields that differ from the top configuration (setting depth, motor, cable, gas handling, and seal when supplied), so tied choices can be made on engineering grounds. The centerpiece uses a real catalog curve behind the sampled operating path.
4. **Validity timeline** provides a horizontal month axis with downthrust / operating-range / BEP / upthrust bands, the deterministic validity boundary, and a distinct violet VSD-recovered band. The checkpoint table remains horizontally scrollable on narrow screens rather than squeezing numeric columns.
5. **Empirical** renders rules as judgments with evidence counts, confidence, confidence basis, and bias flags. Observation entry sends required dates, the selected pump/configuration, full case snapshot, and source record. The live survival endpoint is authoritative: when it returns no estimate, its epistemic explanation is rendered as a first-class neutral empty state and no curve is drawn.

## Live chart integration

### Catalog pump curve

For the selected candidate, the store calls:

```text
GET /api/catalog/pumps/{pump_id}/curve?frequency_hz={hz}&stages={n}&samples=80
```

`PumpCurve.vue` plots `points[].head_ft_total` against `points[].q_bpd` as the background head curve and layers the candidate's own sampled operating path (`liquid_rate_bpd`, `head_developed_ft`) over it. The full returned flow scale is preserved; the path is not rescaled to exaggerate a small excursion.

- The thrust shading uses `downthrust_limit_bpd` and `upthrust_limit_bpd` returned by the API; it is not re-derived in the client.
- The BEP line uses returned `bep_q_bpd`.
- The returned `caveats[]` and `data_quality` are rendered inside the chart in a prominent amber qualification block. This keeps the parametric-estimate qualification and the nonphysical near-zero-flow efficiency warning visible beside the chart, rather than hiding them in a tooltip or footnote.
- A white outlined circle labels the first sampled month; a cyan arrow labels the final sampled month and establishes direction. This makes the truthful conclusion—“the point barely moves”—legible without distorting axes.
- The frontend does not plot the catalog efficiency polynomial. In particular, it does not present the known nonphysical near-zero-flow efficiency as valid.

### Empirical survival

The store calls:

```text
GET /api/empirical/survival?pump_model={selected model}&confidence_level=0.95
```

`SurvivalChart.vue` uses returned `KaplanMeierEstimate.points` directly:

- the Kaplan–Meier series is a step plot;
- the Greenwood/log-log lower and upper values form one translucent filled confidence band, not two additional lines;
- amber `×` symbols identify points with `n_censored > 0`;
- at censor-only times the step remains level, while it drops only where `n_events > 0`.

For `estimate: null`, `EmpiricalView.vue` displays the endpoint's `explanation` unchanged. It deliberately draws no curve and never falls back to fixture values in live mode.

## API integration notes

- The frontend accepts both `{"items":[]}` and documented `{"rules":[]}` response envelopes for empirical rules.
- The API returns deterministic results in `facts` and empirical overlays in `judgments.empirical`; normalization retains this separation, and the renderer never uses a judgment as a fact metric.
- Candidate equipment IDs are optional in historical configuration snapshots. The candidate picker compares any supplied `motor_*`, `cable_*`, `gas_handling_*`, and `seal_id` fields with the top candidate; it falls back to the actual differing setting depth where that is the only recorded difference.
- Case editing requires a complete valid Case PATCH, not a field-level patch. The store follows that contract.
- No response-shape mismatch was observed against `catalog_pump_curve`, `empirical_survival`, and `test_api_curve_and_survival.py`.

## Verification

Build passed:

```bash
cd frontend
npm run build
```

Final live screenshots are retained in `frontend/qa/`:

- `results-live-1280-dark.png`, `results-live-375-dark.png`
- `empirical-live-1280-dark.png`, `empirical-live-375-dark.png`
- `empirical-populated-1280-dark.png`, `empirical-populated-375-dark.png`

The first pair verifies the real catalog curve, API-provided thrust boundaries, prominent wrapped qualifications, tied-candidate presentation, and readable first/final-month operating markers. The empty empirical pair verifies the neutral informational absence state and the desktop reflow that removes the former large empty left column.

For populated-survival visual QA only, `backend/qa_seed_survival.py` seeds ten mixed records (five failures and five right-censored records) into the isolated `qa-survival` tenant in a copied scratch database. `qa-survival-populated.mjs` asserts that censor-only points leave survival unchanged and failure points lower it; the resulting desktop and 375 px screenshots verify distinct amber censor symbols and a filled—not line-like—confidence band. Scratch data is not a frontend fixture and is not committed as application data.

The retained Playwright scripts (`qa-live.mjs` and `qa-survival-populated.mjs`) checked `scrollWidth === clientWidth` at 375 px for the views they exercised. Visual inspection found no text overflow, mid-word truncation, unreadable chart labels, chart-annotation overlap, low-contrast empty state, or squished mobile layout in the final captures.
