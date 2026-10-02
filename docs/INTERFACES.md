# Engine module interface contract — v0.1

**Binding.** Every module below must expose exactly these signatures. Parallel
work composes only if these are honored precisely. If a signature is genuinely
wrong, note it in your report — do not silently change it.

Common rules:
- Package root: `/home/user/workspace/esp/backend/esp_engine/`
- Field units, named in every parameter (`rate_bpd`, `depth_ft`, `pressure_psi`,
  `temp_f`). Use helpers from `units.py`. Never accept a bare `temp` or `rate`.
- Pure functions. No I/O, no network, no clock, no randomness, no LLM.
- Every function raises `ValueError` with an actionable message on non-physical
  input. Use `units.require_positive` / `require_fraction` /
  `require_absolute_temperature`.
- Correlation choices come from `config.CorrelationConfig`, never hardcoded.
- Every module docstring cites the `docs/physics-reference.md` section it
  implements plus the framework section it serves.
- Full type hints. `from __future__ import annotations` at the top.
- Result objects are frozen Pydantic models (`ConfigDict(frozen=True)`) or
  `@dataclass(frozen=True)`.

Already built and stable — import, do not modify:
`units.py`, `provenance.py` (`Tracked`, `Source`, `Assumption`, `BiasPolicy`,
`Judgment`), `models.py` (`Case` and all input classes), `config.py`.

---

## Group P — fluid and hydraulics kernel

### `pvt.py`

```python
class FluidState(BaseModel):          # frozen
    pressure_psi: float
    temp_f: float
    # oil
    rs_scf_stb: float                 # solution GOR at these conditions
    bo_rb_stb: float
    oil_density_lb_ft3: float
    oil_viscosity_cp: float
    oil_sg: float
    bubble_point_psi: float
    # gas
    z_factor: float
    bg_ft3_scf: float
    gas_density_lb_ft3: float
    gas_viscosity_cp: float
    free_gas_scf_stb: float           # Rs_total - Rs at these conditions
    # water
    bw_rb_stb: float
    water_density_lb_ft3: float
    water_viscosity_cp: float
    # correlations actually used, for the audit trail
    correlations_used: dict[str, str]

class FluidSpec(BaseModel):           # frozen — plain floats, no Tracked
    oil_api: float
    gas_sg: float
    water_sg: float
    gor_scf_stb: float
    water_cut_frac: float
    salinity_ppm: float = 30000.0
    bubble_point_psi: float | None = None   # if measured, overrides correlation

def solve_fluid_state(spec: FluidSpec, pressure_psi: float, temp_f: float,
                      cfg: CorrelationConfig) -> FluidState
```

Plus individually testable correlation functions, each named for its source:
`standing_bubble_point_psi`, `standing_rs_scf_stb`, `standing_bo_rb_stb`,
`vazquez_beggs_*` (same three), `beggs_robinson_dead_oil_viscosity_cp`,
`beggs_robinson_live_oil_viscosity_cp`, `sutton_pseudocriticals`,
`dak_z_factor`, `hall_yarborough_z_factor`, `mccain_bw_rb_stb`,
`water_density_lb_ft3`, `water_viscosity_cp`, `lee_gonzalez_gas_viscosity_cp`.

### `inflow.py`

```python
class InflowSpec(BaseModel):          # frozen
    reservoir_pressure_psi: float
    productivity_index_bpd_psi: float | None
    bubble_point_psi: float
    test_rate_bpd: float | None = None      # for PI back-calculation
    test_pwf_psi: float | None = None
    model: IPRModel = IPRModel.COMPOSITE

def pwf_for_rate(spec: InflowSpec, rate_bpd: float) -> float
def rate_for_pwf(spec: InflowSpec, pwf_psi: float) -> float
def absolute_open_flow_bpd(spec: InflowSpec) -> float
def productivity_index_from_test(rate_bpd, pwf_psi, reservoir_pressure_psi,
                                 bubble_point_psi, model) -> float
def max_rate_bpd(spec: InflowSpec) -> float
```

`pwf_for_rate` must raise a clear `ValueError` when the requested rate exceeds
AOF — "target rate of X bpd exceeds the well's absolute open flow of Y bpd; the
expectation is not achievable regardless of pump selection". Framework §3.1
requires that unrealistic expectations be *revealed*, so this error message is a
product feature, not just an exception.

### `intake.py`

```python
class IntakeConditions(BaseModel):    # frozen
    pip_psi: float
    intake_temp_f: float
    setting_depth_md_ft: float
    setting_depth_tvd_ft: float
    fluid_over_pump_ft: float
    submergence_ft: float
    fluid: FluidState
    # volumes at intake
    oil_rate_intake_bpd: float
    water_rate_intake_bpd: float
    free_gas_rate_intake_bpd: float
    total_liquid_intake_bpd: float
    total_fluid_intake_bpd: float     # liquid + free gas
    free_gas_fraction: float          # FGVF at intake, before separation
    mixture_density_lb_ft3: float
    mixture_sg: float
    mixture_viscosity_cp: float
    converged: bool
    iterations: int

def solve_intake_conditions(
    *, case_geometry_casing_id_in: float, setting_depth_md_ft: float,
    setting_depth_tvd_ft: float, target_rate_bpd: float,
    inflow: InflowSpec, fluid_spec: FluidSpec,
    perforation_tvd_ft: float, bht_f: float, surface_temp_f: float,
    casing_pressure_psi: float, cfg: CorrelationConfig,
    max_iter: int = 50, tol_psi: float = 0.5,
) -> IntakeConditions
```

