# ESP Design Automation API

Base URL: `http://localhost:8000`  
Format: JSON  
Units: **US oilfield units only** (`bpd`, `psi`, `ft`, `in`, `F`, `Hz`, `scf/stb`, and fractions in `[0,1]`).

Every request is tenant-scoped. Supply `X-Tenant-Id`; an omitted header uses the isolated `demo` tenant. A resource that belongs to another tenant is reported as `404`, rather than revealing whether it exists. CORS accepts all origins for the separately hosted frontend.

The server seeds these `demo` cases at startup: `demo-permian-h12`, `demo-intake-blocked`, and `demo-target-unachievable`. Seeded assumptions use the engine's `Tracked` plus `Assumption` representation and disclose their basis/rationale.

## Shared conventions

### Provenance-carrying input

A numeric input is a `Tracked` object, never an unlabelled scalar:

```json
{
  "value": 2000.0,
  "unit": "bpd",
  "source": "customer_stated",
  "note": "customer design target"
}
```

A deliberate assumption is explicit:

```json
{
  "value": 35000.0,
  "unit": "ppm",
  "source": "assumption",
  "assumption": {
    "basis": "Permian produced-water regional typical",
    "rationale": "No water analysis was supplied.",
    "bias": "neutral",
    "unbiased_value": null,
    "scenario_swept": false
  }
}
```

### Design presentation boundary

A design response **always** has separate top-level `facts` and `judgments` objects. `facts` is the deterministic `DesignResult` with no judgment list; `judgments.empirical` and `judgments.narrative` are advisory overlays. Frontends must not blend them.

```json
{
  "id": "a-design-id",
  "facts": {"verdict": "feasible", "candidates": []},
  "judgments": {"empirical": [], "narrative": {"status": "ok"}},
  "reproducibility": {
    "case_hash": "...",
    "config_hash": "...",
    "catalog_version": "...",
    "engine_version": "..."
  }
}
```

The persisted design also retains immutable snapshots of the case, engine configuration, and catalog. The four values above identify the historical deterministic run.

## Cases

### `POST /api/cases`

Create either a raw request for agent intake or an already validated engine `Case`. The API owns the case ID and tenant; submitted metadata cannot write across tenants.

**Raw-request example**

```bash
curl -sS -X POST http://localhost:8000/api/cases \
  -H 'Content-Type: application/json' -H 'X-Tenant-Id: acme-field' \
  -d '{"raw_request":"Please review an ESP for our 7 in casing well."}'
```

```json
{
  "id": "b159cfa5-68b0-4d9c-a39b-cb6375f906de",
  "tenant_id": "acme-field",
  "status": "raw_input_saved",
  "next_action": "POST /api/cases/b159cfa5-68b0-4d9c-a39b-cb6375f906de/intake"
}
```

**Structured-case example**

The object below is a valid small case shape; production callers normally preserve the full field-survey and PVT evidence in the same `Case` model.

```bash
curl -sS -X POST http://localhost:8000/api/cases \
  -H 'Content-Type: application/json' -H 'X-Tenant-Id: acme-field' \
  -d @case.json
```

`case.json` includes (among the required `Case` fields) a tracked target:

```json
{
  "metadata": {"case_id": "acme-h12", "well_name": "Acme H-12"},
  "task_type": "new_well",
  "expectations": {"target_rate_bpd": {"value": 2000.0, "unit": "bpd", "source": "customer_stated"}},
  "geometry": {"casing_sections": [{"od_in": 7.0, "id_in": 6.276, "drift_id_in": 6.246, "top_md_ft": 0.0, "bottom_md_ft": 9500.0}], "deviation_survey": [{"md_ft": 0.0, "tvd_ft": 0.0, "inclination_deg": 0.0, "dogleg_severity_deg_per_100ft": 0.0}], "perforation_top_md_ft": {"value": 9300.0, "unit": "ft", "source": "measurement"}, "total_depth_md_ft": {"value": 9800.0, "unit": "ft", "source": "measurement"}, "tubing_id_in": {"value": 2.441, "unit": "in", "source": "measurement"}}
}
```

A successful structured response is `201` and includes `status: "created"` and the tenant-normalized `case`. Schema-invalid input is `422`.

