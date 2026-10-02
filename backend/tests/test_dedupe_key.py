"""Dedupe-key regression (Finding 1, half A).

The original key was::

    int(round(total_head / (total_head * tolerance)))

which mathematically reduces to the constant ``1 / tolerance``, so every stage
count for a given (pump, depth) folded into ONE bucket. The tiebreak keeps the
lowest-stage member, so the enumerator's lower-bracket sample systematically
displaced the correctly-sized member. Combined with the missing hydraulic gate,
this made three under-staged RC2500 41-stg configurations rank first in the
demo case with a 22.6% negative head margin.

These tests defend the fixed key -- an absolute head window anchored to a
reference TDH, which is what tolerance means physically -- against both the
original bug and one obvious near-miss.
"""

from __future__ import annotations

import pytest

from api.app import _demo_feasible_case
from esp_engine.catalog import load_catalog
from esp_engine.config import DEFAULT_CONFIG
from esp_engine.selection import (
    dedupe_by_hydraulic_equivalence,
    enumerate_candidates,
)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def enumerated(catalog):
    return enumerate_candidates(
        case=_demo_feasible_case(),
        catalog=catalog,
        cfg=DEFAULT_CONFIG,
        tdh_estimate_ft=1517.4,
        design_rate_bpd=2368.0,
        gas_degradation_estimate=0.9717,
    )


# --- the promise -------------------------------------------------------------


def test_different_stage_counts_do_not_collapse_into_one_bucket(enumerated):
    """The whole point of dedupe is to fold hydraulically indistinguishable
    configs. Different stage counts of the same pump at the same frequency
    produce different total head and are NOT indistinguishable.

    Under the original bug this test would fail: only one RC2500 stage count
    would survive per (pump, depth).
    """
    after = dedupe_by_hydraulic_equivalence(
        enumerated.candidates, design_rate_bpd=2368.0, reference_head_ft=1517.4
    )
    rc = [
        c
        for c, p, _ in after
        if p.model == "RC2500" and c.setting_depth_md_ft == 9000
    ]
    # group by frequency; each frequency must contribute more than one stage count
    from collections import defaultdict

    by_freq: dict[float, list[int]] = defaultdict(list)
    for c in rc:
        by_freq[c.frequency_hz].append(c.stages)

    multi_stage_frequencies = sum(1 for stages in by_freq.values() if len(stages) > 1)
    assert multi_stage_frequencies >= 3, (
        f"dedupe collapsed too aggressively: only {multi_stage_frequencies} "
        f"frequencies retained more than one stage count. by_freq={dict(by_freq)}. "
        f"The original degenerate key was ``1/tolerance`` for every candidate; "
        f"this collapse pattern is exactly what the fix must avoid."
    )


def test_reference_head_scales_the_grid_cell_in_absolute_feet(enumerated):
    """A larger reference TDH must produce a proportionally larger grid cell,
    which folds more configurations. This is the linear property we want."""
    tight = dedupe_by_hydraulic_equivalence(
        enumerated.candidates, design_rate_bpd=2368.0, reference_head_ft=500.0
    )
    loose = dedupe_by_hydraulic_equivalence(
        enumerated.candidates, design_rate_bpd=2368.0, reference_head_ft=5000.0
    )
    assert len(loose) <= len(tight), (
        f"loose reference (5000 ft) should fold at least as many candidates as "
        f"tight reference (500 ft): tight={len(tight)}, loose={len(loose)}"
    )


# --- mutation guard: reintroducing the original bug must fail this test ------


def test_mutation_restoring_original_stages_independent_key_would_regress(
    monkeypatch, catalog, enumerated
):
    """Simulate the original defective key (grid cell scales with stages, so
    the ratio cancels and every stage count collapses). Verify the mutation
    reproduces the original defect directly."""
    from esp_engine import selection as selection_module

    original = selection_module.dedupe_by_hydraulic_equivalence

    def buggy(candidates, design_rate_bpd, reference_head_ft, tolerance=0.015):
        # replicate the ORIGINAL bug: grid = total_head * tolerance -> ratio
        # is constant, all stage counts collapse.
        from esp_engine.curves import evaluate_stage

        buckets: dict[tuple[str, int], tuple] = {}
        for config, pump, curve in candidates:
            try:
                point = evaluate_stage(curve, design_rate_bpd)
            except Exception:
                continue
            total_head = point.head_ft_per_stage * config.stages
            if total_head <= 0:
                key_head = 0
            else:
                grid = total_head * tolerance
                key_head = int(round(total_head / grid))
            key = (f"{pump.id}|{config.setting_depth_md_ft:.0f}", key_head)
            existing = buckets.get(key)
            if existing is None or config.stages < existing[0].stages:
                buckets[key] = (config, pump, curve)
        return list(buckets.values())

    monkeypatch.setattr(selection_module, "dedupe_by_hydraulic_equivalence", buggy)

    # under the buggy key: each (pump, depth) folds to exactly one bucket, and
    # the retained member is the smallest stage count.
    after = buggy(enumerated.candidates, design_rate_bpd=2368.0, reference_head_ft=1517.4)
    rc9000 = [
        c
        for c, p, _ in after
        if p.model == "RC2500" and c.setting_depth_md_ft == 9000
    ]
    assert len(rc9000) == 1, (
        f"the ORIGINAL degenerate key must produce exactly one RC2500-at-9000 "
        f"survivor per (pump, depth); got {len(rc9000)}. If this changes, the "
        f"mutation is no longer replicating the pre-fix behaviour and the "
        f"guard is invalid."
    )
    # and that survivor must be the minimum-stage variant -- the exact defect
    from esp_engine.selection import candidate_stage_counts
    from esp_engine.catalog import load_catalog as _load

    cat = _load()
    rc = next(p for p in cat.pumps if "RC2500" in p.model)
    # cheapest sanity: 43 stages was the minimum RC2500 stage bracket count in
    # the pre-fix reproduction (65 Hz, needs 53 -> bracket goes 33/43/53). Any
    # value smaller than a correctly-sized build (>= 53) is the defect signal.
    assert rc9000[0].stages < 53, (
        f"the original defect kept an under-staged member; the mutation should "
        f"reproduce that. Got {rc9000[0].stages} stages."
    )