PIP requires iteration: PIP sets free gas, free gas sets the gradient between
perforations and pump, the gradient sets PIP. Use damped successive substitution
or bisection; guarantee termination; set `converged=False` rather than looping
forever. Never raise on non-convergence — a non-converged cell must be
*reportable*, because silently dropping it would bias the whole scenario sweep.

### `gas.py`

```python
class GasStrategy(str, Enum):
    NONE, STATIC_SEPARATOR, ROTARY_SEPARATOR, GAS_HANDLER,
    SEPARATOR_PLUS_HANDLER, ADVANCED_GAS_HANDLER, NOT_ESP_CANDIDATE

class GasAssessment(BaseModel):       # frozen
    fgvf_at_intake: float
    natural_separation_efficiency: float
    fgvf_after_natural_separation: float
    device_separation_efficiency: float
    fgvf_entering_pump: float
    turpin_parameter: float
    turpin_stable: bool
    recommended_strategy: GasStrategy
    head_degradation_factor: float    # multiply pump head by this, in (0, 1]
    rationale: str                    # engineer-readable, cites the thresholds
    warnings: list[str]

def assess_gas(intake: IntakeConditions, *, casing_id_in: float,
               equipment_od_in: float, has_vsd: bool,
               cfg: CorrelationConfig, thresholds: GasThresholds) -> GasAssessment

def natural_separation_efficiency(*, liquid_rate_bpd, gas_rate_bpd,
                                  casing_id_in, equipment_od_in,
                                  model: NaturalSeparationModel) -> float
def turpin_parameter(*, free_gas_rate_bpd, liquid_rate_bpd, pip_psi) -> float
def head_degradation_factor(fgvf: float) -> float
```

`rationale` must name the numeric threshold it crossed, e.g. "FGVF entering pump
0.32 exceeds standard-pump limit 0.10 and the rotary-separator recommendation
threshold 0.25 → rotary separator". The engineer must be able to audit the
decision without reading source.

### `tdh.py`

```python
class TDHBreakdown(BaseModel):        # frozen
    net_lift_ft: float
    tubing_friction_ft: float
    wellhead_head_ft: float
    tdh_ft: float
    discharge_pressure_psi: float
    fluid_sg_used: float
    friction_method: FrictionMethod
    reynolds_number: float | None
    friction_factor: float | None
    velocity_ft_s: float

def compute_tdh(*, setting_depth_tvd_ft: float, pip_psi: float,
                wellhead_pressure_psi: float, total_liquid_rate_bpd: float,
                tubing_id_in: float, tubing_length_ft: float,
                mixture_sg: float, mixture_viscosity_cp: float,
                roughness_in: float = 0.0018,
                method: FrictionMethod = FrictionMethod.DARCY_WEISBACH
                ) -> TDHBreakdown
```

---

## Group C — catalog, curves, and equipment sizing

### `catalog.py`

Loads and validates the JSON catalog from `/home/user/workspace/esp/data/catalog/`.
Schemas are exactly as written in those files. Frozen Pydantic models:
`PumpModel`, `MotorModel`, `CableModel`, `GasHandlingModel`, `SealModel`,
`CasingSize`.

```python
class Catalog(BaseModel):             # frozen
    pumps: list[PumpModel]
    motors: list[MotorModel]
    cables: list[CableModel]
    gas_handling: list[GasHandlingModel]
    seals: list[SealModel]
    casing: list[CasingSize]
    version: str                      # hash of the loaded files, for replay

    def pumps_for_casing(self, casing_id_in: float,
                         min_clearance_in: float) -> list[PumpModel]
    def pumps_for_rate(self, rate_bpd: float, tolerance: float = 0.6
                       ) -> list[PumpModel]
    def motors_for_series(self, series: int) -> list[MotorModel]
    def smallest_motor_for_hp(self, hp: float, series: int,
                              max_od_in: float) -> MotorModel | None
    def seals_for_series(self, series: int) -> list[SealModel]
    def gas_handling_for(self, strategy: GasStrategy, series: int
                         ) -> list[GasHandlingModel]
    def pump(self, pump_id: str) -> PumpModel

def load_catalog(path: Path | None = None) -> Catalog     # cached
```

Every catalog model must expose `data_quality` and `source_urls`, and `Catalog`
must expose `has_estimated_data: bool`. The UI is required to warn when a design
depends on parametric-estimate data, so the engine has to surface it.

### `curves.py`

The heart of the scoring layer.

