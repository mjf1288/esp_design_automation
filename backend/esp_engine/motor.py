"""Motor selection, constant-V/Hz scaling, and annular cooling screening.

Implements physics-reference.md §6 for framework §12 motor loading selection,
and the framework B.16 induction/permanent-magnet fork. The result is a
screening calculation; it does not substitute for a vendor motor thermal model
or load-current performance table.

B.16 makes motor type a fork rather than a substitution. Three things branch:

1. Full-load current. B.14.1 computes I_FL from HP, volts, power factor and
   efficiency. A PMM runs at near-unity power factor and higher efficiency, so
   carrying induction assumptions across systematically oversizes cable and
   transformer. When PF and efficiency are not cataloged, this module refuses
   the B.14.1 form and falls back to the declared nameplate-scaling
   approximation rather than assuming a power factor.
2. Thermal limits. An induction motor is bounded by winding insulation class.
   A PMM carries an *additional* magnet demagnetization limit, and exceeding
   it is irreversible rather than gradual. See ``MagnetGate``.
3. VSD requirement. A PMM cannot start across the line, so drive presence is
   not optional. Carried on ``MotorModel.vsd_required``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .catalog import Catalog, MotorModel
from .config import MotorThresholds
from .cooling import CoolingAssessment, assess_cooling
from .electrical_load import (
    LoadCurrentBasis,
    OperatingCurrent,
    compute_operating_current,
)
from .models import ElectricalConstraints
from .motor_thermal import MotorSelfHeating, estimate_motor_self_heating
from .units import SQRT3, annulus_area_ft2, bpd_to_ft_per_sec, require_positive

#: Outcome of the B.16 magnet demagnetization gate.
#:
#: There is deliberately no ``"pass"`` member. The only magnet-temperature
#: estimate available here is intake temperature, which is a *lower* bound on
#: the real magnet temperature (the magnet sits inside a motor that is heating
#: the fluid passing it). A lower bound can falsify the gate but can never
#: clear it, so the honest outcomes are "not applicable", "we cannot know",
#: and "already exceeded at the floor".
MagnetGate = Literal[
    "not_applicable",
    "limit_not_published",
    "indeterminate_needs_vendor_thermal_model",
    "exceeded_at_intake_temperature",
]

#: Which form produced the full-load reference current. Kept distinct
#: because the provenance of a current propagates into cable gauge and
#: surface voltage, and a reviewer must be able to tell a vendor-grounded
#: figure from a screening approximation without rerunning the calculation.
#:
#: The load-scaling that turns I_FL into an operating current is a
#: separate provenance carried on ``OperatingCurrent.basis``; both are
#: reported so the reviewer can see the full chain from nameplate to the
#: number that sized the cable.
AmpsBasis = Literal[
    "b14_1_from_power_factor_and_efficiency",
    "b14_1_from_operator_supplied_data",
    "nameplate_linear_scaling",
]


def _apply_override(motor: MotorModel, override) -> tuple[MotorModel, list[str]]:
    """Merge operator-supplied vendor data onto a catalog motor record.

    Re-validates through ``MotorModel`` rather than using ``model_copy``, so the
    catalog's own invariants still apply to operator data. In particular a
    demagnetization temperature supplied for an induction motor is rejected
    here, not quietly stored: it would make the B.16 magnet gate evaluate a
    machine with no magnets, and the operator has clearly confused two records.

    Returns the effective motor and the disclosure lines describing what was
    substituted. Overriding a value the catalog already publishes is allowed but
    always reported -- the operator may hold a purchase datasheet for their
    specific unit that differs from the generic published figure, and silently
    preferring one over the other is the decision this engine must not make for
    them.
    """
    if override is None:
        return motor, []
    updates: dict[str, float] = {}
    notes: list[str] = []
    for field in ("power_factor", "efficiency", "demag_temp_f"):
        tracked = getattr(override, field, None)
        if tracked is None:
            continue
        updates[field] = tracked.value
        published = getattr(motor, field)
        origin = f"source={tracked.source.value}, confidence={tracked.confidence:.2f}"
        if published is None:
            notes.append(
                f"{motor.id} {field} = {tracked.value} supplied by the operator "
                f"({origin}); not published in the catalog."
            )
        else:
            notes.append(
                f"{motor.id} {field} = {tracked.value} supplied by the operator "
                f"({origin}) REPLACES the cataloged value {published}. Both are "
                "claims about the same machine; the design uses the operator's."
            )
    if not updates:
        return motor, []
    effective = MotorModel.model_validate({**motor.model_dump(), **updates})
    return effective, notes


def _magnet_gate(motor: MotorModel, *, estimated_magnet_temp_floor_f: float) -> MagnetGate:
    """Evaluate the B.16 demagnetization gate without ever claiming a pass."""
    if not motor.is_permanent_magnet:
        return "not_applicable"
    if motor.demag_temp_f is None:
        return "limit_not_published"
    if estimated_magnet_temp_floor_f > motor.demag_temp_f:
        return "exceeded_at_intake_temperature"
    return "indeterminate_needs_vendor_thermal_model"


def _full_load_amps(motor: MotorModel, *, volts: float) -> tuple[float | None, AmpsBasis]:
    """B.14.1 I_FL = (HP x 746) / (sqrt(3) x V x PF x Eff), when data allows.

    Evaluated at the NAMEPLATE point on purpose, not at the operating point.

    Under constant V/Hz both horsepower and voltage scale with frequency, so
    full-load current is invariant: (HP.r x 746) / (sqrt3 x V.r x PF x Eff)
    cancels r. Passing the frequency-scaled voltage while leaving horsepower at
    its nameplate value does not cancel, and produces a current that drifts as
    1/r -- 7.7% low at 65 Hz and 20% high at 50 Hz. Low is the dangerous
    direction, because this current sizes the cable.

    ``volts`` is therefore accepted and deliberately unused for the ratio; it is
    kept in the signature so callers cannot pass an operating voltage believing
    it is being honored. Guarded below rather than dropped, so a caller passing
    a nonsense voltage still gets None instead of a plausible number.
    """
    if motor.power_factor is None or motor.efficiency is None:
        return None, "nameplate_linear_scaling"
    if volts <= 0 or motor.volts <= 0:
        return None, "nameplate_linear_scaling"
    denominator = SQRT3 * motor.volts * motor.power_factor * motor.efficiency
    if denominator <= 0:
        return None, "nameplate_linear_scaling"
    return (motor.hp * 746.0) / denominator, "b14_1_from_power_factor_and_efficiency"


def _reconcile_with_nameplate(
    b14_1_amps: float,
    nameplate_amps: float,
    *,
    tolerance_frac: float,
) -> tuple[float, float, str | None]:
    """Reconcile the B.14.1 current against the nameplate current.

    These are two vendor claims about the same machine and they should agree. On
    this catalog they do not: back-solving PF x Eff from the published
    HP / volts / amps triple gives 0.46 to 0.59 across the motors, which is far
    below any physically plausible ESP figure (B.16 puts induction near 0.70 and
    a PMM near 0.92). Something in the published triple is not a full-load
    quantity -- most likely the amps and volts come from different winding taps.

    That matters because the disagreement runs in the unsafe direction. Feeding
    a plausible PF and efficiency into B.14.1 returns a current 35-48% BELOW
    nameplate, which would shrink the cable and flatter the voltage drop. So the
    higher of the two is carried forward and the contradiction is reported. This
    is not conservatism for its own sake: choosing the lower of two
    contradictory currents to size a conductor is not a defensible engineering
    decision, and picking the one that improves the result is worse.

    Returns (amps_to_use, disagreement_frac, disclosure or None).
    """
    disagreement = (b14_1_amps - nameplate_amps) / nameplate_amps
    if abs(disagreement) <= tolerance_frac:
        return b14_1_amps, disagreement, None
    chosen = max(b14_1_amps, nameplate_amps)
    which = "nameplate" if chosen == nameplate_amps else "B.14.1"
    return (
        chosen,
        disagreement,
        f"B.14.1 full-load current ({b14_1_amps:.1f} A) and cataloged nameplate "
        f"current ({nameplate_amps:.1f} A) disagree by {disagreement:+.1%}, beyond "
        f"the {tolerance_frac:.0%} tolerance. These are two vendor claims about the "
        f"same machine, so one of the inputs is wrong. The higher ({which}, "
        f"{chosen:.1f} A) is carried into cable sizing because choosing the lower "
        f"of two contradictory currents to size a conductor is not defensible. "
        "Obtain the vendor full-load current at the design voltage to resolve it."
    )


class MotorSizing(BaseModel):
    model_config = ConfigDict(frozen=True)

    motor: MotorModel
    hp_required: float
    hp_nameplate_at_frequency: float
    loading_frac: float
    loading_in_target_band: bool
    operating_amps: float
    operating_current: OperatingCurrent | None = Field(
        default=None,
        description="Load-current phasor decomposition, if the physics "
        "path was taken. ``None`` means the linear fallback ran because "
        "neither power factor nor an operator magnetizing override was "
        "available. The scalar ``operating_amps`` field is always "
        "populated for backwards compatibility.",
    )
    operating_volts: float
    frequency_hz: float
    cooling_velocity_ft_s: float
    cooling_adequate: bool
    # Framework 6D.2 replaced the single-boolean cooling screen with a bounded
    # range. cooling_adequate is retained and still means "clears the MINIMUM",
    # because that is what the hard thermal screen consumes; an upper-bound
    # concern is a warning per 6D.2 and must not start rejecting configurations.
    cooling: CoolingAssessment | None = None
    estimated_winding_temp_f: float
    self_heating: MotorSelfHeating = Field(
        description="Framework §6D.2 two-term display: fluid temperature and "
        "calculated self-heating rise, plus the intermediate factors so a "
        "reader can see which term (load, velocity, gas, efficiency) drives "
        "the winding temperature."
    )
    thermal_ok: bool
    warnings: list[str]
    motor_type: Literal["induction", "permanent_magnet"]
    vsd_required: bool
    amps_basis: AmpsBasis
    full_load_amps_b14_1: float | None
    magnet_gate: MagnetGate
    magnet_demag_limit_f: float | None
    loading_band_is_induction_derived: bool
    uses_operator_supplied_motor_data: bool = False
    nameplate_amps_disagreement_frac: float | None = Field(
        default=None,
        description="Signed fractional gap between the B.14.1 full-load current "
        "and the cataloged nameplate current. None when B.14.1 was unavailable.",
    )
    nameplate_amps_conflict: bool = Field(
        default=False,
        description="True when that gap exceeded tolerance, meaning the design "
        "current is the conservative one and an unresolved electrical data "
        "contradiction is outstanding.",
    )
    operator_supplied_fields: tuple[str, ...] = ()
    catalog_motor: MotorModel | None = Field(
        default=None,
        description="The unmodified catalog record, when an operator override "
        "was applied. Kept so a reviewer can see what the published data said "
        "without reloading the catalog at the version the design was run "
        "against.",
    )

    @property
    def magnet_thermal_limit_unverified(self) -> bool:
        """True when a PMM design carries an unresolved B.16 magnet gate.

        This is a disclosure flag, not a scored penalty. The gap is in the
        catalog, not in the design, and penalizing it would bias selection
        toward the two induction motors purely because more is published
        about them.
        """
        return self.magnet_gate in (
            "limit_not_published",
            "indeterminate_needs_vendor_thermal_model",
        )


def _selection_key(loading: float, motor: MotorModel, target_low: float, target_high: float) -> tuple[float, float, float, str]:
    distance = target_low - loading if loading < target_low else loading - target_high if loading > target_high else 0.0
    return (distance, motor.hp, motor.od_in, motor.id)


def size_motor(
    *,
    catalog: Catalog,
    bhp_pump: float,
    gas_handling_hp: float,
    protector_loss_hp: float,
    series: int | None,
    max_od_in: float,
    frequency_hz: float,
    casing_id_in: float,
    total_fluid_rate_bpd: float,
    intake_temp_f: float,
    thresholds: MotorThresholds,
    overrides: ElectricalConstraints | None = None,
    viscous_oil_declared: bool = False,
    oil_viscosity_cp: float | None = None,
    solids_present: bool = False,
    gas_separated_to_annulus: bool = False,
    water_cut_frac: float = 1.0,
    free_gas_fraction_at_intake: float = 0.0,
) -> MotorSizing | None:
    """Select a catalog motor, returning ``None`` when no hard-fit exists.

    ``series=None`` widens the search to every motor that physically fits the
    casing, regardless of series. This exists because equipment-series
    compatibility is a vendor coupling/adapter question that no public datasheet
    in this catalog answers, and inventing a cross-series compatibility mapping
    would be fabrication. An OD fit is a real, checkable constraint; a series
    match is a claim about hardware we cannot verify. Callers that pass ``None``
    are responsible for reporting the resulting series mismatch as unverified
    rather than as approved.
    """
    require_positive("bhp_pump", bhp_pump)
    if gas_handling_hp < 0:
        raise ValueError(f"gas_handling_hp must be nonnegative, got {gas_handling_hp}")
    if protector_loss_hp < 0:
        raise ValueError(f"protector_loss_hp must be nonnegative, got {protector_loss_hp}")
    require_positive("max_od_in", max_od_in)
    require_positive("frequency_hz", frequency_hz)
    require_positive("casing_id_in", casing_id_in)
    if total_fluid_rate_bpd < 0:
        raise ValueError(f"total_fluid_rate_bpd must be nonnegative, got {total_fluid_rate_bpd}")
    require_positive("thresholds.target_loading_min", thresholds.target_loading_min)
    require_positive("thresholds.target_loading_max", thresholds.target_loading_max)
    require_positive("thresholds.absolute_loading_max", thresholds.absolute_loading_max)
    if thresholds.target_loading_min > thresholds.target_loading_max:
        raise ValueError("thresholds.target_loading_min must not exceed target_loading_max")

    hp_required = bhp_pump + gas_handling_hp + protector_loss_hp
    frequency_ratio = frequency_hz / 60.0
    candidates: list[tuple[float, MotorModel, float, float]] = []
    pool = (
        catalog.motors_for_series(series)
        if series is not None
        else sorted(catalog.motors, key=lambda m: (m.hp, m.od_in, m.id))
    )
    for motor in pool:
        if motor.od_in > max_od_in or motor.od_in >= casing_id_in or casing_id_in < motor.min_casing_id_in:
            continue
        hp_at_frequency = motor.hp * frequency_hz / 60.0  # constant torque/V-per-Hz screening
        loading = hp_required / hp_at_frequency
        # Expert sign-off: overload remains visible, not a catalog filter.
        cooling_velocity = bpd_to_ft_per_sec(
            total_fluid_rate_bpd, annulus_area_ft2(casing_id_in, motor.od_in)
        )
        candidates.append((loading, motor, hp_at_frequency, cooling_velocity))
    if not candidates:
        return None

    loading, motor, hp_at_frequency, cooling_velocity = min(
        candidates,
        key=lambda item: _selection_key(
            item[0], item[1], thresholds.target_loading_min, thresholds.target_loading_max
        ),
    )
    # Operator-supplied vendor data is merged AFTER selection and never before.
    # Selection is on physical fit and loading only; letting an override change
    # which motor gets picked would mean the design depends on which motors the
    # operator happened to have paperwork for, not on which motor is right.
    catalog_motor = motor
    override = overrides.override_for(motor.id) if overrides is not None else None
    motor, override_notes = _apply_override(motor, override)
    used_operator_data = bool(override_notes)

    operating_volts = motor.volts * frequency_ratio
    # B.14.1 preferred; nameplate scaling only when PF/efficiency are absent.
    full_load_amps, amps_basis = _full_load_amps(motor, volts=operating_volts)
    if full_load_amps is not None and used_operator_data:
        # Relabel: the B.14.1 identity is the same, but its inputs came from the
        # operator rather than a published table, and B.15 transformer sizing
        # will inherit that distinction.
        amps_basis = "b14_1_from_operator_supplied_data"
    nameplate_disagreement: float | None = None
    nameplate_conflict: str | None = None
    if full_load_amps is not None:
        design_amps, nameplate_disagreement, nameplate_conflict = _reconcile_with_nameplate(
            full_load_amps,
            motor.amps,
            tolerance_frac=thresholds.nameplate_amps_tolerance_frac,
        )
    else:
        design_amps = None
    # Framework §6 (last remaining electrical placeholder). Operating
    # current is not shaft load linearly multiplied by I_FL: the machine
    # has a magnetizing branch that stays roughly fixed while the
    # torque-producing branch scales with load, so the total is a
    # phasor sum. See esp_engine/electrical_load.py for the derivation.
    operating_current = compute_operating_current(
        load_fraction=loading,
        full_load_amps=design_amps,
        nameplate_amps=motor.amps,
        power_factor=motor.power_factor,
        is_permanent_magnet=motor.is_permanent_magnet,
        # Operator override for I_mu/I_FL is available through the same
        # provenance mechanism as PF and efficiency once the catalog
        # field is added; for now the override path is exercised via
        # tests. PMM residual defaults to zero rather than a midpoint.
    )
    operating_amps = operating_current.operating_amps
    cooling = assess_cooling(
        velocity_ft_s=cooling_velocity,
        thresholds=thresholds,
        viscous_oil_declared=viscous_oil_declared,
        oil_viscosity_cp=oil_viscosity_cp,
        solids_present=solids_present,
        gas_separated_to_annulus=gas_separated_to_annulus,
    )
    cooling_adequate = cooling.adequate
    # Framework §6D.2: winding temperature = fluid temperature + calculated
    # self-heating under load. The previous placeholder set winding = intake,
    # which zeroed the self-heating term and under-stated the temperature the
    # B.14.5 insulation check consumes.
    self_heating = estimate_motor_self_heating(
        intake_temp_f=intake_temp_f,
        cooling_velocity_ft_s=cooling_velocity,
        loading_frac=loading,
        water_cut_frac=water_cut_frac,
        free_gas_fraction_at_intake=free_gas_fraction_at_intake,
        motor_efficiency=motor.efficiency,
        motor_type=motor.motor_type,
    )
    estimated_winding_temp = self_heating.winding_temp_f
    thermal_limit = min(motor.max_winding_temp_f, thresholds.max_winding_temp_f)
    magnet_gate = _magnet_gate(motor, estimated_magnet_temp_floor_f=estimated_winding_temp)
    # B.16: the magnet limit is an additional hard gate with no induction
    # equivalent. An unresolvable gate must not read as thermally cleared.
    thermal_ok = (
        cooling_adequate
        and estimated_winding_temp <= thermal_limit
        and magnet_gate != "exceeded_at_intake_temperature"
    )

    # B.16: the target loading band in MotorThresholds is induction-motor
    # practice. A PMM holds efficiency and power factor at partial load, so
    # underloading is not the same penalty. A separate PMM floor is honored
    # when configured; otherwise the induction band is applied and the
    # provenance of the band is disclosed rather than silently reused.
    loading_min = thresholds.target_loading_min
    band_is_induction_derived = True
    if motor.is_permanent_magnet and thresholds.pmm_target_loading_min is not None:
        loading_min = thresholds.pmm_target_loading_min
        band_is_induction_derived = False
    in_target = loading_min <= loading <= thresholds.target_loading_max

    warnings: list[str] = list(override_notes)
    if nameplate_conflict is not None:
        warnings.append(nameplate_conflict)
    if amps_basis == "nameplate_linear_scaling":
        warnings.append(
            "operating amps are linearly scaled from nameplate because no vendor "
            "load-current/PF/efficiency curve is cataloged; the B.14.1 I_FL form "
            "is unavailable for this motor, and without power factor the "
            "phasor load-current form (\u00a76) cannot be evaluated either."
        )
    elif (
        operating_current.basis == "phasor_from_cataloged_power_factor"
        and operating_current.load_fraction < 0.9
    ):
        # At partial load the phasor and linear forms disagree; disclose
        # the gap so a reviewer can see the correction is doing work.
        delta = operating_current.operating_amps - operating_current.linear_reference_amps
        warnings.append(
            f"\u00a76 phasor load-current: at {operating_current.load_fraction:.0%} "
            f"load, operating current {operating_current.operating_amps:.1f} A vs "
            f"the linear approximation {operating_current.linear_reference_amps:.1f} A "
            f"({delta:+.1f} A). Apparent power factor at load = "
            f"{operating_current.apparent_power_factor:.2f} vs nameplate PF "
            f"{motor.power_factor:.2f}."
        )
    if self_heating.efficiency_basis == "reference_induction_fallback":
        # Framework §6D.2 does not cite a per-motor efficiency map. When the
        # catalog omits efficiency the model reverts to the anchor's typical-
        # induction figure. That is disclosed rather than silently used.
        warnings.append(
            f"{motor.id} catalog record has no efficiency figure; the §6D.2 "
            "self-heating anchor was applied at its reference induction value "
            "(0.85). A cataloged efficiency will move the winding temperature."
        )
    if motor.vsd_required:
        warnings.append(
            f"{motor.id} is a permanent magnet motor: a VSD with PMM rotor-position "
            "control is mandatory (B.16), not an option. Across-the-line start on a "
            "switchboard is not possible."
        )
    if magnet_gate == "limit_not_published":
        warnings.append(
            f"{motor.id} magnet demagnetization limit is not published in this "
            "catalog, so the B.16 magnet gate cannot be evaluated. Demagnetization "
            "is irreversible; obtain the vendor limit before install."
        )
    elif magnet_gate == "indeterminate_needs_vendor_thermal_model":
        warnings.append(
            f"{motor.id} magnet demagnetization limit "
            f"({motor.demag_temp_f:.0f} F) is above intake temperature "
            f"{intake_temp_f:.0f} F, but intake temperature is a lower bound on "
            "magnet temperature, so the B.16 magnet gate is indeterminate rather "
            "than cleared. A vendor thermal model is required to clear it."
        )
    elif magnet_gate == "exceeded_at_intake_temperature":
        warnings.append(
            f"{motor.id} magnet demagnetization limit "
            f"({motor.demag_temp_f:.0f} F) is already exceeded by intake "
            f"temperature {intake_temp_f:.0f} F. This is an irreversible failure "
            "mode, not a degradation."
        )
    if not in_target:
        warnings.append(
            f"motor loading {loading:.1%} is outside target {loading_min:.0%}–{thresholds.target_loading_max:.0%}"
        )
        if motor.is_permanent_magnet and band_is_induction_derived and loading < loading_min:
            warnings.append(
                "that target band is induction-motor practice; B.16 notes a PMM "
                "holds efficiency and near-unity power factor at partial load, so "
                "this underloading flag may overstate the real penalty. Set "
                "thresholds.motor.pmm_target_loading_min from vendor data to "
                "calibrate it."
            )
    # 6D.2's lower- and upper-bound warnings are different engineering
    # conversations with opposite remedies, so they are authored in cooling.py
    # and passed through verbatim rather than rewritten into one message here.
    warnings.extend(cooling.warnings)
    if intake_temp_f > thermal_limit:
        warnings.append(
            f"intake temperature {intake_temp_f:.1f} F exceeds motor/configuration limit {thermal_limit:.1f} F"
        )
    return MotorSizing(
        motor=motor,
        hp_required=hp_required,
        hp_nameplate_at_frequency=hp_at_frequency,
        loading_frac=loading,
        loading_in_target_band=in_target,
        operating_amps=operating_amps,
        operating_current=operating_current,
        operating_volts=operating_volts,
        frequency_hz=frequency_hz,
        cooling_velocity_ft_s=cooling_velocity,
        cooling_adequate=cooling_adequate,
        cooling=cooling,
        estimated_winding_temp_f=estimated_winding_temp,
        thermal_ok=thermal_ok,
        warnings=warnings,
        motor_type=motor.motor_type,
        vsd_required=motor.vsd_required,
        amps_basis=amps_basis,
        full_load_amps_b14_1=full_load_amps,
        magnet_gate=magnet_gate,
        magnet_demag_limit_f=motor.demag_temp_f,
        loading_band_is_induction_derived=band_is_induction_derived,
        uses_operator_supplied_motor_data=used_operator_data,
        operator_supplied_fields=tuple(
            f
            for f in ("power_factor", "efficiency", "demag_temp_f")
            if override is not None and getattr(override, f, None) is not None
        ),
        catalog_motor=catalog_motor,
        nameplate_amps_disagreement_frac=nameplate_disagreement,
        nameplate_amps_conflict=nameplate_conflict is not None,
        self_heating=self_heating,
    )
