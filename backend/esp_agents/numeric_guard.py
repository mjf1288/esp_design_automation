"""Scoped, unit-aware numeric validation for generated narrative text.

## Why this module exists

The first implementation of the numeric guard built a single flat set of every
number appearing anywhere in a ``DesignResult`` — all 240 evaluated cells, all
200 rejected configurations, every scenario point, plus every number embedded in
prose explanation strings — and accepted a narrative token if it matched any of
them.

Measured against a real engine run, that whitelist held 2,684 distinct values.
**61% of all integers from 1 to 2000 passed undetected.** A guard that accepts
three out of five fabricated numbers is not a guard; it is a source of false
confidence, which in an engineering document is worse than no guard at all.

Two design errors caused it:

1. **No scope.** A narrative describing ranked candidate #1 was permitted to
   cite any number from 200 rejected configurations it never mentions. The
   narrative's subject matter is a tiny slice of the result, so the whitelist
   must be that slice.
2. **No units.** ``41`` matching "41" anywhere meant a fabricated
   "41 Hz" was accepted because 41 appeared as a stage count. Numbers in
   engineering prose are meaningless without their dimension.

This module fixes both. The schema already encodes units in field names
(``setting_depth_md_ft``, ``pip_psi``, ``frequency_hz``), so unit association is
derived from the data model rather than from a hand-maintained table.

## What this guard does and does not promise

It verifies that every numeric token in the narrative traces to a value the
deterministic engine actually computed, in a compatible dimension, within the
narrated scope. It does not verify that the number is being *described*
correctly — "efficiency is 67.2%" and "efficiency drops from 67.2%" are
indistinguishable to it. Semantic correctness still requires review, and §11
progressive trust is what governs that.

Known and accepted limits, restated because a silent limit is a liability:

- Spelled-out quantities ("five hertz") are not detected.
- A dimensionless token with no adjacent unit falls back to scope-only checking.
- Arithmetic the LLM performs on real values (a legitimate-looking difference of
  two true numbers) is rejected, not accepted. That is the intended direction of
  the error: the narrative agent must quote, not compute.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from esp_engine.results import DesignResult

__all__ = [
    "NumericFinding",
    "narrative_scope",
    "ScopedNumericGuard",
    "UNIT_ALIASES",
]

# A number, optionally signed, with optional thousands separators and decimals,
# optionally followed by a percent sign.
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")

# Unit token appearing immediately BEFORE a number, e.g. "month 17", "stage 41",
# "AWG 6". Engineering prose uses this ordering for ordinal/indexed quantities as
# readily as it uses a trailing unit, and ignoring it left a hole: "falls out of
# range at month 17" was accepted because 17 appeared somewhere unrelated.
_LEADING_UNIT_RE = re.compile(r"([A-Za-z][A-Za-z°/%.\-]{0,14})\s{0,3}$")

# Unit token appearing immediately after a number, e.g. "9,000 ft", "65 Hz".
# Captures word characters and a few punctuation forms used in unit strings
# ("scf/stb", "bbl/d", "degF", "V/1000ft").
_TRAILING_UNIT_RE = re.compile(r"[\s-]{0,3}([A-Za-z][A-Za-z°/%.\-]{0,14})")

# Units that may legitimately PRECEDE a number. Engineering prose puts only
# ordinal/index labels in front ("month 17", "stage 41", "AWG 6"); it never writes
# "psi 250". Restricting the leading position matters because short unit aliases
# collide with ordinary words — "a" is the amps symbol and also the English
# article, so "over a 24-month horizon" was being read as 24 amps and rejected.
_LEADING_UNIT_WHITELIST: frozenset[str] = frozenset(
    {
        "month",
        "months",
        "day",
        "days",
        "year",
        "years",
        "stage",
        "stages",
        "awg",
        "series",
        "rank",
        "candidate",
        "point",
    }
)

# Canonical dimension for each unit spelling an LLM might produce.
# Maps a lowercased surface form to a canonical dimension key.
UNIT_ALIASES: dict[str, str] = {
    # length / depth
    "ft": "ft", "feet": "ft", "foot": "ft",
    # diameter
    "in": "in", "inch": "in", "inches": "in",
    # pressure
    "psi": "psi", "psia": "psi", "psig": "psi",
    # liquid rate
    "bpd": "bpd", "bfpd": "bpd", "blpd": "bpd", "bopd": "bpd", "bwpd": "bpd",
    "stb/d": "bpd", "bbl/d": "bpd", "b/d": "bpd",
    # gas rate
    "mscf/d": "mscfd", "mscfd": "mscfd", "scf/d": "scfd",
    # gas-oil ratio
    "scf/stb": "scf_stb", "scf/bbl": "scf_stb",
    # temperature
    "f": "f", "degf": "f", "°f": "f", "deg": "f",
    # frequency
    "hz": "hz", "hertz": "hz",
    # power
    "hp": "hp", "bhp": "hp", "horsepower": "hp",
    # electrical
    "v": "v", "volt": "v", "volts": "v",
    "a": "a", "amp": "a", "amps": "a", "ampere": "a", "amperes": "a",
    "kva": "kva",
    # time
    "month": "months", "months": "months", "mo": "months",
    "day": "days", "days": "days",
    "year": "years", "years": "years", "yr": "years",
    # discrete counts
    "stage": "stages", "stages": "stages",
    # dimensionless presentation
    "%": "frac_pct",
}

# Field-name suffix -> canonical dimension. The engine's models name every
# physical quantity with its unit, so this is read off the schema rather than
# maintained by hand.
_SUFFIX_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("_md_ft", "ft"),
    ("_tvd_ft", "ft"),
    ("_ft", "ft"),
    ("_in", "in"),
    ("_psi", "psi"),
    ("_bpd", "bpd"),
    ("_mscfd", "mscfd"),
    ("_scfd", "scfd"),
    ("_scf_stb", "scf_stb"),
    ("_f", "f"),
    ("_hz", "hz"),
    ("_hp", "hp"),
    ("_v", "v"),
    ("_v_per_1000ft", "v"),
    ("_a", "a"),
    ("kva_required", "kva"),
    ("_months", "months"),
    ("_days", "days"),
    ("_years", "years"),
    ("stages", "stages"),
    ("_lb", "lb"),
    ("_lb_ft3", "lb_ft3"),
    ("_cp", "cp"),
)

# Some result fields are bare names rather than unit-suffixed ones. The timeline
# row field is literally ``month``, which matches no suffix in the table above, so
# without these mappings every real month index fell into the unknown-dimension
# bucket and any narrative that said "by month 16" was rejected despite month 16
# being a genuine row in the timeline.
_EXACT_FIELD_DIMENSIONS: dict[str, str] = {
    "month": "months",
    "months": "months",
    "day": "days",
    "days": "days",
    "year": "years",
    "years": "years",
    "stages": "stages",
    "frequency": "hz",
    "hz": "hz",
}

# Units as they appear in the assumption ledger's explicit ``unit`` field, mapped
# to guard dimensions.
_LEDGER_UNIT_DIMENSIONS: dict[str, str] = {
    "ft": "ft",
    "f": "f",
    "degf": "f",
    "psi": "psi",
    "psia": "psi",
    "bpd": "bpd",
    "stb/d": "bpd",
    "hz": "hz",
    "hp": "hp",
    "v": "v",
    "a": "a",
    "in": "in",
    "months": "months",
    "days": "days",
    "ppm": "ppm",
    "scf/stb": "scf_stb",
    "lb": "lb",
    "cp": "cp",
    "lb/ft3": "lb_ft3",
}

# Widest gap between a stored fraction expressed as a percentage and the text the
# model writes for it. 0.05 accommodates one-decimal rounding (0.39897 -> 39.9%)
# without letting neighbouring fractions vouch for each other.
_PERCENT_ABSOLUTE_TOLERANCE = 0.05

# Fractions are the one presentation transform allowed: a stored 0.672 may be
# written as 67.2%. Recognised by these suffixes.
_FRACTION_SUFFIXES = ("_frac", "_fraction", "_efficiency", "_over_qbep")

# Quantity-name binding for percentages.
#
# The plain frac_pct check compares an integer like 58 against every fraction
# in scope, and a 24-month trajectory across three envelopes legitimately
# publishes dozens of them (water cut per month, efficiency per month, q/qBEP
# per month, motor loading per candidate). Any one true value therefore vouches
# for a fabrication that happens to sit at the same integer. Measured effect
# on the demo case: 31 of 100 integer percentages accepted with no quantity
# name at all, and the same 31 accepted when a fake quantity name ("efficiency
# 58%") was placed in front -- the guard was blind to the label.
#
# When the narrative writes ``<quantity name> <N>%`` the check tightens to the
# specific field the name resolves to. If the phrase is not one this table
# knows, or if the resolved field carries no value in scope, the check falls
# back to the plain frac_pct behaviour: the direction of error is a false
# accept the wider check would have made anyway, never a false reject of a
# legitimate narrative. This is the guardrail the roadmap calls out --
# narrowing scope must not turn correct narratives into refusals.
#
# Multi-word aliases are matched longest-first, so ``motor loading`` binds to
# ``motor_loading_frac`` rather than the shorter ``loading`` alias.
_QUANTITY_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "water_cut_frac": (
        "water cut", "watercut", "water-cut", "wc",
    ),
    "motor_loading_frac": (
        "motor loading", "motor load", "motor loading fraction",
    ),
    "loading": (
        "loading",
    ),
    "efficiency_frac": (
        "efficiency", "pump efficiency", "hydraulic efficiency",
    ),
    "efficiency_avg_frac": (
        "average efficiency", "avg efficiency", "mean efficiency",
    ),
    "cable_voltage_drop_frac": (
        "voltage drop", "cable voltage drop", "vd",
    ),
    "head_margin_frac": (
        "head margin", "margin",
    ),
    "q_over_qbep": (
        "q/qbep", "q over qbep", "q/q_bep", "q_over_qbep",
    ),
    "distance_from_bep_frac": (
        "distance from bep", "bep distance",
    ),
    "bep_time_frac": (
        "bep time fraction", "time near bep", "time in bep", "time at bep",
    ),
    "time_coverage_frac": (
        "time coverage", "coverage",
    ),
    "load_fraction": (
        "load fraction",
    ),
    # Framework §6 phasor-form quantities. Apparent PF is a decimal already,
    # not a percentage, but a narrative may still write "apparent PF drops to
    # 72%" so the alias is bound to the fraction field. Load fraction is the
    # motor's operating-current load fraction.
    "apparent_power_factor": (
        "apparent pf", "apparent power factor", "apparent-pf",
    ),
}

# Cross-alias index: lowercased phrase -> canonical field name. Built once at
# import from the table above. Sorted longest-first so alternation prefers the
# most specific phrase (``motor loading`` before ``loading``).
_QUANTITY_PHRASE_TO_FIELD: dict[str, str] = {
    phrase: field
    for field, phrases in _QUANTITY_NAME_ALIASES.items()
    for phrase in phrases
}
_QUANTITY_PHRASES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(_QUANTITY_PHRASE_TO_FIELD.keys(), key=len, reverse=True)
)
# Preceding-phrase regex.
#   (?:^|\W)               -- a word boundary before the phrase
#   (<alternation>)         -- one of the known phrases (case-insensitive)
#   \b                     -- end of the phrase
#   (?:\W+\w+){0,3}         -- up to three short intervening words
#                              ("is", "reaches", "dropped to", "was")
#   \W*$                   -- possibly some trailing whitespace/punctuation,
#                              then end of the prefix (which ends immediately
#                              before the number the guard is looking at).
_LEADING_QUANTITY_RE = re.compile(
    r"(?:^|\W)(" + "|".join(re.escape(p) for p in _QUANTITY_PHRASES_BY_LENGTH)
    + r")\b(?:\W+\w+){0,3}\W*$",
    re.IGNORECASE,
)


# Run-level fields the narrative may legitimately reference.
_RUN_LEVEL_KEYS: tuple[str, ...] = (
    "verdict",
    "verdict_explanation",
    "branch",
    "branch_rationale",
    "scenario_points",
    "assumption_ledger",
    "blocking_data_gaps",
    "recommended_data_requests",
    "input_confidence",
    "provenance",
    "engine_warnings",
    "rejection_summary",
)


def narrative_scope(
    result: DesignResult, *, candidate_rank: int | None = 1
) -> dict[str, Any]:
    """Project the slice of a result the narrative is permitted to discuss.

    **This must be the single source of truth for narrative scope.** Both the
    prompt sent to the model and the guard that checks its output are built from
    this function.

    An earlier version violated that rule: the prompt serialized the entire
    ``DesignResult`` — every rejected configuration and every evaluated cell,
    thousands of numbers — while the guard whitelisted only the narrated
    candidate. The model was shown numbers it was forbidden to use, so refusal
    was structurally guaranteed rather than a signal of hallucination. Showing a
    model data and then punishing it for using that data is a contradiction in
    the design, not a safety feature.
    """
    payload = result.model_dump(mode="json")
    scope: dict[str, Any] = {
        key: payload.get(key) for key in _RUN_LEVEL_KEYS if key in payload
    }

    # Exactly the narrated candidate. Deliberately NOT the rejection list and NOT
    # other candidates: the narrative has no business quoting configurations it
    # does not mention, and admitting them is what made the original guard
    # permissive enough to be useless.
    candidates = payload.get("candidates") or []
    if candidate_rank is not None:
        narrated = next(
            (c for c in candidates if c.get("rank") == candidate_rank), None
        )
    else:
        narrated = candidates
    scope["candidate"] = _strip_diagnostic_arrays(narrated)
    return scope


@dataclass(frozen=True)
class NumericFinding:
    """One numeric token in the narrative that could not be substantiated."""

    token: str
    value: float
    unit: str | None
    dimension: str | None
    field: str
    context: str
    reason: str

    def __str__(self) -> str:  # pragma: no cover - diagnostic convenience
        return f"{self.field}: {self.token!r} ({self.reason}) near {self.context!r}"


def _dimension_for_field(field_name: str) -> str | None:
    """Infer the physical dimension of a result field from its name."""
    lowered = field_name.lower()
    if lowered in _EXACT_FIELD_DIMENSIONS:
        return _EXACT_FIELD_DIMENSIONS[lowered]
    if any(lowered.endswith(suffix) for suffix in _FRACTION_SUFFIXES):
        return "frac"
    for suffix, dimension in _SUFFIX_DIMENSIONS:
        if lowered.endswith(suffix):
            return dimension
    return None


def _is_embedded_in_token(text: str, start: int) -> bool:
    """True when a number is part of a unit or identifier, not a quantity itself.

    ``8.0 deg/100ft`` contains "100" as part of the unit string, and ``RC2500``
    contains "2500" as part of a model name. Neither is a value the narrative is
    reporting, but both were being extracted and then rejected as unsupported —
    turning a correctly-quoted assumption into a refusal. A digit run attached
    directly to a preceding letter or solidus belongs to that token.
    """
    return start > 0 and (text[start - 1].isalpha() or text[start - 1] == "/")


def _walk(value: Any, field: str = "") -> Iterable[tuple[str, float]]:
    """Yield (field_name, numeric_value) for every number in a JSON-ish tree.

    Booleans are skipped: ``True`` is not the number 1 in any narrative sense,
    and admitting it would whitelist 1 and 0 universally.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        as_float = float(value)
        if math.isfinite(as_float):
            yield field, as_float
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, key if isinstance(key, str) else field)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, field)


