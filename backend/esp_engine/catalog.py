"""Versioned ESP catalog loading and selection helpers.

Implements the catalog layer serving framework §6 candidate selection and the
versioned-data requirement in ``docs/physics-reference.md`` implementation note 9.
"""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .units import require_positive

if TYPE_CHECKING:
    from .gas import GasStrategy


class _CatalogModel(BaseModel):
    """Common immutable provenance fields for catalog records."""

    model_config = ConfigDict(frozen=True)

    source_urls: list[str] = Field(default_factory=list)
    data_quality: str = "unknown"
    notes: str | None = None
    synthetic: bool = False


class PumpCurvePoint(BaseModel):
    """A reference-frequency water-test point, expressed per pump stage."""

    model_config = ConfigDict(frozen=True)

    q_bpd: float
    head_ft: float
    eff_pct: float
    bhp_hp: float


class PumpCurve(BaseModel):
    """Reference curve supplied with a :class:`PumpModel`."""

    model_config = ConfigDict(frozen=True)

    basis: str = "per_stage_at_reference_frequency"
    sg_basis: float = 1.0
    points: list[PumpCurvePoint]


class PumpCurveFit(BaseModel):
    """Descending-power polynomial coefficients in q_bpd."""

    model_config = ConfigDict(frozen=True)

    head_coeffs: list[float]
    eff_coeffs: list[float]
    bhp_coeffs: list[float]
    form: str = "polynomial_in_q_bpd_descending"


class PumpModel(_CatalogModel):
    id: str
    manufacturer: str
    model: str
    series: int
    housing_od_in: float
    min_casing_id_in: float
    stage_type: str
    pump_type: Literal["floater", "compression"] | None = None
    shaft_diameter_in: float | None = None
    impeller_thrust_lb_per_stage: float | None = None
    price_per_stage: float | None = None
    frequency_ref_hz: float
    bep_flow_bpd: float
    recommended_range_bpd: tuple[float, float]
    downthrust_limit_bpd: float | None = None
    upthrust_limit_bpd: float | None = None
    max_stages: int
    shaft_hp_limit: float
    thrust_bearing_capacity_lb: float | None = None
    curve: PumpCurve
    curve_fit: PumpCurveFit
    # Optional catalog extension for genuine vendor prebuilt curves. Kept as raw
    # data because the binding schema does not prescribe an interchange format.
    prebuilt_curves: dict[str, Any] | None = None

    @field_validator("recommended_range_bpd")
    @classmethod
    def _ordered_rate_range(cls, value: tuple[float, float]) -> tuple[float, float]:
        if len(value) != 2 or value[0] < 0 or value[1] <= value[0]:
            raise ValueError("recommended_range_bpd must be [nonnegative min, positive max]")
        return value


