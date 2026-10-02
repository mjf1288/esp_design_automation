"""ESP string assembly and compatibility checks.

Framework refs: §12 (string assembly: tubing -> head -> pump sections -> GH/GS/
intake -> seal -> motor -> sensor, then compatibility and make-up check).

The string is where a design that passed every individual calculation can still
be unbuildable: shaft couplings that do not mate across series, a housing length
that will not clear the wellhead, a total string length that changes the setting
depth you designed for. These are the errors that show up on the rig floor.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .catalog import (
    Catalog,
    CableModel,
    GasHandlingModel,
    MotorModel,
    PumpModel,
    SealModel,
    is_estimated_record,
)
from .results import ConstraintViolation, PumpConfiguration

# Component lengths not carried in the seed catalog. Screening values from
# typical ESP equipment, used only for string length and wellhead clearance
# estimates — never for load calculations.
_DISCHARGE_HEAD_FT = 2.0
_INTAKE_FT = 1.5
_SENSOR_FT = 2.5
_STAGES_PER_FT = 12.0  # ~1 in per stage for mid-series pumps
_GAS_DEVICE_FT = 3.0  # not carried in the seed catalog
_MAX_HOUSING_STAGES = 200  # above this, split into tandem housings


class StringComponent(BaseModel):
    """One item in the run-in-hole order."""

    position: int
    component_type: str
    description: str
    part_id: str | None = None
    od_in: float | None = None
    length_ft: float | None = None
    note: str | None = None
    is_estimate: bool = Field(
        default=False,
        description="True when a dimension is a screening estimate rather than "
        "catalog data. Rendered distinctly so a rig hand never mistakes an "
        "estimate for a tally.",
    )


class StringAssembly(BaseModel):
    components: list[StringComponent]
    total_length_ft: float
    max_od_in: float
    pump_housing_count: int
    violations: list[ConstraintViolation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    length_is_estimate: bool = True


def build_string(
    config: PumpConfiguration, catalog: Catalog, casing_id_in: float | None = None
) -> StringAssembly:
    """Assemble the string in §12 order, top to bottom (run-in-hole order)."""
    components: list[StringComponent] = []
    violations: list[ConstraintViolation] = []
    warnings: list[str] = []
    pos = 1

    pump: PumpModel = catalog.pump(config.pump_id)

    def add(**kwargs) -> None:
        nonlocal pos
        components.append(StringComponent(position=pos, **kwargs))
        pos += 1

    # --- Tubing ------------------------------------------------------------
    add(
        component_type="tubing",
        description="Production tubing to setting depth",
        length_ft=config.setting_depth_md_ft,
        note="Tubing size and grade from the completion design; not selected here.",
        is_estimate=False,
    )

    # --- Discharge head ----------------------------------------------------
    add(
        component_type="discharge_head",
        description=f"Discharge head, series {pump.series}",
        od_in=pump.housing_od_in,
        length_ft=_DISCHARGE_HEAD_FT,
        is_estimate=True,
    )

    # --- Pump sections -----------------------------------------------------
    housings = _split_housings(config.stages)
    for i, stages in enumerate(housings, start=1):
        suffix = f" (upper)" if i == 1 and len(housings) > 1 else (
            f" (lower)" if i == len(housings) and len(housings) > 1 else ""
        )
        add(
            component_type="pump",
            description=f"{pump.manufacturer} {pump.model}, {stages} stages{suffix}",
            part_id=pump.id,
            od_in=pump.housing_od_in,
            length_ft=stages / _STAGES_PER_FT,
            note=f"Stage type: {pump.stage_type}",
            is_estimate=True,
        )

    # --- Gas handling / separator / intake ---------------------------------
    gas_device: GasHandlingModel | None = None
    if config.gas_handling_id:
        gas_device = next(
            (g for g in catalog.gas_handling if g.id == config.gas_handling_id), None
        )
    if gas_device is not None:
        add(
            component_type="gas_handling",
            description=f"{gas_device.id} ({gas_device.type})",
            part_id=gas_device.id,
            od_in=gas_device.od_in,
            length_ft=_GAS_DEVICE_FT,
            note=_gas_device_note(gas_device),
            is_estimate=True,
        )
    else:
        add(
            component_type="intake",
            description=f"Standard bolt-on intake, series {pump.series}",
            od_in=pump.housing_od_in,
            length_ft=_INTAKE_FT,
            note="No gas-handling device required at the computed intake conditions.",
            is_estimate=True,
        )

    # --- Seal / protector --------------------------------------------------
    seal: SealModel | None = None
    if config.seal_id:
        seal = next((s for s in catalog.seals if s.id == config.seal_id), None)
    if seal is None:
        seal = next(iter(catalog.seals_for_series(pump.series)), None)
    if seal is not None:
        add(
            component_type="seal",
            description=f"{seal.id} protector, series {seal.series}",
            part_id=seal.id,
            od_in=seal.od_in,
            length_ft=seal.length_ft,
            note=(
                f"Thrust capacity {seal.thrust_bearing_capacity_lb:,.0f} lb"
                if seal.thrust_bearing_capacity_lb
                else "Thrust capacity not published for this record"
            ),
            is_estimate=is_estimated_record(seal),
        )
    else:
        violations.append(
            ConstraintViolation(
                constraint_type="availability",
                rigidity="soft",
                message=(
                    f"No series-{pump.series} protector in the catalog. The string "
                    f"is incomplete: a seal section is mandatory."
                ),
                remedy_hint="Add a compatible protector to the catalog, or change series.",
            )
        )

    # --- Motor -------------------------------------------------------------
    motor: MotorModel | None = None
    if config.motor_id:
        motor = next((m for m in catalog.motors if m.id == config.motor_id), None)
    if motor is not None:
        add(
            component_type="motor",
            description=(
                f"{motor.manufacturer} series {motor.series}, {motor.hp:g} hp, "
                f"{motor.volts:g} V / {motor.amps:g} A"
            ),
            part_id=motor.id,
            od_in=motor.od_in,
            length_ft=motor.length_ft,
            note=f"Maximum winding temperature {motor.max_winding_temp_f:.0f} F",
            is_estimate=is_estimated_record(motor),
        )

    # --- Sensor ------------------------------------------------------------
    add(
        component_type="sensor",
        description="Downhole gauge (PIP, discharge pressure, motor winding temp)",
        od_in=motor.od_in if motor else pump.housing_od_in,
        length_ft=_SENSOR_FT,
        note=(
            "Optional in scope terms, but this is the instrument that feeds the "
            "empirical layer (§8.3 surveillance feeds design). Omitting it means "
            "this well teaches the system nothing."
        ),
        is_estimate=True,
    )

    # --- Cable (runs alongside, not in series) -----------------------------
    cable: CableModel | None = None
    if config.cable_id:
        cable = next((c for c in catalog.cables if c.id == config.cable_id), None)
    if cable is not None:
        add(
            component_type="cable",
            description=(
                f"{cable.awg} AWG "
                f"{'flat' if cable.od_flat_in else 'round'} cable, "
                f"{cable.ampacity_a:.0f} A ampacity, rated {cable.max_temp_f:.0f} F"
                + (f", {cable.armor} armor" if cable.armor else "")
            ),
            part_id=cable.id,
            od_in=cable.od_flat_in or cable.od_round_in,
            length_ft=config.setting_depth_md_ft + 100.0,
            note="Banded to the tubing; not part of the string OD stack.",
            is_estimate=is_estimated_record(cable),
        )

    # --- Totals and checks -------------------------------------------------
    in_string = [c for c in components if c.component_type not in {"tubing", "cable"}]
    total_length = sum(c.length_ft or 0.0 for c in in_string)
    max_od = max((c.od_in or 0.0 for c in in_string), default=0.0)

    v2, w2 = check_compatibility(
        config=config,
        pump=pump,
        motor=motor,
        seal=seal,
        gas_device=gas_device,
        cable=cable,
        total_length_ft=total_length,
        max_od_in=max_od,
        casing_id_in=casing_id_in,
        housing_count=len(housings),
    )
    violations.extend(v2)
    warnings.extend(w2)

    return StringAssembly(
        components=components,
        total_length_ft=total_length,
        max_od_in=max_od,
        pump_housing_count=len(housings),
        violations=violations,
        warnings=warnings,
        length_is_estimate=any(c.is_estimate for c in in_string),
    )


def check_compatibility(
    *,
    config: PumpConfiguration,
    pump: PumpModel,
    motor: MotorModel | None,
    seal: SealModel | None,
    gas_device: GasHandlingModel | None,
    cable: CableModel | None,
    total_length_ft: float,
    max_od_in: float,
    casing_id_in: float | None,
    housing_count: int,
) -> tuple[list[ConstraintViolation], list[str]]:
    """§12 compatibility and make-up check.

    Series matching is treated as a HARD constraint rather than inferred. Group C
    deliberately refused to invent cross-series compatibility mappings, and that
    restraint is respected here: a mismatch is reported as unverified, not
    silently resolved. An invented compatibility table would be the single most
    dangerous fabrication this system could produce.
    """
    violations: list[ConstraintViolation] = []
    warnings: list[str] = []

    for component, label in ((motor, "motor"), (seal, "protector"), (gas_device, "gas-handling device")):
        if component is None:
            continue
        if getattr(component, "series", None) != pump.series:
            violations.append(
                ConstraintViolation(
                    constraint_type="mechanical",
                    rigidity="hard",
                    message=(
                        f"Series mismatch: pump is series {pump.series} but the "
                        f"{label} is series {getattr(component, 'series', 'unknown')}. "
                        f"Cross-series make-up requires a vendor-confirmed adapter; "
                        f"no compatibility mapping is asserted here."
                    ),
                    remedy_hint=(
                        f"Select a series-{pump.series} {label}, or obtain vendor "
                        f"confirmation of an adapter and record it as an override."
                    ),
                )
            )

    if casing_id_in is not None and max_od_in > 0:
        clearance = casing_id_in - max_od_in
        if clearance < 0.20:
            violations.append(
                ConstraintViolation(
                    constraint_type="geometry",
                    rigidity="absolute",
                    physical_impossibility=True,
                    message=(
                        f"Assembled string maximum OD is {max_od_in:.2f} in, leaving "
                        f"{clearance:.3f} in clearance in {casing_id_in:.3f} in "
                        f"casing. The controlling component may be the motor or the "
                        f"gas-handling device, not the pump."
                    ),
                    actual_value=clearance,
                    limit_value=0.20,
                    unit="in",
                    remedy_hint="Select a smaller motor or gas-handling device.",
                )
            )

    if housing_count > 1:
        warnings.append(
            f"{config.stages} stages require {housing_count} pump housings in "
            f"tandem. Confirm the shaft rating for the full tandem string and the "
            f"available rig-floor handling length."
        )

    if total_length_ft > 120.0:
        warnings.append(
            f"Assembled string length is approximately {total_length_ft:.0f} ft. "
            f"Confirm the setting depth accounts for string length and that the "
            f"interval at depth is straight over the full length."
        )

    if cable is not None and motor is not None:
        if cable.max_temp_f < 250.0:
            warnings.append(
                f"Cable {cable.awg} is rated to {cable.max_temp_f:.0f} F. Verify "
                f"against the actual motor-nameplate temperature rise at depth."
            )

    warnings.append(
        "String lengths are screening estimates from typical equipment dimensions, "
        "not a vendor tally. A make-up tally is required before any field job."
    )
    return violations, warnings


def assemble_string(config: PumpConfiguration, catalog: Catalog) -> str:
    """One-line-per-component summary for the results view."""
    try:
        assembly = build_string(config, catalog)
    except (KeyError, ValueError) as exc:
        return f"String could not be assembled: {exc}"

    lines = []
    for c in assembly.components:
        if c.component_type == "cable":
            continue
        od = f"{c.od_in:.2f} in" if c.od_in else "-"
        lines.append(f"{c.position}. {c.component_type}: {c.description} [{od}]")
    lines.append(
        f"Assembled length approximately {assembly.total_length_ft:.0f} ft, "
        f"maximum OD {assembly.max_od_in:.2f} in ({assembly.pump_housing_count} "
        f"pump housing{'s' if assembly.pump_housing_count != 1 else ''})."
    )
    return "\n".join(lines)


def _gas_device_note(device: GasHandlingModel) -> str | None:
    """Published capability of the gas device, where the vendor states it."""
    parts: list[str] = []
    if device.max_free_gas_fraction_handled is not None:
        parts.append(
            f"rated to {device.max_free_gas_fraction_handled:.0%} free gas at intake"
        )
    if device.separation_efficiency_pct_range is not None:
        lo, hi = device.separation_efficiency_pct_range
        parts.append(f"published separation efficiency {lo:.0f}-{hi:.0f}%")
    if device.hp_consumed > 0:
        parts.append(f"consumes {device.hp_consumed:g} hp")
    return "; ".join(parts).capitalize() if parts else None


def _split_housings(stages: int) -> list[int]:
    """Split a stage count across housings.

    Real ESPs are limited by housing length and shaft strength, so a 340-stage
    pump ships as tandem housings. The split is reported because it changes the
    string length, the handling plan, and the shaft loading — all of which the
    engineer needs before the equipment arrives.
    """
    if stages <= _MAX_HOUSING_STAGES:
        return [stages]
    count = -(-stages // _MAX_HOUSING_STAGES)  # ceiling division
    base = stages // count
    remainder = stages % count
    return [base + (1 if i < remainder else 0) for i in range(count)]
