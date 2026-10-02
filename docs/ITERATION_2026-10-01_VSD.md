# ESP iteration report: evidence-backed VSD recovery

The false 24-month recovery claim is removed. The same demo pump remains the leading candidate, but a head-deficient point can no longer count as acceptable merely because it is near BEP.

## Engineering result

| Quantity | Corrected result |
| --- | --- |
| Installed pump | RC2500, 84 stages, baseline 55 Hz, 8,000 ft MD |
| First failed baseline sample | Month 3: 1,630 ft head against 1,693 ft TDH |
| Baseline failure envelope | Months 2–5 across the modeled scenarios |
| Month 6, baseline | 1,634 ft head against 1,890 ft TDH; 13.6% shortfall |
| Month 6, VSD trial | 60 Hz; 2,008 ft head; 118 ft surplus |
| Month 6, original motor/cable | 72.2% motor loading; 25.0% cable voltage drop; 219°F winding; 187°F cable |
| Recovery samples | Months 3–14; 57.5–65 Hz |
| First unrecovered sample after that sequence | Month 15: insufficient head even at tested maximum 65 Hz |
| Leading score | 0.722560, previously 0.992559 |

The fractional cable drop is not a universal 5% building-wiring criterion. This existing ESP model uses voltage-per-length, ampacity, temperature and supplied surface-voltage constraints, with a 30% fractional backstop. The 72.2% motor loading is below the preferred 75–85% band and remains a soft caveat, not an assertion that the equipment is optimal.

## Changes

- **Validity:** Explicit head sufficiency, gas stability and shaft-risk conditions supplement the operating-zone/gate checks. Later-month hydraulic shortfalls retain the candidate for reporting but end acceptable operation.
- **Fixed equipment:** Recheck original motor, cable and protector at every trajectory/VSD point instead of skipping sizing checks or selecting replacement hardware.
- **Evidence:** Store actual frequency, head, TDH, motor load, cable drop and thermal results for every recovered sample. No recovery schedule may jump over a failed sampled month.
- **Display:** Replace the unsupported horizon bar with baseline head, required TDH and discrete verified VSD points. Add first-failure/last-recovered summaries, checkpoint reconciliation and an expandable equipment table.
- **Legacy data:** Hide pre-correction validity claims in the timeline and request recalculation. Preserve existing stored runs; add a new demo run.
- **Narrative safety:** Share one reduced recovery-evidence scope between prompt and guard, keeping boundary points and the full frequency schedule. Cap length matching at nearest-foot rounding rather than permitting multi-foot discrepancies through relative tolerance.

## Verification

- **Backend:** 420 tests pass, including 18 dedicated VSD/validity regressions; one pre-existing collection warning for a domain class named TestPoint.
- **Frontend:** Type checking and production build pass.
- **Browser:** Live baseline/VSD reconciliation, schedule expand/collapse, candidate switching, dark/light theme cycle, desktop/mobile fit, legacy-run refusal, no-recovery and no-VSD states checked. No browser runtime errors or page-level horizontal overflow observed in these checks.
- **Live queue:** New facts stored in 0.86 seconds. The separate generated narrative was withheld after a numeric-parser false positive involving the preposition “in”; this is a remaining narrative limitation, not a failed engine run.

## Framework alignment and remaining limits

- **Aligned:** Explicit validity boundary and VSD recovery disclosure (§6.4); fixed-string trajectory evaluation and point-wise thermal checks (§6D.2); facts remain separate from generated narrative.
- **Partial:** Monthly samples and 2.5 Hz trials do not implement a continuous-frequency optimum (§6B.3), prove conditions between samples, or constitute a full field/start-up approval.
- **Data/licensing:** Estimated pump curves, conflicting motor-current data, absent vendor thrust limits and licensed viscous-correction access remain blockers. No new vendor data was acquired in this iteration.
- **Engineering:** Full surface/start-up validation and vendor-supported limits remain unfinished. No new expert decision is needed to correct this logical contradiction; field-release criteria still require engineering ownership.

The correction does not certify an operating schedule for a real well. It makes the prototype's calculated claims explicit, auditable and internally consistent.