class MotorModel(_CatalogModel):
    """An ESP motor.

    Framework ref: B.16 makes induction vs. permanent-magnet a fork in the
    sizing workflow rather than a catalog substitution, so ``motor_type`` is
    required on every record. ``power_factor``, ``efficiency`` and
    ``demag_temp_f`` are optional because no public datasheet in this catalog
    publishes them per model; ``None`` means "not published", never "not
    applicable". Downstream code must branch on absence rather than
    substituting a default, because a default here silently changes I_FL
    (B.14.1) and transformer kVA (B.15.1) for every design.
    """

    id: str
    manufacturer: str
    series: int
    od_in: float
    min_casing_id_in: float
    hp: float
    volts: float
    amps: float
    rpm_synchronous_60hz: float | None = None
    length_ft: float
    max_winding_temp_f: float
    motor_type: Literal["induction", "permanent_magnet"]
    power_factor: float | None = None
    efficiency: float | None = None
    demag_temp_f: float | None = None

    @property
    def is_permanent_magnet(self) -> bool:
        return self.motor_type == "permanent_magnet"

    @property
    def vsd_required(self) -> bool:
        """B.16: a PMM cannot start across the line and requires a VSD.

        This is a property of the machine, not of the well or the operator's
        infrastructure, so it is derived here rather than configured.
        """
        return self.is_permanent_magnet

    @field_validator("power_factor")
    @classmethod
    def _check_power_factor(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 < value <= 1.0:
            raise ValueError(f"power_factor must be in (0, 1], got {value}")
        return value

    @field_validator("efficiency")
    @classmethod
    def _check_efficiency(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 < value < 1.0:
            raise ValueError(f"efficiency must be in (0, 1), got {value}")
        return value

    @model_validator(mode="after")
    def _check_demag_applicability(self) -> "MotorModel":
        if self.demag_temp_f is not None:
            if self.motor_type != "permanent_magnet":
                raise ValueError(
                    f"motor {self.id}: demag_temp_f is only meaningful for a "
                    "permanent_magnet motor; an induction motor has no magnet "
                    "to demagnetize"
                )
            if self.demag_temp_f <= 0:
                raise ValueError(
                    f"motor {self.id}: demag_temp_f must be positive, got "
                    f"{self.demag_temp_f}"
                )
        return self


class CableModel(_CatalogModel):
    id: str
    awg: int
    conductor_area_cmil: float
    ampacity_a: float
    resistance_ohm_per_1000ft_at_77f: float
    temp_coeff_per_f: float = 0.00214
    od_flat_in: float | None = None
    od_round_in: float | None = None
    max_temp_f: float
    armor: str | None = None
    price_per_ft: float | None = None
    reactance_ohm_per_1000ft: float | None = None


class GasHandlingModel(_CatalogModel):
    id: str
    type: str
    series: int
    od_in: float | None = None
    hp_consumed: float = 0.0
    max_free_gas_fraction_handled: float | None = None
    separation_efficiency_pct_range: tuple[float, float] | None = None

    @field_validator("hp_consumed", mode="before")
    @classmethod
    def _null_hp_is_zero(cls, value: float | None) -> float:
        return 0.0 if value is None else value


class SealModel(_CatalogModel):
    id: str
    series: int
    od_in: float
    thrust_bearing_capacity_lb: float | None = None
    max_shaft_hp: float | None = None
    length_ft: float
    chamber_type: str | None = None


class CasingSize(_CatalogModel):
    size_in: float
    weight_lb_per_ft: float
    id_in: float
    drift_id_in: float | None = None


class Catalog(BaseModel):
    """Validated, immutable equipment catalog with deterministic selection helpers."""

    model_config = ConfigDict(frozen=True)

    pumps: list[PumpModel]
    motors: list[MotorModel]
    cables: list[CableModel]
    gas_handling: list[GasHandlingModel]
    seals: list[SealModel]
    casing: list[CasingSize]
    version: str
    has_estimated_data: bool = False

    def pumps_for_casing(
        self, casing_id_in: float, min_clearance_in: float
    ) -> list[PumpModel]:
        require_positive("casing_id_in", casing_id_in)
        if min_clearance_in < 0:
            raise ValueError(f"min_clearance_in must be nonnegative, got {min_clearance_in}")
        return sorted(
            [
                pump
                for pump in self.pumps
                if casing_id_in >= pump.min_casing_id_in
                and casing_id_in - pump.housing_od_in >= min_clearance_in
            ],
            key=lambda item: (item.series, item.bep_flow_bpd, item.id),
        )

    def pumps_for_rate(self, rate_bpd: float, tolerance: float = 0.6) -> list[PumpModel]:
        if rate_bpd < 0:
            raise ValueError(f"rate_bpd must be nonnegative, got {rate_bpd}")
        if tolerance < 0:
            raise ValueError(f"tolerance must be nonnegative, got {tolerance}")
        return sorted(
            [
                pump
                for pump in self.pumps
                if abs(rate_bpd - pump.bep_flow_bpd) / pump.bep_flow_bpd <= tolerance
            ],
            key=lambda item: (abs(rate_bpd - item.bep_flow_bpd), item.id),
        )

    def motors_for_series(self, series: int) -> list[MotorModel]:
        return sorted(
            [motor for motor in self.motors if motor.series == series],
            key=lambda item: (item.hp, item.od_in, item.id),
        )

    def smallest_motor_for_hp(self, hp: float, series: int, max_od_in: float) -> MotorModel | None:
        require_positive("hp", hp)
        require_positive("max_od_in", max_od_in)
        options = [
            motor
            for motor in self.motors_for_series(series)
            if motor.hp >= hp and motor.od_in <= max_od_in
        ]
        return options[0] if options else None

    def seals_for_series(self, series: int) -> list[SealModel]:
        return sorted(
            [seal for seal in self.seals if seal.series == series],
            key=lambda item: (item.od_in, item.length_ft, item.id),
        )

    def gas_handling_for(self, strategy: GasStrategy | str, series: int) -> list[GasHandlingModel]:
        """Return compatible devices without importing the in-progress gas module.

        ``strategy`` accepts a string until ``esp_engine.gas`` is installed; enum
        values work identically once that module exists.
        """
        strategy_value = str(getattr(strategy, "value", strategy)).lower()
        if strategy_value == "none" or strategy_value == "not_esp_candidate":
            return []
        accepted: dict[str, set[str]] = {
            "static_separator": {"static_separator", "separator"},
            "rotary_separator": {"rotary_separator", "separator"},
            "gas_handler": {"gas_handler"},
            "separator_plus_handler": {"static_separator", "rotary_separator", "separator", "gas_handler"},
            "advanced_gas_handler": {"advanced_gas_handler", "gas_handler"},
        }
        types = accepted.get(strategy_value, {strategy_value})
        return sorted(
            [device for device in self.gas_handling if device.series == series and device.type.lower() in types],
            key=lambda item: (item.hp_consumed, item.od_in, item.id),
        )

    def pump(self, pump_id: str) -> PumpModel:
        for pump in self.pumps:
            if pump.id == pump_id:
                return pump
        raise ValueError(f"pump_id {pump_id!r} is not present in catalog version {self.version}")


_FILE_TO_FIELD: dict[str, tuple[str, type[BaseModel]]] = {
    "pumps.json": ("pumps", PumpModel),
    "motors.json": ("motors", MotorModel),
    "cables.json": ("cables", CableModel),
    "gas_handling.json": ("gas_handling", GasHandlingModel),
    "seals.json": ("seals", SealModel),
    "casing.json": ("casing", CasingSize),
}


def is_estimated_record(record: _CatalogModel) -> bool:
    """Whether a catalog record's numbers are parametric estimates.

    Public because downstream layers must be able to mark a result as resting on
    estimated data. A tool that renders an estimated pump curve with the same
    confidence as a digitized vendor curve launders an estimate into an
    authority, which is worse than having no tool.
    """
    return record.synthetic or "estimate" in record.data_quality.lower()


# Backwards-compatible private alias.
_is_estimated = is_estimated_record


@lru_cache(maxsize=16)
def _load_catalog_cached(path_string: str) -> Catalog:
    directory = Path(path_string)
    if not directory.is_dir():
        raise ValueError(f"catalog directory does not exist: {directory}")

    loaded: dict[str, list[BaseModel]] = {}
    digest = sha256()
    for filename, (field_name, model_class) in _FILE_TO_FIELD.items():
        file_path = directory / filename
        if not file_path.is_file():
            raise ValueError(
                f"catalog is incomplete: required file {file_path} is missing; "
                f"expected {', '.join(_FILE_TO_FIELD)}"
            )
        raw = file_path.read_bytes()
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"catalog file {file_path} is not valid JSON: {exc}") from exc
        if not isinstance(decoded, list):
            raise ValueError(f"catalog file {file_path} must contain a JSON array")
        try:
            loaded[field_name] = [model_class.model_validate(record) for record in decoded]
        except Exception as exc:  # Pydantic's detailed path is valuable to callers.
            raise ValueError(f"invalid {field_name} catalog in {file_path}: {exc}") from exc

    all_records = [record for records in loaded.values() for record in records]
    return Catalog(
        **loaded,
        version=digest.hexdigest(),
        has_estimated_data=any(_is_estimated(record) for record in all_records),
    )


def load_catalog(path: Path | None = None) -> Catalog:
    """Load catalog files once per directory and hash their exact loaded bytes."""
    directory = path if path is not None else Path(__file__).resolve().parents[2] / "data" / "catalog"
    return _load_catalog_cached(str(directory.expanduser().resolve()))
