# API live verification

Run date: 2026-08-24 UTC. The server was launched from `backend` with `python api_server.py`, bound to port 8000, and given `llm-api:website` credentials. The environment's `pplx-tool start_server` command was unavailable (`tool_not_allowed`), so the identical command was kept alive through the sandbox's supervised shell instead.

The unabridged curl response bodies and their trailing `HTTP` status lines are retained in [`../verification/`](../verification/):

- `curl_case_create.txt`
- `curl_design_feasible.txt`
- `curl_design_refusal.txt`
- `curl_catalog_pumps.txt`
- `curl_empirical_rules.txt`
- `curl_empirical_observation.txt`
- `curl_design_get.txt`
- `curl_design_report.txt`
- `curl_design_review.txt`
- `curl_trust.txt`

## Actual curl output: create a case

```text
{"id":"5f02ee82-1fa3-41ff-988a-c750896a660f","tenant_id":"live-verification","status":"raw_input_saved","next_action":"POST /api/cases/5f02ee82-1fa3-41ff-988a-c750896a660f/intake"}
HTTP 201
```

## Actual curl output: feasible ranked design

The complete response is 346,405 bytes because it includes all deterministic facts, candidate timelines, design ledger, and provenance. The following values are copied from that exact response:

```json
{
  "id": "31b66eac-00b1-47eb-a902-31a520f34122",
  "case_id": "demo-permian-h12",
  "facts.verdict": "feasible",
  "facts.candidates.length": 3,
  "facts.candidates[0].configuration": {
    "pump_model": "RC2500",
    "stages": 41,
    "frequency_hz": 65.0,
    "setting_depth_md_ft": 9000.0,
    "motor_hp": 60.0,
    "cable_awg": "6 AWG"
  },
  "facts_has_judgments_key": false,
  "judgments_keys": ["empirical", "narrative"],
  "judgments.narrative.status": "refused",
  "reproducibility": {
    "case_hash": "d2f4f4acd38c5979",
    "config_hash": "dbdbe645b72ec379",
    "catalog_version": "f33c3cfeabaa04ae29b57cec9d87b86fad0c6fbd31f253b1c9af97e3fb2e7273",
    "engine_version": "0.1.0"
  }
}
HTTP 200
```

The optional narrative was correctly refused by the agent's numeric guard; the deterministic feasible result and ranked candidates remained successful product output.

## Actual curl output: deterministic refusal

```json
{
  "id": "d19b0aec-0fca-4373-9fac-ef711eae6e8e",
  "facts.verdict": "target_unachievable",
  "facts.candidates.length": 0
}
HTTP 200
```

The exact full response is in `curl_design_refusal.txt` (4,692 bytes), including the deterministic refusal explanation and provenance.

## Actual curl output: catalog and empirical endpoints

```json
{
  "catalog_pumps": {
    "tenant_id": "live-verification",
    "catalog_version": "f33c3cfeabaa04ae29b57cec9d87b86fad0c6fbd31f253b1c9af97e3fb2e7273",
    "item_count": 13,
    "first_model": "AN550"
  },
  "empirical_rules": {"tenant_id": "live-verification", "rules": []},
  "empirical_observation": {
    "observation_id": "41ac7b80-7039-46af-bba2-850fe4f8b7a0",
    "tenant_id": "live-verification",
    "pump_model": "RC2500",
    "still_running": true
  }
}
HTTP catalog=200; rules=200; observation=201
```

Every catalog route was also live-checked with `X-Tenant-Id: live-verification`: root (`200`), pumps (`200`, 13 items), motors (`200`, 14), cables (`200`, 8), gas-handling (`200`, 8), seals (`200`, 4), and casing (`200`, 12). The exact responses are retained as `curl_catalog_*.txt`; the compact actual status/count output is `../verification/catalog_all_summary.json`.

## Additional live routes

```json
{
  "GET /api/designs/31b66eac-00b1-47eb-a902-31a520f34122": {"http": 200, "verdict": "feasible", "separate_judgments": true},
  "GET /api/designs/31b66eac-00b1-47eb-a902-31a520f34122/report": {"http": 200, "report_prefix": "# ESP Design Report"},
  "POST /api/designs/31b66eac-00b1-47eb-a902-31a520f34122/review": {"http": 200, "action": "accept", "agreed": true},
  "GET /api/trust": {"http": 200, "trust_level": "L1", "agreement_count": 1}
}
```
