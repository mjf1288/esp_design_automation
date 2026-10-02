"""Post-selection shaft, stage-count, and thrust-data mechanical gate.

Implements physics-reference.md §5.5 and framework §3.2 / §6C.4.

Framework v0.6 §6C.4 changed the reference quantity for the shaft check, and the
change is not cosmetic. The check is against **motor nameplate power**, not
against the operating load:

    Shaft and bearing strength limit  >=  100% of motor nameplate power

The reasoning is a failure-mode argument rather than a margin preference. The
motor is physically capable of delivering nameplate power. If anything drives it
there -- a frequency increase, a change in fluid properties, a restart -- a shaft
rated below that point fractures. That is breakage, not overload followed by
gradual degradation, so there is no graded response available and no operating
margin that protects against it. Sizing to the operating point leaves the design
with no protection against the motor's own capability.

This module previously checked ``(bhp + gas_handling_hp) x safety_factor`` with
the factor defaulting to 1.0, i.e. the bare hydraulic load. On the demo wells the
nameplate is only about 25% of the pump shaft limit, so switching reference
quantities changes no current verdict -- the margin is wide enough to absorb a
4x more conservative test. It is not vacuous, though: three cataloged pumps
(GN3200 and SN3600 at 256 hp, HN13500 at 375 hp) have shaft limits below the
largest motor that physically fits their housing, so the rule binds as soon as a
higher-load well is sized.

§6C.4 is also explicit that an exceeded limit is a WARNING and not a rejection:
"the system displays the figures and warns, but does not reject the
configuration." That is a deliberate widening of engine output. See
``check_mechanical`` for how the two rules are reconciled with §3.2 listing shaft
strength as a Hard constraint.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .catalog import PumpModel, SealModel
from .config import MechanicalThresholds
from .units import require_positive


class MechanicalCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    # §6C.4's display table wants all three quantities visible, because the
    # engineer's next action depends on which one is binding: the strength limit
    # is a build-selection question, the operating load is a hydraulics question,
    # and the nameplate is a motor-selection question.
    shaft_hp_required: float
    shaft_hp_available: float
    shaft_hp_utilization: float
    motor_nameplate_hp: float | None = None
    shaft_hp_operating_load: float | None = None
    shaft_nameplate_utilization: float | None = None
    shaft_nameplate_check_possible: bool = False
    build_variants_available: tuple[str, ...] = ()
    thrust_load_lb: float | None
    thrust_capacity_lb: float | None
    thrust_utilization: float | None
    passed: bool
    failures: list[str]
    warnings: list[str]


def check_mechanical(
    *,
    pump: PumpModel,
    seal: SealModel | None,
    stages: int,
    bhp_total: float,
    gas_handling_hp: float,
    frequency_hz: float,
    thresholds: MechanicalThresholds,
    motor_nameplate_hp: float | None = None,
    head_developed_ft: float | None = None,
    mixture_sg: float | None = None,
    thrust_path_override: str | None = None,
) -> MechanicalCheck:
    """Check only catalog-supported limits; never invent a thrust-load equation.

    ``motor_nameplate_hp`` is the §6C.4 reference quantity. It is optional rather
    than required because ``size_motor`` can legitimately return None (no motor
    fits the casing), and in that case there is no nameplate to check against.
    When it is absent the nameplate check is reported as impossible instead of
    being silently skipped -- a skipped safety check that leaves no trace is
    indistinguishable in the report from a passed one.
    """
    if stages < 1:
        raise ValueError(f"stages must be at least 1, got {stages}")
    require_positive("bhp_total", bhp_total)
    if gas_handling_hp < 0:
        raise ValueError(f"gas_handling_hp must be nonnegative, got {gas_handling_hp}")
    require_positive("frequency_hz", frequency_hz)
    require_positive("thresholds.shaft_hp_safety_factor", thresholds.shaft_hp_safety_factor)
    require_positive("thresholds.thrust_safety_factor", thresholds.thrust_safety_factor)

    # The operating load, retained for display (§6C.4's middle row) and for the
    # frequency-reference warning. It is no longer what the check is against.
    operating_load = (bhp_total + gas_handling_hp) * thresholds.shaft_hp_safety_factor

    # §6C.4: the limit is the weakest link across every shaft-bearing component
    # -- pump sections, seal section, motor. Only the pump and seal publish a
    # rating here; the motor's own shaft rating is not a cataloged field, which is
    # disclosed below rather than assumed to be non-binding.
    shaft_limits = [pump.shaft_hp_limit]
    if seal is not None and seal.max_shaft_hp is not None:
        shaft_limits.append(seal.max_shaft_hp)
    shaft_available = min(shaft_limits)

    # §6C.4: check against what the motor CAN deliver, not what it is delivering.
    nameplate_possible = motor_nameplate_hp is not None and motor_nameplate_hp > 0
    if nameplate_possible:
        shaft_required = float(motor_nameplate_hp)
        nameplate_utilization: float | None = shaft_required / shaft_available
    else:
        # No motor was selected, so nameplate is unavailable. Fall back to the
        # operating load so the field is still populated, and record that the
        # governing check could not be performed.
        shaft_required = operating_load
        nameplate_utilization = None
    shaft_utilization = shaft_required / shaft_available

    capacities = [capacity for capacity in [pump.thrust_bearing_capacity_lb, seal.thrust_bearing_capacity_lb if seal else None] if capacity is not None]
    thrust_capacity = min(capacities) if capacities else None
    # The binding interface has no axial-flow/thrust input. Reporting a fake load
    # would look precise but be physically indefensible; retain the data gap.
    thrust_load: float | None = None
    thrust_utilization: float | None = None
    if head_developed_ft is not None and mixture_sg is not None:
        from .expert_decisions import thrust_assessment
        thrust = thrust_assessment(pump, seal, head_ft=head_developed_ft, sg=mixture_sg,
            stages=stages, path_override=thrust_path_override)
        thrust_load = thrust["load_lb"]
        thrust_capacity = thrust["capacity_lb"]
        thrust_utilization = thrust_load/thrust_capacity if thrust_load is not None and thrust_capacity else None

    failures: list[str] = []
    warnings: list[str] = []
    if stages > pump.max_stages:
        failures.append(
            f"stages required {stages} exceeds pump {pump.model} maximum {pump.max_stages} "
            "-> reduce TDH, increase frequency only within vendor limits, or select a higher-head/longer housing"
        )
    if stages > thresholds.max_stages_per_housing:
        failures.append(
            f"stages required {stages} exceeds configured housing limit {thresholds.max_stages_per_housing} "
            "-> reduce stages through a higher-head pump or select a housing approved for more stages"
        )
    # §6C.4: a warning, not a rejection. "The system displays the figures and
    # warns, but does not reject the configuration." This is a deliberate
    # widening: §3.2 still lists shaft strength as a Hard constraint, but the
    # resolution path §3.2 gives for it is build-variant selection, not
    # elimination from the candidate list. Removing the configuration outright
    # would hide from the engineer the fact that a large-shaft build of the same
    # pump -- identical hydraulics, identical curve -- may resolve it.
    #
    # The warning is worded for the failure mode rather than as a generic
    # overload, because the two are not the same conversation.
    if nameplate_possible and shaft_required > shaft_available:
        warnings.append(
            f"SHAFT FRACTURE RISK: motor nameplate {shaft_required:.1f} hp exceeds "
            f"the weakest cataloged shaft/bearing rating in the string "
            f"({shaft_available:.1f} hp, pump {pump.model}"
            + (" or its protector" if len(shaft_limits) > 1 else "")
            + "). Per framework 6C.4 the shaft is checked against what the motor "
            "CAN deliver, not against the present operating load of "
            f"{operating_load:.1f} hp. A frequency increase, a fluid-property "
            "change or a restart can drive the motor to nameplate, and a shaft "
            "below that fractures rather than degrading. Resolve by selecting a "
            "large-shaft or high-strength build of the same pump (hydraulics are "
            "unchanged), or by selecting a smaller motor. This configuration is "
            "reported rather than rejected so that the build option remains "
            "visible; it is not cleared for install."
        )
    if not nameplate_possible:
        warnings.append(
            "the 6C.4 shaft check against motor nameplate could not be performed "
            "because no motor was selected for this configuration; the shaft "
            f"utilization shown is against the operating load ({operating_load:.1f} hp) "
            "and is therefore not the governing check"
        )
    # Framework 3.2 Principle 2: insufficient shaft strength should escalate
    # through builds of the same pump (standard -> large shaft -> high strength)
    # before returning to hydraulics. No cataloged pump carries build variants,
    # so that escalation cannot be attempted here. Disclosed unconditionally
    # rather than only on overload, because the absence of the escalation path is
    # a property of the catalog, not of this well.
    if nameplate_possible and shaft_required > shaft_available:
        warnings.append(
            "no shaft build variants (large shaft / high strength) are cataloged "
            "for any pump, so the framework 3.2 Principle 2 escalation path could "
            "not be attempted. Load build variants into the catalog to make this "
            "resolvable without returning to hydraulic sizing."
        )
    if frequency_hz != pump.frequency_ref_hz:
        warnings.append(
            f"shaft rating {shaft_available:.1f} hp is cataloged at reference conditions; "
            f"confirm vendor torque/shaft rating at {frequency_hz:g} Hz"
        )
    if thrust_capacity is None:
        warnings.append(
            "no numerical pump/protector thrust-bearing capacity is cataloged; obtain vendor thrust limits before release"
        )
    elif thrust_load is None:
        warnings.append(
            f"minimum catalog thrust-bearing capacity is {thrust_capacity:.0f} lb, but the interface has no axial thrust-load model; "
            "verify downthrust/upthrust load against vendor data before release"
        )
    elif thrust_utilization is not None and thrust_utilization > 1:
        warnings.append(f"Protector thrust {thrust_load:.1f} lb exceeds capacity {thrust_capacity:.1f} lb by {thrust_load-thrust_capacity:.1f} lb. Recommend tandem; warning only, single section remains selectable.")

    return MechanicalCheck(
        shaft_hp_required=shaft_required,
        shaft_hp_available=shaft_available,
        shaft_hp_utilization=shaft_utilization,
        motor_nameplate_hp=motor_nameplate_hp,
        shaft_hp_operating_load=operating_load,
        shaft_nameplate_utilization=nameplate_utilization,
        shaft_nameplate_check_possible=nameplate_possible,
        build_variants_available=(),
        thrust_load_lb=thrust_load,
        thrust_capacity_lb=thrust_capacity,
        thrust_utilization=thrust_utilization,
        passed=not failures,
        failures=failures,
        warnings=warnings,
    )
