<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useEspStore } from "../stores";
import PumpCurve from "../components/PumpCurve.vue";
import ToleranceBand from "../components/ToleranceBand.vue";
const store = useEspStore();
const candidate = computed(() => store.selected);
const topCandidate = computed(() => store.design.candidates[0]);
// Presentation safeguard only: retain the engine's deterministic order, but do
// not imply score discrimination smaller than the supported input fidelity.
const CANDIDATE_SCORE_RELATIVE_TOLERANCE = 1e-3;
const validityMonths = computed(() => {
  const v = candidate.value?.validity;
  return (
    v?.valid_until_months ??
    (v?.valid_through_horizon ? v.horizon_months : null)
  );
});
const validityEnvelope = computed(() => {
  const validity = candidate.value?.validity;
  const minimum = validity?.valid_until_months_min,
    maximum = validity?.valid_until_months_max;
  if (
    minimum !== null &&
    minimum !== undefined &&
    maximum !== null &&
    maximum !== undefined
  )
    return minimum === maximum ? `${minimum} mo` : `${minimum}–${maximum} mo`;
  if (maximum !== null && maximum !== undefined) return `${maximum} mo`;
  if (minimum !== null && minimum !== undefined) return `${minimum} mo`;
  return "not available";
});
function relativeScoreDistance(first: number, second: number) {
  return (
    Math.abs(first - second) /
    Math.max(Math.abs(first), Math.abs(second), Number.EPSILON)
  );
}
function isScoreIndistinguishable(item: any) {
  const top = topCandidate.value;
  return (
    Boolean(top) &&
    relativeScoreDistance(item.score.total_score, top.score.total_score) <=
      CANDIDATE_SCORE_RELATIVE_TOLERANCE
  );
}
const tiedWithTop = computed(() => []);
function candidateRankLabel(item: any) {
  return `#${item.rank}`;
}
function configValue(value: unknown) {
  return value === null || value === undefined || value === ""
    ? "none"
    : String(value);
}
function differenceValue(field: string, value: unknown) {
  const text = configValue(value);
  if (text === "none") return text;
  if (field === "setting_depth_md_ft") return `${text} ft MD`;
  if (field === "motor_hp") return `${text} hp`;
  if (field === "cable_awg") return text.endsWith("AWG") ? text : `${text} AWG`;
  return text;
}
function candidateDifferences(item: any) {
  if (item.rank === topCandidate.value?.rank) return ["baseline configuration"];
  const base = topCandidate.value?.configuration;
  const fields: [string, string][] = [
    ["setting_depth_md_ft", "setting depth"],
    ["motor_hp", "motor"],
    ["motor_id", "motor"],
    ["cable_awg", "cable"],
    ["cable_id", "cable"],
    ["gas_handling_type", "gas handler"],
    ["gas_handling_id", "gas handler"],
    ["seal_id", "seal"],
  ];
  const unique = new Map<string, string[]>();
  const current = item.configuration as Record<string, unknown>,
    baseline = (base ?? {}) as Record<string, unknown>;
  for (const [field, label] of fields)
    if (configValue(current[field]) !== configValue(baseline[field]))
      unique.set(label, [
        ...(unique.get(label) ?? []),
        differenceValue(field, current[field]),
      ]);
  const differences = [...unique.entries()]
    .map(([label, values]) => `${label}: ${values.join(" / ")}`)
    .slice(0, 2);
  return differences.length ? differences : ["same displayed equipment"];
}
function scoreLabel(item: any) {
  return item.zone_duration_months == null
    ? item.score.total_score.toFixed(3)
    : item.zone_duration_months.toFixed(1) + " mo";
}
onMounted(() => store.loadPumpCurve());
watch(
  () => candidate.value?.configuration.config_id,
  () => store.loadPumpCurve(),
);

