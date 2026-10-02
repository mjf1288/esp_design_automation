"""Narrative generation with a mechanical numeric-hallucination guard."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import ValidationError

from esp_engine.results import DesignResult
from .numeric_guard import ScopedNumericGuard, narrative_scope

from .contracts import (
    NarrativeLLMResponse,
    NarrativeResult,
    NumericViolation,
)
from .llm import StructuredLLM

# Numeric tokens are deliberately digit-based.  The product forbids invented
# engineering values; words such as "one" are prose, not machine-readable values.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?"
)

NARRATIVE_SYSTEM_PROMPT = """You write engineer-readable ESP design explanations.
Return JSON only following the schema. Facts and judgments are separate fields:
put deterministic result explanation only in fact fields; put interpretations,
caveats, and recommendations only in judgments.

You may only use numeric values supplied in the deterministic DesignResult facts.
Do not calculate, infer, round to a new value, convert units, add a threshold, or
state a count that is not supplied. Prefer qualitative language when a value is not
present. Explain configuration, tradeoffs, validity boundary, what happens after it,
and what to watch, without modifying or contradicting the deterministic result.

Time is indexed in MONTHS, matching the engine's timeline rows. Refer to a point in
time only as "month N" using an N present in the timeline. Never relabel a time
point in other units and never invent a time label: write "month 0", not "Day 1";
write "month 24", not "+2 yr" or "2 years". These relabellings introduce numbers
the deterministic result does not contain.

Round dimensionless scores and fractions to at most three decimal places: write
"0.824", not "0.8242040437120908". Reproducing full floating-point precision is
not more accurate, it is less readable, and it implies a certainty the underlying
estimate does not have.

