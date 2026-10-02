# Empirical knowledge layer

## Purpose and non-negotiable boundary

`backend/esp_empirical/` is Layer 3 of the design system: it retains real installation outcomes and produces **advisory judgments**. It does not import an LLM client and it does not modify `esp_engine/`.

The result boundary is structural:

- The deterministic engine continues to own `DesignResult` and its computed facts.
- `esp_empirical.application.apply_rules_as_overlay()` accepts an `OverlayTarget` that carries fact IDs and returns an immutable `AppliedEmpiricalOverlay` containing only existing `esp_engine.provenance.Judgment` objects.
- No overlay API returns a modified `DesignResult`, a revised operating point, a new ranking, or a changed configuration. An empirical judgment explicitly says that it does not alter deterministic calculation or selection.

This implements framework §7's instruction that empirical knowledge adds judgment but never overwrites calculation.

## Package map

| Module | Responsibility |
|---|---|
| `models.py` | Frozen Pydantic domain models, input validation, US-field-unit names, provenance reuse |
| `orm.py` | SQLAlchemy 2.x schema using portable `String`, `Numeric`, `Date`, `Boolean`, and `JSON` columns |
| `database.py` | Tenant context, guarded sessions, schema lifecycle |
| `repository.py` | The only public tenant-bound observation/rule repository |
| `survival.py` | Kaplan–Meier run-life estimation and Greenwood pointwise confidence bands |
| `rules.py` | Pre-specified early-failure association derivation, refusal policy, bias flags |
| `application.py` | Immutable advisory overlay generation |

The package intentionally does **not** contain a shared-anonymized data layer or a factory-preset rule table. The framework calls that a decision/open question; implementing either would require an explicit data-governance, provenance, and customer-consent design.

## Observation schema

Each `ObservationRow` has a string UUID primary key and a non-null, indexed `tenant_id`. The persisted record includes:

- a complete `case_inputs_snapshot` and `selected_configuration` JSON snapshot;
- ESP identity and context: pump model, manufacturer, series, region, field, formation, fluid;
- `install_date`, `pull_date`, `still_running`, and `outcome_observed_date`;
- computed `run_life_days`, `is_failure`, failure mode/location, teardown findings;
- observed operating conditions, including setting depth in ft, GVF/water cut as fractions, pressure in psi, temperature in °F, rate in bpd, frequency in Hz, zone, and complications;
- record-level source and confidence via the existing `esp_engine.provenance.Tracked` / `Source` primitives; and
- engineer commentary and entry identity.

`run_life_days` is derived from `outcome_observed_date - install_date`; it is not a separately trusted hand-entered number. For a running installation, `outcome_observed_date` is a caller-supplied as-of/censoring date. No hidden wall clock is used. A stopped installation requires a pull date, and a documented failure requires a failure mode.

All numeric names use US field units. Fractions must be in `[0, 1]` rather than percent form; this rejects a `35` supplied where `0.35` is required.

## Tenant isolation guarantee

Tenant isolation is deliberately a capability boundary rather than a repository convention:

1. `EmpiricalStore.for_tenant(TenantContext(...))` is the only public way to open empirical persistence access.
2. It returns `ObservationRepository`, which owns a `TenantScopedSession`. That wrapper exposes purpose-built list/get/save methods and no generic `execute` or `scalars` query API.
3. The SQLAlchemy session is a private subclass with a `do_orm_execute` fail-closed guard. A `SELECT` without `esp_empirical_tenant_id` raises `TenantScopeRequiredError`; a scoped query receives SQLAlchemy's global `with_loader_criteria` tenant predicate for both observations and derived rules. SQLAlchemy documents that this criterion applies globally across entity occurrences, subqueries, joins, and relationship loads: <https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html>.
4. Writes check the row tenant against the immutable context and raise `TenantIsolationError` on mismatch.
5. `RuleDeriver` accepts a tenant-bound repository, and `apply_rules_as_overlay` does likewise. Therefore outcome retrieval, rule derivation, rule persistence, and rule application cannot combine tenant datasets through the supported API.