class ScopedNumericGuard:
    """Validates narrative numbers against the narrated slice of a result.

    Construct one per narrative. ``candidate_rank`` names the candidate the
    narrative is about; only that candidate's numbers, plus run-level context
    (verdict, scenario points, provenance, assumption ledger), enter the
    whitelist. Rejected configurations and unrelated candidates are excluded,
    because a narrative that cites them is either off-topic or fabricating.
    """

    def __init__(
        self,
        result: DesignResult,
        *,
        candidate_rank: int | None = 1,
        absolute_tolerance: float = 0.01,
        relative_tolerance: float = 0.005,
        include_prose_numbers: bool = True,
    ) -> None:
        if absolute_tolerance < 0 or relative_tolerance < 0:
            raise ValueError("Numeric tolerances cannot be negative.")
        self.absolute_tolerance = absolute_tolerance
        self.relative_tolerance = relative_tolerance
        self._by_dimension: dict[str | None, set[float]] = {}
        self._any_dimension: set[float] = set()
        # Per-field whitelist for quantity-name binding. Stored as percentages
        # (fraction * 100) so ``water_cut_frac`` -> {35.0, 35.5, ...} rather
        # than {0.35, 0.355, ...}; check_text compares against a percent token
        # the model wrote ("58%") so the units line up without a second
        # conversion at check time.
        self._by_field_pct: dict[str, set[float]] = {}
        # Percentages the engine itself wrote verbatim into its prose
        # (warnings, remedy hints, rejection reasons). Quantity-name binding
        # is a whitelist over the schema's computed fields and does not know
        # about band-boundary constants like ``75%-85%`` that only appear in
        # engine prose. A narrative that quotes engine prose verbatim must
        # stay accepted regardless of the bound field, because those numbers
        # ARE facts the engine committed to. Populated from the same walk
        # that builds ``_by_dimension``.
        self._prose_verbatim_pct: set[float] = set()
        self._build(result, candidate_rank, include_prose_numbers)

    @staticmethod
    def _ledger_values(scope: dict[str, Any]) -> list[tuple[str | None, float]]:
        """Extract assumption-ledger values, which are typed differently.

        Ledger entries store ``value`` as a *string* with the unit in a sibling
        ``unit`` field, so the generic numeric walk skipped them entirely. Every
        assumed input was therefore unquotable — a narrative that correctly
        reported an assumed surface temperature of 80 F was rejected as
        hallucinated. Assumptions are exactly what a narrative most needs to
        disclose, so this is the opposite of the behaviour we want.
        """
        found: list[tuple[str | None, float]] = []
        for entry in scope.get("assumption_ledger") or []:
            if not isinstance(entry, dict):
                continue
            for key in ("value", "unbiased_value"):
                raw = entry.get(key)
                if raw is None:
                    continue
                try:
                    number = float(str(raw).strip())
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(number):
                    continue
                unit = (entry.get("unit") or "").strip().lower()
                dimension = _LEDGER_UNIT_DIMENSIONS.get(unit)
                found.append((dimension, number))
                if dimension is None and 0.0 <= number <= 1.0:
                    # An unitless ledger fraction may be shown as a percentage.
                    found.append(("frac_pct", number * 100.0))
        return found

    # -- whitelist construction -------------------------------------------

    def _build(
        self,
        result: DesignResult,
        candidate_rank: int | None,
        include_prose_numbers: bool,
    ) -> None:
        scope = narrative_scope(result, candidate_rank=candidate_rank)

        for field, number in self._ledger_values(scope):
            self._any_dimension.add(number)
            self._by_dimension.setdefault(field, set()).add(number)

        for field, number in _walk(scope):
            dimension = _dimension_for_field(field)
            self._any_dimension.add(number)
            self._by_dimension.setdefault(dimension, set()).add(number)
            if dimension == "frac":
                # A stored fraction may be presented as a percentage.
                self._by_dimension.setdefault("frac_pct", set()).add(number * 100.0)
                self._any_dimension.add(number * 100.0)
                # Populate the field-scoped whitelist too, so a narrative
                # naming this quantity by phrase ("water cut 35%") is checked
                # only against values from this same field. The key here is
                # the schema field name; alias phrases resolve to it via
                # _QUANTITY_PHRASE_TO_FIELD at check time.
                self._by_field_pct.setdefault(field, set()).add(number * 100.0)
            elif field == "apparent_power_factor":
                # apparent_power_factor is a decimal, not a fraction-suffixed
                # field, but the value range (0..1) makes narrative
                # percentage presentations plausible ("apparent PF at load
                # 72%"). Handled explicitly so the field-scoped whitelist
                # sees it.
                self._by_field_pct.setdefault(field, set()).add(number * 100.0)

        if include_prose_numbers:
            # Numbers the engine itself wrote into explanation strings are real
            # computed values and must remain quotable. Crucially, they are
            # harvested WITH the unit written beside them, using the same unit
            # detection applied to the narrative. Harvesting them as
            # dimensionless was a false-negative machine in both directions: an
            # engine warning saying "outside the 75%-85% target band" left 75 and
            # 85 unavailable as percentages, so a narrative that quoted the band
            # verbatim was rejected, while the bare values 75 and 85 became
            # quotable as any other dimension.
            for _, text in _walk_strings(scope):
                for match in _NUMBER_RE.finditer(text):
                    token = match.group(0)
                    if _is_embedded_in_token(text, match.start()):
                        continue
                    try:
                        parsed = float(token.rstrip("%").replace(",", ""))
                    except ValueError:
                        continue
                    self._any_dimension.add(parsed)
                    _, dimension = self._unit_after(text, match.end(), token)
                    if dimension is not None:
                        self._by_dimension.setdefault(dimension, set()).add(parsed)
                    if dimension == "frac_pct":
                        # Engine prose exemption is quantity-name aware too:
                        # if the prose itself names a quantity ("water cut
                        # 35% -> 42%"), the number is routed to that field's
                        # bucket rather than the general exemption pool. The
                        # unrestricted exemption would let a drift-note
                        # water-cut number vouch for an efficiency
                        # fabrication at the same integer, defeating the
                        # quantity-name binding this module is built for.
                        prose_field = self._leading_quantity_field(
                            text, match.start()
                        )
                        if prose_field is not None:
                            self._by_field_pct.setdefault(
                                prose_field, set()
                            ).add(parsed)
                        else:
                            # No quantity name adjacent -- treat as a
                            # band-boundary / tolerance / limit constant
                            # ("75%-85%", "10% tolerance", "5% allowable").
                            # Restrict to values that aren't already in
                            # some field, so a schema value never
                            # accidentally gains cross-field vouching
                            # power.
                            already_in_field = any(
                                self._matches(parsed, allowed, "frac_pct")
                                for allowed in self._by_field_pct.values()
                            )
                            if not already_in_field:
                                self._prose_verbatim_pct.add(parsed)

    # -- checking ----------------------------------------------------------

    def _matches(
        self, value: float, allowed: Iterable[float], dimension: str | None = None
    ) -> bool:
        return any(
            abs(value - candidate) <= self._window(value, dimension)
            for candidate in allowed
        )

    def _window(self, value: float, dimension: str | None) -> float:
        """Match window for a value, tightened for percentages.

        A relative window is right for large physical magnitudes: quoting 1,845
        bpd for a computed 1,845.14 is faithful reporting, not fabrication.

        It is wrong for percentages. A percentage is a fraction already scaled by
        100, so a 0.5% relative window on "40%" spans 0.2 percentage points. A
        24-month trajectory legitimately contains dozens of distinct fractions
        (efficiency, water cut, q/qBEP, zone severity, each per timeline row),
        and once every one of them is expanded into percent space those windows
        tile the axis: measurement showed 63 of 100 integer percentages accepted,
        which is no protection at all. Percentages therefore get a tight absolute
        window, wide enough only for one-decimal rounding of a real value.
        """
        if dimension == "frac_pct":
            return max(self.absolute_tolerance, _PERCENT_ABSOLUTE_TOLERANCE)
        if dimension == "ft":
            # Head/TDH recovery evidence adds adjacent lengths. A 0.5% window
            # around 2,000 ft would accept a fabricated ten-foot change.
            # Permit nearest-foot reporting, not an engineering error band.
            return max(self.absolute_tolerance, min(0.5, self.relative_tolerance * max(abs(value), 1.0)))
        tolerance_basis = max(abs(value), 1.0)
        return max(self.absolute_tolerance, self.relative_tolerance * tolerance_basis)

    @staticmethod
    def _leading_quantity_field(text: str, position: int) -> str | None:
        """Resolve the schema field a preceding quantity-name phrase points at.

        Looks backward from ``position`` (the start of a number) for one of the
        aliases in :data:`_QUANTITY_PHRASE_TO_FIELD`. Multi-word aliases are
        matched longest-first, and up to three short intervening words are
        allowed ("water cut reaches 58%", "loading dropped to 44%"). Returns
        ``None`` when no known phrase precedes the number -- the caller must
        fall back to the wider whitelist rather than tightening.
        """
        prefix = text[:position]
        match = _LEADING_QUANTITY_RE.search(prefix)
        if match is None:
            return None
        return _QUANTITY_PHRASE_TO_FIELD.get(match.group(1).lower())

    def check_text(self, text: str, *, field: str) -> list[NumericFinding]:
        """Return findings for every unsubstantiated number in ``text``."""
        findings: list[NumericFinding] = []
        for match in _NUMBER_RE.finditer(text):
            token = match.group(0)
            if _is_embedded_in_token(text, match.start()):
                continue
            raw = token.rstrip("%").replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue

            unit, dimension = self._unit_after(text, match.end(), token)
            if dimension is not None:
                # Quantity-name binding for percentages: when the number is a
                # percentage AND is preceded by a phrase that names a schema
                # field, restrict the check to that field's own values. The
                # fall-through direction is deliberate: an unknown phrase or a
                # field with no values in scope reverts to the wider frac_pct
                # check, so a legitimate narrative can never become a refusal
                # by lack of binding. The residual false-accept rate this
                # closes is measurable (see test_agents_numeric_guard.py).
                if dimension == "frac_pct":
                    bound_field = self._leading_quantity_field(text, match.start())
                    if bound_field is not None:
                        field_allowed = self._by_field_pct.get(bound_field)
                        if field_allowed:
                            if self._matches(value, field_allowed, dimension):
                                continue
                            # Engine prose exemption: a percentage the engine
                            # itself wrote (band constants, warning
                            # thresholds) stays quotable even under a
                            # binding, because a narrative repeating engine
                            # prose verbatim is not fabricating.
                            if self._matches(
                                value, self._prose_verbatim_pct, dimension
                            ):
                                continue
                            # Fall through into the standard failure path below,
                            # with a reason that names the binding so the
                            # writer knows exactly which channel refused it
                            # ("water cut has no value near 58% in scope").
                            reason = (
                                f"no {bound_field} value near {value:g}% "
                                f"appears in the narrated scope; the quantity "
                                f"name binds this token to {bound_field} "
                                f"specifically, not to any percentage in the "
                                f"result"
                            )
                            start = max(0, match.start() - 40)
                            end = min(len(text), match.end() + 40)
                            findings.append(
                                NumericFinding(
                                    token=token,
                                    value=value,
                                    unit=unit,
                                    dimension=dimension,
                                    field=field,
                                    context=text[start:end].strip(),
                                    reason=reason,
                                )
                            )
                            continue
                allowed = self._by_dimension.get(dimension, set())
                if self._matches(value, allowed, dimension):
                    continue
                reason = (
                    f"no computed value in {dimension} matches this number within "
                    f"tolerance for the narrated candidate"
                )
            else:
                if self._matches(value, self._any_dimension):
                    continue
                reason = (
                    "number does not appear anywhere in the narrated scope of the "
                    "engine result"
                )

            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            findings.append(
                NumericFinding(
                    token=token,
                    value=value,
                    unit=unit,
                    dimension=dimension,
                    field=field,
                    context=text[start:end].strip(),
                    reason=reason,
                )
            )
        return findings

    @staticmethod
    def _unit_after(
        text: str, position: int, token: str
    ) -> tuple[str | None, str | None]:
        """Identify the dimension of a number from an adjacent unit token.

        A trailing unit is checked first ("65 Hz"), then a leading one
        ("month 17"). Only a recognised unit yields a dimension; an unrecognised
        neighbouring word degrades to scope-only checking rather than guessing.
        """
        if token.endswith("%"):
            return "%", "frac_pct"

        # A space-separated percent sign ("39.9 %") must be treated identically to
        # the attached form. Without this the number falls through to the
        # dimensionless path and is checked against every value in scope, which
        # is far weaker than a percentage check.
        if text[position : position + 2].lstrip().startswith("%"):
            return "%", "frac_pct"

        tail_match = _TRAILING_UNIT_RE.match(text[position : position + 20])
        if tail_match:
            surface = tail_match.group(1).strip(".,;:").lower()
            dimension = UNIT_ALIASES.get(surface)
            if surface == "in" and re.match(r"\s*in\s+(?:the|a|an|this|that|these|those)\b", text[position:], re.I):
                dimension = None
            if dimension is not None:
                return surface, dimension

        head_match = _LEADING_UNIT_RE.search(text[: position - len(token)])
        if head_match:
            surface = head_match.group(1).strip(".,;:").lower()
            if surface in _LEADING_UNIT_WHITELIST:
                dimension = UNIT_ALIASES.get(surface)
                if dimension is not None:
                    return surface, dimension

        return None, None


