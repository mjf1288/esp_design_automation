# Confidentiality Perimeters

Implementation of framework v0.3 §9. This document is written to be handed to a
customer's security function, which §9.6 identifies as the first gate a deal
passes through.

## What changed and why

v0.1 shipped a hosted multi-tenant service: one SQLite store, a `tenant_id`
column on every table, and query helpers that supplied the predicate. Isolation
was tested and correct.

§9.4 rules that out anyway, and the reasoning is worth restating because it is
not about code quality:

| Approach | Mechanism | Failure mode |
| --- | --- | --- |
| Access check | `if user.can_see(data)` | one forgotten check, in one query |
| Structural isolation | different perimeters never share a store | omission is not expressible |

A tenant predicate protects against a logic error. It does not protect against a
missing enforcement point, because the rows remain physically reachable and the
predicate is the only thing between them and the caller. The acceptance criterion
in §9.4 is that forgetting a check must be *technically impossible*, and no
amount of care applied to a shared table satisfies it.

The threat model also changed. v0.1 tenancy separated our customers from each
other. §9.1 ships the product as a tool the customer deploys in their own
environment, so the perimeters now separate one service company's operators from
each other, inside a deployment we never touch — and the engineer crossing
between them is an authenticated insider, not an outsider.

## The nested capsule

```
org  — oilfield service company        equipment catalog, internal standards, fleet
 ├── operator A                        wells, run-life history, empirical rules
 └── operator B                        wells, run-life history, empirical rules
                                       siblings never merge (§9.2)
```

`Perimeter(org_id, operator_id)`. `operator_id is None` denotes the org capsule.

Org level is **not a wildcard**. It owns what the org owns and cannot read
operator data. This matters: the flat model had one namespace holding both the
catalog and the well history, so "shared catalog, isolated history" was not
expressible at all. Now catalog reads work at org level and every well, design,
observation, and rule endpoint calls `require_operator()` and refuses at org
level with a message naming the level.

Containment is asymmetric and does not span siblings:

```python
org.contains(operator_a)          # True  — shared downward
operator_a.contains(operator_b)   # False — never sideways, same org or not
operator_a.contains(org)          # False — an operator cannot read org-private data
```

## Isolation is the store selection

`PerimeterStoreRegistry` opens one SQLite file per perimeter:

```
data/perimeters/<org_id>/<operator_id>.db
data/perimeters/<org_id>/_org.db
```

The database is chosen from the perimeter before any statement is constructed.
`SELECT * FROM api_cases` issued inside operator A's session returns operator A's
cases, and there is no way to write it so it returns operator B's. This is
asserted directly in `test_perimeter_isolation.py` by running an unscoped raw
SELECT against a sibling's store, rather than by testing that a predicate was
applied.

Rows still carry a `perimeter_key` stamp. It is a **misrouting tripwire, not the
isolation mechanism** — if it were dropped entirely, rows would remain
unreachable from another perimeter. It exists so that a store wired to the wrong
perimeter fails loudly instead of quietly accepting foreign data.

Two independent guarantees stand behind the empirical layer specifically: the
per-perimeter store, plus the pre-existing fail-closed session guard that refuses
an unscoped SELECT outright. The guard was not removed when the stores were
split. Both have to fail before one operator's run-life history reaches another.

### Path safety

Perimeter ids become path segments, so the character set is a security boundary:
`^[a-z0-9][a-z0-9._-]{0,62}$`. Traversal sequences, separators, leading dots and
NUL are outside the pattern by construction, with no sanitising step that could
be skipped. `path_for()` then independently verifies the resolved path is inside
the storage root, so loosening the pattern later still cannot open a file
elsewhere on disk.

## A rule inherits its source perimeter

§9.3 identifies learning as the least obvious leak: a rule derived from one
operator's failures encodes their operating regime even with names stripped.

Audit of what v0.1 already had, and what it did not:

| Object | Perimeter before | Now |
| --- | --- | --- |
| `EmpiricalObservation` | `tenant_id` | perimeter key |
| `DerivedEmpiricalRule` | `tenant_id` | perimeter key |
| `RuleEvidence` | **none** | `source_perimeter` |
| `RuleHypothesis` | **none** (carries `field_id`) | `authored_in_perimeter` |

`RuleEvidence` was the hole. It carries `n_distinct_fields`,
`n_distinct_regions`, and effect sizes — an anonymised rule shipped with its
evidence attached still discloses the source field's regime, and evidence is a
detachable object. It now names its own perimeter, and
`DerivedEmpiricalRule.evidence_visible_in(perimeter_key)` is enforced at the
presentation boundary, not just at storage. Storage selection cannot help when a
rule object is passed between contexts in process — an overlay, a report, a
cached response — which is exactly how evidence would escape.

`RuleHypothesis` has a `field_id` and no perimeter, so a hypothesis authored in
one operator's project could name another operator's field. It now records where
it was authored.

Nothing promotes a rule upward. The org does not aggregate its operators' rules,
and §9.5's shared cross-company layer is deliberately not built: per §9.3
anonymising a rule does not anonymise the experience it encodes. The realistic
alternative is a factory preset owned by no customer.

## The model is an access channel

§9.1 requires that no access channel exists as a property of the architecture.
§9 names the tool developer and stops there, but the agentic layer sends case
data — well data, failure history, teardown text — to a hosted model endpoint,
and that is an access channel to a third party. A security reviewer finds this
immediately, and "the vendor cannot see it" does not survive the follow-up.

So the agentic layer is severable. `ESP_AGENTIC_LAYER=off` (or
`create_app(agentic=False)`) means no LLM client is constructed at all. With it
off:

- intake returns an explicit refusal explaining a structured `Case` must be
  supplied directly to `PUT /api/cases/{id}`;
- design runs work unchanged and `judgments.narrative` is null with a stated
  reason;
- there is no network egress from the design path.

This is clean because §6A.3 already places the entire agentic layer after TDH.
The deterministic engine has no import path to it and is untouched by the switch.

Two further options are configuration rather than architecture, and neither is
built yet: a self-hosted open-weights model inside the perimeter, or the
customer's own model tenancy. Off is the only posture that is true in every
deployment, which is why it exists first.

## GET /api/deployment

Returns the posture above as data: the perimeter model, the caller's resolved
perimeter and level, whether stores are per-perimeter and where the root is,
whether the agentic layer is on, and an explicit list of what leaves the
perimeter.

When the agentic layer is on, that list **names the model endpoint as egress**.
The endpoint is a security claim, so it does not report a deployment as
egress-free while it calls a hosted model.

## What is still open

- **Authentication.** Perimeter identity arrives in `X-Org-Id` / `X-Operator-Id`
  headers and nothing verifies the caller is entitled to them. Structural
  isolation means a wrong perimeter cannot reach another's rows *by accident*; it
  does not stop a caller who asserts a perimeter that isn't theirs. §9.4's
  criterion is met, §15.3 item 6 is not. This must close before any external
  user.
- **Cross-perimeter aggregate leakage.** Per-perimeter stores prevent row access.
  Counts and survival curves computed inside one perimeter are safe, but nothing
  currently prevents adding an endpoint that aggregates across an org's
  operators. §9.3 forbids it; the code does not yet.
- **§9.5 remains an open question**, deliberately.

## Legacy header

`X-Tenant-Id` is still accepted and maps to an *operator* capsule under the
default org, because everything the flat API stored was operator-level data.
Mapping it to org level would silently widen the perimeter of existing rows.
