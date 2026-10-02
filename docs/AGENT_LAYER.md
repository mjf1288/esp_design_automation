# ESP agent layer

## Purpose and hard boundary

`backend/esp_agents/` is the agentic layer required by Framework §1.  It imports
`esp_engine` models and calls an `EngineRunner`, but `esp_engine/` has no import
of this package, no LLM client, and no network dependency.

The boundary is structural:

```text
raw request -> IntakeAgent -> validated Case -> deterministic EngineRunner
            -> immutable DesignResult -> NarrativeAgent -> review/release gate
```

The orchestrator preserves the exact `DesignResult` instance returned by the
engine.  It never edits it, copies a recommendation into it, or asks an LLM to
adjust a fact.  Agent output is either:

- a `Case` with provenance before the engine runs, or
- a narrative overlay after the engine runs, whose fact prose and judgments are
  separate schema fields.

The only route with no engine call is malformed LLM output that cannot form a
validated `Case`.  If there is a valid Case—even one blocked by missing
hard-stop data—the orchestrator calls the engine.  The engine then returns the
authoritative blocked result.

## Package map

| Module | Responsibility |
|---|---|
| `llm.py` | Small mockable `StructuredLLM` protocol and `OpenAIResponsesLLM` adapter. |
| `contracts.py` | Pydantic schemas for extraction evidence, intake results, narrative sections, and numeric violations. |
| `intake.py` | Evidence validation, hard-stop refusal, provenance enforcement, assumption policy enforcement, Decision Gate 1 output. |
| `trust.py` | Enforced L1/L2/L3 review/release routing. |
| `narrative.py` | Structured engineer narrative and numeric-hallucination validator. |
| `orchestrator.py` | Thin deterministic-first sequence. |

## LLM boundary and structured output

Both agents call only the `StructuredLLM.generate_json(...)` protocol.  Production
uses `OpenAIResponsesLLM`, which calls the OpenAI **Responses API** with a
Pydantic-generated JSON schema and `strict: true`.  It creates `OpenAI()` without
a hard-coded key.  The runtime must inject the documented `llm-api:website`
credential preset when running a server that invokes the adapter.

Tests use deterministic fake implementations of the protocol.  A malformed JSON
response, a schema mismatch, or a missing structured output is normal operational
input: each agent retries up to its configured attempt limit and returns a
reviewable refusal rather than a partial, invented result.

### Intake prompt and schema

The intake prompt directs the model to:

1. extract only values supported by exact source text;
2. use the caller-selected `Source` for supplied evidence;
3. classify each supplied value as `EXPECTATIONS`, `CONSTRAINTS`,
   `COMPLICATIONS`, or `REFERENCE`;
4. apply `absolute`, `hard`, or `soft` rigidity where the value is a constraint;
5. never infer a target production rate, casing program, or deviation survey;
6. use US field units; and
7. retain assumptions only with basis, rationale, bias, and neutral value.

The structured response is `IntakeLLMResponse`:

```text
task_type
case                         # partial Case body; metadata is caller-owned
fields[]:
  path
  input_class
  rigidity
  confidence
  source_span
  unit
  classification_rationale
could_not_determine[]
decision_gate_rationale
```

`IntakeAgent` constructs `CaseMetadata` itself, including the raw request and
tenant/case IDs.  The LLM does not get to create identity or tenancy fields.

`input_source` is explicit on `IntakeAgent.run`:

| Raw input type | Typical `input_source` |
|---|---|
| Email, call transcript, free-form pasted text | `Source.TEXT_EXTRACTION` |
| Partially completed customer form/data sheet | `Source.CUSTOMER_STATED` |
| Vendor or teardown report | `Source.REPORT` |
| Pasted measured well-test record | `Source.MEASUREMENT` |
| Direct monitoring export handled by an upstream adapter | `Source.TELEMETRY` |

For every source type, the agent verifies the cited source span occurs exactly in
the supplied raw request.  For `TEXT_EXTRACTION` this is also required directly
by `Tracked`.  A value cannot silently claim a more authoritative `Source` than
the caller chose for this intake.