`test_empirical_store.py` proves that tenant B cannot list or fetch tenant A's observation by UUID, that an attempted raw guarded `SELECT` without a context fails, and that a cross-tenant write is rejected. `test_empirical_rules.py` proves tenant A's derived rule does not appear in a tenant B overlay.

SQLite's guard is application-level isolation, suitable for this development implementation. A PostgreSQL deployment must additionally enable and test database Row-Level Security with a transaction-local tenant identity. Application guards are not a substitute for database access controls when an attacker or a separate service receives direct database credentials.

## Censored run-life analysis

A `still_running` unit is right-censored, not a success and not a failure. `kaplan_meier()` uses it in the at-risk set until its explicit censoring date. The estimate is

\[
\widehat{S}(t)=\prod_{t_i\le t}\left(1-\frac{d_i}{Y_i}\right),
\]

where `d_i` is observed failure count and `Y_i` is the count at risk immediately before each failure time. This treatment of right-censoring and the Greenwood variance formula are documented in [Zee & Xie, *The Kaplan–Meier Method*](https://pmc.ncbi.nlm.nih.gov/articles/PMC6141203/):

\[
\widehat{V}[\widehat{S}(t)] = \widehat{S}(t)^2
\sum_{t_i\le t}\frac{d_i}{Y_i(Y_i-d_i)}.
\]

The implementation exposes every curve step: time in days, units at risk, events, censors, survival probability, Greenwood variance, and a 95% **pointwise** log-log confidence band. The log-log transformation keeps a non-terminal interval inside the probability range. If all remaining units fail at one time, survival becomes zero and the terminal band is reported as `[0, 0]` rather than fabricating a finite Greenwood standard error.

Honest survival limitations:

- Kaplan–Meier requires non-informative censoring; the database cannot establish whether a still-running record was censored independently of failure risk.
- Confidence bands are pointwise, not simultaneous bands over all time points.
- No failures yields an all-one curve but **not** proof of reliable long-life performance; a warning states that follow-up is incomplete.
- The estimator provides descriptive survival, not a causal comparison between pump selections.

## Rule derivation and computed confidence

### Rule form

The implemented rule is a pre-specified early-failure association. For one named pump model, it compares observations meeting both:

- setting depth below a supplied threshold in ft MD; and
- observed GVF at or above a supplied fraction;

against all otherwise eligible observations for that pump/context. The output statement is conditional and uses “higher observed early-failure risk,” never “causes failure.” A caller can also scope the hypothesis to a field or region.

Thresholds are supplied in `RuleHypothesis` **before** outcome analysis. Automatic search over every depth/GVF split is deliberately not implemented because it would select the most favorable result after observing data. This is an explicit small-sample/selection guard, not missing functionality.

### Eligibility and right censoring

A failed unit is eligible at any duration. A non-failure is eligible only if it has follow-up through the early-failure cutoff. A censored unit ending before the cutoff is excluded from the binary early-failure table because it cannot honestly be classified as either an early failure or a non-failure. The count is surfaced in warnings. All candidate records also feed the Kaplan–Meier summary carried with the rule.

### Required evidence and refusal policy

A rule is refused unless all of the following are true:

1. at least **20** classifiable observations;
2. at least **8** classifiable observations in both exposed and comparison groups;
3. at least **3** observed early failures overall;
4. positive exposed-minus-comparison early-failure risk difference;
5. two-sided Fisher exact p-value no greater than 0.05; and
6. lower 95% risk-difference bound greater than zero.

The 20/8/3 thresholds are product safety guard rails, not vendor facts or population-power guarantees. Their rationale is precisely the framework's warning that three wells in one field must not be displayed as a broadly reliable rule. A refusal is returned as `RuleDerivationRefusal` with every applicable reason and count.

### Evidence displayed with every emitted rule

`RuleEvidence` contains only computed quantities:

- eligible sample size and exposed/comparison group size;
- early-failure counts in both groups;
- distinct known field and region counts;
- effect size: risk difference and risk ratio when its denominator is nonzero;
- 95% risk-difference interval made from component Wilson score intervals; NIST documents the Wilson-score construction at <https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm>;
- stated test: two-sided Fisher exact test, its null, and exact p-value; SciPy documents the fixed-margin 2×2 null at <https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.fisher_exact.html>;
- mean stored `Tracked` source confidence; and
- `evidence_strength` and an auditable text formula.

`evidence_strength` is deliberately **not** a posterior probability or a probability that the rule is true. It is a bounded, computed evidence-strength score:

\[
\text{source quality}
\times \min(1,\sqrt{n/100})
\times \frac{\text{distinct fields}}{\text{distinct fields}+1}
\times \max(0,1-\text{CI width}/2)
\times \min(1,\text{risk difference})
\times (1-p).
\]

It is transparent, automatically penalizes small samples, poor provenance, few fields, imprecise intervals, small effects, and weak test support, and never exceeds 1. The source formula, all component values, and an explanation are persisted so that the UI need not trust a hand-entered confidence label.

## Bias guards

Every emitted rule carries a machine-readable `bias_flags` list and explanations:

| Flag | When it is surfaced | Meaning |
|---|---|---|
| `selection_bias` | Always | Historical installations were engineer-selected rather than randomly assigned. Rejected designs are absent. |
| `confounding` | Always | Depth/GVF is not experimentally isolated from fluid quality, temperature, completion, operation, or other uncontrolled conditions. |
| `survivorship_bias` | Unless population completeness is explicitly confirmed | Installations/outcomes never entered into the database may alter the pattern. |
| `small_sample_overfitting` | Fewer than 100 eligible observations | The rule passed the minimum guard rail but remains unstable evidence. |
| `single_field_generalization` | A non-field-scoped rule has one known field | It must not be generalized outside that field. |
| `right_censoring_limitation` | Censored records ended before the classification cutoff | Those observations were excluded from the contingency table. |

The guard flags are intentionally not erased because a p-value is small. A statistically detectable association in selected, confounded history remains a non-causal advisory judgment.

## What this layer refuses to build

- **No vendor reliability number or causal failure prediction.** There are no field outcomes supplied with the codebase, and observational data cannot establish causality by itself.
- **No automatic pattern mining.** The system will not make up depth/GVF cutoffs from a small or repeatedly searched dataset.
- **No rule below the emission guard rails.** A three-well or one-sided comparison becomes a stated refusal, not a weakly styled recommendation.
- **No unbounded cross-customer learning.** There is no shared data query or shared rule application path. A future shared layer must be a separately consented and governed feature.
- **No result override or optimization objective adjustment.** Empirical output cannot change TDH, a zone, a constraint result, candidate rank, or configuration.
- **No inference from absent values.** Observations missing depth/GVF are excluded with warnings rather than being silently imputed.

## Framework findings

1. **§8.2 correctly requires context, count, source, run life, and outcome, but it does not define an inferential rule or a minimum evidence policy.** This implementation makes its explicit 20/8/3 guard rails visible and treats them as conservative product policy, not external ESP truth.
2. **§8.2 calls confidence “computable” but does not specify whether it means a statistical confidence level, a posterior probability, or an evidence score.** The implementation uses the last option and avoids falsely translating a p-value into probability a rule is true.
3. **§9 decides per-customer isolation but does not specify the database threat model.** The SQLite prototype has structural repository/session guards; production needs PostgreSQL RLS plus credential separation and policy tests.
4. **The proposed example mixes a causal-sounding condition with field history.** Even when a conditional association meets a test threshold, selection and confounding remain possible explanations; this is why rules are labeled advisory and always carry bias flags.
5. **The framework appropriately identifies right censoring, but no surveillance data contract is given for `as_of` time.** The schema requires an explicit outcome-observed date to make censoring and every survival curve reproducible.
