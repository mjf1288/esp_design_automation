"""API RP 11S2 acceptance tolerances applied to the catalog curve (framework §6B.2).

Framework §6B.2 makes a specific claim: the catalog curve is *not* an unknown,
it is a **known approximation with bounded error**, because API RP 11S2 (2nd
ed., 1997) bounds how far a manufactured pump may deviate from its published
curve.  Calculating from the catalog is therefore valid by default, and the
system's real job is deciding **when** loading a unit test report is worth the
engineer's time.

Two things follow, and the second is the one that does the work.

**The band is a property of the curve's provenance, not of the pump.**  RP 11S2
bounds the deviation of a *manufactured unit* from *the manufacturer's
published curve*.  It says nothing whatever about how far our own reconstruction
of that curve sits from the published one.  Those are two different errors and
they add.  Every pump in this catalog is a ``parametric_estimate`` -- the curve
was fitted, not transcribed -- so for this catalog the RP 11S2 band is simply
not available, and asserting it would convert an unbounded, unquantified error
into a falsely bounded one.  That is worse than saying nothing, because §6B.2's
whole value proposition is the word *bounded*.  ``curve_basis_of`` therefore
gates the entire assessment, and the permissive state is never the default.

**Materiality is a decision flip, not a distance.**  "Within 5% of a boundary"
is a proximity heuristic; it fires on numbers, not on consequences.  What the
engineer actually needs to know is whether some decision the design rests on
changes when the curve is moved anywhere inside a band the standard says is
*acceptable*.  Each channel below therefore perturbs the curve by the tolerance
the standard assigns to that quantity, re-runs the specific decision that reads
it, and reports material only if the decision comes out differently.

Nothing here rejects, penalizes, or scores a candidate.  A tolerance band is
uncertainty about a number, not a defect in a design; turning it into a soft
violation would score candidates on how well documented they are, which is the
same mistake as scoring on missing catalog data (§5.1).  The output is a
recommendation and a data request.
"""

from __future__ import annotations

from enum import Enum
from math import ceil
from typing import TYPE_CHECKING, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .catalog import PumpModel
from .config import DesignThresholds, ScoringObjective, ThrustZoneMethod
from .curves import ScaledCurve, classify_zone
from .models import OperatingZone

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .results import CandidateResult, CellResult


# =============================================================================
# The standard
# =============================================================================


class AcceptanceStandard(BaseModel):
    """A published acceptance-test tolerance set, with its scope attached.

    The scope travels with the numbers deliberately.  A tolerance quoted without
    the population it was written for is how a new-equipment acceptance
    criterion silently becomes a refurbished-equipment guarantee.
    """

    model_config = ConfigDict(frozen=True)

    citation: str
    head_frac: float
    rate_frac: float
    bhp_frac: float
    efficiency_frac: float
    efficiency_unilateral: bool
    efficiency_applies_at: str
    applies_over: str
    acceptance_scope: str


API_RP_11S2 = AcceptanceStandard(
    citation="API RP 11S2, 2nd ed. (1997)",
    head_frac=0.05,
    rate_frac=0.05,
    bhp_frac=0.08,
    efficiency_frac=0.10,
    efficiency_unilateral=True,
    efficiency_applies_at="best efficiency point",
    applies_over="operating range",
    acceptance_scope="equipment sold as new",
)

SCOPE_DISCLOSURE = (
    "API RP 11S2 is an acceptance criterion for equipment sold as new. Repair "
    "shops test against the same catalog curves, but the acceptance criterion "
    "for a repaired or refurbished unit may be a corporate standard rather than "
    "this one, and must be confirmed with the customer before these bounds are "
    "relied on for a rebuilt pump (§6B.2)."
)

RATE_MODEL_DISCLOSURE = (
    "The rate tolerance is applied as a uniform shift of the whole curve along "
    "the rate axis, which moves the BEP and the thrust boundaries together. The "
    "standard bounds each measured point independently; a uniform shift is one "
    "realisation of that, chosen because it is the one that actually moves a "
    "zone label. Independent per-point error would widen the band further, so "
    "this is not a conservative bound on zone stability -- it is a lower bound "
    "on how unstable the zone label can be."
)

