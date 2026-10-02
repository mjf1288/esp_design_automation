"""Provenance tracking — the backbone of the fact/judgment separation.

Framework refs: §5.2 (assumptions must be explicit), §8.2 (every empirical rule
stored with context), §10 (text-extracted data flagged as less certain than
telemetry), §5 (asymmetric conservatism).

Every soft input to the engine is a ``Tracked`` value. It carries not just a
number but where the number came from, how much to trust it, and — if it was
assumed — what the assumption was, which direction it was biased, and why.

This is what lets the output say

    "GOR assumed 800 scf/stb (regional typical 550, biased upward per
     gassy-reservoir policy)"

instead of silently designing against a number nobody chose deliberately.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class Source(str, Enum):
    """Where a value came from, ordered by trustworthiness.

    Framework §10: "The engineer said 8 months" and "the system logged 243 days"
    are facts of different weight. That ordering is encoded here and is
    machine-comparable via ``Source.rank``.
    """

    TELEMETRY = "telemetry"  # measured, logged by a system
    MEASUREMENT = "measurement"  # a real test point / survey
    CATALOG = "catalog"  # vendor published data
    ENGINEER_OVERRIDE = "engineer_override"  # a human deliberately set this
    CUSTOMER_STATED = "customer_stated"  # customer said so in a data sheet
    REPORT = "report"  # from a document (teardown, well report)
    TEXT_EXTRACTION = "text_extraction"  # NLP-extracted from prose
    ASSUMPTION = "assumption"  # we filled it in
    DEFAULT = "default"  # system default, nobody chose it

    @property
    def rank(self) -> int:
        """Higher is more trustworthy. Used to resolve conflicts and to weight
        empirical observations."""
        return _SOURCE_RANK[self]

    @property
    def base_confidence(self) -> float:
        """Default confidence for a value from this source, before any
        source-specific adjustment."""
        return _SOURCE_BASE_CONFIDENCE[self]

    @property
    def is_derived(self) -> bool:
        """True if a human or a model supplied this rather than an instrument."""
        return self in {
            Source.TEXT_EXTRACTION,
            Source.ASSUMPTION,
            Source.DEFAULT,
        }


_SOURCE_RANK: dict[Source, int] = {
    Source.TELEMETRY: 100,
    Source.MEASUREMENT: 90,
    Source.CATALOG: 85,
    Source.ENGINEER_OVERRIDE: 80,
    Source.CUSTOMER_STATED: 60,
    Source.REPORT: 50,
    Source.TEXT_EXTRACTION: 30,
    Source.ASSUMPTION: 20,
    Source.DEFAULT: 10,
}

_SOURCE_BASE_CONFIDENCE: dict[Source, float] = {
    Source.TELEMETRY: 0.97,
    Source.MEASUREMENT: 0.92,
    Source.CATALOG: 0.90,
    Source.ENGINEER_OVERRIDE: 0.85,
    Source.CUSTOMER_STATED: 0.70,
    Source.REPORT: 0.60,
    Source.TEXT_EXTRACTION: 0.40,
    Source.ASSUMPTION: 0.30,
    Source.DEFAULT: 0.15,
}


class ResponsibilityClass(str, Enum):
    """Who is answerable for a value's accuracy.

    Deliberately has no ``UNKNOWN`` member. Every ``Source`` maps to an owner,
    and ``test_every_source_has_an_owner`` fails if a new ``Source`` is added
    without deciding whose fault it is when the number is wrong. An unattributed
    value is exactly what §3.0 exists to prevent.
    """

    CUSTOMER = "customer"  # measured or asserted on the operator's side
    VENDOR = "vendor"  # published in a manufacturer datasheet
    ENGINEER = "engineer"  # the person operating this system set it
    SYSTEM = "system"  # this software supplied it because data was absent


_RESPONSIBILITY: dict[Source, ResponsibilityClass] = {
    Source.TELEMETRY: ResponsibilityClass.CUSTOMER,
    Source.MEASUREMENT: ResponsibilityClass.CUSTOMER,
    Source.CUSTOMER_STATED: ResponsibilityClass.CUSTOMER,
    Source.REPORT: ResponsibilityClass.CUSTOMER,
    # Text extraction is the one split case: the CONTENT is the customer's, the
    # TRANSCRIPTION is ours. Ownership is recorded as the customer's because
    # that is who measured the quantity, and the separate transcription risk is
    # surfaced by ``transcription_owned_by_system`` rather than by pretending the
    # system measured the well.
    Source.TEXT_EXTRACTION: ResponsibilityClass.CUSTOMER,
    Source.CATALOG: ResponsibilityClass.VENDOR,
    Source.ENGINEER_OVERRIDE: ResponsibilityClass.ENGINEER,
    Source.ASSUMPTION: ResponsibilityClass.SYSTEM,
    Source.DEFAULT: ResponsibilityClass.SYSTEM,
}


def responsibility_of(source: Source) -> ResponsibilityClass:
    return _RESPONSIBILITY[source]


def transcription_owned_by_system(source: Source) -> bool:
    """True where the system mediated the reading of someone else's number.

    An extracted pressure can be wrong two ways: the customer mismeasured it, or
    we misread their document. Only the second is ours to fix, and it is the one
    a reviewer can actually check — which is why ``Tracked`` already refuses a
    ``TEXT_EXTRACTION`` value with no verbatim span.
    """
    return source is Source.TEXT_EXTRACTION


_CUSTOMER_OWNED_SOURCES: frozenset[Source] = frozenset(
    {
        Source.TELEMETRY,
        Source.MEASUREMENT,
        Source.CUSTOMER_STATED,
        Source.REPORT,
        Source.TEXT_EXTRACTION,
    }
)


class BiasDirection(str, Enum):
    """Direction an assumption was deliberately biased.

    Framework §5: "assumptions should not err toward the middle but toward
    margin. Underestimating gas kills the installation; overestimating it merely
    adds gas handling."
    """

    UPWARD = "upward"  # assume higher than typical (gas, temperature, viscosity)
    DOWNWARD = "downward"  # assume lower than typical
    DEEPER = "deeper"  # setting depth: deeper is safer, bounded by geometry
    NEUTRAL = "neutral"  # no defensible bias direction
    RANGE = "range"  # both directions dangerous -> must be scenario-swept


class Assumption(BaseModel):
    """A deliberately filled-in value, with its full justification.

    ``unbiased_value`` is retained so the report can show the engineer both the
    neutral estimate and the margin that was added. Hidden conservatism is as
    unhelpful as hidden optimism — the engineer needs to see the size of the
    cushion to judge it.
    """

    model_config = ConfigDict(frozen=True)

    basis: str = Field(
        description="Where the assumption came from, e.g. 'regional typical for "
        "West Siberia carbonate' or 'previous installation on this well'"
    )
    bias: BiasDirection = BiasDirection.NEUTRAL
    rationale: str = Field(
        description="Why this bias direction, in the engineer's language"
    )
    unbiased_value: Any | None = Field(
        default=None,
        description="The neutral estimate before conservative bias was applied",
    )
    policy_id: str | None = Field(
        default=None,
        description="Identifier of the BiasPolicy that produced this, for audit",
    )
    scenario_swept: bool = Field(
        default=False,
        description="True if this parameter is handled by scenario range rather "
        "than a single assumed value (framework §5.2: water cut)",
    )

    def describe(self) -> str:
        parts = [f"assumed from {self.basis}"]
        if self.bias is not BiasDirection.NEUTRAL:
            parts.append(f"biased {self.bias.value}")
            if self.unbiased_value is not None:
                parts.append(f"(neutral estimate {self.unbiased_value})")
        if self.scenario_swept:
            parts.append("swept across scenario range")
        return "; ".join(parts)


class Tracked(BaseModel, Generic[T]):
    """A value plus everything needed to judge how much to trust it.

    Construct via the classmethods rather than the raw initializer — they set a
    coherent default confidence from the source rank.
    """

    model_config = ConfigDict(frozen=True)

    value: T
    unit: str | None = None
    source: Source = Source.DEFAULT
    confidence: float = Field(default=0.15, ge=0.0, le=1.0)
    assumption: Assumption | None = None
    extracted_from: str | None = Field(
        default=None,
        description="Verbatim span from the source document this was read from. "
        "Required for TEXT_EXTRACTION: no span, no value.",
    )
    source_document_ref: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _span_required_for_extraction(self) -> "Tracked[T]":
        """A field validator was insufficient here.

        Pydantic runs a field validator only when the field is present in the
        input, so ``extracted_from=None`` was rejected while OMITTING the
        argument entirely passed silently -- the guard fired only for callers who
        were already thinking about the span. That is backwards: the caller who
        forgets the field is exactly the one the rule exists to stop. A model
        validator runs unconditionally.
        """
        if self.assumption is not None and self.source in _CUSTOMER_OWNED_SOURCES:
            raise ValueError(
                f"a {self.source.value} value must not carry an Assumption: "
                "§3.0 forbids the system from improving the customer's data. "
                "Record it as given and disclose it in the trust register, or "
                "change the source to ASSUMPTION if the system really did "
                "choose the number."
            )
        if self.source is Source.TEXT_EXTRACTION and not self.extracted_from:
            raise ValueError(
                "TEXT_EXTRACTION values must carry extracted_from (the verbatim "
                "source span). An extracted value with no traceable span cannot "
                "be reviewed and must not enter a design."
            )
        return self

    # --- Constructors --------------------------------------------------------

    @classmethod
    def measured(
        cls,
        value: T,
        unit: str | None = None,
        source: Source = Source.TELEMETRY,
        note: str | None = None,
    ) -> "Tracked[T]":
        return cls(
            value=value,
            unit=unit,
            source=source,
            confidence=source.base_confidence,
            note=note,
        )

    @classmethod
    def stated(
        cls, value: T, unit: str | None = None, note: str | None = None
    ) -> "Tracked[T]":
        """Customer told us, e.g. on a data sheet."""
        return cls(
            value=value,
            unit=unit,
            source=Source.CUSTOMER_STATED,
            confidence=Source.CUSTOMER_STATED.base_confidence,
            note=note,
        )

    @classmethod
    def extracted(
        cls,
        value: T,
        span: str,
        unit: str | None = None,
        document_ref: str | None = None,
        confidence: float | None = None,
    ) -> "Tracked[T]":
        """NLP-extracted from unstructured text (framework §10, mode 3)."""
        return cls(
            value=value,
            unit=unit,
            source=Source.TEXT_EXTRACTION,
            confidence=(
                confidence
                if confidence is not None
                else Source.TEXT_EXTRACTION.base_confidence
            ),
            extracted_from=span,
            source_document_ref=document_ref,
        )

    @classmethod
    def assumed(
        cls,
        value: T,
        assumption: Assumption,
        unit: str | None = None,
        confidence: float | None = None,
    ) -> "Tracked[T]":
        return cls(
            value=value,
            unit=unit,
            source=Source.ASSUMPTION,
            confidence=(
                confidence if confidence is not None else Source.ASSUMPTION.base_confidence
            ),
            assumption=assumption,
        )

    @classmethod
    def overridden(
        cls,
        value: T,
        unit: str | None = None,
        note: str | None = None,
    ) -> "Tracked[T]":
        """An engineer deliberately set this value."""
        return cls(
            value=value,
            unit=unit,
            source=Source.ENGINEER_OVERRIDE,
            confidence=Source.ENGINEER_OVERRIDE.base_confidence,
            note=note,
        )

    @classmethod
    def from_catalog(cls, value: T, unit: str | None = None, ref: str | None = None):
        return cls(
            value=value,
            unit=unit,
            source=Source.CATALOG,
            confidence=Source.CATALOG.base_confidence,
            source_document_ref=ref,
        )

    # --- Behaviour -----------------------------------------------------------

    @property
    def is_assumed(self) -> bool:
        return self.source in {Source.ASSUMPTION, Source.DEFAULT}

    @property
    def needs_review(self) -> bool:
        """Values an engineer should look at before releasing a design."""
        return self.source.is_derived or self.confidence < 0.5

    def describe(self) -> str:
        """One-line human explanation, for reports and tooltips."""
        base = f"{self.value}" + (f" {self.unit}" if self.unit else "")
        if self.assumption is not None:
            return f"{base} — {self.assumption.describe()}"
        if self.extracted_from:
            snippet = self.extracted_from.strip()
            if len(snippet) > 80:
                snippet = snippet[:77] + "..."
            return f'{base} — extracted from text: "{snippet}"'
        return f"{base} — {self.source.value}"

    def with_value(self, value: T) -> "Tracked[T]":
        """Copy carrying a new value and identical provenance. Used by bias
        application and unit normalization, which change the number without
        changing where it came from."""
        return self.model_copy(update={"value": value})


class BiasPolicy(BaseModel):
    """Rule for how to bias an assumption for a given parameter.

    Framework §5. Kept as data rather than scattered ``if`` statements so the
    complete conservatism policy of the system is inspectable in one place, can
    be tuned per tenant, and appears in the audit trail.
    """

    model_config = ConfigDict(frozen=True)

    policy_id: str
    parameter: str
    direction: BiasDirection
    factor: float = Field(
        default=1.0,
        description="Multiplier applied to the neutral estimate. 1.45 on GOR "
        "means assume 45% more gas than typical.",
    )
    applies_when: str = Field(
        default="always", description="Human-readable trigger condition"
    )
    rationale: str = ""

    def apply(self, neutral_value: float) -> float:
        if self.direction is BiasDirection.UPWARD:
            return neutral_value * self.factor
        if self.direction is BiasDirection.DOWNWARD:
            return neutral_value / self.factor
        return neutral_value

    def make_assumption(self, neutral_value: float, basis: str) -> Assumption:
        return Assumption(
            basis=basis,
            bias=self.direction,
            rationale=self.rationale,
            unbiased_value=neutral_value,
            policy_id=self.policy_id,
            scenario_swept=self.direction is BiasDirection.RANGE,
        )


# --- Default bias policies ---------------------------------------------------
#
# Framework §5.2 table plus §3.3 ("for gassy reservoirs, bias high"). These are
# defaults; a tenant can override them, and whichever policy was used is
# recorded on every resulting assumption.

DEFAULT_BIAS_POLICIES: dict[str, BiasPolicy] = {
    "gor_gassy": BiasPolicy(
        policy_id="gor_gassy",
        parameter="gor_scf_stb",
        direction=BiasDirection.UPWARD,
        factor=1.45,
        applies_when="reservoir flagged gassy, or GOR not measured",
        rationale=(
            "Underestimating gas kills the installation; overestimating it only "
            "adds gas handling cost. Asymmetric downside justifies a hard "
            "upward bias (framework §5)."
        ),
    ),
    "gor_default": BiasPolicy(
        policy_id="gor_default",
        parameter="gor_scf_stb",
        direction=BiasDirection.UPWARD,
        factor=1.20,
        applies_when="GOR inferred from offset wells or previous installation",
        rationale="Gas underestimation is the dominant ESP failure mode; bias up modestly.",
    ),
    "temperature": BiasPolicy(
        policy_id="temperature",
        parameter="bht_f",
        direction=BiasDirection.UPWARD,
        factor=1.05,
        applies_when="bottomhole temperature estimated from gradient",
        rationale=(
            "Motor and cable ratings derate with temperature; a low temperature "
            "assumption yields an under-rated string."
        ),
    ),
    "viscosity": BiasPolicy(
        policy_id="viscosity",
        parameter="oil_viscosity_cp",
        direction=BiasDirection.UPWARD,
        factor=1.25,
        applies_when="viscosity from correlation rather than lab PVT",
        rationale=(
            "Viscosity degrades head and efficiency and raises power. Optimism "
            "here produces a pump that cannot make rate."
        ),
    ),
    "productivity_index": BiasPolicy(
        policy_id="productivity_index",
        parameter="pi_bpd_psi",
        direction=BiasDirection.RANGE,
        factor=1.0,
        applies_when="PI not established by a multi-rate test",
        rationale=(
            "PI errors are dangerous in BOTH directions: too low starves the "
            "pump into downthrust, too high overloads it into upthrust. There is "
            "no safe single value, so PI must be swept across a scenario range "
            "rather than assumed."
        ),
    ),
    "setting_depth": BiasPolicy(
        policy_id="setting_depth",
        parameter="setting_depth_ft",
        direction=BiasDirection.DEEPER,
        factor=1.0,
        applies_when="setting depth not specified by customer",
        rationale=(
            "Deeper gives more submergence and lower free gas at intake, but is "
            "hard-bounded by the deviation survey and perforation depth "
            "(framework §5.2)."
        ),
    ),
    "water_cut": BiasPolicy(
        policy_id="water_cut",
        parameter="water_cut_frac",
        direction=BiasDirection.RANGE,
        factor=1.0,
        applies_when="always",
        rationale=(
            "Water cut changes over the well's life by definition, so it is "
            "handled by scenario trajectory rather than a point assumption "
            "(framework §5.2)."
        ),
    ),
}


class Judgment(BaseModel):
    """An interpretation layered on top of computed facts.

    Framework §7: "empirical knowledge does not overwrite the calculation; it
    adds judgment on top of it."

    A ``Judgment`` references facts by ID and can never modify them. The API
    serializes judgments in a separate block from facts, and the UI styles them
    differently, so an engineer can always tell computation from opinion.
    """

    model_config = ConfigDict(frozen=True)

    judgment_id: str
    statement: str
    kind: Literal["risk", "mitigation", "context", "caveat", "recommendation"]
    references_facts: list[str] = Field(
        default_factory=list,
        description="IDs of DesignResult facts this judgment interprets",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_basis: str = Field(
        description="Human-readable explanation of the confidence number, e.g. "
        "'3 observations, 1 field, 2 unresolved confounders'"
    )
    supporting_rule_ids: list[str] = Field(default_factory=list)
    severity: Literal["info", "low", "moderate", "high", "critical"] = "info"

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < 0.45
