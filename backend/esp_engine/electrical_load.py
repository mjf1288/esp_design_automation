"""Load-dependent operating current for ESP motors.

Framework \u00a76 (last remaining electrical placeholder from the v0.1 roadmap).
The pre-existing implementation computed ``operating_amps = full_load_amps
\u00d7 loading`` \u2014 a linear approximation that ignores the machine's
magnetizing branch. For an induction motor this is materially wrong at
partial load: the magnetizing (imaginary) branch stays roughly constant
across the load range, so total current does not track shaft load
linearly, and sizing cable against a linearly scaled figure will
under-count real conductor loss at low loads and mis-estimate the
voltage drop at intermediate loads.

Physics
-------

At the machine terminals the current is the vector sum of a
magnetizing component ``I_\u03bc`` (in quadrature with voltage, sets the
air-gap flux) and a load / torque-producing component ``I_L`` (in phase
with voltage after losses). At the nameplate operating point:

    |I_FL|\u00b2 = I_\u03bc\u00b2 + I_L\u00b2                                          [1]
    I_L    = I_FL \u00b7 PF                                              [2]
    I_\u03bc    = I_FL \u00b7 \u221a(1 \u2212 PF\u00b2)                                     [3]

At partial load with a fixed rotor flux the magnetizing branch stays
approximately fixed while the torque-producing branch scales roughly
linearly with shaft load (torque is proportional to real current). So:

    I_L(load) \u2248 load \u00b7 I_FL \u00b7 PF                                     [4]
    I(load)   = \u221a( I_\u03bc\u00b2 + I_L(load)\u00b2 )                              [5]

This reproduces the qualitative behaviour the framework document
records ("power factor drops off-BEP"). Numerical example with
PF_FL = 0.85: I_\u03bc / I_FL = 0.527, I_L / I_FL = 0.85. At load = 0.5:
I = \u221a(0.527\u00b2 + 0.425\u00b2) \u00b7 I_FL = 0.677 \u00b7 I_FL, so the current does not
halve. Computed power factor at 50% load is 0.425 / 0.677 = 0.628,
below the 0.85 nameplate figure \u2014 the framework's stated qualitative
behaviour.

Permanent-magnet motor (PMM)
----------------------------

A PMM has essentially no magnetizing current: field excitation is
supplied by the rotor magnets, not by stator current. B.16 of the
framework records PF near unity across the load range, which is
exactly what the phasor identity predicts for I_\u03bc \u2248 0. So for a PMM
the model degenerates to ``I(load) = load \u00b7 I_FL`` \u2014 nearly linear,
with a small residual magnetizing component (typically <10% of I_FL,
from stator inductance and controller overhead) held configurable but
defaulting to zero rather than substituted from a midpoint.

Provenance
----------

Every returned figure carries the basis that produced it. A downstream
reviewer must be able to tell a phasor-grounded figure (grounded in
cataloged PF, B.14.1 identity, and the phasor identity above) from a
screening approximation (linear scaling when neither PF nor an
operator override was available). Cable gauge and transformer kVA
inherit the same provenance.

Where the model refuses
-----------------------

- If PF is not cataloged AND no operator override supplies a magnetizing
  fraction, the model reverts to linear nameplate scaling and reports
  ``nameplate_linear_scaling``. This preserves the framework's stance
  that PF is not filled by midpoint (\u00a73.5 / B.16).
- Field-weakening at very high load (> 100%) is not modeled. The
  operating range is < 100% by design (target loading band is 50\u201385%);
  overload behaviour requires a vendor performance curve.
- The model does not attempt to reproduce the transient inrush current
  at startup. That is a separate switchboard / VSD sizing question
  handled by B.15's safety factor.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Provenance of the operating current calculation. Ordered from most
#: vendor-grounded to least.
LoadCurrentBasis = Literal[
    "phasor_from_cataloged_power_factor",
    "phasor_from_operator_magnetizing_fraction",
    "pmm_nearly_linear",
    "nameplate_linear_scaling",
]


class OperatingCurrent(BaseModel):
    """Operating current with the phasor decomposition that produced it.

    The decomposition is reported alongside the scalar current so a
    reviewer can see whether the load-scaling is doing meaningful work
    or degenerating to the linear approximation. At load = 1.0 the
    linear and phasor forms agree by construction; the difference
    grows as load drops.
    """

    model_config = ConfigDict(frozen=True)

    operating_amps: float = Field(
        description="Total current at the operating load, in amps. This "
        "is the figure cable and transformer sizing consume."
    )
    full_load_amps: float = Field(
        description="The full-load reference used as the phasor anchor. "
        "Equal to the B.14.1 I_FL when that identity is available; "
        "equal to nameplate amps in the linear-fallback case."
    )
    load_fraction: float = Field(
        description="Shaft-load fraction consumed. Reported so the "
        "reviewer can reconstruct the calculation without rerunning."
    )
    magnetizing_amps: float = Field(
        description="I_\u03bc, the load-independent (magnetizing) current "
        "component. Zero in the linear-fallback and PMM cases."
    )
    load_amps: float = Field(
        description="I_L(load), the load-scaled torque-producing "
        "current component. Equals ``load_fraction \u00b7 I_FL \u00b7 PF`` in the "
        "phasor cases and ``load_fraction \u00b7 I_FL`` in the fallbacks."
    )
    apparent_power_factor: float = Field(
        description="Computed PF at the operating load, = "
        "load_amps / operating_amps. Drops below the nameplate PF at "
        "partial load, as B.16 records qualitatively."
    )
    linear_reference_amps: float = Field(
        description="What ``full_load_amps \u00b7 load_fraction`` would have "
        "returned. Kept so reviewers can see the delta the phasor "
        "correction adds (or would add), rather than having to compute "
        "it separately."
    )
    basis: LoadCurrentBasis = Field(
        description="Which form produced ``operating_amps``. The phasor "
        "forms are grounded in cataloged or operator-supplied PF; the "
        "linear forms are screening approximations."
    )


def _phasor_from_power_factor(
    *,
    full_load_amps: float,
    power_factor: float,
    load_fraction: float,
    basis: LoadCurrentBasis,
) -> OperatingCurrent:
    if not 0.0 < power_factor <= 1.0:
        raise ValueError(
            f"power_factor must be in (0, 1], got {power_factor}"
        )
    magnetizing = full_load_amps * math.sqrt(max(0.0, 1.0 - power_factor**2))
    load_amps = load_fraction * full_load_amps * power_factor
    operating = math.sqrt(magnetizing**2 + load_amps**2)
    apparent_pf = load_amps / operating if operating > 0.0 else 0.0
    return OperatingCurrent(
        operating_amps=operating,
        full_load_amps=full_load_amps,
        load_fraction=load_fraction,
        magnetizing_amps=magnetizing,
        load_amps=load_amps,
        apparent_power_factor=apparent_pf,
        linear_reference_amps=full_load_amps * load_fraction,
        basis=basis,
    )


def _phasor_from_magnetizing_fraction(
    *,
    full_load_amps: float,
    magnetizing_fraction: float,
    load_fraction: float,
) -> OperatingCurrent:
    """Operator override path: magnetizing fraction supplied directly.

    Some vendor performance sheets publish ``I_NL / I_FL`` (no-load /
    full-load current ratio) rather than the machine's power factor.
    That number is the same physical quantity as ``I_\u03bc / I_FL`` for
    an induction motor at rated voltage, so we accept either.
    """
    if not 0.0 <= magnetizing_fraction < 1.0:
        raise ValueError(
            f"magnetizing_fraction must be in [0, 1), got "
            f"{magnetizing_fraction}"
        )
    magnetizing = full_load_amps * magnetizing_fraction
    # Derive I_L from the phasor identity at nameplate rather than
    # requiring PF as a second input; the two are equivalent.
    load_component_at_fl = full_load_amps * math.sqrt(
        1.0 - magnetizing_fraction**2
    )
    load_amps = load_fraction * load_component_at_fl
    operating = math.sqrt(magnetizing**2 + load_amps**2)
    apparent_pf = load_amps / operating if operating > 0.0 else 0.0
    return OperatingCurrent(
        operating_amps=operating,
        full_load_amps=full_load_amps,
        load_fraction=load_fraction,
        magnetizing_amps=magnetizing,
        load_amps=load_amps,
        apparent_power_factor=apparent_pf,
        linear_reference_amps=full_load_amps * load_fraction,
        basis="phasor_from_operator_magnetizing_fraction",
    )


def _pmm_nearly_linear(
    *,
    full_load_amps: float,
    load_fraction: float,
    residual_magnetizing_fraction: float,
) -> OperatingCurrent:
    """PMM path. Magnets supply excitation; the stator only carries
    torque current plus a small controller / iron-loss residual. The
    residual is configurable but defaults to zero so the model does not
    fabricate a magnetizing branch on a machine that does not have one.
    """
    if not 0.0 <= residual_magnetizing_fraction < 1.0:
        raise ValueError(
            "residual_magnetizing_fraction must be in [0, 1), got "
            f"{residual_magnetizing_fraction}"
        )
    magnetizing = full_load_amps * residual_magnetizing_fraction
    # For a PMM at unity PF, the load component at full load is the full
    # I_FL by the phasor identity with I_mu = residual.
    load_component_at_fl = full_load_amps * math.sqrt(
        1.0 - residual_magnetizing_fraction**2
    )
    load_amps = load_fraction * load_component_at_fl
    operating = math.sqrt(magnetizing**2 + load_amps**2)
    apparent_pf = load_amps / operating if operating > 0.0 else 0.0
    return OperatingCurrent(
        operating_amps=operating,
        full_load_amps=full_load_amps,
        load_fraction=load_fraction,
        magnetizing_amps=magnetizing,
        load_amps=load_amps,
        apparent_power_factor=apparent_pf,
        linear_reference_amps=full_load_amps * load_fraction,
        basis="pmm_nearly_linear",
    )


def _nameplate_linear(
    *,
    nameplate_amps: float,
    load_fraction: float,
) -> OperatingCurrent:
    """Fallback: no PF, no operator override. Linear screening
    approximation, matches the pre-existing behaviour so the fallback
    surface stays identical when data is missing.
    """
    operating = nameplate_amps * load_fraction
    return OperatingCurrent(
        operating_amps=operating,
        full_load_amps=nameplate_amps,
        load_fraction=load_fraction,
        magnetizing_amps=0.0,
        load_amps=operating,
        apparent_power_factor=1.0,
        linear_reference_amps=operating,
        basis="nameplate_linear_scaling",
    )


def compute_operating_current(
    *,
    load_fraction: float,
    full_load_amps: float | None,
    nameplate_amps: float,
    power_factor: float | None,
    is_permanent_magnet: bool,
    operator_magnetizing_fraction: float | None = None,
    pmm_residual_magnetizing_fraction: float = 0.0,
    b14_1_source_note: str = "",
) -> OperatingCurrent:
    """Route to the right physics form given what data is available.

    Precedence (highest = most vendor-grounded):

    1. Operator override supplies a magnetizing fraction directly.
    2. B.14.1 I_FL is available AND PF is cataloged \u2192 phasor from PF.
    3. Motor is a PMM \u2192 near-linear with configurable residual.
    4. Neither PF nor override \u2192 linear nameplate scaling, disclosed
       as such.

    ``b14_1_source_note`` is a display hint kept alongside the returned
    basis so the disclosure text can mention operator-supplied vs
    cataloged inputs without re-deriving the routing decision here.
    """
    if load_fraction < 0.0:
        raise ValueError(
            f"load_fraction must be nonnegative, got {load_fraction}"
        )

    # Operator override wins over PF-derivation: an operator holding a
    # measured no-load current curve is a more direct source than the
    # phasor identity applied to a cataloged nameplate PF.
    if operator_magnetizing_fraction is not None and full_load_amps is not None:
        return _phasor_from_magnetizing_fraction(
            full_load_amps=full_load_amps,
            magnetizing_fraction=operator_magnetizing_fraction,
            load_fraction=load_fraction,
        )

    if is_permanent_magnet and full_load_amps is not None:
        return _pmm_nearly_linear(
            full_load_amps=full_load_amps,
            load_fraction=load_fraction,
            residual_magnetizing_fraction=pmm_residual_magnetizing_fraction,
        )

    if full_load_amps is not None and power_factor is not None:
        basis: LoadCurrentBasis = "phasor_from_cataloged_power_factor"
        return _phasor_from_power_factor(
            full_load_amps=full_load_amps,
            power_factor=power_factor,
            load_fraction=load_fraction,
            basis=basis,
        )

    return _nameplate_linear(
        nameplate_amps=nameplate_amps,
        load_fraction=load_fraction,
    )