```python
class CurvePoint(BaseModel):          # frozen
    q_bpd: float
    head_ft_per_stage: float
    efficiency_frac: float
    bhp_per_stage: float

class ZoneClassification(BaseModel):  # frozen
    zone: OperatingZone
    q_over_qbep: float
    distance_from_bep_frac: float     # signed: negative = left of BEP
    severity: float                   # 0.0 at BEP -> 1.0 at the curve edge
    rationale: str

def scale_curve_to_frequency(pump: PumpModel, frequency_hz: float,
                             method: FrequencyCurveMethod) -> ScaledCurve
def evaluate_stage(curve: ScaledCurve, q_bpd: float) -> CurvePoint
def classify_zone(curve: ScaledCurve, q_bpd: float,
                  method: ThrustZoneMethod, thresholds: DesignThresholds
                  ) -> ZoneClassification
def apply_viscosity_correction(point: CurvePoint, *, q_bpd, viscosity_cp,
                               head_ft_per_stage, bep_q_bpd,
                               method: ViscosityCorrectionMethod) -> CurvePoint
def stages_required(tdh_ft: float, head_ft_per_stage: float,
                    gas_degradation_factor: float = 1.0) -> int
def total_head_ft(curve, q_bpd, stages, degradation_factor=1.0) -> float
def total_bhp(curve, q_bpd, stages, mixture_sg, degradation_factor=1.0) -> float
```

`ScaledCurve` holds frequency-scaled polynomial coefficients plus the scaled BEP
and range limits. Affinity laws: `Q ∝ N`, `H ∝ N²`, `BHP ∝ N³`. Scale the
*coefficients* analytically rather than resampling — it is both faster and exact.

`severity` is what makes the framework's four zone labels scoreable rather than
merely categorical, so it must be continuous and monotonic in distance from BEP.

### `mechanical.py`

Framework §3.2 note: the post-hoc gate. Failure returns to selection.

```python
class MechanicalCheck(BaseModel):     # frozen
    shaft_hp_required: float
    shaft_hp_available: float
    shaft_hp_utilization: float
    thrust_load_lb: float | None
    thrust_capacity_lb: float | None
    thrust_utilization: float | None
    passed: bool
    failures: list[str]               # actionable: what to change in selection
    warnings: list[str]

def check_mechanical(*, pump: PumpModel, seal: SealModel | None, stages: int,
                     bhp_total: float, gas_handling_hp: float,
                     frequency_hz: float, thresholds: MechanicalThresholds
                     ) -> MechanicalCheck
```

### `motor.py`

```python
class MotorSizing(BaseModel):         # frozen
    motor: MotorModel
    hp_required: float
    hp_nameplate_at_frequency: float
    loading_frac: float
    loading_in_target_band: bool
    operating_amps: float
    operating_volts: float
    frequency_hz: float
    cooling_velocity_ft_s: float
    cooling_adequate: bool
    estimated_winding_temp_f: float
    thermal_ok: bool
    warnings: list[str]

def size_motor(*, catalog, bhp_pump: float, gas_handling_hp: float,
               protector_loss_hp: float, series: int, max_od_in: float,
               frequency_hz: float, casing_id_in: float,
               total_fluid_rate_bpd: float, intake_temp_f: float,
               thresholds: MotorThresholds) -> MotorSizing | None
```

Motor HP and voltage scale with frequency under constant V/Hz. Target loading is
75–85% (framework §12). Returns `None` when nothing in the catalog fits, so the
enumerator can record *why* a config died.

### `cable.py`

```python
class CableSizing(BaseModel):         # frozen
    cable: CableModel
    length_ft: float
    voltage_drop_v: float
    voltage_drop_frac: float
    surface_voltage_required_v: float
    ampacity_a: float
    ampacity_utilization: float
    conductor_temp_f: float
    kva_required: float
    passed: bool
    warnings: list[str]

def size_cable(*, catalog, motor: MotorSizing, setting_depth_md_ft: float,
               surface_lead_ft: float = 100.0, casing_id_in: float,
               pump_od_in: float, max_temp_f: float,
               thresholds: ElectricalThresholds,
               available_surface_voltage_v: float | None = None
               ) -> CableSizing | None
```

Correct conductor resistance for temperature — an uncorrected 77 °F resistance
understates voltage drop badly in a hot well, and that error shows up as a motor
that will not start.

---

## Tests

`backend/tests/`, pytest, one file per module (`test_pvt.py`, ...).

Required for each module:
1. **Published-value checks.** Reproduce a worked example from
   `docs/physics-reference.md` or a cited source, within its stated tolerance.
   Cite the source in the test docstring. These are the tests that matter.
2. **Monotonicity / physical sanity.** Rs increases with pressure below pb;
   head decreases with flow; voltage drop increases with length and current;
   free gas fraction decreases with PIP.
3. **Unit-trap guards.** degF passed where Rankine is expected raises; a
   percentage passed where a fraction is expected raises.
4. **Edge cases.** Zero water cut, 100% water cut, zero GOR, at/above/below
   bubble point, single-phase water.
5. **Determinism.** Identical inputs produce byte-identical output.

Run `python -m pytest backend/tests -q` from `/home/user/workspace/esp` and make
it pass before reporting. Do not weaken an assertion to make a test pass — if a
published value cannot be reproduced, report the discrepancy with numbers.
