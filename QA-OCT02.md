# October 2 QA inventory

- Synthetic case chooser: load replacement, new-well, and gas cases; show explicit source banner and data-level SYN tags.
- Ranking: switch run-life to target BEP and back; persist controls through recalculation; compare leaders with duration, efficiency, head margin and partial cost.
- Gas: zero identity and table values; manual correction with ledger entry; invalid coefficient rejected; no extrapolation above 25%.
- Physical boundary: actual casing interference and PMM without VSD excluded. Clearance margin and calculated exceedances remain warnings.
- Thrust: automatic floater/compression equations; per-section override; missing head is uncomputed, not zero load.
- Cable: select a different gauge, run, verify installed ID; inspect worst-current alternatives, frequency samples, margin and committed-ceiling status.
- Bend: default 6/2 controls; edit and restore; preserve separate physical-fit check.
- Project preference: disabled until confirmation/identifier; API isolation between operators; opt-in application.
- Narrative: correctly rounded payload and strict refusal of 68.0% for 68.056%.
- UI visual checks: desktop and 375px; both themes; Engineering, Results, Timeline and Case views; no page-level horizontal overflow; charts label synthetic ticks.
- Off-happy-path: invalid gas override; physical-only rejection; high-gas coefficient gap with no fictitious thrust/performance; incomplete catalog stays qualified.

Release limitation: section screening is not a validated integrated tapered-string design. Real catalog, modular protector guidance, full geometry/FEA and reactive cable-drop data remain absent.