PROPAGATION_DISCLOSURE = (
    "Head, rate and BHP deviations are not propagated into the candidate score. "
    "Doing so would require re-running the full trajectory against a perturbed "
    "curve for every candidate. Ranking stability is therefore assessed against "
    "the efficiency tolerance only, and a ranking reported stable here may still "
    "be sensitive to the head and rate bands."
)


# =============================================================================
# Curve provenance -- what the band may be attached to
# =============================================================================


class CurveBasis(str, Enum):
    """Where the curve being calculated from actually came from.

    There is no member meaning "assume it is fine".  A record that does not say
    where its curve came from lands in ``UNDECLARED`` and gets no band, for the
    same reason ``MagnetGate`` has no ``pass`` member: a check that cannot be
    performed must not read as a check that cleared.
    """

    VENDOR_PUBLISHED = "vendor_published"
    PARAMETRIC_ESTIMATE = "parametric_estimate"
    UNIT_TEST_REPORT = "unit_test_report"
    UNDECLARED = "undeclared"


# Substring match against ``data_quality``, most specific first.  Ordered rather
# than a dict because "vendor_test_report" must not be caught by "vendor".
_BASIS_MARKERS: tuple[tuple[str, CurveBasis], ...] = (
    ("unit_test_report", CurveBasis.UNIT_TEST_REPORT),
    ("test_report", CurveBasis.UNIT_TEST_REPORT),
    ("estimate", CurveBasis.PARAMETRIC_ESTIMATE),
    ("estimated", CurveBasis.PARAMETRIC_ESTIMATE),
    ("interpolat", CurveBasis.PARAMETRIC_ESTIMATE),
    ("digitized_from_datasheet", CurveBasis.VENDOR_PUBLISHED),
    ("vendor_published", CurveBasis.VENDOR_PUBLISHED),
    ("vendor_datasheet", CurveBasis.VENDOR_PUBLISHED),
    ("manufacturer_catalog", CurveBasis.VENDOR_PUBLISHED),
)


def curve_basis_of(pump: PumpModel) -> CurveBasis:
    """Classify the curve's provenance from the catalog record's own claim."""
    marker = (pump.data_quality or "").strip().lower()
    if not marker:
        return CurveBasis.UNDECLARED
    for needle, basis in _BASIS_MARKERS:
        if needle in marker:
            return basis
    return CurveBasis.UNDECLARED


def band_applies(basis: CurveBasis) -> bool:
    """Only a curve transcribed from a vendor publication carries the band."""
    return basis is CurveBasis.VENDOR_PUBLISHED


# =============================================================================
# Findings
# =============================================================================


class ToleranceChannel(str, Enum):
    """A decision in this engine that reads a tolerance-bounded quantity."""

    STAGE_COUNT = "stage_count"
    HEAD_MARGIN = "head_margin"
    OPERATING_ZONE = "operating_zone"
    MOTOR_LOADING = "motor_loading"
    EFFICIENCY_RANKING = "efficiency_ranking"


class ChannelEffect(str, Enum):
    """What the band does to this channel's decision."""

    FLIPS = "flips"  # a value inside the band changes the decision
    HOLDS = "holds"  # no value inside the band changes it
    NO_CONSUMER = "no_consumer"  # nothing in this run reads the quantity
    NOT_COMPUTABLE = "not_computable"  # the input the channel needs is absent


class ChannelFinding(BaseModel):
    """One decision, tested against the band the standard assigns to its input."""

    model_config = ConfigDict(frozen=True)

    channel: ToleranceChannel
    effect: ChannelEffect
    band_frac: float
    flip_at_frac: float | None = Field(
        default=None,
        description="Smallest deviation, as a fraction of the nominal, that "
        "changes the decision. Compare against band_frac: below it, the "
        "standard permits a compliant pump to land on the other side.",
    )
    at_month: float | None = None
    detail: str

    @property
    def material(self) -> bool:
        return self.effect is ChannelEffect.FLIPS