Write each unit exactly as the field name gives it (psi, ft, bpd, Hz, hp, F). A
value stored as a fraction may be written as a percentage, and that is the only
transformation permitted. Quote at most one decimal place more than you need; do
not restate a value at a precision the result does not support."""


class NarrativeCancelled(Exception):
    """Raised when a narrative run is cancelled between model calls."""

    def __init__(self, attempts_made: int) -> None:
        super().__init__(f"Narrative cancelled after {attempts_made} model call(s).")
        self.attempts_made = attempts_made


class NarrativeAgent:
    """Creates a prose overlay that cannot silently introduce engineering numbers."""

    def __init__(
        self,
        llm: StructuredLLM,
        *,
        max_attempts: int = 2,
        absolute_tolerance: float = 0.01,
        relative_tolerance: float = 0.005,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if absolute_tolerance < 0 or relative_tolerance < 0:
            raise ValueError("Narrative numeric tolerances cannot be negative.")
        self._llm = llm
        self._max_attempts = max_attempts
        self._absolute_tolerance = absolute_tolerance
        self._relative_tolerance = relative_tolerance

    def run(
        self,
        result: DesignResult,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> NarrativeResult:
        """Generate, validate, retry once or more, then refuse unsafe prose.

        ``should_stop`` is checked before every model call. An in-flight call
        cannot be aborted from here, so cancellation is cooperative: a
        cancelled run makes no further model calls and raises
        ``NarrativeCancelled`` instead of returning prose nobody asked for.
        """

        prompt = self._prompt_for(result)
        violations: list[NumericViolation] = []
        for attempt in range(1, self._max_attempts + 1):
            if should_stop is not None and should_stop():
                raise NarrativeCancelled(attempt - 1)
            try:
                raw_json = self._llm.generate_json(
                    system_prompt=NARRATIVE_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    schema=NarrativeLLMResponse.model_json_schema(),
                    schema_name="esp_narrative",
                )
                narrative = NarrativeLLMResponse.model_validate_json(raw_json)
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                violations = [
                    NumericViolation(
                        token="<malformed-output>",
                        value=math.nan,
                        context=f"Structured narrative output was rejected: {exc}",
                    )
                ]
                continue

            violations = validate_narrative_numbers(
                narrative,
                result,
                absolute_tolerance=self._absolute_tolerance,
                relative_tolerance=self._relative_tolerance,
            )
            if not violations:
                return NarrativeResult(status="ok", narrative=narrative, attempts=attempt)

            # Make corrective regeneration explicit.  No unsafe draft escapes the
            # agent just because a later attempt fails.
            prompt = (
                f"{self._prompt_for(result)}\n\n"
                "Your prior response contained ungrounded numeric tokens: "
                + ", ".join(v.token for v in violations)
                + ". Regenerate without those numbers."
            )
        return NarrativeResult(
            status="refused",
            numeric_violations=violations,
            refusal_reason=(
                "Narrative withheld: repeated output contained numbers that could "
                "not be accounted for by the deterministic DesignResult."
            ),
            attempts=self._max_attempts,
        )

    @staticmethod
    def _prompt_for(result: DesignResult, candidate_rank: int | None = 1) -> str:
        """Build the prompt from the SAME projection the guard validates against.

        The model is shown exactly the numbers it is permitted to quote, and
        nothing else. Any divergence between this projection and the guard's
        whitelist reappears as unexplained refusals, so both call
        ``narrative_scope``.
        """
        from .rounded_facts import rounded_narrative_scope
        facts = rounded_narrative_scope(result, candidate_rank=candidate_rank)
        judgments = [judgment.model_dump(mode="json") for judgment in result.judgments]
        return (
            "DETERMINISTIC FACTS (do not edit or replace). Every number you write "
            "must appear verbatim in this object, in the same unit. Do not compute, "
            "convert, round, truncate, or infer any new number. All figures are "
            "already deterministically rounded using standard mathematical rounding:\n"
            f"{json.dumps(facts, sort_keys=True)}\n\n"
            "SEPARATE JUDGMENTS (may be described only as judgments):\n"
            f"{json.dumps(judgments, sort_keys=True)}"
        )


def validate_narrative_numbers(
    narrative: NarrativeLLMResponse,
    result: DesignResult,
    *,
    absolute_tolerance: float = 0.01,
    relative_tolerance: float = 0.005,
    candidate_rank: int | None = 1,
) -> list[NumericViolation]:
    """Return every narrative number not substantiated by the engine result.

    Delegates to :class:`~esp_agents.numeric_guard.ScopedNumericGuard`, which
    checks each token against the *narrated slice* of the result and in a
    *compatible dimension*.

    The previous implementation compared every token against a flat set of all
    numbers anywhere in the result. On a real engine run that set held 2,684
    values and admitted 61% of all integers from 1 to 2000, so a fabricated
    "58 Hz" passed because 58 happened to appear as an unrelated pressure. Scope
    and unit checking are what make this a guard rather than a formality; see
    ``numeric_guard.py`` for the full rationale and the remaining known limits.
    """

    guard = ScopedNumericGuard(
        result,
        candidate_rank=candidate_rank,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
    )
    violations: list[NumericViolation] = []
    for field, text in _narrative_fields(narrative):
        for finding in guard.check_text(text, field=field):
            violations.append(
                NumericViolation(
                    token=finding.token,
                    value=finding.value,
                    context=f"[{finding.field}] {finding.context} — {finding.reason}",
                )
            )
    return violations


def _within_tolerance(
    actual: float, allowed: float, absolute_tolerance: float, relative_tolerance: float
) -> bool:
    return abs(actual - allowed) <= max(
        absolute_tolerance, relative_tolerance * max(abs(actual), abs(allowed), 1.0)
    )


def _result_numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            yield float(value)
        return
    if isinstance(value, list):
        for child in value:
            yield from _result_numbers(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _result_numbers(child)


def _numbers_in_strings(value: Any) -> Iterable[float]:
    if isinstance(value, str):
        for match in _NUMBER_RE.finditer(value):
            try:
                yield float(match.group(0).rstrip("%").replace(",", ""))
            except ValueError:
                continue
        return
    if isinstance(value, list):
        for child in value:
            yield from _numbers_in_strings(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _numbers_in_strings(child)


def _narrative_strings(narrative: NarrativeLLMResponse) -> Iterable[str]:
    yield narrative.fact_summary
    yield from narrative.configuration_facts
    yield from narrative.validity_boundary_facts
    yield from narrative.watch_facts
    for judgment in narrative.judgments:
        yield judgment.statement


def _narrative_fields(narrative: NarrativeLLMResponse) -> Iterable[tuple[str, str]]:
    """Yield (field_label, text) so a violation can name where it appeared.

    Judgments are checked exactly as strictly as facts. A judgment is allowed to
    be an opinion; it is not allowed to contain an invented number, because a
    reader cannot tell which numbers in a document were computed and which were
    improvised.
    """
    yield "fact_summary", narrative.fact_summary
    for index, text in enumerate(narrative.configuration_facts):
        yield f"configuration_facts[{index}]", text
    for index, text in enumerate(narrative.validity_boundary_facts):
        yield f"validity_boundary_facts[{index}]", text
    for index, text in enumerate(narrative.watch_facts):
        yield f"watch_facts[{index}]", text
    for index, judgment in enumerate(narrative.judgments):
        yield f"judgments[{index}]", judgment.statement