// Framework 6C.4. Deliberately NOT folded into electricalDisclosures: this is a
// mechanical fracture risk on a configuration the engine still ranks and
// presents, which is the most dangerous state the UI can show — it looks
// selectable. Amber ("you must know this") under-signals a part that breaks
// rather than degrades, so this gets its own blocking-styled section even
// though 6C.4 explicitly does not reject the configuration.
const shaftFracture = computed(() => {
  const c = candidate.value;
  if (!c) return null;
  if (!c.shaft_fracture_risk) return null;
  const p = c.design_point;
  return {
    nameplate: p?.motor_nameplate_hp ?? null,
    limit:
      p?.motor_nameplate_hp != null && p?.shaft_nameplate_utilization
        ? p.motor_nameplate_hp / p.shaft_nameplate_utilization
        : null,
    operating: p?.shaft_hp_operating_load ?? null,
    utilization: p?.shaft_nameplate_utilization ?? null,
  };
});
// A safety check that did not run must not be indistinguishable from one that
// passed, so its absence is stated rather than left blank.
const shaftCheckSkipped = computed(() => {
  const c = candidate.value;
  return !!c && c.shaft_nameplate_check_performed === false;
});
const coolingNote = computed(() => {
  const c = candidate.value;
  if (!c) return null;
  const v = c.cooling_verdict;
  if (!v || v === "adequate") return null;
  const vel = c.cooling_velocity_ft_s,
    floor = c.cooling_floor_applied_ft_s;
  // The two bounds are separate failure modes with opposite remedies, so they
  // get separate copy rather than one "velocity out of range" message. Telling
  // an engineer to fit a shroud for an over-velocity problem would be wrong:
  // a shroud raises velocity further.
  if (v === "within_viscous_band")
    return {
      tone: "indeterminate",
      eyebrow: "thermal / framework 6D.2 — unresolved",
      title:
        "Motor cooling is neither confirmed nor ruled out for this viscous fluid",
      body:
        "The CFD source for viscous service publishes a range of 2.6–2.8 ft/s, not a " +
        "single figure, and the annular velocity of this string lands inside it. No " +
        "midpoint has been substituted — averaging a published range would turn an " +
        "unresolved question into a confident answer. Resolving this needs the motor " +
        "manufacturer’s minimum cooling velocity for the declared fluid.",
      velocity: vel,
      reference: floor,
      referenceLabel: "published band (ft/s)",
      band: "2.6–2.8",
    };
  if (v === "below_minimum")
    return {
      tone: "danger",
      eyebrow: "thermal / framework 6D.2 — overheating",
      title: "Annular velocity is below the minimum for heat rejection",
      body:
        "The motor rejects its heat into the fluid moving past it. Below the minimum " +
        "velocity the windings run hotter than the insulation class allows regardless of " +
        "the fluid temperature. The remedy is to raise the velocity: a shroud is the " +
        "primary measure, and is close to mandatory in a deviated or horizontal well with " +
        "perforations above the pump. A larger motor OD narrows the annulus to the same " +
        "effect. Production rate is not a lever — it is set by the customer.",
      velocity: vel,
      reference: floor,
      referenceLabel: "minimum required (ft/s)",
      band: null,
    };
  if (v === "above_upper_bound")
    return {
      tone: "warn",
      eyebrow: "thermal / framework 6D.2 — erosion and separation",
      title: "Annular velocity exceeds the configured upper bound",
      body:
        "This is not a cooling problem — higher velocity always cools better. The " +
        "concern is erosion of the motor housing where solids are present, and loss of " +
        "the gas separation that depends on slower annular flow. The remedy is the " +
        "opposite of the low-velocity one: remove the shroud, or select a smaller motor " +
        "OD to widen the annulus. Do not fit a shroud to address this.",
      velocity: vel,
      reference: c.cooling_floor_applied_ft_s,
      referenceLabel: "minimum required (ft/s)",
      band: null,
    };
  return {
    tone: "warn",
    eyebrow: "thermal / framework 6D.2 — bound not cataloged",
    title: "The erosion and separation upper bound is unchecked, not satisfied",
    body:
      "A mechanism that bounds annular velocity from above is active for this well — " +
      "solids, or gas separating into the annulus — but no numeric upper bound is " +
      "cataloged for this motor. API RP 14E erosional velocity is a pipeline criterion " +
      "with no standing in an ESP annulus, so no figure has been borrowed from it. The " +
      "minimum is met; the upper bound has simply not been tested.",
    velocity: vel,
    reference: floor,
    referenceLabel: "minimum required (ft/s)",
    band: null,
  };
});
// Framework 6D.2 two-term display: fluid temperature and calculated
// self-heating are shown separately so the engineer can see which lever
// helps. The dominant term picks the remedy — a shroud raises velocity and
// helps the self-heating term; a high-temperature build raises the rating
// and helps when the fluid itself is already near the limit.
const selfHeating = computed(() => {
  const c = candidate.value;
  if (!c || !c.self_heating) return null;
  const sh = c.self_heating;
  const dominant = sh.rise_f > sh.fluid_temp_f ? "self-heating" : "fluid";
  const remedy =
    dominant === "self-heating"
      ? "Self-heating is the dominant contribution, so a shroud or a larger motor OD (both raise annular velocity) is the primary lever."
      : "The fluid temperature is the dominant contribution, so a high-temperature (Class H) build is the primary lever. Improving cooling velocity helps less.";
  const fallbackDisclosure =
    sh.efficiency_basis === "reference_induction_fallback"
      ? "The catalog record for this motor has no efficiency figure, so the anchor value (0.85, typical induction) was applied. A cataloged efficiency will move the winding temperature; a permanent-magnet motor at 0.94 would reduce it by roughly 60% for the same load."
      : null;
  return { sh, dominant, remedy, fallbackDisclosure };
});
// Framework 6D.2 row 4: calculated cable temperature. Same two-term shape
// as the motor row so the two can be read side by side. Dominant term
// picks the remedy: shroud (raises annular velocity, drops rise) vs
// high-temperature MLE / insulation build (raises the rating).
const cableSelfHeating = computed(() => {
  const c = candidate.value;
  if (!c || !c.cable_self_heating) return null;
  const csh = c.cable_self_heating;
  const dominant = csh.rise_f > csh.fluid_temp_f ? "self-heating" : "fluid";
  const remedy =
    dominant === "self-heating"
      ? "The conductor is running well above the fluid, so a shroud (raises annular velocity) or a larger conductor (lowers current density) is the primary lever."
      : "The fluid itself dominates the conductor temperature. A high-temperature MLE / cable insulation build is the primary lever; a shroud helps less because the conductor is already close to the fluid.";
  const currentPct =
    csh.current_factor > 0 ? Math.sqrt(csh.current_factor) * 100 : 0;
  return { csh, dominant, remedy, currentPct };
});
// Framework §6 load-scaled operating amps: shown when the phasor form ran
// (cataloged PF or operator-supplied override). Discloses the magnetizing
// + load decomposition and the delta vs the naive load\u00b7I_FL figure.
// On the pmm_nearly_linear basis the delta is zero by construction, so
// the section still renders but says so explicitly rather than hiding.
const loadCurrent = computed(() => {
  const c = candidate.value;
  if (!c || !c.motor_operating_current) return null;
  const oc = c.motor_operating_current;
  const deltaA = oc.operating_amps - oc.linear_reference_amps;
  const deltaPct =
    oc.linear_reference_amps > 0
      ? (deltaA / oc.linear_reference_amps) * 100
      : 0;
  const isPMM = oc.basis === "pmm_nearly_linear";
  const basisLabel = isPMM
    ? "permanent-magnet motor (rotor excitation, no stator magnetizing branch)"
    : oc.basis === "phasor_from_cataloged_power_factor"
      ? "induction phasor form from cataloged power factor"
      : oc.basis === "phasor_from_operator_magnetizing_fraction"
        ? "induction phasor form from operator-supplied magnetizing fraction"
        : oc.basis === "nameplate_linear_scaling"
          ? "linear nameplate scaling (no power factor available)"
          : oc.basis;
  const explanation = isPMM
    ? "A permanent-magnet motor has no stator magnetizing branch to speak of, so operating current scales linearly with load. The phasor decomposition still runs and reports a magnetizing component of zero; the linear reference matches the operating figure by construction."
    : Math.abs(deltaPct) < 1
      ? "At this load the phasor and linear forms are within one percent. That is the expected regime near nameplate load; the correction grows as load drops."
      : `The phasor form is ${deltaPct > 0 ? "above" : "below"} the naive load\u00b7I_FL figure by ${Math.abs(deltaPct).toFixed(0)}%. The stator magnetizing branch does not scale with shaft load, so at partial load the current an induction motor draws stays higher than a proportional scaling suggests. This is the current the cable is actually sized against.`;
  return { oc, deltaA, deltaPct, isPMM, basisLabel, explanation };
});
const electricalDisclosures = computed(() => {
  const d = store.design;
  if (!d) return [];
  const out: Array<{ title: string; detail: string }> = [];
  if (d.has_nameplate_amps_conflict)
    out.push({
      title: "Full-load current contradicts the cataloged nameplate",
      detail:
        "The B.14.1 current computed from power factor and efficiency disagrees " +
        "with the cataloged nameplate current beyond tolerance, so one of the published " +
        "values is not a full-load quantity. The higher of the two was carried into cable " +
        "sizing, because choosing the lower of two contradictory currents to size a " +
        "conductor is not defensible. Cable gauge and surface voltage are therefore " +
        "conservative, not optimal.",
    });
  if (d.uses_operator_supplied_motor_data)
    out.push({
      title: "Motor electrical data was supplied by the operator",
      detail:
        "Power factor, efficiency or the demagnetization limit came from the case " +
        "rather than the catalog, so this result is not reproducible from the catalog " +
        "version alone — the case is part of the input. See the assumption ledger for " +
        "the stated source and confidence of each value.",
    });
  if (d.requires_magnet_thermal_verification)
    out.push({
      title: "Magnet demagnetization gate is unresolved",
      detail:
        "The selected motor is a permanent magnet machine whose demagnetization " +
        "limit could not be cleared. The only magnet-temperature estimate available is " +
        "intake temperature, which is a lower bound, so the gate can be falsified but " +
        "never passed. Demagnetization is irreversible rather than gradual; obtain the " +
        "vendor thermal model before install.",
    });
  return out;
});
</script>
<template>
  <section class="view" data-testid="view-results">
    <div class="view-heading">
      <div>
        <p class="eyebrow">03 / deterministic design result</p>
        <h1>Candidate configurations over time</h1>
        <p>
          The curve visualizes the operating point leaving its acceptable zone.
        </p>
      </div>
      <span class="status-pill fact">deterministic facts</span>
    </div>
    <div v-if="store.isRefusal" class="refusal">
      <strong>{{ store.design.verdict.replaceAll("_", " ") }}</strong>
      <p>{{ store.design.verdict_explanation }}</p>
      <ul>
        <li v-for="x in store.design.recommended_data_requests" :key="x">
          {{ x }}
        </li>
      </ul>
    </div>
    <template v-else-if="candidate"
      ><div
        v-if="store.design.uses_estimated_catalog_data"
        class="catalog-warning"
      >
        <strong>Run-level warning</strong
        ><span
          >Catalog curve quality is qualified in the chart before it can be
          interpreted as a design reference.</span
        >
      </div>
      <!-- B.14.1 / B.16 electrical data disclosures.
     Amber, not red: none of these makes the design invalid. Each one means a
     number the reader is about to rely on rests on something other than a
     published vendor value, and the framework's rule is that a fact and a
     qualified fact must never look the same. These were backend-only flags
     until now, which is precisely the failure mode the rule exists to prevent. -->
      <section
        v-if="shaftFracture"
        class="shaft-fracture"
        aria-label="Shaft fracture risk"
      >
        <p class="eyebrow">
          mechanical / framework 6C.4 — not cleared for install
        </p>
        <strong
          >Motor nameplate power exceeds the shaft and bearing rating of this
          string</strong
        >
        <p>
          The shaft is checked against what the motor <em>can</em> deliver, not
          against the load it happens to be carrying. A frequency increase, a
          change in fluid properties or a restart can drive the motor to
          nameplate, and a shaft rated below that point fractures rather than
          degrading — there is no graded warning and no operating margin that
          protects against it.
        </p>
        <dl class="shaft-fracture-figures">
          <div>
            <dt>motor capability</dt>
            <dd class="number">
              {{ shaftFracture.nameplate?.toFixed(1) ?? "—" }} hp
            </dd>
          </div>
          <div>
            <dt>weakest shaft/bearing rating</dt>
            <dd class="number">
              {{ shaftFracture.limit?.toFixed(1) ?? "—" }} hp
            </dd>
          </div>
          <div>
            <dt>present operating load</dt>
            <dd class="number">
              {{ shaftFracture.operating?.toFixed(1) ?? "—" }} hp
            </dd>
          </div>
          <div>
            <dt>utilization</dt>
            <dd class="number">
              {{
                shaftFracture.utilization != null
                  ? (shaftFracture.utilization * 100).toFixed(0) + "%"
                  : "—"
              }}
            </dd>
          </div>
        </dl>
        <p>
          This configuration is shown rather than removed so that the remedy
          stays visible: a large-shaft or high-strength build of the same pump
          has identical hydraulics and an identical curve. No build variants are
          cataloged yet, so that substitution cannot be made here — the
          alternative is a smaller motor.
        </p>
      </section>
      <section
        v-if="shaftCheckSkipped"
        class="electrical-disclosures"
        aria-label="Shaft check not performed"
      >
        <p class="eyebrow">mechanical / check not performed</p>
        <div class="electrical-disclosure">
          <strong>The 6C.4 shaft-versus-nameplate check did not run</strong>
          <p>
            No motor was selected for this configuration, so there is no
            nameplate to check the shaft against. Any shaft utilization shown
            elsewhere is against the operating load and is not the governing
            check.
          </p>
        </div>
      </section>
      <section
        v-if="coolingNote"
        :class="['cooling-note', 'cooling-' + coolingNote.tone]"
        aria-label="Motor cooling"
      >
        <p class="eyebrow">{{ coolingNote.eyebrow }}</p>
        <strong>{{ coolingNote.title }}</strong>
        <p>{{ coolingNote.body }}</p>
        <dl class="cooling-figures">
          <div>
            <dt>annular velocity (ft/s)</dt>
            <dd>
              {{
                coolingNote.velocity != null
                  ? coolingNote.velocity.toFixed(2)
                  : "—"
              }}
            </dd>
          </div>
          <div v-if="coolingNote.band">
            <dt>{{ coolingNote.referenceLabel }}</dt>
            <dd>{{ coolingNote.band }}</dd>
          </div>
          <div v-else>
            <dt>{{ coolingNote.referenceLabel }}</dt>
            <dd>
              {{
                coolingNote.reference != null
                  ? coolingNote.reference.toFixed(2)
                  : "not cataloged"
              }}
            </dd>
          </div>
        </dl>
      </section>
      <section
        v-if="selfHeating"
        class="self-heating"
        aria-label="Motor thermal budget"
      >
        <p class="eyebrow">thermal / framework 6D.2 — winding temperature</p>
        <strong
          >Winding temperature = fluid temperature + calculated
          self-heating</strong
        >
        <p>
          Both terms are displayed so the appropriate remedy is visible before
          the winding rating is reached. {{ selfHeating.remedy }}
        </p>
        <dl class="self-heating-figures">
          <div>
            <dt>fluid temperature</dt>
            <dd class="number">
              {{ selfHeating.sh.fluid_temp_f.toFixed(0) }} <small>°F</small>
            </dd>
          </div>
          <div>
            <dt>self-heating rise</dt>
            <dd class="number">
              +{{ selfHeating.sh.rise_f.toFixed(0) }} <small>°F</small>
            </dd>
          </div>
          <div>
            <dt>winding temperature</dt>
            <dd class="number">
              {{ selfHeating.sh.winding_temp_f.toFixed(0) }} <small>°F</small>
            </dd>
          </div>
          <div>
            <dt>dominant term</dt>
            <dd>{{ selfHeating.dominant }}</dd>
          </div>
        </dl>
        <p
          v-if="selfHeating.fallbackDisclosure"
          class="self-heating-disclosure"
        >
          {{ selfHeating.fallbackDisclosure }}
        </p>
        <p class="self-heating-anchor">
          Model anchor: 50°F rise for water and 90°F for oil at 1 ft/s annular
          velocity and nameplate load, per the field observation cited in the
          framework. Load and velocity scaling are load-linear and
          Dittus-Boelter turbulent forced convection, respectively.
        </p>
      </section>
      <section
        v-if="cableSelfHeating"
        class="self-heating"
        aria-label="Cable thermal budget"
      >
        <p class="eyebrow">
          thermal / framework 6D.2 row 4 — cable calculated temperature
        </p>
        <strong
          >Conductor temperature = fluid temperature + I²R self-heating</strong
        >
        <p>
          Row 4 of the §6D.2 display table: the copper temperature the cable
          insulation actually sees, not the fluid ambient. This is the number
          the B.14.5 insulation-class check runs against, and it is also the
          temperature used to correct the cable resistivity in the voltage-drop
          calculation. {{ cableSelfHeating.remedy }}
        </p>
        <dl class="self-heating-figures">
          <div>
            <dt>fluid temperature</dt>
            <dd class="number">
              {{ cableSelfHeating.csh.fluid_temp_f.toFixed(0) }}
              <small>°F</small>
            </dd>
          </div>
          <div>
            <dt>I²R rise</dt>
            <dd class="number">
              +{{ cableSelfHeating.csh.rise_f.toFixed(0) }} <small>°F</small>
            </dd>
          </div>
          <div>
            <dt>conductor temperature</dt>
            <dd class="number">
              {{ cableSelfHeating.csh.conductor_temp_f.toFixed(0) }}
              <small>°F</small>
            </dd>
          </div>
          <div>
            <dt>current utilization</dt>
            <dd class="number">
              {{ cableSelfHeating.currentPct.toFixed(0) }}
              <small>% of ampacity</small>
            </dd>
          </div>
        </dl>
        <p class="self-heating-anchor">
          Model anchor: 20°F rise for water and 36°F for oil at cable ampacity,
          1 ft/s annular velocity. Scaling: (I / I_amp)² on the loss side,
          Dittus-Boelter (v_ref / v)^0.8 on the fluid film, and a resistance
          amplification loop for the copper's own temperature coefficient.
          Dielectric losses (negligible at medium voltage) and per-layer
          insulation conduction (bounded, not resolved) are not modeled.
        </p>
      </section>
      <section
        v-if="loadCurrent"
        class="self-heating"
        aria-label="Load-scaled operating current"
      >
        <p class="eyebrow">
          electrical / framework §6 — load-scaled operating current
        </p>
        <strong>Operating current = magnetizing branch ⊕ load branch</strong>
        <p>{{ loadCurrent.explanation }}</p>
        <dl class="self-heating-figures">
          <div>
            <dt>full-load current</dt>
            <dd class="number">
              {{ loadCurrent.oc.full_load_amps.toFixed(1) }} <small>A</small>
            </dd>
          </div>
          <div>
            <dt>magnetizing (I_μ)</dt>
            <dd class="number">
              {{ loadCurrent.oc.magnetizing_amps.toFixed(1) }} <small>A</small>
            </dd>
          </div>
          <div>
            <dt>load branch (I_L)</dt>
            <dd class="number">
              {{ loadCurrent.oc.load_amps.toFixed(1) }} <small>A</small>
            </dd>
          </div>
          <div>
            <dt>operating current</dt>
            <dd class="number">
              {{ loadCurrent.oc.operating_amps.toFixed(1) }}
              <small
                >A at {{ Math.round(loadCurrent.oc.load_fraction * 100) }}%
                load</small
              >
            </dd>
          </div>
          <div>
            <dt>vs load·I_FL</dt>
            <dd class="number">
              {{ loadCurrent.deltaPct >= 0 ? "+" : ""
              }}{{ loadCurrent.deltaPct.toFixed(1) }}
              <small
                >% ({{ loadCurrent.deltaA >= 0 ? "+" : ""
                }}{{ loadCurrent.deltaA.toFixed(1) }} A)</small
              >
            </dd>
          </div>
          <div>
            <dt>apparent PF at this load</dt>
            <dd class="number">
              {{ loadCurrent.oc.apparent_power_factor.toFixed(2) }}
            </dd>
          </div>
        </dl>
        <p class="self-heating-anchor">
          Basis: {{ loadCurrent.basisLabel }}. Model: |I(load)| = √(I_μ² +
          (load·I_FL·PF)²) with I_μ = I_FL·√(1−PF²). The apparent power factor
          drops off nameplate load because the magnetizing branch stays roughly
          fixed while the load branch shrinks (§B.16). Field weakening above
          100% load is not modeled; the operating range is designed to sit below
          nameplate.
        </p>
      </section>
      <section
        v-if="electricalDisclosures.length"
        class="electrical-disclosures"
        aria-label="Electrical data disclosures"
      >
        <p class="eyebrow">electrical data / qualified facts</p>
        <div
          v-for="d in electricalDisclosures"
          :key="d.title"
          class="electrical-disclosure"
        >
          <strong>{{ d.title }}</strong>
          <p>{{ d.detail }}</p>
        </div>
      </section>
      <section
        class="reproducibility"
        aria-label="Deterministic run reproducibility identifiers"
      >
        <span class="eyebrow"
          >reproducibility / immutable deterministic run</span
        >
        <div>
          <span>case hash</span
          ><strong class="number">{{
            store.design.provenance.case_hash || "absent"
          }}</strong>
        </div>
        <div>
          <span>config hash</span
          ><strong class="number">{{
            store.design.provenance.config_hash || "absent"
          }}</strong>
        </div>
        <div>
          <span>catalog version</span
          ><strong class="number">{{
            store.design.provenance.catalog_version || "absent"
          }}</strong>
        </div>
        <div>
          <span>engine version</span
          ><strong class="number">{{
            store.design.provenance.engine_version || "absent"
          }}</strong>
        </div>
      </section>
      <div class="result-layout">
        <aside class="candidate-list">
          <div class="panel-title">
            <h2>Configurations</h2>
            <span>{{
              tiedWithTop.length > 1 ? "tied group" : "engine order"
            }}</span>
          </div>
          <p
            v-if="tiedWithTop.length > 1"
            class="candidate-tie-note"
            data-testid="candidate-tie-note"
          >
            Scores are statistically indistinguishable within the
            {{ (CANDIDATE_SCORE_RELATIVE_TOLERANCE * 100).toFixed(1) }}%
            relative tolerance. Choose on the differing fields shown or other
            engineering grounds.
          </p>
          <button
            v-for="c in store.design.candidates"
            :key="c.rank"
            class="candidate"
            :class="{ active: c.rank === candidate.rank }"
            @click="store.selectedRank = c.rank"
          >
            <span class="rank candidate-rank">{{ candidateRankLabel(c) }}</span
            ><span
              ><strong>{{ c.configuration.pump_model }}</strong
              ><small class="number"
                >{{ c.configuration.stages }} stg ·
                {{ c.configuration.frequency_hz }} Hz</small
              ><small class="candidate-diff">{{
                candidateDifferences(c).join(" · ")
              }}</small></span
            ><span class="number score">{{ scoreLabel(c) }}</span>
          </button>
        </aside>
        <div v-if="candidate.design_point.converged===false" class="panel"><h2>Performance uncomputed</h2><p>The candidate remains selectable, but missing coefficients or curve coverage prevent a numerical performance claim. Review the Engineering decisions view; zero placeholders are not calculated performance.</p></div>
        <div v-else class="results-main">
          <div class="metrics">
            <div>
              <span>Validity boundary</span
              ><strong class="number"
                >{{ validityMonths ?? "—" }} <small>mo</small></strong
              >
            </div>
            <div>
              <span>Design point</span
              ><strong class="number"
                >{{ Math.round(candidate.design_point.liquid_rate_bpd) }}
                <small>bpd</small></strong
              >
            </div>
            <div>
              <span>Efficiency</span
              ><strong class="number"
                >{{ Math.round(candidate.design_point.efficiency_frac * 100)
                }}<small>%</small></strong
              >
            </div>
            <div>
              <span>Coverage</span
              ><strong class="number"
                >{{ Math.round(candidate.score.time_coverage_frac * 100)
                }}<small>%</small></strong
              >
            </div>
          </div>
          <article class="panel curve-panel">
            <div class="panel-title">
              <div>
                <p class="eyebrow">centerpiece / catalog + computed facts</p>
                <h2>Pump curve + operating path</h2>
              </div>
              <span v-if="store.curve"
                >left: downthrust · right: upthrust<br />BEP
                {{ Math.round(store.curve.bep_q_bpd) }} bpd ·
                {{ store.curve.frequency_hz }} Hz ·
                {{ store.curve.stages }} stg</span
              >
            </div>
            <PumpCurve
              v-if="store.curveState === 'ready' && store.curve"
              :candidate="candidate"
              :curve="store.curve"
            />
            <div
              v-else-if="store.curveState === 'loading'"
              class="chart-loading"
            >
              Loading the catalog curve for this selected pump…
            </div>
            <div v-else class="empty-state">
              A real catalog curve is unavailable for this selected
              configuration{{
                store.curveError ? `: ${store.curveError}` : "."
              }}
              No curve background has been substituted.
            </div>
            <p class="chart-note">
              Circle = first sampled month; arrow = final sampled month. The
              full catalog-rate scale is retained so the path’s small movement
              is not exaggerated. Thrust shading and BEP come directly from the
              catalog-curve response.
            </p>
          </article>
          <div class="detail-grid">
            <article class="panel">
              <div class="panel-title">
                <h2>Validity boundary</h2>
                <span class="fact-label">computed fact</span>
              </div>
              <p class="boundary-number number">
                {{ validityMonths ?? "—" }}
                <small>{{
                  candidate.validity.valid_through_horizon
                    ? "months / full horizon"
                    : "months base"
                }}</small>
              </p>
              <p>{{ candidate.validity.limiting_mechanism }}</p>
              <p class="muted number">envelope {{ validityEnvelope }}</p>
            </article>
            <article v-if="candidate.vsd_recovery?.vsd_available" class="panel">
              <div class="panel-title">
                <h2>VSD recovery</h2>
                <span class="fact-label">computed fact</span>
              </div>
              <p class="boundary-number number">
                {{ candidate.vsd_recovery.extended_validity_months ?? "—" }}
                <small>months</small>
              </p>
              <p class="number">
                {{ candidate.vsd_recovery.frequency_band_hz?.join("–") }} Hz ·
                {{
                  Math.round(
                    (candidate.vsd_recovery.recovered_horizon_frac ?? 0) * 100,
                  )
                }}% recovered
              </p>
              <p>{{ candidate.vsd_recovery.note }}</p>
            </article>
          </div>
        </div>
      </div>
      <ToleranceBand
        :candidate="candidate"
        :ranking-stability="store.design.ranking_stability"
      />
      <section v-if="store.design.judgments.length" class="judgment-stack">
        <article
          v-for="j in store.design.judgments"
          :key="j.judgment_id"
          class="judgment"
        >
          <div>
            <p class="eyebrow">judgment / {{ j.kind }}</p>
            <strong>{{ j.statement }}</strong>
          </div>
          <span class="confidence"
            >{{ Math.round(j.confidence * 100) }}% confidence<br /><small>{{
              j.confidence_basis
            }}</small></span
          >
        </article>
      </section>
    </template>
    <div v-else class="empty-state">
      No candidate configurations were returned.
    </div>
  </section>
</template>