class ToleranceAssessment(BaseModel):
    """Whether loading a test report for this candidate is worth the time."""

    model_config = ConfigDict(frozen=True)

    pump_id: str
    standard: str
    curve_basis: CurveBasis
    band_applied: bool
    findings: list[ChannelFinding] = Field(default_factory=list)
    test_report_recommended: bool = False
    recommendation: str
    disclosures: list[str] = Field(default_factory=list)

    @property
    def material_channels(self) -> list[ToleranceChannel]:
        return [f.channel for f in self.findings if f.material]


# =============================================================================
# Channel implementations
# =============================================================================


def _trajectory_cells(candidate: "CandidateResult") -> list["CellResult"]:
    """Design point plus sampled trajectory, de-duplicated, in time order."""
    seen: set[tuple[str, str]] = set()
    out: list["CellResult"] = []
    for cell in [candidate.design_point, *candidate.cells_sampled]:
        key = (cell.config_id, cell.point_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(cell)
    return sorted(out, key=lambda c: c.month)


def _head_margin_finding(
    cells: Sequence["CellResult"], standard: AcceptanceStandard
) -> ChannelFinding:
    """Does a compliant-but-low pump still make the required TDH?

    ``head_margin_frac`` is ``(developed - required) / required``.  A head
    deviation of ``d`` scales the developed head, so the margin goes to
    ``(1 - d)(1 + m) - 1``, which reaches zero at ``d = m / (1 + m)``.  That is
    the deviation at which the pump stops making TDH, and it is directly
    comparable to the band.
    """
    band = standard.head_frac
    worst: tuple[float, "CellResult"] | None = None
    for cell in cells:
        if cell.zone in (OperatingZone.OFF_CURVE_LEFT, OperatingZone.OFF_CURVE_RIGHT):
            continue
        margin = cell.head_margin_frac
        if margin <= -1.0:
            continue
        flip_at = margin / (1.0 + margin)
        if flip_at < 0:
            # Already short of TDH at the catalog curve; the band is not what
            # put it there and reporting it here would misattribute the cause.
            continue
        if worst is None or flip_at < worst[0]:
            worst = (flip_at, cell)

    if worst is None:
        already_short = [
            c
            for c in cells
            if -1.0 < c.head_margin_frac < 0.0
            and c.zone
            not in (OperatingZone.OFF_CURVE_LEFT, OperatingZone.OFF_CURVE_RIGHT)
        ]
        if already_short:
            deepest = min(already_short, key=lambda c: c.head_margin_frac)
            return ChannelFinding(
                channel=ToleranceChannel.HEAD_MARGIN,
                effect=ChannelEffect.NOT_COMPUTABLE,
                band_frac=band,
                at_month=deepest.month,
                detail=(
                    f"This configuration is already short of the required TDH on "
                    f"the catalog curve itself -- {deepest.head_margin_frac:.1%} "
                    f"at month {deepest.month:g}, on every one of the "
                    f"{len(already_short)} cells checked. The acceptance band is "
                    f"not what put it there, and reporting a tolerance finding "
                    f"here would misattribute a sizing shortfall to "
                    f"manufacturing variation. The shortfall is the finding."
                ),
            )
        return ChannelFinding(
            channel=ToleranceChannel.HEAD_MARGIN,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            detail=(
                "No trajectory cell carries a head margin computed against the "
                "fitted curve, so the head band has nothing to move."
            ),
        )

    flip_at, cell = worst
    if flip_at <= band:
        return ChannelFinding(
            channel=ToleranceChannel.HEAD_MARGIN,
            effect=ChannelEffect.FLIPS,
            band_frac=band,
            flip_at_frac=flip_at,
            at_month=cell.month,
            detail=(
                f"At month {cell.month:g} the head margin is "
                f"{cell.head_margin_frac:.1%}. A head deviation of "
                f"{flip_at:.1%} takes the design below the required TDH, and "
                f"the standard permits {band:.0%}. A unit inside the acceptance "
                f"band would not lift the well."
            ),
        )
    return ChannelFinding(
        channel=ToleranceChannel.HEAD_MARGIN,
        effect=ChannelEffect.HOLDS,
        band_frac=band,
        flip_at_frac=flip_at,
        at_month=cell.month,
        detail=(
            f"Thinnest head margin is {cell.head_margin_frac:.1%} at month "
            f"{cell.month:g}; it would take a {flip_at:.1%} head deviation to "
            f"lose TDH, outside the {band:.0%} band."
        ),
    )


def _stage_count_finding(
    cells: Sequence["CellResult"],
    *,
    pump: PumpModel,
    stages: int,
    standard: AcceptanceStandard,
) -> ChannelFinding:
    """Would a low-but-compliant curve need more stages than the housing holds?

    A stage count that merely goes up is a procurement fact, not an
    infeasibility.  Only exceeding ``max_stages`` is a decision that flips: past
    that point no restage recovers the design and the candidate is a different
    candidate.
    """
    band = standard.head_frac
    if stages < 1:
        return ChannelFinding(
            channel=ToleranceChannel.STAGE_COUNT,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            detail="Configuration carries no stage count.",
        )

    worst_banded = 0
    worst_nominal = 0
    worst_cell: "CellResult" | None = None
    for cell in cells:
        if cell.head_developed_ft <= 0 or cell.head_required_ft <= 0:
            continue
        head_per_stage = cell.head_developed_ft / stages
        banded = ceil(cell.head_required_ft / ((1.0 - band) * head_per_stage))
        if banded > worst_banded:
            worst_banded = banded
            worst_nominal = ceil(cell.head_required_ft / head_per_stage)
            worst_cell = cell

    if worst_cell is None:
        return ChannelFinding(
            channel=ToleranceChannel.STAGE_COUNT,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            detail="No cell carries both a developed and a required head.",
        )

    if worst_nominal > pump.max_stages:
        # The housing limit is already breached by the catalog curve. Attributing
        # that to manufacturing tolerance would be a false alarm: the band is not
        # what decides it, and removing the band would not help.
        return ChannelFinding(
            channel=ToleranceChannel.STAGE_COUNT,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            at_month=worst_cell.month,
            detail=(
                f"The catalog curve alone already calls for {worst_nominal} "
                f"stages at month {worst_cell.month:g} against a "
                f"{pump.max_stages}-stage housing limit. The acceptance band is "
                f"not the binding constraint here and a test report would not "
                f"change it."
            ),
        )

    if worst_banded > pump.max_stages:
        return ChannelFinding(
            channel=ToleranceChannel.STAGE_COUNT,
            effect=ChannelEffect.FLIPS,
            band_frac=band,
            at_month=worst_cell.month,
            detail=(
                f"The catalog curve calls for {worst_nominal} stages at month "
                f"{worst_cell.month:g}, inside the {pump.max_stages}-stage "
                f"housing limit; a {band:.0%}-low but still compliant unit would "
                f"need {worst_banded}, which the housing cannot hold. The band "
                f"is what decides whether this pump has a restage at all."
            ),
        )
    return ChannelFinding(
        channel=ToleranceChannel.STAGE_COUNT,
        effect=ChannelEffect.HOLDS,
        band_frac=band,
        at_month=worst_cell.month,
        detail=(
            f"A {band:.0%}-low unit would need up to {worst_banded} stages "
            f"(catalog curve {worst_nominal}, designed {stages}, housing limit "
            f"{pump.max_stages}); the restage stays inside the housing."
        ),
    )


def perturb_curve_rate_axis(curve: ScaledCurve, frac: float) -> ScaledCurve:
    """Shift every rate-axis landmark of a curve by ``frac``.

    Only the landmarks move.  The polynomial coefficients are deliberately left
    alone: this function exists to test the *zone label*, which is decided
    entirely by where the flow sits relative to BEP and the thrust boundaries.
    Rescaling the coefficients as well would additionally change head and BHP,
    double-counting the head band that ``_head_margin_finding`` already applies.
    """
    if frac <= -1.0:
        raise ValueError(f"rate perturbation {frac} would invert the curve")
    k = 1.0 + frac
    return curve.model_copy(
        update={
            "q_min_bpd": curve.q_min_bpd * k,
            "q_max_bpd": curve.q_max_bpd * k,
            "bep_q_bpd": curve.bep_q_bpd * k,
            "recommended_min_bpd": curve.recommended_min_bpd * k,
            "recommended_max_bpd": curve.recommended_max_bpd * k,
            "downthrust_limit_bpd": (
                None
                if curve.downthrust_limit_bpd is None
                else curve.downthrust_limit_bpd * k
            ),
            "upthrust_limit_bpd": (
                None
                if curve.upthrust_limit_bpd is None
                else curve.upthrust_limit_bpd * k
            ),
        }
    )


def _zone_finding(
    cells: Sequence["CellResult"],
    *,
    curve: ScaledCurve,
    method: ThrustZoneMethod,
    thresholds: DesignThresholds,
    zone_weights: dict[str, float],
    standard: AcceptanceStandard,
) -> ChannelFinding:
    """Does the operating zone survive a compliant shift of the rate axis?

    The zone label is the single largest term in the score (up to 0.75), so a
    label that is not stable under the standard's own acceptance tolerance is
    the strongest argument there is for loading the test report.
    """
    band = standard.rate_frac
    try:
        # Signs are the deviation of the ACTUAL unit's landmarks from the
        # published ones: "+5%" means the real pump's BEP and thrust boundaries
        # sit 5% higher in rate, which makes a given flow relatively lower on
        # its curve.
        shifted_up = perturb_curve_rate_axis(curve, +band)
        shifted_down = perturb_curve_rate_axis(curve, -band)
    except ValueError as exc:
        return ChannelFinding(
            channel=ToleranceChannel.OPERATING_ZONE,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            detail=f"Rate axis could not be perturbed: {exc}",
        )

    checked = 0
    flips = 0
    # Report the flip with the largest scoring consequence, not the first one in
    # time. A month-0 operating_range -> bep flip and a month-18 operating_range
    # -> downthrust flip are both flips; only the second one is worth an
    # engineer's attention first.
    worst: tuple[float, float, OperatingZone, OperatingZone, str, float] | None = None
    for cell in cells:
        q = cell.total_fluid_intake_bpd
        if q <= 0:
            continue
        checked += 1
        nominal = cell.zone
        for shifted, direction in ((shifted_up, "+"), (shifted_down, "-")):
            try:
                moved = classify_zone(shifted, q, method, thresholds).zone
            except ValueError:
                continue
            if moved is nominal:
                continue
            flips += 1
            swing = abs(
                zone_weights.get(nominal.value, 0.0) - zone_weights.get(moved.value, 0.0)
            )
            if worst is None or swing > worst[0]:
                worst = (swing, cell.month, nominal, moved, direction, q)

    if worst is not None:
        swing, month, nominal, moved, direction, q = worst
        return ChannelFinding(
            channel=ToleranceChannel.OPERATING_ZONE,
            effect=ChannelEffect.FLIPS,
            band_frac=band,
            at_month=month,
            detail=(
                f"{flips} of the {checked} sampled cells change zone inside the "
                f"±{band:.0%} rate band. The largest consequence is at month "
                f"{month:g}: the catalog curve puts {q:,.0f} bpd in "
                f"{nominal.value}, and a {direction}{band:.0%} rate deviation, "
                f"which the standard accepts, puts it in {moved.value} -- a "
                f"{swing:.2f} swing in zone quality, the term carrying the "
                f"largest weight in the score. This candidate's ranking rests on "
                f"a distinction the catalog curve cannot resolve."
            ),
        )

    if checked == 0:
        return ChannelFinding(
            channel=ToleranceChannel.OPERATING_ZONE,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            detail="No trajectory cell carries a positive intake rate.",
        )
    return ChannelFinding(
        channel=ToleranceChannel.OPERATING_ZONE,
        effect=ChannelEffect.HOLDS,
        band_frac=band,
        detail=(
            f"Every one of the {checked} sampled cells keeps its zone under a "
            f"±{band:.0%} shift of the rate axis."
        ),
    )


def _motor_loading_finding(
    cells: Sequence["CellResult"], *, thresholds: DesignThresholds, standard: AcceptanceStandard
) -> ChannelFinding:
    """Does the 75-85% loading band verdict survive the BHP tolerance?"""
    band = standard.bhp_frac
    lo = thresholds.motor.target_loading_min
    hi = thresholds.motor.target_loading_max

    loaded = [c for c in cells if c.motor_loading_frac is not None]
    if not loaded:
        return ChannelFinding(
            channel=ToleranceChannel.MOTOR_LOADING,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            detail=(
                "No cell carries a motor loading. Equipment sizing runs once at "
                "the design point by design, so this is expected on cells that "
                "were not sized -- not a gap."
            ),
        )

    worst: tuple[float, "CellResult", str] | None = None
    for cell in loaded:
        load = cell.motor_loading_frac or 0.0
        if load <= 0:
            continue
        inside = lo <= load <= hi
        # Distance to whichever edge the band could carry it across.
        if inside:
            up = (hi / load) - 1.0
            down = 1.0 - (lo / load)
            flip_at, edge = (up, "over") if up <= down else (down, "under")
        else:
            if load > hi:
                flip_at, edge = 1.0 - (hi / load), "back inside from over-load"
            else:
                flip_at, edge = (lo / load) - 1.0, "back inside from under-load"
        if worst is None or flip_at < worst[0]:
            worst = (flip_at, cell, edge)

    if worst is None:
        return ChannelFinding(
            channel=ToleranceChannel.MOTOR_LOADING,
            effect=ChannelEffect.NOT_COMPUTABLE,
            band_frac=band,
            detail="Motor loading present but not positive on any cell.",
        )

    flip_at, cell, edge = worst
    load = cell.motor_loading_frac or 0.0
    if flip_at <= band:
        return ChannelFinding(
            channel=ToleranceChannel.MOTOR_LOADING,
            effect=ChannelEffect.FLIPS,
            band_frac=band,
            flip_at_frac=flip_at,
            at_month=cell.month,
            detail=(
                f"Motor loading is {load:.1%} against the {lo:.0%}-{hi:.0%} "
                f"target band. A {flip_at:.1%} BHP deviation moves it {edge}, "
                f"and the standard permits {band:.0%}. The loading verdict is "
                f"not decided by the catalog curve."
            ),
        )
    return ChannelFinding(
        channel=ToleranceChannel.MOTOR_LOADING,
        effect=ChannelEffect.HOLDS,
        band_frac=band,
        flip_at_frac=flip_at,
        at_month=cell.month,
        detail=(
            f"Motor loading {load:.1%} needs a {flip_at:.1%} BHP deviation to "
            f"move {edge}, outside the {band:.0%} band."
        ),
    )


def _efficiency_finding(
    *, objective: ScoringObjective, standard: AcceptanceStandard
) -> ChannelFinding:
    """The efficiency tolerance only bites if something reads efficiency.

    Efficiency is computed on every cell and displayed on every candidate, which
    makes it look load-bearing.  It is not: ``efficiency_avg_frac`` enters the
    total score under exactly one objective, ``MAX_EFFICIENCY`` (weight 0.45).
    Under every other objective the weight is zero and a 10% efficiency error
    changes no decision at all.  Saying so is more useful than manufacturing a
    band around a number nothing consumes.
    """
    band = standard.efficiency_frac
    if objective is ScoringObjective.MAX_EFFICIENCY:
        return ChannelFinding(
            channel=ToleranceChannel.EFFICIENCY_RANKING,
            effect=ChannelEffect.HOLDS,
            band_frac=band,
            detail=(
                f"Objective is {objective.value}, so efficiency carries 0.45 of "
                f"the total score and the one-sided {band:.0%} tolerance at the "
                f"{standard.efficiency_applies_at} can reorder candidates. "
                f"Ranking stability is assessed across candidates, not here."
            ),
        )
    return ChannelFinding(
        channel=ToleranceChannel.EFFICIENCY_RANKING,
        effect=ChannelEffect.NO_CONSUMER,
        band_frac=band,
        detail=(
            f"Under objective {objective.value} the efficiency term carries zero "
            f"weight in the total score, and no constraint reads efficiency. The "
            f"{band:.0%} one-sided tolerance therefore changes nothing in this "
            f"run, however large it is. Efficiency is reported to the engineer "
            f"but is not a decision input here."
        ),
    )


# =============================================================================
# Assessment
# =============================================================================


def assess_candidate_tolerance(
    candidate: "CandidateResult",
    *,
    pump: PumpModel,
    curve: ScaledCurve,
    thresholds: DesignThresholds,
    thrust_zone_method: ThrustZoneMethod,
    objective: ScoringObjective,
    zone_weights: dict[str, float] | None = None,
    standard: AcceptanceStandard = API_RP_11S2,
) -> ToleranceAssessment:
    """Decide whether a unit test report is worth loading for this candidate."""
    basis = curve_basis_of(pump)

    if basis is CurveBasis.UNIT_TEST_REPORT:
        return ToleranceAssessment(
            pump_id=pump.id,
            standard=standard.citation,
            curve_basis=basis,
            band_applied=False,
            test_report_recommended=False,
            recommendation=(
                "The curve in use is already the tested curve of this unit "
                "(§6B.1: repaired equipment binds to the S/N, not the model). "
                "There is no acceptance band left to apply and no report left "
                "to load."
            ),
        )

    if not band_applies(basis):
        return ToleranceAssessment(
            pump_id=pump.id,
            standard=standard.citation,
            curve_basis=basis,
            band_applied=False,
            test_report_recommended=False,
            recommendation=(
                f"No acceptance band is asserted. {standard.citation} bounds how "
                f"far a manufactured unit may deviate from the manufacturer's "
                f"published curve; the curve for {pump.id} is recorded as "
                f"'{pump.data_quality}', so the published curve is not what this "
                f"engine calculated from. The reconstruction error and the "
                f"manufacturing tolerance are separate and additive, and only "
                f"the second one is bounded. Loading a test report would compare "
                f"the unit against a curve we do not hold."
            ),
            disclosures=[
                "§6B.2's claim that the catalog curve is a 'known approximation "
                "with bounded error' does not hold for this record. The bound "
                "belongs to the vendor's published curve, and this catalog "
                "carries a parametric reconstruction of it. Acquiring licensed "
                "vendor curve data (register item, \u00a714) is what makes the band "
                "available -- not a test report.",
            ],
        )

    cells = _trajectory_cells(candidate)
    findings = [
        _head_margin_finding(cells, standard),
        _stage_count_finding(
            cells,
            pump=pump,
            stages=candidate.configuration.stages,
            standard=standard,
        ),
        _zone_finding(
            cells,
            curve=curve,
            method=thrust_zone_method,
            thresholds=thresholds,
            zone_weights=zone_weights or {},
            standard=standard,
        ),
        _motor_loading_finding(cells, thresholds=thresholds, standard=standard),
        _efficiency_finding(objective=objective, standard=standard),
    ]

    disclosures = [SCOPE_DISCLOSURE, RATE_MODEL_DISCLOSURE, PROPAGATION_DISCLOSURE]
    if _boundaries_are_copies(pump):
        disclosures.append(
            f"The thrust boundaries for {pump.id} are byte-identical to its "
            f"recommended-range endpoints, so §6.4's four zones are really two. A "
            f"zone flip reported above is a crossing of the recommended-range "
            f"edge, not of an independently published down/up-thrust limit "
            f"(§6B.1 requires both to come from the datasheet)."
        )

    material = [f for f in findings if f.material]
    if material:
        names = ", ".join(f.channel.value for f in material)
        recommendation = (
            f"Load the unit test report. Inside the {standard.citation} "
            f"acceptance band the catalog curve does not settle: {names}. "
            f"A pump that passes acceptance testing can land on either side of "
            f"these decisions, so the catalog is not sufficient for this design."
        )
    else:
        recommendation = (
            f"Proceed on the catalog curve. Every decision this design rests on "
            f"holds across the full {standard.citation} acceptance band, so a "
            f"unit test report would confirm what is already decided and is not "
            f"worth the time (§6B.2)."
        )

    return ToleranceAssessment(
        pump_id=pump.id,
        standard=standard.citation,
        curve_basis=basis,
        band_applied=True,
        findings=findings,
        test_report_recommended=bool(material),
        recommendation=recommendation,
        disclosures=disclosures,
    )


def _boundaries_are_copies(pump: PumpModel) -> bool:
    """True when the thrust limits merely repeat the recommended range."""
    if pump.downthrust_limit_bpd is None or pump.upthrust_limit_bpd is None:
        return False
    return (
        pump.downthrust_limit_bpd == pump.recommended_range_bpd[0]
        and pump.upthrust_limit_bpd == pump.recommended_range_bpd[1]
    )


# =============================================================================
# Design-level: does the ranking survive the band?
# =============================================================================


class RankingStability(BaseModel):
    """Whether the presented order survives the one tolerance we can propagate."""

    model_config = ConfigDict(frozen=True)

    assessed: bool
    stable: bool | None = None
    score_gap: float | None = None
    induced_swing: float | None = None
    # The threshold the comparison was made against travels with the verdict.
    # A gap of 4e-4 is 'stable' against one tolerance and 'not settled' against
    # another; publishing the number without the threshold is unreadable.
    tie_tolerance: float
    detail: str


def assess_ranking_stability(
    candidates: Sequence["CandidateResult"],
    *,
    objective: ScoringObjective,
    tie_tolerance: float = 1e-3,
    standard: AcceptanceStandard = API_RP_11S2,
) -> RankingStability:
    """Could the efficiency tolerance alone swap the top two candidates?

    Under ``MAX_EFFICIENCY`` the total score carries ``0.45 * eff_avg``.  The
    tolerance is one-sided and per-unit: the winner's physical pump may sit at
    the bottom of the band while the runner-up's sits at the top.  The worst
    case is therefore the winner losing ``0.45 * f * eff_1`` while the runner-up
    loses nothing.

    Under any other objective the efficiency weight is zero and this cannot
    move the ranking at all -- which is a real answer, not an unassessed one.
    """
    if len(candidates) < 2:
        return RankingStability(
            assessed=False,
            tie_tolerance=tie_tolerance,
            detail="Fewer than two candidates were presented; there is no order to destabilise.",
        )
    if objective is not ScoringObjective.MAX_EFFICIENCY:
        return RankingStability(
            assessed=False,
            tie_tolerance=tie_tolerance,
            detail=(
                f"Efficiency carries no weight under objective {objective.value}, "
                f"so the {standard.efficiency_frac:.0%} efficiency tolerance "
                f"cannot reorder candidates. The head, rate and BHP bands are "
                f"not propagated into the score (see disclosures), so the "
                f"ranking is untested against those."
            ),
        )

    first, second = candidates[0], candidates[1]
    gap = first.score.total_score - second.score.total_score
    swing = 0.45 * standard.efficiency_frac * first.score.efficiency_avg_frac
    stable = gap - swing > tie_tolerance
    return RankingStability(
        assessed=True,
        stable=stable,
        score_gap=gap,
        induced_swing=swing,
        tie_tolerance=tie_tolerance,
        detail=(
            f"Top two are separated by {gap:.6f}. A one-sided "
            f"{standard.efficiency_frac:.0%} efficiency shortfall on the leader "
            f"alone moves its score by {swing:.6f}, which "
            f"{'does not close' if stable else 'closes'} the gap beyond the "
            f"{tie_tolerance:.0e} tie tolerance (\u00a73.5). "
            + (
                "The presented order holds."
                if stable
                else "The presented order is not decided by the catalog curve; "
                "load test reports for both before choosing between them."
            )
        ),
    )