### `POST /api/cases/{id}/intake`

Run agentic extraction on a case created with `raw_request`. Optional body fields are `raw_request`, `source_documents`, and `input_source` (an engine `Source`, normally `text_extraction`). Extraction can return a valid case, a reviewable incomplete case, or explicit issues; it does not calculate a pump design.

```bash
curl -sS -X POST http://localhost:8000/api/cases/b159cfa5-68b0-4d9c-a39b-cb6375f906de/intake \
  -H 'Content-Type: application/json' -H 'X-Tenant-Id: acme-field' \
  -d '{"input_source":"text_extraction"}'
```

```json
{
  "id": "b159cfa5-68b0-4d9c-a39b-cb6375f906de",
  "intake": {
    "case": null,
    "blocking_data_requests": ["Provide target production rate in bpd."],
    "attempts": 1
  },
  "case": null
}
```

### `GET /api/cases/{id}`

Read the tenant-owned raw request, latest structured case, and intake record.

```bash
curl -sS http://localhost:8000/api/cases/demo-permian-h12 -H 'X-Tenant-Id: demo'
```

```json
{
  "id": "demo-permian-h12",
  "tenant_id": "demo",
  "case": {"metadata": {"case_id": "demo-permian-h12", "well_name": "Permian H-12"}, "task_type": "new_well"},
  "raw_request": "Seeded demonstration case; inputs marked with source and assumptions.",
  "intake": null,
  "created_at": "2026-08-24T01:53:00+00:00",
  "updated_at": "2026-08-24T01:53:00+00:00"
}
```

### `PATCH /api/cases/{id}`

Replace a structured case after engineering review. Send either the `Case` itself or `{ "case": <Case>, "override_reason": "...", "engineer_id": "..." }`. An override reason is recorded as an auditable review event.

```bash
curl -sS -X PATCH http://localhost:8000/api/cases/acme-h12 \
  -H 'Content-Type: application/json' -H 'X-Tenant-Id: acme-field' \
  -d '{"case": {"metadata": {"well_name": "Acme H-12"}, "task_type": "new_well"}, "override_reason":"Updated from the final deviation survey.", "engineer_id":"eng-41"}'
```

```json
{
  "id": "acme-h12",
  "status": "updated",
  "case": {"metadata": {"case_id": "acme-h12", "tenant_id": "acme-field"}},
  "agreement_record_id": "06e6b076-a1c8-4fe3-b3f9-4063c1d65ca2"
}
```

The body must still be a complete valid `Case`; the short `case` fragment is illustrative of the envelope, not a replacement for required engineering data.

## Deterministic design and review

### `POST /api/cases/{id}/design`

Run the deterministic engine, apply tenant-scoped empirical overlays, and request a guarded narrative. No pump or hydraulic calculation is performed by the route itself.

```bash
curl -sS -X POST http://localhost:8000/api/cases/demo-permian-h12/design \
  -H 'X-Tenant-Id: demo'
```

```json
{
  "id": "f3d2dca3-11f7-4da8-90a2-1561cb2d876e",
  "case_id": "demo-permian-h12",
  "facts": {
    "verdict": "feasible",
    "candidates": [{"rank": 1, "configuration": {"pump_model": "...", "stages": 0, "frequency_hz": 0.0}}]
  },
  "judgments": {"empirical": [], "narrative": {"status": "ok", "attempts": 1}},
  "reproducibility": {"case_hash": "...", "config_hash": "...", "catalog_version": "...", "engine_version": "..."}
}
```

`blocked_missing_data`, `target_unachievable`, and `no_viable_configuration` are successful `200` product responses, not HTTP errors. For example:

```bash
curl -sS -X POST http://localhost:8000/api/cases/demo-intake-blocked/design -H 'X-Tenant-Id: demo'
```

```json
{
  "facts": {"verdict": "blocked_missing_data", "candidates": []},
  "judgments": {"empirical": [], "narrative": {"status": "ok"}}
}
```

### `GET /api/designs/{id}`

Read a persisted historical run. Facts and judgments remain separate top-level keys.

```bash
curl -sS http://localhost:8000/api/designs/f3d2dca3-11f7-4da8-90a2-1561cb2d876e -H 'X-Tenant-Id: demo'
```

