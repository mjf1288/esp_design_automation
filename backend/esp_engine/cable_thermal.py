"""Framework §6D.2 cable calculated temperature model.

Historical placeholder: the cable sizer used ``intake_temp_f`` as the
conductor temperature. That is the *ambient* the cable sees, not the
temperature of the copper. The framework's §6D.2 display table row 4
requires a *calculated cable temperature* checked against the insulation
class (B.14.5). The placeholder therefore:

  1. Under-stated cable temperature by the entire self-heating term
     (a hot well at high motor current can rise 20-40 F above ambient),
     leading to voltage-drop calculations that used a lower copper
     resistivity than the real installation and to insulation-class
     checks that could pass in the software while failing in the field.

  2. Made the cable insulation class an effective free variable rather
     than a hard gate: the check ``max_temp_f > cable.max_temp_f`` in
     the sizer compared the fluid temperature to the rating, so a cable
     rated exactly at intake could clear the screen while running above
     rating at load.

This module closes that gap with a two-term energy balance, structurally
parallel to :mod:`esp_engine.motor_thermal`:

    T_conductor = T_fluid + rise
    rise = ΔT_ref * (I / I_amp)^2 * R_ratio * (v_ref / v)^0.8 * fluid_factor

**Anchor.** Vendor guidance and IEEE Std 1018 / NEMA WC-53 field data
put a typical ESP power cable operating at nameplate ampacity, submerged
in produced water at 1 ft/sec annular velocity, at roughly 20 F above
ambient. Oil is worse than water for cable cooling for the same reason
it is worse for the motor — lower thermal conductivity and lower film
coefficient — with a comparable ~1.8x multiplier. This model uses:

    ΔT_ref_water = 20 F
    ΔT_ref_oil   = 36 F  (1.8 * water, same water/oil ratio as §6D.2 motor anchor)

Both figures are for a conductor operating at cable ampacity with the
fluid at 1 ft/sec cooling velocity.

**Refusals preserved.**

- Dielectric losses in the MLE insulation are neglected. At medium
  voltage (up to ~5 kV) they are 1-2 orders of magnitude below I²R;
  ignoring them biases conservatively low but only slightly. A high-
  voltage (>5 kV) or long-cable case would need a real dielectric term.
- Multi-node radial conduction through the individual insulation layers,
  jacket, and armor is not modeled. The single lumped rise-to-fluid form
  above bounds the conductor temperature but does not resolve intra-
  bundle gradients. A vendor thermal model with insulation, jacket,
  bedding, and armor as separate nodes would give a per-layer profile;
  none is publicly available for the cables in this catalog and
  substituting one would be fabrication.
- Ambient-air lead-in (the surface lead from wellhead to VSD) is not
  modeled separately. That segment sees air not fluid and is a distinct
  thermal regime; in practice ampacity there is limited by the wellhead
  penetrator and MLE, not the surface lead itself, so the downhole
  conductor temperature is the governing figure and the one modeled
  here. The pipeline currently sizes cable at ``setting_depth + surface
  lead`` for voltage-drop; that length is preserved.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# Reference conditions for the anchor: cable at nameplate ampacity, fluid
# temperature 77 F (matches the resistance reference), fluid velocity 1
# ft/sec (matches the §6D.2 motor anchor). The anchor rise is the steady-
# state figure a real cable exhibits at those conditions -- resistance
# amplification is already inside it (a real cable at 97 F copper is running
# with resistance R(97), not R(77)). The scaling equations below therefore
# use R(T_c) / R(T_c_anchor) so that reproducing the anchor conditions
# reproduces the anchor rise exactly, and departures from the anchor scale
# the resistance amplification incrementally from the anchor's own
# operating resistance.
REFERENCE_CURRENT_RATIO = 1.0
REFERENCE_VELOCITY_FT_S = 1.0
REFERENCE_FLUID_TEMP_F = 77.0
REFERENCE_WATER_RISE_F = 20.0
REFERENCE_OIL_RISE_F = 36.0

# Dittus-Boelter turbulent forced-convection exponent — same functional
# form as the motor annular flow, since the cable sits in the same
# annulus and the same correlation governs the fluid film.
VELOCITY_EXPONENT = 0.8

# Below 0.5 ft/sec the turbulent Dittus-Boelter form breaks down (laminar
# regime). The pipeline's separate cooling-velocity gate rejects those
# configurations; this floor only keeps the calculation well-conditioned
# during enumeration.
MIN_MODELED_VELOCITY_FT_S = 0.5

# The temperature-coefficient of resistance for copper (per °F) matches
# the default on :class:`CableModel`. Held here so the rise calculation
# is independent of the specific cable object during iteration.
COPPER_TEMP_COEFF_PER_F = 0.00214

# Fixed-point iteration for the coupled ``T_c ↔ R(T_c)`` balance. Two
# iterations converge to <0.01 F for any realistic input; the extra
# margin here brings anchor-condition round-trip below 1e-9 F so tests
# can pin the anchor exactly.
_FIXED_POINT_ITERATIONS = 12


class CableSelfHeating(BaseModel):
    """§6D.2 requires that fluid temperature and self-heating be *displayed
    separately* because the two components imply different remedies. The
    cable row of the display table (row 4) uses the same two-term shape
    as the motor row so both can be read side by side."""

    model_config = ConfigDict(frozen=True)

    fluid_temp_f: float = Field(
        description="Ambient the cable sees, from customer §3.0 data. Same "
        "value as motor fluid_temp_f at the setting depth; upstream cases "
        "with a non-flat well temperature profile would populate a "
        "cable-length-averaged value here instead."
    )
    rise_f: float = Field(
        description="Calculated temperature rise of the copper above the "
        "fluid, from I²R losses removed by annular convection. Positive."
    )
    conductor_temp_f: float = Field(
        description="Fluid + rise. Compared against the cable's insulation "
        "class max_temp_f per B.14.5, and used as the conductor "
        "temperature for the voltage-drop resistivity correction."
    )
    reference_rise_f: float = Field(
        description="The anchor rise at reference conditions (ampacity, "
        "1 ft/sec, blended by water cut). Kept so the calibration point "
        "the number derives from is visible in the record."
    )
    current_factor: float = Field(
        description="(I / I_amp)² — losses scale with current squared. "
        "Above 1.0 is impossible because the ampacity screen upstream "
        "rejects overcurrent."
    )
    velocity_factor: float = Field(
        description="(v_ref / v)^0.8 from Dittus-Boelter for turbulent "
        "annular flow. Higher velocity → lower rise."
    )
    resistance_factor: float = Field(
        description="R(T_c) / R(anchor T_c). At the anchor operating point "
        "this is exactly 1.0. Above 1.0 means the conductor is running "
        "warmer than the anchor and the resistance amplification is "
        "boosting the rise further; below 1.0 means the conductor is "
        "cooler than the anchor (lightly loaded well) and the amplification "
        "works in the other direction. Reported so the loop is visible."
    )
    fluid_factor: float = Field(
        description="Water-cut blend of the reference-rise anchor. Higher "
        "oil fraction → higher rise for the same current."
    )


def estimate_cable_self_heating(
    *,
    fluid_temp_f: float,
    cooling_velocity_ft_s: float,
    current_a: float,
    ampacity_a: float,
    water_cut_frac: float,
    conductor_temp_coeff_per_f: float = COPPER_TEMP_COEFF_PER_F,
) -> CableSelfHeating:
    """Return the §6D.2 two-term cable temperature.

    Inputs are quantities the deterministic engine computes at the point
    ``size_cable`` runs. The function is pure so that a mutation guard
    can perturb any single input and confirm the calculation is not
    degenerate.
    """
    if fluid_temp_f < -100.0 or fluid_temp_f > 700.0:
        raise ValueError(f"fluid_temp_f out of range: {fluid_temp_f}")
    if current_a < 0.0:
        raise ValueError(f"current_a must be nonnegative, got {current_a}")
    if ampacity_a <= 0.0:
        raise ValueError(f"ampacity_a must be positive, got {ampacity_a}")
    if not 0.0 <= water_cut_frac <= 1.0:
        raise ValueError(f"water_cut_frac must be in [0, 1], got {water_cut_frac}")
    if conductor_temp_coeff_per_f <= 0.0:
        raise ValueError(
            f"conductor_temp_coeff_per_f must be positive, got "
            f"{conductor_temp_coeff_per_f}"
        )

    reference_rise_f = (
        water_cut_frac * REFERENCE_WATER_RISE_F
        + (1.0 - water_cut_frac) * REFERENCE_OIL_RISE_F
    )
    fluid_factor = reference_rise_f / REFERENCE_WATER_RISE_F

    current_ratio = current_a / ampacity_a
    current_factor = current_ratio * current_ratio

    v = max(cooling_velocity_ft_s, MIN_MODELED_VELOCITY_FT_S)
    velocity_factor = (REFERENCE_VELOCITY_FT_S / v) ** VELOCITY_EXPONENT

    # Anchor conductor temperature: R(T_c) / R(anchor T_c) is what scales
    # the rise, not R(T_c) / R(77 F). Otherwise reproducing the anchor
    # inputs would not reproduce the anchor output because the anchor
    # measurement already runs at its own elevated conductor temperature.
    anchor_conductor_temp_f = REFERENCE_FLUID_TEMP_F + reference_rise_f
    anchor_resistance = 1.0 + conductor_temp_coeff_per_f * (
        anchor_conductor_temp_f - 77.0
    )

    # Fixed-point: rise depends on R which depends on conductor temp which
    # depends on rise. Start from the isothermal solution and iterate.
    conductor_temp_f = fluid_temp_f
    resistance_factor = 1.0
    rise_f = 0.0
    for _ in range(_FIXED_POINT_ITERATIONS):
        raw_resistance = 1.0 + conductor_temp_coeff_per_f * (
            conductor_temp_f - 77.0
        )
        if raw_resistance <= 0.0:
            raise ValueError(
                f"conductor resistance factor became nonphysical "
                f"({raw_resistance:g}); check inputs"
            )
        # Scale relative to the anchor's own operating resistance, so the
        # incremental amplification is what departs the anchor rise.
        resistance_factor = raw_resistance / anchor_resistance
        rise_f = (
            reference_rise_f
            * current_factor
            * velocity_factor
            * resistance_factor
        )
        conductor_temp_f = fluid_temp_f + rise_f

    return CableSelfHeating(
        fluid_temp_f=fluid_temp_f,
        rise_f=rise_f,
        conductor_temp_f=conductor_temp_f,
        reference_rise_f=reference_rise_f,
        current_factor=current_factor,
        velocity_factor=velocity_factor,
        resistance_factor=resistance_factor,
        fluid_factor=fluid_factor,
    )