### Intake validation and provenance

The agent validates all of the following before it returns a `Case`:

- every explicit draft value has exactly one evidence/classification record;
- each cited source span occurs verbatim in the request;
- an evidence record's confidence, unit, and span match the corresponding
  `Tracked` value;
- geometry is always an `absolute` constraint; electrical and mechanical
  constraints are `hard`; availability constraints are `soft`;
- mapped engineering fields use expected US field units, such as `bpd`, `psi`,
  `ft`, `in`, `Hz`, `F`, and `scf/stb`;
- supplied values have the caller-selected `Source` and a traceable span;
- assumptions use `Source.ASSUMPTION` and contain a complete immutable
  `Assumption`; and
- engine defaults are converted into visible `Source.ASSUMPTION` review items
  rather than being presented as facts.

The current binding `Case` model contains several structural geometry values
(`CasingSection` and `DeviationSurveyPoint`) as bare scalar values rather than
`Tracked` values.  Their source, class, rigidity, confidence, unit, and verbatim
span therefore live in `IntakeResult.fields`.  This is an engine-schema
limitation; the agent does not modify `esp_engine` to change that contract.

### Framework §5.1 hard stops

The agent never accepts an assumed or default target rate.  It also never
manufactures casing geometry or a deviation survey.  Missing data is preserved
as a non-calculable `Case` and returned in both:

- `blocking_data_requests`, with a customer-ready request; and
- `could_not_determine`.

This lets the deterministic engine issue its own canonical
`BLOCKED_MISSING_DATA` result.  Refusal is the intended behavior.

### Asymmetric conservatism

The validator enforces the existing engine `BiasDirection` policy for assumptions:

| Field | Required behavior |
|---|---|
| `fluid.gor_scf_stb` | `UPWARD`; assumed value cannot be below `unbiased_value`. |
| `reservoir.bht_f` | `UPWARD`; neutral value must be retained. |
| `fluid.oil_viscosity_cp` | `UPWARD`; neutral value must be retained. |
| `expectations.setting_depth_md_ft` | `DEEPER`; must still be checked by engine geometry logic. |
| `fluid.water_cut_frac` | `RANGE` and `scenario_swept=true`. |
| `reservoir.productivity_index_bpd_psi` | `RANGE` and `scenario_swept=true`. |

An incomplete or non-conservative assumption is rejected as an intake issue.  No
assumption changes a deterministic fact: it is a provenanced input to the engine.

### Decision Gate 1

`Case.branch` is authoritative.  It deterministically selects:

- **Branch A** only when an ESP replacement/redesign has a usable prior pump
  plus performance test point; or
- **Branch B** for a new/conversion/post-stimulation task and for a nominal
  replacement without usable history.

`IntakeResult` carries the branch and `Case.branch_rationale`.  The LLM's
decision-gate rationale is retained in its validated extraction response path,
but it never overrides the Case property.

## Narrative prompt, schema, and numeric guard

The narrative prompt supplies two separate JSON blocks:

1. the complete deterministic `DesignResult` **without** `judgments`; and
2. the result's separate judgment objects.

It requires engineer-readable explanation of configuration, tradeoffs, validity
boundary, behavior past the boundary, and watch items.  It forbids calculations,
unit conversion, thresholds, counts, and any number not supported by the
deterministic result.

The response schema is `NarrativeLLMResponse`:

```text
fact_summary
configuration_facts[]
validity_boundary_facts[]
watch_facts[]
judgments[]:
  statement
  references_facts[]
  confidence
```

The fact fields and `judgments` field are structurally distinct so the UI can
style authoritative engine facts differently from agent interpretation.  The
narrative cannot update `DesignResult.judgments` or any fact.

### Guard algorithm

After Pydantic schema validation, `validate_narrative_numbers`:

1. recursively collects every finite numeric value in the complete
   `DesignResult`;
2. also collects numeric tokens in result strings, such as a configuration ID
   that legitimately appears in text;
