<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useEspStore } from "../stores";
const store = useEspStore();
const candidate = computed(() => store.selected as any);
const details = computed(() => candidate.value?.engineering ?? {});
const comparison = computed(
  () => ((store.design as any).ranking_comparison ?? []) as any[],
);
const mode = ref("run_life"),
  kq = ref(""),
  kh = ref(""),
  running = ref(6),
  setting = ref(2),
  path = ref("catalog"),
  cable = ref(""),
  ceiling = ref("");
const sectionPaths = ref(["catalog", "catalog"]),
  sectionCounts = ref([50, 50]),
  sectionPumps = ref(["", ""]);
const status = ref(""),
  busy = ref(false),
  confirm = ref(false),
  engineer = ref(""),
  preference = ref(""),
  preferCost = ref(false);
const acceptSingleSeal=ref(false);
const f = (v: any, n = 1) =>
  v == null ? "Not supplied" : Number(v).toFixed(n);
const pct = (v: any) =>
  v == null ? "Not supplied" : `${(Number(v) * 100).toFixed(1)}%`;
const warningValue=(v:any,unit:string)=>unit==="fraction"?pct(v):`${f(v)} ${unit??""}`;
watch(
  () => store.caseData,
  () => {
    const c = store.caseData as any,
      e = c.engineering ?? {},
      g = c.constraints?.geometry ?? {};
    mode.value = e.ranking_mode ?? "run_life";
    kq.value = e.gas_kq_override?.value ?? "";
    kh.value = e.gas_kh_override?.value ?? "";
    running.value = g.max_dogleg_deg_per_100ft?.value ?? 6;
    setting.value = g.max_setting_dogleg_deg_per_100ft?.value ?? 2;
    path.value = e.seal_thrust_paths?.main ?? "catalog";
    cable.value = e.cable_id ?? "";
    ceiling.value = e.operating_ceiling_hz?.value ?? "";
    if(e.tapered_sections?.length===2){
      sectionCounts.value=e.tapered_sections.map((s:any)=>s.stages);
      sectionPumps.value=e.tapered_sections.map((s:any)=>s.pump_id??"");
    }
    sectionPaths.value=[e.seal_thrust_paths?.['section-1']??'catalog',e.seal_thrust_paths?.['section-2']??'catalog'];
  },
  { immediate: true },
);
watch(()=>details.value.tapered,(preview)=>{
  if(preview?.sections?.length===2 && !(store.caseData as any).engineering?.tapered_sections?.length){
    sectionCounts.value=preview.sections.map((s:any)=>s.stages);
    sectionPumps.value=preview.sections.map((s:any)=>s.pump_id);
  }
},{immediate:true});
const tracked = (value: any, unit?: string) => ({
  value,
  unit,
  source: "engineer_override",
  confidence: 1,
  note: "Explicit engineer control; not a vendor measurement.",
});
async function apply() {
  busy.value = true;
  status.value = "";
  try {
    const paths: Record<string, string> = {};
    if (path.value !== "catalog") paths.main = path.value;
    sectionPaths.value.forEach((p, i) => {
      if (p !== "catalog") paths[`section-${i + 1}`] = p;
    });
    await store.setEngineering(
      {
        ranking_mode: mode.value,
        gas_kq_override: kq.value === "" ? null : tracked(Number(kq.value)),
        gas_kh_override: kh.value === "" ? null : tracked(Number(kh.value)),
        cable_id: cable.value || null,
        operating_ceiling_hz:
          ceiling.value === "" ? null : tracked(Number(ceiling.value), "Hz"),
        seal_thrust_paths: paths,
        tapered_sections: sectionPumps.value.some(Boolean)
          ? sectionCounts.value.map((stages, i) => ({
              stages: Number(stages),
              pump_id: sectionPumps.value[i] || undefined,
            }))
          : [],
      },
      {
        max_dogleg_deg_per_100ft: tracked(Number(running.value), "deg/100ft"),
        max_setting_dogleg_deg_per_100ft: tracked(
          Number(setting.value),
          "deg/100ft",
        ),
      },
    );
    status.value =
      "Inputs saved and recalculated. Original run remains available.";
  } catch (e) {
    status.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function savePreference() {
  try {
    await store.saveProjectPreference({
      confirmed: confirm.value,
      engineer_id: engineer.value,
      ranking_mode: mode.value,
      note: preference.value,
      prefer_lower_cost: preferCost.value,
      accept_single_seal: acceptSingleSeal.value,
    });
    status.value =
      "Project preference saved in this operator perimeter. Apply it explicitly to this case to change ranking.";
  } catch (e) {
    status.value = (e as Error).message;
  }
}
</script>
<template>
  <section class="view decision-view" data-testid="view-engineering">
    <div class="view-heading">
      <div>
        <p class="eyebrow">Expert sign-off / 02 October 2026</p>
        <h1>Engineering decisions</h1>
        <p>
          Calculated exceedances remain selectable warnings. Physical
          impossibilities are excluded; selection is not operating approval.
        </p>
      </div>
    </div>
    <div class="catalog-warning">
      <strong>{{
        (store.caseData as any).synthetic ? "SYNTHETIC" : "PROTOTYPE"
      }}</strong
      ><span>{{
        (store.caseData as any).synthetic
          ? "Every input, part, curve and price in this case is fictional demo data. No vendor certification or field approval."
          : "Catalog limitations remain. A warning does not establish safe operation."
      }}</span>
    </div>
    <article class="panel">
      <h2>Explicit controls</h2>
      <p>
        Changes create a new calculation. Gas overrides retain
        engineer-corrected provenance; blank means use the disclosed table.
      </p>
      <div class="controls-grid">
        <label
          >Ranking mode<select v-model="mode" data-testid="ranking-mode">
            <option value="run_life">
              Run-life zone duration + efficiency
            </option>
            <option value="bep_target">BEP at target + strength margin</option>
          </select></label
        >
        <label
          >Kq override<input
            v-model="kq"
            type="number"
            min="0.001"
            max="1"
            step=".01"
            placeholder="Table"
            data-testid="override-kq"
        /></label>
        <label
          >Kh override<input
            v-model="kh"
            type="number"
            min="0.001"
            max="1"
            step=".01"
            placeholder="Table"
            data-testid="override-kh"
        /></label>
        <label
          >Running bend trigger, °/100 ft<input
            v-model="running"
            type="number"
            min="0"
            step=".5"
            data-testid="bend-running"
        /></label>
        <label
          >Setting bend trigger, °/100 ft<input
            v-model="setting"
            type="number"
            min="0"
            step=".5"
            data-testid="bend-setting"
        /></label>
        <label
          >Main protector thrust path<select v-model="path">
            <option value="catalog">Catalog pump type</option>
            <option value="floater">Floater: ΔP × shaft area</option>
            <option value="compression">Compression: stack + shaft</option>
          </select></label
        >
        <label
          >Cable selection<select v-model="cable" data-testid="cable-choice">
            <option value="">Worst-case recommendation</option>
            <option
              v-for="c in details.cables ?? []"
              :key="c.cable_id"
              :value="c.cable_id"
            >
              {{ c.awg }} AWG · {{ c.synthetic ? "SYNTHETIC" : c.cable_id }}
            </option>
          </select></label
        >
        <label
          >Committed operating ceiling, Hz<input
            v-model="ceiling"
            type="number"
            min="1"
            step="2.5"
            placeholder="No commitment"
        /></label>
      </div>
      <button
        class="action"
        :disabled="busy"
        @click="apply"
        data-testid="apply-engineering"
      >
        {{ busy ? "Recalculating…" : "Apply and recalculate" }}
      </button>
      <p role="status">{{ status }}</p>
    </article>
    <article class="panel">
      <h2>Ranking alternatives, shown together</h2>
      <p>
        Zone duration does not imply head sufficiency. No hidden “materiality”
        cutoff; non-dominated alternatives and both mode leaders are shown.
      </p>
      <div class="table-scroll">
        <table data-testid="ranking-comparison">
          <thead>
            <tr>
              <th>Configuration</th>
              <th>Zone duration</th>
              <th>Efficiency</th>
              <th>Head margin, day 1</th>
              <th>Life / BEP ranks</th>
              <th>Comparable cost</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="x in comparison" :key="x.config_id">
              <td>
                {{ x.pump }} · {{ x.stages }} stg · {{ x.frequency_hz }} Hz
              </td>
              <td>{{ f(x.zone_duration_months) }} mo</td>
              <td>{{ pct(x.efficiency_frac) }}</td>
              <td>{{ pct(x.head_margin_frac) }}</td>
              <td>{{ x.run_life_rank }} / {{ x.bep_rank }}</td>
              <td>
                {{
                  x.comparable_cost == null
                    ? "No price data"
                    : "Demo $" + f(x.comparable_cost, 0)
                }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>
        Cost compares pump stages and cable only; motor, protector, installation
        and energy are excluded. Synthetic prices cannot establish real savings.
      </p>
    </article>
    <article class="panel" v-if="candidate">
      <h2>Selected candidate: warnings remain visible</h2>
      <p>
        Actual − limit is a signed difference, not a safety margin. “Not
        supplied” is a missing input, never a passed check.
      </p>
      <div class="table-scroll">
        <table data-testid="engineering-warnings">
          <thead>
            <tr>
              <th>Finding</th>
              <th>Actual</th>
              <th>Limit</th>
              <th>Actual − limit</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(w, i) in candidate.soft_violations" :key="i">
              <td>{{ w.message }}</td>
              <td>{{ warningValue(w.actual_value,w.unit) }}</td>
              <td>{{ warningValue(w.limit_value,w.unit) }}</td>
              <td>{{ warningValue(w.margin,w.unit) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>
    <div class="detail-grid">
      <article class="panel">
        <h2>Gas correction</h2>
        <template v-if="details.gas"
          ><p>
            Intake GVF:
            <span class="number">{{ pct(details.gas.fgvf_at_intake) }}</span>
          </p>
          <p>
            Kq: <span class="number">{{ f(details.gas.kq, 3) }}</span> · Kh:
            <span class="number">{{ f(details.gas.kh, 3) }}</span>
          </p>
          <p>{{ details.gas.correction_source }}</p>
          <p>
            Zero gas is exactly Kq = Kh = 1.0. Above 25% GVF the signed table
            has no numeric endpoint; explicit coefficients are required, not
            extrapolated.
          </p></template
        >
        <p v-else>
          Gas-adjusted performance is not computed for this selection.
        </p>
      </article>
      <article class="panel">
        <h2>Protector thrust</h2>
        <template v-if="details.thrust"
          ><p>
            Path: {{ details.thrust.path ?? "Missing pump type" }} ·
            {{ details.thrust.source }}
          </p>
          <p>
            ΔP:
            <span class="number"
              >{{ f(details.thrust.delta_pressure_psi) }} psi</span
            >
            · shaft area:
            <span class="number"
              >{{ f(details.thrust.shaft_area_in2, 3) }} in²</span
            >
          </p>
          <p>
            Load:
            <span class="number">{{ f(details.thrust.load_lb) }} lb</span> ·
            capacity:
            <span class="number">{{ f(details.thrust.capacity_lb) }} lb</span>
          </p>
          <p>
            Capacity − load:
            <span class="number">{{ f(details.thrust.margin_lb) }} lb</span>
          </p>
          <p v-if="details.thrust.recommend_tandem">
            Tandem recommended. Single section remains selectable with disclosed
            risk.
          </p>
          <p>{{ details.thrust.assembly_note }}</p>
          <p>{{details.seal_chamber?.note}}</p>
          <p v-if="details.thrust.missing?.length">
            Missing: {{ details.thrust.missing.join(", ") }}
          </p></template
        >
      </article>
    </div>
    <article class="panel">
      <h2>Cable decision surface</h2>
      <p>
        Worst-case current uses nameplate/B.14.1 and the modeled drive-frequency
        grid. Smaller gauges remain selectable with warnings; sampled
        frequencies are not continuous or future-temperature approval.
      </p>
      <div class="table-scroll">
        <table data-testid="cable-surface">
          <thead>
            <tr>
              <th>Gauge</th>
              <th>Worst current / ampacity</th>
              <th>Voltage drop</th>
              <th>Conductor / rating</th>
              <th>Insulation margin</th>
              <th>Verified frequencies at this well state</th>
              <th>Demo cost</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in details.cables ?? []" :key="c.cable_id">
              <td>
                {{ c.awg }} AWG<br />{{
                  c.selected
                    ? "Selected"
                    : c.recommended
                      ? "Recommended"
                      : "Alternative"
                }}
              </td>
              <td>{{ f(c.current_a) }} / {{ f(c.ampacity_a) }} A</td>
              <td>{{ pct(c.voltage_drop_frac) }}</td>
              <td>
                {{ f(c.conductor_temp_f) }} / {{ f(c.temperature_limit_f) }} °F
              </td>
              <td>{{ f(c.insulation_margin_f) }} °F</td>
              <td>
                {{
                  c.verified_frequencies_hz?.join(", ") || "None verified"
                }}
                Hz
                <br/>{{c.ceiling_assessment}}
              </td>
              <td>
                {{
                  c.total_price == null
                    ? "No price data"
                    : "$" + f(c.total_price, 0)
                }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>
        Voltage drop currently uses resistance only. Reactance data are absent;
        no generic NEC table or invented reactance is substituted.
      </p>
    </article>
    <article class="panel" v-if="details.tapered">
      <h2>Gas-well section configurator</h2>
      <p>{{ details.tapered.note }}</p>
      <div class="table-scroll">
        <table data-testid="tapered-sections">
          <thead>
            <tr>
              <th>Section</th>
              <th>Pump / stages</th>
              <th>Volumetric rate</th>
              <th>GVF</th>
              <th>Inlet pressure</th>
              <th>Section head</th>
              <th>Protector thrust</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in details.tapered.sections" :key="s.section_id">
              <td>{{ s.position }}</td>
              <td>{{ s.pump }} / {{ s.stages }}</td>
              <td>{{ f(s.volumetric_rate_bpd) }} bpd</td>
              <td>{{ pct(s.gas_fraction) }}</td>
              <td>{{ f(s.inlet_pressure_psi) }} psi</td>
              <td>{{ f(s.head_ft) }} ft</td>
              <td>{{ f(s.thrust.load_lb) }} lb · {{ s.thrust.path }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="controls-grid">
        <template v-for="(_, i) in sectionCounts" :key="i"
          ><label
            >Section {{ i + 1 }} pump<select v-model="sectionPumps[i]">
              <option value="">Automatic screening choice</option>
              <option value="synthetic-1800">SYN-1800</option>
              <option value="synthetic-2600">SYN-2600</option>
              <option value="synthetic-3600">SYN-3600</option>
            </select></label
          ><label
            >Section {{ i + 1 }} stages<input
              v-model="sectionCounts[i]"
              type="number"
              min="1"
              max="300" /></label
          ><label
            >Section {{ i + 1 }} thrust path<select v-model="sectionPaths[i]">
              <option value="catalog">Catalog</option>
              <option value="floater">Floater</option>
              <option value="compression">Compression</option>
            </select></label
          ></template
        >
      </div>
      <button class="action" :disabled="busy" @click="apply">
        Recalculate section proposal
      </button>
    </article>
    <article class="panel">
      <h2>Project preference: explicit confirmation only</h2>
      <p>
        Saved within this operator’s project perimeter, never inferred from a
        selection. A cost preference changes ranking only where comparable price
        inputs exist; tradeoffs remain visible.
      </p>
      <div class="controls-grid">
        <label
          >Engineer identifier<input
            v-model="engineer"
            placeholder="Explicit sign-off identifier" /></label
        ><label
          >Preference rationale<input
            v-model="preference"
            placeholder="Customer engineering standard"
        /></label>
      </div>
      <label class="check"><input type="checkbox" v-model="acceptSingleSeal"/> Record acceptance of a single protector despite the disclosed tandem recommendation</label>
      <p v-if="details.preference?.applied">Applied preference: {{details.preference.applied.note || 'Confirmed project standard'}}. Cost priority: {{details.preference.applied.prefer_lower_cost?'on':'off'}}; single protector accepted: {{details.preference.applied.accept_single_seal?'yes':'no'}}.</p>
      <label class="check"
        ><input type="checkbox" v-model="preferCost" /> Prefer lower comparable
        pump-and-cable cost</label
      ><label class="check"
        ><input type="checkbox" v-model="confirm" /> I explicitly confirm this
        project preference</label
      ><button
        class="action"
        :disabled="!confirm || !engineer.trim()"
        @click="savePreference"
      >
        Save confirmed preference</button
      ><button
        class="text-button"
        @click="store.setEngineering({ apply_project_preference: true })"
      >
        Apply saved project preference and recalculate
      </button>
    </article>
  </section>
</template>
<style scoped>
.panel {
  margin-bottom: 20px;
}
.panel > p {
  margin: 8px 0;
}
.controls-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin: 18px 0;
}
label {
  display: grid;
  gap: 7px;
  font-size: 12px;
  color: var(--muted);
}
input,
select {
  width: 100%;
  padding: 9px;
  background: var(--surface2);
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--text);
  min-width: 0;
}
.action {
  background: var(--accent);
  border: 0;
  border-radius: 4px;
  color: var(--bg);
  font-weight: 600;
  padding: 10px 15px;
  margin: 8px 0;
}
.action:disabled {
  opacity: 0.5;
  cursor: wait;
}
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
.check {
  display: flex;
  align-items: center;
  margin: 12px 0;
}
.check input {
  width: auto;
}
td {
  font-size: 12px;
  min-width: 100px;
}
th {
  font-size: 11px;
  text-align: left;
}
table {
  width: 100%;
  border-collapse: collapse;
}
td,
th {
  padding: 10px;
  border-bottom: 1px solid var(--line);
}
.table-scroll {
  overflow: auto;
}
p .number {
  color: var(--text);
}
@media (max-width: 750px) {
  .controls-grid,
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
