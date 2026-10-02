"""Framework §6D.2 motor thermal self-heating model.

Historical placeholder: ``estimated_winding_temp_f = intake_temp_f``. That
understated winding temperature by the entire self-heating term, and per the
framework's own critique row ("understated value as calculated") propagated
silently into the B.14.5 insulation check and cable sizing.

This module closes that gap with a two-term energy balance anchored to the
framework's cited field observation:

> "Even above 1 ft/sec, the temperature rise above ambient is about 50°F for
> water and 90°F for oil, higher with gas present."
> — ESP Design Framework v0.6 §6D.2

**Scope.** The output is *rise above the fluid temperature*, split into a
displayed self-heating component per §6D.2's requirement that fluid and
self-heating be shown separately so the engineer sees which lever helps:
a shroud (raises cooling velocity) or a high-temperature build (raises the
rating). The final winding temperature is the sum.

**Non-goals.** This is not a full electromagnetic thermal network. A vendor
finite-element or lumped-parameter model with per-frame stator-copper,
stator-iron, rotor, air-gap, and end-winding nodes would be more accurate.
That model is not publicly available for any motor in this catalog and
substituting one would be fabrication. The single-anchor form here is
defensible because it is fully traceable to a cited number, load-linear, and
velocity-consistent with forced convection through an annulus.

**Refusals preserved.**
- Efficiency load dependence is not modeled: without a vendor efficiency map,
  nameplate η is used at every load. This over-estimates rise at very light
  load (real efficiency drops below nameplate there); the target loading band
  keeps designs away from that regime.
- Above 50% intake FGVF the framework refuses on separate gates (§6C); the
  gas augmentation term is therefore only defined up to that limit.
- Cable calculated temperature (§6D.2 row 4) is *not* changed here. The cable
  sizing module still uses intake temperature as its ambient. That is a
  separate slice; a full §6D.2 close needs a cable calculated-temperature
  model too.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# Framework §6D.2 anchor: rise above ambient at reference conditions.
# Reference conditions: 1 ft/sec cooling velocity, nameplate load, a typical
# induction motor (η ≈ 0.85), no free gas.
REFERENCE_VELOCITY_FT_S = 1.0
REFERENCE_WATER_RISE_F = 50.0
REFERENCE_OIL_RISE_F = 90.0
REFERENCE_EFFICIENCY = 0.85

# Dittus-Boelter turbulent forced-convection exponent for the fluid side of an
# annulus. `h ∝ v^0.8`, so ΔT ∝ v^-0.8. Applied to the ratio v_ref / v so a
# velocity below 1 ft/sec increases rise (below the framework's minimum, but
# the pipeline gates cooling velocity separately — this model must not
# extrapolate to zero and return a finite rise).
VELOCITY_EXPONENT = 0.8

# Framework refuses to design above 50% intake FGVF on separate gates (§6C).
# The linear-liquid-mass reduction below is only well-posed inside that band.
MAX_MODELED_FGVF = 0.5

# Floor cooling velocity used to keep the model well-conditioned when the
# pipeline is still enumerating configurations that will later be rejected by
# the hard cooling-velocity gate. Below 0.5 ft/sec the convection correlation
# breaks down (laminar); the pipeline's separate cooling gate rejects those.
MIN_MODELED_VELOCITY_FT_S = 0.5


class MotorSelfHeating(BaseModel):
    """§6D.2 requires that fluid temperature and self-heating be *displayed
    separately* because the two components imply different remedies. This
    record carries both."""

    model_config = ConfigDict(frozen=True)

    fluid_temp_f: float = Field(
        description="Ambient temperature the motor sees, from customer §3.0 data."
    )
    rise_f: float = Field(
        description="Calculated temperature rise above the fluid, from motor "
        "losses removed by annular convection. Positive."
    )
    winding_temp_f: float = Field(
        description="Fluid + rise. Compared against the motor's max winding "
        "temperature rating per B.14.5."
    )
    reference_rise_f: float = Field(
        description="The framework's cited rise at 1 ft/sec, nameplate load, "
        "η=0.85, no gas, blended by water cut. Kept so the calibration point "
        "the number derives from is visible in the record."
    )
    load_factor: float = Field(
        description="loading_frac; losses are linear in shaft load."
    )
    velocity_factor: float = Field(
        description="(v_ref / v)^0.8 from Dittus-Boelter for turbulent "
        "annular flow. Higher velocity → lower rise."
    )
    efficiency_factor: float = Field(
        description="(1 - η) / (1 - 0.85) rescaling the anchor from a typical "
        "induction motor to this machine. Above unity means this machine "
        "generates more heat than the reference; below unity means less."
    )
    gas_factor: float = Field(
        description="1 / (1 - fgvf) reflecting reduced liquid mass flux past "
        "the stator. Framework: 'higher with gas present' with no cited number, "
        "so a physical liquid-mass form is used, capped at 50% FGVF."
    )
    efficiency_basis: str = Field(
        description="Whether motor efficiency came from the catalog or the "
        "typical-induction fallback."
    )


def estimate_motor_self_heating(
    *,
    intake_temp_f: float,
    cooling_velocity_ft_s: float,
    loading_frac: float,
    water_cut_frac: float,
    free_gas_fraction_at_intake: float,
    motor_efficiency: float | None,
    motor_type: str,
) -> MotorSelfHeating:
    """Return the §6D.2 two-term motor temperature.

    All inputs are quantities the deterministic engine already computes at the
    point ``size_motor`` runs. The function is pure so that a mutation guard
    can perturb any single input and confirm the calculation is not degenerate.
    """
    if intake_temp_f < -100.0 or intake_temp_f > 700.0:
        raise ValueError(f"intake_temp_f out of range: {intake_temp_f}")
    if loading_frac <= 0.0:
        raise ValueError(f"loading_frac must be positive, got {loading_frac}")
    if not 0.0 <= water_cut_frac <= 1.0:
        raise ValueError(f"water_cut_frac must be in [0, 1], got {water_cut_frac}")
    if not 0.0 <= free_gas_fraction_at_intake < 1.0:
        raise ValueError(
            f"free_gas_fraction_at_intake must be in [0, 1), got "
            f"{free_gas_fraction_at_intake}"
        )
    if motor_efficiency is not None and not 0.0 < motor_efficiency < 1.0:
        raise ValueError(f"motor_efficiency must be in (0, 1), got {motor_efficiency}")

    reference_rise_f = (
        water_cut_frac * REFERENCE_WATER_RISE_F
        + (1.0 - water_cut_frac) * REFERENCE_OIL_RISE_F
    )

    load_factor = loading_frac

    v = max(cooling_velocity_ft_s, MIN_MODELED_VELOCITY_FT_S)
    velocity_factor = (REFERENCE_VELOCITY_FT_S / v) ** VELOCITY_EXPONENT

    if motor_efficiency is not None:
        efficiency_basis = "cataloged"
        efficiency_used = motor_efficiency
    else:
        # Refusal preserved: without a vendor efficiency figure, fall back to
        # the reference-condition induction efficiency. This makes the ratio
        # exactly 1.0 (no under-crediting a PMM whose data we do not have,
        # and no over-crediting either). The fallback is disclosed via
        # efficiency_basis so a downstream reader can act on it.
        efficiency_basis = "reference_induction_fallback"
        efficiency_used = REFERENCE_EFFICIENCY

    efficiency_factor = (1.0 - efficiency_used) / (1.0 - REFERENCE_EFFICIENCY)

    fgvf_clipped = min(free_gas_fraction_at_intake, MAX_MODELED_FGVF)
    gas_factor = 1.0 / (1.0 - fgvf_clipped)

    rise_f = (
        reference_rise_f
        * load_factor
        * velocity_factor
        * efficiency_factor
        * gas_factor
    )
    winding_temp_f = intake_temp_f + rise_f

    return MotorSelfHeating(
        fluid_temp_f=intake_temp_f,
        rise_f=rise_f,
        winding_temp_f=winding_temp_f,
        reference_rise_f=reference_rise_f,
        load_factor=load_factor,
        velocity_factor=velocity_factor,
        efficiency_factor=efficiency_factor,
        gas_factor=gas_factor,
        efficiency_basis=efficiency_basis,
    )
