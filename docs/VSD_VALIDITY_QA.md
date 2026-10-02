# VSD validity correction: QA inventory

The correction must explain why a baseline head deficit and a successful VSD trial can coexist, without presenting untested recovery as a continuous operating guarantee. This inventory covers the changed timeline and its supporting calculation.

## Calculation checks

- **Head sufficiency:** A near-BEP point with insufficient head is not acceptable, even if its hydraulic warning is classified soft to retain the candidate for trajectory reporting.
- **Fixed equipment:** Later months and VSD trials use the originally selected motor, cable and protector. Missing or inadequate installed equipment must not be replaced silently.
- **Recovery evidence:** Every published point has sufficient head and passes the implemented gates, including electrical and thermal checks. Recovery stops at the first failed sampled month.
- **No invented recovery:** No VSD, no supplied trial, already-valid baseline, unchecked equipment and a failed first recovery month have separate tests.
- **Demo reconciliation:** First baseline failure at month 3; month 6 baseline 1,634 ft against 1,890 ft; 60 Hz trial 2,008 ft; last recovered sample month 14; month 15 fails at 65 Hz.
- **Narrative guard:** Newly available evidence must not weaken the existing arbitrary-number acceptance ceilings.

## Browser checks

- **Live flow:** Open live design, select timeline and match chart/table/summary against the stored facts.
- **Schedule control:** Expand and collapse evidence; verify 12 monthly rows and motor/cable values.
- **Candidate selection:** Switch to rank 2 and back to rank 1; ensure boundary and recovery update.
- **Theme cycle:** Inspect dark, light, then dark; chart remains legible.
- **Responsive layout:** Desktop 1440 px and mobile 375 px. Table scrolling is intentional; page-level horizontal overflow is not.
- **Off-happy path, old run:** Intercept a read-only response with legacy engine provenance; show recalculation warning and hide unverified chart/table.
- **Off-happy path, no recovery:** Intercept a read-only response with no verified schedule; show no recovered series or continuous recovery band.
- **Screenshot:** Capture only genuine recalculated demo results, visibly marked prototype. No data changes for presentation.

## Scope limitations

Monthly samples and 2.5 Hz trial increments do not establish between-sample validity or a continuous-frequency optimum. Estimated curves, missing vendor thrust limits and incomplete surface/start-up verification remain outside this correction.

## Completed verification

All listed calculation checks pass in the full 420-test backend suite, including
18 dedicated validity/recovery cases. Production frontend type checking and build
pass. Browser checks exercised rank 1 → rank 2 → rank 1, schedule expansion and
collapse, dark → light → dark, and 1440 px / 375 px layouts.

Read-only mocked legacy, no-recovery and no-VSD responses produced the expected
warnings/absence of recovery points; real stored data was not modified by those
checks. No runtime errors or page-level horizontal overflow were observed.
The final screenshot uses the genuine new demo run, not a mocked response.