# Internal diagnostic arrays excluded from the whitelist. These hold the raw
# per-cell sweep the engine used to reach its conclusions. On a real run,
# ``cells_sampled`` alone contributed 144 distinct depth values, and removing it
# dropped the share of arbitrary integers accepted as a length from 22.4% to a
# few percent. The narrative has a purpose-built summary of the same information
# in ``timeline``; if it wants to cite a depth or a head, it must cite that.
_DIAGNOSTIC_KEYS = frozenset({"cells_sampled", "engineering"})


def _strip_diagnostic_arrays(candidate: Any) -> Any:
    """Drop internal sweep arrays from a candidate before whitelisting it."""
    if isinstance(candidate, dict):
        projected = {
            k: _strip_diagnostic_arrays(v)
            for k, v in candidate.items() if k not in _DIAGNOSTIC_KEYS
        }
        # The narrative explains the recovery boundary; the full monthly
        # evidence belongs in the engineer's table. Retain its first and last
        # verified points plus the entire frequency schedule. Prompt and guard
        # share this projection, so neither can quote hidden interior values.
        points = projected.get("verified_points")
        if isinstance(points, list) and len(points) > 2:
            projected["verified_points"] = [points[0], points[-1]]
        return projected
    if isinstance(candidate, list):
        return [_strip_diagnostic_arrays(item) for item in candidate]
    return candidate


def _walk_strings(value: Any, field: str = "") -> Iterable[tuple[str, str]]:
    """Yield (field_name, text) for every string in a JSON-ish tree."""
    if isinstance(value, str):
        yield field, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, key if isinstance(key, str) else field)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child, field)
