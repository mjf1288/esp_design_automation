"""Unstructured ESP request intake with hard-stop and provenance enforcement."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from esp_engine.models import Case, CaseMetadata, ComplicationType, TaskType
from esp_engine.provenance import (
    Assumption,
    BiasDirection,
    Source,
    Tracked,
)

from .contracts import (
    ConstraintRigidity,
    ExtractedField,
    IntakeIssue,
    IntakeLLMResponse,
    IntakeResult,
    InputClass,
)
from .llm import StructuredLLM

_HARD_STOP_PATHS = {
    "expectations.target_rate_bpd",
    "geometry.casing_sections",
    "geometry.deviation_survey",
}

_EXPECTED_RIGIDITY: dict[str, ConstraintRigidity] = {
    "geometry": ConstraintRigidity.ABSOLUTE,
    "constraints.geometry": ConstraintRigidity.ABSOLUTE,
    "constraints.electrical": ConstraintRigidity.HARD,
    "constraints.mechanical": ConstraintRigidity.HARD,
    "constraints.availability": ConstraintRigidity.SOFT,
}

# The case model uses US field units.  A unit not in this map is retained only
# for text (e.g. pump model); a mapped value must use the indicated field unit.
_US_FIELD_UNITS: dict[str, set[str]] = {
    "target_rate_bpd": {"bpd"},
    "rate_bpd": {"bpd"},
    "pressure_psi": {"psi"},
    "casing_pressure_psi": {"psi"},
    "wellhead_pressure_psi": {"psi"},
    "target_pip_psi": {"psi"},
    "desired_drawdown_psi": {"psi"},
    "gor_scf_stb": {"scf/stb"},
    "setting_depth_md_ft": {"ft"},
    "depth_md_ft": {"ft"},
    "depth_tvd_ft": {"ft"},
    "bht_f": {"F"},
    "temp_f": {"F"},
    "frequency_hz": {"Hz"},
    "id_in": {"in"},
    "od_in": {"in"},
    "water_cut_frac": {"fraction"},
    "productivity_index_bpd_psi": {"bpd/psi"},
}

INTAKE_SYSTEM_PROMPT = """You are an ESP application-engineering intake extractor.
Return JSON only and obey the supplied schema. Extract values only when the request
explicitly supports them. Each extracted value must carry an exact verbatim source
span and use the source class supplied by the caller. Do not infer, estimate, convert, or fabricate the target production rate, casing
program, or deviation survey: those are blocking hard stops if absent.

Classify every supplied value as EXPECTATIONS, CONSTRAINTS (absolute, hard, or soft),
COMPLICATIONS, or REFERENCE. Use US oilfield units only. The `case` payload must use
the ESP Case field names and Tracked shape. Values derived from the request have the
caller-supplied source; assumptions, if supplied, need source `assumption` and a
complete Assumption object with basis, rationale, bias, and unbiased_value.

