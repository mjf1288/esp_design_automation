"""Framework v0.6 §3.0 — the MEASURED class and the responsibility boundary.

§3.0 introduces a class of input the system is forbidden to improve:

    "The system should not attempt to improve the customer's characterization of
     complications, nor argue with supplied pressures. Its job is to show what
     the given data implies and to disclose what was taken on trust."

    "This differs from assumptions (§5.2): an assumption is made by the system
     where data is absent, whereas here data exists — its quality is simply
     outside the engineer's control."

Two design decisions in this module are deliberate and load-bearing.


Why MEASURED is not a new ``Source`` member
-------------------------------------------
``Source`` is an ordering of TRUSTWORTHINESS: it carries ``rank`` and
``base_confidence``, and exists to resolve conflicts between values. MEASURED is
a statement about RESPONSIBILITY, and the two axes are independent. A telemetry
pressure (confidence 0.97) and a customer-stated pressure (0.70) are both
MEASURED-class: the system may weight them differently but may dispute neither.
Adding ``Source.MEASURED`` would force one rank onto a class that spans the
range, and would make ``TELEMETRY`` and ``MEASURED`` mutually exclusive when they
are in fact orthogonal facts about the same number.


Why the class cannot be derived from ``Source`` alone either
------------------------------------------------------------
This is the subtle half. ``CUSTOMER_STATED`` does not imply MEASURED.

  - ``reservoir.reservoir_pressure_psi``, customer-stated -> MEASURED. §3.0: the
    system does not argue with supplied pressures.
  - ``expectations.target_rate_bpd``, customer-stated -> EXPECTATION. §3.1: "may
    be overstated or understated — they reflect the customer's intent, not
    necessarily what the well can do", and a gap against well capability "must be
    surfaced to the engineer rather than silently resolved".

The same source, the same customer, the same data sheet — and opposite
obligations. One the system must not challenge; the other it is required to
challenge. So the class is a property of the FIELD (what the quantity is), while
the responsibility owner is a property of the SOURCE (who produced the number),
and the §3.0 rule applies to the pair.

The consequence worth stating plainly: an engineer override on an EXPECTATION is
routine, and an engineer override on a MEASURED field is the responsibility
boundary being crossed. Both are legitimate acts; only the second needs
disclosing. Neither is detectable from ``Source`` on its own.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .provenance import (
    ResponsibilityClass,
    Source,
    Tracked,
    responsibility_of,
    transcription_owned_by_system,
)


class DataClass(str, Enum):
    """Framework §3 input classification. A property of the field, not the value."""

    MEASURED = "measured"  # §3.0 — the customer's factual data
    EXPECTATION = "expectation"  # §3.1 — intent and forecast
    CONSTRAINT = "constraint"  # §3.2 — hard limits
    REFERENCE = "reference"  # §3.4 — the previous installation as anchor
    DERIVED = "derived"  # a quantity §3 does not classify as input


# --- Field classification ----------------------------------------------------
#
# Longest matching prefix wins, so a specific path can opt out of its parent's
# class. Ordering in the tuple is irrelevant; specificity decides.

_FIELD_CLASSES: tuple[tuple[str, DataClass], ...] = (
    # §3.0 lists pressures, complications (composition, concentrations,
    # temperature, viscosity, solids) and test points.
    ("reservoir", DataClass.MEASURED),
    ("fluid", DataClass.MEASURED),
    ("complications", DataClass.MEASURED),
    # §3.3 is explicit: "These belong to the MEASURED class (§3.0)."
    # Geometry is a deviation survey and casing tally: unambiguously measured on
    # the customer's side. §3.2 lists geometry under CONSTRAINTS, but that table
    # is about RIGIDITY and how the limit is applied (an input filter), not about
    # who measured the hole. Custody and application are different questions.
    ("geometry", DataClass.MEASURED),
    # §3.4 — the anchor. §3.0 names test points explicitly, and a previous
    # installation's performance history is measured, not forecast.
    ("reference", DataClass.REFERENCE),
    # §3.1 — intent and forecast. The system is REQUIRED to challenge these.
    ("expectations", DataClass.EXPECTATION),
    ("trajectory", DataClass.EXPECTATION),
    # §3.2 — hard limits set by the operator's policy and equipment inventory.
    ("constraints", DataClass.CONSTRAINT),
    # Productivity index is not a §3.0 measurement even when it arrives from
    # telemetry: §5.2 treats it as a quantity to be SWEPT because error in either
    # direction is dangerous (too low starves the pump into downthrust, too high
    # overloads it into upthrust). Classifying it MEASURED would make the sweep
    # look like the system arguing with supplied data, when the sweep is the
    # framework's own instruction.
    ("reservoir.productivity_index_bpd_psi", DataClass.DERIVED),
    # §3.0 lists the measured pressures as "reservoir, intake, wellhead,
    # casing". Two of those live on the Expectations model in this codebase,
    # which groups them with genuine intent quantities like target_pip_psi and
    # desired_drawdown_psi. The framework's classification governs over the
    # model's grouping: a wellhead pressure is a gauge reading, not a wish, and
    # filing it as an EXPECTATION would license the system to argue with it.
    ("expectations.wellhead_pressure_psi", DataClass.MEASURED),
    ("expectations.casing_pressure_psi", DataClass.MEASURED),
)


def data_class_of(field_path: str) -> DataClass:
    """Classify a case field path per framework §3."""
    best: DataClass = DataClass.DERIVED
    best_len = -1
    for prefix, cls in _FIELD_CLASSES:
        if field_path == prefix or field_path.startswith(prefix + "."):
            if len(prefix) > best_len:
                best, best_len = cls, len(prefix)
    return best


# --- The trust register ------------------------------------------------------


class TrustRegisterEntry(BaseModel):
    """One value the design rests on that the system did not produce.

    §3.0 requires the system to "disclose what was taken on trust". That is a
    different list from the assumption ledger, and the difference is the point:
    the ledger says "we filled this in, here is our reasoning, challenge it",
    while this register says "this came from you, we used it as given, we are not
    in a position to check it". Merging them would imply the system stands behind
    numbers it never produced.
    """

    model_config = ConfigDict(frozen=True)

    field_path: str
    value: str
    unit: str | None = None
    data_class: str
    source: str
    confidence: float
    accuracy_owner: str
    transcription_owned_by_system: bool = False
    boundary_note: str | None = Field(
        default=None,
        description=(
            "Set where the §3.0 responsibility boundary was crossed — the "
            "system or the engineer supplied a value for a field whose accuracy "
            "belongs to the customer."
        ),
    )


def _describe_owner(
    data_class: DataClass, responsibility: ResponsibilityClass
) -> str:
    if responsibility is ResponsibilityClass.CUSTOMER:
        if data_class is DataClass.MEASURED:
            return (
                "the customer measured this; assessing its accuracy is outside "
                "the ESP design engineer's responsibility (§3.0)"
            )
        if data_class is DataClass.EXPECTATION:
            return (
                "the customer stated this as intent; it is checked against well "
                "capability rather than taken as fact (§3.1)"
            )
        if data_class is DataClass.DERIVED:
            # Productivity index is the case that forced this branch. The
            # customer supplied a number, so the row would otherwise read
            # "supplied by the customer" under a DERIVED tag, which invites the
            # reader to treat it the way §3.0 treats a gauge reading. §5.2 says
            # the opposite: PI error is dangerous in both directions, so the
            # system sweeps it rather than accepting it.
            return (
                "stated by the customer, but derived rather than measured \u2014 "
                "the system sweeps it instead of accepting the single value "
                "(\u00a75.2)"
            )
        if data_class is DataClass.CONSTRAINT:
            return (
                "a limit the customer imposed; it bounds the search rather than "
                "describing the well"
            )
        return "supplied by the customer"
    if responsibility is ResponsibilityClass.VENDOR:
        return "published by the equipment manufacturer"
    if responsibility is ResponsibilityClass.ENGINEER:
        return "set deliberately by the engineer operating this system"
    return "supplied by this system because the data was absent (§5.2)"


def _boundary_note(
    data_class: DataClass, responsibility: ResponsibilityClass
) -> str | None:
    """Flag the two ways the §3.0 boundary gets crossed.

    Neither is prohibited outright — a design has to proceed somehow when a
    pressure is missing, and an engineer may have better information than the
    data sheet. What §3.0 prohibits is doing either SILENTLY, so both produce a
    note rather than a refusal.
    """
    if data_class not in (DataClass.MEASURED, DataClass.REFERENCE):
        return None
    if responsibility is ResponsibilityClass.SYSTEM:
        return (
            "This is a §3.0 quantity the customer normally measures, but no "
            "value was supplied and the system filled it in. The design rests "
            "on a number nobody measured — this is a data gap, not data taken "
            "on trust, and it is the opposite of the §3.0 situation."
        )
    if responsibility is ResponsibilityClass.ENGINEER:
        return (
            "An engineer override replaced a §3.0 quantity whose accuracy "
            "belongs to the customer. §3.0 says the design engineer works with "
            "what is given and does not dispute it, so the substitution is "
            "recorded here: the delivered design no longer reflects the data "
            "the customer supplied."
        )
    return None


def build_trust_register(case) -> list[TrustRegisterEntry]:
    """Every externally-owned value the design rests on, plus boundary crossings.

    Walks the whole case rather than only the low-confidence fields, because
    §3.0's disclosure obligation is not conditional on confidence: a telemetry
    pressure at 0.97 was still taken on trust, and the register is what lets a
    reviewer two years later see exactly which inputs the result depended on and
    who is answerable for each.
    """
    entries: list[TrustRegisterEntry] = []

    def walk(obj, path: str) -> None:
        if isinstance(obj, Tracked):
            data_class = data_class_of(path)
            responsibility = responsibility_of(obj.source)
            note = _boundary_note(data_class, responsibility)
            # A system-supplied value on a non-§3.0 field is an assumption and
            # belongs to the assumption ledger, not here. It is included only
            # when it crossed the boundary, where the register is the only place
            # the crossing would otherwise be visible.
            if responsibility is ResponsibilityClass.SYSTEM and note is None:
                return
            entries.append(
                TrustRegisterEntry(
                    field_path=path,
                    value=f"{obj.value}",
                    unit=obj.unit,
                    data_class=data_class.value,
                    source=obj.source.value,
                    confidence=obj.confidence,
                    accuracy_owner=_describe_owner(data_class, responsibility),
                    transcription_owned_by_system=transcription_owned_by_system(
                        obj.source
                    ),
                    boundary_note=note,
                )
            )
            return
        if isinstance(obj, BaseModel):
            for name in obj.__class__.model_fields:
                walk(getattr(obj, name), f"{path}.{name}" if path else name)
            return
        if isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]")
            return
        if isinstance(obj, dict):
            for key, item in obj.items():
                walk(item, f"{path}[{key!r}]")

    walk(case, "")
    # Alphabetical order buried the point of the register: it put the electrical
    # constraints at the top and the reservoir pressure the whole design rests on
    # near the bottom. Order by class instead, MEASURED first, and float boundary
    # crossings to the head of their class -- those are the entries a reviewer
    # must not scroll past.
    _class_order = {
        DataClass.MEASURED: 0,
        DataClass.EXPECTATION: 1,
        DataClass.CONSTRAINT: 2,
        DataClass.REFERENCE: 3,
        DataClass.DERIVED: 4,
    }
    return sorted(
        entries,
        key=lambda e: (
            _class_order.get(DataClass(e.data_class), 9),
            e.boundary_note is None,
            e.field_path,
        ),
    )


# --- Bias, and the boundary it must not cross --------------------------------


class BiasRefused(Exception):
    """Raised when a conservative bias is applied to data the system does not own.

    §5's asymmetric conservatism exists to add margin to values THE SYSTEM CHOSE.
    Applying the viscosity policy's 1.25 factor to a customer's lab PVT viscosity
    is not conservatism, it is the system overwriting a measurement with a larger
    number of its own invention — precisely what §3.0 prohibits, and it would be
    invisible in the output because the value would still be reported with its
    original measured provenance.

    The exception is deliberately loud rather than a silent pass-through. A bias
    that quietly declines to apply looks identical to one that applied, and the
    caller would carry on believing it had margin it does not have.
    """


def apply_bias(policy, tracked: Tracked, basis: str) -> Tracked:
    """Apply a §5 bias policy, refusing where the value is not the system's.

    This is the enforcement point §3.0 needs and the codebase did not have: the
    bias policies were previously pure data with no application path at all, so
    there was nowhere for the boundary to be checked.
    """
    owner = responsibility_of(tracked.source)
    if owner is not ResponsibilityClass.SYSTEM:
        raise BiasRefused(
            f"cannot apply bias policy {policy.policy_id!r} to a "
            f"{tracked.source.value} value: its accuracy belongs to the "
            f"{owner.value}, and §3.0 forbids the system from improving data it "
            "did not produce. Bias applies to assumptions only."
        )
    if not isinstance(tracked.value, (int, float)) or isinstance(tracked.value, bool):
        raise BiasRefused(
            f"bias policy {policy.policy_id!r} needs a numeric value, got "
            f"{type(tracked.value).__name__}"
        )
    neutral = float(tracked.value)
    biased = policy.apply(neutral)
    return Tracked(
        value=biased,
        unit=tracked.unit,
        source=Source.ASSUMPTION,
        confidence=Source.ASSUMPTION.base_confidence,
        assumption=policy.make_assumption(neutral, basis),
        note=tracked.note,
    )
