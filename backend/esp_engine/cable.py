"""Temperature-corrected three-phase ESP cable sizing and surface kVA screen.

Implements physics-reference.md §7 and the assembly-clearance principle in §8.1.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .cable_thermal import CableSelfHeating, estimate_cable_self_heating
from .catalog import CableModel, Catalog
from .config import ElectricalThresholds
from .motor import MotorSizing
from .units import SQRT3, require_positive


class CableSizing(BaseModel):
    model_config = ConfigDict(frozen=True)

    cable: CableModel
    length_ft: float
    voltage_drop_v: float
    voltage_drop_frac: float
    surface_voltage_required_v: float
    ampacity_a: float
    ampacity_utilization: float
    conductor_temp_f: float
    self_heating: CableSelfHeating
    kva_required: float
    passed: bool
    warnings: list[str]
    decision_surface: list[dict] = []
    sizing_current_a: float | None = None


def _installed_od_in(cable: CableModel) -> float | None:
    """Prefer flat cable because it is normally banded alongside ESP equipment."""
    return cable.od_flat_in if cable.od_flat_in is not None else cable.od_round_in


def _voltage_drop_v(cable: CableModel, *, length_ft: float, current_a: float, conductor_temp_f: float) -> float:
    temperature_factor = 1.0 + cable.temp_coeff_per_f * (conductor_temp_f - 77.0)
    if temperature_factor <= 0:
        raise ValueError(
            f"cable {cable.id} temperature correction factor is nonphysical ({temperature_factor:g})"
        )
    resistance = cable.resistance_ohm_per_1000ft_at_77f * length_ft / 1000.0 * temperature_factor
    return SQRT3 * current_a * resistance


def size_cable(
    *,
    catalog: Catalog,
    motor: MotorSizing,
    setting_depth_md_ft: float,
    surface_lead_ft: float = 100.0,
    casing_id_in: float,
    pump_od_in: float,
    fluid_temp_f: float,
    cooling_velocity_ft_s: float,
    water_cut_frac: float,
    thresholds: ElectricalThresholds,
    available_surface_voltage_v: float | None = None,
    sizing_current_a: float | None = None,
    selected_cable_id: str | None = None,
) -> CableSizing | None:
    """Choose the physically smallest catalog cable passing all hard screens.

    Framework §6D.2 requires the cable check to be against a *calculated*
    cable temperature, not the fluid temperature. The per-candidate rise
    is computed inside the loop from the candidate's own ampacity and
    the annular cooling velocity, and the calculated conductor
    temperature is what the insulation-class screen and the voltage-drop
    resistivity correction both use.
    """
    require_positive("setting_depth_md_ft", setting_depth_md_ft)
    if surface_lead_ft < 0:
        raise ValueError(f"surface_lead_ft must be nonnegative, got {surface_lead_ft}")
    require_positive("casing_id_in", casing_id_in)
    require_positive("pump_od_in", pump_od_in)
    require_positive("fluid_temp_f", fluid_temp_f)
    require_positive("cooling_velocity_ft_s", cooling_velocity_ft_s)
    if not 0.0 <= water_cut_frac <= 1.0:
        raise ValueError(f"water_cut_frac must be in [0, 1], got {water_cut_frac}")
    require_positive("thresholds.max_cable_voltage_drop_frac", thresholds.max_cable_voltage_drop_frac)
    require_positive("thresholds.cable_ampacity_derate", thresholds.cable_ampacity_derate)
    if available_surface_voltage_v is not None:
        require_positive("available_surface_voltage_v", available_surface_voltage_v)

    length = setting_depth_md_ft + surface_lead_ft
    current = sizing_current_a if sizing_current_a is not None else motor.operating_amps
    if current < 0:
        raise ValueError(f"motor.operating_amps must be nonnegative, got {current}")
    if motor.operating_volts <= 0:
        raise ValueError(f"motor.operating_volts must be positive, got {motor.operating_volts}")

    passing: list[
        tuple[CableModel, float, float, float, float, CableSelfHeating]
    ] = []
    for cable in catalog.cables:
        installed_od = _installed_od_in(cable)
        if installed_od is None:
            continue
        # Conservative side-by-side clearance screen. Detailed bands, guards and
        # coupling upsets remain a separate geometry tally gate.
        if max(pump_od_in, motor.motor.od_in) + installed_od >= casing_id_in:
            continue
        ampacity = cable.ampacity_a * thresholds.cable_ampacity_derate
        # §6D.2 row 4: calculated cable temperature, not fluid temperature.
        # Computed per-candidate because the rise depends on the specific
        # cable's ampacity (through I/I_amp) and its temperature
        # coefficient (through resistance amplification).
        self_heating = estimate_cable_self_heating(
            fluid_temp_f=fluid_temp_f,
            cooling_velocity_ft_s=cooling_velocity_ft_s,
            current_a=current,
            ampacity_a=ampacity,
            water_cut_frac=water_cut_frac,
            conductor_temp_coeff_per_f=cable.temp_coeff_per_f,
        )
        voltage_drop = _voltage_drop_v(
            cable,
            length_ft=length,
            current_a=current,
            conductor_temp_f=self_heating.conductor_temp_f,
        )
        drop_frac = voltage_drop / motor.operating_volts
        surface_required = motor.operating_volts + voltage_drop
        passing.append(
            (cable, ampacity, voltage_drop, drop_frac, surface_required, self_heating)
        )

    if not passing:
        return None

    # The 30 V/1000 ft figure is vendor RECOMMENDED PRACTICE, not a physical
    # limit, so it is applied as a preference rather than a hard filter. Cables
    # meeting it are preferred; if none does, the largest available conductor is
    # returned with an explicit warning. Treating a guideline as a hard gate
    # would report "no cable exists" for a well that is routinely cabled in the
    # field, and would hide the real tradeoff from the engineer.
    within_guideline = [
        item
        for item in passing
        if item[2] / (length / 1000.0) <= thresholds.max_voltage_drop_v_per_1000ft
        and current <= item[1]
        and item[5].conductor_temp_f <= item[0].max_temp_f - 25
        and item[3] <= thresholds.max_cable_voltage_drop_frac
        and (available_surface_voltage_v is None or item[4] <= available_surface_voltage_v)
    ]
    selected = next((x for x in passing if x[0].id == selected_cable_id), None)
    if selected_cable_id and selected is None:
        return None  # Explicit selected hardware is absent or cannot physically fit.
    if selected is not None:
        cable, ampacity, voltage_drop, drop_frac, surface_required, self_heating = selected
    elif within_guideline:
        # Larger AWG number / fewer circular mils is the physically smallest cable.
        cable, ampacity, voltage_drop, drop_frac, surface_required, self_heating = min(
            within_guideline,
            key=lambda item: (-item[0].awg, item[0].conductor_area_cmil, item[0].id),
        )
    else:
        # Nothing meets the guideline: take the lowest-drop (largest) conductor.
        cable, ampacity, voltage_drop, drop_frac, surface_required, self_heating = min(
            passing, key=lambda item: (item[2], item[0].id)
        )
    voltage_per_kft = voltage_drop / length * 1000.0
    warnings: list[str] = []
    rows = []
    for c, amp, drop, fraction, surface, thermal in passing:
        findings = []
        for name, actual, limit, unit in [
            ("ampacity", current, amp, "A"),
            ("voltage_drop", fraction, thresholds.max_cable_voltage_drop_frac, "fraction"),
            ("insulation_buffer", thermal.conductor_temp_f, c.max_temp_f - 25, "F"),
        ]:
            if actual > limit:
                findings.append({"quantity": name, "actual": actual, "limit": limit, "margin": actual-limit, "unit": unit})
        if available_surface_voltage_v is not None and surface > available_surface_voltage_v:
            findings.append({"quantity":"surface_voltage", "actual":surface, "limit":available_surface_voltage_v, "margin":surface-available_surface_voltage_v, "unit":"V"})
        rows.append({
            "cable_id": c.id, "awg": c.awg, "synthetic": c.synthetic,
            "current_a": current, "ampacity_a": amp,
            "ampacity_margin_a": amp-current, "voltage_drop_frac": fraction,
            "voltage_drop_v": drop, "conductor_temp_f": thermal.conductor_temp_f,
            "temperature_limit_f": c.max_temp_f, "insulation_margin_f": c.max_temp_f-thermal.conductor_temp_f,
            "surface_voltage_required_v": surface, "warnings":findings,
            "selected":c.id==cable.id, "recommended":bool(within_guideline and c.id == min(within_guideline, key=lambda x:(-x[0].awg,x[0].id))[0].id),
            "price_per_ft":c.price_per_ft, "total_price":c.price_per_ft*length if c.price_per_ft is not None else None,
        })
        if c.id == cable.id:
            warnings.extend(f"{f['quantity']}: actual {f['actual']:.3f}, limit {f['limit']:.3f}, exceedance {f['margin']:.3f} {f['unit']}. Warning only; engineer may select." for f in findings)
    if voltage_per_kft > thresholds.max_voltage_drop_v_per_1000ft:
        warnings.append(
            f"voltage drop {voltage_per_kft:.1f} V/kft exceeds the "
            f"{thresholds.max_voltage_drop_v_per_1000ft:.0f} V/kft vendor recommended "
            "practice; this is the largest conductor available in the catalog that "
            "otherwise fits, so verify motor starting voltage and consider a larger "
            "conductor or a shallower setting depth"
        )
    if available_surface_voltage_v is None:
        warnings.append("surface supply voltage was not provided; verify VSD/transformer voltage and transient starting margin")
    installed_od = _installed_od_in(cable)
    assert installed_od is not None
    warnings.append(
        f"assembly clearance screen uses pump OD {pump_od_in:.2f} in + cable profile {installed_od:.2f} in; "
        "verify bands, guards, couplings, restrictions, and drift separately"
    )
    return CableSizing(
        cable=cable,
        length_ft=length,
        voltage_drop_v=voltage_drop,
        voltage_drop_frac=drop_frac,
        surface_voltage_required_v=surface_required,
        ampacity_a=ampacity,
        ampacity_utilization=current / ampacity,
        conductor_temp_f=self_heating.conductor_temp_f,
        self_heating=self_heating,
        kva_required=SQRT3 * surface_required * current / 1000.0,
        passed=current <= ampacity and drop_frac <= thresholds.max_cable_voltage_drop_frac and self_heating.conductor_temp_f <= cable.max_temp_f,
        warnings=warnings,
        decision_surface=rows,
        sizing_current_a=current,
    )
