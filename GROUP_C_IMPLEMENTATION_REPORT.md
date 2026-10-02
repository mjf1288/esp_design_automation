# Group C implementation report

## Delivered modules

- `backend/esp_engine/catalog.py`
- `backend/esp_engine/curves.py`
- `backend/esp_engine/mechanical.py`
- `backend/esp_engine/motor.py`
- `backend/esp_engine/cable.py`

The matching regression tests are in `backend/tests/test_catalog.py`,
`test_curves.py`, `test_mechanical.py`, `test_motor.py`, and `test_cable.py`.
A self-contained fixture catalog is under `backend/tests/fixtures/catalog/`.

## Validation performed

- Exact requested command: `python -m pytest backend/tests -q`
- Result: **35 passed**.
- Group C-only command: **14 passed**.
- The real `data/catalog/` files were present and loaded successfully:
  13 pumps, 14 motors, 8 gas-handling records. The loader computed catalog
  version `098305b96114…` during validation and correctly surfaced
  `has_estimated_data=True`.
- Every real catalog pump passed the monotonic decreasing-head test at every
  fitted curve point.
- `esp_engine.gas.GasStrategy` import works. `Catalog.gas_handling_for` accepts
  either the enum or its string value without a runtime dependency on the gas
  module.

## Deliberate safety limitations / interface implications

1. `apply_viscosity_correction` applies identity for water-like viscosity
   (<=1.1 cP) and refuses ANSI/HI 9.6.7 for more viscous liquid. The supplied
   physics reference explicitly requires a licensed complete ANSI/HI standard
   or validated vendor curve and prohibits inventing a correction factor. This
   is the only material implementation limitation relative to a fully released
   viscous-fluid design workflow.
2. Mechanical thrust load is `None` because the binding interface provides no
   axial flow/thrust load input or vendor thrust map. Catalog capacity is exposed
   and the result warns that vendor verification is required rather than emitting
   an invented load.
3. Motor thermal result uses intake temperature as a lower-bound estimate and
   makes cooling velocity a hard screen. Operating amps are linearly scaled from
   nameplate because no motor load-current/PF/efficiency map is in the catalog.
   Both are explicitly warned and require vendor curves for release.
4. The catalog's exact-series motor filter is honored. The real catalog does not
   provide a one-to-one motor entry for every pump series, so no undocumented
   cross-series compatibility mapping was invented.
5. Cable fit is a conservative pump-OD-plus-cable-profile screen; bands, cable
   guards, couplings, shrouds and drift restrictions remain the separate full
   geometry tally required by the physics reference.