```json
{
  "id": "f3d2dca3-11f7-4da8-90a2-1561cb2d876e",
  "facts": {"verdict": "feasible", "candidates": []},
  "judgments": {"empirical": [], "narrative": {"status": "ok"}},
  "reproducibility": {"case_hash": "...", "config_hash": "...", "catalog_version": "...", "engine_version": "..."}
}
```

### `GET /api/designs/{id}/report`

Return a render-ready report with the same separated structures plus deterministic markdown.

```bash
curl -sS http://localhost:8000/api/designs/f3d2dca3-11f7-4da8-90a2-1561cb2d876e/report -H 'X-Tenant-Id: demo'
```

```json
{
  "id": "f3d2dca3-11f7-4da8-90a2-1561cb2d876e",
  "facts": {"verdict": "feasible"},
  "judgments": {"empirical": [], "narrative": {"status": "ok"}},
  "report_markdown": "# ESP Design Report\n\nDesign ID: f3d2dca3-11f7-4da8-90a2-1561cb2d876e\nVerdict: feasible"
}
```

### `POST /api/designs/{id}/review`

Record an engineer disposition. `action` is `accept`, `override`, or `escalate`; `override` requires `override_reason`.

```bash
curl -sS -X POST http://localhost:8000/api/designs/f3d2dca3-11f7-4da8-90a2-1561cb2d876e/review \
  -H 'Content-Type: application/json' -H 'X-Tenant-Id: demo' \
  -d '{"action":"accept","engineer_id":"eng-41","final_decision":{"selected_rank":1}}'
```

```json
{
  "id": "71b34c28-e02d-48c7-b8b9-a913f42e4c4b",
  "design_id": "f3d2dca3-11f7-4da8-90a2-1561cb2d876e",
  "action": "accept",
  "agreed": true,
  "created_at": "2026-08-24T01:55:00+00:00"
}
```

## Catalog

All catalog routes return versioned globally curated equipment reference data and echo the request tenant. They are read-only.

### `GET /api/catalog`

```bash
curl -sS http://localhost:8000/api/catalog -H 'X-Tenant-Id: demo'
```

```json
{"tenant_id":"demo","version":"f33c3cfe...","has_estimated_data":true,"counts":{"pumps":13,"motors":14,"cables":8,"gas_handling":8,"seals":4,"casing":12}}
```

### `GET /api/catalog/pumps`

```bash
curl -sS http://localhost:8000/api/catalog/pumps -H 'X-Tenant-Id: demo'
```

```json
{"tenant_id":"demo","catalog_version":"f33c3cfe...","items":[{"model":"..."}]}
```

### `GET /api/catalog/motors`

```bash
curl -sS http://localhost:8000/api/catalog/motors -H 'X-Tenant-Id: demo'
```

```json
{"tenant_id":"demo","catalog_version":"f33c3cfe...","items":[{"model":"..."}]}
```

### `GET /api/catalog/cables`

```bash
curl -sS http://localhost:8000/api/catalog/cables -H 'X-Tenant-Id: demo'
```

```json
{"tenant_id":"demo","catalog_version":"f33c3cfe...","items":[{"model":"..."}]}
```

### `GET /api/catalog/gas-handling`

```bash
curl -sS http://localhost:8000/api/catalog/gas-handling -H 'X-Tenant-Id: demo'
```

```json
{"tenant_id":"demo","catalog_version":"f33c3cfe...","items":[{"model":"..."}]}
```

### `GET /api/catalog/seals`

```bash
curl -sS http://localhost:8000/api/catalog/seals -H 'X-Tenant-Id: demo'
```

```json
{"tenant_id":"demo","catalog_version":"f33c3cfe...","items":[{"model":"..."}]}
```

### `GET /api/catalog/casing`

```bash
curl -sS http://localhost:8000/api/catalog/casing -H 'X-Tenant-Id: demo'
```

```json
{"tenant_id":"demo","catalog_version":"f33c3cfe...","items":[{"size_label":"..."}]}
```

## Empirical layer

### `GET /api/empirical/rules`

List only derived rules belonging to the requesting tenant. A fresh tenant returns an empty array.

```bash
curl -sS http://localhost:8000/api/empirical/rules -H 'X-Tenant-Id: acme-field'
```

