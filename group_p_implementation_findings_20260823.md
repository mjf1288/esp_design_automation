# Group P implementation findings — 2026-08-23

## Delivered modules

- `backend/esp_engine/pvt.py`
- `backend/esp_engine/inflow.py`
- `backend/esp_engine/intake.py`
- `backend/esp_engine/gas.py`
- `backend/esp_engine/tdh.py`

## Tests delivered

- `backend/tests/test_pvt.py` (6 tests)
- `backend/tests/test_inflow.py` (4 tests)
- `backend/tests/test_intake.py` (4 tests)
- `backend/tests/test_gas.py` (4 tests)
- `backend/tests/test_tdh.py` (3 tests)

Run from `/home/user/workspace/esp`:

```
python -m pytest backend/tests -q
35 passed in 0.10s
```

The full suite includes 14 concurrently present tests outside Group P.  A one-line
precision-only correction was made to `motor.py` (`motor.hp * frequency_hz / 60.0`
instead of multiplication by a previously-rounded ratio) so that an existing exact
constant-V/Hz regression passes; there is no intended engineering-model change.

## Interface and reference limitations surfaced explicitly

1. `FluidState.bg_ft3_scf` is named as ft3/scf, while the reference equation at
   §2.5 defines the 0.00504 coefficient in **rb/scf**.  The implementation keeps
   0.00504 (rb/scf) because intake volumes are required in rb/d by §2.8 and applies
   it consistently.  The frozen binding field name was not changed.  Consumers
   should therefore treat the present field value as rb/scf until the contract is
   clarified.
2. The intake binding has casing ID but no ESP assembly OD, annular roughness, or
   selectable annulus pressure-gradient method.  `solve_intake_conditions` uses
   the documented homogeneous/static screening gradient and no annular friction;
   it does not claim a slip-aware traverse.
3. The Alhanati natural-separation binding lacks surface tension, liquid/gas
   density, and flow-regime inputs.  Its selectable implementation uses stated
   air/water reference assumptions and flags the result.  The generic vendor-rule
   selection receives zero (conservative) natural-separation credit because no
   vendor map or credible vent-flow data exists in the interface.
4. The TDH binding exposes no Hazen--Williams C value.  The explicit
   Hazen--Williams selection uses a documented C=120 new-steel screening default;
   Darcy--Weisbach remains default.
5. `CorrelationConfig` has no selected undersaturated-oil Bo model despite
   reference §2.2 requiring one.  Bo is therefore the selected saturated family
   evaluated at capped Rs above bubble point, and the correlation trail declares
   this limitation.  Release use needs an explicit lab-table/compressibility
   option added to configuration.

## Physics-reference issues / lower-confidence areas

- §1.4's printed PIP hydrostatic sign produces higher pressure at an intake above
  the perforations.  The implementation uses the physically consistent depth
  relation `PIP = Pwf + gradient * (TVDpump - TVDperf)`; it lowers PIP when the
  pump is above the perforations.
- §2.6 prints water viscosity as `A*T^(-B)` although its listed B is negative;
  that gives approximately 21,209 cP at 180 F / 30,000 ppm, which is physically
  implausible.  The implementation uses `A*T^B`, producing 0.390 cP at 1,000 psi,
  180 F, 30,000 ppm.  This is a deliberate correction pending verification
  against the original Mathews--Russell/McCain source.
- Hall--Yarborough is supplied as a selectable safeguarded implicit fit.  The
  reference itself calls for validation against a trusted implementation, so it
  remains lower confidence than DAK.
- The Turpin/field-linear head factor is reported as a screening factor only;
  neither is a vendor two-phase map.  Device separation efficiency is intentionally
  zero until an actual vendor map is provided.