3. extracts every digit-based numeric token from every narrative fact and
   judgment statement (signed numbers, decimals, exponent notation, and
   comma-separated thousands are supported);
4. matches each token against the result inventory; and
5. returns a `NumericViolation` for every unmatched token.

The default tolerance is:

\[
\lvert x - y \rvert \leq \max(0.01,\ 0.005 \times \max(\lvert x\rvert,\lvert y\rvert,1))
\]

This permits ordinary display rounding but not a new engineering value.  A token
with `%` may additionally match a stored fraction multiplied by 100, solely to
allow display of an existing fraction as a percent.  No other conversion,
calculation, or inferred threshold is allowed.

An unsafe narrative is regenerated with the offending tokens named.  If all
attempts fail, the agent returns `NarrativeResult(status="refused")` and no
prose.  Tests inject `9999` into a narrative and prove the validator catches it,
prove a second safe attempt succeeds, and prove repeated unsafe output is
refused.

## Progressive trust policy

`TrustPolicy` is executable gating.  It does not promote a system by elapsed
time; the persistence/application layer must select a per-segment `TrustLevel`
from accumulated agreement statistics.  The policy then routes each case:

| Level | Engine/narrative preparation | Release rule |
|---|---|---|
| L1 | May calculate and prepare a narrative. | Engineer review is required for every case. |
| L2 | May calculate and prepare a narrative. | Routine cases can release; low-confidence, assumed, hard-stop-missing, Branch B, or non-actionable cases require review. |
| L3 | May calculate and prepare a narrative. | Only routine cases release autonomously. Branch B, assumptions/derived values, low confidence, missing hard stops, and non-actionable engine results escalate. |

This deliberately uses a conservative definition of routine: an anchored Branch
A case with no derived/assumed load-bearing inputs and sufficient confidence.
Trust controls release and review—not whether the deterministic engine produces
facts.

## Known limitations and underspecified framework points

1. **No regional priors service is specified.** The framework says assumptions
   may use reservoir/region experience but provides no tenant prior schema,
   calibration source, or approval process.  The agent validates a supplied
   assumption; it does not manufacture generic priors.
2. **No source-document parser is included.** PDF/OCR/table parsing and
   telemetry ingestion need upstream adapters that pass `raw_request`,
   `input_source`, and document references.  The agent is a structured
   interpretation layer, not a document extraction engine.
3. **Geometry provenance is split.** As noted above, the binding engine model
   leaves casing/survey scalar fields untracked.  `IntakeResult.fields` retains
   their metadata until a future engine contract can make them `Tracked`.
4. **Input classifications do not have a native field on `Case`.** The existing
   types encode most classification structurally; the intake-specific audit
   records remain alongside the Case in `IntakeResult.fields`.
5. **Trust agreement statistics storage is outside this package.** The framework
   requires segmentation by branch, complication class, and confidence band but
   does not define repository/API tables.  `TrustPolicy` is intentionally pure
   and accepts the selected level.
6. **Numeric grounding checks digits, not spelled-out numbers.** The guard
   blocks engineering numeric tokens and is intentionally not a natural-language
   number parser. Prompts direct the model not to introduce values in any form.
7. **The result may contain values not exposed by `facts_index`.** The guard
   uses a full recursive `DesignResult` walk rather than `facts_index`, avoiding
   a false refusal for a legitimate deterministic number that the convenience
   index omitted.

---

## Numeric hallucination guard: hardening after adversarial review (v2)

The first guard implementation passed its own unit tests while being far weaker
than those tests implied. It is documented here in full, because the failure mode
is instructive and because anyone extending the guard needs to know what it does
and does not promise.

### The measured defect

The original `validate_narrative_numbers` flattened every number appearing
anywhere in a `DesignResult` into one set and accepted a narrative token matching
any member. Measured on a real feasible engine run:

| Metric | Original guard |
| --- | --- |
| Distinct values in whitelist | 2,684 |
| Integers 1–2000 accepted undetected | 1,221 (**61.0%**) |