```json
{"tenant_id":"acme-field","rules":[]}
```

### `POST /api/empirical/observations`

Add an auditable field outcome. The server attaches the tenant from the header; a body cannot select a tenant. Dates drive run life, and a still-running record is right-censored.

```bash
curl -sS -X POST http://localhost:8000/api/empirical/observations \
  -H 'Content-Type: application/json' -H 'X-Tenant-Id: acme-field' \
  -d '{"external_case_id":"field-101","pump_model":"ESP-TEST","case_inputs_snapshot":{"units":"US field"},"selected_configuration":{"pump_model":"ESP-TEST"},"install_date":"2024-01-01","still_running":true,"outcome_observed_date":"2024-06-01","is_failure":false,"operating_conditions":{},"source":{"value":"installation report","unit":null,"source":"customer_stated","note":"field record"}}'
```

```json
{
  "observation_id": "c3f2d09e-75aa-4e16-81cc-7d0ddb8b5ed0",
  "tenant_id": "acme-field",
  "pump_model": "ESP-TEST",
  "still_running": true,
  "is_failure": false
}
```

## Trust

### `GET /api/trust`

Return tenant-local review agreement segments. The API reports `L1` by default, promotes to `L2` at at least 20 reviews and 80% agreement, and to `L3` at at least 50 reviews and 95% agreement.

```bash
curl -sS http://localhost:8000/api/trust -H 'X-Tenant-Id: demo'
```

```json
{
  "tenant_id": "demo",
  "segments": [{"branch":"B_new","complication_segment":"standard","confidence_band":"low","trust_level":"L1","agreement_count":1,"accepted_count":1,"agreement_rate":1.0}],
  "policy": "L2 requires >=20 reviews and >=80% agreement; L3 requires >=50 reviews and >=95% agreement."
}
```

## Error semantics

- `404`: a requested tenant-owned case or design is not present for this tenant.
- `409`: trying to design a raw-only case before intake creates a valid case, or attempting an already-used case ID.
- `422`: schema/unit/provenance validation failure, blank tenant header, or invalid review action.
- `200`: deterministic design success **and** deterministic refusal verdicts. Refusals are correct product outcomes and include the engine explanation in `facts`.

## GET /api/catalog/pumps/{pump_id}/curve

Sampled performance curve for one pump at one frequency and stage count.

| Query param | Default | Notes |
| --- | --- | --- |
| `frequency_hz` | `60.0` | Scaled with the engine's configured method, not a local default |
| `stages` | `1` | Must be >= 1 |
| `samples` | `60` | 2–400 |

Returns `pump_id, pump_model, manufacturer, catalog_version, frequency_hz,
reference_frequency_hz, stages, bep_q_bpd, q_min_bpd, q_max_bpd,
recommended_min_bpd, recommended_max_bpd, downthrust_limit_bpd,
upthrust_limit_bpd, data_quality, is_estimated, frequency_curve_method,
caveats, points[]`.

Each point: `q_bpd, head_ft_per_stage, head_ft_total, efficiency_frac,
bhp_per_stage, bhp_total`.

`404` unknown pump. `422` non-positive or out-of-range frequency, `stages < 1`,
`samples` outside 2–400.

**Clients must render `caveats`.** They are returned by the API rather than left
to the client so that every consumer inherits them. Currently they state that the
curve is a parametric estimate rather than a digitized vendor curve, and that the
fitted efficiency polynomial is not constrained to zero at shut-in — it reports
about 0.327 efficiency at zero flow, which is nonphysical. The domain is not
trimmed to hide this, because trimming would imply the fit is physical there.

## GET /api/empirical/survival

Tenant-scoped Kaplan-Meier run-life estimate with Greenwood log-log confidence
bands, computed from that tenant's own observations.

| Query param | Default | Notes |
| --- | --- | --- |
| `pump_model` | none | Optional filter |
| `confidence_level` | `0.95` | Strictly between 0 and 1, else `422` |

With no observations, returns `n_observations: 0`, `estimate: null`, and an
`explanation` stating that the absence of observations is not evidence of long
run life. No synthetic curve is ever returned: an invented survival band rendered
beside real ones would be the most misleading output this product could produce.