For an assumed GOR, temperature, or viscosity, use conservative upward bias. For
assumed water cut or productivity index use RANGE and scenario_swept=true. Setting
depth assumptions must be DEEPER and remain bounded by the provided well geometry.
Never fill a hard stop as an assumption."""


class IntakeAgent:
    """Turns dirty input into a validated, reviewable ``Case``.

    The model is allowed to identify spans and propose a typed shape.  This class,
    not the model, decides whether a span is present, an assumption is complete,
    a unit is acceptable, and a hard stop may enter the case.
    """

    def __init__(self, llm: StructuredLLM, *, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self._llm = llm
        self._max_attempts = max_attempts

    def run(
        self,
        raw_request: str,
        *,
        case_id: str,
        tenant_id: str,
        source_documents: list[str] | None = None,
        input_source: Source = Source.TEXT_EXTRACTION,
    ) -> IntakeResult:
        """Extract and validate one raw request without calculating anything."""

        if input_source in {Source.ASSUMPTION, Source.DEFAULT, Source.CATALOG}:
            raise ValueError("input_source must describe supplied evidence, not an assumption/default.")
        user_prompt = (
            "Extract this ESP request. Do not add facts that are not in it.\n\n"
            f"REQUIRED SOURCE FOR EXTRACTED VALUES: {input_source.value}\n\n"
            f"REQUEST:\n{raw_request}"
        )
        issues: list[IntakeIssue] = []
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw_json = self._llm.generate_json(
                    system_prompt=INTAKE_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    schema=IntakeLLMResponse.model_json_schema(),
                    schema_name="esp_intake",
                )
                response = IntakeLLMResponse.model_validate_json(raw_json)
                return self._to_result(
                    response,
                    raw_request=raw_request,
                    case_id=case_id,
                    tenant_id=tenant_id,
                    source_documents=source_documents or [],
                    input_source=input_source,
                    attempts=attempt,
                )
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                # A malformed extraction is expected in production.  It is never
                # converted into a partly invented Case.
                issues.append(
                    IntakeIssue(
                        code="malformed_llm_output",
                        message=f"Structured intake output was rejected: {exc}",
                    )
                )
        return IntakeResult(
            issues=issues,
            could_not_determine=[
                "No validated structured intake could be produced from this request."
            ],
            blocking_data_requests=[
                "Provide target production rate in bpd.",
                "Provide casing ID versus depth and a deviation survey, or confirm the well is vertical.",
            ],
            attempts=self._max_attempts,
        )

    def _to_result(
        self,
        response: IntakeLLMResponse,
        *,
        raw_request: str,
        case_id: str,
        tenant_id: str,
        source_documents: list[str],
        input_source: Source,
        attempts: int,
    ) -> IntakeResult:
        issues = self._validate_evidence(response, raw_request)
        if issues:
            return IntakeResult(
                fields=response.fields,
                field_confidence={f.path: f.confidence for f in response.fields},
                could_not_determine=response.could_not_determine,
                issues=issues,
                attempts=attempts,
            )

        payload = dict(response.case)
        payload["task_type"] = response.task_type
        payload["metadata"] = CaseMetadata(
            case_id=case_id,
            tenant_id=tenant_id,
            raw_request=raw_request,
            source_documents=source_documents,
        ).model_dump(mode="json")
        try:
            case = Case.model_validate(payload)
        except ValidationError as exc:
            return IntakeResult(
                fields=response.fields,
                field_confidence={f.path: f.confidence for f in response.fields},
                could_not_determine=response.could_not_determine,
                issues=[
                    IntakeIssue(
                        code="case_schema_validation",
                        message=f"Extracted data does not form a valid Case: {exc}",
                    )
                ],
                attempts=attempts,
            )

        issues = self._validate_tracked_values(
            case, raw_request, response.fields, input_source
        )
        if issues:
            return IntakeResult(
                fields=response.fields,
                field_confidence={f.path: f.confidence for f in response.fields},
                could_not_determine=response.could_not_determine,
                issues=issues,
                attempts=attempts,
            )

        # Engine model defaults are made explicit assumptions here.  That makes
        # the review surface honest instead of silently treating defaults as facts.
        case = self._replace_defaults_with_assumptions(case)
        missing = case.missing_hard_stops()
        requests = [_data_request(item) for item in missing]
        cannot_determine = _dedupe([*response.could_not_determine, *missing])
        return IntakeResult(
            case=case,
            fields=response.fields,
            branch=case.branch,
            branch_rationale=case.branch_rationale,
            field_confidence={f.path: f.confidence for f in response.fields},
            blocking_data_requests=requests,
            could_not_determine=cannot_determine,
            attempts=attempts,
        )

    def _validate_evidence(
        self, response: IntakeLLMResponse, raw_request: str
    ) -> list[IntakeIssue]:
        issues: list[IntakeIssue] = []
        evidence_by_path = {field.path: field for field in response.fields}
        if len(evidence_by_path) != len(response.fields):
            issues.append(
                IntakeIssue(
                    code="duplicate_field_evidence",
                    message="Each extracted Case path may have one evidence record.",
                )
            )
        for field in response.fields:
            if field.source_span not in raw_request:
                issues.append(
                    IntakeIssue(
                        code="unverifiable_source_span",
                        path=field.path,
                        message="The claimed source span does not occur verbatim in the request.",
                    )
                )
            if field.input_class is InputClass.CONSTRAINTS and field.rigidity is None:
                issues.append(
                    IntakeIssue(
                        code="missing_constraint_rigidity",
                        path=field.path,
                        message="Every CONSTRAINTS classification needs absolute, hard, or soft rigidity.",
                    )
                )
            if field.input_class is not InputClass.CONSTRAINTS and field.rigidity is not None:
                issues.append(
                    IntakeIssue(
                        code="unexpected_nonconstraint_rigidity",
                        path=field.path,
                        message="Rigidity is defined only for CONSTRAINTS inputs.",
                    )
                )
            expected = _rigidity_for_path(field.path)
            if expected is not None and field.rigidity is not expected:
                issues.append(
                    IntakeIssue(
                        code="incorrect_constraint_rigidity",
                        path=field.path,
                        message=(
                            f"{field.path} must be classified as {expected.value}, "
                            f"not {field.rigidity!s}."
                        ),
                    )
                )
            leaf_name = field.path.rsplit(".", 1)[-1].split("[", 1)[0]
            allowed = _US_FIELD_UNITS.get(leaf_name)
            if field.unit is not None and allowed is not None and field.unit not in allowed:
                issues.append(
                    IntakeIssue(
                        code="non_us_or_unexpected_unit",
                        path=field.path,
                        message=(
                            f"{field.path} must use one of {sorted(allowed)}; "
                            f"received {field.unit!r}."
                        ),
                    )
                )

        expected_paths = set(_draft_value_paths(response.case))
        # task_type drives Decision Gate 1 rather than a §3 input class.
        missing_evidence = expected_paths - set(evidence_by_path)
        extra_evidence = set(evidence_by_path) - expected_paths
        for path in sorted(missing_evidence):
            issues.append(
                IntakeIssue(
                    code="unclassified_extracted_value",
                    path=path,
                    message="Every value supplied in the case draft needs §3 classification.",
                )
            )
        for path in sorted(extra_evidence):
            issues.append(
                IntakeIssue(
                    code="evidence_without_case_value",
                    path=path,
                    message="Field evidence names no value in the case draft.",
                )
            )
        return issues

    def _validate_tracked_values(
        self,
        case: Case,
        raw_request: str,
        evidence: list[ExtractedField],
        input_source: Source,
    ) -> list[IntakeIssue]:
        issues: list[IntakeIssue] = []
        evidence_by_path = {field.path: field for field in evidence}
        for path, tracked in _walk_tracked(case):
            if tracked.source is input_source:
                if not tracked.extracted_from or tracked.extracted_from not in raw_request:
                    issues.append(
                        IntakeIssue(
                            code="unverifiable_tracked_span",
                            path=path,
                            message="A supplied Case value lacks a valid source span.",
                        )
                    )
                field = evidence_by_path.get(path)
                if field is not None and (
                    field.source_span != tracked.extracted_from
                    or not _within_confidence_tolerance(field.confidence, tracked.confidence)
                    or (field.unit is not None and field.unit != tracked.unit)
                ):
                    issues.append(
                        IntakeIssue(
                            code="evidence_provenance_mismatch",
                            path=path,
                            message=(
                                "Field evidence must exactly match the Tracker's "
                                "source span and unit and must carry the same confidence."
                            ),
                        )
                    )
            elif tracked.source is Source.ASSUMPTION:
                issues.extend(_validate_assumption(path, tracked))
            elif tracked.source is Source.DEFAULT:
                # Defaults are normalized after validation, never exposed as facts.
                continue
            else:
                issues.append(
                    IntakeIssue(
                        code="unsupported_intake_source",
                        path=path,
                        message=(
                            f"This intake is configured for {input_source.value} evidence; "
                            "a value may use that source or an explicit assumption only."
                        ),
                    )
                )
        target = case.expectations.target_rate_bpd
        if target is not None and target.source in {Source.ASSUMPTION, Source.DEFAULT}:
            issues.append(
                IntakeIssue(
                    code="assumed_hard_stop",
                    path="expectations.target_rate_bpd",
                    message="Target production rate is a hard stop and cannot be assumed.",
                )
            )
        return issues

    @staticmethod
    def _replace_defaults_with_assumptions(case: Case) -> Case:
        """Promote engine defaults to explicit reviewable assumptions."""

        def transform(obj: Any, path: str = "") -> Any:
            if isinstance(obj, Tracked):
                if obj.source is Source.DEFAULT:
                    return Tracked.assumed(
                        obj.value,
                        Assumption(
                            basis="ESP engine default pending customer confirmation",
                            bias=BiasDirection.NEUTRAL,
                            rationale=(
                                f"No value was supplied for {path}; the engine default "
                                "is retained only as an explicit review item."
                            ),
                            unbiased_value=obj.value,
                            policy_id="engine_default_explicit",
                        ),
                        unit=obj.unit,
                        confidence=Source.DEFAULT.base_confidence,
                    )
                return obj
            if isinstance(obj, list):
                return [transform(value, f"{path}[{index}]") for index, value in enumerate(obj)]
            if hasattr(obj, "model_dump"):
                data = {
                    name: transform(getattr(obj, name), f"{path}.{name}" if path else name)
                    for name in obj.__class__.model_fields
                }
                return obj.__class__.model_validate(data)
            return obj

        return transform(case)


def _draft_value_paths(value: Any, path: str = "") -> Iterable[str]:
    """Yield one path per explicit source value in the model's partial case draft."""

    if isinstance(value, Mapping):
        if "value" in value and set(value).intersection(
            {"source", "confidence", "extracted_from", "assumption", "unit"}
        ):
            if path:
                yield path
            return
        for key, child in value.items():
            if key in {"metadata", "task_type"}:
                continue
            child_path = f"{path}.{key}" if path else key
            yield from _draft_value_paths(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _draft_value_paths(child, f"{path}[{index}]")
    elif path:
        yield path


def _walk_tracked(value: Any, path: str = "") -> Iterable[tuple[str, Tracked[Any]]]:
    if isinstance(value, Tracked):
        yield path, value
        return
    if hasattr(value.__class__, "model_fields"):
        for name in value.__class__.model_fields:
            child_path = f"{path}.{name}" if path else name
            yield from _walk_tracked(getattr(value, name), child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_tracked(child, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_tracked(child, f"{path}[{key!r}]")


def _validate_assumption(path: str, tracked: Tracked[Any]) -> list[IntakeIssue]:
    assumption = tracked.assumption
    if assumption is None or not assumption.basis.strip() or not assumption.rationale.strip():
        return [
            IntakeIssue(
                code="incomplete_assumption",
                path=path,
                message="Assumed values need basis and rationale, not just a source label.",
            )
        ]
    if path in _HARD_STOP_PATHS:
        return [
            IntakeIssue(
                code="assumed_hard_stop",
                path=path,
                message=f"{path} is a §5.1 hard stop and must never be assumed.",
            )
        ]
    rules: dict[str, tuple[BiasDirection, bool]] = {
        "fluid.gor_scf_stb": (BiasDirection.UPWARD, False),
        "reservoir.bht_f": (BiasDirection.UPWARD, False),
        "fluid.oil_viscosity_cp": (BiasDirection.UPWARD, False),
        "expectations.setting_depth_md_ft": (BiasDirection.DEEPER, False),
        "fluid.water_cut_frac": (BiasDirection.RANGE, True),
        "reservoir.productivity_index_bpd_psi": (BiasDirection.RANGE, True),
    }
    expected = rules.get(path)
    if expected is None:
        return []
    direction, scenario_swept = expected
    issues: list[IntakeIssue] = []
    if assumption.bias is not direction:
        issues.append(
            IntakeIssue(
                code="unsafe_assumption_bias",
                path=path,
                message=f"{path} requires {direction.value} bias under the conservatism policy.",
            )
        )
    if assumption.unbiased_value is None:
        issues.append(
            IntakeIssue(
                code="missing_unbiased_value",
                path=path,
                message="A conservatively biased assumption must record its neutral value.",
            )
        )
    if assumption.scenario_swept is not scenario_swept:
        issues.append(
            IntakeIssue(
                code="incorrect_scenario_sweep",
                path=path,
                message=f"{path} must set scenario_swept={scenario_swept}.",
            )
        )
    if (
        direction is BiasDirection.UPWARD
        and isinstance(tracked.value, (int, float))
        and isinstance(assumption.unbiased_value, (int, float))
        and tracked.value < assumption.unbiased_value
    ):
        issues.append(
            IntakeIssue(
                code="nonconservative_assumed_value",
                path=path,
                message="An upward-biased assumption may not be below its neutral value.",
            )
        )
    return issues


def _rigidity_for_path(path: str) -> ConstraintRigidity | None:
    for prefix, rigidity in _EXPECTED_RIGIDITY.items():
        if path == prefix or path.startswith(f"{prefix}."):
            return rigidity
    if path.startswith("geometry."):
        return ConstraintRigidity.ABSOLUTE
    return None


def _data_request(missing: str) -> str:
    if missing.startswith("target production rate"):
        return "What target total-liquid production rate is required, in bpd?"
    if missing.startswith("casing program"):
        return "Provide casing ID (and drift ID if available) versus measured depth."
    if missing.startswith("deviation survey"):
        return "Provide the deviation survey or explicitly confirm that the well is vertical."
    return f"Provide: {missing}."


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _within_confidence_tolerance(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9