A guard that accepts three of every five fabricated numbers offers false
confidence, which in an engineering document is worse than no guard.

Two root causes:

1. **No scope.** A narrative about ranked candidate #1 could cite any number from
   the ~200 rejected configurations and 240 evaluated cells it never mentions.
2. **No dimensions.** `41` was a valid stage count, so a fabricated "41 psi" was
   accepted. Every number vouched for every other number.

### The fix

`esp_agents/numeric_guard.py` — `ScopedNumericGuard`:

- **Scoped whitelist.** Only the narrated candidate plus run-level context
  (verdict, scenario points, provenance, assumption ledger, engine warnings).
  Rejected configurations and other candidates are excluded.
- **Diagnostic arrays excluded.** `cells_sampled` alone contributed 144 distinct
  depth values. The narrative has `timeline` for that information.
- **Dimension inference from the schema.** Field names encode units
  (`setting_depth_md_ft`, `pip_psi`, `frequency_hz`), so a number is matched only
  against values of a compatible dimension. No hand-maintained field table.
- **Units read on either side of the number.** Trailing (`65 Hz`) and leading
  (`month 17`, `stage 41`, `AWG 6`) forms are both recognised.
- **Fraction-to-percent is the only permitted transform.** A stored `0.672` may
  be written `67.2%`. No other conversion or arithmetic is allowed.
- **Judgments are checked as strictly as facts.** A judgment may be an opinion; it
  may not contain an invented number, because a reader cannot tell which numbers
  in a document were computed and which were improvised.

### Measured result

False-accept rate over integers 1–2000, same engine run:

| Dimension | Original | Hardened |
| --- | --- | --- |
| Hz | 61.0% | **0.1%** |
| stages | 61.0% | **0.1%** |
| hp | 61.0% | **0.1%** |
| days | 61.0% | **0.0%** |
| psi | 61.0% | **1.3%** |
| ft | 61.0% | **1.4%** |
| bpd | 61.0% | **8.3%** |

`bpd` remains the weakest dimension because a trajectory design legitimately
contains many distinct rate values across scenario points and envelopes. This is
a real residual exposure, not a rounding artifact.

These rates are asserted as test ceilings in
`backend/tests/test_agents_numeric_guard.py::test_false_accept_rate_stays_low`,
so a future change that weakens the guard fails the suite rather than passing
quietly.

### Remaining limitations, stated plainly

- **Spelled-out quantities are not detected.** "five hertz" passes.
- **Semantics are not checked.** "efficiency is 67.2%" and "efficiency falls to
  67.2%" are indistinguishable to the guard. It verifies provenance of the
  number, not the correctness of the claim around it. §11 progressive trust and
  human review remain necessary.
- **A dimensionless number with no adjacent recognised unit** falls back to
  scope-only checking, which is weaker.
- **Legitimate arithmetic is rejected, not accepted.** If the LLM computes a
  difference of two true values, the guard blocks it. This is the intended
  direction of failure: the narrative agent must quote, not compute.

## v3: what probing against a real model actually revealed

The v2 notes above describe the guard as hardened. That was measured against
synthetic text. The first run against a real model refused *every* narrative,
including correct ones. Six defects were responsible, and none of them were model
hallucination. They are recorded here because each represents a class of mistake
that is easy to reintroduce.

### The structural defect: two definitions of scope

`NarrativeAgent._prompt_for` serialized the entire `DesignResult` — roughly 200
rejected configurations and 240 sampled cells — while the guard whitelisted only
the numbers belonging to the single narrated candidate. The model was therefore
*shown* numbers it was *forbidden to quote*. Refusal was not a risk, it was
guaranteed by construction, and no amount of prompt tuning could have fixed it.

The fix is the design rule, not the patch: `narrative_scope(result, candidate_rank=)`
in `numeric_guard.py` is the single source of truth, and both the prompt and the
guard derive from it. A guard whose permitted set is defined separately from the
prompt's visible set will always drift.

### Five unit-handling defects

