"""Framework v0.6 §6D.2: annular cooling velocity, bounded on both sides.

Until v0.6 this was a single comparison against one number
(``min_cooling_velocity_ft_s = 1.0``) producing one boolean. v0.6 replaces that
with a *range*, and the two bounds are not two ends of the same rule:

    | Bound                        | Value          | Mechanism when crossed          |
    |------------------------------|----------------|---------------------------------|
    | Minimum, standard conditions | 1 ft/s         | motor overheating               |
    | Minimum, viscous oil         | 2.6-2.8 ft/s   | the 1 ft/s rule is inadequate   |
    | Upper bound                  | erosion / sep. | erosion with solids; degraded   |
    |                              |                | gas separation into the annulus |

The framework is explicit that "the warnings for the lower and upper bounds are
therefore **different**: below, overheating risk; above, erosion and loss of
separation." Collapsing them into one "velocity out of range" message would be a
factual error about the physics, not just a wording choice: raising velocity is
the *fix* for the lower bound and the *cause* of the upper one, so an engineer
who reads the wrong message takes the wrong action.

Three deliberate refusals in here, all of which would be easy to paper over:

1. **The viscous floor is a band (2.6-2.8), not a value.** It comes from CFD work
   at Missouri S&T, and the source publishes a range. Picking 2.7 as a midpoint
   would manufacture a precision the source does not have -- the same error
   §6B.1 prohibits for BEP. So below 2.6 is inadequate, at or above 2.8 is
   cleared, and *inside* the band is INDETERMINATE: the honest answer is that
   this well lands where the published evidence does not resolve.

2. **No numeric upper bound is invented.** The framework names the two governing
   mechanisms but publishes no velocity for either, and §13 still lists minimum
   cooling velocity among the open vendor-specific boundaries. An erosional
   velocity borrowed from API RP 14E is a pipeline-flow criterion, not an ESP
   annulus criterion, and substituting it would produce a confident number with
   no standing. Instead the upper bound is configurable and defaults to absent;
   when it is absent and a governing mechanism is *active*, that is reported as
   an unbounded risk plus a data request, never as a pass.

3. **"Viscous" is not inferred from a viscosity number.** The threshold in
   centipoise at which the 1 ft/s rule stops holding is vendor-specific and not
   cataloged. The viscous floor therefore triggers on the declared VISCOUS_OIL
   complication (customer-stated data, §3.0/§3.3). If a numeric viscosity is
   present without that declaration, the mismatch is disclosed rather than
   silently classified in either direction.

The velocity itself is unchanged: B.12's annular relation, which the framework
writes as ``Q x 0.0119 / (Casing ID^2 - Motor OD^2)`` and which
``units.bpd_to_ft_per_sec`` already computes from the annulus area.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .config import MotorThresholds


class CoolingFloorBasis(str, Enum):
    """Which minimum applies, and on what authority."""

    STANDARD = "standard"
    VISCOUS_DECLARED = "viscous_declared"


class CoolingVerdict(str, Enum):
    """Deliberately has no single ``fail`` member.

    The three ways to be inadequate are different engineering situations with
    different remedies, and the report has to preserve that distinction:

    - ``below_minimum``       overheating; raise velocity (shroud, larger OD)
    - ``within_viscous_band`` the published CFD range does not resolve this well
    - ``above_upper_bound``   erosion / lost separation; velocity is too HIGH,
                              so the lower-bound remedy makes it worse
    """

    ADEQUATE = "adequate"
    BELOW_MINIMUM = "below_minimum"
    WITHIN_VISCOUS_BAND = "within_viscous_band"
    ABOVE_UPPER_BOUND = "above_upper_bound"
    UPPER_BOUND_NOT_CATALOGED = "upper_bound_not_cataloged"


class CoolingAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    velocity_ft_s: float
    floor_basis: CoolingFloorBasis
    floor_applied_ft_s: float = Field(
        description="The velocity this well must exceed to clear the minimum. "
        "For a viscous well this is the CONSERVATIVE end of the published band, "
        "not its midpoint."
    )
    viscous_band_ft_s: tuple[float, float] | None = Field(
        default=None,
        description="The published CFD band when the viscous floor applies. "
        "Retained so the report can show that the floor is a band rather than "
        "a determined value.",
    )
    upper_bound_ft_s: float | None = Field(
        default=None,
        description="None means no numeric upper bound is cataloged -- which is "
        "the default, because the framework names the mechanisms but publishes "
        "no velocity for either.",
    )
    verdict: CoolingVerdict
    adequate: bool = Field(
        description="Clears the MINIMUM. Deliberately not a whole-range verdict: "
        "this feeds the existing hard thermal screen, and an upper-bound "
        "concern must not silently start rejecting configurations that the "
        "framework says to warn about."
    )
    erosion_mechanism_active: bool = Field(
        default=False,
        description="Solids are present, so the erosion mechanism that sets the "
        "upper bound is live for this well rather than hypothetical.",
    )
    separation_mechanism_active: bool = Field(
        default=False,
        description="Free gas is being separated into the annulus, so the "
        "separation-degradation mechanism is live for this well.",
    )
    warnings: tuple[str, ...] = ()
    data_requests: tuple[str, ...] = ()


def assess_cooling(
    *,
    velocity_ft_s: float,
    thresholds: MotorThresholds,
    viscous_oil_declared: bool = False,
    oil_viscosity_cp: float | None = None,
    solids_present: bool = False,
    gas_separated_to_annulus: bool = False,
) -> CoolingAssessment:
    """Assess one annular velocity against both bounds of the §6D.2 range.

    ``solids_present`` and ``gas_separated_to_annulus`` do not change the
    verdict on their own. They record whether the mechanisms that *set* the
    upper bound are live, which is what makes an uncataloged upper bound
    material for this particular well rather than a generic caveat.
    """
    if velocity_ft_s < 0:
        raise ValueError(f"velocity must be nonnegative, got {velocity_ft_s}")

    warnings: list[str] = []
    data_requests: list[str] = []

    band: tuple[float, float] | None = None
    if viscous_oil_declared:
        basis = CoolingFloorBasis.VISCOUS_DECLARED
        band = (
            thresholds.min_cooling_velocity_viscous_ft_s_low,
            thresholds.min_cooling_velocity_viscous_ft_s_high,
        )
        # The conservative end. For a floor, higher is safer, and the band's low
        # end is the value the source declines to guarantee.
        floor = band[1]
    else:
        basis = CoolingFloorBasis.STANDARD
        floor = thresholds.min_cooling_velocity_ft_s

    # A numeric viscosity with no declaration, or a declaration with no number,
    # are both worth stating: the first because the system is NOT applying the
    # viscous floor and the reader might assume it is, the second because the
    # engineer cannot check the classification without the number.
    if oil_viscosity_cp is not None and not viscous_oil_declared:
        warnings.append(
            f"a numeric oil viscosity of {oil_viscosity_cp:g} cp is on file but no "
            "viscous-oil complication is declared, so the STANDARD "
            f"{thresholds.min_cooling_velocity_ft_s:g} ft/s cooling floor was "
            "applied rather than the viscous floor. The viscosity at which the "
            "1 ft/s rule stops holding is vendor-specific and is not cataloged, "
            "so the system does not classify the well from the number alone. "
            "Declare the complication if the viscous floor should govern."
        )
        data_requests.append(
            "vendor-specific oil viscosity threshold above which the elevated "
            "annular cooling velocity floor applies (framework 13, open item: "
            "vendor-specific boundaries including minimum cooling velocity)"
        )

    upper = thresholds.max_cooling_velocity_ft_s
    mechanisms = [
        name
        for name, active in (
            ("erosion with solids present", solids_present),
            ("degraded natural gas separation into the annulus", gas_separated_to_annulus),
        )
        if active
    ]

    # --- verdict ---------------------------------------------------------
    # Order matters. The minimum is a hard overheating screen and is evaluated
    # first; an upper-bound concern is a warning per 6D.2 and must not mask it.
    # For a viscous well the band's LOW end is where inadequacy is established;
    # between the two ends the evidence is silent, which is a distinct verdict.
    definitely_below = band[0] if band is not None else floor
    if velocity_ft_s < definitely_below:
        verdict = CoolingVerdict.BELOW_MINIMUM
    elif band is not None and velocity_ft_s < band[1]:
        verdict = CoolingVerdict.WITHIN_VISCOUS_BAND
    elif upper is not None and velocity_ft_s > upper:
        verdict = CoolingVerdict.ABOVE_UPPER_BOUND
    elif upper is None and mechanisms:
        verdict = CoolingVerdict.UPPER_BOUND_NOT_CATALOGED
    else:
        verdict = CoolingVerdict.ADEQUATE

    # Clearing the minimum is what the hard thermal screen consumes. Being
    # inside the viscous band does NOT clear it: the published evidence does
    # not establish that this velocity cools a viscous fluid adequately, and an
    # unresolved thermal question must not read as a passed one -- the same rule
    # the magnet gate follows.
    adequate = verdict not in {
        CoolingVerdict.BELOW_MINIMUM,
        CoolingVerdict.WITHIN_VISCOUS_BAND,
    }

    # --- lower-bound warning: overheating, remedy is to RAISE velocity ---
    if verdict is CoolingVerdict.BELOW_MINIMUM:
        floor_text = (
            f"the conservative end of the {band[0]:g}-{band[1]:g} ft/s viscous band"
            if band is not None
            else f"the {floor:g} ft/s standard minimum"
        )
        warnings.append(
            f"COOLING BELOW MINIMUM: annular velocity {velocity_ft_s:.2f} ft/s is "
            f"under {floor_text}, so the motor is at risk of OVERHEATING. Raise "
            "the velocity by fitting or verifying a shroud -- the primary measure, "
            "particularly in deviated and horizontal wells with perforations above "
            "the pump -- or by selecting a larger motor OD to narrow the annular "
            "clearance. Production rate is not available as a lever: it is set by "
            "the customer."
            + (
                " The 1 ft/s rule is inadequate for viscous fluids, so this well "
                "is held to the elevated floor."
                if band is not None
                else ""
            )
        )
    elif verdict is CoolingVerdict.WITHIN_VISCOUS_BAND:
        assert band is not None
        warnings.append(
            f"COOLING INDETERMINATE FOR A VISCOUS FLUID: annular velocity "
            f"{velocity_ft_s:.2f} ft/s falls inside the published "
            f"{band[0]:g}-{band[1]:g} ft/s CFD band (Missouri S&T) rather than "
            "above it. The source publishes a range, not a value, so no midpoint "
            "has been substituted and this well cannot be declared adequately "
            "cooled or inadequately cooled from it. Treat as not cleared: obtain "
            "the vendor's own minimum for this motor and fluid, or raise the "
            f"velocity above {band[1]:g} ft/s with a shroud or a larger motor OD."
        )
        data_requests.append(
            f"vendor minimum annular cooling velocity for a viscous fluid on this "
            f"motor; the generic published band is {band[0]:g}-{band[1]:g} ft/s and "
            f"this configuration computes {velocity_ft_s:.2f} ft/s, inside it"
        )

    # --- upper-bound warning: DIFFERENT mechanisms, opposite remedy ------
    # Framework 6D.2: "The upper bound is set not by cooling (higher velocity
    # always cools better) but by two other mechanisms."
    if verdict is CoolingVerdict.ABOVE_UPPER_BOUND:
        assert upper is not None
        warnings.append(
            f"COOLING VELOCITY ABOVE UPPER BOUND: annular velocity "
            f"{velocity_ft_s:.2f} ft/s exceeds the configured {upper:g} ft/s "
            "limit. This is NOT a cooling problem -- higher velocity always cools "
            "better. The upper bound is set by "
            + (" and ".join(mechanisms) if mechanisms else "erosion and separation")
            + ". The remedy is the opposite of the low-velocity remedy: removing "
            "a shroud or selecting a smaller motor OD widens the annulus and "
            "reduces velocity. Do not fit a shroud to address this."
        )
    elif verdict is CoolingVerdict.UPPER_BOUND_NOT_CATALOGED:
        warnings.append(
            f"COOLING UPPER BOUND UNBOUNDED: annular velocity is "
            f"{velocity_ft_s:.2f} ft/s and the mechanisms that set the upper bound "
            "are active on this well ("
            + "; ".join(mechanisms)
            + "). No numeric upper bound is cataloged -- the framework names both "
            "mechanisms but publishes a velocity for neither, and an erosional "
            "velocity borrowed from pipeline practice would not be an ESP annulus "
            "criterion. The minimum is cleared; the upper bound is UNCHECKED "
            "rather than satisfied."
        )
        data_requests.append(
            "vendor or erosion-model maximum annular velocity past the motor for "
            "this well's solids loading and separation duty; the upper bound of "
            "the framework 6D.2 cooling range is currently uncataloged and "
            "therefore unchecked"
        )

    return CoolingAssessment(
        velocity_ft_s=velocity_ft_s,
        floor_basis=basis,
        floor_applied_ft_s=floor,
        viscous_band_ft_s=band,
        upper_bound_ft_s=upper,
        verdict=verdict,
        adequate=adequate,
        erosion_mechanism_active=solids_present,
        separation_mechanism_active=gas_separated_to_annulus,
        warnings=tuple(warnings),
        data_requests=tuple(data_requests),
    )
