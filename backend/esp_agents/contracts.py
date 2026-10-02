"""Pydantic contracts owned by the agentic layer.

The LLM produces these small, reviewable schemas rather than a free-form
document.  ``Case`` itself remains owned by ``esp_engine.models``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from esp_engine.models import TaskBranch


class InputClass(str, Enum):
    """Framework §3's four logic branches, including its reference anchor."""

    EXPECTATIONS = "expectations"
    CONSTRAINTS = "constraints"
    COMPLICATIONS = "complications"
    REFERENCE = "reference"


class ConstraintRigidity(str, Enum):
    ABSOLUTE = "absolute"
    HARD = "hard"
    SOFT = "soft"


class ExtractedField(BaseModel):
    """The audit record for one explicit value read from the request."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(
        description="Canonical Case path, for example expectations.target_rate_bpd"
    )
    input_class: InputClass
    rigidity: ConstraintRigidity | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str = Field(
        min_length=1,
        description="Exact verbatim substring from the supplied request",
    )
    unit: str | None = None
    classification_rationale: str = Field(min_length=1)


class IntakeLLMResponse(BaseModel):
    """Strict schema for the extraction pass.

    ``case`` intentionally excludes metadata: IDs and the raw request come from
    the caller, not from a probabilistic model.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: str
    case: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Partial Case body only. Every present tracked value must use either "
            "text_extraction with a verbatim span or assumption with full basis, "
            "rationale, bias, and unbiased_value."
        ),
    )
    fields: list[ExtractedField] = Field(default_factory=list)
    could_not_determine: list[str] = Field(default_factory=list)
    decision_gate_rationale: str = Field(min_length=1)


class IntakeIssue(BaseModel):
    """A non-exceptional validation problem returned for engineer review."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    path: str | None = None


class IntakeResult(BaseModel):
    """The intake deliverable before the deterministic engine is invoked."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    case: Any | None = Field(
        default=None,
        description="Validated esp_engine.models.Case, or None if output was malformed.",
    )
    fields: list[ExtractedField] = Field(default_factory=list)
    branch: TaskBranch | None = None
    branch_rationale: str | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
    blocking_data_requests: list[str] = Field(default_factory=list)
    could_not_determine: list[str] = Field(default_factory=list)
    issues: list[IntakeIssue] = Field(default_factory=list)
    attempts: int = 0

    @property
    def is_valid(self) -> bool:
        return self.case is not None and not self.issues


class NarrativeJudgment(BaseModel):
    """A judgment is always distinct from deterministic explanatory prose."""

    model_config = ConfigDict(frozen=True)

    statement: str = Field(min_length=1)
    references_facts: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class NarrativeLLMResponse(BaseModel):
    """Schema for a UI-separable engineer-facing narrative."""

    model_config = ConfigDict(extra="forbid")

    fact_summary: str = Field(min_length=1)
    configuration_facts: list[str] = Field(default_factory=list)
    validity_boundary_facts: list[str] = Field(default_factory=list)
    watch_facts: list[str] = Field(default_factory=list)
    judgments: list[NarrativeJudgment] = Field(default_factory=list)


class NumericViolation(BaseModel):
    """One ungrounded numeric token found after narrative generation."""

    model_config = ConfigDict(frozen=True)

    token: str
    value: float
    context: str


class NarrativeResult(BaseModel):
    """Narrative outcome, including deliberate refusal after failed retries."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["ok", "refused"] = "refused"
    narrative: NarrativeLLMResponse | None = None
    numeric_violations: list[NumericViolation] = Field(default_factory=list)
    refusal_reason: str | None = None
    attempts: int = 0