| Defect | Consequence |
| --- | --- |
| Timeline rows are keyed `month`, matching no `_months` suffix | All 25 real month values fell into the unknown-dimension bucket. "by month 16" was rejected although month 16 is a genuine row. |
| Assumption ledger stores `value` as a **string**, unit in a sibling `unit` field | No assumed input was ever quotable — the thing a narrative most needs to disclose. |
| `" %"` with a space not recognised as a percentage | Fell through to the far weaker dimensionless path. |
| Digit runs inside tokens (`8.0 deg/100ft`, `RC2500`) extracted as quantities | Model names and unit denominators were rejected as fabrications. |
| `24-month` rejected by the trailing-unit regex, then the article "a" read as the amps alias | Ordinary prose was unquotable. |
| Engine-authored prose numbers harvested **without** units | A narrative quoting the engine's own warning `outside the 75%-85% target band` verbatim was rejected. |

Percent tolerance was also wrong in kind: a relative window (0.5% of "40%" is 0.2
points) tiled the axis. Percentages now use an absolute window,
`_PERCENT_ABSOLUTE_TOLERANCE = 0.05`, selected by a `_window(value, dimension)`
policy.

### A test that had pinned a bug in place as a requirement

`test_fabricated_numbers_are_blocked` asserted that "month 17" was fabricated.
Month 17 is a legitimate row in a 24-month timeline; the assertion only passed
because of the timeline-key defect. It now uses month 37, beyond the horizon. A
test written against observed behaviour rather than intended behaviour converts a
bug into a contract, and it is worth checking for this whenever a "hardening"
change makes a test pass.

### Also fixed in the LLM adapter

`PplxStructuredLLM` filtered `result.content` for `TextBlock`, but the SDK
returns `TextBlockEvent`. The isinstance filter therefore yielded `""` and the
guard reported a phantom "malformed output" refusal. It now uses `result.text`.
The adapter authenticates from `PPLX_LLM_API_*`, which is what makes the guard
exercisable from a shell via `backend/scripts/probe_narrative.py`; the previous
OpenAI-style adapter depended on variables only injected into the server, so the
guard could not be probed at all. That is the reason all six defects survived to
this point.

### Measured false-accept rates after the fixes

Over integers 1–2000 unless noted. Baseline for comparison: the original flat,
unit-blind whitelist accepted **61%**.

| Dimension | Rate | | Dimension | Rate |
| --- | --- | --- | --- | --- |
| Hz | 0.1% | | months | 1.2% |
| stages | 0.1% | | psi | 1.3% |
| days | 0.1% | | ft | 1.5% |
| in | 0.1% | | bpd | 8.3% |
| hp | 0.2% | | percent | 27% (over 1–100) |

Ceilings are asserted in `tests/test_agents_numeric_guard.py::test_false_accept_rate_stays_low`
and `test_percentage_false_accept_ceiling`.

### Why the percentage rate is not a guard weakness, and was not tuned away

Percentages measure worst because the engine's own scenario prose legitimately
contains roughly 30 distinct integer percentages between 1 and 100. Any number in
that range is genuinely present in scope. During probing the model wrote "water
cut reaching approximately 58%"; this was traced to the engine's own scenario
point, `water cut 35% -> 58%`. It was a correct quotation, not a hallucination.

The rate could be lowered by narrowing the percentage scope, but only by
discarding real data — which would cause the guard to reject accurate narratives
again, reproducing the v2 failure in a subtler form. The honest fix is
**quantity-name binding**: requiring "water cut 58%" to match a water-cut field
specifically, rather than any percentage anywhere in scope. That is deliberately
not attempted here, and it is the single highest-value next change to this layer.

### Limitations that remain, and should not be silently "fixed"

- Spelled-out quantities ("five hertz") are undetected.
- Semantics are unchecked. The guard verifies a number's provenance, not the
  truth of the claim built around it.
- Dimensionless tokens with no adjacent unit fall back to scope-only checking.
- LLM arithmetic on real values is rejected. This is the intended direction:
  derived numbers must come from the engine.
